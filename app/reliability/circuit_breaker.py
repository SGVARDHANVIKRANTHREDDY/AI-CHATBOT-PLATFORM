"""
Circuit Breaker — Prevents cascading failures across the AI platform.

Implements the three-state circuit breaker pattern (CLOSED → OPEN → HALF_OPEN)
modelled after AWS resilience patterns and Google SRE principles.

State machine:
    CLOSED   — All calls pass through.  Failures are counted.
               When failure threshold is exceeded → OPEN.
    OPEN     — All calls are rejected immediately with CircuitOpenError.
               After recovery_timeout elapses → HALF_OPEN.
    HALF_OPEN — A limited number of probe calls are allowed through.
               If they succeed → CLOSED.  If any fail → OPEN.

Design rationale:
    • Protects LLM provider calls, tool execution, and vector queries.
    • Prevents a degraded upstream from consuming all resources.
    • Integrates with FailureTracker for shared failure intelligence.
    • Emits Prometheus metrics on every state change.
"""

from __future__ import annotations

import enum
import threading
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from app.reliability.failure_tracker import FailureTracker
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

T = TypeVar("T")


class CircuitState(enum.Enum):
    """Possible states of the circuit breaker."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is rejected because the circuit is open."""

    def __init__(self, component: str, retry_after: float) -> None:
        self.component = component
        self.retry_after = retry_after
        super().__init__(f"Circuit open for '{component}'. Retry after {retry_after:.1f}s.")


class CircuitBreaker:
    """Async-compatible circuit breaker for any coroutine.

    Args:
        component_name: Logical name (e.g. ``"llm_provider"``, ``"tool_runner"``).
        failure_threshold: Number of failures in the tracker's window that trip the breaker.
        recovery_timeout: Seconds the circuit stays OPEN before transitioning to HALF_OPEN.
        half_open_max_calls: Max probe calls allowed in HALF_OPEN before deciding.
        tracker: Optional shared FailureTracker; one is created if not supplied.
        fallback: Optional async callable invoked when the circuit is OPEN.

    Example::

        cb = CircuitBreaker("llm_openai", failure_threshold=5, recovery_timeout=30)

        try:
            result = await cb.call(llm.ask, prompt, system_prompt=sp)
        except CircuitOpenError:
            result = "Service temporarily unavailable"
    """

    def __init__(
        self,
        component_name: str,
        *,
        failure_threshold: int = 5,
        recovery_timeout: float = 30.0,
        half_open_max_calls: int = 3,
        tracker: FailureTracker | None = None,
        fallback: Callable[..., Coroutine[Any, Any, Any]] | None = None,
    ) -> None:
        self.component_name = component_name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls
        self.fallback = fallback

        self.tracker = tracker or FailureTracker(component_name)

        self._state = CircuitState.CLOSED
        self._opened_at: float = 0.0
        self._half_open_calls: int = 0
        self._lock = threading.Lock()

        # Lazy import to avoid circular deps at module level
        self._metrics_emitted = False

    # ── State inspection ──────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        """Current state, accounting for automatic OPEN → HALF_OPEN transition."""
        with self._lock:
            if self._state == CircuitState.OPEN and time.monotonic() - self._opened_at >= self.recovery_timeout:
                self._transition(CircuitState.HALF_OPEN)
            return self._state

    # ── Core call wrapper ─────────────────────────────────────────

    async def call(
        self,
        coro_fn: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute *coro_fn* through the circuit breaker.

        Returns:
            The result of the coroutine on success.

        Raises:
            CircuitOpenError: If the circuit is OPEN and no fallback is set.
        """
        current = self.state  # triggers auto-transition check

        if current == CircuitState.OPEN:
            return await self._handle_open(*args, **kwargs)

        if current == CircuitState.HALF_OPEN:
            with self._lock:
                if self._half_open_calls >= self.half_open_max_calls:
                    return await self._handle_open(*args, **kwargs)
                self._half_open_calls += 1

        # CLOSED or HALF_OPEN probe
        try:
            result = await coro_fn(*args, **kwargs)
            self._on_success()
            return result
        except Exception as exc:
            self._on_failure(exc)
            raise

    # ── Internal helpers ──────────────────────────────────────────

    async def _handle_open(self, *args: Any, **kwargs: Any) -> Any:
        """Called when circuit is OPEN — use fallback or raise."""
        retry_after = self.recovery_timeout - (time.monotonic() - self._opened_at)
        if self.fallback is not None:
            _LOG.info("Circuit OPEN for %s — invoking fallback", self.component_name)
            return await self.fallback(*args, **kwargs)
        raise CircuitOpenError(self.component_name, max(retry_after, 0.0))

    def _on_success(self) -> None:
        self.tracker.record_success()
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._transition(CircuitState.CLOSED)

    def _on_failure(self, exc: Exception) -> None:
        self.tracker.record_failure(exc)
        stats = self.tracker.get_stats()
        with self._lock:
            if (
                self._state in (CircuitState.CLOSED, CircuitState.HALF_OPEN)
                and stats.window_failures >= self.failure_threshold
            ):
                self._transition(CircuitState.OPEN)

    def _transition(self, new_state: CircuitState) -> None:
        """Transition to *new_state* — caller must hold ``_lock``."""
        old = self._state
        self._state = new_state

        if new_state == CircuitState.OPEN:
            self._opened_at = time.monotonic()
        elif new_state == CircuitState.HALF_OPEN:
            self._half_open_calls = 0

        _LOG.info(
            "CircuitBreaker[%s]: %s → %s",
            self.component_name,
            old.value,
            new_state.value,
        )
        self._emit_metric(new_state)

    def _emit_metric(self, new_state: CircuitState) -> None:
        """Emit Prometheus counter for state transitions."""
        try:
            from app.shared.monitoring import CIRCUIT_BREAKER_STATE_CHANGES

            CIRCUIT_BREAKER_STATE_CHANGES.labels(component=self.component_name, state=new_state.value).inc()
        except Exception:  # noqa: S110
            pass  # Metrics are best-effort

    # ── Manual controls ───────────────────────────────────────────

    def force_open(self) -> None:
        """Manually trip the circuit (e.g. during planned maintenance)."""
        with self._lock:
            self._transition(CircuitState.OPEN)

    def force_close(self) -> None:
        """Manually reset the circuit."""
        with self._lock:
            self._transition(CircuitState.CLOSED)
        self.tracker.reset()
