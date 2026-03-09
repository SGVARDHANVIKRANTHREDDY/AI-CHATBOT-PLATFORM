"""
VectorMemory — Facade that delegates to a pluggable VectorStore backend.

Maintains the same public API (``add`` / ``search``) so that
MemoryRetriever, maintenance, and all other callers continue to work
without modification.

The backend is selected by the ``VECTOR_BACKEND`` setting:
    "qdrant" → QdrantVectorStore (production)
    "faiss"  → FAISSVectorStore  (development / offline)
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from app.config.settings import settings
from app.shared.utils import get_logger
from app.vector_memory.base import SearchResult, VectorStore
from app.vector_memory.embeddings import embedding_service

_LOG = get_logger(__name__)


def _create_backend(memory_type: str) -> VectorStore:
    """Factory: build the configured VectorStore backend."""
    backend = getattr(settings, "VECTOR_BACKEND", "faiss").lower()

    if backend == "qdrant":
        from app.vector_memory.qdrant_store import QdrantVectorStore

        return QdrantVectorStore(
            collection_name=memory_type,
            url=getattr(settings, "QDRANT_URL", "http://localhost:6333"),
            api_key=getattr(settings, "QDRANT_API_KEY", None),
            embedding_dim=settings.VECTOR_MEMORY_DIM,
        )
    else:
        from app.vector_memory.faiss_store import FAISSVectorStore

        return FAISSVectorStore(
            collection_name=memory_type,
            embedding_dim=settings.VECTOR_MEMORY_DIM,
        )


class VectorMemory:
    """Manages long-term memories (Episodic, Semantic, User Profile).

    Delegates all storage to the configured VectorStore backend while
    keeping the same async ``add()`` / ``search()`` API.
    """

    def __init__(self, memory_type: str = "episodic"):
        self.memory_type = memory_type
        self._store: VectorStore = _create_backend(memory_type)
        self._initialized = False

    async def _ensure_init(self) -> None:
        if not self._initialized:
            await self._store.initialize()
            self._initialized = True

    async def add(self, text: str, metadata: dict[str, Any] | None = None):
        """Adds a new memory to the store."""
        await self._ensure_init()

        emb = embedding_service.encode_single(text)
        record_id = uuid.uuid4().hex

        await self._store.add_embedding(
            id=record_id,
            embedding=emb,
            text=text,
            metadata={
                "timestamp": datetime.now(UTC).isoformat(),
                "memory_type": self.memory_type,
                **(metadata or {}),
            },
        )
        _LOG.info("Added %s memory: %s…", self.memory_type, text[:50])

    async def search(self, query: str, top_k: int = settings.VECTOR_MEMORY_TOP_K) -> list[dict[str, Any]]:
        """Searches for similar memories."""
        await self._ensure_init()

        q_emb = embedding_service.encode_single(query)
        results: list[SearchResult] = await self._store.search(q_emb, top_k=top_k)

        hits: list[dict[str, Any]] = []
        for r in results:
            hits.append(
                {
                    "text": r.text,
                    "score": r.score,
                    "metadata": r.metadata,
                    "timestamp": r.metadata.get("timestamp", ""),
                }
            )
        return hits
