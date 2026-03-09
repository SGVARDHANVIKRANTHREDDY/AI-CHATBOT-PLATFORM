"""
FAISS Backend — Local / development vector store.

Wraps the existing FAISS IndexFlatIP behind the VectorStore interface
so that existing code continues to work without Qdrant running.

This backend stores data on disk (JSON metadata + FAISS binary index).
It is NOT recommended for production — use QdrantVectorStore instead.
"""

from __future__ import annotations

import json
import time
from typing import Any

import faiss
import numpy as np
from app.config.settings import settings
from app.shared.utils import get_logger
from app.vector_memory.base import SearchResult, VectorRecord, VectorStore

_LOG = get_logger(__name__)


class FAISSVectorStore(VectorStore):
    """FAISS-backed vector store implementing the VectorStore interface.

    Args:
        collection_name: Sub-directory under the vector index dir.
        embedding_dim: Dimensionality of vectors.
    """

    def __init__(
        self,
        collection_name: str = "memories",
        embedding_dim: int = 384,
    ) -> None:
        self.collection_name = collection_name
        self.embedding_dim = embedding_dim

        self.index_dir = settings.VECTOR_INDEX_DIR / "memory" / collection_name
        self.index_dir.mkdir(parents=True, exist_ok=True)
        self.faiss_path = self.index_dir / "memory.index"
        self.meta_path = self.index_dir / "memory_meta.json"

        self._index: faiss.Index | None = None
        self._records: list[dict[str, Any]] = []

    # ── Lifecycle ─────────────────────────────────────────────────

    async def initialize(self) -> None:
        self._load()

    async def close(self) -> None:
        self._save()

    def _load(self) -> None:
        if self.faiss_path.exists() and self.meta_path.exists():
            try:
                self._index = faiss.read_index(str(self.faiss_path))
                with open(self.meta_path, encoding="utf-8") as f:
                    self._records = json.load(f)
                _LOG.info(
                    "FAISS store '%s' loaded: %d vectors",
                    self.collection_name,
                    len(self._records),
                )
            except Exception as e:
                _LOG.error("Failed to load FAISS store '%s': %s", self.collection_name, e)

    def _save(self) -> None:
        if self._index is None:
            return
        try:
            faiss.write_index(self._index, str(self.faiss_path))
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(self._records, f, indent=2)
        except Exception as e:
            _LOG.error("Failed to save FAISS store '%s': %s", self.collection_name, e)

    # ── Core operations ───────────────────────────────────────────

    async def add_embedding(
        self,
        id: str,
        embedding: np.ndarray,
        text: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        emb = np.asarray(embedding, dtype="float32").reshape(1, -1)
        if self._index is None:
            self._index = faiss.IndexFlatIP(emb.shape[1])
        self._index.add(emb)
        self._records.append(
            {
                "id": id,
                "text": text,
                "metadata": metadata or {},
            }
        )
        self._save()

    async def search(
        self,
        query_embedding: np.ndarray,
        top_k: int = 5,
        filters: dict[str, Any] | None = None,
    ) -> list[SearchResult]:
        if self._index is None or not self._records:
            return []

        start = time.perf_counter()
        q = np.asarray(query_embedding, dtype="float32").reshape(1, -1)
        k = min(len(self._records), top_k)
        scores, idxs = self._index.search(q, k)

        results: list[SearchResult] = []
        for score, idx in zip(scores[0], idxs[0], strict=False):
            if idx == -1 or idx >= len(self._records):
                continue
            rec = self._records[idx]

            # Apply metadata filters if given
            if filters:
                meta = rec.get("metadata", {})
                if not all(meta.get(fk) == fv for fk, fv in filters.items()):
                    continue

            results.append(
                SearchResult(
                    id=rec.get("id", f"rec-{idx}"),
                    text=rec.get("text", ""),
                    score=float(score),
                    metadata=rec.get("metadata", {}),
                )
            )

        _emit_search_metric(time.perf_counter() - start)
        return results

    async def delete(self, ids: list[str]) -> int:
        """Delete by rebuilding the index without the given IDs.

        FAISS IndexFlatIP does not support in-place deletion, so we
        reconstruct the index from remaining vectors.
        """
        if self._index is None or not self._records:
            return 0

        id_set = set(ids)
        keep_indices = [i for i, r in enumerate(self._records) if r["id"] not in id_set]
        removed = len(self._records) - len(keep_indices)

        if removed == 0:
            return 0

        # Reconstruct vectors for kept records
        dim = self._index.d
        new_index = faiss.IndexFlatIP(dim)

        if keep_indices:
            kept_vectors = np.zeros((len(keep_indices), dim), dtype="float32")
            for new_i, old_i in enumerate(keep_indices):
                kept_vectors[new_i] = self._index.reconstruct(old_i)
            new_index.add(kept_vectors)

        self._index = new_index
        self._records = [self._records[i] for i in keep_indices]
        self._save()
        return removed

    async def batch_insert(self, records: list[VectorRecord]) -> int:
        if not records:
            return 0

        embeddings = np.stack([np.asarray(r.embedding, dtype="float32") for r in records])
        if self._index is None:
            self._index = faiss.IndexFlatIP(embeddings.shape[1])
        self._index.add(embeddings)

        for rec in records:
            self._records.append(
                {
                    "id": rec.id,
                    "text": rec.text,
                    "metadata": rec.metadata,
                }
            )
        self._save()
        return len(records)

    async def count(self) -> int:
        if self._index is None:
            return 0
        return self._index.ntotal


def _emit_search_metric(duration: float) -> None:
    try:
        from app.shared.monitoring import VECTOR_QUERY_LATENCY

        VECTOR_QUERY_LATENCY.observe(duration)
    except Exception:  # noqa: S110
        pass
