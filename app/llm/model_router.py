from __future__ import annotations

import re
import time
from typing import Any

from app.config.settings import settings
from app.shared.utils import get_logger, log_event

_LOG = get_logger(__name__)


class ModelRouter:
    """
    Intelligently routes requests to specialized models based on query intent.
    Tracks latency, intent distribution, and provides cost hooks.
    """

    def __init__(self):
        # Intent detection patterns
        self.routes = {
            "coding": re.compile(
                r"(?i)\b(code|python|js|javascript|java|rust|implement|function|class|refactor|debug|api)\b"
            ),
            "summarization": re.compile(r"(?i)\b(summarize|summary|tl;dr|shorter|bullet points|abstract)\b"),
            "reasoning": re.compile(r"(?i)\b(reason|logic|math|calculate|solve|complex|philosophy|strategy|proof)\b"),
            "embeddings": re.compile(r"(?i)\b(vector|embed|similarity|search context|rag setup)\b"),
        }

        # Approximate costs per 1k tokens (illustrative)
        self.costs = {
            settings.HF_MODEL: 0.0001,
            settings.MODEL_REASONING: 0.0003,
            settings.MODEL_CODING: 0.0002,
            settings.MODEL_SUMMARIZATION: 0.00005,
        }

    def detect_intent(self, text: str) -> str:
        """Detects the primary intent of the query."""
        for intent, pattern in self.routes.items():
            if pattern.search(text):
                return intent
        return "general"

    def get_model_for_intent(self, intent: str) -> str:
        """Maps intent to the specialized model from settings."""
        intent_models = {
            "coding": settings.MODEL_CODING,
            "summarization": settings.MODEL_SUMMARIZATION,
            "reasoning": settings.MODEL_REASONING,
        }
        return intent_models.get(intent, settings.HF_MODEL)

    def get_cost_estimate(self, model: str, tokens: int) -> float:
        """Estimates cost based on model and token count."""
        base_rate = self.costs.get(model, 0.0001)
        return (tokens / 1000) * base_rate

    async def route(self, query: str) -> dict[str, Any]:
        """Routes a query and returns model metadata."""
        start_time = time.perf_counter()

        intent = self.detect_intent(query)
        selected_model = self.get_model_for_intent(intent)

        latency_ms = (time.perf_counter() - start_time) * 1000

        log_event(_LOG, "model_routed", intent=intent, model=selected_model, latency_ms=f"{latency_ms:.2f}")

        return {
            "selected_model": selected_model,
            "intent": intent,
            "routing_latency_ms": latency_ms,
            "estimated_cost_per_1k": self.costs.get(selected_model, 0.0001),
        }
