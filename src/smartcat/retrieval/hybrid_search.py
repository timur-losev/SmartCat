"""Hybrid search combining vector, keyword, and QA channels with RRF fusion."""

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
    """Three-channel hybrid search: vector + keyword + QA."""

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

        Three channels feed into RRF fusion:
          1. Vector search (document chunks) — semantic similarity
          2. Keyword search (FTS5 BM25) — exact term matching
          3. QA pairs (question vectors) — pre-computed answers

        All three are equal participants in RRF. The reranker
        (downstream) decides the final order.

        Args:
            query: Natural language search query.
            top_n: Number of results after fusion (before reranking).
            vector_filters: Optional Qdrant payload filters for vector channel.

        Returns:
            List of result dicts with email metadata and scores.
        """
        ranked_lists = []
        qa_payload_map: dict[int, dict] = {}  # email_id -> QA payload

        # Channel 1: Vector search (document chunks only)
        try:
            query_vec = self.embedder.embed_query(query)
            vector_results = self.qdrant.search(
                query_vector=query_vec,
                limit=self.top_k,
                filters=vector_filters,
            )
            # Separate document chunks from QA pairs
            doc_ranked = []
            qa_ranked = []
            for r in vector_results:
                payload = r.get("payload", {})
                if payload.get("chunk_type") == "qa":
                    email_id = payload.get("email_id", 0)
                    qa_ranked.append((email_id, r["score"]))
                    # Store QA payload for enrichment later
                    if email_id not in qa_payload_map:
                        qa_payload_map[email_id] = {
                            "question": payload.get("question", ""),
                            "answer": payload.get("answer", ""),
                        }
                else:
                    doc_ranked.append(
                        (payload.get("email_id", r["id"]), r["score"])
                    )
            ranked_lists.append(doc_ranked)

            # Channel 3: QA pairs as separate RRF channel
            if qa_ranked:
                ranked_lists.append(qa_ranked)

            log.debug("search.vector", docs=len(doc_ranked), qa=len(qa_ranked))
        except Exception as e:
            log.warning("search.vector.failed", error=str(e))

        # Channel 2: Keyword search (BM25 via FTS5)
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

        # RRF fusion across all channels (doc + FTS + QA)
        fused = reciprocal_rank_fusion(ranked_lists, k=self.rrf_k)

        # Take top_n and enrich with email metadata
        results = []
        for email_id, rrf_score in fused[:top_n]:
            email = self.sqlite.get_email(email_id)
            if email:
                result = {
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
                }
                # Attach QA context if this email had a QA match
                if email_id in qa_payload_map:
                    qa = qa_payload_map[email_id]
                    result["_qa_question"] = qa["question"]
                    result["_qa_answer"] = qa["answer"]
                    result["_source"] = "qa+doc"
                results.append(result)

        log.info(
            "search.hybrid",
            query=query[:50],
            channels=len(ranked_lists),
            results=len(results),
            qa_matches=len(qa_payload_map),
        )
        return results
