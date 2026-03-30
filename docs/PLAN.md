# SmartCat — RAG System for Email Search

## Context

The goal is to build a RAG system for full-text AI search over an email corpus (test: Enron 517K emails, production: 30GB+ HTML with PDF/JPG attachments). The system should extract participants, topics, dates, amounts, documents, conversation threads and provide AI answers with reasoning.

**Hardware**: RTX 3090/4090 24GB VRAM, local inference.
**UI**: CLI-first, API later.

---

## 1. Dataset Selection: `maildir/` (primary)

**Using `maildir/`**, not `emails.csv`:
- **Production compatibility** — production will be file-based, not CSV. A file-based pipeline transfers directly
- **Parallel processing** — each file is independent, progress can be checkpointed per file
- **Attachments** — CSV lacks attachments, maildir architecture naturally extends to attachments
- **`emails.csv`** is used only for validation (count verification, quick parsing prototyping)

---

## 2. Data Pipeline Architecture

### 2.1 Email Parsing (Python `email` stdlib)
```
maildir/{user}/{folder}/{id}_ → Structured dict:
  message_id, date, from/to/cc/bcc (addr+name),
  subject, body_text, content_type, x_folder,
  source_path, has_forwarded_content, has_reply_content
```

**Error handling**: `processing_errors(file_path, error_type, error_msg, timestamp)` table in SQLite. Skip-and-log for corrupted files, unreadable encodings, empty files. Resumable ingestion via `processed_files(file_path, status, processed_at)` table.

### 2.2 Thread Reconstruction
**Primary path** (for production, when headers are available):
1. `In-Reply-To` / `References` headers → direct tree construction

**Fallback** (for Enron, where these headers are missing):
2. Subject normalization (strip RE:/FW:/Fwd:)
3. Grouping by canonical subject + participant overlap
4. Parsing `"Forwarded by"` and `"-----Original Message-----"` from body
5. Result: `thread_id` + `parent_message_id` on each email

### 2.3 Metadata Extraction
- **Participants**: canonical names (X-From/X-To → unified person table)
- **Dates**: `dateutil.parser` from headers + regex from body
- **Amounts**: regex `\$[\d,]+\.?\d*`, "million", "MMBtu", etc.
- **Document references**: `*.xls`, `*.doc`, `*.pdf`, contract numbers
- **No spaCy** — for email, regex + structured headers are sufficient. NER is not needed since participants are already in From/To.

### 2.4 Docling (for production)
```
MIME attachment → type detection:
  PDF      → Docling PDF pipeline (layout + OCR)
  image/*  → Docling OCR
  HTML     → BeautifulSoup + readability-lxml
  .doc/xls → Docling / python-docx / openpyxl
→ Text is linked to parent email via message_id + attachment_id
```

**Large attachments**: PDF 100+ pages → chunks with `attachment_id + page_range` for precise citation.
**Attachment deduplication**: file hash → if the same attachment is forwarded in another email, link instead of reprocessing.

### 2.5 HTML Processing (for production)
- Extracting highlighted text (`<mark>`, `<b>`, `<strong>`, inline `background-color`)
- Tables → markdown
- Removing boilerplate (signatures, disclaimers, tracking pixels)
- Preserving quoted reply sections

### 2.6 Email Deduplication
- The same email can be in sender's `sent_items/` and recipient's `inbox/`
- **Strategy**: store all instances in SQLite (different folder = different context), but **one set of vectors in Qdrant** per unique Message-ID
- Relationship: `email_instances(message_id, source_path, x_folder, folder_owner)` — N:1 to the main email record

---

## 3. Storage & Indexing

### 3.1 Vector DB: **Qdrant** (Docker, single-node)
- Rust, payload filtering (filtering by date/participant at ANN level), disk-backed
- ChromaDB cannot handle >1M vectors, Milvus is overkill for single-machine
- 517K unique emails x ~3 chunks = ~1.5M vectors → ~4.5GB (RAM)
- Production ~10M vectors → ~30GB (disk-backed mode, 64GB+ RAM)

