"""
Tests — Agent Watchdog and Execution Context.

Simulates:
    1. Infinite agent iteration loops
    2. Runaway tool calls
    3. Slow LLM responses (wall-clock timeout)
    4. Graceful cancellation with partial result return
    5. Normal execution within budget
"""

from __future__ import annotations

import asyncio

import pytest
from app.orchestrator.watchdog import (
    AgentBudgetExceededError,
    AgentExecutionContext,
    AgentWatchdog,
    TerminationReason,
)

# ═════════════════════════════════════════════════════════════════
# AgentExecutionContext — unit tests
# ═════════════════════════════════════════════════════════════════


class TestAgentExecutionContext:
    def test_fresh_context_within_budget(self):
        ctx = AgentExecutionContext(
            execution_id="t1",
            max_iterations=10,
            max_tool_calls=20,
            max_runtime_seconds=30,
        )
        ctx.check()  # should not raise

    def test_iteration_limit_raises(self):
        ctx = AgentExecutionContext(execution_id="t2", max_iterations=3)
        for _ in range(3):
            ctx.record_iteration()
        with pytest.raises(AgentBudgetExceededError) as exc_info:
            ctx.check()
        assert exc_info.value.reason == TerminationReason.ITERATION_LIMIT
        assert ctx.termination_reason == TerminationReason.ITERATION_LIMIT

    def test_tool_call_limit_raises(self):
        ctx = AgentExecutionContext(execution_id="t3", max_tool_calls=2)
        ctx.record_tool_call("tool_a")
        ctx.record_tool_call("tool_b")
        with pytest.raises(AgentBudgetExceededError) as exc_info:
            ctx.check()
        assert exc_info.value.reason == TerminationReason.TOOL_CALL_LIMIT

    def test_runtime_limit_raises(self):
        ctx = AgentExecutionContext(
            execution_id="t4",
            max_runtime_seconds=0.0,  # already expired
        )
        with pytest.raises(AgentBudgetExceededError) as exc_info:
            ctx.check()
        assert exc_info.value.reason == TerminationReason.RUNTIME_LIMIT

    def test_cancel_raises(self):
        ctx = AgentExecutionContext(execution_id="t5")
        ctx.cancel()
        assert ctx.is_cancelled
        with pytest.raises(AgentBudgetExceededError) as exc_info:
            ctx.check()
        assert exc_info.value.reason == TerminationReason.CANCELLED

    def test_partial_results_saved(self):
        ctx = AgentExecutionContext(execution_id="t6")
        ctx.save_partial({"node": "A", "result": "partial-A"})
        ctx.save_partial({"node": "B", "result": "partial-B"})
        assert len(ctx.partial_results) == 2
        assert ctx.partial_results[0]["result"] == "partial-A"

    def test_summary_dict(self):
        ctx = AgentExecutionContext(execution_id="t7")
        ctx.record_iteration()
        ctx.record_tool_call("web_search")
        s = ctx.summary()
        assert s["execution_id"] == "t7"
        assert s["iterations"] == 1
        assert s["tool_calls"] == 1
        assert "elapsed_seconds" in s

    def test_remaining_seconds(self):
        ctx = AgentExecutionContext(execution_id="t8", max_runtime_seconds=100)
        assert ctx.remaining_seconds > 90


# ═════════════════════════════════════════════════════════════════
# AgentWatchdog — integration tests
# ═════════════════════════════════════════════════════════════════


