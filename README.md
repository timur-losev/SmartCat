# SmartCat

Local-first RAG system for semantic email search with AI reasoning. Hybrid retrieval (vector + BM25 + QA pairs) with RRF fusion, ReAct agent with 7 tools, cross-encoder reranking. Tested on Enron corpus (245K emails, 658K chunks, 31K QA pairs). Runs fully offline on consumer GPU via llama.cpp.

## What it does

Ask natural language questions about email archives — SmartCat finds relevant emails, reasons over threads, and answers with citations.

```
You: When did Enron file for Chapter 11 bankruptcy?

SmartCat: Enron filed for Chapter 11 bankruptcy on December 2, 2001.
This is confirmed by multiple emails from Ken Lay, Office of the Chairman:
- [2001-12-02] Message-ID: 1752967.1075840036795.JavaMail.evans@thyme
- [2001-12-02] Message-ID: 18978289.1075841408194.JavaMail.evans@thyme
```

## Architecture

```
maildir (.eml files)
  -> MIME parser -> threading reconstruction -> entity extraction -> SQLite
  -> Docling cleanup (HTML -> markdown, PDF/DOCX -> text)
  -> hierarchical chunker (L1 summary, L2 body, L3 quoted, L4 attachment)
  -> nomic-embed-text-v1.5 (768d) -> Qdrant (658K doc vectors)
  -> QA extraction (Qwen3 14B) -> 31K QA pairs -> Qdrant (QA vectors)

User query
  -> ReAct Agent (Qwen3 32B, llama.cpp)
  -> HybridSearcher
     |- Channel 1: Vector search (Qdrant, cosine, top-60)
     |- Channel 2: Keyword search (SQLite FTS5 BM25, top-60)
     |- Channel 3: QA pairs (pre-computed answers)
     -> RRF fusion (k=60) -> top-30
     -> Reranker (bge-reranker-v2-m3, cross-encoder) -> top-10
  -> Agent reasoning (max 5 steps) -> Answer with citations
```

## Features

- **Hybrid search**: vector similarity + keyword BM25 + QA pairs, fused with Reciprocal Rank Fusion
- **ReAct agent**: multi-step reasoning with 7 tools (search, participant lookup, date range, entity search, thread retrieval, email stats)
- **QA enrichment**: LLM-extracted question-answer pairs from email threads boost retrieval accuracy
- **Cross-encoder reranking**: bge-reranker-v2-m3 re-scores candidates for precision
- **Hierarchical chunking**: email-aware L1-L4 chunks with metadata payloads
- **Thread reconstruction**: header-based + subject normalization fallback
- **Entity extraction**: monetary amounts, dates, document refs, deal/contract IDs
- **Docling integration**: HTML body cleanup and PDF/DOCX/PPTX attachment extraction
- **Multi-turn chat**: conversation history for follow-up questions
- **Pause/resume**: all batch pipelines are resumable (Ctrl+C safe)
- **Fully local**: no cloud APIs, runs on single machine with consumer GPU

## Tech Stack

| Component | Choice |
|-----------|--------|
| Email parsing | Python `email` stdlib + `dateutil` |
| Doc processing | Docling 2.82 (HTML/PDF/DOCX -> markdown) |
| Embedding | `nomic-ai/nomic-embed-text-v1.5` (768d, 137M params) |
| Vector DB | Qdrant (Docker, cosine distance, payload filtering) |
| Metadata DB | SQLite + FTS5 (BM25 keyword search, WAL mode) |
| Reranker | `BAAI/bge-reranker-v2-m3` (cross-encoder, CPU) |
| LLM (agent) | Qwen3 32B Q8_0 GGUF via llama.cpp (33 GPU layers) |
| LLM (QA extraction) | Qwen3 14B Q8_0 GGUF via llama.cpp (fully on GPU) |
| CLI | `click` + `rich` |
| Logging | `structlog` + `tqdm` |

## Hardware Requirements

- **GPU**: 16+ GB VRAM (RTX 3090/4090 recommended)
- **RAM**: 32+ GB
- **Storage**: ~50GB (model weights + vector DB + SQLite)

## Quick Start

### Prerequisites

