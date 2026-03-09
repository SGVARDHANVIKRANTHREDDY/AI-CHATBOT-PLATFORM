from __future__ import annotations

import time
from collections.abc import AsyncIterator

from huggingface_hub import AsyncInferenceClient
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.config.settings import settings
from app.llm.base import LLMProvider
from app.shared.monitoring import LLM_CALL_DURATION, LLM_TOKEN_USAGE
from app.shared.tracing import traced
from app.shared.utils import emit_observability_event, get_logger

_LOG = get_logger(__name__)


class HuggingFaceProvider(LLMProvider):
    def __init__(
        self,
        model_id: str = settings.HF_MODEL,
        token: str | None = settings.HF_TOKEN,
        api_url: str | None = settings.HF_API_URL,
    ):
        self.model_id = model_id
        if api_url:
            self.client = AsyncInferenceClient(model=api_url, token=token)
        else:
            self.client = AsyncInferenceClient(model=model_id, token=token)

    @retry(
        stop=stop_after_attempt(settings.LLM_RETRY_ATTEMPTS),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type(Exception),
        reraise=True,
    )
    @traced("llm.ask")
    async def ask(self, prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
        model_to_use = model or self.model_id
        t0 = time.perf_counter()
        emit_observability_event(
            _LOG,
            event="llm.call.start",
            category="llm",
            model=model_to_use,
            provider="huggingface",
            prompt_length=len(prompt),
        )
        try:
            response = await self.client.chat_completion(
                model=model_to_use,
                messages=[
                    {"role": "system", "content": system_prompt or "You are a helpful assistant."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=settings.HF_MAX_TOKENS,
                temperature=settings.HF_TEMPERATURE,
                stream=False,
            )
            elapsed = time.perf_counter() - t0
            LLM_CALL_DURATION.labels(model=model_to_use, provider="huggingface").observe(elapsed)
            content = response.choices[0].message.content.strip()
            # Token usage from HF response if available
            usage = getattr(response, "usage", None)
            if usage:
                prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
                completion_tokens = getattr(usage, "completion_tokens", 0) or 0
                LLM_TOKEN_USAGE.labels(model=model_to_use, type="prompt").inc(prompt_tokens)
                LLM_TOKEN_USAGE.labels(model=model_to_use, type="completion").inc(completion_tokens)
            emit_observability_event(
                _LOG,
                event="llm.call.complete",
                category="llm",
                duration_ms=elapsed * 1000,
                model=model_to_use,
                provider="huggingface",
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
                provider="huggingface",
                error=str(e),
            )
            _LOG.error(f"HuggingFace request failed: {e}")
            raise

    async def ask_stream(
        self, prompt: str, system_prompt: str | None = None, model: str | None = None
    ) -> AsyncIterator[str]:
        model_to_use = model or self.model_id
        _LOG.info(f"Asynchronous streaming from HF API: {model_to_use}")
        try:
            stream = await self.client.chat_completion(
                model=model_to_use,
                messages=[
                    {"role": "system", "content": system_prompt or "You are a helpful assistant."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=settings.HF_MAX_TOKENS,
                temperature=settings.HF_TEMPERATURE,
                stream=True,
            )
            async for chunk in stream:
                content = chunk.choices[0].delta.content
                if content:
                    yield content
        except Exception as e:
            _LOG.error(f"HuggingFace streaming failed: {e}")
            yield f"\n[ERROR] Streaming failed: {e}"

    async def health_check(self) -> bool:
        try:
            await self.client.get_model_status(self.model_id)
            return True
        except Exception:
            return False
