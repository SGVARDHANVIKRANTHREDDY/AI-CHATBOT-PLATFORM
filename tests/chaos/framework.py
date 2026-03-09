"""
Chaos Testing Framework — Core primitives for fault injection.

Provides the building blocks for simulating failures across the AI platform:
    • FaultInjector base class with lifecycle hooks
    • ChaosContext manager for scoped fault injection
    • ChaosResult for structured outcome capture
    • Component-specific fault types

All faults are reversible — they patch targets during the test and
restore originals on cleanup, even if the test raises.
"""

from __future__ import annotations

import asyncio
import enum
import functools
import random
import time
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

# ── Fault categories ──────────────────────────────────────────────


class FaultType(enum.Enum):
    OUTAGE = "outage"  # Component completely unavailable
    LATENCY = "latency"  # Delayed responses
    INTERMITTENT = "intermittent"  # Flaky — fails N% of the time
    CORRUPTION = "corruption"  # Returns bad data
    TIMEOUT = "timeout"  # Hangs until timeout fires
    CRASH = "crash"  # Raises unexpected exception
    RESOURCE_EXHAUSTION = "resource_exhaustion"  # OOM / disk full


class FaultSeverity(enum.Enum):
    PARTIAL = "partial"  # Some operations affected
    COMPLETE = "complete"  # All operations affected


# ── Outcome tracking ─────────────────────────────────────────────


@dataclass
class ChaosResult:
    """Captures the outcome of a chaos experiment."""

    fault_type: FaultType
    target_component: str
    injected_at: float = 0.0
    resolved_at: float = 0.0
    recovered: bool = False
    fallback_used: bool = False
    retries_attempted: int = 0
    error_type: str | None = None
    error_message: str | None = None
    degraded_response: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def recovery_time_ms(self) -> float:
        if self.resolved_at and self.injected_at:
            return (self.resolved_at - self.injected_at) * 1000
        return 0.0


# ── Base fault injector ──────────────────────────────────────────


class FaultInjector:
    """Base class for all fault injectors.

    Subclasses implement ``_apply`` and ``_revert`` to patch/unpatch
    the target component.  The ``inject`` async-context-manager
    guarantees cleanup even on test failure.
    """

    def __init__(
        self,
        component_name: str,
        fault_type: FaultType,
        severity: FaultSeverity = FaultSeverity.COMPLETE,
    ) -> None:
        self.component_name = component_name
        self.fault_type = fault_type
        self.severity = severity
        self._active = False
        self._patches: list[Any] = []

    async def apply(self) -> None:
        """Activate the fault injection."""
        self._active = True
        await self._apply()

    async def revert(self) -> None:
        """Deactivate the fault and restore originals."""
        self._active = False
        await self._revert()
        for p in reversed(self._patches):
            p.stop()
        self._patches.clear()

    async def _apply(self) -> None:
        raise NotImplementedError

    async def _revert(self) -> None:
        pass  # subclasses override if needed beyond patch cleanup

    @asynccontextmanager
    async def inject(self):
        """Context manager for scoped fault injection."""
        result = ChaosResult(
            fault_type=self.fault_type,
            target_component=self.component_name,
            injected_at=time.monotonic(),
        )
        try:
            await self.apply()
            yield result
        finally:
            await self.revert()
            result.resolved_at = time.monotonic()


# ── Failure-producing helpers ─────────────────────────────────────


def make_failing_coro(
    error_cls: type[Exception] = ConnectionError,
    message: str = "Chaos: simulated failure",
):
    """Return an async function that always raises."""

    async def _fail(*args, **kwargs):
        raise error_cls(message)

    return _fail


def make_timeout_coro(delay: float = 120.0):
    """Return an async function that sleeps forever (until cancelled)."""

    async def _hang(*args, **kwargs):
        await asyncio.sleep(delay)
        return "should not reach"

    return _hang


def make_intermittent_coro(
    original_fn: Callable,
    failure_rate: float = 0.5,
    error_cls: type[Exception] = ConnectionError,
):
    """Wraps *original_fn*; fails *failure_rate* fraction of calls."""

    @functools.wraps(original_fn)
    async def _maybe_fail(*args, **kwargs):
        if random.random() < failure_rate:  # noqa: S311
            raise error_cls(f"Chaos: intermittent failure ({failure_rate * 100:.0f}%)")
        return await original_fn(*args, **kwargs)

    return _maybe_fail


def make_latency_coro(original_fn: Callable, added_seconds: float = 5.0):
    """Wraps *original_fn* adding artificial latency."""

    @functools.wraps(original_fn)
    async def _slow(*args, **kwargs):
        await asyncio.sleep(added_seconds)
        return await original_fn(*args, **kwargs)

    return _slow


def make_corrupt_coro(corruption: str = "{{CORRUPTED_RESPONSE}}"):
    """Return an async function that returns garbage data."""

    async def _corrupt(*args, **kwargs):
        return corruption

    return _corrupt
