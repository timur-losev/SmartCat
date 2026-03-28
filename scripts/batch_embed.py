"""Batch embedding: embed all chunks and upsert into Qdrant.

Usage:
    # Ensure Qdrant Docker is running:
    #   docker run -d --name qdrant -p 6333:6333 qdrant/qdrant
    python scripts/batch_embed.py [--db data/smartcat.db] [--batch-size 512] [--device cuda] [--recreate]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import gc

import numpy as np
import structlog
import torch
from tqdm import tqdm

from smartcat.config import (
    SQLITE_DB_PATH,
    EMBEDDING_MODEL,
    EMBEDDING_BATCH_SIZE,
)
from smartcat.embedding.embedder import Embedder
from smartcat.storage.qdrant_store import QdrantStore
from smartcat.storage.sqlite_store import EmailStore

log = structlog.get_logger()


def build_payload(chunk: dict) -> dict:
    """Build Qdrant payload from a chunk row."""
    return {
        "chunk_id": chunk["chunk_id"],
        "email_id": chunk["email_id"],
        "message_id": chunk.get("message_id", ""),
        "chunk_type": chunk["chunk_type"],
        "chunk_index": chunk["chunk_index"],
        "date_sent": chunk.get("date_sent", ""),
        "from_address": chunk.get("from_address", ""),
        "thread_id": chunk.get("thread_id", ""),
        "has_attachments": bool(chunk.get("has_attachments", 0)),
        "token_count": chunk["token_count"],
    }


def batch_embed_and_upsert(
    store: EmailStore,
    embedder: Embedder,
    qdrant: QdrantStore,
    batch_size: int = 512,
    db_fetch_size: int = 10000,
    resume_offset: int = 0,
) -> int:
    """Embed all chunks and upsert into Qdrant.

    Fetches chunks from SQLite in pages, embeds in GPU batches,
    and upserts into Qdrant.

    Returns total vectors upserted.
    """
    total_upserted = 0
    offset = resume_offset

    total_chunks = store.get_chunk_count()
    pbar = tqdm(total=total_chunks, initial=resume_offset, desc="Embedding", unit="chunk")

    while True:
        chunks = store.get_chunks_for_embedding(limit=db_fetch_size, offset=offset)
        if not chunks:
            break

        texts = [c["text"] for c in chunks]
        ids = [c["chunk_id"] for c in chunks]
        payloads = [build_payload(c) for c in chunks]

        # Process in smaller sub-batches to control RAM + VRAM
        # Each sub-batch: embed → upsert → free memory
        embed_batch = 2000
        for i in range(0, len(texts), embed_batch):
            end = min(i + embed_batch, len(texts))
            sub_texts = texts[i:end]
            sub_ids = ids[i:end]
            sub_payloads = payloads[i:end]

            vectors = embedder.embed_texts(sub_texts, show_progress=False)

            # Upsert immediately, free large objects
            n = len(sub_texts)
            qdrant.upsert_batch(sub_ids, vectors, sub_payloads)
            del vectors, sub_texts, sub_ids, sub_payloads

            total_upserted += n
            pbar.update(n)

            # Clear CUDA cache periodically (not every batch — too much overhead)
            if total_upserted % 20000 == 0:
                gc.collect()
                torch.cuda.empty_cache()

        log.info(
            "embed.batch_done",
            resume_cmd=f"--resume-offset {offset + db_fetch_size}",
            upserted=total_upserted,
            offset=offset,
        )

        offset += db_fetch_size

    pbar.close()
    return total_upserted


def main():
    parser = argparse.ArgumentParser(description="Batch embed chunks into Qdrant")
    parser.add_argument("--db", type=str, default=str(SQLITE_DB_PATH))
    parser.add_argument("--model", type=str, default=EMBEDDING_MODEL)
    parser.add_argument("--batch-size", type=int, default=EMBEDDING_BATCH_SIZE)
    parser.add_argument("--device", type=str, default="cuda",
                        help="Device: cuda, cpu, or auto")
    parser.add_argument("--recreate", action="store_true",
                        help="Recreate Qdrant collection (deletes existing vectors)")
    parser.add_argument("--resume-offset", type=int, default=0,
                        help="Resume from this chunk offset (skip already embedded)")
    args = parser.parse_args()

    store = EmailStore(Path(args.db))
    chunk_count = store.get_chunk_count()
    log.info("embed.start", chunks=chunk_count, model=args.model, device=args.device)

    if chunk_count == 0:
        log.error("embed.no_chunks", msg="Run batch_chunk.py first")
        sys.exit(1)

    # Init embedder
    embedder = Embedder(
        model_name=args.model,
        device=args.device,
        batch_size=args.batch_size,
    )
    dim = embedder.dimension
    log.info("embed.model_loaded", dim=dim)

    # Init Qdrant
    qdrant = QdrantStore(embedding_dim=dim)
    qdrant.create_collection(recreate=args.recreate)

    t0 = time.time()
    total = batch_embed_and_upsert(
        store=store,
        embedder=embedder,
        qdrant=qdrant,
        batch_size=args.batch_size,
        resume_offset=args.resume_offset,
    )
    elapsed = time.time() - t0

    # Stats
    info = qdrant.get_collection_info()
    log.info(
        "embed.done",
        vectors_upserted=total,
        qdrant_vectors=info.get("vectors_count", 0),
        elapsed_sec=f"{elapsed:.1f}",
        rate=f"{total / max(elapsed, 1):.0f} vec/s",
    )

    embedder.unload()
    store.close()


if __name__ == "__main__":
    main()