class TestAgentWatchdog:
    @pytest.mark.asyncio
    async def test_infinite_agent_loop_terminated(self):
        """Simulate an agent that loops forever — watchdog must kill it."""
        watchdog = AgentWatchdog(poll_interval=0.1)

        async def infinite_loop(ctx: AgentExecutionContext):
            while True:
                ctx.record_iteration()
                ctx.check()
                await asyncio.sleep(0.01)

        result, ctx = await watchdog.guarded_execute(
            "loop-1",
            infinite_loop,
            max_iterations=5,
            max_runtime_seconds=5,
        )

        assert result is None
        assert ctx.termination_reason == TerminationReason.ITERATION_LIMIT
        assert ctx.iteration_count == 5
        await watchdog.shutdown()

    @pytest.mark.asyncio
    async def test_runaway_tool_calls_terminated(self):
        """Simulate an agent making unlimited tool calls."""
        watchdog = AgentWatchdog(poll_interval=0.1)

        async def tool_spammer(ctx: AgentExecutionContext):
            i = 0
            while True:
                ctx.record_tool_call(f"tool_{i}")
                ctx.check()
                i += 1
                await asyncio.sleep(0.01)

        result, ctx = await watchdog.guarded_execute(
            "tools-1",
            tool_spammer,
            max_tool_calls=8,
            max_runtime_seconds=5,
        )

        assert result is None
        assert ctx.termination_reason == TerminationReason.TOOL_CALL_LIMIT
        assert ctx.tool_call_count == 8
        await watchdog.shutdown()

    @pytest.mark.asyncio
    async def test_slow_llm_response_timeout(self):
        """Simulate a slow LLM that exceeds the runtime budget."""
        watchdog = AgentWatchdog(poll_interval=0.1)

        async def slow_llm(ctx: AgentExecutionContext):
            ctx.record_iteration()
            # Simulate an LLM call that takes way too long
            await asyncio.sleep(10)
            return "should never reach here"

        result, ctx = await watchdog.guarded_execute(
            "slow-1",
            slow_llm,
            max_runtime_seconds=0.5,
        )

        assert result is None
        assert ctx.termination_reason == TerminationReason.RUNTIME_LIMIT
        assert ctx.elapsed_seconds >= 0.5
        await watchdog.shutdown()

    @pytest.mark.asyncio
    async def test_partial_results_preserved_on_budget(self):
        """When budget is exceeded, partial results remain accessible."""
        watchdog = AgentWatchdog(poll_interval=0.1)

        async def partial_worker(ctx: AgentExecutionContext):
            for i in range(20):
                ctx.record_iteration()
                ctx.save_partial({"step": i, "result": f"data-{i}"})
                ctx.check()
                await asyncio.sleep(0.01)

        result, ctx = await watchdog.guarded_execute(
            "partial-1",
            partial_worker,
            max_iterations=5,
            max_runtime_seconds=5,
        )

        assert result is None
        # Should have saved 5 partials before the 5th iteration tripped the limit
        assert len(ctx.partial_results) == 5
        assert ctx.partial_results[0]["result"] == "data-0"
        await watchdog.shutdown()

    @pytest.mark.asyncio
    async def test_normal_execution_completes(self):
        """Execution within budget should complete successfully."""
        watchdog = AgentWatchdog(poll_interval=0.1)

        async def good_agent(ctx: AgentExecutionContext):
            for i in range(3):
                ctx.record_iteration()
                ctx.record_tool_call(f"tool_{i}")
                ctx.check()
                await asyncio.sleep(0.01)
            return "final-answer"

        result, ctx = await watchdog.guarded_execute(
            "ok-1",
            good_agent,
            max_iterations=10,
            max_tool_calls=20,
            max_runtime_seconds=5,
        )

        assert result == "final-answer"
        assert ctx.termination_reason == TerminationReason.COMPLETED
        assert ctx.iteration_count == 3
        assert ctx.tool_call_count == 3
        await watchdog.shutdown()

    @pytest.mark.asyncio
    async def test_error_in_agent_returns_none(self):
        """An unhandled exception inside the agent should not crash."""
        watchdog = AgentWatchdog(poll_interval=0.1)

        async def buggy_agent(ctx: AgentExecutionContext):
            raise ValueError("oops")

        result, ctx = await watchdog.guarded_execute(
            "err-1",
            buggy_agent,
        )

        assert result is None
        assert ctx.termination_reason == TerminationReason.ERROR
        await watchdog.shutdown()

    @pytest.mark.asyncio
    async def test_concurrent_watched_executions(self):
        """Multiple independent executions tracked simultaneously."""
        watchdog = AgentWatchdog(poll_interval=0.1)

        async def worker(ctx: AgentExecutionContext):
            for _ in range(3):
                ctx.record_iteration()
                ctx.check()
                await asyncio.sleep(0.05)
            return f"done-{ctx.execution_id}"

        tasks = [
            watchdog.guarded_execute(
                f"multi-{i}",
                worker,
                max_iterations=10,
                max_runtime_seconds=5,
            )
            for i in range(5)
        ]
        results = await asyncio.gather(*tasks)

        for result_val, ctx in results:
            assert result_val is not None
            assert ctx.termination_reason == TerminationReason.COMPLETED

        await watchdog.shutdown()

    @pytest.mark.asyncio
    async def test_watchdog_shutdown_cancels_all(self):
        """shutdown() should cancel all tracked executions."""
        watchdog = AgentWatchdog(poll_interval=0.1)

        async def forever(ctx: AgentExecutionContext):
            while True:
                await asyncio.sleep(0.1)

        # Start but don't await — just register
        ctx = watchdog.register("shutdown-1", max_runtime_seconds=100)
        task = asyncio.ensure_future(forever(ctx))
        watchdog.attach_task("shutdown-1", task)

        await asyncio.sleep(0.2)
        await watchdog.shutdown()

        assert task.cancelled() or task.done()

    @pytest.mark.asyncio
    async def test_register_unregister_returns_context(self):
        watchdog = AgentWatchdog(poll_interval=0.1)
        ctx = watchdog.register("reg-1", max_iterations=5)
        assert ctx.execution_id == "reg-1"

        returned = watchdog.unregister("reg-1")
        assert returned is ctx

        assert watchdog.unregister("nonexistent") is None
        await watchdog.shutdown()
