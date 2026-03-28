"""Qdrant vector store for email chunk embeddings."""

from __future__ import annotations

from typing import Any, Optional

import numpy as np
import structlog

from smartcat.config import (
    QDRANT_HOST,
    QDRANT_PORT,
    QDRANT_COLLECTION,
    EMBEDDING_DIM,
    SEARCH_TOP_K_PER_CHANNEL,
)

log = structlog.get_logger()


class QdrantStore:
    """Qdrant vector database operations for email chunks."""

    def __init__(
        self,
        host: str = QDRANT_HOST,
        port: int = QDRANT_PORT,
        collection: str = QDRANT_COLLECTION,
        embedding_dim: int = EMBEDDING_DIM,
    ):
        self.host = host
        self.port = port
        self.collection = collection
        self.embedding_dim = embedding_dim
        self._client = None

    def connect(self):
        if self._client is None:
            from qdrant_client import QdrantClient

            self._client = QdrantClient(host=self.host, port=self.port, timeout=120)
            log.info("qdrant.connected", host=self.host, port=self.port)
        return self._client

    def create_collection(self, recreate: bool = False):
        """Create the email chunks collection with payload indexes."""
        from qdrant_client.models import (
            Distance,
            VectorParams,
            PayloadSchemaType,
        )

        client = self.connect()

        if recreate:
            client.delete_collection(self.collection)
            log.info("qdrant.collection_deleted", name=self.collection)

        # Check if collection exists
        collections = client.get_collections().collections
        if any(c.name == self.collection for c in collections):
            log.info("qdrant.collection_exists", name=self.collection)
            return

        client.create_collection(
            collection_name=self.collection,
            vectors_config=VectorParams(
                size=self.embedding_dim,
                distance=Distance.COSINE,
            ),
        )
        log.info("qdrant.collection_created", name=self.collection, dim=self.embedding_dim)

        # Create payload indexes for filtered search
        for field, schema_type in [
            ("date_sent", PayloadSchemaType.KEYWORD),
            ("from_address", PayloadSchemaType.KEYWORD),
            ("thread_id", PayloadSchemaType.KEYWORD),
            ("chunk_type", PayloadSchemaType.KEYWORD),
            ("has_monetary", PayloadSchemaType.BOOL),
            ("has_attachment", PayloadSchemaType.BOOL),
            ("message_id", PayloadSchemaType.KEYWORD),
        ]:
            client.create_payload_index(
                collection_name=self.collection,
                field_name=field,
                field_schema=schema_type,
            )
        log.info("qdrant.indexes_created")

    def upsert_batch(
        self,
        ids: list[str],
        vectors: np.ndarray,
        payloads: list[dict[str, Any]],
        batch_size: int = 500,
    ):
        """Batch upsert vectors with payloads."""
        from qdrant_client.models import PointStruct

        client = self.connect()
        total = len(ids)

        for i in range(0, total, batch_size):
            end = min(i + batch_size, total)
            points = [
                PointStruct(
                    id=ids[j] if isinstance(ids[j], int) else hash(ids[j]) % (2**63),
                    vector=vectors[j].tolist(),
                    payload=payloads[j],
                )
                for j in range(i, end)
            ]
            client.upsert(collection_name=self.collection, points=points)

        log.info("qdrant.upserted", count=total)

    def search(
        self,
        query_vector: np.ndarray,
        limit: int = SEARCH_TOP_K_PER_CHANNEL,
        filters: Optional[dict] = None,
    ) -> list[dict]:
        """Vector similarity search with optional payload filters.

        Args:
            query_vector: Query embedding vector.
            limit: Max results to return.
            filters: Optional dict of payload field filters.

        Returns:
            List of dicts with 'id', 'score', and 'payload' keys.
        """
        from qdrant_client.models import Filter, FieldCondition, MatchValue

        client = self.connect()

        query_filter = None
        if filters:
            conditions = []
            for field, value in filters.items():
                conditions.append(
                    FieldCondition(key=field, match=MatchValue(value=value))
                )
            query_filter = Filter(must=conditions)

        results = client.query_points(
            collection_name=self.collection,
            query=query_vector.tolist(),
            limit=limit,
            query_filter=query_filter,
            with_payload=True,
        )

        return [
            {
                "id": str(r.id),
                "score": r.score,
                "payload": r.payload,
            }
            for r in results.points
        ]

    def get_collection_info(self) -> dict:
        """Get collection stats."""
        client = self.connect()
        info = client.get_collection(self.collection)
        return {
            "vectors_count": info.vectors_count,
            "points_count": info.points_count,
            "status": info.status.value,
        }
