"""Dependency injection — singleton agent and tools initialization."""

from __future__ import annotations

from pathlib import Path

from smartcat.agent.streaming import AsyncReactAgent
from smartcat.agent.tools import AgentTools
from smartcat.config import SQLITE_DB_PATH
from smartcat.embedding.embedder import Embedder
from smartcat.retrieval.hybrid_search import HybridSearcher
from smartcat.retrieval.reranker import Reranker
from smartcat.storage.qdrant_store import QdrantStore
from smartcat.storage.sqlite_store import EmailStore

_agent: AsyncReactAgent | None = None
_store: EmailStore | None = None


async def get_agent() -> AsyncReactAgent:
    global _agent, _store
    if _agent is None:
        _store = EmailStore(SQLITE_DB_PATH)
        _store.connect()
        embedder = Embedder(device="cpu")
        qdrant = QdrantStore()
        searcher = HybridSearcher(embedder, qdrant, _store)
        reranker = Reranker(device="cpu")
        tools = AgentTools(searcher, reranker, _store)
        _agent = AsyncReactAgent(tools)
    return _agent


async def shutdown():
    global _agent, _store
    if _store:
        _store.close()
        _store = None
    _agent = None
