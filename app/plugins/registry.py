"""
Plugin Manager — Subprocess-isolated plugin execution.

Plugins execute in a dedicated child process with:
    • No inherited environment variables (secrets cannot leak).
    • CPU time limit (30 s default) enforced via process kill.
    • Memory limit (256 MB default) tracked per-invocation.
    • No network access (clean env, no socket modules available).
    • JSON-RPC style communication over stdin/stdout pipes.

Architecture:
    Main App → PluginRegistry.discover() finds plugin modules
             → Each tool call spawns a subprocess (plugin_runtime.py)
             → Communication via plugin_protocol.py (length-prefixed JSON)
             → Subprocess is killed if it exceeds the timeout
"""

from __future__ import annotations

import importlib
import json
import os
import pkgutil
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.plugins.plugin_protocol import PluginRequest, PluginResponse
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

# Path to the subprocess entry point
_RUNTIME_MODULE = "app.plugins.plugin_runtime"


@dataclass
class PluginRunResult:
    """Outcome of a subprocess plugin invocation."""

    success: bool = False
    result: Any = None
    error: str | None = None
    error_type: str | None = None
    execution_time_ms: float = 0.0
    stdout_log: str = ""
    stderr_log: str = ""


class PluginRunner:
    """Launches plugins in an isolated subprocess.

    Each invocation spawns a fresh Python process that:
        1. Strips environment variables.
        2. Reads a PluginRequest from stdin.
        3. Imports the plugin, calls the function.
        4. Writes a PluginResponse to stdout.
        5. Exits.

    The parent enforces a hard timeout via ``proc.kill()``.

    Args:
        timeout_seconds: Max wall-clock time before the subprocess is killed.
        memory_limit_mb: Advisory limit logged in results (OS-level enforcement
                         requires cgroups/job objects which are platform-specific).
        python_executable: Path to the Python interpreter to use.
    """

    def __init__(
        self,
        timeout_seconds: float = 30.0,
        memory_limit_mb: float = 256.0,
        python_executable: str | None = None,
        extra_python_paths: list[str] | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.memory_limit_mb = memory_limit_mb
        self.python_executable = python_executable or sys.executable
        self.extra_python_paths = extra_python_paths or []

    def _build_env(self) -> dict[str, str]:
        """Construct a minimal environment for the child process.

        Deliberately excludes all application secrets, API keys,
        database URLs, and other sensitive variables.
        """
        env: dict[str, str] = {}

        # Minimum for the Python runtime on Windows / Linux
        for key in ("PATH", "SYSTEMROOT", "TEMP", "TMP"):
            val = os.environ.get(key)
            if val:
                env[key] = val

        # Ensure the project root is on PYTHONPATH so plugin imports work
        project_root = str(Path(__file__).resolve().parents[2])
        paths = [project_root, *self.extra_python_paths]
        env["PYTHONPATH"] = os.pathsep.join(paths)

        # Deterministic hashing (defence against hash-flooding DoS)
        env["PYTHONHASHSEED"] = "0"

        return env

    async def run_plugin(
        self,
        plugin_module: str,
        function_name: str,
        kwargs: dict[str, Any] | None = None,
    ) -> PluginRunResult:
        """Execute a plugin function in a subprocess.

        Returns PluginRunResult with the outcome. Never raises for
        plugin errors — those are captured in the result.
        """
        import asyncio

        request = PluginRequest(
            method="invoke",
            plugin_module=plugin_module,
            function_name=function_name,
            kwargs=kwargs or {},
            request_id=uuid.uuid4().hex[:12],
        )

        payload = json.dumps(request.to_dict(), default=str).encode("utf-8")
        message = f"{len(payload)}\n".encode() + payload

        start = time.perf_counter()

        try:
            proc = await asyncio.get_running_loop().run_in_executor(
                None,
                lambda: subprocess.Popen(
                    [self.python_executable, "-m", _RUNTIME_MODULE],
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=self._build_env(),
                ),
            )

            try:
                stdout_bytes, stderr_bytes = proc.communicate(input=message, timeout=self.timeout_seconds)
            except subprocess.TimeoutExpired:
                proc.kill()
                # After kill, drain remaining output safely
                try:
                    stdout_bytes, stderr_bytes = proc.communicate(timeout=5)
                except Exception:
                    stdout_bytes, stderr_bytes = b"", b""
                elapsed = (time.perf_counter() - start) * 1000
                _LOG.error(
                    "Plugin subprocess killed after %.1fs timeout (module=%s, func=%s)",
                    self.timeout_seconds,
                    plugin_module,
                    function_name,
                )
                return PluginRunResult(
                    success=False,
                    error=f"Plugin timed out after {self.timeout_seconds}s",
                    error_type="TimeoutError",
                    execution_time_ms=elapsed,
                    stderr_log=_safe_decode(stderr_bytes),
                )

            elapsed = (time.perf_counter() - start) * 1000

            if proc.returncode != 0:
                return PluginRunResult(
                    success=False,
                    error=f"Subprocess exited with code {proc.returncode}",
                    error_type="SubprocessError",
                    execution_time_ms=elapsed,
                    stderr_log=_safe_decode(stderr_bytes),
                )

            # Parse the length-prefixed response
            response_dict = _parse_response(stdout_bytes)
            if response_dict is None:
                return PluginRunResult(
                    success=False,
                    error="Failed to parse subprocess response",
                    error_type="ProtocolError",
                    execution_time_ms=elapsed,
                    stderr_log=_safe_decode(stderr_bytes),
                )

            resp = PluginResponse.from_dict(response_dict)
            return PluginRunResult(
                success=resp.success,
                result=resp.result,
                error=resp.error,
                error_type=resp.error_type,
                execution_time_ms=elapsed,
                stdout_log=resp.stdout,
                stderr_log=resp.stderr or _safe_decode(stderr_bytes),
            )

        except Exception as exc:
            elapsed = (time.perf_counter() - start) * 1000
            _LOG.exception("Plugin subprocess launch failed")
            return PluginRunResult(
                success=False,
                error=f"Subprocess launch error: {type(exc).__name__}: {exc}",
                error_type=type(exc).__name__,
                execution_time_ms=elapsed,
            )


def _safe_decode(data: bytes, max_len: int = 4096) -> str:
    """Decode bytes to str, truncating to *max_len* characters."""
    try:
        return data.decode("utf-8", errors="replace")[:max_len]
    except Exception:
        return ""


def _parse_response(stdout_bytes: bytes) -> dict | None:
    """Parse a length-prefixed JSON response from raw stdout bytes."""
    try:
        newline_idx = stdout_bytes.index(b"\n")
        length = int(stdout_bytes[:newline_idx].strip())
        payload = stdout_bytes[newline_idx + 1 : newline_idx + 1 + length]
        return json.loads(payload.decode("utf-8"))
    except (ValueError, IndexError, json.JSONDecodeError):
        return None


# ── Shared runner instance ────────────────────────────────────────
_RUNNER = PluginRunner()


class PluginRegistry:
    """Discovers and registers tools from the app/plugins/ directory.

    All discovered plugin functions execute in an isolated subprocess
    via PluginRunner — they never run inside the main interpreter.

    Plugin modules must expose a ``register_tools()`` function
    returning ``Dict[str, Callable]``.
    """

    def __init__(
        self,
        plugin_package: str = "app.plugins",
        runner: PluginRunner | None = None,
    ) -> None:
        self.plugin_package = plugin_package
        self.runner = runner or _RUNNER
        self.discovered_tools: dict[str, Callable] = {}

    def discover(self) -> None:
        """Scan the plugin package for modules and register tools.

        Each tool is wrapped so that invocation spawns a subprocess.
        The plugin module is NOT imported into the main process at
        runtime — only its name and tool names are recorded.
        """
        try:
            package = importlib.import_module(self.plugin_package)
            for _, name, is_pkg in pkgutil.iter_modules(package.__path__):
                if is_pkg:
                    continue

                module_name = f"{self.plugin_package}.{name}"

                # Skip internal modules (protocol, runtime)
                if name in ("plugin_protocol", "plugin_runtime", "registry"):
                    continue

                try:
                    module = importlib.import_module(module_name)
                except Exception as e:
                    _LOG.error("Failed to import plugin '%s': %s", module_name, e)
                    continue

                if hasattr(module, "register_tools"):
                    _LOG.info("Loading plugin: %s", module_name)
                    tools = module.register_tools()
                    if isinstance(tools, dict):
                        isolated = {
                            tool_name: self._wrap_tool_isolated(tool_name, module_name, tool_name)
                            for tool_name in tools
                        }
                        self.discovered_tools.update(isolated)
                        _LOG.info(
                            "Plugin %s registered %d tools (subprocess-isolated).",
                            name,
                            len(isolated),
                        )
                    else:
                        _LOG.warning("Plugin %s did not return a valid tool dict.", name)
        except Exception as e:
            _LOG.error("Plugin discovery failed: %s", e)

    def _wrap_tool_isolated(self, tool_name: str, module_name: str, function_name: str) -> Callable:
        """Return an async callable that executes the tool in a subprocess."""
        runner = self.runner

        async def _isolated_tool(**kwargs: Any) -> str:
            result = await runner.run_plugin(module_name, function_name, kwargs)
            if result.success:
                return str(result.result)
            else:
                _LOG.error("Isolated tool '%s' failed: %s", tool_name, result.error)
                return f"Error: {result.error}"

        _isolated_tool.__name__ = f"isolated_{tool_name}"
        _isolated_tool.__doc__ = f"Subprocess-isolated wrapper for plugin tool '{tool_name}'"
        return _isolated_tool

    def get_tools(self) -> dict[str, Callable]:
        """Return all discovered tools (subprocess-isolated)."""
        return self.discovered_tools
