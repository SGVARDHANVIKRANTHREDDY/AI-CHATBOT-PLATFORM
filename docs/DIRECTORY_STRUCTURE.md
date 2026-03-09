# Directory Structure

> Complete file and folder listing of the AI Chatbot Platform repository with role descriptions for every module.

```
chatbot/
├── .dockerignore
├── .github/
│   └── workflows/
│       └── ci-cd.yml
├── .pytest_cache/
├── .venv/
├── .vscode/
├── coderead.txt
├── pyproject.toml
├── pytest.ini
├── requirements.txt
├── run.py
│
├── app/
│   ├── __init__.py
│   │
│   ├── agents/
│   │   ├── agent_router.py
│   │   ├── agent_state.py
│   │   ├── coding_agent.py
│   │   ├── critic_agent.py
│   │   ├── planner_agent.py
│   │   ├── reasoning_agent.py
│   │   ├── research_agent.py
│   │   └── task_graph.py
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   ├── main.py
│   │   ├── dependencies/
│   │   │   ├── __init__.py
│   │   │   └── providers.py
│   │   ├── middleware/
│   │   │   ├── __init__.py
│   │   │   ├── auth.py
│   │   │   ├── correlation.py
│   │   │   ├── jwt_auth.py
│   │   │   ├── rate_limit.py
│   │   │   ├── request_protection.py
│   │   │   └── token_bucket.py
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   └── chat.py
│   │   └── schemas/
│   │       ├── __init__.py
│   │       └── chat.py
│   │
│   ├── cache/
│   │   └── semantic_cache.py
│   │
│   ├── config/
│   │   ├── __init__.py
│   │   ├── logging_config.py
│   │   └── settings.py
│   │
│   ├── evaluation/
│   │   ├── dataset_builder.py
│   │   └── response_grader.py
│   │
│   ├── knowledge_graph/
│   │   ├── entity_extractor.py
│   │   ├── graph_store.py
│   │   └── trust.py
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── base.py
│   │   ├── model_router.py
│   │   ├── prompts/
│   │   │   └── __init__.py
│   │   ├── providers/
│   │   │   ├── __init__.py
│   │   │   ├── fallback_provider.py
│   │   │   ├── huggingface_provider.py
│   │   │   ├── local_provider.py
│   │   │   └── openai_provider.py
│   │   └── tokenizer/
│   │       ├── __init__.py
│   │       └── adaptive_budget.py
│   │
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── authority.py
│   │   ├── conversation_store.py
│   │   ├── memory_controller.py
│   │   ├── memory_service.py
│   │   ├── session_cache.py
│   │   └── summarizer.py
│   │
│   ├── orchestrator/
│   │   ├── __init__.py
│   │   ├── chat_orchestrator.py
│   │   ├── context_builder.py
│   │   ├── pipeline.py
│   │   ├── tool_runner.py
│   │   └── watchdog.py
│   │
│   ├── plugins/
│   │   ├── plugin_protocol.py
│   │   ├── plugin_runtime.py
│   │   ├── registry.py
│   │   ├── weather_plugin.py
│   │   └── sandbox/
│   │       ├── __init__.py
│   │       └── sandbox_runner.py
│   │
│   ├── prompts/
│   │   ├── __init__.py
│   │   └── evolution/
│   │       ├── __init__.py
│   │       ├── manager.py
│   │       └── models.py
│   │
│   ├── rag/
│   │   ├── __init__.py
│   │   ├── crawler.py
│   │   ├── reranker.py
│   │   └── retriever.py
│   │
│   ├── reasoning_graph/
│   │   ├── __init__.py
│   │   ├── engine.py
│   │   └── models.py
│   │
│   ├── reliability/
│   │   ├── __init__.py
│   │   ├── circuit_breaker.py
│   │   ├── failure_tracker.py
│   │   ├── load_guard.py
│   │   ├── response_guard.py
│   │   ├── retry_policy.py
│   │   └── timeout_controller.py
│   │
│   ├── security/
│   │   ├── __init__.py
│   │   ├── content_safety.py
│   │   ├── prompt_guard.py
│   │   └── refusal_guard.py
│   │
│   ├── shared/
│   │   ├── __init__.py
│   │   ├── exceptions.py
│   │   ├── monitoring.py
│   │   ├── tracing.py
│   │   ├── types.py
│   │   └── utils.py
│   │
│   ├── storage/
│   │   └── __init__.py
│   │
│   ├── swarm/
│   │   ├── __init__.py
│   │   └── execution.py
│   │
│   ├── tool_router/
│   │   ├── __init__.py
│   │   └── neural_router.py
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   ├── registry.py
│   │   └── web_search.py
│   │
│   └── vector_memory/
│       ├── base.py
│       ├── embeddings.py
│       ├── faiss_store.py
│       ├── maintenance.py
│       ├── memory_retriever.py
│       ├── migration.py
│       ├── qdrant_store.py
│       └── vector_store.py
│
├── data/
│   ├── processed_docs/          (empty)
│   ├── prompts/                 (empty)
│   ├── raw_docs/                (empty)
│   └── vector_index/
│       ├── evaluation_dataset.json
│       ├── memory/
│       │   ├── episodic/
│       │   │   ├── memory.index
│       │   │   └── memory_meta.json
│       │   ├── profile/         (empty)
│       │   └── semantic/        (empty)
│       └── semantic_cache/      (empty)
│
├── docs/
│   ├── AGENT_SYSTEM.md
│   ├── API_REFERENCE.md
│   ├── ARCHITECTURE.md
│   ├── CONFIGURATION.md
│   ├── CONTRIBUTING.md
│   ├── DEPLOYMENT.md
│   ├── DEVELOPMENT_GUIDE.md
│   ├── DIRECTORY_STRUCTURE.md
│   ├── ERROR_HANDLING.md
│   ├── INSTALLATION.md
│   ├── KNOWLEDGE_GRAPH.md
│   ├── MEMORY_SYSTEM.md
│   ├── OBSERVABILITY.md
│   ├── PERFORMANCE.md
│   ├── PLUGIN_SYSTEM.md
│   ├── PROMPT_EVOLUTION.md
│   ├── QUICKSTART.md
│   ├── RAG_PIPELINE.md
│   ├── README.md
│   ├── RELIABILITY_LAYER.md
│   ├── SCALING.md
│   ├── SECURITY_MODEL.md
│   ├── SWARM_EXECUTION.md
│   ├── SYSTEM_OVERVIEW.md
│   ├── TESTING.md
│   ├── TOOL_ROUTER.md
│   └── VECTOR_DATABASE.md
│
├── infra/
│   ├── docker/
│   │   ├── Dockerfile
│   │   └── docker-compose.yml
│   └── monitoring/
│       ├── docker-compose.observability.yml
│       ├── filebeat.yml
│       ├── prometheus.yml
│       └── grafana/
│           ├── dashboards/
│           │   ├── ai-platform-agent-deepdive.json
│           │   └── ai-platform-overview.json
│           └── provisioning/
│               ├── dashboards/
│               │   └── dashboards.yml
│               └── datasources/
│                   └── datasources.yml
│
├── tests/
│   ├── test_answer_contract_and_gating.py
│   ├── test_api.py
│   ├── test_api_protection.py
│   ├── test_content_safety.py
│   ├── test_memory_authority.py
│   ├── test_plugin_isolation.py
│   ├── test_rag.py
│   ├── test_semantic_cache.py
│   ├── test_watchdog.py
│   ├── chaos/
│   │   ├── __init__.py
│   │   ├── fault_injectors.py
│   │   ├── framework.py
│   │   └── test_chaos.py
│   ├── e2e/
│   │   ├── __init__.py
│   │   ├── test_concurrent_load.py
│   │   ├── test_multi_agent_reasoning.py
│   │   ├── test_rag_retrieval.py
│   │   ├── test_swarm_execution.py
│   │   └── test_tool_routing.py
│   ├── integration/
│   │   ├── __init__.py
│   │   ├── conftest.py
│   │   ├── test_brain_layer.py
│   │   ├── test_multi_agent_system.py
│   │   ├── test_performance.py
│   │   ├── test_stress_multi_agent.py
│   │   └── test_vector_store.py
│   └── unit/
│       └── test_llm.py
│
├── tmp/                         (empty)
│
├── tools/
│   └── generate_coderead.py
│
└── workers/
    ├── __init__.py
    ├── celery_app.py
    ├── indexing_worker.py
    ├── ingestion_worker.py
    ├── knowledge_builder.py
    └── maintenance_worker.py
```

