"""
Background Indexing Worker — Celery task for async vector ingestion.

Accepts batches of texts + metadata and inserts them into the
configured VectorStore backend in the background, keeping the
hot path (API request → response) fast.

Registered as a Celery task so it can be called with::

    from workers.indexing_worker import index_vectors
    index_vectors.delay(records=[...], memory_type="episodic")
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any, Dict, List, Optional

from workers.celery_app import celery_app
from app.shared.utils import get_logger

_LOG = get_logger(__name__)


@celery_app.task(
    name="indexing_worker.index_vectors",
    bind=True,
    max_retries=2,
    soft_time_limit=300,
    time_limit=360,
)
def index_vectors(
    self,
    records: List[Dict[str, Any]],
    memory_type: str = "episodic",
) -> Dict[str, Any]:
    """Celery task: batch-insert vector records into the configured store.

    Each record dict should contain:
        text (str): The text to embed.
        metadata (dict, optional): Additional metadata.

    Embeddings are generated server-side by the EmbeddingService.

    Returns:
        Summary dict with insert count.
    """
    from app.vector_memory.embeddings import embedding_service
    from app.vector_memory.vector_store import _create_backend
    from app.vector_memory.base import VectorRecord

    import numpy as np

    store = _create_backend(memory_type)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(store.initialize())

        # Build VectorRecord list with fresh embeddings
        texts = [r.get("text", "") for r in records]
        embeddings = embedding_service.encode(texts)

        vector_records = []
        for i, rec in enumerate(records):
            vector_records.append(
                VectorRecord(
                    id=uuid.uuid4().hex,
                    embedding=embeddings[i],
                    text=rec.get("text", ""),
                    metadata={
                        "memory_type": memory_type,
                        **rec.get("metadata", {}),
                    },
                )
            )

        count = loop.run_until_complete(store.batch_insert(vector_records))
        _LOG.info(
            "Indexed %d vectors into '%s'", count, memory_type
        )
        return {"status": "success", "indexed": count, "memory_type": memory_type}

    except Exception as e:
        _LOG.error("Background indexing failed for '%s': %s", memory_type, e)
        raise self.retry(countdown=30, exc=e)
    finally:
        loop.run_until_complete(store.close())
        loop.close()


@celery_app.task(
    name="indexing_worker.run_migration",
    bind=True,
    max_retries=1,
    soft_time_limit=1800,
    time_limit=1860,
)
def run_migration_task(
    self,
    memory_types: Optional[List[str]] = None,
    qdrant_url: str = "http://localhost:6333",
    collection_name: str = "memories",
) -> Dict[str, Any]:
    """Celery task: migrate FAISS indices to Qdrant."""
    from app.vector_memory.migration import run_migration

    loop = asyncio.new_event_loop()
    try:
        result = loop.run_until_complete(
            run_migration(memory_types, qdrant_url, collection_name)
        )
        return result
    except Exception as e:
        _LOG.error("Migration task failed: %s", e)
        raise self.retry(countdown=120, exc=e)
    finally:
        loop.close()