```bash
# Docker (for Qdrant)
docker run -d --name qdrant -p 6333:6333 -v qdrant_data:/qdrant/storage qdrant/qdrant

# Download LLM models
# Qwen3 32B Q8 for agent (~33GB)
# Qwen3 14B Q8 for QA extraction (~15GB)
```

### Installation

```bash
git clone https://github.com/timur-losev/SmartCat.git
cd SmartCat
python -m venv .venv
.venv\Scripts\activate      # Windows
pip install -e ".[dev]"
```

### Data Pipeline

```bash
# 1. Ingest emails from maildir
python scripts/ingest_maildir.py --maildir maildir --db data/smartcat.db

# 2. Clean HTML bodies via Docling
python scripts/batch_convert.py --db data/smartcat.db --phase all

# 3. Generate chunks
python scripts/batch_chunk.py --db data/smartcat.db

# 4. Embed chunks into Qdrant
python scripts/batch_embed.py --db data/smartcat.db --device cuda --batch-size 16 --recreate

# 5. Extract QA pairs (start llama-server with 14B first)
scripts/start_llama_14b.bat
python scripts/batch_qa_extract.py --db data/smartcat.db --tier 1

# 6. Embed QA questions
python scripts/embed_qa.py --db data/smartcat.db --device cuda
```

### Usage

```bash
# Start LLM server (32B for agent)
scripts/start_llama.bat

# Interactive chat
set PYTHONPATH=src
python -m smartcat.cli.main chat --db data/smartcat.db

# Search without LLM
python -m smartcat.cli.main search --db data/smartcat.db "California energy crisis"

# Search by participant
python -m smartcat.cli.main participant --db data/smartcat.db "jeff.skilling"

# Database stats
python -m smartcat.cli.main stats --db data/smartcat.db
```

### Evaluation

```bash
# Run 50-question evaluation
python scripts/run_eval.py --db data/smartcat.db --output data/eval_results.json
```

## Project Structure

```
smartcat/
  src/smartcat/
    parsing/
      mime_parser.py      # MIME email parser with attachment extraction
      metadata.py         # Entity extraction (monetary, dates, deals)
      threading.py        # Thread reconstruction (headers + subject)
    storage/
      sqlite_store.py     # SQLite + FTS5 storage layer
      qdrant_store.py     # Qdrant vector store wrapper
    chunking/
      email_chunker.py    # Hierarchical L1-L4 chunker
    embedding/
      embedder.py         # Sentence-transformers wrapper
    conversion/
      docling_converter.py # Docling HTML/PDF/DOCX converter
    retrieval/
      hybrid_search.py    # 3-channel search + RRF fusion
      reranker.py         # Cross-encoder reranker
    agent/
      tools.py            # 7 agent tools
      react_agent.py      # ReAct loop with conversation history
    cli/
      main.py             # Click CLI interface
  scripts/
    ingest_maildir.py     # Batch email ingestion
    batch_convert.py      # Batch Docling conversion
    batch_chunk.py        # Batch chunking
    batch_embed.py        # Batch embedding
    batch_qa_extract.py   # QA pair extraction (tiered, pause/resume)
    embed_qa.py           # QA question embedding
    run_eval.py           # Automated evaluation (50 questions)
    start_llama.bat       # Qwen3 32B launcher
    start_llama_14b.bat   # Qwen3 14B launcher
  docs/
    PLAN.md               # Original system design
    DOCLING_PLAN.md       # Docling integration plan
    SYSTEM_DESIGN.md      # Full architecture diagram
    EVAL_QUESTIONS.md     # 50 evaluation questions (5 categories)
  tests/
```

## Evaluation Results (Baseline)

Tested on Enron email corpus (245K emails):

| Metric | Value |
|--------|-------|
| Questions | 50 |
| With citations | 64% |
| Avg latency | 271s |
| Errors (timeout) | 8% |

| Category | Citations | Avg Latency |
|----------|-----------|-------------|
| Factual Lookup | 5/10 -> 9/10 (after QA) | 229s -> 117s |
| People | 5/10 -> 7/10 (after QA) | 310s -> 213s |
| Timeline | 8/10 | 243s |
| Topics | 9/10 | 279s |
| Reasoning | 5/10 | 294s |

## License

MIT
