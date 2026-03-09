from __future__ import annotations
import time
from typing import AsyncIterator, List, Optional, Dict
from app.llm.base import LLMProvider
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

class CircuitBreaker:
    def __init__(self, failure_threshold: int = 3, recovery_timeout: int = 60):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.failure_count = 0
        self.last_failure_time = 0
        self.state = "CLOSED" # CLOSED, OPEN, HALF-OPEN

    def is_available(self) -> bool:
        if self.state == "OPEN":
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = "HALF-OPEN"
                return True
            return False
        return True

    def record_success(self):
        self.failure_count = 0
        self.state = "CLOSED"

    def record_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        if self.failure_count >= self.failure_threshold:
            self.state = "OPEN"
            _LOG.error(f"Circuit breaker TRIPPED! State: {self.state}")

class FallbackProvider(LLMProvider):
    def __init__(self, primary: LLMProvider, fallbacks: List[LLMProvider]):
        self.primary = primary
        self.fallbacks = fallbacks
        self.breakers: Dict[LLMProvider, CircuitBreaker] = {
            p: CircuitBreaker() for p in ([primary] + fallbacks)
        }

    async def ask(self, prompt: str, system_prompt: Optional[str] = None, model: Optional[str] = None) -> str:
        # Try primary first if available
        if self.breakers[self.primary].is_available():
            try:
                res = await self.primary.ask(prompt, system_prompt=system_prompt, model=model)
                self.breakers[self.primary].record_success()
                return res
            except Exception as e:
                _LOG.warning(f"Primary provider failed: {e}. Recording failure.")
                self.breakers[self.primary].record_failure()
        
        # Try fallbacks
        for fallback in self.fallbacks:
            if self.breakers[fallback].is_available():
                try:
                    res = await fallback.ask(prompt, system_prompt=system_prompt, model=model)
                    self.breakers[fallback].record_success()
                    return res
                except Exception as fe:
                    _LOG.error(f"Fallback provider failed: {fe}. Recording failure.")
                    self.breakers[fallback].record_failure()
        
        raise RuntimeError("All LLM providers are currently unavailable or circuit-broken.")

    async def ask_stream(self, prompt: str, system_prompt: Optional[str] = None, model: Optional[str] = None) -> AsyncIterator[str]:
        # Implementation for streaming fallback with circuit breaker
        if self.breakers[self.primary].is_available():
            try:
                # We consume a bit to see if it even starts
                async for chunk in self.primary.ask_stream(prompt, system_prompt=system_prompt, model=model):
                    yield chunk
                self.breakers[self.primary].record_success()
                return
            except Exception as e:
                _LOG.warning(f"Primary streaming failed: {e}")
                self.breakers[self.primary].record_failure()

        for fallback in self.fallbacks:
            if self.breakers[fallback].is_available():
                try:
                    async for chunk in fallback.ask_stream(prompt, system_prompt=system_prompt, model=model):
                        yield chunk
                    self.breakers[fallback].record_success()
                    return
                except Exception as fe:
                    _LOG.error(f"Fallback streaming failed: {fe}")
                    self.breakers[fallback].record_failure()

        yield "\n[ERROR] All LLM providers failed or are suspended."

    async def health_check(self) -> bool:
        # Check if at least one provider is CLOSED and healthy
        for p, b in self.breakers.items():
            if b.state == "CLOSED" and await p.health_check():
                return True
        return False