---

## Module Descriptions

### Root Files

| File | Purpose |
|------|---------|
| `run.py` | Application entry point — starts Uvicorn with FastAPI app |
| `pyproject.toml` | Project metadata, ruff/mypy/bandit/pytest configuration |
| `pytest.ini` | Pytest configuration and markers |
| `requirements.txt` | Python package dependencies |
| `coderead.txt` | Generated codebase summary |

### `app/agents/` — Multi-Agent System

| File | Purpose |
|------|---------|
| `agent_router.py` | Routes queries to the best-fit specialist agent |
| `agent_state.py` | Shared state object passed through agent execution |
| `planner_agent.py` | Decomposes complex queries into a DAG of subtasks |
| `reasoning_agent.py` | Handles analytical and logical reasoning tasks |
| `coding_agent.py` | Generates, reviews, and explains code |
| `research_agent.py` | Synthesizes information from multiple sources |
| `critic_agent.py` | Evaluates and scores agent outputs for quality |
| `task_graph.py` | DAG data structure for multi-step task execution |

### `app/api/` — HTTP Layer

| File | Purpose |
|------|---------|
| `main.py` | FastAPI application factory — mounts routers and middleware |
| `dependencies/providers.py` | Dependency injection for LLM, memory, and RAG services |
| `middleware/auth.py` | API key authentication middleware |
| `middleware/jwt_auth.py` | JWT token validation and claims extraction |
| `middleware/rate_limit.py` | Per-IP rate limiting via Redis |
| `middleware/token_bucket.py` | Token bucket algorithm for rate limiting |
| `middleware/correlation.py` | Request correlation ID injection for tracing |
| `middleware/request_protection.py` | Request size limits and input sanitization |
| `routes/chat.py` | Chat endpoint — main user-facing API |
| `schemas/chat.py` | Pydantic request/response models |

