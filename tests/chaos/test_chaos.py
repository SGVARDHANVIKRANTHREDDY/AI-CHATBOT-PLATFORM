"""
Chaos Test Suite — Automated failure simulation and recovery verification.

Tests four failure domains:
    1. LLM provider outage / degradation
    2. Vector database downtime / corruption
    3. Plugin crash / timeout
    4. Worker process failure

For each domain, verifies:
    • System fallback activates correctly
    • Retry logic executes the right number of attempts
    • Graceful degradation returns safe responses (no crashes)
    • Recovery works once the fault is removed
"""

from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from unittest.mock import MagicMock

import pytest
from tests.chaos.fault_injectors import (
    LLMProviderFault,
    PluginFault,
    VectorDBFault,
    WorkerFault,
)
from tests.chaos.framework import (
    FaultType,
)

# ── Helpers ───────────────────────────────────────────────────────


class StubLLMProvider:
    """Minimal LLM provider for chaos testing."""

    def __init__(self, response: str = "Hello from LLM"):
        self._response = response
        self.call_count = 0

    async def ask(self, prompt: str, system_prompt: str | None = None, model: str | None = None) -> str:
        self.call_count += 1
        return self._response

    async def ask_stream(self, prompt: str, **kwargs):
        self.call_count += 1
        for word in self._response.split():
            yield word + " "

    async def health_check(self) -> bool:
        return True


class StubVectorStore:
    """Minimal vector store for chaos testing."""

    def __init__(self):
        self._data: dict[str, Any] = {}
        self.call_count = 0

    async def initialize(self):
        pass

    async def add_embedding(self, id: str, embedding: Any, text: str = "", metadata: dict | None = None) -> None:
        self.call_count += 1
        self._data[id] = {"text": text, "embedding": embedding, "metadata": metadata or {}}

    async def search(self, query_embedding: Any, top_k: int = 5, filters: dict | None = None) -> list[dict]:
        self.call_count += 1
        results = []
        for rid, rec in list(self._data.items())[:top_k]:
            results.append({"id": rid, "text": rec["text"], "score": 0.95, "metadata": rec["metadata"]})
        return results

    async def delete(self, ids: list[str]) -> int:
        self.call_count += 1
        removed = 0
        for rid in ids:
            if rid in self._data:
                del self._data[rid]
                removed += 1
        return removed

    async def batch_insert(self, records: list[Any]) -> int:
        self.call_count += 1
        return len(records)


class StubPluginRunner:
    """Minimal plugin runner for chaos testing."""

    def __init__(self):
        self.call_count = 0

    async def run_plugin(self, plugin_module: str, function_name: str, **kwargs):
        self.call_count += 1
        return MagicMock(success=True, result={"value": 42}, error=None)


class StubCeleryTask:
    """Minimal Celery task stub for chaos testing."""

    def __init__(self):
        self.call_count = 0
        self._result = {"status": "success"}

    def delay(self, *args, **kwargs):
        self.call_count += 1
        result = MagicMock()
        result.id = "test-task-id"
        result.get.return_value = self._result
        result.ready.return_value = True
        result.successful.return_value = True
        result.failed.return_value = False
        return result

    def apply_async(self, *args, **kwargs):
        return self.delay(*args, **kwargs)


# ═══════════════════════════════════════════════════════════════════
#  1. LLM PROVIDER CHAOS TESTS
# ═══════════════════════════════════════════════════════════════════


