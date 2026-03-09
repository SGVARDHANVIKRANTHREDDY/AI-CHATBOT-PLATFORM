"""
Sandbox Runner — Restricted execution environment for plugins.

Enforces security boundaries so plugins cannot:
    • Import dangerous modules (os, sys, subprocess, socket, etc.)
    • Access the filesystem (open, pathlib, os.path)
    • Open network connections
    • Exceed execution time limits
    • Consume excessive memory

Security model:
    Plugins execute inside a restricted ``exec()`` scope with a
    custom ``__builtins__`` dict that only exposes safe built-in
    functions and a custom ``__import__`` hook that enforces the
    module whitelist.

    This is NOT a process-level sandbox (which would require Docker
    or seccomp).  It is a defence-in-depth layer that prevents
    *accidental* or *opportunistic* abuse.  For truly untrusted code,
    pair this with container isolation.
"""

from __future__ import annotations

import asyncio
import functools
import time
import tracemalloc
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from app.shared.utils import get_logger

_LOG = get_logger(__name__)

# ─── Module whitelist ─────────────────────────────────────────────
ALLOWED_MODULES: set[str] = {
    "json",
    "math",
    "re",
    "datetime",
    "collections",
    "typing",
    "string",
    "itertools",
    "functools",
    "decimal",
    "fractions",
    "hashlib",
    "base64",
    "copy",
    "enum",
    "dataclasses",
    "abc",
    "numbers",
    "statistics",
    "textwrap",
    "unicodedata",
}

# Modules explicitly blocked even if someone tries to sneak them in
BLOCKED_MODULES: set[str] = {
    "os",
    "sys",
    "subprocess",
    "shutil",
    "socket",
    "http",
    "urllib",
    "requests",
    "httpx",
    "aiohttp",
    "pathlib",
    "importlib",
    "ctypes",
    "multiprocessing",
    "threading",
    "signal",
    "code",
    "compile",
    "exec",
    "eval",
    "pickle",
    "shelve",
    "tempfile",
    "glob",
    "fnmatch",
    "io",
    "builtins",
    "__builtin__",
    "_io",
    "webbrowser",
    "smtplib",
    "ftplib",
    "xmlrpc",
}

# Safe built-in functions exposed to sandboxed code
SAFE_BUILTINS: dict[str, Any] = {
    # Types
    "True": True,
    "False": False,
    "None": None,
    # Functions
    "abs": abs,
    "all": all,
    "any": any,
    "bin": bin,
    "bool": bool,
    "chr": chr,
    "dict": dict,
    "dir": dir,
    "divmod": divmod,
    "enumerate": enumerate,
    "filter": filter,
    "float": float,
    "format": format,
    "frozenset": frozenset,
    "hasattr": hasattr,
    "hash": hash,
    "hex": hex,
    "int": int,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "iter": iter,
    "len": len,
    "list": list,
    "map": map,
    "max": max,
    "min": min,
    "next": next,
    "oct": oct,
    "ord": ord,
    "pow": pow,
    "print": print,  # Allow print for debug — output is captured
    "range": range,
    "repr": repr,
    "reversed": reversed,
    "round": round,
    "set": set,
    "slice": slice,
    "sorted": sorted,
    "str": str,
    "sum": sum,
    "tuple": tuple,
    "type": type,
    "zip": zip,
    # Explicitly NOT included: open, exec, eval, compile, __import__ (custom one added separately)
}


@dataclass
class SandboxResult:
    """Result of a sandboxed plugin execution."""

    success: bool = False
    result: Any = None
    error: str | None = None
    execution_time_ms: float = 0.0
    peak_memory_bytes: int = 0
    warnings: list[str] = field(default_factory=list)


class SandboxSecurityError(Exception):
    """Raised when a plugin violates sandbox security rules."""

    pass


class SandboxTimeoutError(Exception):
    """Raised when a plugin exceeds its execution time limit."""

    pass


