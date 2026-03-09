"""
Vector Maintenance Manager — Lifecycle management for FAISS indices.

Provides scheduled maintenance tasks to keep vector stores healthy:
    • Deduplicate near-identical embeddings
    • Remove stale entries older than a cutoff
    • Rebuild / compact the FAISS index
    • Compress old vectors (float32 → float16 for storage)

Runs as a Celery beat job (daily default) to prevent unbounded
index growth and accuracy degradation.

Design rationale:
    FAISS IndexFlatIP has O(n) search cost.  Without maintenance the
    index grows without bound, slowing every retrieval.  Duplicate
    embeddings waste memory and skew similarity scores.  Stale entries
    from outdated conversations pollute results.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import faiss
import numpy as np

from app.config.settings import settings
from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class VectorMaintenanceManager:
    """Maintains health of a VectorMemory instance.

    Args:
        memory_type: The memory type to maintain (matches VectorMemory naming).
        dedup_threshold: Cosine similarity above which two vectors are duplicates.
        stale_days: Memories older than this are removed.
        compress_days: Memories older than this get precision-reduced.
    """

    def __init__(
        self,
        memory_type: str = "episodic",
        dedup_threshold: float = 0.98,
        stale_days: int = 90,
        compress_days: int = 30,
    ) -> None:
        self.memory_type = memory_type
        self.dedup_threshold = dedup_threshold
        self.stale_days = stale_days
        self.compress_days = compress_days

        self.index_dir = settings.VECTOR_INDEX_DIR / "memory" / memory_type
        self.faiss_path = self.index_dir / "memory.index"
        self.meta_path = self.index_dir / "memory_meta.json"

    # ── Public API ────────────────────────────────────────────────

    def run_full_maintenance(self) -> Dict[str, Any]:
        """Execute all maintenance tasks in sequence.

        Returns:
            Summary dict with counts of actions taken.
        """
        start = time.perf_counter()
        _LOG.info("Starting full maintenance for '%s'", self.memory_type)

        summary: Dict[str, Any] = {
            "memory_type": self.memory_type,
            "stale_removed": 0,
            "duplicates_removed": 0,
            "reindexed": False,
            "compressed": 0,
        }

        index, memories = self._load()
        if index is None or not memories:
            _LOG.info("No index or memories found for '%s' — skipping", self.memory_type)
            summary["skipped"] = True
            return summary

        original_count = len(memories)

        # 1. Remove stale
        memories, stale_count = self._remove_stale(memories)
        summary["stale_removed"] = stale_count

        # 2. Deduplicate
        if index is not None and len(memories) > 1:
            memories, dedup_count = self._deduplicate(index, memories)
            summary["duplicates_removed"] = dedup_count

        # 3. Reindex (rebuild from scratch with remaining memories)
        if stale_count > 0 or summary["duplicates_removed"] > 0:
            self._reindex(memories)
            summary["reindexed"] = True

        # 4. Compress old vectors (reduce precision for storage)
        compress_count = self._compress_old_vectors(memories)
        summary["compressed"] = compress_count

        elapsed = time.perf_counter() - start
        summary["elapsed_seconds"] = round(elapsed, 2)
        summary["before_count"] = original_count
        summary["after_count"] = len(memories)

        _LOG.info(
            "Maintenance complete for '%s': %s",
            self.memory_type,
            summary,
        )
        return summary

    def deduplicate(self) -> int:
        """Run deduplication only. Returns count of removed duplicates."""
        index, memories = self._load()
        if index is None or len(memories) <= 1:
            return 0
        memories, count = self._deduplicate(index, memories)
        if count > 0:
            self._reindex(memories)
        return count

    def remove_stale(self, max_age_days: Optional[int] = None) -> int:
        """Remove memories older than cutoff. Returns count removed."""
        index, memories = self._load()
        if not memories:
            return 0
        orig_days = self.stale_days
        if max_age_days is not None:
            self.stale_days = max_age_days
        memories, count = self._remove_stale(memories)
        self.stale_days = orig_days
        if count > 0:
            self._reindex(memories)
        return count

    def reindex(self) -> bool:
        """Rebuild the FAISS index from metadata."""
        _, memories = self._load()
        if not memories:
            return False
        self._reindex(memories)
        return True

    # ── Private implementation ────────────────────────────────────

    def _load(self):
        """Load FAISS index and metadata from disk."""
        index = None
        memories: List[Dict[str, Any]] = []

        if self.faiss_path.exists():
            try:
                index = faiss.read_index(str(self.faiss_path))
            except Exception as e:
                _LOG.error("Failed to load FAISS index: %s", e)

        if self.meta_path.exists():
            try:
                with open(self.meta_path, "r", encoding="utf-8") as f:
                    memories = json.load(f)
            except Exception as e:
                _LOG.error("Failed to load memory metadata: %s", e)

        return index, memories

    def _save_metadata(self, memories: List[Dict[str, Any]]) -> None:
        """Save metadata to disk."""
        try:
            with open(self.meta_path, "w", encoding="utf-8") as f:
                json.dump(memories, f, indent=2)
        except Exception as e:
            _LOG.error("Failed to save metadata: %s", e)

    def _remove_stale(
        self, memories: List[Dict[str, Any]]
    ) -> tuple:
        """Remove memories older than stale_days."""
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.stale_days)
        kept: List[Dict[str, Any]] = []
        removed = 0

        for mem in memories:
            try:
                ts = datetime.fromisoformat(mem.get("timestamp", ""))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts >= cutoff:
                    kept.append(mem)
                else:
                    removed += 1
            except (ValueError, TypeError):
                # Keep memories with unparseable timestamps
                kept.append(mem)

        if removed > 0:
            _LOG.info(
                "Removed %d stale memories (older than %d days)",
                removed,
                self.stale_days,
            )
        return kept, removed

    def _deduplicate(
        self, index: faiss.Index, memories: List[Dict[str, Any]]
    ) -> tuple:
        """Remove near-duplicate vectors using cosine similarity."""
        n = index.ntotal
        if n <= 1 or n != len(memories):
            return memories, 0

        # Reconstruct all vectors
        try:
            vectors = np.zeros((n, index.d), dtype="float32")
            for i in range(n):
                vectors[i] = index.reconstruct(i)
        except Exception as e:
            _LOG.warning("Cannot reconstruct vectors for dedup: %s", e)
            return memories, 0

        # Find duplicates via pairwise similarity
        duplicates: Set[int] = set()
        for i in range(n):
            if i in duplicates:
                continue
            for j in range(i + 1, n):
                if j in duplicates:
                    continue
                sim = float(np.dot(vectors[i], vectors[j]))
                if sim >= self.dedup_threshold:
                    duplicates.add(j)

        if not duplicates:
            return memories, 0

        kept = [m for idx, m in enumerate(memories) if idx not in duplicates]
        _LOG.info("Deduplicated: removed %d duplicates", len(duplicates))
        return kept, len(duplicates)

    def _reindex(self, memories: List[Dict[str, Any]]) -> None:
        """Rebuild the FAISS index from remaining memories."""
        from app.vector_memory.embeddings import embedding_service

        if not memories:
            _LOG.info("No memories to reindex — creating empty index")
            empty_index = faiss.IndexFlatIP(settings.VECTOR_MEMORY_DIM)
            faiss.write_index(empty_index, str(self.faiss_path))
            self._save_metadata([])
            return

        texts = [m["text"] for m in memories]
        embeddings = embedding_service.encode(texts)

        new_index = faiss.IndexFlatIP(int(embeddings.shape[1]))
        new_index.add(np.asarray(embeddings, dtype="float32"))

        faiss.write_index(new_index, str(self.faiss_path))
        self._save_metadata(memories)

        _LOG.info(
            "Reindexed '%s': %d vectors, dim=%d",
            self.memory_type,
            new_index.ntotal,
            new_index.d,
        )

    def _compress_old_vectors(self, memories: List[Dict[str, Any]]) -> int:
        """Flag old memories as compressed (precision reduction for storage).

        Note: FAISS IndexFlatIP always stores float32 internally.
        This method marks old entries so future reindex operations
        can use a quantized index type (e.g. IndexIVFPQ) for them.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.compress_days)
        compressed = 0

        for mem in memories:
            try:
                ts = datetime.fromisoformat(mem.get("timestamp", ""))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if ts < cutoff and not mem.get("metadata", {}).get("compressed"):
                    mem.setdefault("metadata", {})["compressed"] = True
                    compressed += 1
            except (ValueError, TypeError):
                pass

        if compressed > 0:
            self._save_metadata(memories)
            _LOG.info("Marked %d old memories as compressed", compressed)

        return compressed
