"""
Failure Tracker — Centralized failure accounting per component.

Maintains sliding-window statistics (failure rate, total failures,
last failure time) used by CircuitBreaker and RetryPolicy to share
cross-cutting failure intelligence.

Design rationale:
    Decouple failure observation from failure *policy* (circuit-breaking,
    retrying).  A single FailureTracker instance per logical component
    lets every reliability primitive see the same view of health.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass

from app.shared.utils import get_logger

_LOG = get_logger(__name__)


@dataclass
class FailureRecord:
    """Immutable record of a single failure event."""

    timestamp: float
    error_type: str
    message: str


@dataclass
class FailureStats:
    """Point-in-time snapshot of failure statistics."""

    total_failures: int = 0
    total_successes: int = 0
    failure_rate: float = 0.0
    last_failure_time: float | None = None
    last_failure_type: str | None = None
    window_failures: int = 0
    window_successes: int = 0


class FailureTracker:
    """Tracks failures and successes for a named component using a sliding window.

    Thread-safe — uses a lock so callers from different asyncio tasks
    or threads can share a single tracker safely.

    Args:
        component_name: Human-readable name for logging and metrics.
        window_seconds: Length of the sliding window in seconds.
        max_records: Maximum failure records kept in memory.
    """

    def __init__(
        self,
        component_name: str,
        window_seconds: float = 60.0,
        max_records: int = 1000,
    ) -> None:
        self.component_name = component_name
        self.window_seconds = window_seconds
        self.max_records = max_records

        self._lock = threading.Lock()
        self._failures: deque[FailureRecord] = deque(maxlen=max_records)
        self._success_timestamps: deque[float] = deque(maxlen=max_records)
        self._total_failures: int = 0
        self._total_successes: int = 0

    # ── Public API ────────────────────────────────────────────────

    def record_failure(self, error: Exception) -> None:
        """Record a failure event."""
        now = time.monotonic()
        record = FailureRecord(
            timestamp=now,
            error_type=type(error).__name__,
            message=str(error)[:256],
        )
        with self._lock:
            self._failures.append(record)
            self._total_failures += 1

        _LOG.warning(
            "Failure recorded for %s: %s — %s",
            self.component_name,
            record.error_type,
            record.message,
        )

    def record_success(self) -> None:
        """Record a successful call."""
        with self._lock:
            self._success_timestamps.append(time.monotonic())
            self._total_successes += 1

    def get_stats(self) -> FailureStats:
        """Return a point-in-time snapshot of failure statistics."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            window_failures = sum(1 for r in self._failures if r.timestamp >= cutoff)
            window_successes = sum(1 for t in self._success_timestamps if t >= cutoff)
            total_in_window = window_failures + window_successes
            failure_rate = window_failures / total_in_window if total_in_window > 0 else 0.0
            last = self._failures[-1] if self._failures else None

        return FailureStats(
            total_failures=self._total_failures,
            total_successes=self._total_successes,
            failure_rate=failure_rate,
            last_failure_time=last.timestamp if last else None,
            last_failure_type=last.error_type if last else None,
            window_failures=window_failures,
            window_successes=window_successes,
        )

    def reset(self) -> None:
        """Reset all tracked state — useful after a full recovery."""
        with self._lock:
            self._failures.clear()
            self._success_timestamps.clear()
            self._total_failures = 0
            self._total_successes = 0
        _LOG.info("FailureTracker reset for %s", self.component_name)
