from __future__ import annotations

from typing import Any

from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class Summarizer:
    """Summarization logic to compress conversation history."""

    def __init__(self, llm_provider: Any):
        self.llm = llm_provider

    async def summarize(self, messages: list[dict[str, str]]) -> str:
        if not messages:
            return ""

        history_text = "\n".join([f"{m['role']}: {m['content']}" for m in messages])
        prompt = (
            "Summarize the following conversation history into a concise paragraph "
            "that captures the main topics and entities discussed. "
            "Maintain context for future turns.\n\n"
            f"Conversation:\n{history_text}\n\n"
            "Summary:"
        )

        try:
            summary = await self.llm.ask(prompt, system_prompt="You are a summarization assistant.")
            return summary.strip()
        except Exception as e:
            _LOG.error(f"Summarization failed: {e}")
            return ""
