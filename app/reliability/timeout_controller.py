"""
Timeout Controller — Bounded execution time for all external calls.

Wraps ``asyncio.wait_for`` with structured logging, named timeouts,
and Prometheus metric emission.

Design rationale:
    Every external call (LLM API, tool execution, vector search) MUST have
    a timeout.  Without one, a hung upstream can block an agent indefinitely,
    consuming a worker slot and degrading the whole system.

    TimeoutController provides a clean, reusable API so callers don't
    scatter raw ``asyncio.wait_for`` everywhere.
"""
from __future__ import annotations

import asyncio
from typing import Any, Callable, Coroutine, TypeVar

from app.shared.utils import get_logger

_LOG = get_logger(__name__)

T = TypeVar("T")


class TimeoutError(asyncio.TimeoutError):
    """Domain-specific timeout for clearer stack traces."""

    def __init__(self, operation: str, timeout_seconds: float) -> None:
        self.operation = operation
        self.timeout_seconds = timeout_seconds
        super().__init__(
            f"Operation '{operation}' timed out after {timeout_seconds:.1f}s"
        )


class TimeoutController:
    """Named timeout wrapper for async coroutines.

    Args:
        operation_name: Human-readable name for logs and metrics.
        timeout_seconds: Maximum time allowed before cancellation.

    Example::

        tc = TimeoutController("llm_ask", timeout_seconds=30.0)
        result = await tc.execute(llm.ask, prompt, system_prompt=sp)
    """

    def __init__(self, operation_name: str, timeout_seconds: float = 30.0) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.operation_name = operation_name
        self.timeout_seconds = timeout_seconds

    async def execute(
        self,
        coro_fn: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute *coro_fn* with a hard timeout.

        Returns:
            The result of the coroutine on success.

        Raises:
            TimeoutError: If the coroutine does not complete in time.
        """
        try:
            return await asyncio.wait_for(
                coro_fn(*args, **kwargs),
                timeout=self.timeout_seconds,
            )
        except asyncio.TimeoutError:
            _LOG.error(
                "Timeout: '%s' exceeded %ss limit",
                self.operation_name,
                self.timeout_seconds,
            )
            raise TimeoutError(self.operation_name, self.timeout_seconds)

    async def execute_with_fallback(
        self,
        coro_fn: Callable[..., Coroutine[Any, Any, T]],
        fallback_value: T,
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute *coro_fn* with a timeout, returning *fallback_value* on timeout.

        This is useful for non-critical enrichment calls where a timeout
        should not block the main flow.
        """
        try:
            return await self.execute(coro_fn, *args, **kwargs)
        except (asyncio.TimeoutError, TimeoutError):
            _LOG.warning(
                "Timeout fallback: '%s' — returning default value",
                self.operation_name,
            )
            return fallback_value
