from __future__ import annotations
from typing import List, Optional
import numpy as np
from sentence_transformers import SentenceTransformer
from app.config.settings import settings
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

class EmbeddingService:
    """
    Centralized service for generating text embeddings.
    """
    _instance: Optional[EmbeddingService] = None
    _model: Optional[SentenceTransformer] = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(EmbeddingService, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        # Already initialized via singleton pattern if _model is set
        if self._model is None:
            _LOG.info(f"Initializing SentenceTransformer with model: {settings.EMBEDDING_MODEL}")
            self._model = SentenceTransformer(settings.EMBEDDING_MODEL)

    def encode(self, texts: List[str]) -> np.ndarray:
        """Generates normalized embeddings for a list of strings."""
        embs = self._model.encode(texts, normalize_embeddings=True)
        return np.asarray(embs, dtype="float32")

    def encode_single(self, text: str) -> np.ndarray:
        """Generates a single normalized embedding."""
        emb = self._model.encode([text], normalize_embeddings=True)
        return np.asarray(emb[0], dtype="float32")

embedding_service = EmbeddingService()