### `app/cache/` — Semantic Cache

| File | Purpose |
|------|---------|
| `semantic_cache.py` | Redis-backed semantic similarity cache (cosine ≥ 0.92) |

### `app/config/` — Configuration

| File | Purpose |
|------|---------|
| `settings.py` | Pydantic Settings model — all environment variables |
| `logging_config.py` | Structured JSON logging setup with ELK formatter |

### `app/evaluation/` — Response Quality

| File | Purpose |
|------|---------|
| `response_grader.py` | LLM-as-judge grading for response quality |
| `dataset_builder.py` | Builds evaluation datasets from production conversations |

### `app/knowledge_graph/` — Knowledge Graph

| File | Purpose |
|------|---------|
| `entity_extractor.py` | NLP-based entity and relationship extraction |
| `graph_store.py` | In-memory graph store with traversal algorithms |
| `trust.py` | Source trust evaluation and decay scoring |

### `app/llm/` — Language Model Layer

| File | Purpose |
|------|---------|
| `base.py` | Abstract `LLMProvider` base class |
| `model_router.py` | Routes to specialized models by task type |
| `providers/huggingface_provider.py` | HuggingFace Inference API provider |
| `providers/openai_provider.py` | OpenAI API provider |
| `providers/fallback_provider.py` | Multi-provider fallback with circuit breakers |
| `providers/local_provider.py` | Local model provider for development |
| `tokenizer/adaptive_budget.py` | Token budget allocation using tiktoken |

### `app/memory/` — Memory System

| File | Purpose |
|------|---------|
| `memory_service.py` | High-level memory facade for read/write |
| `memory_controller.py` | Unified memory controller aggregating sources |
| `session_cache.py` | Redis-backed session state cache |
| `conversation_store.py` | PostgreSQL-backed conversation history |
| `summarizer.py` | Conversation summarization for long-term memory |
| `authority.py` | Memory conflict resolution between sources |

### `app/orchestrator/` — Request Orchestration

| File | Purpose |
|------|---------|
| `chat_orchestrator.py` | 10-step request processing pipeline |
| `context_builder.py` | Assembles RAG + memory context for LLM |
| `pipeline.py` | Streaming pipeline abstraction |
| `tool_runner.py` | Streaming tool call execution |
| `watchdog.py` | Agent budget enforcement (time, tool calls) |

### `app/plugins/` — Plugin System

| File | Purpose |
|------|---------|
| `plugin_protocol.py` | Plugin interface (Protocol class) |
| `plugin_runtime.py` | Plugin lifecycle management |
| `registry.py` | Plugin discovery and registration |
| `weather_plugin.py` | Example weather plugin |
| `sandbox/sandbox_runner.py` | Sandboxed plugin execution environment |

### `app/rag/` — Retrieval-Augmented Generation

