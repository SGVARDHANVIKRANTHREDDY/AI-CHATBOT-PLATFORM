"""
Fault Injectors — Component-specific chaos fault implementations.

Each injector targets a specific system component and simulates
realistic failure modes observed in production AI platforms:

    LLMProviderFault    — Provider API outage, timeouts, degraded responses
    VectorDBFault       — Vector store unavailability, search corruption
    PluginFault         — Plugin subprocess crashes, hangs, bad output
    WorkerFault         — Celery worker failures, task rejections
"""
from __future__ import annotations

import asyncio
from typing import Any, Dict, List, Optional, Type
from unittest.mock import AsyncMock, patch, MagicMock

from tests.chaos.framework import (
    ChaosResult,
    FaultInjector,
    FaultSeverity,
    FaultType,
    make_corrupt_coro,
    make_failing_coro,
    make_intermittent_coro,
    make_latency_coro,
    make_timeout_coro,
)


# ═══════════════════════════════════════════════════════════════════
#  LLM Provider Faults
# ═══════════════════════════════════════════════════════════════════

class LLMProviderFault(FaultInjector):
    """Simulates LLM provider failures on a live provider instance.

    Patches the provider's ``ask`` and ``ask_stream`` methods directly.

    Modes:
        OUTAGE   — All calls raise ConnectionError
        TIMEOUT  — All calls hang until async timeout fires
        INTERMITTENT — ``failure_rate`` fraction of calls fail
        CORRUPTION — Returns malformed / nonsensical output
        LATENCY  — Adds artificial delay before responding
    """

    def __init__(
        self,
        provider: Any,
        fault_type: FaultType = FaultType.OUTAGE,
        severity: FaultSeverity = FaultSeverity.COMPLETE,
        failure_rate: float = 0.5,
        latency_seconds: float = 5.0,
        error_cls: Type[Exception] = ConnectionError,
        error_message: str = "Chaos: LLM provider unavailable",
    ) -> None:
        super().__init__("llm_provider", fault_type, severity)
        self.provider = provider
        self.failure_rate = failure_rate
        self.latency_seconds = latency_seconds
        self.error_cls = error_cls
        self.error_message = error_message
        self._original_ask = None
        self._original_stream = None
        self._original_health = None

    async def _apply(self) -> None:
        self._original_ask = self.provider.ask
        self._original_stream = getattr(self.provider, "ask_stream", None)
        self._original_health = getattr(self.provider, "health_check", None)

        if self.fault_type == FaultType.OUTAGE:
            self.provider.ask = make_failing_coro(self.error_cls, self.error_message)
            if self._original_stream:
                self.provider.ask_stream = make_failing_coro(self.error_cls, self.error_message)
            if self._original_health:
                self.provider.health_check = make_failing_coro(self.error_cls, self.error_message)

        elif self.fault_type == FaultType.TIMEOUT:
            self.provider.ask = make_timeout_coro()
            if self._original_stream:
                self.provider.ask_stream = make_timeout_coro()

        elif self.fault_type == FaultType.INTERMITTENT:
            self.provider.ask = make_intermittent_coro(
                self._original_ask, self.failure_rate, self.error_cls,
            )

        elif self.fault_type == FaultType.CORRUPTION:
            self.provider.ask = make_corrupt_coro("{{LLM_GARBAGE_92hf}}")

        elif self.fault_type == FaultType.LATENCY:
            self.provider.ask = make_latency_coro(
                self._original_ask, self.latency_seconds,
            )

    async def _revert(self) -> None:
        if self._original_ask:
            self.provider.ask = self._original_ask
        if self._original_stream:
            self.provider.ask_stream = self._original_stream
        if self._original_health:
            self.provider.health_check = self._original_health


# ═══════════════════════════════════════════════════════════════════
#  Vector Database Faults
# ═══════════════════════════════════════════════════════════════════

