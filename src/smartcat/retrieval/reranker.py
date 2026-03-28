"""Cross-encoder reranker for search results."""

from __future__ import annotations

from typing import Optional

import structlog

from smartcat.config import RERANKER_MODEL, RERANKER_TOP_K, RERANKER_DEVICE

log = structlog.get_logger()


class Reranker:
    """Cross-encoder reranker using sentence-transformers CrossEncoder."""

    def __init__(
        self,
        model_name: str = RERANKER_MODEL,
        device: str = RERANKER_DEVICE,
        top_k: int = RERANKER_TOP_K,
    ):
        self.model_name = model_name
        self.device = device
        self.top_k = top_k
        self._model = None

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            log.info("reranker.loading", model=self.model_name, device=self.device)
            self._model = CrossEncoder(self.model_name, device=self.device)
            log.info("reranker.loaded")

    def rerank(
        self,
        query: str,
        results: list[dict],
        text_key: str = "body_text",
        top_k: Optional[int] = None,
    ) -> list[dict]:
        """Rerank search results using cross-encoder.

        Args:
            query: Original search query.
            results: List of result dicts (must contain text_key field).
            text_key: Key in result dict for the text to score against query.
            top_k: Number of top results to return (default: self.top_k).

        Returns:
            Reranked results with added 'rerank_score' field.
        """
        if not results:
            return []

        self._load_model()
        k = top_k or self.top_k

        # Build query-document pairs
        pairs = []
        for r in results:
            text = r.get(text_key, "")
            # Use subject + body preview for better relevance
            subject = r.get("subject", "")
            preview = text[:500] if text else ""
            doc_text = f"{subject}\n{preview}" if subject else preview
            pairs.append((query, doc_text))

        # Score all pairs
        scores = self._model.predict(pairs)

        # Add scores and sort
        for r, score in zip(results, scores):
            r["rerank_score"] = float(score)

        reranked = sorted(results, key=lambda x: x["rerank_score"], reverse=True)
        return reranked[:k]

    def unload(self):
        """Unload model from memory."""
        if self._model is not None:
            del self._model
            self._model = None
            log.info("reranker.unloaded")
