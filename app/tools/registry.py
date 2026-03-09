from __future__ import annotations
import os
import ast
import operator
from typing import Dict, Any, Callable, Awaitable
from app.shared.utils import get_logger
from app.tools.web_search import get_web_context

_LOG = get_logger(__name__)

async def web_search_tool(query: str) -> str:
    """Performs a web search and returns text context."""
    try:
        context, _ = await get_web_context(query)
        return context or "No relevant search results found."
    except Exception as e:
        return f"Search Error: {e}"

async def calculator(expression: str) -> str:
    """Safely evaluates a mathematical expression."""
    try:
        def safe_eval(expr):
            operators = {ast.Add: operator.add, ast.Sub: operator.sub, 
                         ast.Mult: operator.mul, ast.Div: operator.truediv, 
                         ast.Pow: operator.pow, ast.BitXor: operator.xor,
                         ast.USub: operator.neg}
            def eval_(node):
                if isinstance(node, ast.Num): return node.n
                elif isinstance(node, ast.BinOp):
                    return operators[type(node.op)](eval_(node.left), eval_(node.right))
                elif isinstance(node, ast.UnaryOp):
                    return operators[type(node.op)](eval_(node.operand))
                else: raise TypeError(node)
            return eval_(ast.parse(expr, mode='eval').body)
        
        result = safe_eval(expression)
        return str(result)
    except Exception as e:
        return f"Calc Error: {e}"

async def file_reader(path: str) -> str:
    """Reads content from a local file within the data directory."""
    try:
        if ".." in path or path.startswith("/") or ":" in path:
            return "Error: Access denied. Paths must be relative to data partition."
        full_path = os.path.join("data", path)
        if not os.path.exists(full_path):
            return f"Error: File {path} not found."
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(5000)
    except Exception as e:
        return f"File Error: {e}"

async def database_query(query: str) -> str:
    """Executes a read-only query against the primary store (Simulated)."""
    return f"DB Result (Simulated): Found 4 records for query '{query}'."

async def code_execution(code: str) -> str:
    """Executes python code in a restricted sandbox (Simulated)."""
    _LOG.warning(f"Simulating code execution: {code[:100]}")
    return "Output: 42 (Simulated Execution)"

# tool name to function mapping
TOOL_REGISTRY: Dict[str, Callable[..., Awaitable[str]]] = {
    "web_search": web_search_tool,
    "calculator": calculator,
    "file_reader": file_reader,
    "database_query": database_query,
    "code_execution": code_execution,
}
