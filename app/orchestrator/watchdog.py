"""
Agent Watchdog — Execution safety net for agent orchestration.

Prevents runaway agent loops by enforcing hard limits on iterations,
tool calls, and wall-clock time.  Provides graceful cancellation with
partial result preservation and error telemetry.

Components:
    AgentExecutionContext  — Per-execution tracker for iterations,
                             tool calls, and elapsed time.
    AgentWatchdog          — Background monitor that terminates agents
                             exceeding their budgets.

Design rationale:
    The existing AgentState tracks *logical* steps, but does not enforce
    wall-clock deadlines or provide an external kill-switch.  A runaway
    LLM response, infinite planning loop, or misbehaving tool can pin
    resources indefinitely.  The watchdog provides an independent,
    timer-based safety net that operates *outside* the agent loop and
    can cancel its asyncio tasks.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, TypeVar

from app.shared.utils import get_logger

_LOG = get_logger(__name__)

T = TypeVar("T")

# ── Defaults (overridable per-execution via constructor args) ─────
MAX_AGENT_ITERATIONS = 10
MAX_TOOL_CALLS = 20
MAX_RUNTIME_SECONDS = 30


class TerminationReason(StrEnum):
    COMPLETED = "completed"
    ITERATION_LIMIT = "iteration_limit"
    TOOL_CALL_LIMIT = "tool_call_limit"
    RUNTIME_LIMIT = "runtime_limit"
    CANCELLED = "cancelled"
    ERROR = "error"


@dataclass
class AgentExecutionContext:
    """Per-execution budget tracker.

    Created at the start of every agent loop invocation and threaded
    through AgentState / ReasoningGraphEngine so that each operation
    can check (and increment) the shared counters.

    The ``check()`` method raises ``AgentBudgetExceededError`` when any
    limit is breached, giving the caller an opportunity to capture
    partial results before exiting.
    """

    execution_id: str
    session_id: str = ""

    # Limits (mutable so callers can tighten per-request)
    max_iterations: int = MAX_AGENT_ITERATIONS
    max_tool_calls: int = MAX_TOOL_CALLS
    max_runtime_seconds: float = MAX_RUNTIME_SECONDS

    # Counters
    iteration_count: int = 0
    tool_call_count: int = 0
    _start_time: float = field(default_factory=time.monotonic)

    # Bookkeeping
    partial_results: list[dict[str, Any]] = field(default_factory=list)
    termination_reason: TerminationReason = TerminationReason.COMPLETED
    _cancelled: bool = False

    # ── Counter mutators ──────────────────────────────────────────

    def record_iteration(self) -> None:
        self.iteration_count += 1
        _LOG.debug(
            "[%s] iteration %d / %d",
            self.execution_id,
            self.iteration_count,
            self.max_iterations,
        )

    def record_tool_call(self, tool_name: str = "") -> None:
        self.tool_call_count += 1
        _LOG.debug(
            "[%s] tool_call %d / %d  (%s)",
            self.execution_id,
            self.tool_call_count,
            self.max_tool_calls,
            tool_name,
        )

    # ── Budget queries ────────────────────────────────────────────

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self._start_time

    @property
    def remaining_seconds(self) -> float:
        return max(0.0, self.max_runtime_seconds - self.elapsed_seconds)

    @property
    def is_cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True

    def check(self) -> None:
        """Raise AgentBudgetExceededError if any budget is exhausted."""
        if self._cancelled:
            self.termination_reason = TerminationReason.CANCELLED
            raise AgentBudgetExceededError(self, TerminationReason.CANCELLED)

        if self.iteration_count >= self.max_iterations:
            self.termination_reason = TerminationReason.ITERATION_LIMIT
            _LOG.warning(
                "[%s] iteration limit reached (%d)",
                self.execution_id,
                self.max_iterations,
            )
            raise AgentBudgetExceededError(self, TerminationReason.ITERATION_LIMIT)

        if self.tool_call_count >= self.max_tool_calls:
            self.termination_reason = TerminationReason.TOOL_CALL_LIMIT
            _LOG.warning(
                "[%s] tool-call limit reached (%d)",
                self.execution_id,
                self.max_tool_calls,
            )
            raise AgentBudgetExceededError(self, TerminationReason.TOOL_CALL_LIMIT)

        if self.elapsed_seconds >= self.max_runtime_seconds:
            self.termination_reason = TerminationReason.RUNTIME_LIMIT
            _LOG.warning(
                "[%s] runtime limit reached (%.1fs)",
                self.execution_id,
                self.max_runtime_seconds,
            )
            raise AgentBudgetExceededError(self, TerminationReason.RUNTIME_LIMIT)

    def save_partial(self, result: dict[str, Any]) -> None:
        """Stash a partial result so the caller can return *something*."""
        self.partial_results.append(result)

    def summary(self) -> dict[str, Any]:
        return {
            "execution_id": self.execution_id,
            "iterations": self.iteration_count,
            "tool_calls": self.tool_call_count,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "termination_reason": self.termination_reason.value,
            "partial_results_count": len(self.partial_results),
        }


class AgentBudgetExceededError(Exception):
    """Raised when an agent exceeds its execution budget."""

    def __init__(self, ctx: AgentExecutionContext, reason: TerminationReason) -> None:
        self.ctx = ctx
        self.reason = reason
        super().__init__(
            f"Agent {ctx.execution_id} terminated: {reason.value} "
            f"(iters={ctx.iteration_count}, tools={ctx.tool_call_count}, "
            f"elapsed={ctx.elapsed_seconds:.1f}s)"
        )


# ── AgentWatchdog ─────────────────────────────────────────────────


class AgentWatchdog:
    """Background monitor that terminates agents exceeding budgets.

    Usage::

        watchdog = AgentWatchdog()

        ctx = watchdog.register("exec-123", session_id="s1")
        task = asyncio.create_task(run_agent(ctx))
        watchdog.attach_task("exec-123", task)

        # ... when the orchestrator is done:
        watchdog.unregister("exec-123")

    The watchdog runs a single asyncio background task that polls all
    registered executions every ``poll_interval`` seconds and cancels
    any that have exceeded their wall-clock budget.
    """

    def __init__(self, poll_interval: float = 1.0) -> None:
        self.poll_interval = poll_interval
        self._executions: dict[str, _WatchedExecution] = {}
        self._monitor_task: asyncio.Task | None = None

    # ── Registration ──────────────────────────────────────────────

    def register(
        self,
        execution_id: str,
        *,
        session_id: str = "",
        max_iterations: int = MAX_AGENT_ITERATIONS,
        max_tool_calls: int = MAX_TOOL_CALLS,
        max_runtime_seconds: float = MAX_RUNTIME_SECONDS,
    ) -> AgentExecutionContext:
        """Create a new execution context and start monitoring it."""
        ctx = AgentExecutionContext(
            execution_id=execution_id,
            session_id=session_id,
            max_iterations=max_iterations,
            max_tool_calls=max_tool_calls,
            max_runtime_seconds=max_runtime_seconds,
        )
        self._executions[execution_id] = _WatchedExecution(ctx=ctx)
        _LOG.info(
            "Watchdog: registered execution %s (limits: iters=%d, tools=%d, runtime=%.0fs)",
            execution_id,
            max_iterations,
            max_tool_calls,
            max_runtime_seconds,
        )
        self._ensure_monitor_running()
        return ctx

    def attach_task(self, execution_id: str, task: asyncio.Task) -> None:
        """Associate an asyncio.Task so the watchdog can cancel it."""
        if execution_id in self._executions:
            self._executions[execution_id].task = task

    def unregister(self, execution_id: str) -> AgentExecutionContext | None:
        """Remove an execution from monitoring and return its context."""
        watched = self._executions.pop(execution_id, None)
        if watched:
            _LOG.info(
                "Watchdog: unregistered execution %s (%s)",
                execution_id,
                watched.ctx.termination_reason.value,
            )
            return watched.ctx
        return None

    # ── Background monitor loop ───────────────────────────────────

    def _ensure_monitor_running(self) -> None:
        if self._monitor_task is None or self._monitor_task.done():
            try:
                loop = asyncio.get_running_loop()
                self._monitor_task = loop.create_task(self._monitor_loop())
            except RuntimeError:
                pass  # no running loop yet

    async def _monitor_loop(self) -> None:
        """Poll registered executions and kill overdue ones."""
        _LOG.debug("Watchdog monitor loop started")
        try:
            while self._executions:
                to_kill: list[str] = []
                for eid, watched in list(self._executions.items()):
                    ctx = watched.ctx
                    if ctx.elapsed_seconds >= ctx.max_runtime_seconds:
                        to_kill.append(eid)

                for eid in to_kill:
                    self._terminate(eid)

                await asyncio.sleep(self.poll_interval)
        except asyncio.CancelledError:
            pass
        _LOG.debug("Watchdog monitor loop exited")

    def _terminate(self, execution_id: str) -> None:
        """Forcefully terminate an execution."""
        watched = self._executions.get(execution_id)
        if not watched:
            return

        ctx = watched.ctx
        ctx.termination_reason = TerminationReason.RUNTIME_LIMIT
        ctx.cancel()

        if watched.task and not watched.task.done():
            watched.task.cancel()
            _LOG.warning(
                "Watchdog: FORCE-TERMINATED execution %s after %.1fs (iters=%d, tools=%d)",
                execution_id,
                ctx.elapsed_seconds,
                ctx.iteration_count,
                ctx.tool_call_count,
            )
        else:
            _LOG.info(
                "Watchdog: marked execution %s as timed out (task already done)",
                execution_id,
            )

        # Emit telemetry
        try:
            from app.shared.monitoring import (
                WATCHDOG_EXECUTION_DURATION,
                WATCHDOG_TERMINATIONS,
            )

            WATCHDOG_TERMINATIONS.labels(reason=ctx.termination_reason.value).inc()
            WATCHDOG_EXECUTION_DURATION.observe(ctx.elapsed_seconds)
        except Exception:  # noqa: S110
            pass  # telemetry failure must not break the watchdog

    # ── Convenience: run a coroutine under watchdog protection ────

    async def guarded_execute(
        self,
        execution_id: str,
        coro_factory: Callable[[AgentExecutionContext], Coroutine[Any, Any, T]],
        *,
        session_id: str = "",
        max_iterations: int = MAX_AGENT_ITERATIONS,
        max_tool_calls: int = MAX_TOOL_CALLS,
        max_runtime_seconds: float = MAX_RUNTIME_SECONDS,
    ) -> tuple[T | None, AgentExecutionContext]:
        """Run *coro_factory(ctx)* with full watchdog protection.

        Returns (result_or_None, context).  On budget exceedance the
        result is ``None`` and ``context.partial_results`` contains
        whatever the agent saved before termination.
        """
        ctx = self.register(
            execution_id,
            session_id=session_id,
            max_iterations=max_iterations,
            max_tool_calls=max_tool_calls,
            max_runtime_seconds=max_runtime_seconds,
        )

        async def _wrapped() -> T:
            return await coro_factory(ctx)

        task = asyncio.current_task()
        if task:
            wrapper_task = asyncio.ensure_future(_wrapped())
            self.attach_task(execution_id, wrapper_task)
            try:
                result = await wrapper_task
                ctx.termination_reason = TerminationReason.COMPLETED
                return result, ctx
            except asyncio.CancelledError:
                ctx.termination_reason = TerminationReason.RUNTIME_LIMIT
                _LOG.warning(
                    "Watchdog: execution %s cancelled (partial results: %d)",
                    execution_id,
                    len(ctx.partial_results),
                )
                return None, ctx
            except AgentBudgetExceededError:
                _LOG.warning(
                    "Watchdog: execution %s hit budget (%s)",
                    execution_id,
                    ctx.termination_reason.value,
                )
                return None, ctx
            except Exception as exc:
                ctx.termination_reason = TerminationReason.ERROR
                _LOG.error(
                    "Watchdog: execution %s failed with %s: %s",
                    execution_id,
                    type(exc).__name__,
                    exc,
                )
                return None, ctx
            finally:
                self.unregister(execution_id)
                self._emit_completion_telemetry(ctx)
        else:
            # Fallback: no current task (testing scenario)
            try:
                result = await _wrapped()
                ctx.termination_reason = TerminationReason.COMPLETED
                return result, ctx
            except (asyncio.CancelledError, AgentBudgetExceededError):
                return None, ctx
            except Exception:
                ctx.termination_reason = TerminationReason.ERROR
                return None, ctx
            finally:
                self.unregister(execution_id)
                self._emit_completion_telemetry(ctx)

    def _emit_completion_telemetry(self, ctx: AgentExecutionContext) -> None:
        try:
            from app.shared.monitoring import (
                WATCHDOG_EXECUTION_DURATION,
                WATCHDOG_ITERATIONS_USED,
                WATCHDOG_TOOL_CALLS_USED,
            )

            WATCHDOG_EXECUTION_DURATION.observe(ctx.elapsed_seconds)
            WATCHDOG_ITERATIONS_USED.observe(ctx.iteration_count)
            WATCHDOG_TOOL_CALLS_USED.observe(ctx.tool_call_count)
        except Exception:  # noqa: S110
            pass

    async def shutdown(self) -> None:
        """Cancel the monitor loop and all tracked executions."""
        for eid in list(self._executions):
            self._terminate(eid)
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._monitor_task
        self._executions.clear()
        _LOG.info("Watchdog shut down")


@dataclass
class _WatchedExecution:
    ctx: AgentExecutionContext
    task: asyncio.Task | None = None
