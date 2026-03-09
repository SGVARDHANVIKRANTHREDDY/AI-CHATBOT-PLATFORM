from __future__ import annotations

from app.rag.retriever import RAGRetriever
from app.shared.utils import get_logger

from workers.celery_app import celery_app

_LOG = get_logger(__name__)


@celery_app.task(name="workers.ingestion_worker.ingest_documents")
def ingest_documents():
    """Background task to rebuild the RAG index."""
    _LOG.info("Starting background document ingestion...")
    try:
        retriever = RAGRetriever()
        retriever.embed_documents()
        _LOG.info("Document ingestion completed successfully.")
        return {"status": "success", "chunks": len(retriever.chunks)}
    except Exception as e:
        _LOG.error(f"Ingestion task failed: {e}")
        return {"status": "error", "message": str(e)}


@celery_app.task(name="workers.ingestion_worker.rebuild_index")
def rebuild_index():
    """Forces a full rebuild of the FAISS index."""
    return ingest_documents()