class VectorDBFault(FaultInjector):
    """Simulates vector database failures on a store instance.

    Patches ``search``, ``add_embedding``, ``delete``, and ``batch_insert``.

    Modes:
        OUTAGE    — All operations raise ConnectionError
        TIMEOUT   — Operations hang until timeout
        CORRUPTION — search returns empty or garbage results
        INTERMITTENT — Flaky connectivity
    """

    def __init__(
        self,
        store: Any,
        fault_type: FaultType = FaultType.OUTAGE,
        severity: FaultSeverity = FaultSeverity.COMPLETE,
        failure_rate: float = 0.5,
        error_cls: Type[Exception] = ConnectionError,
        error_message: str = "Chaos: Vector DB connection refused",
    ) -> None:
        super().__init__("vector_database", fault_type, severity)
        self.store = store
        self.failure_rate = failure_rate
        self.error_cls = error_cls
        self.error_message = error_message
        self._originals: Dict[str, Any] = {}

    async def _apply(self) -> None:
        ops = ["search", "add_embedding", "delete", "batch_insert"]
        for op_name in ops:
            original = getattr(self.store, op_name, None)
            if original is None:
                continue
            self._originals[op_name] = original

            if self.fault_type == FaultType.OUTAGE:
                setattr(self.store, op_name, make_failing_coro(
                    self.error_cls, f"Chaos: {op_name} — {self.error_message}",
                ))
            elif self.fault_type == FaultType.TIMEOUT:
                setattr(self.store, op_name, make_timeout_coro())
            elif self.fault_type == FaultType.INTERMITTENT:
                setattr(self.store, op_name, make_intermittent_coro(
                    original, self.failure_rate, self.error_cls,
                ))
            elif self.fault_type == FaultType.CORRUPTION:
                # search returns empty; writes silently succeed
                if op_name == "search":
                    async def _empty_search(*a, **kw):
                        return []
                    setattr(self.store, op_name, _empty_search)
                else:
                    async def _noop(*a, **kw):
                        return 0
                    setattr(self.store, op_name, _noop)

    async def _revert(self) -> None:
        for op_name, original in self._originals.items():
            setattr(self.store, op_name, original)
        self._originals.clear()


# ═══════════════════════════════════════════════════════════════════
#  Plugin Faults
# ═══════════════════════════════════════════════════════════════════

class PluginFault(FaultInjector):
    """Simulates plugin execution failures on a PluginRunner instance.

    Modes:
        CRASH     — run_plugin raises RuntimeError
        TIMEOUT   — run_plugin hangs
        CORRUPTION — run_plugin returns error result
    """

    def __init__(
        self,
        runner: Any,
        fault_type: FaultType = FaultType.CRASH,
        severity: FaultSeverity = FaultSeverity.COMPLETE,
        error_message: str = "Chaos: plugin process crashed (SIGSEGV)",
    ) -> None:
        super().__init__("plugin_runner", fault_type, severity)
        self.runner = runner
        self.error_message = error_message
        self._original_run = None

    async def _apply(self) -> None:
        self._original_run = getattr(self.runner, "run_plugin", None)

        if self.fault_type == FaultType.CRASH:
            async def _crash(*args, **kwargs):
                raise RuntimeError(self.error_message)
            self.runner.run_plugin = _crash

        elif self.fault_type == FaultType.TIMEOUT:
            self.runner.run_plugin = make_timeout_coro()

        elif self.fault_type == FaultType.CORRUPTION:
            async def _bad_result(*args, **kwargs):
                from app.plugins.registry import PluginRunResult
                return PluginRunResult(
                    success=False,
                    error="Chaos: plugin returned corrupted output",
                    error_type="ChaosCorruption",
                )
            self.runner.run_plugin = _bad_result

    async def _revert(self) -> None:
        if self._original_run is not None:
            self.runner.run_plugin = self._original_run


# ═══════════════════════════════════════════════════════════════════
#  Worker Process Faults
# ═══════════════════════════════════════════════════════════════════

class WorkerFault(FaultInjector):
    """Simulates Celery worker / background task failures.

    Patches the task's ``delay`` and ``apply_async`` so callers
    get realistic error behaviour without a running broker.

    Modes:
        CRASH   — Task raises immediately on dispatch
        TIMEOUT — Task result never resolves (simulates stuck worker)
        OUTAGE  — Broker unreachable (kombu OperationalError)
    """

    def __init__(
        self,
        task: Any,
        fault_type: FaultType = FaultType.CRASH,
        severity: FaultSeverity = FaultSeverity.COMPLETE,
        error_message: str = "Chaos: worker process died",
    ) -> None:
        super().__init__("worker_process", fault_type, severity)
        self.task = task
        self.error_message = error_message
        self._original_delay = None
        self._original_apply_async = None

    async def _apply(self) -> None:
        self._original_delay = getattr(self.task, "delay", None)
        self._original_apply_async = getattr(self.task, "apply_async", None)

        if self.fault_type in (FaultType.CRASH, FaultType.OUTAGE):
            def _fail(*args, **kwargs):
                raise ConnectionError(self.error_message)
            self.task.delay = _fail
            self.task.apply_async = _fail

        elif self.fault_type == FaultType.TIMEOUT:
            class _HungResult:
                """Simulates an AsyncResult that never resolves."""
                id = "chaos-hung-task"
                def get(self, timeout=None, **kw):
                    raise TimeoutError(f"Chaos: task result timed out after {timeout}s")
                def ready(self):
                    return False
                def successful(self):
                    return False
                def failed(self):
                    return False

            def _hung_dispatch(*args, **kwargs):
                return _HungResult()
            self.task.delay = _hung_dispatch
            self.task.apply_async = _hung_dispatch

    async def _revert(self) -> None:
        if self._original_delay is not None:
            self.task.delay = self._original_delay
        if self._original_apply_async is not None:
            self.task.apply_async = self._original_apply_async
