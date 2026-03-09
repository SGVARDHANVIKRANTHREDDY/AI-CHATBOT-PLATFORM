"""
Plugin Protocol — Strict JSON message format for subprocess plugin IPC.

Defines the wire protocol between the Plugin Manager (parent process)
and the Isolated Plugin Runtime (child subprocess).

Message flow:
    Parent → Child:  PluginRequest  (invoke a plugin function)
    Child  → Parent: PluginResponse (result or error)

Security invariants:
    • Messages are length-prefixed JSON on stdin/stdout.
    • No pickle, no eval, no arbitrary deserialization.
    • The child MUST respond within the timeout or be killed.
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# ── Wire format helpers ───────────────────────────────────────────

def write_message(stream, msg: dict) -> None:
    """Write a length-prefixed JSON message to *stream*."""
    payload = json.dumps(msg, default=str).encode("utf-8")
    header = f"{len(payload)}\n".encode("utf-8")
    stream.write(header)
    stream.write(payload)
    stream.flush()


def read_message(stream) -> Optional[dict]:
    """Read a length-prefixed JSON message from *stream*.

    Returns None on EOF or malformed input.
    """
    header = stream.readline()
    if not header:
        return None
    try:
        length = int(header.strip())
    except (ValueError, TypeError):
        return None
    if length <= 0 or length > 10 * 1024 * 1024:  # 10 MB hard cap
        return None
    payload = stream.read(length)
    if len(payload) != length:
        return None
    return json.loads(payload.decode("utf-8"))


# ── Request / Response data classes ───────────────────────────────

@dataclass
class PluginRequest:
    """Message sent from parent to the plugin subprocess."""
    method: str                         # "invoke"
    plugin_module: str                  # e.g. "app.plugins.weather_plugin"
    function_name: str                  # e.g. "get_weather"
    kwargs: Dict[str, Any] = field(default_factory=dict)
    request_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PluginRequest":
        return cls(
            method=d.get("method", "invoke"),
            plugin_module=d.get("plugin_module", ""),
            function_name=d.get("function_name", ""),
            kwargs=d.get("kwargs", {}),
            request_id=d.get("request_id", ""),
        )


@dataclass
class PluginResponse:
    """Message sent from the plugin subprocess back to the parent."""
    success: bool = False
    result: Any = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    execution_time_ms: float = 0.0
    stdout: str = ""
    stderr: str = ""
    request_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PluginResponse":
        return cls(
            success=d.get("success", False),
            result=d.get("result"),
            error=d.get("error"),
            error_type=d.get("error_type"),
            execution_time_ms=d.get("execution_time_ms", 0.0),
            stdout=d.get("stdout", ""),
            stderr=d.get("stderr", ""),
            request_id=d.get("request_id", ""),
        )
