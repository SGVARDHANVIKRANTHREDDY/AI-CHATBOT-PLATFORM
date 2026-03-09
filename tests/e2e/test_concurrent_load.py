"""
E2E Test: Concurrent Load Simulation

Simulates 100 concurrent requests through the load guard,
validates graceful degradation, and measures throughput.
"""

from __future__ import annotations

import asyncio
import contextlib
import time

import pytest
from app.reliability.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from app.reliability.failure_tracker import FailureTracker
from app.reliability.load_guard import (
    AgentExecutionLimiter,
    LoadGuardRejectionError,
    RequestQueueLimiter,
    SwarmThrottle,
)

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def load_system():
    """Create a full load protection system."""
    return {
        "request_limiter": RequestQueueLimiter(max_concurrent=20, queue_timeout=2.0),
        "agent_limiter": AgentExecutionLimiter(max_agents=10, queue_timeout=2.0),
        "circuit_breaker": CircuitBreaker("load_test_llm", failure_threshold=10, recovery_timeout=5.0),
    }


# ── Concurrent Load Tests ────────────────────────────────────────


class TestConcurrentLoad:
    """Tests simulating heavy concurrent load on the system."""

    @pytest.mark.asyncio
    async def test_50_concurrent_requests(self, load_system):
        """50 concurrent requests with 20 slots — some should queue, all complete."""
        limiter = load_system["request_limiter"]
        results: list[str] = []
        errors: list[str] = []

        async def simulate_request(idx: int):
            try:

                async def work():
                    await asyncio.sleep(0.05)  # Simulate fast work
                    return f"result_{idx}"

                result = await limiter.execute(work)
                results.append(result)
            except LoadGuardRejectionError:
                errors.append(f"rejected_{idx}")

        tasks = [asyncio.create_task(simulate_request(i)) for i in range(50)]
        await asyncio.gather(*tasks)

        # Most should succeed (20 concurrent with quick 50ms work)
        assert len(results) >= 20
        total = len(results) + len(errors)
        assert total == 50

    @pytest.mark.asyncio
    async def test_100_concurrent_requests_graceful_degradation(self):
        """100 concurrent requests with tight limits — system degrades gracefully."""
        limiter = RequestQueueLimiter(max_concurrent=5, queue_timeout=0.2)
        succeeded = 0
        rejected = 0

        async def simulate_request(idx: int):
            nonlocal succeeded, rejected
            try:

                async def work():
                    await asyncio.sleep(0.15)  # 150ms work per request
                    return "ok"

                await limiter.execute(work)
                succeeded += 1
            except LoadGuardRejectionError:
                rejected += 1

        tasks = [asyncio.create_task(simulate_request(i)) for i in range(100)]
        await asyncio.gather(*tasks)

        # System should handle the load without crashing
        total = succeeded + rejected
        assert total == 100
        # Some requests should succeed
        assert succeeded > 0
        # Under tight limits, some should be rejected
        assert rejected > 0

    @pytest.mark.asyncio
    async def test_agent_limiter_under_load(self):
        """Agent limiter correctly bounds concurrent agent executions."""
        limiter = AgentExecutionLimiter(max_agents=5, queue_timeout=1.0)
        max_concurrent_seen = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def simulate_agent(idx: int):
            nonlocal max_concurrent_seen, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent_seen = max(max_concurrent_seen, current_concurrent)

            await asyncio.sleep(0.05)

            async with lock:
                current_concurrent -= 1

        tasks = []
        for i in range(20):
            tasks.append(asyncio.create_task(limiter.execute(simulate_agent, i)))

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Max concurrent should not exceed the limit
        assert max_concurrent_seen <= 5
        # Some tasks may have been rejected
        succeeded = sum(1 for r in results if not isinstance(r, Exception))
        assert succeeded > 0

    @pytest.mark.asyncio
    async def test_circuit_breaker_under_provider_outage(self):
        """Circuit breaker opens during simulated provider outage."""
        tracker = FailureTracker("outage_test")
        cb = CircuitBreaker(
            "outage_provider",
            failure_threshold=5,
            recovery_timeout=2.0,
            tracker=tracker,
        )

        call_count = 0
        blocked_count = 0

        async def flaky_provider():
            nonlocal call_count
            call_count += 1
            raise ConnectionError("provider down")

        # Phase 1: Calls fail until circuit opens
        for _i in range(10):
            try:
                await cb.call(flaky_provider)
            except ConnectionError:
                pass
            except CircuitOpenError:
                blocked_count += 1

        # Circuit should be open now
        assert cb.state == CircuitState.OPEN
        # Some calls were blocked by the breaker (didn't reach the provider)
        assert blocked_count > 0
        # Not all calls reached the provider (circuit opened before attempt 10)
        assert call_count < 10

    @pytest.mark.asyncio
    async def test_swarm_throttle_under_pressure(self):
        """Swarm throttle reduces parallelism under load pressure."""
        req_limiter = RequestQueueLimiter(max_concurrent=10)
        agent_limiter = AgentExecutionLimiter(max_agents=10)
        throttle = SwarmThrottle(
            request_limiter=req_limiter,
            agent_limiter=agent_limiter,
            pressure_threshold=0.5,
        )

        # Low pressure
        assert throttle.get_effective_parallelism(10) == 10

        # Simulate increasing pressure
        req_limiter._active = 7  # 70% utilization
        effective_70 = throttle.get_effective_parallelism(10)
        assert effective_70 < 10

        req_limiter._active = 9  # 90% utilization
        effective_90 = throttle.get_effective_parallelism(10)
        assert effective_90 < effective_70

        # Maximum pressure
        req_limiter._active = 10  # 100% utilization
        effective_100 = throttle.get_effective_parallelism(10)
        assert effective_100 >= 1  # Never drops to 0

    @pytest.mark.asyncio
    async def test_combined_limiter_throughput(self):
        """Measure throughput through combined request + agent limiters."""
        req_limiter = RequestQueueLimiter(max_concurrent=20, queue_timeout=5.0)
        agent_limiter = AgentExecutionLimiter(max_agents=10, queue_timeout=5.0)

        completed = 0
        start = time.perf_counter()

        async def full_request(idx: int):
            nonlocal completed

            async def agent_work():
                await asyncio.sleep(0.01)  # 10ms agent work
                return "done"

            async def request_work():
                return await agent_limiter.execute(agent_work)

            result = await req_limiter.execute(request_work)
            completed += 1
            return result

        tasks = [asyncio.create_task(full_request(i)) for i in range(50)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = time.perf_counter() - start
        succeeded = sum(1 for r in results if not isinstance(r, Exception))

        # Should complete within a reasonable time
        assert elapsed < 30.0
        # Most requests should succeed
        assert succeeded >= 40

    @pytest.mark.asyncio
    async def test_request_release_after_exception(self, load_system):
        """Slots are released even when the task raises an exception."""
        limiter = load_system["request_limiter"]

        async def failing_work():
            raise ValueError("task failed")

        # Fill and release slots via exceptions
        for _ in range(25):
            with contextlib.suppress(ValueError):
                await limiter.execute(failing_work)

        # All slots should be available again
        assert limiter.active_requests == 0

    @pytest.mark.asyncio
    async def test_graceful_degradation_response_time(self):
        """Under heavy load, requests are either served or rejected quickly."""
        limiter = RequestQueueLimiter(max_concurrent=5, queue_timeout=0.5)
        response_times: list[float] = []

        async def timed_request(idx: int):
            start = time.perf_counter()
            try:

                async def work():
                    await asyncio.sleep(0.2)
                    return "ok"

                await limiter.execute(work)
            except LoadGuardRejectionError:
                pass
            finally:
                elapsed = time.perf_counter() - start
                response_times.append(elapsed)

        tasks = [asyncio.create_task(timed_request(i)) for i in range(30)]
        await asyncio.gather(*tasks)

        # Rejected requests should be fast (< queue_timeout + small buffer)
        for rt in response_times:
            assert rt < 3.0  # No request should hang for more than 3s
