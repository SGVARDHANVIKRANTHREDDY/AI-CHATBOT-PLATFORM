# Development Guide

> Developer onboarding guide â€” how to install, run, test, and extend the platform by adding agents, tools, plugins, memory providers, and LLM providers.

---

## Table of Contents

- [Getting Started](#getting-started)
- [Running the Project](#running-the-project)
- [Running Tests](#running-tests)
- [Architecture Overview](#architecture-overview)
- [Adding a New Agent](#adding-a-new-agent)
- [Adding a New Tool](#adding-a-new-tool)
- [Adding a New Plugin](#adding-a-new-plugin)
- [Adding an LLM Provider](#adding-an-llm-provider)
- [Adding a Vector Store Backend](#adding-a-vector-store-backend)
- [Extending Memory](#extending-memory)
- [Adding an API Endpoint](#adding-an-api-endpoint)
- [Adding a Celery Worker Task](#adding-a-celery-worker-task)
- [Debugging](#debugging)

---

## Getting Started

### Prerequisites

| Tool | Version | Purpose |
|------|---------|---------|
| Python | 3.12+ | Runtime |
| Redis | 5+ | Session cache, rate limiter, Celery broker |
| PostgreSQL | 15+ | Conversation history (optional in dev) |
| Git | 2.30+ | Version control |
| Docker | 24+ | Container deployment (optional) |

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/your-org/chatbot.git
cd chatbot

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate   # Linux/macOS
.venv\Scripts\activate      # Windows

# 3. Install production dependencies
pip install -r requirements.txt

# 4. Install development dependencies
pip install ruff mypy bandit pytest pytest-asyncio pytest-cov

# 5. Configure environment
cp .env.example .env
# Edit .env â€” set at minimum: HF_TOKEN and REDIS_URL
```

### Environment Variables (Minimum for Development)

```bash
HF_TOKEN=hf_your_token_here          # HuggingFace API token
REDIS_URL=redis://localhost:6379/0    # Redis connection
DEBUG=true                            # Enable debug mode + hot-reload
```

See [CONFIGURATION.md](CONFIGURATION.md) for the full list of settings.

---

## Running the Project

### Start Redis (required)

```bash
# Docker (simplest)
docker run -d --name redis -p 6379:6379 redis:7-alpine

# Or install natively and run
redis-server
```

### Start the API Server

```bash
python run.py
# Server starts at http://localhost:8000
# Swagger docs at http://localhost:8000/docs
# Health check at http://localhost:8000/healthz
```

### Start Celery Workers (for background tasks)

```bash
# In a separate terminal
celery -A workers.celery_app worker --loglevel=info

# With beat scheduler (for periodic tasks)
celery -A workers.celery_app worker --beat --loglevel=info
```

### Docker Quick Start

```bash
cd infra/docker
docker compose up -d
# Starts: api (8000), worker, redis (6379), postgres (5432), prometheus (9090)
```

### Verify Installation

```bash
# Health check
curl http://localhost:8000/healthz

# Send a test request
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Hello, how are you?"}'
```

---

## Running Tests

### Quick Commands

```bash
# Run all tests
pytest

# Unit tests only
pytest tests/unit/ -v

# Integration tests
pytest tests/integration/ -v

# End-to-end tests
pytest tests/e2e/ -v

# Chaos tests
pytest tests/chaos/ -v

# With coverage report
pytest --cov=app --cov-report=html

# Run a specific test file
pytest tests/test_api.py -v

# Run a specific test
pytest tests/unit/test_llm.py::TestHuggingFaceProvider::test_ask_returns_response -v
```

### Linting and Type Checking

```bash
# Lint (Ruff)
ruff check .

# Auto-fix lint issues
ruff check --fix .

# Format code
ruff format .

# Type check (mypy)
mypy app/ --ignore-missing-imports

# Security scan (bandit)
bandit -r app/ -c pyproject.toml

# Dependency audit
pip-audit
```

---

## Architecture Overview

```
Request â†’ API Layer â†’ Orchestrator â†’ Agents/Tools/RAG â†’ LLM â†’ Response
                          â”‚
                    â”Œâ”€â”€â”€â”€â”€â”´â”€â”€â”€â”€â”€â”€â”
                    â”‚            â”‚
              Memory Layer  Reliability Layer
```

Key extension points:
- **Agents:** `app/agents/` â€” Add reasoning capabilities
- **Tools:** `app/tools/` â€” Add callable functions
- **Plugins:** `app/plugins/` â€” Add sandboxed external plugins
- **LLM Providers:** `app/llm/providers/` â€” Add model backends
- **Vector Stores:** `app/vector_memory/` â€” Add vector DB backends

---

## Adding a New Agent

### 1. Create the Agent Class

Create `app/agents/my_agent.py`:

```python
from __future__ import annotations
from typing import Any, Dict
from app.agents.agent_state import AgentState

class MyAgent:
    """Agent specialized in <purpose>."""

    AGENT_NAME = "my_agent"

    def __init__(self, llm_provider, tools=None):
        self.llm = llm_provider
        self.tools = tools or []

    async def execute(self, task: str, state: AgentState) -> Dict[str, Any]:
        """Execute the agent's reasoning on the given task."""
        prompt = self._build_prompt(task, state)
        response = await self.llm.ask(prompt)
        return {
            "agent": self.AGENT_NAME,
            "result": response,
            "confidence": self._assess_confidence(response),
        }

    def _build_prompt(self, task: str, state: AgentState) -> str:
        return f"You are a specialist in <domain>. Task: {task}"

    def _assess_confidence(self, response: str) -> float:
        return 0.8  # Implement confidence assessment
```

### 2. Register in AgentRouter

In `app/agents/agent_router.py`, add the new agent to the routing logic:

```python
from app.agents.my_agent import MyAgent

class AgentRouter:
    def __init__(self, ...):
        self.agents["my_agent"] = MyAgent(llm_provider, tools)

    def route(self, query: str) -> str:
        # Add routing condition
        if self._needs_my_agent(query):
            return "my_agent"
```

### 3. Add Tests

Create `tests/unit/test_my_agent.py`:

```python
import pytest
from app.agents.my_agent import MyAgent

class TestMyAgent:
    async def test_execute_returns_result(self, mock_llm):
        agent = MyAgent(mock_llm)
        result = await agent.execute("test task", state)
        assert result["agent"] == "my_agent"
        assert "result" in result
```

---

## Adding a New Tool

### 1. Define the Tool Function

Add to `app/tools/tool_registry.py` or create a new module:

```python
from app.tools.tool_registry import ToolRegistry

@ToolRegistry.register(
    name="calculator",
    description="Perform mathematical calculations",
    parameters={
        "expression": {"type": "string", "description": "Math expression to evaluate"}
    }
)
async def calculator(expression: str) -> str:
    """Evaluate a mathematical expression safely."""
    # Use ast.literal_eval or a safe math parser â€” never eval()
    import ast
    result = ast.literal_eval(expression)
    return str(result)
```

### 2. Register the Tool Embedding

The `NeuralToolRouter` automatically indexes tool descriptions:

```python
# In app/tool_router/neural_router.py
# Tool descriptions are embedded at startup for semantic matching
# No manual registration needed beyond @ToolRegistry.register
```

### 3. Test the Tool

```python
class TestCalculator:
    async def test_basic_calculation(self):
        result = await calculator("2 + 3")
        assert result == "5"

    async def test_invalid_expression(self):
        with pytest.raises(ValueError):
            await calculator("import os")  # Security: reject non-math
```

---

## Adding a New Plugin

### 1. Implement the Plugin Protocol

Create `app/plugins/my_plugin.py`:

```python
from app.plugins.plugin_protocol import PluginProtocol

class MyPlugin(PluginProtocol):
    """Plugin for <purpose>."""

    name = "my_plugin"
    version = "1.0.0"
    description = "Does something useful"

    async def initialize(self) -> None:
        """Called once when plugin is loaded."""
        pass

    async def execute(self, params: dict) -> dict:
        """Execute the plugin's main functionality."""
        return {"result": "value"}

    async def health_check(self) -> bool:
        return True
```

### 2. Register the Plugin

In `app/plugins/registry.py`:

```python
from app.plugins.my_plugin import MyPlugin

registry.register(MyPlugin())
```

### 3. Plugin Sandbox

Plugins run in a sandboxed environment (`app/plugins/sandbox/`):
- Restricted imports (no `os`, `sys`, `socket`)
- No file system access
- Memory limits enforced
- Execution timeout

---

## Adding an LLM Provider

### 1. Implement the Base Interface

Create `app/llm/providers/my_provider.py`:

```python
from app.llm.base import LLMProvider

class MyProvider(LLMProvider):
    """Provider for <model service>."""

    PROVIDER_NAME = "my_provider"

    def __init__(self, api_key: str, model: str, **kwargs):
        self.api_key = api_key
        self.model = model

    async def ask(
        self,
        prompt: str,
        system_prompt: str = "",
        max_tokens: int = 1024,
        temperature: float = 0.7,
        **kwargs,
    ) -> str:
        """Send prompt to the model and return response text."""
        # Implement API call
        ...

    async def health_check(self) -> bool:
        """Check if the provider is responsive."""
        try:
            await self.ask("test", max_tokens=1)
            return True
        except Exception:
            return False
```

### 2. Wire into FallbackProvider

In `app/llm/providers/fallback_provider.py`:

```python
from app.llm.providers.my_provider import MyProvider

# Add as a fallback tier
providers = [
    primary_provider,
    MyProvider(api_key=settings.MY_API_KEY, model="model-name"),
]
```

### 3. Add Settings

In `app/config/settings.py`:

```python
class Settings(BaseSettings):
    # ... existing fields ...
    MY_API_KEY: str = ""
    MY_MODEL: str = "default-model"
```

### 4. Add Health Check

Ensure `health_check()` probes the provider's availability without consuming quota:

```python
async def health_check(self) -> bool:
    try:
        # Prefer a lightweight status endpoint over a full inference call
        response = await self.client.get("/status")
        return response.status_code == 200
    except Exception:
        return False
```

---

## Adding a Vector Store Backend

### 1. Implement the VectorStore Interface

Create `app/vector_memory/my_store.py`:

```python
from app.vector_memory.base import VectorStore, VectorRecord, SearchResult
import numpy as np
from typing import List, Optional, Dict, Any

class MyVectorStore(VectorStore):
    """Custom vector store backend."""

    async def initialize(self) -> None:
        """Connect to the backend and create collections/indices."""
        ...

    async def add_embedding(
        self, id: str, embedding: np.ndarray, text: str, metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """Insert a single vector with its text and metadata."""
        ...

    async def search(
        self, query_embedding: np.ndarray, top_k: int = 5, filters: Optional[Dict] = None
    ) -> List[SearchResult]:
        """Return the top_k most similar vectors."""
        ...

    async def delete(self, ids: List[str]) -> int:
        """Delete vectors by ID. Return count deleted."""
        ...

    async def batch_insert(self, records: List[VectorRecord]) -> int:
        """Bulk insert records. Return count inserted."""
        ...

    async def count(self) -> int:
        """Total number of vectors stored."""
        ...

    async def close(self) -> None:
        """Release connections."""
        ...
```

### 2. Wire Into the Facade

In the vector memory facade, add the new backend option:

```python
# app/vector_memory/vector_store.py
from app.vector_memory.my_store import MyVectorStore

if settings.VECTOR_STORE == "my_store":
    store = MyVectorStore(config=settings)
```

### 3. Test the Backend

```python
class TestMyVectorStore:
    async def test_round_trip(self):
        store = MyVectorStore()
        await store.initialize()
        embedding = np.random.rand(384).astype(np.float32)
        await store.add_embedding("doc1", embedding, "test text")
        results = await store.search(embedding, top_k=1)
        assert len(results) == 1
        assert results[0].id == "doc1"
```

---

## Extending Memory

### Adding a New Memory Layer

The `UnifiedMemoryController` in `app/memory/memory_controller.py` aggregates all memory sources. To add a new layer:

### 1. Create the memory source

```python
# app/memory/my_memory_source.py
class MyMemorySource:
    async def store(self, session_id: str, key: str, value: str) -> None:
        ...

    async def retrieve(self, query: str, limit: int = 5) -> list:
        ...
```

### 2. Register in UnifiedMemoryController

```python
class UnifiedMemoryController:
    def __init__(self, ...):
        self.my_source = MyMemorySource()

    async def get_unified_context(self, query, session_id, ...):
        # Fetch from new source alongside existing ones
        my_data = await self.my_source.retrieve(query)
        # Include in authority resolution
```

### 3. Update the Memory Authority Resolver

Add your source to the `MemorySource` enum in `app/memory/authority.py`:

```python
class MemorySource(Enum):
    VECTOR = 1
    KNOWLEDGE_GRAPH = 2
    CONVERSATION = 3
    MY_SOURCE = 4       # Add new source with its authority rank
```

---

## Adding an API Endpoint

### 1. Create a Route Module

Create `app/api/routes/my_route.py`:

```python
from fastapi import APIRouter, Depends
from app.api.dependencies.providers import get_llm_provider

router = APIRouter(prefix="/api/v1/my-feature", tags=["my-feature"])

@router.post("/action")
async def perform_action(
    request: MyRequest,
    llm=Depends(get_llm_provider),
):
    result = await llm.ask(request.prompt)
    return {"ok": True, "result": result}
```

### 2. Create the Schema

Create `app/api/schemas/my_schema.py`:

```python
from pydantic import BaseModel, Field

class MyRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=10000)

class MyResponse(BaseModel):
    ok: bool
    result: str
```

### 3. Register the Router

In `app/api/main.py`:

```python
from app.api.routes.my_route import router as my_router

app.include_router(my_router)
```

---

## Adding a Celery Worker Task

### 1. Define the Task

Create or add to a worker module in `workers/`:

```python
# workers/my_worker.py
from workers.celery_app import celery_app

@celery_app.task(
    name="my_custom_task",
    bind=True,
    max_retries=3,
    soft_time_limit=300,
    time_limit=360,
)
def my_custom_task(self, param1: str, param2: int = 10):
    """Describe what this task does."""
    try:
        # Task logic here
        result = process(param1, param2)
        return {"status": "ok", "result": result}
    except Exception as exc:
        self.retry(exc=exc, countdown=60)
```

### 2. Schedule as a Periodic Task (Optional)

In `workers/celery_app.py`, add to the beat schedule:

```python
app.conf.beat_schedule["my-periodic-task"] = {
    "task": "my_custom_task",
    "schedule": crontab(hour=6, minute=0),  # Daily at 06:00 UTC
    "args": ("default_param",),
}
```

### 3. Trigger from API (Optional)

```python
from workers.my_worker import my_custom_task

@router.post("/trigger-task")
async def trigger_task(request: TaskRequest):
    task = my_custom_task.delay(request.param1, request.param2)
    return {"task_id": task.id, "status": "queued"}
```

---

## Debugging

### Structured Logging

The platform uses structured JSON logging (ELK-compatible). Enable verbose output:

```bash
# Set debug mode in .env
LOG_LEVEL=DEBUG
```

Use the shared logger throughout your code:

```python
from app.shared.utils import get_logger
logger = get_logger(__name__)

logger.info("Operation completed", extra={
    "session_id": session_id,
    "duration_ms": elapsed * 1000,
    "component": "my_feature",
})
```

### OpenTelemetry Tracing

```python
from app.shared.tracing import get_tracer
tracer = get_tracer(__name__)

with tracer.start_as_current_span("my_operation") as span:
    span.set_attribute("custom.key", "value")
    result = await do_work()
```

View traces in Jaeger at `http://localhost:16686`.

### Debugging Agent Execution

```python
# Enable agent-level debug logging
import logging
logging.getLogger("app.agents").setLevel(logging.DEBUG)

# The AgentRouter logs routing decisions
# The TaskGraph logs node execution order and dependencies
# The CriticAgent logs scoring breakdowns
```

### Debugging the RAG Pipeline

```python
# Enable RAG debug logging
logging.getLogger("app.rag").setLevel(logging.DEBUG)

# The pipeline logs each stage:
#   1. Query preprocessing
#   2. Embedding generation
#   3. Vector search (top-k candidates)
#   4. Re-ranking scores
#   5. Context assembly
#   6. LLM prompt construction
```

### Debugging Memory Systems

```python
# Log memory operations
logging.getLogger("app.memory").setLevel(logging.DEBUG)

# Inspect unified memory state
from app.memory import UnifiedMemoryController
controller = UnifiedMemoryController(session_id="test")
state = await controller.get_context()
print(state)  # Shows short-term, long-term, graph sources
```

### Debugging with VS Code

Add this to `.vscode/launch.json`:

```json
{
    "version": "0.2.0",
    "configurations": [
        {
            "name": "Nimbus API",
            "type": "debugpy",
            "request": "launch",
            "module": "uvicorn",
            "args": ["app.api.main:app", "--reload", "--port", "8000"],
            "env": {"LOG_LEVEL": "DEBUG"},
            "justMyCode": false
        }
    ]
}
```

### Debugging in Docker

```bash
# Tail API logs
docker compose logs -f api 2>&1 | python -m json.tool

# Attach to a running container
docker compose exec api bash

# Run tests inside container
docker compose exec api pytest tests/ -x -v
```

### Celery Worker Debugging

```bash
# Start worker in foreground with debug logging
celery -A workers.celery_app worker --loglevel=debug --concurrency=1

# Inspect active tasks
celery -A workers.celery_app inspect active

# Inspect worker stats
celery -A workers.celery_app inspect stats

# Purge all pending tasks (use with caution)
celery -A workers.celery_app purge
```

### Common Issues and Solutions

| Symptom | Cause | Fix |
|---------|-------|-----|
| `FAISS index not found` | Vector index not built | Run `python -m workers.indexing_worker` |
| `Redis ConnectionError` | Redis not running | `docker compose up redis -d` |
| `LLM timeout` | Provider rate limit | Check `RATE_LIMIT_*` settings, enable fallback |
| `Memory conflict` | Contradictory facts | Check `MemoryAuthorityResolver` trust scores |
| `Plugin not loading` | Missing manifest | Verify `plugin.yaml` exists with correct schema |
| `Agent loop detected` | Circular delegation | Review `AgentRouter` routing rules |
| `Embedding dimension mismatch` | Model changed | Rebuild vector index with new model |

### Performance Profiling

```bash
# Profile with cProfile
python -m cProfile -o output.prof run.py

# Visualize with snakeviz
pip install snakeviz
snakeviz output.prof

# Memory profiling
pip install memray
python -m memray run run.py
python -m memray flamegraph memray-output.bin
```

---

## End-to-End Example: Building a Translation Agent

This walkthrough ties together agent creation, tool registration, memory integration, and testing into one complete feature.

### Goal

Build a `TranslationAgent` that:
1. Accepts a translation task from the planner
2. Uses a `detect_language` tool to identify the source language
3. Calls the LLM for translation
4. Stores the translation pair in memory for future recall

### Step 1: Create the Agent

```python
# app/agents/translation_agent.py
from app.agents.agent_state import AgentState
from typing import Any, Dict

class TranslationAgent:
    AGENT_NAME = "translation"

    def __init__(self, llm_provider, tools=None):
        self.llm = llm_provider
        self.tools = tools or []

    async def execute(self, task: str, state: AgentState) -> Dict[str, Any]:
        # Step 1: Detect language using registered tool
        source_lang = await self._detect_language(task, state)

        # Step 2: Build translation prompt
        prompt = (
            f"Translate the following from {source_lang} to English.\n"
            f"Text: {task}\n"
            f"Translation:"
        )

        # Step 3: Call LLM
        translation = await self.llm.ask(prompt)

        return {
            "agent": self.AGENT_NAME,
            "result": translation,
            "source_language": source_lang,
            "confidence": 0.85,
        }

    async def _detect_language(self, text: str, state: AgentState) -> str:
        for tool in self.tools:
            if tool.name == "detect_language":
                return await tool.execute({"text": text})
        return "unknown"
```

### Step 2: Register the Tool

```python
# app/tools/tool_registry.py  (add to existing registry)
@ToolRegistry.register(
    name="detect_language",
    description="Detect the language of input text",
    parameters={"text": {"type": "string", "description": "Text to analyze"}},
)
async def detect_language(text: str) -> str:
    """Detect language from a text sample."""
    # Simple heuristic; in production use a library like langdetect
    prompt = f"What language is this text written in? Reply with just the language name.\n\n{text}"
    # This would call the LLM or use a lightweight model
    return "French"  # Placeholder
```

### Step 3: Wire into AgentRouter

```python
# app/agents/agent_router.py  (add to __init__ and route methods)
from app.agents.translation_agent import TranslationAgent

class AgentRouter:
    def __init__(self, ...):
        # ... existing agents ...
        self.agents["translation"] = TranslationAgent(llm_provider, tools)

    def route(self, query: str) -> str:
        if any(kw in query.lower() for kw in ["translate", "translation", "en français"]):
            return "translation"
        # ... existing routing ...
```

### Step 4: Store in Memory

```python
# In the orchestrator, after agent execution:
if result.get("agent") == "translation":
    await memory_controller.store(
        session_id=session_id,
        key=f"translation:{result['source_language']}",
        value=result["result"],
    )
```

### Step 5: Write Tests

```python
# tests/unit/test_translation_agent.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from app.agents.translation_agent import TranslationAgent
from app.agents.agent_state import AgentState

class TestTranslationAgent:
    @pytest.fixture
    def mock_llm(self):
        llm = AsyncMock()
        llm.ask.return_value = "Hello, how are you?"
        return llm

    @pytest.fixture
    def mock_tool(self):
        tool = MagicMock()
        tool.name = "detect_language"
        tool.execute = AsyncMock(return_value="French")
        return tool

    @pytest.mark.asyncio
    async def test_translate_french_to_english(self, mock_llm, mock_tool):
        agent = TranslationAgent(mock_llm, tools=[mock_tool])
        state = AgentState(query="Bonjour, comment allez-vous?")

        result = await agent.execute("Bonjour, comment allez-vous?", state)

        assert result["agent"] == "translation"
        assert result["source_language"] == "French"
        assert result["result"] == "Hello, how are you?"
        mock_llm.ask.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_tool_defaults_to_unknown(self, mock_llm):
        agent = TranslationAgent(mock_llm, tools=[])
        state = AgentState(query="test")

        result = await agent.execute("test", state)
        assert result["source_language"] == "unknown"
```

### Step 6: Verify

```bash
# Run the new test
pytest tests/unit/test_translation_agent.py -v

# Lint
ruff check app/agents/translation_agent.py

# Start server and test manually
python run.py
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Translate: Bonjour, comment allez-vous?"}'
```

This pattern (Agent → Tool → Router → Memory → Test) applies to any new capability you add.

---

## Extension Checklist

Use this checklist whenever you add a new component:

```
□ Created the implementation file in the correct directory
□ Registered the component (AgentRouter / ToolRegistry / PluginRegistry / etc.)
□ Added settings to app/config/settings.py (if configurable)
□ Wrote unit tests with mocked dependencies
□ Wrote integration test (if component talks to external services)
□ Updated documentation (this guide + relevant subsystem doc)
□ Ran full linting: ruff check . && ruff format --check .
□ Ran tests: pytest tests/unit/ -v
□ Verified no broken existing tests: pytest tests/ -v
```
