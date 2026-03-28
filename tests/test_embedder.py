"""Tests for embedding module (unit tests, no model download required)."""

import pytest
from unittest.mock import MagicMock, patch
from smartcat.embedding.embedder import Embedder


class TestEmbedderPrefixes:
    def test_nomic_document_prefix(self):
        emb = Embedder(model_name="nomic-ai/nomic-embed-text-v1.5")
        result = emb._add_prefix(["hello"], is_query=False)
        assert result == ["search_document: hello"]

    def test_nomic_query_prefix(self):
        emb = Embedder(model_name="nomic-ai/nomic-embed-text-v1.5")
        result = emb._add_prefix(["hello"], is_query=True)
        assert result == ["search_query: hello"]

    def test_e5_document_prefix(self):
        emb = Embedder(model_name="intfloat/e5-large-v2")
        result = emb._add_prefix(["hello"], is_query=False)
        assert result == ["passage: hello"]

    def test_e5_query_prefix(self):
        emb = Embedder(model_name="intfloat/e5-large-v2")
        result = emb._add_prefix(["hello"], is_query=True)
        assert result == ["query: hello"]

    def test_bge_no_prefix(self):
        emb = Embedder(model_name="BAAI/bge-large-en-v1.5")
        result = emb._add_prefix(["hello"], is_query=False)
        assert result == ["hello"]

    def test_multiple_texts(self):
        emb = Embedder(model_name="nomic-ai/nomic-embed-text-v1.5")
        result = emb._add_prefix(["a", "b", "c"], is_query=False)
        assert len(result) == 3
        assert all(t.startswith("search_document: ") for t in result)


class TestEmbedderInit:
    def test_default_config(self):
        emb = Embedder()
        assert emb.model_name == "nomic-ai/nomic-embed-text-v1.5"
        assert emb.batch_size == 256
        assert emb._model is None

    def test_custom_config(self):
        emb = Embedder(model_name="custom/model", device="cpu", batch_size=32)
        assert emb.model_name == "custom/model"
        assert emb._device == "cpu"
        assert emb.batch_size == 32

    def test_unload_when_not_loaded(self):
        emb = Embedder()
        emb.unload()  # Should not raise
