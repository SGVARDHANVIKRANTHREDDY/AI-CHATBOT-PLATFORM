# Tool Router & Tool System

> Complete documentation of the neural tool router, streaming tool runner, tool registry, and individual tool implementations.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Neural Tool Router](#neural-tool-router)
- [Streaming Tool Runner](#streaming-tool-runner)
- [Tool Registry](#tool-registry)
- [Built-in Tools](#built-in-tools)
  - [Web Search](#web-search)
  - [Calculator](#calculator)
  - [File Reader](#file-reader)
  - [Database Query](#database-query)
  - [Code Execution](#code-execution)
- [Tool Call Protocol](#tool-call-protocol)
- [Security Constraints](#security-constraints)
- [Configuration](#configuration)
- [Metrics](#metrics)
- [Failure Modes](#failure-modes)

---

## Overview

The tool system enables agents to interact with external services and perform computations. It consists of three layers:

1. **NeuralToolRouter** — Semantic selection of the best tool for a task using FAISS embedding similarity
2. **StreamingToolRunner** — Pattern-based tool call detection and execution from LLM output streams
3. **ToolRegistry** — Registry of tool implementations with input validation

---

## Architecture

```
Agent Output (possibly streaming)
     │
     ▼
┌──────────────────────────────┐
│ StreamingToolRunner          │
│ Buffer: 4KB sliding window   │
│ Pattern: <tool_call: ...>    │
│                              │
│ Detects tool call? ──No──▶ Return text
│     │ Yes                    │
│     ▼                        │
│ Parse name + args            │
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│ NeuralToolRouter             │
│ (optional pre-selection)     │
│ FAISS IndexFlatIP over       │
│ tool description embeddings  │
└────────┬─────────────────────┘
         │
         ▼
┌──────────────────────────────┐
│ ToolRegistry                 │
│ Dispatch to tool function    │
│ Input sanitization           │
│ Timeout enforcement          │
└──────────────────────────────┘
```

---

## Neural Tool Router

**Location:** `app/tool_router/neural_router.py`

The `NeuralToolRouter` uses semantic similarity to recommend tools for a given task description.

### How It Works

```python
class NeuralToolRouter:
    def __init__(self):
        self.embedding_service = EmbeddingService()
        self.index = faiss.IndexFlatIP(384)
        self.tool_names = []

    def register_tools(self, tools: Dict[str, str]):
        """Register tools with their descriptions for semantic matching."""
        descriptions = list(tools.values())
        self.tool_names = list(tools.keys())

        embeddings = self.embedding_service.embed(descriptions)
        self.index.add(np.array(embeddings, dtype=np.float32))

    def recommend_tool(self, task: str, threshold: float = 0.3) -> Optional[str]:
        """Recommend the best tool for a task based on semantic similarity."""
        query_embedding = self.embedding_service.embed([task])
        scores, indices = self.index.search(query_embedding, 1)

        if scores[0][0] >= threshold:
            return self.tool_names[indices[0][0]]
        return None

    def get_top_tools(self, task: str, top_k: int = 3) -> List[Tuple[str, float]]:
        """Get top-k tools ranked by relevance."""
        query_embedding = self.embedding_service.embed([task])
        scores, indices = self.index.search(query_embedding, top_k)

        return [
            (self.tool_names[idx], float(score))
            for score, idx in zip(scores[0], indices[0])
            if idx < len(self.tool_names)
        ]
```

### Threshold

The default threshold of **0.3** balances between:
- Recommending tools when clearly relevant
- Avoiding false positives on unrelated tasks

#### Why 0.3?

The threshold operates on cosine similarity of 384-dimensional embeddings (inner product on L2-normalized vectors). At this scale:

| Threshold | Behavior | False Positive Risk |
|-----------|----------|-------------------|
| 0.1 | Matches nearly everything | High — unrelated tasks get tool suggestions |
| 0.2 | Liberal matching | Moderate — some irrelevant matches |
| **0.3 (default)** | Balanced | Low — matches when task semantically relates to tool description |
| 0.5 | Conservative | Very low — only clear matches |
| 0.7 | Very strict | Near-zero — only near-exact description matches |

**Tuning advice:** If agents are using tools incorrectly (wrong tool for the task), increase to 0.4-0.5. If agents aren't finding tools they should use, decrease to 0.2.

---

## Streaming Tool Runner

**Location:** `app/orchestrator/tool_runner.py`

The `StreamingToolRunner` detects tool calls embedded in LLM output streams.

### Tool Call Pattern

```python
TOOL_PATTERN = re.compile(r'<tool_call:\s*(\w+)\(([^)]*)\)>')
```

This matches patterns like:
```
<tool_call: web_search(query="AI news")>
<tool_call: calculator(expression="2 + 2")>
<tool_call: file_reader(path="data/docs/guide.txt")>
```

### Buffer-Based Detection

```python
class StreamingToolRunner:
    BUFFER_SIZE = 4096  # 4KB sliding window

    async def process_stream(self, stream, state: AgentState):
        """Process an LLM output stream, detecting and executing tool calls."""
        buffer = ""
        full_response = ""

        async for chunk in stream:
            buffer += chunk
            full_response += chunk

            # Check buffer for tool call pattern
            match = self.TOOL_PATTERN.search(buffer)
            if match:
                tool_name = match.group(1)
                args_str = match.group(2)

                # Parse arguments
                args = self._parse_args(args_str)

                # Execute tool
                result = await self._execute_tool(tool_name, args, state)

                # Replace tool call in output with result
                buffer = buffer[:match.start()] + str(result) + buffer[match.end():]

            # Trim buffer to prevent unbounded growth
            if len(buffer) > self.BUFFER_SIZE:
                buffer = buffer[-self.BUFFER_SIZE:]

        return full_response
```

### Argument Parsing

```python
def _parse_args(self, args_str: str) -> dict:
    """Parse tool call arguments from string format."""
    # Handles: key="value", key=123, key=True
    args = {}
    for match in re.finditer(r'(\w+)\s*=\s*(?:"([^"]*)"|(\S+))', args_str):
        key = match.group(1)
        value = match.group(2) if match.group(2) is not None else match.group(3)
        args[key] = value
    return args
```

---

## Tool Registry

**Location:** `app/tools/tool_registry.py`

### Registry Structure

```python
TOOL_REGISTRY = {
    "web_search": {
        "function": web_search,
        "description": "Search the web for current information",
        "parameters": {"query": "str"},
    },
    "calculator": {
        "function": calculator,
        "description": "Evaluate mathematical expressions safely",
        "parameters": {"expression": "str"},
    },
    "file_reader": {
        "function": file_reader,
        "description": "Read contents of a file",
        "parameters": {"path": "str"},
    },
    "database_query": {
        "function": database_query,
        "description": "Query the database for information",
        "parameters": {"query": "str"},
    },
    "code_execution": {
        "function": code_execution,
        "description": "Execute Python code safely",
        "parameters": {"code": "str"},
    },
}
```

---

## Built-in Tools

### Web Search

**Location:** `app/tools/web_search.py`

Searches the web using DuckDuckGo and extracts clean content:

```python
async def web_search(query: str) -> str:
    """Search the web and return relevant results."""
    # 1. DuckDuckGo search
    results = ddg_search.text(query, max_results=5)

    # 2. URL canonicalization
    urls = [canonicalize_url(r["href"]) for r in results]

    # 3. Domain trust scoring
    trusted_urls = [u for u in urls if get_domain_trust(u) >= 0.3]

    # 4. Content extraction
    contents = []
    for url in trusted_urls[:3]:
        html = await fetch(url)
        text = extract_text(html)       # BeautifulSoup parsing
        clean = sanitize(text)           # Injection redaction
        contents.append(clean)

    return "\n\n".join(contents)
```

**Security features:**
- Domain trust scoring filters untrusted sources
- Content is sanitized to remove potential injection payloads
- URL canonicalization prevents open redirect attacks

### Calculator

Evaluates mathematical expressions using Python's AST module for safe parsing:

```python
def calculator(expression: str) -> str:
    """Safely evaluate a mathematical expression."""
    try:
        tree = ast.parse(expression, mode='eval')
        # Only allow mathematical operations
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp,
                                      ast.Num, ast.Add, ast.Sub, ast.Mult,
                                      ast.Div, ast.Pow, ast.Mod)):
                raise ValueError(f"Unsafe operation: {type(node).__name__}")
        result = eval(compile(tree, '<calc>', 'eval'))
        return str(result)
    except Exception as e:
        return f"Error: {e}"
```

**Security:** Uses AST whitelisting to prevent code injection — only pure mathematical operations are allowed.

### File Reader

Reads file contents with path traversal protection:

```python
def file_reader(path: str) -> str:
    """Read file contents with path safety checks."""
    # Resolve to absolute path
    resolved = Path(path).resolve()

    # Prevent path traversal
    allowed_base = Path("data").resolve()
    if not str(resolved).startswith(str(allowed_base)):
        raise SecurityError("Path traversal detected")

    return resolved.read_text(encoding='utf-8')
```

**Security:** The file reader:
1. Resolves the path to its canonical form (eliminates `..` traversal)
2. Verifies the resolved path is within the `data/` directory
3. Rejects any path that would escape the allowed directory tree

### Database Query

Mock implementation for structured data queries:

```python
def database_query(query: str) -> str:
    """Execute a database query (mock implementation)."""
    return f"Database query result for: {query}"
```

### Code Execution

Mock implementation for code execution:

```python
def code_execution(code: str) -> str:
    """Execute Python code (mock implementation)."""
    return f"Code execution result for: {code}"
```

---

## Tool Call Protocol

### From Agent to Tool

1. Agent generates text containing `<tool_call: name(args)>`
2. `StreamingToolRunner` detects the pattern via regex
3. Tool name and arguments are extracted
4. Tool function is looked up in `TOOL_REGISTRY`
5. Arguments are passed to the tool function
6. Result replaces the tool call in the output

### From Tool to Agent

1. Tool returns a string result
2. Result is appended to the agent's context as an "observation"
3. Agent receives the observation and can generate further output

### Multi-Turn Tool Loop

Agents (Research, Coding) support multi-turn tool interactions:

```
Turn 1: Agent → "<tool_call: web_search(query="AI news")>"
         Runner → Execute web_search → "Results: ..."
         Observation appended to context

Turn 2: Agent → "Based on the search results, <tool_call: web_search(query="GPT-5 release")>"
         Runner → Execute web_search → "Results: ..."
         Observation appended to context

Turn 3: Agent → "Here is a summary of the latest AI news..."
         No tool call → Return final response
```

Maximum 3 turns per agent invocation to prevent infinite loops.

---

## Security Constraints

| Constraint | Implementation |
|-----------|---------------|
| **Calculator injection** | AST whitelist — only math operations allowed |
| **Path traversal** | Canonical path resolution + directory boundary check |
| **Web content injection** | Sanitization + injection redaction via bleach |
| **Untrusted domains** | Domain trust scoring with 0.3 threshold |
| **Tool call budget** | AgentState tracks tool_call_count against max_tool_calls (10) |
| **Execution timeout** | AgentWatchdog enforces 30s wall-clock limit |
| **Buffer overflow** | StreamingToolRunner limits buffer to 4KB |

---

## Configuration

| Parameter | Default | Description |
|-----------|---------|-------------|
| `TOOL_PATTERN` | `<tool_call: name(args)>` | Regex for tool detection |
| Buffer size | 4096 bytes | Streaming buffer limit |
| Max tool turns | 3 | Per-agent tool loop limit |
| Max tool calls | 10 | Per-session tool call budget |
| Router threshold | 0.3 | Minimum similarity for tool recommendation |
| Search max results | 5 | DuckDuckGo result limit |
| Domain trust threshold | 0.3 | Minimum domain trust for web results |
| File reader base | `data/` | Allowed directory for file reads |

---

## Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `tool_execution_time_seconds` | Histogram | `tool_name` | Per-tool execution latency |
| `tool_calls_total` | Counter | `tool_name`, `status` | Total tool invocations |
| `tool_errors_total` | Counter | `tool_name` | Tool execution failures |
| `tool_router_latency_seconds` | Histogram | — | Neural tool router selection time |

---

## Failure Modes

| Failure | Impact | Recovery |
|---------|--------|----------|
| Tool function raises exception | Agent receives error string | Error message returned as observation |
| Tool call pattern not detected | Tool embedded in response text | Response returned as-is (degraded) |
| Neural router returns no match | No tool recommended | Agent proceeds without tool suggestion |
| Tool call budget exceeded | Agent cannot invoke more tools | AgentState.is_complete() returns True |
| Web search timeout | No search results | Error returned to agent |
| DuckDuckGo rate limit | Temporary search failure | Exponential backoff retry |
