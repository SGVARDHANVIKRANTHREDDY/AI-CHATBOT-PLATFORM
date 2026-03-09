from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.config.settings import settings
from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class DatasetBuilder:
    """
    Collects interaction data (Query, Response, Grade) for future training.
    Specifically flags low-score responses for human/machine review.
    """

    def __init__(self, storage_path: Path = settings.VECTOR_INDEX_DIR / "evaluation_dataset.json"):
        self.path = storage_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data: list[dict[str, Any]] = []
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                with open(self.path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except Exception:
                _LOG.debug("Failed to load dataset from %s", self.path)

    def _save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except Exception as e:
            _LOG.error(f"Failed to save dataset: {e}")

    def log_interaction(self, query: str, response: str, grade: dict[str, Any], plan: dict[str, Any] | None = None):
        """Logs a completed interaction and its evaluation."""
        entry = {
            "timestamp": datetime.now().isoformat(),
            "query": query,
            "response": response,
            "plan": plan,
            "grade": grade,
            "success": grade.get("score", 0.0) >= 0.7,
        }
        self.data.append(entry)

        if not entry["success"]:
            _LOG.warning(f"Logged failure for potential correction. Score: {grade.get('score')}")

        self._save()
