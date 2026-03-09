# Plugin System

> Complete documentation of the plugin architecture: discovery, subprocess isolation, IPC protocol, sandbox security, and the development API.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Plugin Registry](#plugin-registry)
- [Plugin Runner](#plugin-runner)
- [Plugin Protocol (IPC)](#plugin-protocol-ipc)
- [Plugin Runtime](#plugin-runtime)
- [Sandbox Runner](#sandbox-runner)
- [Writing a Plugin](#writing-a-plugin)
- [Security Model](#security-model)
- [Example: Weather Plugin](#example-weather-plugin)
- [Configuration](#configuration)
- [Failure Modes](#failure-modes)

---

## Overview

The plugin system extends the chatbot with third-party capabilities while maintaining strict security isolation. Plugins run in **subprocess sandboxes** with sanitized environments, IPC-based communication, and execution timeouts.

**Design principles:**
- **Isolation:** Each plugin invocation spawns a new subprocess
- **Least privilege:** Environment stripped to PATH, TEMP, PYTHONPATH only
- **Time-bounded:** 30-second execution timeout per invocation
- **Protocol-driven:** Length-prefixed JSON messages over stdout/stdin
- **Fail-safe:** Plugin failures cannot crash the host process

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│ Host Process (FastAPI)                                  │
│                                                         │
│  ┌──────────────┐     ┌──────────────┐                 │
│  │ Plugin       │     │ Plugin       │                 │
│  │ Registry     │────▶│ Runner       │                 │
│  │              │     │ (subprocess) │                 │
│  └──────────────┘     └──────┬───────┘                 │
│                              │                         │
│                     ┌────────▼────────┐                │
│                     │ Subprocess      │                │
│                     │ Management      │                │
│                     │ • Spawn         │                │
│                     │ • Timeout (30s) │                │
│                     │ • Env sanitize  │                │
│                     └────────┬────────┘                │
└──────────────────────────────┼─────────────────────────┘
                               │ stdin/stdout
                     ┌─────────▼──────────┐
                     │ Plugin Subprocess  │
                     │                    │
                     │  ┌──────────────┐  │
                     │  │ PluginRuntime│  │
                     │  │ (entry point)│  │
                     │  └──────┬───────┘  │
                     │         │          │
                     │  ┌──────▼───────┐  │
                     │  │ Plugin Code  │  │
                     │  │ register_    │  │
                     │  │ tools()      │  │
                     │  └──────────────┘  │
                     └────────────────────┘
```

---

## Plugin Registry

**Location:** `app/plugins/registry.py`

The registry discovers, loads, and manages plugin modules.

### Discovery

```python
class PluginRegistry:
    def __init__(self, plugin_dir: str = "app/plugins"):
        self.plugins = {}   # name → plugin module
        self.tools = {}     # tool_name → tool function

    def discover(self):
        """Scan plugin directory for modules with register_tools()."""
        for file in Path(self.plugin_dir).glob("*.py"):
            if file.stem.startswith("_"):
                continue
            module = importlib.import_module(f"app.plugins.{file.stem}")
            if hasattr(module, "register_tools"):
                tools = module.register_tools()
                for tool_name, tool_fn in tools.items():
                    self.tools[tool_name] = tool_fn
                    self.plugins[file.stem] = module
```

### Plugin Module Contract

Every plugin module must expose a `register_tools()` function:

```python
def register_tools() -> Dict[str, Callable]:
    """Return a dict of tool_name → tool_function."""
    return {
        "get_weather": get_weather,
        "get_forecast": get_forecast,
    }
```

### Tool Wrapping

Discovered plugin tools are wrapped for subprocess execution:

```python
def _wrap_as_subprocess(self, tool_name: str, module_path: str):
    """Wrap a plugin tool to run in a subprocess."""
    async def wrapped(**kwargs):
        runner = PluginRunner()
        return await runner.run(module_path, tool_name, kwargs)
    return wrapped
```

---

## Plugin Runner

**Location:** `app/plugins/plugin_runtime.py`

The `PluginRunner` executes plugin tools in isolated subprocesses.

### Execution Flow

```python
class PluginRunner:
    TIMEOUT = 30  # seconds

    async def run(self, module_path: str, tool_name: str, args: dict) -> str:
        """Run a plugin tool in an isolated subprocess."""

        # 1. Sanitize environment
        safe_env = self._sanitize_env()

        # 2. Spawn subprocess
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "app.plugins.plugin_runtime",
            module_path, tool_name,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=safe_env,
        )

        # 3. Send arguments via IPC protocol
        request = json.dumps(args).encode()
        length_prefix = struct.pack(">I", len(request))

        # 4. Wait for response with timeout
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(input=length_prefix + request),
                timeout=self.TIMEOUT
            )
        except asyncio.TimeoutError:
            process.kill()
            raise PluginTimeoutError(f"Plugin {tool_name} timed out after {self.TIMEOUT}s")

        # 5. Parse response
        return self._parse_response(stdout)
```

### Environment Sanitization

```python
def _sanitize_env(self) -> dict:
    """Create a minimal environment for the plugin subprocess."""
    return {
        "PATH": os.environ.get("PATH", ""),
        "TEMP": os.environ.get("TEMP", "/tmp"),
        "PYTHONPATH": os.environ.get("PYTHONPATH", ""),
        # All other env vars are stripped
        # No API keys, no database URLs, no secrets
    }
```

This prevents plugins from accessing:
- API keys (`HF_API_TOKEN`, `OPENAI_API_KEY`)
- Database credentials (`DATABASE_URL`, `REDIS_URL`)
- System configuration (`JWT_SECRET`, internal settings)

---

## Plugin Protocol (IPC)

**Location:** `app/plugins/plugin_protocol.py`

Communication between the host and plugin subprocess uses length-prefixed JSON messages.

### Wire Format

```
┌──────────────┬───────────────────────────┐
│ 4 bytes      │ N bytes                   │
│ (big-endian) │ (JSON payload)            │
│ = N          │                           │
└──────────────┴───────────────────────────┘
```

### Protocol Implementation

```python
class PluginProtocol:
    MAX_MESSAGE_SIZE = 10 * 1024 * 1024  # 10MB

    @staticmethod
    def send(stream, data: dict):
        """Send a length-prefixed JSON message."""
        payload = json.dumps(data).encode('utf-8')
        if len(payload) > PluginProtocol.MAX_MESSAGE_SIZE:
            raise PluginProtocolError("Message exceeds 10MB limit")
        stream.write(struct.pack(">I", len(payload)))
        stream.write(payload)
        stream.flush()

    @staticmethod
    def receive(stream) -> dict:
        """Receive a length-prefixed JSON message."""
        header = stream.read(4)
        if len(header) < 4:
            raise PluginProtocolError("Incomplete header")
        length = struct.unpack(">I", header)[0]
        if length > PluginProtocol.MAX_MESSAGE_SIZE:
            raise PluginProtocolError(f"Message size {length} exceeds 10MB limit")
        payload = stream.read(length)
        return json.loads(payload.decode('utf-8'))
```

### Size Limit

The 10MB cap prevents plugins from:
- Exhausting host memory with large responses
- Performing denial-of-service via message flooding

---

## Plugin Runtime

**Location:** `app/plugins/plugin_runtime.py`

The runtime is the entry point for the plugin subprocess.

### Subprocess Entry Point

```python
# Executed as: python -m app.plugins.plugin_runtime <module_path> <tool_name>

def main():
    module_path = sys.argv[1]
    tool_name = sys.argv[2]

    # 1. Dynamically import the plugin module
    module = importlib.import_module(module_path)

    # 2. Get the tool function
    tools = module.register_tools()
    tool_fn = tools[tool_name]

    # 3. Read request via IPC protocol
    request = PluginProtocol.receive(sys.stdin.buffer)

    # 4. Capture stdout/stderr (4KB limit)
    with redirect_stdout(StringIO()) as stdout_capture:
        with redirect_stderr(StringIO()) as stderr_capture:
            result = tool_fn(**request)

    # 5. Send response via IPC protocol
    response = {
        "result": result,
        "stdout": stdout_capture.getvalue()[:4096],
        "stderr": stderr_capture.getvalue()[:4096],
    }
    PluginProtocol.send(sys.stdout.buffer, response)
```

### Output Capture

The runtime captures the tool's stdout and stderr to prevent plugins from interfering with the IPC channel:
- stdout/stderr are redirected to StringIO buffers
- Each buffer is truncated to 4KB to prevent memory unbounding
- Captured output is included in the response for debugging

---

## Sandbox Runner

**Location:** `app/plugins/sandbox/sandbox_runner.py`

The `SandboxRunner` provides an **in-process** sandbox for lightweight plugin execution (alternative to subprocess isolation).

### Module Restrictions

```python
ALLOWED_MODULES = [
    "json", "math", "re", "datetime", "collections", "itertools",
    "functools", "operator", "string", "textwrap", "unicodedata",
    "hashlib", "hmac", "base64", "urllib.parse", "html",
    "dataclasses", "enum", "typing", "abc", "copy", "pprint",
    "statistics", "decimal", "fractions", "random"
]  # 24 safe modules

BLOCKED_MODULES = [
    "os", "sys", "subprocess", "shutil", "pathlib",
    "socket", "http", "urllib.request", "ftplib", "smtplib",
    "ctypes", "cffi", "importlib", "pickle", "shelve",
    "sqlite3", "multiprocessing", "threading", "signal",
    "code", "compile", "exec", "eval", "ast",
    "inspect", "gc", "resource", "pty"
]  # 28 blocked modules
```

### Custom Import Hook

```python
def _safe_import(name, *args, **kwargs):
    """Custom __import__ that enforces module restrictions."""
    if name in BLOCKED_MODULES or any(name.startswith(b + ".") for b in BLOCKED_MODULES):
        raise ImportError(f"Module '{name}' is not allowed in sandbox")
    if name not in ALLOWED_MODULES and not any(name.startswith(a + ".") for a in ALLOWED_MODULES):
        raise ImportError(f"Module '{name}' is not in the allowed list")
    return original_import(name, *args, **kwargs)
```

### Safe Builtins

```python
SAFE_BUILTINS = {
    "print": print,
    "len": len,
    "range": range,
    "enumerate": enumerate,
    "zip": zip,
    "map": map,
    "filter": filter,
    "sorted": sorted,
    "reversed": reversed,
    "min": min,
    "max": max,
    "sum": sum,
    "abs": abs,
    "round": round,
    "isinstance": isinstance,
    "issubclass": issubclass,
    "type": type,
    "str": str,
    "int": int,
    "float": float,
    "bool": bool,
    "list": list,
    "dict": dict,
    "set": set,
    "tuple": tuple,
    "frozenset": frozenset,
    "bytes": bytes,
    "bytearray": bytearray,
    # Dangerous builtins removed: exec, eval, compile, __import__, open, globals, locals
}
```

### Memory Tracking

```python
def execute(self, code: str, context: dict = None):
    """Execute code in sandbox with memory tracking."""
    tracemalloc.start()

    try:
        # Static source analysis
        self._check_source(code)

        # Execute with restricted builtins
        exec_globals = {"__builtins__": SAFE_BUILTINS}
        if context:
            exec_globals.update(context)

        exec(compile(code, "<sandbox>", "exec"), exec_globals)

        return exec_globals.get("result")
    finally:
        current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
```

### Static Source Analysis

Before execution, the sandbox performs static analysis to detect dangerous patterns:

```python
def _check_source(self, code: str):
    """Static analysis to detect dangerous code patterns."""
    tree = ast.parse(code)
    for node in ast.walk(tree):
        # Block direct calls to dangerous functions
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in ("exec", "eval", "compile", "__import__", "open"):
                raise SecurityError(f"Blocked dangerous call: {node.func.id}")
```

---

## Writing a Plugin

### Plugin Structure

```python
# app/plugins/my_plugin.py

def my_tool(param1: str, param2: int = 10) -> str:
    """Your tool implementation."""
    # Only safe modules available (json, math, re, etc.)
    # No access to os, sys, subprocess, network, etc.
    result = f"Processed {param1} with factor {param2}"
    return result

def register_tools():
    """Required: return dict of tool_name → function."""
    return {
        "my_tool": my_tool,
    }
```

### Requirements

1. **File location:** Must be placed in `app/plugins/`
2. **Entry point:** Must export `register_tools()` returning `Dict[str, Callable]`
3. **Return type:** Tool functions must return `str` or JSON-serializable values
4. **Input:** Arguments passed as keyword arguments from `dict`
5. **No side effects:** Plugins should not modify global state
6. **No network access:** Subprocess environment has no API keys

### Testing

```python
# Test your plugin directly
from app.plugins.my_plugin import register_tools

tools = register_tools()
result = tools["my_tool"](param1="test", param2=5)
assert isinstance(result, str)
```

---

## Security Model

### Layer 1: Subprocess Isolation

| Property | Value |
|----------|-------|
| Isolation | OS-level process boundary |
| Environment | Stripped to PATH, TEMP, PYTHONPATH |
| Timeout | 30 seconds |
| Communication | Length-prefixed JSON IPC |
| Message limit | 10MB |
| Output capture | 4KB per stream |

### Layer 2: Sandbox Restrictions (In-Process)

| Property | Value |
|----------|-------|
| Allowed modules | 24 safe standard library modules |
| Blocked modules | 28 dangerous modules |
| Builtins | Safe subset (no exec/eval/compile/open) |
| Static analysis | AST-based dangerous call detection |
| Memory tracking | tracemalloc for monitoring |

### Layer 3: Plugin Protocol

| Property | Value |
|----------|-------|
| Wire format | 4-byte length prefix + JSON payload |
| Max message | 10MB |
| Encoding | UTF-8 |
| Error handling | Subprocess exit code + stderr capture |

---

## Example: Weather Plugin

**Location:** `app/plugins/weather_plugin.py`

```python
def get_weather(city: str) -> str:
    """Get current weather for a city (mock implementation)."""
    return json.dumps({
        "city": city,
        "temperature": 22,
        "condition": "Sunny",
        "humidity": 45,
    })

def register_tools():
    return {
        "get_weather": get_weather,
    }
```

---

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| Plugin directory | `app/plugins/` | Directory scanned for plugins |
| Subprocess timeout | 30s | Maximum plugin execution time |
| IPC max message | 10MB | Maximum message size |
| Output capture | 4KB | Per-stream output buffer limit |
| Allowed modules | 24 | Sandbox module whitelist size |
| Blocked modules | 28 | Sandbox module blocklist size |

---

## Failure Modes

| Failure | Impact | Recovery |
|---------|--------|----------|
| Plugin timeout | Tool returns error | Process killed, timeout error returned |
| Subprocess crash | Tool returns error | Exit code captured, error returned |
| Module not found | Plugin not registered | Discovery skips file, logged |
| No `register_tools()` | Plugin not registered | Discovery skips module |
| IPC parse error | Tool returns error | Protocol error returned to caller |
| Message too large | Tool returns error | PluginProtocolError raised |
| Blocked import | Tool execution fails | ImportError with descriptive message |
| Static analysis failure | Code not executed | SecurityError before execution |
| Memory exhaustion | Tracked but not limited | tracemalloc reports peak usage |

---

## Subprocess vs. Sandbox Comparison

The system provides two plugin execution modes. Choose based on trust level and performance needs:

| Criterion | Subprocess (PluginRunner) | Sandbox (SandboxRunner) |
|-----------|--------------------------|------------------------|
| **Isolation** | OS-level process boundary | Python-level import hook |
| **Startup latency** | ~50ms (new process) | ~1ms (same process) |
| **Use for untrusted code?** | Yes (recommended) | No (can be bypassed) |
| **Performance critical?** | No (process overhead) | Yes (in-process) |
| **Network access** | Blocked (env sanitized) | Blocked (socket import blocked) |
| **Debugging** | Inspect stderr output | Standard pdb works |
| **Resource limits** | 30s timeout; no CPU/memory cap | 30s timeout; no CPU/memory cap |

**Recommendation:** Use Subprocess for all third-party/untrusted plugins. Use Sandbox only for internal tools where startup latency matters and the code is fully trusted.

> **Security note:** Sandbox AST analysis catches direct `import os` or `exec()` calls but not obfuscated variants like `__import__('o'+'s')`. Subprocess isolation is the true security boundary.

---

## Debugging Plugins

### View Plugin Discovery Errors

```bash
# Run with debug logging to see which plugins loaded/failed
LOG_LEVEL=DEBUG python run.py

# Look for these log patterns:
# "Discovered plugin: weather_plugin" → success
# "Skipping module: broken_plugin — no register_tools()" → missing protocol
# "Plugin discovery error: syntax_error_plugin" → parse failure
```

### Inspect Plugin Execution Output

Plugin stderr is captured and returned in the tool result:

```python
# Response from a failed plugin call:
{
    "error": "Plugin execution failed",
    "stderr": "Traceback (most recent call last):\n  File ...\nValueError: invalid city name",
    "stdout": "",       # print() output captured here
    "exit_code": 1,
    "duration_ms": 150
}
```

### Tool Name Conflicts

If two plugins register a tool with the same name, the second registration overwrites the first (logged as WARNING). Use namespaced tool names to avoid conflicts:

```python
# Good: namespaced
def register_tools():
    return {"weather_v1_get_current": get_weather}

# Risky: generic name may collide
def register_tools():
    return {"get_data": get_data}
```

### Output Truncation

Plugin stdout/stderr is limited to 4KB per stream. Output exceeding this limit is silently truncated. For plugins returning large data:

```python
# Instead of returning 50KB inline:
def large_result_tool(query):
    result = compute_large_result(query)
    # Write to temp file, return path
    path = f"/tmp/result_{uuid4()}.json"
    with open(path, "w") as f:
        json.dump(result, f)
    return json.dumps({"result_file": path, "row_count": len(result)})
```
