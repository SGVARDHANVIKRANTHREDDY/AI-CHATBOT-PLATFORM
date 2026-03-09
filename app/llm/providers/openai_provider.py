from __future__ import annotations

import time
from collections.abc import AsyncIterator

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config.settings import settings
from app.llm.base import LLMProvider
from app.shared.monitoring import LLM_CALL_DURATION, LLM_TOKEN_USAGE
from app.shared.tracing import traced
from app.shared.utils import emit_observability_event, get_logger

_LOG = get_logger(__name__)


class OpenAIProvider(LLMProvider):
    def __init__(self, api_key: str | None = settings.OPENAI_API_KEY, model: str = settings.OPENAI_MODEL):
        self.model = model
        self._api_key = api_key
        self.client = None
        if api_key:
            try:
                import openai

                self.client = openai.AsyncOpenAI(api_key=api_key)
            except ImportError:
                _LOG.warning("openai package not installed; OpenAIProvider will be unavailable.")

    @retry(
        stop=stop_after_attempt(settings.LLM_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    @traced("llm.ask")
    async def ask(self, prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
        if not self.client:
            return "[ERROR] OpenAI API Key not configured."
        model_to_use = model or self.model
        t0 = time.perf_counter()
        emit_observability_event(
            _LOG,
            event="llm.call.start",
            category="llm",
            model=model_to_use,
            provider="openai",
            prompt_length=len(prompt),
        )
        try:
            response = await self.client.chat.completions.create(
                model=model_to_use,
                messages=[
                    {"role": "system", "content": system_prompt or "You are a helpful assistant."},
                    {"role": "user", "content": prompt},
                ],
                temperature=settings.OPENAI_TEMPERATURE,
            )
            elapsed = time.perf_counter() - t0
            LLM_CALL_DURATION.labels(model=model_to_use, provider="openai").observe(elapsed)
            content = response.choices[0].message.content.strip()
            # Token usage from OpenAI response
            usage = getattr(response, "usage", None)
            if usage:
                LLM_TOKEN_USAGE.labels(model=model_to_use, type="prompt").inc(usage.prompt_tokens or 0)
                LLM_TOKEN_USAGE.labels(model=model_to_use, type="completion").inc(usage.completion_tokens or 0)
            emit_observability_event(
                _LOG,
                event="llm.call.complete",
                category="llm",
                duration_ms=elapsed * 1000,
                model=model_to_use,
                provider="openai",
                response_length=len(content),
            )
            return content
        except Exception as e:
            elapsed = time.perf_counter() - t0
            emit_observability_event(
                _LOG,
                event="llm.call.error",
                category="llm",
                duration_ms=elapsed * 1000,
                model=model_to_use,
                provider="openai",
                error=str(e),
            )
            _LOG.error(f"OpenAI request failed: {e}")
            raise

    async def ask_stream(
        self, prompt: str, system_prompt: str | None = None, model: str | None = None
    ) -> AsyncIterator[str]:
        if not self.client:
            yield "[ERROR] OpenAI API Key not configured."
            return
        model_to_use = model or self.model
        _LOG.info(f"Asynchronous streaming from OpenAI: {model_to_use}")
        try:
            stream = await self.client.chat.completions.create(
                model=model_to_use,
                messages=[
                    {"role": "system", "content": system_prompt or "You are a helpful assistant."},
                    {"role": "user", "content": prompt},
                ],
                stream=True,
                temperature=settings.OPENAI_TEMPERATURE,
            )
            async for chunk in stream:
                content = chunk.choices[0].delta.content
                if content:
                    yield content
        except Exception as e:
            _LOG.error(f"OpenAI streaming failed: {e}")
            yield f"\n[ERROR] Streaming failed: {e}"

    async def health_check(self) -> bool:
        return self.client is not None