### 3.2 Metadata: **SQLite + FTS5**
- FTS5 provides BM25 keyword search out of the box
- Zero-ops, single file

```sql
emails (message_id PK, date_sent, subject, body_text, from_address,
        from_name, thread_id, parent_message_id, has_attachments, ...)
email_instances (id PK, message_id FK, source_path, x_folder, folder_owner)
participants (id PK, email UNIQUE, canonical_name)
email_participants (message_id, participant_id, role)
entities (message_id, entity_type, entity_value, context)
attachments (id PK, message_id FK, filename, content_type, file_hash,
             extracted_text, page_count)
chunks (chunk_id PK, message_id FK, attachment_id FK NULL,
        chunk_type, chunk_index, text, token_count)
processed_files (file_path PK, status, processed_at)
processing_errors (file_path, error_type, error_msg, timestamp)
emails_fts USING fts5(subject, body_text)
```

### 3.3 Chunking: hierarchical, email-aware
| Level | Content | Size |
|-------|---------|------|
| L1 Summary | Subject + From/To/Date + first 200 chars | 100-300 tokens |
| L2 Body | Body paragraphs, merge small ones up to ~400 tokens | 300-512 tokens |
| L3 Quoted | Each nested FW/RE as a separate chunk | varies |
| L4 Attachment | Docling-extracted text from attachments (with page_range) | 300-500 tokens |

Each chunk carries a payload in Qdrant: `message_id, chunk_type, date_sent, from_address, to_addresses, thread_id, has_monetary, has_attachment`

### 3.4 Embedding: choose from 2-3 models
Candidates (all local, <1GB):
- `nomic-embed-text-v1.5` — 768d, 137M, Matryoshka, 8K context
- `bge-large-en-v1.5` — 1024d, 335M, strong on retrieval
- `e5-large-v2` — 1024d, 335M, instruction-tuned

**Step**: before batch embedding the entire corpus — **compare on 20 test queries** (Recall@10). Choose the best one.

Via `sentence-transformers` (not Ollama — faster in batches). ~1000 chunks/sec GPU.

---

## 4. RAG Architecture

### 4.1 Hybrid Search (3 channels → RRF fusion)
```
Query → [in parallel]
  ├─ Vector Search (Qdrant, top-60, metadata pre-filters)
  ├─ Keyword Search (SQLite FTS5 BM25, top-60)
  └─ Structured Filters (agent forms via tools, not NLU parsing)
      ↓
  RRF fusion (k=60) → top-30
```

**Structured Query**: We do NOT try to parse NL queries with regex. Instead, the agent decides when to call `search_by_participant()` or `search_by_date_range()`. This is more reliable and extensible.

### 4.2 Re-ranking
- Cross-encoder `bge-reranker-v2-m3` on top-30 → top-10
- **Runs on CPU** (~300ms for 30 pairs) — frees GPU for LLM
- Metadata boost: thread completeness, recency, participant match

### 4.3 VRAM Lifecycle (24GB constraint)
```
Ingestion mode:  embedding model (GPU, ~0.5GB) — batch processing
Serving mode:    LLM (GPU, ~20GB) + reranker (CPU) + embedding (CPU, for query)
```
- During ingestion LLM is not needed → all VRAM for embedding
- During serving, embedding a single query on CPU — ~50ms, acceptable
- Reranker always on CPU
- **Do not load all models simultaneously on GPU**

### 4.4 Context Assembly
- Deduplication by message_id
- For each email: full record from SQLite + thread context
- Up to 24K tokens of context (Qwen3 32B, 32K context window)

### 4.5 AI Agent (ReAct loop)
Agent tools:
- `search_emails(query, filters)` — hybrid search (vector + BM25)
- `search_by_participant(name_or_email)` — structured search by participant
- `search_by_date_range(start, end, query?)` — date range filter
- `search_entities(type, value)` — search for amounts, dates, documents
- `get_email(message_id)` — full email
- `get_thread(thread_id)` — entire thread in chronological order
- `get_email_stats(filters)` — aggregate statistics

