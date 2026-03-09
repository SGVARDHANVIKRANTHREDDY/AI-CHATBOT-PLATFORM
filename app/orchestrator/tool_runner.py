from __future__ import annotations
import re
import time
from typing import AsyncIterator, Dict, Any, Callable
from app.shared.utils import get_logger, emit_observability_event
from app.shared.tracing import traced, start_span
from app.shared.monitoring import TOOL_CALL_COUNT
from app.tools.registry import TOOL_REGISTRY

_LOG = get_logger(__name__)

class StreamingToolRunner:
    """
    Detects and executes tool calls within a streaming LLM response.
    Expected format: <tool_call: name(arg1="val", arg2=123)>
    """
    
    TOOL_PATTERN = re.compile(r"<tool_call: (\w+)\((.*)\)>")

    def __init__(self, registry: Dict[str, Callable] = TOOL_REGISTRY):
        self.registry = registry

    async def run_tool(self, name: str, args_str: str) -> str:
        """Parses arguments and executes the tool from registry."""
        if name not in self.registry:
            TOOL_CALL_COUNT.labels(tool_name=name, status="not_found").inc()
            _LOG.warning(f"Tool '{name}' requested but not in registry.")
            return f"Error: Tool '{name}' not found."
        
        try:
            args = {}
            # Regex to match key="value", key='value', or key=123
            for match in re.finditer(r'(\w+)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|(\d+(?:\.\d+)?))', args_str):
                key = match.group(1)
                if match.group(2) is not None:
                    args[key] = match.group(2)
                elif match.group(3) is not None:
                    args[key] = match.group(3)
                else:
                    val = match.group(4)
                    args[key] = float(val) if "." in val else int(val)
            
            t0 = time.perf_counter()
            emit_observability_event(
                _LOG, event="tool.execute.start", category="tool",
                tool_name=name, args=str(args),
            )
            result = await self.registry[name](**args)
            elapsed = (time.perf_counter() - t0) * 1000
            TOOL_CALL_COUNT.labels(tool_name=name, status="ok").inc()
            emit_observability_event(
                _LOG, event="tool.execute.complete", category="tool",
                duration_ms=elapsed, tool_name=name,
            )
            return str(result)
        except Exception as e:
            TOOL_CALL_COUNT.labels(tool_name=name, status="error").inc()
            emit_observability_event(
                _LOG, event="tool.execute.error", category="tool",
                tool_name=name, error=str(e),
            )
            _LOG.error(f"Tool execution failed ({name}): {e}")
            return f"Error executing tool {name}: {e}"

    async def wrap_stream(self, stream: AsyncIterator[str]) -> AsyncIterator[str]:
        """Wraps an LLM response stream to intercept and execute tool calls."""
        buffer = ""
        # Maximum buffer size to avoid memory issues if tag is never closed
        MAX_BUFFER = 4096 
        
        async for chunk in stream:
            buffer += chunk
            
            if "<tool_call:" in buffer:
                if ">" in buffer:
                    match = self.TOOL_PATTERN.search(buffer)
                    if match:
                        # Yield content before the tool call
                        yield buffer[:match.start()]
                        
                        name = match.group(1)
                        args = match.group(2)
                        
                        yield f"\n[⚙️ Tool: {name}]\n"
                        result = await self.run_tool(name, args)
                        yield f"\n[🔍 Result] {result}\n"
                        
                        # Remove processed part from buffer
                        buffer = buffer[match.end():]
                        if buffer: yield buffer
                        buffer = ""
                    elif len(buffer) > MAX_BUFFER:
                        # Pattern didn't match and buffer is too large, flush it
                        yield buffer
                        buffer = ""
                else:
                    # Tag started but not finished, wait for next chunk
                    if len(buffer) > MAX_BUFFER:
                        yield buffer
                        buffer = ""
                    continue
            else:
                yield buffer
                buffer = ""
        
        if buffer:
            yield buffer