| File | Purpose |
|------|---------|
| `retriever.py` | RAG retriever — embedding search + reranking |
| `reranker.py` | Cross-encoder reranking (`ms-marco-MiniLM-L-6-v2`) |
| `crawler.py` | Web crawler for knowledge base ingestion |

### `app/reasoning_graph/` — Reasoning Engine

| File | Purpose |
|------|---------|
| `models.py` | ReasoningGraph, ReasoningNode data models |
| `engine.py` | DAG execution engine for multi-step reasoning |

### `app/reliability/` — Reliability Layer

| File | Purpose |
|------|---------|
| `circuit_breaker.py` | Three-state circuit breaker (CLOSED → OPEN → HALF_OPEN) |
| `retry_policy.py` | Exponential backoff with decorrelated jitter |
| `timeout_controller.py` | Hard timeout wrapper for all external calls |
| `failure_tracker.py` | Sliding-window failure accounting |
| `response_guard.py` | LLM output validation (hallucination, injection, length) |
| `load_guard.py` | Admission control (request queue, agent limit, swarm throttle) |

### `app/security/` — Security Layer

| File | Purpose |
|------|---------|
| `content_safety.py` | Content safety filter with threat pattern detection |
| `prompt_guard.py` | Prompt injection detection and blocking |
| `refusal_guard.py` | Ensures LLM refuses dangerous/disallowed requests |

### `app/shared/` — Cross-Cutting Utilities

| File | Purpose |
|------|---------|
| `exceptions.py` | Exception hierarchy (ChatBotError base) |
| `monitoring.py` | Prometheus metrics definitions (80+ metrics) |
| `tracing.py` | OpenTelemetry tracer setup |
| `types.py` | Shared type definitions and TypedDicts |
| `utils.py` | Logger factory, helper utilities |

### `app/vector_memory/` — Vector Database

| File | Purpose |
|------|---------|
| `base.py` | VectorStore abstract base class |
| `faiss_store.py` | FAISS vector store implementation |
| `qdrant_store.py` | Qdrant vector store implementation (async gRPC) |
| `vector_store.py` | VectorMemory facade selecting backend |
| `embeddings.py` | EmbeddingService (SentenceTransformers) |
| `memory_retriever.py` | Semantic memory retrieval interface |
| `maintenance.py` | Vector index maintenance and optimization |
| `migration.py` | Data migration between vector store backends |

### `app/swarm/` — Swarm Execution

| File | Purpose |
|------|---------|
| `execution.py` | Parallel/sequential multi-agent execution and result merging |

### `app/tool_router/` — Neural Tool Router

| File | Purpose |
|------|---------|
| `neural_router.py` | FAISS-based semantic tool matching from natural language |

### `app/tools/` — Built-in Tools

| File | Purpose |
|------|---------|
| `registry.py` | Tool registration and discovery (5 built-in tools) |
| `web_search.py` | Web search tool implementation |

### `workers/` — Celery Background Workers

| File | Purpose |
|------|---------|
| `celery_app.py` | Celery application configuration and beat schedule |
| `ingestion_worker.py` | Document ingestion (parsing, chunking) |
| `indexing_worker.py` | Embedding generation and vector indexing |
| `knowledge_builder.py` | Knowledge graph construction from documents |
| `maintenance_worker.py` | Index optimization and cleanup (03:00 UTC daily) |

### `tests/` — Test Suite

| Directory | Purpose |
|-----------|---------|
| `unit/` | Isolated unit tests (LLM provider) |
| `integration/` | Multi-component integration tests |
| `e2e/` | Full system end-to-end tests |
| `chaos/` | Chaos engineering fault injection framework |

### `infra/` — Infrastructure

| File | Purpose |
|------|---------|
| `docker/Dockerfile` | Multi-stage Docker build |
| `docker/docker-compose.yml` | 5-service application stack |
| `monitoring/docker-compose.observability.yml` | 6-service observability stack |
| `monitoring/prometheus.yml` | Prometheus scrape configuration |
| `monitoring/filebeat.yml` | Filebeat → Elasticsearch log shipping |
| `monitoring/grafana/` | Grafana dashboards and provisioning |

---

## Summary

| Category | Count |
|----------|-------|
| Python source files (`app/`) | ~70 |
| Test files (`tests/`) | ~19 |
| Worker files (`workers/`) | 6 |
| Documentation files (`docs/`) | 28 |
| Infrastructure files (`infra/`) | 8 |
| Root config files | 5 |
| Total directories | ~55 |
