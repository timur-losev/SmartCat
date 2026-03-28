"""Evaluate embedding models on a dev query set.

Compares Recall@10 across candidate models using a small set of
manually-labeled queries with expected email matches.

Usage:
    python scripts/eval_embeddings.py [--db data/smartcat.db] [--device cuda]
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import numpy as np
import structlog

from smartcat.config import EMBEDDING_CANDIDATES, SQLITE_DB_PATH
from smartcat.embedding.embedder import Embedder
from smartcat.storage.sqlite_store import EmailStore

log = structlog.get_logger()

# ─── Dev query set (for model selection only, NOT final evaluation) ───
# Each entry: query string, list of expected email_id or subject substrings
# These are verified against the Enron maildir/allen-p subset + general corpus
DEV_QUERIES = [
    {
        "query": "natural gas trading west coast positions",
        "expected_subjects": ["west", "position"],
        "description": "Core trading topic",
    },
    {
        "query": "meeting schedule next week",
        "expected_subjects": ["meeting", "schedule"],
        "description": "Calendar/scheduling",
    },
    {
        "query": "contract negotiation terms and conditions",
        "expected_subjects": ["contract", "terms"],
        "description": "Legal/contract",
    },
    {
        "query": "quarterly financial report revenue",
        "expected_subjects": ["quarterly", "report", "revenue", "financial"],
        "description": "Financial reporting",
    },
    {
        "query": "pipeline capacity allocation gas transport",
        "expected_subjects": ["pipeline", "capacity", "transport", "gas"],
        "description": "Energy infrastructure",
    },
    {
        "query": "California power crisis energy prices",
        "expected_subjects": ["california", "power", "energy", "price"],
        "description": "CA energy crisis (major Enron topic)",
    },
    {
        "query": "employee benefits vacation time off",
        "expected_subjects": ["benefit", "vacation", "time off"],
        "description": "HR topics",
    },
    {
        "query": "risk management hedging exposure",
        "expected_subjects": ["risk", "hedg", "exposure"],
        "description": "Risk/trading",
    },
    {
        "query": "deal confirmation counterparty settlement",
        "expected_subjects": ["deal", "confirm", "settle", "counterpart"],
        "description": "Deal ops",
    },
    {
        "query": "forwarded attachment document review",
        "expected_subjects": ["forward", "attach", "document", "review"],
        "description": "Document workflow",
    },
    {
        "query": "weather forecast temperature demand",
        "expected_subjects": ["weather", "temperature", "demand", "forecast"],
        "description": "Weather impact on energy",
    },
    {
        "query": "price curve shift basis spread",
        "expected_subjects": ["curve", "shift", "basis", "spread", "price"],
        "description": "Trading analytics",
    },
    {
        "query": "FERC regulatory compliance filing",
        "expected_subjects": ["ferc", "regulat", "compliance", "filing"],
        "description": "Regulatory",
    },
    {
        "query": "Enron stock options compensation",
        "expected_subjects": ["stock", "option", "compens", "enron"],
        "description": "Compensation",
    },
    {
        "query": "holiday party office announcement",
        "expected_subjects": ["holiday", "party", "office", "announc"],
        "description": "Office culture",
    },
    {
        "query": "system outage IT support technical issue",
        "expected_subjects": ["system", "outage", "support", "technical", "issue"],
        "description": "IT/technical",
    },
    {
        "query": "invoice payment amount due accounts",
        "expected_subjects": ["invoice", "payment", "amount", "account"],
        "description": "Accounts payable",
    },
    {
        "query": "project timeline deadline deliverables",
        "expected_subjects": ["project", "timeline", "deadline", "deliver"],
        "description": "Project management",
    },
    {
        "query": "customer complaint service issue resolution",
        "expected_subjects": ["customer", "complaint", "service", "issue"],
        "description": "Customer service",
    },
    {
        "query": "market analysis daily report summary",
        "expected_subjects": ["market", "analysis", "daily", "report", "summary"],
        "description": "Market intelligence",
    },
]


def evaluate_model(
    model_name: str,
    store: EmailStore,
    device: str = "cuda",
    top_k: int = 10,
) -> dict:
    """Evaluate a single embedding model on the dev query set.

    Uses FTS5 to get ground truth candidates, then measures how well
    vector search (embedding similarity) retrieves those same results.

    Returns dict with model name, avg recall, per-query scores.
    """
    embedder = Embedder(model_name=model_name, device=device)

    # First, embed a sample of chunks for evaluation (10K is enough for Recall comparison)
    chunks = store.get_chunks_for_embedding(limit=10000)
    if not chunks:
        log.error("eval.no_chunks", model=model_name)
        return {"model": model_name, "error": "no chunks"}

    texts = [c["text"] for c in chunks]
    chunk_email_ids = [c["email_id"] for c in chunks]

    log.info("eval.embedding_chunks", model=model_name, count=len(texts))
    t0 = time.time()
    # Use smaller batch size to avoid OOM
    embedder.batch_size = 64
    doc_vectors = embedder.embed_texts(texts, show_progress=True)
    embed_time = time.time() - t0
    log.info("eval.chunks_embedded", time=f"{embed_time:.1f}s",
             rate=f"{len(texts)/embed_time:.0f} chunks/s")

    results = []

    # Build a keyword index over the sampled chunks for fair comparison
    # (FTS searches ALL emails, but we only have vectors for 10K chunks)
    chunk_texts_lower = [t.lower() for t in texts]

    for q in DEV_QUERIES:
        query = q["query"]
        query_terms = query.lower().split()

        # Ground truth: keyword match within the SAME 10K chunks we embedded
        # An email counts as relevant if any query term appears in its chunk text
        gt_indices = set()
        for idx, text in enumerate(chunk_texts_lower):
            matching_terms = sum(1 for term in query_terms if term in text)
            if matching_terms >= 2:  # at least 2 query terms match
                gt_indices.add(idx)

        if not gt_indices:
            results.append({
                "query": query,
                "recall": 0.0,
                "gt_count": 0,
                "note": "no keyword matches in sample",
            })
            continue

        # Vector search: embed query, find nearest neighbors
        query_vec = embedder.embed_query(query)
        similarities = np.dot(doc_vectors, query_vec)
        top_indices = set(np.argsort(similarities)[-top_k:][::-1].tolist())

        # Recall@K: how many keyword-matched chunks are in vector top-K?
        hits = len(gt_indices & top_indices)
        recall = hits / min(len(gt_indices), top_k) if gt_indices else 0.0

        # Also compute MRR: rank of first relevant result
        sorted_indices = np.argsort(similarities)[::-1]
        mrr = 0.0
        for rank, idx in enumerate(sorted_indices, 1):
            if idx in gt_indices:
                mrr = 1.0 / rank
                break

        results.append({
            "query": query,
            "recall": recall,
            "mrr": mrr,
            "gt_count": len(gt_indices),
            "hits": hits,
        })

    avg_recall = np.mean([r["recall"] for r in results])
    avg_mrr = np.mean([r.get("mrr", 0) for r in results])
    embedder.unload()

    return {
        "model": model_name,
        "avg_recall_at_10": float(avg_recall),
        "avg_mrr": float(avg_mrr),
        "embed_time": embed_time,
        "chunks_evaluated": len(texts),
        "per_query": results,
    }


def main():
    parser = argparse.ArgumentParser(description="Evaluate embedding model candidates")
    parser.add_argument("--db", type=str, default=str(SQLITE_DB_PATH))
    parser.add_argument("--device", type=str, default="cuda")
    parser.add_argument("--models", nargs="+", default=EMBEDDING_CANDIDATES)
    args = parser.parse_args()

    store = EmailStore(Path(args.db))

    chunk_count = store.get_chunk_count()
    log.info("eval.start", chunks=chunk_count, models=args.models)

    if chunk_count == 0:
        log.error("eval.no_chunks", msg="Run batch_chunk.py first")
        sys.exit(1)

    all_results = []
    for model_name in args.models:
        log.info("eval.model_start", model=model_name)
        result = evaluate_model(model_name, store, device=args.device)
        all_results.append(result)

        print(f"\n{'='*60}")
        print(f"Model: {model_name}")
        print(f"Avg Recall@10: {result['avg_recall_at_10']:.3f}")
        print(f"Avg MRR:       {result.get('avg_mrr', 0):.3f}")
        print(f"Embed time:    {result.get('embed_time', 0):.1f}s")
        print(f"{'='*60}")

        for qr in result.get("per_query", []):
            status = "OK" if qr["recall"] > 0.3 else "LOW"
            print(f"  [{status}] R={qr['recall']:.2f} MRR={qr.get('mrr',0):.2f} "
                  f"GT={qr.get('gt_count',0)} H={qr.get('hits',0)} | {qr['query'][:50]}")

    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    all_results.sort(key=lambda x: x.get("avg_recall_at_10", 0), reverse=True)
    for r in all_results:
        print(f"  {r['model']:<45} Recall@10={r.get('avg_recall_at_10', 0):.3f}")

    best = all_results[0]
    print(f"\nBest model: {best['model']} (Recall@10={best['avg_recall_at_10']:.3f})")
    print("Update EMBEDDING_MODEL in config.py to use this model.")

    store.close()


if __name__ == "__main__":
    main()
