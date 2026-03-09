"""
FAISS → Qdrant Migration — One-shot migration of existing FAISS indices.

Reads all vectors + metadata from the on-disk FAISS stores, re-embeds
texts if vectors cannot be reconstructed, and batch-inserts them into
the configured Qdrant collection.

Usage (CLI)::

    python -m app.vector_memory.migration \
        --memory-types episodic semantic profile \
        --qdrant-url http://localhost:6333

Or as a Celery task via ``workers.maintenance_worker``.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

import faiss
import numpy as np

from app.config.settings import settings
from app.shared.utils import get_logger
from app.vector_memory.base import VectorRecord

_LOG = get_logger(__name__)


async def migrate_memory_type(
    memory_type: str,
    target_store,
    *,
    embedding_service=None,
) -> Dict[str, Any]:
    """Migrate a single FAISS memory type into *target_store*.

    Args:
        memory_type: One of "episodic", "semantic", "profile".
        target_store: A VectorStore instance (e.g. QdrantVectorStore).
        embedding_service: If vectors cannot be reconstructed from the
            FAISS index, re-encode texts with this service.

    Returns:
        Summary dict with migration statistics.
    """
    index_dir = settings.VECTOR_INDEX_DIR / "memory" / memory_type
    faiss_path = index_dir / "memory.index"
    meta_path = index_dir / "memory_meta.json"

    summary: Dict[str, Any] = {
        "memory_type": memory_type,
        "source_count": 0,
        "migrated": 0,
        "skipped": 0,
        "errors": [],
    }

    if not faiss_path.exists() or not meta_path.exists():
        _LOG.info("No FAISS data for '%s' — skipping", memory_type)
        summary["skipped_reason"] = "no_data"
        return summary

    # Load FAISS index and metadata
    try:
        index = faiss.read_index(str(faiss_path))
        with open(meta_path, "r", encoding="utf-8") as f:
            memories: List[Dict[str, Any]] = json.load(f)
    except Exception as e:
        _LOG.error("Failed to load FAISS data for '%s': %s", memory_type, e)
        summary["errors"].append(str(e))
        return summary

    summary["source_count"] = len(memories)
    n_vectors = index.ntotal

    if n_vectors == 0 or not memories:
        _LOG.info("Empty FAISS store for '%s'", memory_type)
        return summary

    # Reconstruct vectors from the index
    dim = index.d
    vectors: Optional[np.ndarray] = None
    try:
        vectors = np.zeros((n_vectors, dim), dtype="float32")
        for i in range(n_vectors):
            vectors[i] = index.reconstruct(i)
    except Exception:
        _LOG.warning(
            "Cannot reconstruct vectors for '%s' — will re-embed", memory_type
        )
        vectors = None

    # Re-embed if reconstruction failed
    if vectors is None:
        if embedding_service is None:
            from app.vector_memory.embeddings import embedding_service as _es
            embedding_service = _es

        texts = [m.get("text", "") for m in memories]
        vectors = embedding_service.encode(texts)

    # Build VectorRecord list
    records: List[VectorRecord] = []
    for i, mem in enumerate(memories):
        if i >= vectors.shape[0]:
            summary["skipped"] += 1
            continue
        record = VectorRecord(
            id=str(uuid.uuid4()),
            embedding=vectors[i],
            text=mem.get("text", ""),
            metadata={
                "memory_type": memory_type,
                "timestamp": mem.get("timestamp", ""),
                **mem.get("metadata", {}),
            },
        )
        records.append(record)

    # Batch insert into target store
    try:
        count = await target_store.batch_insert(records)
        summary["migrated"] = count
        _LOG.info(
            "Migrated %d/%d vectors for '%s'",
            count,
            len(memories),
            memory_type,
        )
    except Exception as e:
        _LOG.error("Batch insert failed for '%s': %s", memory_type, e)
        summary["errors"].append(str(e))

    return summary


async def run_migration(
    memory_types: Optional[List[str]] = None,
    qdrant_url: str = "http://localhost:6333",
    collection_name: str = "memories",
) -> Dict[str, Any]:
    """Run full migration from FAISS to Qdrant.

    Args:
        memory_types: List of memory types to migrate.
        qdrant_url: Qdrant server URL.
        collection_name: Target Qdrant collection.

    Returns:
        Aggregate summary dict.
    """
    from app.vector_memory.qdrant_store import QdrantVectorStore

    types = memory_types or ["episodic", "semantic", "profile"]

    store = QdrantVectorStore(
        collection_name=collection_name,
        url=qdrant_url,
        embedding_dim=settings.VECTOR_MEMORY_DIM,
    )
    await store.initialize()

    results: Dict[str, Any] = {}
    for mt in types:
        results[mt] = await migrate_memory_type(mt, store)

    await store.close()

    total_migrated = sum(r.get("migrated", 0) for r in results.values())
    _LOG.info("Migration complete: %d total vectors migrated", total_migrated)
    return {"status": "success", "total_migrated": total_migrated, "details": results}


# ── CLI entry point ───────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate FAISS → Qdrant")
    parser.add_argument(
        "--memory-types",
        nargs="+",
        default=["episodic", "semantic", "profile"],
    )
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--collection", default="memories")
    args = parser.parse_args()

    result = asyncio.run(
        run_migration(args.memory_types, args.qdrant_url, args.collection)
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
