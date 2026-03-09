from __future__ import annotations

import requests

from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class LocalLLM:
    """Legacy Ollama client (To be removed in Phase 4)."""

    def __init__(self, model_name: str, base_url: str = "http://localhost:11434"):
        self.model = model_name
        self.base_url = base_url.rstrip("/")

    def ask(self, prompt: str, system_prompt: str | None = None) -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload = {"model": self.model, "messages": messages, "stream": False}
        try:
            r = requests.post(f"{self.base_url}/api/chat", json=payload, timeout=120)
            r.raise_for_status()
            return r.json().get("message", {}).get("content", "").strip()
        except Exception as e:
            _LOG.error(f"Ollama request failed: {e}")
            return f"[ERROR] Ollama failed: {e}"
