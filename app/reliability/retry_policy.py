"""
Retry Policy — Exponential backoff with jitter for transient failures.

Implements the "decorrelated jitter" variant recommended by AWS architecture
blog, which provides better spread than simple exponential backoff.

    delay = min(max_delay, random_between(base_delay, previous_delay * 3))

Design rationale:
    • Wraps any async callable with configurable retry logic.
    • Only retries on explicitly retryable exception types.
    • Integrates with FailureTracker for cross-cutting failure accounting.
    • Does NOT interfere with CircuitBreaker — they compose naturally:
        CircuitBreaker wraps RetryPolicy wraps the actual call.
"""
from __future__ import annotations

import asyncio
import random
from typing import Any, Callable, Coroutine, Optional, Sequence, Tuple, Type, TypeVar

from app.reliability.failure_tracker import FailureTracker
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

T = TypeVar("T")

# Default set of transient exceptions worth retrying
DEFAULT_RETRYABLE: Tuple[Type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
    OSError,
)


class RetryExhaustedError(Exception):
    """Raised when all retry attempts have been exhausted."""

    def __init__(self, component: str, attempts: int, last_error: Exception) -> None:
        self.component = component
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"Retry exhausted for '{component}' after {attempts} attempts: {last_error}"
        )


class RetryPolicy:
    """Async retry wrapper with exponential backoff and jitter.

    Args:
        component_name: Logical name for logging and metrics.
        max_retries: Maximum number of retry attempts (0 = no retries).
        base_delay: Initial delay in seconds before the first retry.
        max_delay: Upper bound for any single delay.
        retryable_exceptions: Tuple of exception types eligible for retry.
        tracker: Optional shared FailureTracker.

    Example::

        retry = RetryPolicy("llm_hf", max_retries=3, base_delay=1.0)
        result = await retry.execute(llm.ask, prompt, system_prompt=sp)
    """

    def __init__(
        self,
        component_name: str,
        *,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 30.0,
        retryable_exceptions: Tuple[Type[Exception], ...] = DEFAULT_RETRYABLE,
        tracker: Optional[FailureTracker] = None,
    ) -> None:
        self.component_name = component_name
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.retryable_exceptions = retryable_exceptions
        self.tracker = tracker or FailureTracker(component_name)

    async def execute(
        self,
        coro_fn: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute *coro_fn* with retries on transient failures.

        Returns:
            The result of the coroutine on success.

        Raises:
            RetryExhaustedError: If all retries fail.
            Exception: Re-raises non-retryable exceptions immediately.
        """
        last_error: Optional[Exception] = None
        delay = self.base_delay

        for attempt in range(1, self.max_retries + 2):  # +2 because range is exclusive and attempt 1 is initial
            try:
                result = await coro_fn(*args, **kwargs)
                self.tracker.record_success()
                if attempt > 1:
                    _LOG.info(
                        "%s: Succeeded on attempt %d", self.component_name, attempt
                    )
                return result
            except self.retryable_exceptions as exc:
                last_error = exc
                self.tracker.record_failure(exc)

                if attempt > self.max_retries:
                    break

                # Decorrelated jitter
                delay = min(self.max_delay, random.uniform(self.base_delay, delay * 3))

                _LOG.warning(
                    "%s: Attempt %d failed (%s). Retrying in %.2fs…",
                    self.component_name,
                    attempt,
                    type(exc).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
            except Exception as exc:
                # Non-retryable — fail immediately
                self.tracker.record_failure(exc)
                _LOG.error(
                    "%s: Non-retryable error on attempt %d: %s",
                    self.component_name,
                    attempt,
                    exc,
                )
                raise

        assert last_error is not None
        raise RetryExhaustedError(
            self.component_name, self.max_retries + 1, last_error
        )
