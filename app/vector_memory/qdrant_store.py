"""
Qdrant Backend — Production vector store using Qdrant.

Features:
    • Async client with connection pooling (grpc preferred).
    • Automatic collection creation with cosine distance.
    • Retries with exponential backoff on transient errors.
    • Batch upsert with configurable chunk size.
    • Prometheus metrics for latency, throughput, and index size.

Requires:
    pip install qdrant-client[fastembed]
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import numpy as np
from app.shared.utils import get_logger
from app.vector_memory.base import SearchResult, VectorRecord, VectorStore

_LOG = get_logger(__name__)

# ── Retry defaults ────────────────────────────────────────────────
_MAX_RETRIES = 4
_BASE_DELAY = 0.25  # seconds
_MAX_DELAY = 8.0


async def _retry_async(coro_factory, *, max_retries=_MAX_RETRIES):
    """Retry an async callable with exponential backoff + jitter."""
    delay = _BASE_DELAY
    for attempt in range(1, max_retries + 1):
        try:
            return await coro_factory()
        except Exception as exc:
            if attempt == max_retries:
                raise
            _LOG.warning(
                "Qdrant retry %d/%d after %s: %s",
                attempt,
                max_retries,
                type(exc).__name__,
                exc,
            )
            jitter = delay * 0.5 * (0.5 + asyncio.get_event_loop().time() % 1)
            await asyncio.sleep(min(delay + jitter, _MAX_DELAY))
            delay *= 2


class QdrantVectorStore(VectorStore):
    """Qdrant-backed vector store.

    Args:
        collection_name: Qdrant collection name.
        url: Qdrant server URL (e.g. ``http://localhost:6333``).
        api_key: Optional API key for Qdrant Cloud.
        embedding_dim: Dimensionality of vectors.
        prefer_grpc: Use gRPC transport for better throughput.
        batch_size: Max vectors per upsert call.
    """

    def __init__(
        self,
        collection_name: str = "memories",
        url: str = "http://localhost:6333",
        api_key: str | None = None,
        embedding_dim: int = 384,
        prefer_grpc: bool = True,
        batch_size: int = 100,
    ) -> None:
        self.collection_name = collection_name
        self.url = url
        self.api_key = api_key
        self.embedding_dim = embedding_dim
        self.prefer_grpc = prefer_grpc
        self.batch_size = batch_size
        self._client = None

    # ── Lifecycle ─────────────────────────────────────────────────

    async def _get_client(self):
        """Lazy-initialize the async Qdrant client."""
        if self._client is None:
            from qdrant_client import AsyncQdrantClient

            self._client = AsyncQdrantClient(
                url=self.url,
                api_key=self.api_key,
                prefer_grpc=self.prefer_grpc,
                timeout=30,
            )
        return self._client

    async def initialize(self) -> None:
        """Create the collection if it doesn't exist."""
        from qdrant_client.models import Distance, VectorParams

        client = await self._get_client()

        collections = await _retry_async(client.get_collections)
        existing = {c.name for c in collections.collections}

        if self.collection_name not in existing:
            await _retry_async(
                lambda: client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.embedding_dim,
                        distance=Distance.COSINE,
                    ),
                )
            )
            _LOG.info(
                "Created Qdrant collection '%s' (dim=%d)",
                self.collection_name,
                self.embedding_dim,
            )
        else:
            _LOG.info("Qdrant collection '%s' already exists", self.collection_name)

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    # ── Core operations ───────────────────────────────────────────

    async def add_embedding(
        self,
        id: str,
        embedding: np.ndarray,
        text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        from qdrant_client.models import PointStruct

        client = await self._get_client()
        payload = {"text": text, **(metadata or {})}
        point = PointStruct(
            id=id,
            vector=embedding.tolist(),
            payload=payload,
        )
        await _retry_async(
            lambda: client.upsert(
                collection_name=self.collection_name,
                points=[point],
            )
        )
        _emit_insert_metric()

    async def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        from qdrant_client.models import FieldCondition, Filter, MatchValue

        client = await self._get_client()

        qdrant_filter = None
        if filters:
            conditions = [FieldCondition(key=k, match=MatchValue(value=v)) for k, v in filters.items()]
            qdrant_filter = Filter(must=conditions)

        start = time.perf_counter()
        hits = await _retry_async(
            lambda: client.search(
                collection_name=self.collection_name,
                query_vector=query_embedding.tolist(),
                limit=top_k,
                query_filter=qdrant_filter,
            )
        )
        _emit_search_metric(time.perf_counter() - start)

        results: list[SearchResult] = []
        for hit in hits:
            payload = hit.payload or {}
            results.append(
                SearchResult(
                    id=str(hit.id),
                    text=payload.get("text", ""),
                    score=hit.score,
                    metadata={k: v for k, v in payload.items() if k != "text"},
                )
            )
        return results

    async def delete(self, ids: list[str]) -> int:
        from qdrant_client.models import PointIdsList

        client = await self._get_client()
        await _retry_async(
            lambda: client.delete(
                collection_name=self.collection_name,
                points_selector=PointIdsList(points=ids),
            )
        )
        return len(ids)

    async def batch_insert(self, records: list[VectorRecord]) -> int:
        from qdrant_client.models import PointStruct

        client = await self._get_client()
        inserted = 0

        for i in range(0, len(records), self.batch_size):
            batch = records[i : i + self.batch_size]
            points = [
                PointStruct(
                    id=rec.id,
                    vector=rec.embedding.tolist(),
                    payload={"text": rec.text, **rec.metadata},
                )
                for rec in batch
            ]
            await _retry_async(
                lambda pts=points: client.upsert(
                    collection_name=self.collection_name,
                    points=pts,
                )
            )
            inserted += len(batch)

        _emit_batch_metric(inserted)
        return inserted

    async def count(self) -> int:
        client = await self._get_client()
        info = await _retry_async(lambda: client.get_collection(self.collection_name))
        return info.points_count or 0


# -- Prometheus helpers (no-op-safe if prometheus_client absent) ---


def _emit_search_metric(duration: float) -> None:
    try:
        from app.shared.monitoring import VECTOR_QUERY_LATENCY

        VECTOR_QUERY_LATENCY.observe(duration)
    except Exception:  # noqa: S110
        pass


def _emit_insert_metric() -> None:
    try:
        from app.shared.monitoring import VECTOR_INDEX_SIZE

        VECTOR_INDEX_SIZE.inc()
    except Exception:  # noqa: S110
        pass


def _emit_batch_metric(count: int) -> None:
    try:
        from app.shared.monitoring import VECTOR_INDEX_SIZE

        VECTOR_INDEX_SIZE.inc(count)
    except Exception:  # noqa: S110
        pass
