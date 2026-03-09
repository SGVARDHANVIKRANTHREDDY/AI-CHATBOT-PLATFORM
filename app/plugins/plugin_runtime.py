"""
Plugin Runtime — Isolated subprocess entry point for plugin execution.

This module is launched as a child process by PluginRunner. It:
    1. Strips environment variables (defence against secret leakage).
    2. Reads a single PluginRequest from stdin.
    3. Imports the requested plugin module and invokes the function.
    4. Writes a PluginResponse to stdout.
    5. Exits immediately.

Security invariants enforced at the OS level by the PARENT process:
    • CPU time limit (SIGALRM / job object on Windows).
    • Memory limit (resource.setrlimit / job object on Windows).
    • Network disabled (cleared env, no socket access in restricted env).
    • No inherited env vars (parent launches with empty env).

This file deliberately avoids importing any application code except
the plugin protocol, so the subprocess stays lightweight.
"""
from __future__ import annotations

import asyncio
import importlib
import io
import json
import os
import sys
import time
import traceback


def _sanitize_environment() -> None:
    """Remove all environment variables to prevent secret leakage.

    Only keeps the minimum required for the Python runtime.
    """
    keep = {"PATH", "SYSTEMROOT", "TEMP", "TMP", "PYTHONPATH", "PYTHONHASHSEED"}
    for key in list(os.environ.keys()):
        if key not in keep:
            del os.environ[key]


def _read_request() -> dict:
    """Read a length-prefixed JSON request from stdin (binary)."""
    header = sys.stdin.buffer.readline()
    if not header:
        sys.exit(1)
    try:
        length = int(header.strip())
    except (ValueError, TypeError):
        sys.exit(1)
    if length <= 0 or length > 10 * 1024 * 1024:
        sys.exit(1)
    payload = sys.stdin.buffer.read(length)
    if len(payload) != length:
        sys.exit(1)
    return json.loads(payload.decode("utf-8"))


def _write_response(resp: dict) -> None:
    """Write a length-prefixed JSON response to stdout (binary)."""
    payload = json.dumps(resp, default=str).encode("utf-8")
    header = f"{len(payload)}\n".encode("utf-8")
    sys.stdout.buffer.write(header)
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _execute_plugin(request: dict) -> dict:
    """Import the plugin module, call the function, return response dict."""
    start = time.perf_counter()

    module_name = request.get("plugin_module", "")
    func_name = request.get("function_name", "")
    kwargs = request.get("kwargs", {})
    request_id = request.get("request_id", "")

    # Capture stdout/stderr from the plugin
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr

    try:
        sys.stdout = captured_stdout
        sys.stderr = captured_stderr

        module = importlib.import_module(module_name)
        func = getattr(module, func_name)

        # Support both sync and async plugin functions
        if asyncio.iscoroutinefunction(func):
            result = asyncio.run(func(**kwargs))
        else:
            result = func(**kwargs)

        elapsed_ms = (time.perf_counter() - start) * 1000

        return {
            "success": True,
            "result": result,
            "error": None,
            "error_type": None,
            "execution_time_ms": elapsed_ms,
            "stdout": captured_stdout.getvalue()[:4096],
            "stderr": captured_stderr.getvalue()[:4096],
            "request_id": request_id,
        }

    except Exception as exc:
        elapsed_ms = (time.perf_counter() - start) * 1000
        return {
            "success": False,
            "result": None,
            "error": f"{type(exc).__name__}: {exc}",
            "error_type": type(exc).__name__,
            "execution_time_ms": elapsed_ms,
            "stdout": captured_stdout.getvalue()[:4096],
            "stderr": captured_stderr.getvalue()[:4096],
            "request_id": request_id,
        }
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr


def main() -> None:
    _sanitize_environment()
    request = _read_request()
    response = _execute_plugin(request)
    _write_response(response)


if __name__ == "__main__":
    main()