class TestLLMProviderChaos:
    """Verify system behaviour when LLM providers fail."""

    # ── Outage ────────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_llm_outage_raises_connection_error(self):
        """Complete LLM outage causes ConnectionError on ask()."""
        provider = StubLLMProvider()
        fault = LLMProviderFault(provider, FaultType.OUTAGE)

        async with fault.inject() as result:
            with pytest.raises(ConnectionError, match=r"Chaos.*unavailable"):
                await provider.ask("test")
            result.error_type = "ConnectionError"

        assert result.fault_type == FaultType.OUTAGE

    @pytest.mark.asyncio
    async def test_llm_outage_health_check_fails(self):
        """Health check also fails during outage."""
        provider = StubLLMProvider()
        fault = LLMProviderFault(provider, FaultType.OUTAGE)

        async with fault.inject():
            with pytest.raises(ConnectionError):
                await provider.health_check()

    @pytest.mark.asyncio
    async def test_llm_recovers_after_outage(self):
        """Provider works normally after fault is reverted."""
        provider = StubLLMProvider(response="recovered")
        fault = LLMProviderFault(provider, FaultType.OUTAGE)

        async with fault.inject():
            with pytest.raises(ConnectionError):
                await provider.ask("during fault")

        # After fault removed, provider works
        result = await provider.ask("after recovery")
        assert result == "recovered"

    # ── Timeout ───────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_llm_timeout_triggers_asyncio_timeout(self):
        """Hanging LLM is caught by asyncio timeout."""
        provider = StubLLMProvider()
        fault = LLMProviderFault(provider, FaultType.TIMEOUT)

        async with fault.inject():
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(provider.ask("test"), timeout=0.1)

    # ── Intermittent ──────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_llm_intermittent_partial_failures(self):
        """Intermittent faults fail only a fraction of calls."""
        provider = StubLLMProvider(response="ok")
        fault = LLMProviderFault(
            provider,
            FaultType.INTERMITTENT,
            failure_rate=0.5,
        )

        successes, failures = 0, 0
        async with fault.inject():
            for _ in range(50):
                try:
                    await provider.ask("test")
                    successes += 1
                except ConnectionError:
                    failures += 1

        # With 50% rate, expect both successes and failures
        assert successes > 0, "Expected at least some successes"
        assert failures > 0, "Expected at least some failures"

    # ── Corruption ────────────────────────────────────────────────

    @pytest.mark.asyncio
    async def test_llm_corruption_returns_garbage(self):
        """Corrupted LLM returns nonsensical output."""
        provider = StubLLMProvider()
        fault = LLMProviderFault(provider, FaultType.CORRUPTION)

        async with fault.inject():
            result = await provider.ask("normal question")
            assert "GARBAGE" in result
            assert result != "Hello from LLM"

    # ── Circuit breaker integration ───────────────────────────────

    @pytest.mark.asyncio
    async def test_circuit_breaker_trips_on_repeated_llm_failures(self):
        """Circuit breaker transitions CLOSED → OPEN after threshold failures."""
        from app.reliability.circuit_breaker import CircuitBreaker, CircuitState

        provider = StubLLMProvider()
        fallback_called = False

        async def _fallback(*a, **kw):
            nonlocal fallback_called
            fallback_called = True
            return "fallback response"

        cb = CircuitBreaker(
            "llm_chaos_test",
            failure_threshold=3,
            recovery_timeout=60.0,
            fallback=_fallback,
        )

        fault = LLMProviderFault(provider, FaultType.OUTAGE)

        async with fault.inject():
            # Trip the breaker
            for _ in range(3):
                with contextlib.suppress(ConnectionError):
                    await cb.call(provider.ask, "test")

            assert cb.state == CircuitState.OPEN

            # Next call should use fallback
            result = await cb.call(provider.ask, "test")
            assert result == "fallback response"
            assert fallback_called

    @pytest.mark.asyncio
    async def test_circuit_breaker_recovers_after_half_open_success(self):
        """Circuit transitions OPEN → HALF_OPEN → CLOSED on success."""
        from app.reliability.circuit_breaker import CircuitBreaker, CircuitState

        provider = StubLLMProvider(response="ok")
        cb = CircuitBreaker(
            "llm_recovery_test",
            failure_threshold=2,
            recovery_timeout=0.1,  # fast recovery for test
        )

        fault = LLMProviderFault(provider, FaultType.OUTAGE)

        # Trip the breaker
        async with fault.inject():
            for _ in range(2):
                with contextlib.suppress(ConnectionError):
                    await cb.call(provider.ask, "test")

        assert cb.state == CircuitState.OPEN

        # Wait for recovery timeout
        await asyncio.sleep(0.15)
        assert cb.state == CircuitState.HALF_OPEN

        # Successful call closes it
        result = await cb.call(provider.ask, "probe")
        assert result == "ok"
        assert cb.state == CircuitState.CLOSED

    # ── Retry logic integration ───────────────────────────────────

    @pytest.mark.asyncio
    async def test_retry_policy_retries_on_llm_outage(self):
        """RetryPolicy attempts configured retries before exhausting."""
        from app.reliability.retry_policy import RetryExhaustedError, RetryPolicy

        provider = StubLLMProvider()
        retry = RetryPolicy(
            "llm_retry_chaos",
            max_retries=2,
            base_delay=0.01,
            max_delay=0.05,
        )
        fault = LLMProviderFault(provider, FaultType.OUTAGE)

        async with fault.inject():
            with pytest.raises(RetryExhaustedError) as exc_info:
                await retry.execute(provider.ask, "test")

            assert exc_info.value.attempts == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_retry_succeeds_on_intermittent_failure(self):
        """Retry logic recovers from intermittent LLM failures."""
        from app.reliability.retry_policy import RetryPolicy

        provider = StubLLMProvider(response="success")
        call_count = 0
        original_ask = provider.ask

        async def _fail_then_succeed(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise ConnectionError("transient")
            return await original_ask(*args, **kwargs)

        provider.ask = _fail_then_succeed

        retry = RetryPolicy(
            "llm_intermittent_retry",
            max_retries=3,
            base_delay=0.01,
            max_delay=0.05,
        )

        result = await retry.execute(provider.ask, "test")
        assert result == "success"
        assert call_count == 3  # 2 failures + 1 success

    # ── Fallback provider integration ─────────────────────────────

    @pytest.mark.asyncio
    async def test_fallback_provider_switches_on_primary_outage(self):
        """FallbackProvider fails over to secondary when primary is down."""
        from app.llm.providers.fallback_provider import FallbackProvider

        primary = StubLLMProvider(response="primary")
        secondary = StubLLMProvider(response="secondary")

        fallback_provider = FallbackProvider(primary, [secondary])

        fault = LLMProviderFault(primary, FaultType.OUTAGE)

        async with fault.inject():
            result = await fallback_provider.ask("test")
            assert result == "secondary"

    @pytest.mark.asyncio
    async def test_fallback_provider_all_down_raises(self):
        """All providers down raises RuntimeError."""
        from app.llm.providers.fallback_provider import FallbackProvider

        primary = StubLLMProvider()
        secondary = StubLLMProvider()
        fb = FallbackProvider(primary, [secondary])

        fault_primary = LLMProviderFault(primary, FaultType.OUTAGE)
        fault_secondary = LLMProviderFault(secondary, FaultType.OUTAGE)

        async with fault_primary.inject(), fault_secondary.inject():
            with pytest.raises(RuntimeError, match="All LLM providers"):
                await fb.ask("test")

    # ── Graceful degradation ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_llm_outage_graceful_degradation_via_circuit(self):
        """System returns degraded response rather than crashing."""
        from app.reliability.circuit_breaker import CircuitBreaker

        provider = StubLLMProvider()

        async def _degraded(*a, **kw):
            return "Service temporarily unavailable. Please try again shortly."

        cb = CircuitBreaker(
            "degradation_test",
            failure_threshold=2,
            recovery_timeout=60,
            fallback=_degraded,
        )

        fault = LLMProviderFault(provider, FaultType.OUTAGE)
        async with fault.inject():
            for _ in range(2):
                with contextlib.suppress(ConnectionError):
                    await cb.call(provider.ask, "test")

            # Degraded path — no crash
            result = await cb.call(provider.ask, "test")
            assert "temporarily unavailable" in result


# ═══════════════════════════════════════════════════════════════════
#  2. VECTOR DATABASE CHAOS TESTS
# ═══════════════════════════════════════════════════════════════════


class TestVectorDBChaos:
    """Verify system behaviour when vector database is down."""

    @pytest.mark.asyncio
    async def test_vector_outage_search_raises(self):
        """Complete vector DB outage raises ConnectionError on search."""
        store = StubVectorStore()
        fault = VectorDBFault(store, FaultType.OUTAGE)

        async with fault.inject():
            with pytest.raises(ConnectionError, match=r"Chaos.*search"):
                await store.search(query_embedding=[0.1, 0.2])

    @pytest.mark.asyncio
    async def test_vector_outage_write_raises(self):
        """Vector DB outage blocks writes too."""
        store = StubVectorStore()
        fault = VectorDBFault(store, FaultType.OUTAGE)

        async with fault.inject():
            with pytest.raises(ConnectionError, match=r"Chaos.*add_embedding"):
                await store.add_embedding(id="x", embedding=[0.1], text="test")

    @pytest.mark.asyncio
    async def test_vector_recovers_after_outage(self):
        """Vector store works normally after fault is removed."""
        store = StubVectorStore()
        fault = VectorDBFault(store, FaultType.OUTAGE)

        async with fault.inject():
            with pytest.raises(ConnectionError):
                await store.search(query_embedding=[0.1])

        # Post-recovery: operations succeed
        await store.add_embedding(id="r1", embedding=[0.1], text="recovered")
        results = await store.search(query_embedding=[0.1])
        assert len(results) == 1
        assert results[0]["text"] == "recovered"

    @pytest.mark.asyncio
    async def test_vector_timeout_caught_by_asyncio(self):
        """Hanging vector DB is caught by timeout."""
        store = StubVectorStore()
        fault = VectorDBFault(store, FaultType.TIMEOUT)

        async with fault.inject():
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(store.search(query_embedding=[0.1]), timeout=0.1)

    @pytest.mark.asyncio
    async def test_vector_corruption_returns_empty_results(self):
        """Corrupted vector DB returns empty search results."""
        store = StubVectorStore()
        # Seed some data
        await store.add_embedding(id="d1", embedding=[0.1], text="real data")

        fault = VectorDBFault(store, FaultType.CORRUPTION)
        async with fault.inject():
            results = await store.search(query_embedding=[0.1])
            assert results == []

    @pytest.mark.asyncio
    async def test_vector_intermittent_partial_failures(self):
        """Flaky vector DB fails some operations."""
        store = StubVectorStore()
        fault = VectorDBFault(store, FaultType.INTERMITTENT, failure_rate=0.5)

        successes, failures = 0, 0
        async with fault.inject():
            for _ in range(40):
                try:
                    await store.search(query_embedding=[0.1])
                    successes += 1
                except ConnectionError:
                    failures += 1

        assert successes > 0
        assert failures > 0

    # ── Circuit breaker for vector DB ─────────────────────────────

    @pytest.mark.asyncio
    async def test_circuit_breaker_trips_on_vector_outage(self):
        """Circuit breaker protects against sustained vector DB downtime."""
        from app.reliability.circuit_breaker import CircuitBreaker, CircuitState

        store = StubVectorStore()
        cb = CircuitBreaker("vector_chaos", failure_threshold=3, recovery_timeout=60)

        fault = VectorDBFault(store, FaultType.OUTAGE)
        async with fault.inject():
            for _ in range(3):
                with contextlib.suppress(ConnectionError):
                    await cb.call(store.search, query_embedding=[0.1])

            assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_vector_retry_then_success(self):
        """Retry policy recovers from transient vector DB failure."""
        from app.reliability.retry_policy import RetryPolicy

        store = StubVectorStore()
        call_count = 0
        original_search = store.search

        async def _flaky_search(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise ConnectionError("transient vector error")
            return await original_search(*args, **kwargs)

        store.search = _flaky_search

        retry = RetryPolicy(
            "vector_retry_chaos",
            max_retries=2,
            base_delay=0.01,
            max_delay=0.05,
        )

        await retry.execute(store.search, query_embedding=[0.1])
        assert call_count == 2

    # ── Graceful degradation — empty results rather than crash ────

    @pytest.mark.asyncio
    async def test_vector_fallback_returns_empty_on_outage(self):
        """System degrades to empty context when vector DB is down."""
        from app.reliability.circuit_breaker import CircuitBreaker

        store = StubVectorStore()

        async def _empty_fallback(*a, **kw):
            return []

        cb = CircuitBreaker(
            "vector_degrade",
            failure_threshold=2,
            recovery_timeout=60,
            fallback=_empty_fallback,
        )

        fault = VectorDBFault(store, FaultType.OUTAGE)
        async with fault.inject():
            for _ in range(2):
                with contextlib.suppress(ConnectionError):
                    await cb.call(store.search, query_embedding=[0.1])

            result = await cb.call(store.search, query_embedding=[0.1])
            assert result == []


# ═══════════════════════════════════════════════════════════════════
#  3. PLUGIN CRASH CHAOS TESTS
# ═══════════════════════════════════════════════════════════════════


class TestPluginChaos:
    """Verify system behaviour when plugins crash or hang."""

    @pytest.mark.asyncio
    async def test_plugin_crash_raises_runtime_error(self):
        """Plugin crash raises RuntimeError."""
        runner = StubPluginRunner()
        fault = PluginFault(runner, FaultType.CRASH)

        async with fault.inject():
            with pytest.raises(RuntimeError, match=r"Chaos.*SIGSEGV"):
                await runner.run_plugin("mod", "func")

    @pytest.mark.asyncio
    async def test_plugin_recovers_after_crash(self):
        """Plugin runner works after crash fault is removed."""
        runner = StubPluginRunner()
        fault = PluginFault(runner, FaultType.CRASH)

        async with fault.inject():
            with pytest.raises(RuntimeError):
                await runner.run_plugin("mod", "func")

        result = await runner.run_plugin("mod", "func")
        assert result.success is True

    @pytest.mark.asyncio
    async def test_plugin_timeout_caught(self):
        """Hanging plugin is caught by asyncio timeout."""
        runner = StubPluginRunner()
        fault = PluginFault(runner, FaultType.TIMEOUT)

        async with fault.inject():
            with pytest.raises(asyncio.TimeoutError):
                await asyncio.wait_for(
                    runner.run_plugin("mod", "func"),
                    timeout=0.1,
                )

    @pytest.mark.asyncio
    async def test_plugin_corruption_returns_error_result(self):
        """Corrupted plugin returns PluginRunResult with error."""
        runner = StubPluginRunner()
        fault = PluginFault(runner, FaultType.CORRUPTION)

        async with fault.inject():
            result = await runner.run_plugin("mod", "func")
            assert result.success is False
            assert "corrupted" in result.error.lower()

    # ── Circuit breaker for plugin execution ──────────────────────

    @pytest.mark.asyncio
    async def test_circuit_breaker_trips_on_plugin_crashes(self):
        """Circuit breaker opens after repeated plugin crashes."""
        from app.reliability.circuit_breaker import CircuitBreaker, CircuitState

        runner = StubPluginRunner()
        cb = CircuitBreaker("plugin_chaos", failure_threshold=3, recovery_timeout=60)

        fault = PluginFault(runner, FaultType.CRASH)
        async with fault.inject():
            for _ in range(3):
                with contextlib.suppress(RuntimeError):
                    await cb.call(runner.run_plugin, "mod", "func")

            assert cb.state == CircuitState.OPEN

    @pytest.mark.asyncio
    async def test_plugin_retry_succeeds_after_transient_crash(self):
        """Retry policy recovers from transient plugin crash."""
        from app.reliability.retry_policy import RetryPolicy

        runner = StubPluginRunner()
        call_count = 0
        original_run = runner.run_plugin

        async def _flaky_run(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                raise RuntimeError("transient crash")
            return await original_run(*args, **kwargs)

        runner.run_plugin = _flaky_run

        retry = RetryPolicy(
            "plugin_retry_chaos",
            max_retries=2,
            base_delay=0.01,
            max_delay=0.05,
            retryable_exceptions=(RuntimeError, ConnectionError, TimeoutError),
        )

        result = await retry.execute(runner.run_plugin, "mod", "func")
        assert result.success is True
        assert call_count == 2

    # ── Graceful degradation ──────────────────────────────────────

    @pytest.mark.asyncio
    async def test_plugin_crash_graceful_degradation(self):
        """System returns a safe fallback when plugin circuit is open."""
        from app.reliability.circuit_breaker import CircuitBreaker

        runner = StubPluginRunner()

        async def _plugin_fallback(*a, **kw):
            return MagicMock(success=False, result=None, error="Plugin unavailable")

        cb = CircuitBreaker(
            "plugin_degrade",
            failure_threshold=2,
            recovery_timeout=60,
            fallback=_plugin_fallback,
        )

        fault = PluginFault(runner, FaultType.CRASH)
        async with fault.inject():
            for _ in range(2):
                with contextlib.suppress(RuntimeError):
                    await cb.call(runner.run_plugin, "mod", "func")

            result = await cb.call(runner.run_plugin, "mod", "func")
            assert result.success is False
            assert "unavailable" in result.error.lower()


# ═══════════════════════════════════════════════════════════════════
#  4. WORKER PROCESS CHAOS TESTS
# ═══════════════════════════════════════════════════════════════════


class TestWorkerChaos:
    """Verify system behaviour when Celery workers fail."""

    @pytest.mark.asyncio
    async def test_worker_crash_raises_on_dispatch(self):
        """Worker crash raises ConnectionError when dispatching task."""
        task = StubCeleryTask()
        fault = WorkerFault(task, FaultType.CRASH)

        async with fault.inject():
            with pytest.raises(ConnectionError, match=r"Chaos.*died"):
                task.delay()

    @pytest.mark.asyncio
    async def test_worker_outage_broker_unreachable(self):
        """Broker outage raises ConnectionError."""
        task = StubCeleryTask()
        fault = WorkerFault(task, FaultType.OUTAGE)

        async with fault.inject():
            with pytest.raises(ConnectionError):
                task.apply_async(args=[])

    @pytest.mark.asyncio
    async def test_worker_timeout_result_never_resolves(self):
        """Hung worker — result.get() times out."""
        task = StubCeleryTask()
        fault = WorkerFault(task, FaultType.TIMEOUT)

        async with fault.inject():
            result = task.delay()
            assert result.ready() is False
            with pytest.raises(TimeoutError, match=r"Chaos.*timed out"):
                result.get(timeout=1)

    @pytest.mark.asyncio
    async def test_worker_recovers_after_crash(self):
        """Worker dispatches normally after fault is removed."""
        task = StubCeleryTask()
        fault = WorkerFault(task, FaultType.CRASH)

        async with fault.inject():
            with pytest.raises(ConnectionError):
                task.delay()

        result = task.delay()
        assert result.successful() is True

    @pytest.mark.asyncio
    async def test_worker_graceful_degradation_catches_dispatch_error(self):
        """System handles worker dispatch failure gracefully."""
        task = StubCeleryTask()
        fault = WorkerFault(task, FaultType.CRASH)

        async with fault.inject():
            # Application code should wrap dispatch in try/except
            dispatched = False
            error_msg = None
            try:
                task.delay()
                dispatched = True
            except (ConnectionError, OSError) as e:
                error_msg = str(e)

            assert not dispatched
            assert error_msg is not None
            assert "Chaos" in error_msg


# ═══════════════════════════════════════════════════════════════════
#  5. CROSS-COMPONENT CHAOS TESTS
# ═══════════════════════════════════════════════════════════════════


class TestCrossComponentChaos:
    """Multi-fault scenarios: verify system survives compound failures."""

    @pytest.mark.asyncio
    async def test_llm_and_vector_simultaneous_outage(self):
        """System handles both LLM and vector DB failing at once."""
        from app.reliability.circuit_breaker import CircuitBreaker, CircuitState

        provider = StubLLMProvider()
        store = StubVectorStore()

        async def _llm_fallback(*a, **kw):
            return "LLM unavailable"

        async def _vector_fallback(*a, **kw):
            return []

        llm_cb = CircuitBreaker("llm_multi", failure_threshold=2, recovery_timeout=60, fallback=_llm_fallback)
        vec_cb = CircuitBreaker("vec_multi", failure_threshold=2, recovery_timeout=60, fallback=_vector_fallback)

        llm_fault = LLMProviderFault(provider, FaultType.OUTAGE)
        vec_fault = VectorDBFault(store, FaultType.OUTAGE)

        async with llm_fault.inject(), vec_fault.inject():
            # Trip both breakers
            for _ in range(2):
                with contextlib.suppress(ConnectionError):
                    await llm_cb.call(provider.ask, "test")
                with contextlib.suppress(ConnectionError):
                    await vec_cb.call(store.search, query_embedding=[0.1])

            assert llm_cb.state == CircuitState.OPEN
            assert vec_cb.state == CircuitState.OPEN

            # Both fallbacks fire — no crash
            llm_result = await llm_cb.call(provider.ask, "test")
            vec_result = await vec_cb.call(store.search, query_embedding=[0.1])

            assert llm_result == "LLM unavailable"
            assert vec_result == []

    @pytest.mark.asyncio
    async def test_cascading_failure_llm_then_plugin(self):
        """LLM outage followed by plugin crash — system stays alive."""
        from app.reliability.circuit_breaker import CircuitBreaker, CircuitState

        provider = StubLLMProvider()
        runner = StubPluginRunner()

        async def _llm_fb(*a, **kw):
            return "degraded"

        async def _plugin_fb(*a, **kw):
            return MagicMock(success=False, error="plugin down")

        llm_cb = CircuitBreaker("llm_cascade", failure_threshold=2, recovery_timeout=60, fallback=_llm_fb)
        plugin_cb = CircuitBreaker("plugin_cascade", failure_threshold=2, recovery_timeout=60, fallback=_plugin_fb)

        # Phase 1: LLM goes down
        llm_fault = LLMProviderFault(provider, FaultType.OUTAGE)
        async with llm_fault.inject():
            for _ in range(2):
                with contextlib.suppress(ConnectionError):
                    await llm_cb.call(provider.ask, "test")

            assert llm_cb.state == CircuitState.OPEN

            # Phase 2: Plugin also crashes
            plugin_fault = PluginFault(runner, FaultType.CRASH)
            async with plugin_fault.inject():
                for _ in range(2):
                    with contextlib.suppress(RuntimeError):
                        await plugin_cb.call(runner.run_plugin, "mod", "func")

                assert plugin_cb.state == CircuitState.OPEN

                # Both fallbacks — system alive
                assert (await llm_cb.call(provider.ask, "t")) == "degraded"
                r = await plugin_cb.call(runner.run_plugin, "mod", "func")
                assert r.success is False

    @pytest.mark.asyncio
    async def test_recovery_sequence_after_compound_failure(self):
        """Components recover independently after compound failure ends."""
        from app.reliability.circuit_breaker import CircuitBreaker, CircuitState

        provider = StubLLMProvider(response="recovered llm")
        store = StubVectorStore()

        llm_cb = CircuitBreaker("llm_seq", failure_threshold=2, recovery_timeout=0.1)
        vec_cb = CircuitBreaker("vec_seq", failure_threshold=2, recovery_timeout=0.1)

        llm_fault = LLMProviderFault(provider, FaultType.OUTAGE)
        vec_fault = VectorDBFault(store, FaultType.OUTAGE)

        async with llm_fault.inject(), vec_fault.inject():
            for _ in range(2):
                with contextlib.suppress(ConnectionError):
                    await llm_cb.call(provider.ask, "test")
                with contextlib.suppress(ConnectionError):
                    await vec_cb.call(store.search, query_embedding=[0.1])

        # Both faults cleared — wait for half-open
        await asyncio.sleep(0.15)

        assert llm_cb.state == CircuitState.HALF_OPEN
        assert vec_cb.state == CircuitState.HALF_OPEN

        # Successful probes close the circuits
        r1 = await llm_cb.call(provider.ask, "probe")
        assert r1 == "recovered llm"
        assert llm_cb.state == CircuitState.CLOSED

        await store.add_embedding(id="p1", embedding=[0.1], text="probe data")
        await vec_cb.call(store.search, query_embedding=[0.1])
        assert vec_cb.state == CircuitState.CLOSED


# ═══════════════════════════════════════════════════════════════════
#  6. TIMEOUT CONTROLLER CHAOS TESTS
# ═══════════════════════════════════════════════════════════════════


class TestTimeoutControllerChaos:
    """Verify TimeoutController catches hung operations."""

    @pytest.mark.asyncio
    async def test_timeout_controller_catches_hanging_llm(self):
        """TimeoutController kills a hanging LLM call."""
        from app.reliability.timeout_controller import TimeoutController
        from app.reliability.timeout_controller import TimeoutError as TOError

        provider = StubLLMProvider()
        tc = TimeoutController("llm_timeout_chaos", timeout_seconds=0.1)
        fault = LLMProviderFault(provider, FaultType.TIMEOUT)

        async with fault.inject():
            with pytest.raises((TOError, asyncio.TimeoutError)):
                await tc.execute(provider.ask, "test")

    @pytest.mark.asyncio
    async def test_timeout_controller_passes_fast_calls(self):
        """TimeoutController lets fast calls through normally."""
        from app.reliability.timeout_controller import TimeoutController

        provider = StubLLMProvider(response="fast")
        tc = TimeoutController("llm_fast_chaos", timeout_seconds=5.0)

        result = await tc.execute(provider.ask, "test")
        assert result == "fast"


# ═══════════════════════════════════════════════════════════════════
#  7. LOAD GUARD CHAOS TESTS
# ═══════════════════════════════════════════════════════════════════


class TestLoadGuardChaos:
    """Verify load guards reject under resource exhaustion."""

    @pytest.mark.asyncio
    async def test_agent_limiter_rejects_when_full(self):
        """AgentExecutionLimiter rejects when at capacity."""
        from app.reliability.load_guard import AgentExecutionLimiter, LoadGuardRejectionError

        limiter = AgentExecutionLimiter(max_agents=2, queue_timeout=0.05)

        async def _slow_agent():
            await asyncio.sleep(2)
            return "done"

        # Fill both slots
        tasks = [asyncio.create_task(limiter.execute(_slow_agent)) for _ in range(2)]
        await asyncio.sleep(0.05)  # Let them acquire

        # Third should be rejected
        with pytest.raises(LoadGuardRejectionError):
            await limiter.execute(_slow_agent)

        # Cancel background tasks
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_request_queue_rejects_overflow(self):
        """RequestQueueLimiter rejects when queue is full."""
        from app.reliability.load_guard import LoadGuardRejectionError, RequestQueueLimiter

        limiter = RequestQueueLimiter(max_concurrent=1, queue_timeout=0.05)

        async def _slow():
            await asyncio.sleep(2)
            return "ok"

        # Fill the single slot
        task = asyncio.create_task(limiter.execute(_slow))
        await asyncio.sleep(0.05)

        # Second request should be rejected
        with pytest.raises(LoadGuardRejectionError):
            await limiter.execute(_slow)

        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


# ═══════════════════════════════════════════════════════════════════
#  8. FAULT INJECTOR LIFECYCLE TESTS
# ═══════════════════════════════════════════════════════════════════


class TestFaultInjectorLifecycle:
    """Verify the chaos framework itself behaves correctly."""

    @pytest.mark.asyncio
    async def test_inject_context_manager_reverts_on_exception(self):
        """Fault is reverted even when the test body raises."""
        provider = StubLLMProvider(response="normal")
        fault = LLMProviderFault(provider, FaultType.OUTAGE)

        with pytest.raises(ValueError):
            async with fault.inject():
                raise ValueError("test error")

        # Should be reverted
        result = await provider.ask("after exception")
        assert result == "normal"

    @pytest.mark.asyncio
    async def test_chaos_result_tracks_timing(self):
        """ChaosResult records injection and resolution timestamps."""
        provider = StubLLMProvider()
        fault = LLMProviderFault(provider, FaultType.OUTAGE)

        async with fault.inject() as result:
            await asyncio.sleep(0.05)

        assert result.injected_at > 0
        assert result.resolved_at > result.injected_at
        assert result.recovery_time_ms >= 40  # at least ~50ms

    @pytest.mark.asyncio
    async def test_multiple_sequential_faults(self):
        """Multiple sequential fault injections and recoveries work."""
        provider = StubLLMProvider(response="ok")

        for fault_type in [FaultType.OUTAGE, FaultType.CORRUPTION, FaultType.TIMEOUT]:
            fault = LLMProviderFault(provider, fault_type)
            async with fault.inject():
                pass  # Just verify it injects and reverts cleanly

            # Provider works after each revert
            result = await provider.ask("test")
            assert result == "ok"
