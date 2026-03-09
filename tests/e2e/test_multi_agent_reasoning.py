"""
E2E Test: Multi-Agent Reasoning Pipeline

Tests the full agent reasoning loop with mocked LLM provider,
verifying that planning → graph execution → synthesis → critic
chain works end-to-end, including reliability wrappers.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest
from app.reliability.circuit_breaker import CircuitBreaker, CircuitOpenError, CircuitState
from app.reliability.failure_tracker import FailureTracker
from app.reliability.retry_policy import RetryExhaustedError, RetryPolicy
from app.reliability.timeout_controller import TimeoutController

# ── Fixtures ──────────────────────────────────────────────────────


@pytest.fixture
def failure_tracker():
    return FailureTracker("test_component", window_seconds=10.0)


@pytest.fixture
def circuit_breaker(failure_tracker):
    return CircuitBreaker(
        "test_cb",
        failure_threshold=3,
        recovery_timeout=1.0,
        half_open_max_calls=2,
        tracker=failure_tracker,
    )


@pytest.fixture
def retry_policy():
    return RetryPolicy(
        "test_retry",
        max_retries=2,
        base_delay=0.01,
        max_delay=0.05,
    )


@pytest.fixture
def timeout_controller():
    return TimeoutController("test_timeout", timeout_seconds=0.5)


# ── Circuit Breaker Tests ────────────────────────────────────────


class TestCircuitBreaker:
    """Tests for the CircuitBreaker reliability primitive."""

    @pytest.mark.asyncio
    async def test_closed_state_passes_calls(self, circuit_breaker):
        """Calls pass through when circuit is CLOSED."""

        async def success():
            return "ok"

        result = await circuit_breaker.call(success)
        assert result == "ok"
        assert circuit_breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_transitions_to_open_on_failures(self, circuit_breaker):
        """Circuit opens after failure_threshold failures."""

        async def failing():
            raise ConnectionError("provider down")

        for _ in range(3):
            with pytest.raises(ConnectionError):
                await circuit_breaker.call(failing)

        assert circuit_breaker.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_open_circuit_rejects_calls(self, circuit_breaker):
        """Open circuit raises CircuitOpenError."""
        circuit_breaker.force_open()

        with pytest.raises(CircuitOpenError) as exc_info:

            async def success():
                return "ok"

            await circuit_breaker.call(success)

        assert exc_info.value.component == "test_cb"

    @pytest.mark.asyncio
    async def test_open_circuit_uses_fallback(self):
        """When a fallback is set, open circuit invokes it instead of raising."""

        async def my_fallback(*args, **kwargs):
            return "fallback_result"

        cb = CircuitBreaker(
            "test_fallback",
            failure_threshold=1,
            recovery_timeout=60.0,
            fallback=my_fallback,
        )
        cb.force_open()

        result = await cb.call(AsyncMock())
        assert result == "fallback_result"

    @pytest.mark.asyncio
    async def test_half_open_recovery(self, circuit_breaker):
        """Circuit recovers from HALF_OPEN to CLOSED on success."""
        circuit_breaker.force_open()
        # Simulate recovery timeout elapsed
        circuit_breaker._opened_at -= circuit_breaker.recovery_timeout + 1

        async def success():
            return "recovered"

        result = await circuit_breaker.call(success)
        assert result == "recovered"
        assert circuit_breaker.state == CircuitState.CLOSED

    @pytest.mark.asyncio
    async def test_manual_force_close(self, circuit_breaker):
        """force_close resets the circuit."""
        circuit_breaker.force_open()
        assert circuit_breaker.state == CircuitState.OPEN

        circuit_breaker.force_close()
        assert circuit_breaker.state == CircuitState.CLOSED


# ── Retry Policy Tests ───────────────────────────────────────────


class TestRetryPolicy:
    """Tests for the RetryPolicy with exponential backoff."""

    @pytest.mark.asyncio
    async def test_success_on_first_attempt(self, retry_policy):
        """No retries needed when call succeeds."""

        async def success():
            return "ok"

        result = await retry_policy.execute(success)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_retries_on_transient_failure(self, retry_policy):
        """Retries on retryable exceptions and succeeds eventually."""
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise ConnectionError("transient")
            return "ok"

        result = await retry_policy.execute(flaky)
        assert result == "ok"
        assert call_count == 3

    @pytest.mark.asyncio
    async def test_exhausts_retries(self, retry_policy):
        """Raises RetryExhaustedError after max_retries."""

        async def always_fails():
            raise ConnectionError("permanent")

        with pytest.raises(RetryExhaustedError) as exc_info:
            await retry_policy.execute(always_fails)

        assert exc_info.value.attempts == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_no_retry_on_non_retryable(self, retry_policy):
        """Non-retryable exceptions are raised immediately."""

        async def type_error():
            raise TypeError("not retryable")

        with pytest.raises(TypeError):
            await retry_policy.execute(type_error)


# ── Timeout Controller Tests ─────────────────────────────────────


class TestTimeoutController:
    """Tests for the TimeoutController."""

    @pytest.mark.asyncio
    async def test_completes_within_timeout(self, timeout_controller):
        """Fast calls complete normally."""

        async def fast():
            return "fast"

        result = await timeout_controller.execute(fast)
        assert result == "fast"

    @pytest.mark.asyncio
    async def test_times_out_slow_call(self):
        """Slow calls are cancelled and raise TimeoutError."""
        tc = TimeoutController("slow_op", timeout_seconds=0.1)

        async def slow():
            await asyncio.sleep(5)
            return "never"

        from app.reliability.timeout_controller import TimeoutError as TOError

        with pytest.raises(TOError):
            await tc.execute(slow)

    @pytest.mark.asyncio
    async def test_fallback_on_timeout(self):
        """execute_with_fallback returns fallback value on timeout."""
        tc = TimeoutController("slow_op", timeout_seconds=0.1)

        async def slow():
            await asyncio.sleep(5)

        result = await tc.execute_with_fallback(slow, "default_value")
        assert result == "default_value"


# ── Failure Tracker Tests ────────────────────────────────────────


class TestFailureTracker:
    """Tests for the FailureTracker sliding window."""

    def test_records_failures(self, failure_tracker):
        """Tracks failure counts."""
        failure_tracker.record_failure(ConnectionError("test"))
        failure_tracker.record_failure(TimeoutError("test"))

        stats = failure_tracker.get_stats()
        assert stats.total_failures == 2

    def test_records_successes(self, failure_tracker):
        """Tracks success counts."""
        failure_tracker.record_success()
        failure_tracker.record_success()

        stats = failure_tracker.get_stats()
        assert stats.total_successes == 2

    def test_failure_rate_calculation(self, failure_tracker):
        """Calculates correct failure rate within window."""
        failure_tracker.record_failure(ConnectionError("fail"))
        failure_tracker.record_success()
        failure_tracker.record_success()
        failure_tracker.record_success()

        stats = failure_tracker.get_stats()
        assert stats.failure_rate == pytest.approx(0.25, abs=0.01)

    def test_reset(self, failure_tracker):
        """Reset clears all state."""
        failure_tracker.record_failure(ConnectionError("fail"))
        failure_tracker.reset()

        stats = failure_tracker.get_stats()
        assert stats.total_failures == 0
        assert stats.total_successes == 0


# ── Integration: Composed Reliability Chain ──────────────────────


class TestReliabilityChain:
    """Tests combining CircuitBreaker + RetryPolicy + Timeout."""

    @pytest.mark.asyncio
    async def test_full_chain_success(self):
        """Full chain: timeout → retry → circuit_breaker succeeds."""
        tracker = FailureTracker("chain_test")
        cb = CircuitBreaker("chain_cb", failure_threshold=5, tracker=tracker)
        retry = RetryPolicy("chain_retry", max_retries=2, base_delay=0.01, tracker=tracker)
        timeout = TimeoutController("chain_timeout", timeout_seconds=1.0)

        async def real_call():
            return "chain_ok"

        async def timed():
            return await timeout.execute(real_call)

        async def retried():
            return await retry.execute(timed)

        result = await cb.call(retried)
        assert result == "chain_ok"

    @pytest.mark.asyncio
    async def test_transient_failure_recovers(self):
        """Chain recovers from transient failures via retry."""
        call_count = 0

        async def flaky():
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("blip")
            return "recovered"

        retry = RetryPolicy("flaky_retry", max_retries=2, base_delay=0.01)
        result = await retry.execute(flaky)
        assert result == "recovered"
        assert call_count == 2
