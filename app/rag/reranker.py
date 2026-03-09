from __future__ import annotations

from typing import Any

from sentence_transformers import CrossEncoder

from app.config.settings import settings
from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class CrossEncoderReranker:
    """Re-ranks candidate chunks using a cross-encoder model for high precision."""

    def __init__(self, model_name: str = settings.RERANKER_MODEL):
        self.model_name = model_name
        self._model: CrossEncoder | None = None

    def _get_model(self) -> CrossEncoder:
        if self._model is None:
            _LOG.info(f"Loading Cross-Encoder model: {self.model_name}")
            self._model = CrossEncoder(self.model_name)
        return self._model

    def rerank(
        self, query: str, chunks: list[dict[str, Any]], top_k: int = settings.RERANKER_TOP_K
    ) -> list[dict[str, Any]]:
        if not chunks or not settings.RERANKING_ENABLED:
            return chunks[:top_k]

        model = self._get_model()

        # Prepare pairs for cross-encoding: (query, chunk_text)
        pairs = [[query, c["text"]] for c in chunks]

        # Cross-encoder scores (higher is better)
        scores = model.predict(pairs)

        # Zip scores with chunks
        for i, score in enumerate(scores):
            chunks[i]["rerank_score"] = float(score)

        # Sort by rerank score
        sorted_chunks = sorted(chunks, key=lambda x: x["rerank_score"], reverse=True)

        _LOG.info(f"Re-ranked {len(chunks)} chunks, returning top {top_k}")
        return sorted_chunks[:top_k]
