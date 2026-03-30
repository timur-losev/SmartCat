# SmartCat System Design

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                           DATA SOURCES                                      │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐                      │
│  │   Maildir/    │  │  Production  │  │   Future:    │                      │
│  │  517K .eml    │  │  30GB+ HTML  │  │  IMAP/API    │                      │
│  │  (Enron)      │  │  PDF/DOCX    │  │  connector   │                      │
│  └──────┬───────┘  └──────┬───────┘  └──────────────┘                      │
└─────────┼──────────────────┼────────────────────────────────────────────────┘
          │                  │
          ▼                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                     OFFLINE PIPELINE (batch processing)                      │
│                                                                             │
│  ┌─────────────────────────────────────────────────────────┐               │
│  │  Phase 1: INGESTION          scripts/ingest_maildir.py  │               │
│  │  ┌──────────┐  ┌───────────┐  ┌──────────┐             │               │
│  │  │  MIME     │→│  Thread   │→│ Metadata  │             │               │
│  │  │  Parser   │  │  Recon    │  │ Extract   │             │               │
│  │  │          │  │ (headers/ │  │ (dates,   │             │               │
│  │  │ email    │  │  subject) │  │  amounts, │             │               │
│  │  │ stdlib   │  │          │  │  deals)   │             │               │
│  │  └──────────┘  └───────────┘  └──────────┘             │               │
│  └────────────────────────┬────────────────────────────────┘               │
│                           ▼                                                 │
│  ┌─────────────────────────────────────────────────────────┐               │
│  │  Phase 2: DOCLING CLEANUP    scripts/batch_convert.py   │               │
│  │  ┌──────────────────┐  ┌──────────────────┐             │               │
│  │  │  HTML body →     │  │  PDF/DOC/DOCX →  │             │               │
│  │  │  clean markdown  │  │  extracted text   │             │               │
│  │  │  (Docling)       │  │  (Docling)        │             │               │
│  │  └──────────────────┘  └──────────────────┘             │               │
│  └────────────────────────┬────────────────────────────────┘               │
│                           ▼                                                 │
│  ┌─────────────────────────────────────────────────────────┐               │
│  │  Phase 3: CHUNKING           scripts/batch_chunk.py     │               │
│  │  ┌────────┐ ┌────────┐ ┌────────┐ ┌────────────┐       │               │
│  │  │L1      │ │L2      │ │L3      │ │L4          │       │               │
│  │  │Summary │ │Body    │ │Quoted/ │ │Attachment  │       │               │
│  │  │chunk   │ │chunks  │ │FW/RE   │ │chunks      │       │               │
│  │  │        │ │(512tok)│ │chunks  │ │(Docling)   │       │               │
│  │  └────────┘ └────────┘ └────────┘ └────────────┘       │               │
│  └────────────────────────┬────────────────────────────────┘               │
│                           ▼                                                 │
│  ┌─────────────────────────────────────────────────────────┐               │
│  │  Phase 4: EMBEDDING          scripts/batch_embed.py     │               │
│  │  ┌────────────────────────┐                              │               │
│  │  │  nomic-embed-text-v1.5 │  658K chunks → 768d vectors │               │
│  │  │  GPU batch (CUDA)      │  ~25 min on RTX 4090        │               │
│  │  └────────────────────────┘                              │               │
│  └────────────────────────┬────────────────────────────────┘               │
│                           ▼                                                 │
│  ┌─────────────────────────────────────────────────────────┐               │
│  │  Phase 5: QA EXTRACTION   scripts/batch_qa_extract.py   │               │
│  │  ┌────────────────────────────────────────────┐         │               │
│  │  │  Email Threads → Qwen3 14B (GPU) → QA pairs│         │               │
│  │  │  Tiered: 5+ emails → 3+ → 2+              │         │               │
│  │  │  ~6s/thread, pause/resume via SQLite       │         │               │
│  │  └────────────────────────────────────────────┘         │               │
│  │  ┌────────────────────────────────────────────┐         │               │
│  │  │  Embed QA questions → Qdrant (same coll.)  │         │               │
│  │  │  scripts/embed_qa.py                       │         │               │
│  │  └────────────────────────────────────────────┘         │               │
│  └─────────────────────────────────────────────────────────┘               │
└─────────────────────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                        STORAGE LAYER                                        │
│                                                                             │
│  ┌──────────────────────────────┐  ┌──────────────────────────────┐        │
│  │     SQLite + FTS5            │  │       Qdrant (Docker)        │        │
│  │     data/smartcat.db         │  │       localhost:6333         │        │
│  │                              │  │                              │        │
│  │  ┌────────────────────────┐  │  │  ┌────────────────────────┐  │        │
│  │  │ emails      (245K)    │  │  │  │ Collection: emails     │  │        │
│  │  │ email_instances       │  │  │  │                        │  │        │
│  │  │ participants (70K)    │  │  │  │ 658K doc vectors       │  │        │
│  │  │ email_participants    │  │  │  │ + QA question vectors  │  │        │
│  │  │ entities    (593K)    │  │  │  │                        │  │        │
│  │  │ attachments (11K)     │  │  │  │ 768d, cosine           │  │        │
│  │  │ chunks      (658K)    │  │  │  │                        │  │        │
│  │  │ qa_pairs    (~34K)    │  │  │  │ Payload indexes:       │  │        │
│  │  │ qa_progress           │  │  │  │  date_sent, from,      │  │        │
│  │  │ processed_files       │  │  │  │  thread_id, chunk_type │  │        │
│  │  │                       │  │  │  │  has_monetary,         │  │        │
│  │  │ FTS5 indexes:         │  │  │  │  has_attachment        │  │        │
│  │  │  emails_fts           │  │  │  └────────────────────────┘  │        │
│  │  │  attachments_fts      │  │  │                              │        │
│  │  └────────────────────────┘  │  └──────────────────────────────┘        │
└──┴──────────────────────────────┴──────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      SERVING PIPELINE (online)                              │
│                                                                             │
│  ┌──────────┐                                                              │
│  │  User    │                                                              │
│  │  Query   │                                                              │
│  └────┬─────┘                                                              │
│       ▼                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐           │
│  │  CLI: smartcat chat / search / participant / stats           │           │
│  │  src/smartcat/cli/main.py                                    │           │
│  └────┬─────────────────────────────────────────────────────────┘           │
│       ▼                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐           │
│  │  ReAct Agent                  react_agent.py                 │           │
│  │  ┌────────────────────────────────────────────────┐          │           │
│  │  │  Thinking → Tool Call → Result → ... → Answer  │          │           │
│  │  │  Max 5 steps, citations required               │          │           │
│  │  └────────────────────────────────────────────────┘          │           │
│  │                          │                                    │           │
│  │  ┌───────────────────────┼────────────────────────┐          │           │
│  │  │     Agent Tools       │        tools.py        │          │           │
│  │  │                       ▼                        │          │           │
│  │  │  search_emails ──→ HybridSearcher + Reranker   │          │           │
│  │  │  search_by_participant ──→ SQLite query         │          │           │
│  │  │  search_by_date_range ──→ SQLite + FTS          │          │           │
│  │  │  search_entities ──→ entities table             │          │           │
│  │  │  get_email ──→ full email by ID                 │          │           │
│  │  │  get_thread ──→ thread conversation             │          │           │
│  │  │  get_email_stats ──→ aggregate stats            │          │           │
│  │  └────────────────────────────────────────────────┘          │           │
│  └──────────────────────────────────────────────────────────────┘           │
│       │                                                                     │
│       ▼                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐           │
│  │  Hybrid Search               hybrid_search.py                │           │
│  │                                                              │           │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │           │
│  │  │ Channel 1   │  │ Channel 2   │  │ Channel 3 (new)     │  │           │
│  │  │ Vector      │  │ Keyword     │  │ QA pairs            │  │           │
│  │  │ Search      │  │ Search      │  │ (pre-computed       │  │           │
│  │  │             │  │             │  │  answers)            │  │           │
│  │  │ Qdrant      │  │ SQLite      │  │                     │  │           │
│  │  │ cosine sim  │  │ FTS5 BM25   │  │ Qdrant              │  │           │
│  │  │ top-60      │  │ top-60      │  │ chunk_type='qa'     │  │           │
│  │  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │           │
│  │         └────────────┬───┘                     │             │           │
│  │                      ▼                         │             │           │
│  │              ┌───────────────┐                  │             │           │
│  │              │  RRF Fusion   │                  │             │           │
│  │              │  k=60         │                  │             │           │
│  │              │  → top-30     │                  │             │           │
│  │              └───────┬───────┘                  │             │           │
│  │                      ▼                         │             │           │
│  │              ┌───────────────┐                  │             │           │
│  │              │  Reranker     │    QA matches ───┘             │           │
│  │              │  bge-reranker │    (boosted,                   │           │
│  │              │  -v2-m3 (CPU) │     prepended)                │           │
│  │              │  → top-10     │                               │           │
│  │              └───────────────┘                               │           │
│  └──────────────────────────────────────────────────────────────┘           │
└─────────────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────────────┐
│                      INFRASTRUCTURE                                         │
│                                                                             │
│  ┌───────────────────┐  ┌───────────────────┐  ┌────────────────────┐      │
│  │  llama-server     │  │  Docker           │  │  Python Process    │      │
│  │                   │  │                   │  │                    │      │
│  │  Qwen3 32B Q8     │  │  Qdrant           │  │  CLI / Agent       │      │
│  │  (agent answers)  │  │  Port 6333        │  │                    │      │
│  │       OR          │  │  Volume:          │  │  nomic-embed       │      │
│  │  Qwen3 14B Q8     │  │   qdrant_data     │  │  (CPU, in-proc)    │      │
│  │  (QA extraction)  │  │                   │  │                    │      │
│  │                   │  │                   │  │  bge-reranker      │      │
│  │  Port 8080        │  │                   │  │  (CPU, in-proc)    │      │
│  │  GPU: RTX 4090    │  │                   │  │                    │      │
│  │  24GB VRAM        │  │                   │  │                    │      │
│  └───────────────────┘  └───────────────────┘  └────────────────────┘      │
│                                                                             │
│  VRAM Lifecycle:                                                            │
│  ┌──────────────────────────────────────────────────────────┐              │
│  │  Ingestion:  embedding (GPU, ~0.5GB) — batch processing  │              │
│  │  QA Extract: Qwen3 14B Q8 (GPU, ~15GB) — batch LLM      │              │
│  │  Serving:    Qwen3 32B Q8 (GPU, ~22GB) + embed (CPU)     │              │
│  │  ⚠ Never load all models simultaneously on GPU           │              │
│  └──────────────────────────────────────────────────────────┘              │
│                                                                             │
│  Evaluation:  scripts/run_eval.py  →  data/eval_baseline.json              │
│               50 questions, 5 categories, auto-scored                       │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Data Flow Summary

```
maildir/*.eml
  → [ingest_maildir.py] → SQLite (emails, participants, entities, threads)
  → [batch_convert.py]  → HTML cleanup via Docling
  → [batch_chunk.py]    → SQLite (chunks: L1-L4)
  → [batch_embed.py]    → Qdrant (658K vectors, 768d)
  → [batch_qa_extract]  → SQLite (qa_pairs) → [embed_qa.py] → Qdrant (QA vectors)

User query
  → CLI chat
  → ReAct Agent (Qwen3 32B, llama-server)
  → Tool: search_emails
    → HybridSearcher
      → Qdrant vector search (docs + QA)
      → SQLite FTS5 BM25
      → RRF fusion → Reranker
    → Agent reasoning → Final answer with citations
```
