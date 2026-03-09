"""
VectorStore — Abstract interface for vector database backends.

Provides a backend-agnostic API so the retrieval layer can swap
between FAISS (development), Qdrant (production), or any future
backend without changing calling code.

All operations are async.  Implementations must be safe for
concurrent use from multiple asyncio tasks.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import numpy as np


@dataclass
class VectorRecord:
    """A single vector with its associated metadata."""
    id: str
    embedding: np.ndarray
    text: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    score: float = 0.0


@dataclass
class SearchResult:
    """Result of a vector similarity search."""
    id: str
    text: str
    score: float
    metadata: Dict[str, Any] = field(default_factory=dict)


class VectorStore(abc.ABC):
    """Abstract vector store interface.

    Every backend must implement these four core operations plus
    ``batch_insert`` for bulk loading.
    """

    @abc.abstractmethod
    async def add_embedding(
        self,
        id: str,
        embedding: np.ndarray,
        text: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Insert a single vector into the store."""

    @abc.abstractmethod
    async def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[SearchResult]:
        """Return the *top_k* most similar vectors."""

    @abc.abstractmethod
    async def delete(self, ids: List[str]) -> int:
        """Delete vectors by ID.  Returns the count actually removed."""

    @abc.abstractmethod
    async def batch_insert(
        self,
        records: List[VectorRecord],
    ) -> int:
        """Bulk-insert multiple vectors.  Returns the count inserted."""

    # ── Optional lifecycle hooks ──────────────────────────────────

    async def initialize(self) -> None:
        """Called once at startup to create collections / tables."""

    async def close(self) -> None:
        """Release connections, flush buffers."""

    async def count(self) -> int:
        """Return the total number of vectors in the store."""
        return 0
