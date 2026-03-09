from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator


class LLMProvider(ABC):
    """Abstract base class for all LLM providers."""

    @abstractmethod
    async def ask(self, prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
        """Asynchronous chat completion."""
        pass

    @abstractmethod
    async def ask_stream(
        self, prompt: str, system_prompt: str | None = None, model: str | None = None
    ) -> AsyncIterator[str]:
        """Asynchronous streaming chat completion."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Asynchronous health check."""
        pass
