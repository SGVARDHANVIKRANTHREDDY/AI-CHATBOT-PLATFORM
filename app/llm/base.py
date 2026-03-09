from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, Iterator, List, Optional, Union

class LLMProvider(ABC):
    """Abstract base class for all LLM providers."""
    
    @abstractmethod
    async def ask(self, prompt: str, system_prompt: Optional[str] = None, model: Optional[str] = None) -> str:
        """Asynchronous chat completion."""
        pass

    @abstractmethod
    async def ask_stream(self, prompt: str, system_prompt: Optional[str] = None, model: Optional[str] = None) -> AsyncIterator[str]:
        """Asynchronous streaming chat completion."""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """Asynchronous health check."""
        pass
