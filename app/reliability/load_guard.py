"""
Load Guard — System load protection with graceful degradation.

Implements admission control and throttling to prevent system collapse
under heavy load.  Modelled after Google SRE load-shedding patterns.

Components:
    RequestQueueLimiter  — Bounds total concurrent API requests.
    AgentExecutionLimiter — Bounds total concurrent agent loops.
    SwarmThrottle        — Dynamically reduces swarm parallelism under pressure.

Design rationale:
    Without admission control, a traffic spike spawns unlimited agents and
    LLM calls, exhausting memory and API quotas.  These limiters provide
    back-pressure so the system degrades gracefully rather than crashing.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

from app.shared.utils import get_logger

_LOG = get_logger(__name__)

T = TypeVar("T")


class LoadGuardRejectionError(Exception):
    """Raised when a request is rejected due to load limits."""

    def __init__(self, limiter_name: str, current: int, limit: int) -> None:
        self.limiter_name = limiter_name
        self.current = current
        self.limit = limit
        super().__init__(f"{limiter_name}: Rejected — {current}/{limit} slots in use")


class RequestQueueLimiter:
    """Semaphore-based admission control for incoming API requests.

    Args:
        max_concurrent: Maximum simultaneous requests allowed.
        queue_timeout: Seconds to wait for a slot before rejecting.
    """

    def __init__(self, max_concurrent: int = 100, queue_timeout: float = 10.0) -> None:
        self.max_concurrent = max_concurrent
        self.queue_timeout = queue_timeout
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active: int = 0
        self._total_rejected: int = 0

    @property
    def active_requests(self) -> int:
        return self._active

    @property
    def utilization(self) -> float:
        return self._active / self.max_concurrent if self.max_concurrent > 0 else 0.0

    async def acquire(self) -> bool:
        """Try to acquire a request slot within the queue timeout."""
        try:
            acquired = await asyncio.wait_for(self._semaphore.acquire(), timeout=self.queue_timeout)
            if acquired:
                self._active += 1
            return acquired
        except TimeoutError:
            self._total_rejected += 1
            _LOG.warning(
                "RequestQueueLimiter: Rejected request (active=%d, limit=%d)",
                self._active,
                self.max_concurrent,
            )
            return False

    def release(self) -> None:
        """Release a request slot."""
        self._active = max(0, self._active - 1)
        self._semaphore.release()

    async def execute(
        self,
        coro_fn: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute within the request limit, raising on rejection."""
        if not await self.acquire():
            raise LoadGuardRejectionError("RequestQueueLimiter", self._active, self.max_concurrent)
        try:
            return await coro_fn(*args, **kwargs)
        finally:
            self.release()

    def get_metrics(self) -> dict:
        """Return current limiter metrics for Prometheus export."""
        return {
            "active_requests": self._active,
            "max_concurrent": self.max_concurrent,
            "utilization": self.utilization,
            "total_rejected": self._total_rejected,
        }


class AgentExecutionLimiter:
    """Bounds total concurrent agent executions across all sessions.

    Prevents agent loop explosions — each agentic reasoning step
    consumes an LLM call, so unbounded agents can exhaust quotas.

    Args:
        max_agents: Maximum simultaneous agent executions.
        queue_timeout: Seconds to wait before falling back to a simpler response.
    """

    def __init__(self, max_agents: int = 20, queue_timeout: float = 15.0) -> None:
        self.max_agents = max_agents
        self.queue_timeout = queue_timeout
        self._semaphore = asyncio.Semaphore(max_agents)
        self._active: int = 0

    @property
    def active_agents(self) -> int:
        return self._active

    async def execute(
        self,
        coro_fn: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute within the agent limit."""
        try:
            await asyncio.wait_for(self._semaphore.acquire(), timeout=self.queue_timeout)
        except TimeoutError:
            _LOG.warning(
                "AgentExecutionLimiter: All %d agent slots occupied — rejecting",
                self.max_agents,
            )
            raise LoadGuardRejectionError("AgentExecutionLimiter", self._active, self.max_agents) from None

        self._active += 1
        try:
            return await coro_fn(*args, **kwargs)
        finally:
            self._active = max(0, self._active - 1)
            self._semaphore.release()


class SwarmThrottle:
    """Dynamic throttle that reduces swarm parallelism under system pressure.

    Monitors current utilization from the request limiter and agent limiter
    and computes a ``throttle_factor`` (0.0-1.0) that swarm execution
    should multiply against its ``max_parallel`` setting.

    Args:
        request_limiter: Reference to the RequestQueueLimiter.
        agent_limiter: Reference to the AgentExecutionLimiter.
        pressure_threshold: Utilization above which throttling begins (0.0-1.0).
    """

    def __init__(
        self,
        request_limiter: RequestQueueLimiter | None = None,
        agent_limiter: AgentExecutionLimiter | None = None,
        pressure_threshold: float = 0.7,
    ) -> None:
        self.request_limiter = request_limiter
        self.agent_limiter = agent_limiter
        self.pressure_threshold = pressure_threshold

    @property
    def throttle_factor(self) -> float:
        """Returns a factor [0.1, 1.0] by which swarm parallelism should be reduced.

        1.0 = no throttle, 0.1 = maximum throttle (only 10% of normal parallelism).
        """
        utilization = 0.0
        if self.request_limiter:
            utilization = max(utilization, self.request_limiter.utilization)
        if self.agent_limiter:
            agent_util = (
                self.agent_limiter.active_agents / self.agent_limiter.max_agents
                if self.agent_limiter.max_agents > 0
                else 0.0
            )
            utilization = max(utilization, agent_util)

        if utilization <= self.pressure_threshold:
            return 1.0

        # Linear ramp-down from 1.0 at threshold to 0.1 at 100% utilization
        excess = (utilization - self.pressure_threshold) / (1.0 - self.pressure_threshold)
        factor = max(0.1, 1.0 - excess * 0.9)
        _LOG.info(
            "SwarmThrottle: utilization=%.2f, throttle_factor=%.2f",
            utilization,
            factor,
        )
        return factor

    def get_effective_parallelism(self, base_parallel: int) -> int:
        """Compute effective parallelism for swarm execution."""
        return max(1, int(base_parallel * self.throttle_factor))