Max 5 reasoning steps → final answer with citations (message_id + date + participants).

---

## 5. Tech Stack

| Component | Choice |
|-----------|--------|
| Language | Python 3.11+ |
| Email parsing | `email` stdlib |
| HTML | `readability-lxml` + `beautifulsoup4` |
| OCR/Documents | Docling |
| Metadata | `dateutil`, regex (no spaCy) |
| Embedding | winner from nomic/bge/e5 (sentence-transformers) |
| Vector DB | Qdrant (Docker) |
| Metadata DB | SQLite + FTS5 |
| Re-ranker | `bge-reranker-v2-m3` (CPU) |
| LLM | Qwen3 32B Q4_K_M GGUF (~20GB VRAM) |
| LLM serving | `llama.cpp` (llama-server) |
| Agent | Custom ReAct loop (~100 lines, no LangChain) |
| UI (dev) | CLI (`rich` + `click`), API later |
| Logging | `structlog` + `tqdm` for pipeline progress |
| Parallelism | `multiprocessing` |

---

## 6. Implementation Order

### Phase 1: Foundation
1. **Project structure + venv** — `python -m venv .venv`, pyproject.toml, `pip install -e ".[dev]"`, src/smartcat/...
2. **MIME parser** — `parsing/mime_parser.py` (Python `email` module), error handling, test on 100 files
3. **SQLite storage** — full schema (including email_instances, chunks, processing_errors), CRUD, FTS5

### Phase 2: Extraction and Threading
4. **Metadata extraction** — participants (canonical names), dates, amounts, documents
5. **Thread reconstruction** — In-Reply-To first, subject fallback second
6. **Batch ingestion** — process all 517K files into SQLite, resumable, progress bar (~15-30 min)

### Phase 3: Embedding and Indexing
7. **Chunking** — hierarchical email-aware chunker, saving to SQLite
8. **Embedding model eval** — compare 3 models on 20 queries, choose the best
9. **Batch embedding + Qdrant** — batch embed, Docker Qdrant, payload indexes, upsert (~25 min GPU)

### Phase 4: Retrieval
10. **Hybrid search** — vector + FTS5 → RRF fusion
11. **Re-ranker** — cross-encoder on CPU + metadata boost
12. **CLI search** — `smartcat search "query"` for testing retrieval

### Phase 5: Agent and LLM
13. **LLM setup** — Qwen3 32B GGUF + llama-server (VRAM lifecycle: unload embedding, load LLM)
14. **Agent tools** — implementation of 7 tools
15. **ReAct loop + CLI chat** — `smartcat chat` with streaming responses

### Phase 6: Production Readiness
16. **Docling integration** — attachment pipeline for production data
17. **Evaluation** — 50 test queries, precision/recall, prompt tuning
18. **FastAPI** — REST endpoints when CLI is stable

---

## Key Files

- `G:/Proj/SmartCat/maildir/` — primary source (517K files, 150 users)
- `G:/Proj/SmartCat/maildir/allen-p/` — integration test (3034 files)
- `G:/Proj/SmartCat/emails.csv` — validation and prototyping

## Project Structure (target)
```
smartcat/
  pyproject.toml
  src/smartcat/
    __init__.py
    config.py
    parsing/
      mime_parser.py
      metadata.py
      threading.py
    storage/
      sqlite_store.py
      qdrant_store.py
    chunking/
      email_chunker.py
    embedding/
      embedder.py
    retrieval/
      hybrid_search.py
      reranker.py
    agent/
      tools.py
      react_agent.py
    cli/
      main.py        # click CLI: search, chat, ingest
  scripts/
    ingest_maildir.py
    eval_embeddings.py
  tests/
```

## Verification
- Unit tests for the parser on 100 emails from different users
- Count verification against emails.csv
- Embedding model comparison on 20 queries (Recall@10)
- 20 manual queries for hybrid search quality
- 50 test queries with expected answers for end-to-end evaluation
- Thread reconstruction verification on known chains (allen-p)
- VRAM monitoring during ingestion/serving mode switching
