"""
E2E Test: Swarm Execution and Load Protection

Tests parallel agent execution with throttling,
load guard admission control, and graceful degradation.
"""
from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock

from app.reliability.load_guard import (
    RequestQueueLimiter,
    AgentExecutionLimiter,
    SwarmThrottle,
    LoadGuardRejection,
)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def request_limiter():
    return RequestQueueLimiter(max_concurrent=5, queue_timeout=0.5)


@pytest.fixture
def agent_limiter():
    return AgentExecutionLimiter(max_agents=3, queue_timeout=0.5)


# ── Request Queue Limiter Tests ──────────────────────────────────

class TestRequestQueueLimiter:
    """Tests for the RequestQueueLimiter admission control."""

    @pytest.mark.asyncio
    async def test_allows_within_limit(self, request_limiter):
        """Requests within the limit pass through."""
        async def work():
            await asyncio.sleep(0.01)
            return "done"

        result = await request_limiter.execute(work)
        assert result == "done"

    @pytest.mark.asyncio
    async def test_tracks_active_requests(self, request_limiter):
        """Active request count is tracked correctly."""
        assert request_limiter.active_requests == 0

        acquired = await request_limiter.acquire()
        assert acquired is True
        assert request_limiter.active_requests == 1

        request_limiter.release()
        assert request_limiter.active_requests == 0

    @pytest.mark.asyncio
    async def test_rejects_over_limit(self):
        """Requests over the limit are rejected after timeout."""
        limiter = RequestQueueLimiter(max_concurrent=1, queue_timeout=0.1)

        async def slow_work():
            await asyncio.sleep(5)
            return "done"

        # Fill the single slot
        task = asyncio.create_task(limiter.execute(slow_work))
        await asyncio.sleep(0.05)  # Let it acquire

        # Second request should be rejected
        with pytest.raises(LoadGuardRejection):
            await limiter.execute(slow_work)

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, LoadGuardRejection):
            pass

    @pytest.mark.asyncio
    async def test_utilization_metric(self, request_limiter):
        """Utilization is calculated correctly."""
        assert request_limiter.utilization == 0.0

        await request_limiter.acquire()
        assert request_limiter.utilization == pytest.approx(0.2, abs=0.01)  # 1/5

        request_limiter.release()

    def test_metrics_output(self, request_limiter):
        """get_metrics returns expected keys."""
        metrics = request_limiter.get_metrics()
        assert "active_requests" in metrics
        assert "max_concurrent" in metrics
        assert "utilization" in metrics
        assert "total_rejected" in metrics


# ── Agent Execution Limiter Tests ────────────────────────────────

class TestAgentExecutionLimiter:
    """Tests for the AgentExecutionLimiter."""

    @pytest.mark.asyncio
    async def test_allows_within_limit(self, agent_limiter):
        """Agent executions within the limit pass through."""
        async def agent_work():
            return "agent_done"

        result = await agent_limiter.execute(agent_work)
        assert result == "agent_done"

    @pytest.mark.asyncio
    async def test_concurrent_agents(self, agent_limiter):
        """Multiple agents can run concurrently up to the limit."""
        results = []

        async def agent_work(idx):
            await asyncio.sleep(0.01)
            results.append(idx)
            return idx

        tasks = [
            asyncio.create_task(agent_limiter.execute(agent_work, i))
            for i in range(3)
        ]
        await asyncio.gather(*tasks)
        assert len(results) == 3

    @pytest.mark.asyncio
    async def test_rejects_over_limit(self):
        """Agent executions over the limit are rejected."""
        limiter = AgentExecutionLimiter(max_agents=1, queue_timeout=0.1)

        async def slow_agent():
            await asyncio.sleep(5)

        task = asyncio.create_task(limiter.execute(slow_agent))
        await asyncio.sleep(0.05)

        with pytest.raises(LoadGuardRejection):
            await limiter.execute(slow_agent)

        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, LoadGuardRejection):
            pass


# ── Swarm Throttle Tests ─────────────────────────────────────────

class TestSwarmThrottle:
    """Tests for dynamic swarm throttling."""

    def test_no_throttle_under_threshold(self):
        """No throttling when utilization is below threshold."""
        limiter = RequestQueueLimiter(max_concurrent=10)
        throttle = SwarmThrottle(request_limiter=limiter, pressure_threshold=0.7)
        assert throttle.throttle_factor == 1.0

    def test_throttles_above_threshold(self):
        """Throttles when utilization exceeds threshold."""
        limiter = RequestQueueLimiter(max_concurrent=10)
        # Simulate high utilization
        limiter._active = 9  # 90% utilization
        throttle = SwarmThrottle(request_limiter=limiter, pressure_threshold=0.7)
        factor = throttle.throttle_factor
        assert factor < 1.0
        assert factor >= 0.1

    def test_effective_parallelism(self):
        """Effective parallelism is computed correctly."""
        limiter = RequestQueueLimiter(max_concurrent=10)
        limiter._active = 9
        throttle = SwarmThrottle(request_limiter=limiter, pressure_threshold=0.7)

        base = 10
        effective = throttle.get_effective_parallelism(base)
        assert 1 <= effective <= base
        assert effective < base  # Should be throttled

    def test_minimum_parallelism(self):
        """Effective parallelism never drops below 1."""
        limiter = RequestQueueLimiter(max_concurrent=10)
        limiter._active = 10  # 100% utilization
        throttle = SwarmThrottle(request_limiter=limiter, pressure_threshold=0.7)

        effective = throttle.get_effective_parallelism(1)
        assert effective >= 1

    def test_no_limiters(self):
        """Throttle returns 1.0 when no limiters are configured."""
        throttle = SwarmThrottle()
        assert throttle.throttle_factor == 1.0
