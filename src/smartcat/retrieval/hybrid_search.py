"""Hybrid search combining vector, keyword, and structured queries with RRF fusion."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

import numpy as np
import structlog

from smartcat.config import SEARCH_TOP_K_PER_CHANNEL, RRF_K, RERANK_CANDIDATES
from smartcat.embedding.embedder import Embedder
from smartcat.storage.qdrant_store import QdrantStore
from smartcat.storage.sqlite_store import EmailStore

log = structlog.get_logger()


def reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[str, float]]],
    k: int = RRF_K,
) -> list[tuple[str, float]]:
    """Reciprocal Rank Fusion across multiple ranked result lists.

    Args:
        ranked_lists: List of ranked results, each as [(id, score), ...].
        k: RRF constant (higher = more weight to lower-ranked items).

    Returns:
        Fused results sorted by RRF score descending.
    """
    scores: dict[str, float] = defaultdict(float)

    for ranked in ranked_lists:
        for rank, (doc_id, _original_score) in enumerate(ranked, start=1):
            scores[doc_id] += 1.0 / (k + rank)

    fused = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return fused


class HybridSearcher:
    """Three-channel hybrid search: vector + keyword + structured."""

    def __init__(
        self,
        embedder: Embedder,
        qdrant: QdrantStore,
        sqlite: EmailStore,
        top_k_per_channel: int = SEARCH_TOP_K_PER_CHANNEL,
        rrf_k: int = RRF_K,
    ):
        self.embedder = embedder
        self.qdrant = qdrant
        self.sqlite = sqlite
        self.top_k = top_k_per_channel
        self.rrf_k = rrf_k

    def search(
        self,
        query: str,
        top_n: int = RERANK_CANDIDATES,
        vector_filters: Optional[dict] = None,
    ) -> list[dict[str, Any]]:
        """Execute hybrid search and return fused results.

        Args:
            query: Natural language search query.
            top_n: Number of results after fusion (before reranking).
            vector_filters: Optional Qdrant payload filters for vector channel.

        Returns:
            List of result dicts with message_id, score, and source info.
        """
        ranked_lists = []

        # Channel 1: Vector search
        try:
            query_vec = self.embedder.embed_query(query)
            vector_results = self.qdrant.search(
                query_vector=query_vec,
                limit=self.top_k,
                filters=vector_filters,
            )
            vector_ranked = [
                (r["payload"].get("email_id", r["id"]), r["score"])
                for r in vector_results
            ]
            ranked_lists.append(vector_ranked)
            log.debug("search.vector", results=len(vector_ranked))
        except Exception as e:
            log.warning("search.vector.failed", error=str(e))

        # Channel 2: Keyword search (BM25 via FTS5, covers emails + attachments)
        try:
            fts_results = self.sqlite.search_fts(query, limit=self.top_k)
            fts_ranked = [
                (r["email_id"], abs(r.get("rank", 0)))
                for r in fts_results
            ]
            ranked_lists.append(fts_ranked)
            log.debug("search.fts", results=len(fts_ranked))
        except Exception as e:
            log.warning("search.fts.failed", error=str(e))

        if not ranked_lists:
            return []

        # RRF fusion
        fused = reciprocal_rank_fusion(ranked_lists, k=self.rrf_k)

        # Take top_n and enrich with email metadata
        results = []
        for email_id, rrf_score in fused[:top_n]:
            email = self.sqlite.get_email(email_id)
            if email:
                results.append({
                    "email_id": email_id,
                    "message_id": email.get("message_id", ""),
                    "rrf_score": rrf_score,
                    "subject": email.get("subject", ""),
                    "from_address": email.get("from_address", ""),
                    "from_name": email.get("from_name", ""),
                    "date_sent": email.get("date_sent", ""),
                    "body_text": email.get("body_text", ""),
                    "thread_id": email.get("thread_id", ""),
                    "has_attachments": email.get("has_attachments", 0),
                })

        log.info("search.hybrid", query=query[:50], results=len(results))
        return results
