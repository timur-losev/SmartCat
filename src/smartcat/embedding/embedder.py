"""Embedding model wrapper using sentence-transformers."""

from __future__ import annotations

import numpy as np
import structlog
from typing import Optional

from smartcat.config import EMBEDDING_MODEL, EMBEDDING_BATCH_SIZE, EMBEDDING_DIM

log = structlog.get_logger()


class Embedder:
    """Wrapper around sentence-transformers for batch and single-query embedding."""

    def __init__(
        self,
        model_name: str = EMBEDDING_MODEL,
        device: Optional[str] = None,
        batch_size: int = EMBEDDING_BATCH_SIZE,
    ):
        self.model_name = model_name
        self.batch_size = batch_size
        self._model = None
        self._device = device

    def _load_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            log.info("embedder.loading", model=self.model_name, device=self._device)
            kwargs = {"trust_remote_code": True}
            if self._device:
                kwargs["device"] = self._device
            self._model = SentenceTransformer(self.model_name, **kwargs)
            log.info(
                "embedder.loaded",
                dim=self._model.get_sentence_embedding_dimension(),
                device=str(self._model.device),
            )

    @property
    def dimension(self) -> int:
        self._load_model()
        return self._model.get_sentence_embedding_dimension()

    def embed_texts(
        self,
        texts: list[str],
        show_progress: bool = True,
        normalize: bool = True,
    ) -> np.ndarray:
        """Embed a batch of texts.

        Args:
            texts: List of strings to embed.
            show_progress: Show tqdm progress bar.
            normalize: L2-normalize embeddings (recommended for cosine similarity).

        Returns:
            numpy array of shape (len(texts), embedding_dim).
        """
        self._load_model()
        log.debug("embedder.encoding", count=len(texts), batch_size=self.batch_size)

        # nomic-embed-text requires "search_document: " prefix for documents
        # and "search_query: " for queries
        prefixed = self._add_prefix(texts, is_query=False)

        embeddings = self._model.encode(
            prefixed,
            batch_size=self.batch_size,
            show_progress_bar=show_progress,
            normalize_embeddings=normalize,
            convert_to_numpy=True,
        )
        del prefixed
        return embeddings

    def embed_query(self, query: str, normalize: bool = True) -> np.ndarray:
        """Embed a single query string.

        Args:
            query: Search query text.
            normalize: L2-normalize embedding.

        Returns:
            1D numpy array of shape (embedding_dim,).
        """
        self._load_model()
        prefixed = self._add_prefix([query], is_query=True)
        embedding = self._model.encode(
            prefixed,
            normalize_embeddings=normalize,
        )
        return embedding[0]

    def _add_prefix(self, texts: list[str], is_query: bool) -> list[str]:
        """Add model-specific prefixes for asymmetric search.

        nomic-embed-text uses: "search_document: " and "search_query: "
        e5 uses: "passage: " and "query: "
        bge uses no prefix for documents, "Represent this sentence: " for queries.
        """
        model_lower = self.model_name.lower()

        if "nomic" in model_lower:
            prefix = "search_query: " if is_query else "search_document: "
            return [prefix + t for t in texts]
        elif "e5" in model_lower:
            prefix = "query: " if is_query else "passage: "
            return [prefix + t for t in texts]
        else:
            # bge and most others don't need prefix for documents
            return texts

    def unload(self):
        """Unload model from memory/VRAM."""
        if self._model is not None:
            del self._model
            self._model = None
            # Try to free VRAM
            try:
                import torch
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
            log.info("embedder.unloaded")