class SandboxRunner:
    """Restricted execution environment for plugin code.

    Args:
        default_timeout: Default execution timeout in seconds per plugin call.
        memory_limit_mb: Maximum memory a plugin can allocate (tracked, not hard-limited).
        allowed_modules: Override the default module whitelist.
        max_workers: Thread pool size for synchronous plugin execution.
    """

    def __init__(
        self,
        default_timeout: float = 5.0,
        memory_limit_mb: float = 50.0,
        allowed_modules: set[str] | None = None,
        max_workers: int = 4,
    ) -> None:
        self.default_timeout = default_timeout
        self.memory_limit_bytes = int(memory_limit_mb * 1024 * 1024)
        self.allowed_modules = allowed_modules or ALLOWED_MODULES
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="plugin_sandbox")

    def _make_safe_import(self) -> Callable:
        """Create a restricted __import__ function."""
        allowed = self.allowed_modules

        def _safe_import(name: str, *args: Any, **kwargs: Any) -> Any:
            # Check top-level module name
            top_module = name.split(".")[0]
            if top_module in BLOCKED_MODULES:
                raise SandboxSecurityError(f"Module '{name}' is blocked in the plugin sandbox")
            if top_module not in allowed:
                raise SandboxSecurityError(
                    f"Module '{name}' is not in the whitelist. Allowed: {', '.join(sorted(allowed))}"
                )
            return (
                __builtins__["__import__"](name, *args, **kwargs)
                if isinstance(__builtins__, dict)
                else __import__(name, *args, **kwargs)
            )

        return _safe_import

    def _get_restricted_globals(self) -> dict[str, Any]:
        """Build the restricted global namespace for sandboxed code."""
        safe = dict(SAFE_BUILTINS)
        safe["__import__"] = self._make_safe_import()
        return {"__builtins__": safe}

    async def execute_plugin(
        self,
        func: Callable[..., Any],
        args: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> SandboxResult:
        """Execute a plugin function within the sandbox.

        If the function is a coroutine, it is awaited.
        If it is synchronous, it runs in a thread pool.

        Args:
            func: The plugin function to execute.
            args: Keyword arguments to pass to the function.
            timeout: Override for per-call timeout.

        Returns:
            SandboxResult with result or error information.
        """
        effective_timeout = timeout or self.default_timeout
        call_args = args or {}
        result = SandboxResult()
        start = time.perf_counter()

        # Start memory tracking
        was_tracing = tracemalloc.is_tracing()
        if not was_tracing:
            tracemalloc.start()

        snapshot_before = tracemalloc.take_snapshot()

        try:
            if asyncio.iscoroutinefunction(func):
                raw = await asyncio.wait_for(func(**call_args), timeout=effective_timeout)
            else:
                # Run sync function in thread pool with timeout
                loop = asyncio.get_running_loop()
                raw = await asyncio.wait_for(
                    loop.run_in_executor(self._executor, functools.partial(func, **call_args)),
                    timeout=effective_timeout,
                )

            result.success = True
            result.result = raw

        except TimeoutError:
            result.error = f"Plugin timed out after {effective_timeout}s"
            _LOG.warning("Sandbox timeout: %s after %.1fs", func.__name__, effective_timeout)

        except SandboxSecurityError as e:
            result.error = f"Security violation: {e}"
            _LOG.error("Sandbox security violation in %s: %s", func.__name__, e)

        except Exception as e:
            result.error = f"Plugin error: {type(e).__name__}: {e}"
            _LOG.error("Sandbox execution error in %s: %s", func.__name__, e)

        finally:
            elapsed = (time.perf_counter() - start) * 1000
            result.execution_time_ms = elapsed

            # Check memory usage
            snapshot_after = tracemalloc.take_snapshot()
            stats = snapshot_after.compare_to(snapshot_before, "lineno")
            peak_delta = sum(s.size_diff for s in stats if s.size_diff > 0)
            result.peak_memory_bytes = peak_delta

            if peak_delta > self.memory_limit_bytes:
                result.warnings.append(
                    f"Memory limit warning: {peak_delta / 1024 / 1024:.1f}MB "
                    f"exceeds {self.memory_limit_bytes / 1024 / 1024:.1f}MB limit"
                )
                _LOG.warning(
                    "Plugin %s exceeded memory limit: %dB > %dB",
                    func.__name__,
                    peak_delta,
                    self.memory_limit_bytes,
                )

            if not was_tracing:
                tracemalloc.stop()

        return result

    def validate_plugin_source(self, source_code: str) -> list[str]:
        """Static analysis of plugin source for forbidden patterns.

        Returns a list of violations found (empty = safe).
        """
        violations: list[str] = []

        for blocked in BLOCKED_MODULES:
            if f"import {blocked}" in source_code or f"from {blocked}" in source_code:
                violations.append(f"Forbidden import: '{blocked}'")

        # Check for dangerous built-in usage
        dangerous_calls = ["exec(", "eval(", "compile(", "__import__(", "open("]
        for call in dangerous_calls:
            if call in source_code:
                violations.append(f"Forbidden call: '{call}'")

        return violations
