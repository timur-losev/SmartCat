"""Embed QA questions into Qdrant alongside document chunks.

Embeds the QUESTION field (not answer) — so user queries match similar questions.
Answer is stored in payload for direct retrieval.

Usage:
    python scripts/embed_qa.py --db data/smartcat.db --device cuda
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from tqdm import tqdm

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from smartcat.embedding.embedder import Embedder
from smartcat.storage.qdrant_store import QdrantStore
from smartcat.storage.sqlite_store import EmailStore


def main():
    parser = argparse.ArgumentParser(description="Embed QA pairs into Qdrant")
    parser.add_argument("--db", default="data/smartcat.db")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--batch-size", type=int, default=256)
    args = parser.parse_args()

    store = EmailStore(Path(args.db))
    store.connect()
    embedder = Embedder(device=args.device)
    qdrant = QdrantStore()

    # Get all QA pairs
    offset = 0
    total = 0
    t0 = time.time()

    while True:
        pairs = store.get_qa_pairs_for_embedding(limit=10000, offset=offset)
        if not pairs:
            break

        questions = [p["question"] for p in pairs]
        ids = [f"qa_{p['id']}" for p in pairs]
        payloads = [
            {
                "chunk_type": "qa",
                "question": p["question"],
                "answer": p["answer"],
                "thread_id": p.get("thread_id", ""),
                "email_id": p.get("email_id", 0),
                "from_address": p.get("from_address", ""),
                "date_sent": p.get("date_sent", ""),
                "subject": p.get("subject", ""),
            }
            for p in pairs
        ]

        # Embed questions (not answers — we want to match user queries to similar questions)
        print(f"Embedding {len(questions)} QA questions...")
        vectors = embedder.embed_texts(questions, normalize=True)

        # Upsert to same Qdrant collection
        qdrant.upsert_batch(ids, vectors, payloads, batch_size=args.batch_size)

        total += len(pairs)
        offset += len(pairs)
        print(f"  Upserted {total} QA vectors")

    elapsed = time.time() - t0
    print(f"\nDone: {total} QA pairs embedded in {elapsed:.1f}s")
    print(f"Collection now has document chunks + QA pairs")

    embedder.unload()
    store.close()


if __name__ == "__main__":
    main()
