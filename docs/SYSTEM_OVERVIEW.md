# System Overview

> High-level description of the Nimbus AI Chatbot Platform — its purpose, components, and how they interact.

---

## Table of Contents

- [What Is Nimbus?](#what-is-nimbus)
- [System Components](#system-components)
- [Component Interactions](#component-interactions)
- [Request Processing Model](#request-processing-model)
- [Background Processing](#background-processing)
- [External Dependencies](#external-dependencies)
- [Key Subsystem Summaries](#key-subsystem-summaries)

---

## What Is Nimbus?

Nimbus is a self-hosted AI chatbot platform that combines:

1. **Multi-Agent Intelligence** — A planner agent decomposes complex queries into a directed acyclic graph (DAG) of reasoning steps. Specialized agents (research, coding, reasoning) execute these steps, and a critic agent validates the final output.

2. **Retrieval-Augmented Generation (RAG)** — Documents are ingested, chunked, embedded, and indexed. At query time, relevant chunks are retrieved, reranked, and injected into the LLM prompt to ground responses in factual content.

3. **Knowledge Graph** — Entities and relationships are extracted from conversations and ingested documents, stored persistently, and used to enrich context.

4. **Multi-Tier Vector Memory** — Three memory tiers (episodic, semantic, profile) with pluggable backends (FAISS for development, Qdrant for production) provide persistent recall across sessions.

5. **Production Reliability** — Circuit breakers, retry policies, timeout controllers, load guards, and an agent watchdog ensure the system degrades gracefully under failure conditions.

6. **Automated Prompt Evolution** — Prompts are versioned, A/B tested, and mutated via LLM feedback to continuously improve agent performance.

---

## System Components

```
┌─────────────────────────────────────────────────────────────┐
│                    NIMBUS PLATFORM                           │
│                                                             │
│  ┌───────────┐  ┌──────────────┐  ┌───────────────────┐    │
│  │  API      │  │ Orchestrator │  │ Agent System      │    │
│  │  Gateway  │──│              │──│ (Planner, Research,│    │
│  │  (FastAPI)│  │              │  │  Coding, Critic)  │    │
│  └───────────┘  └──────────────┘  └───────────────────┘    │
│        │               │                   │                │
│        │               ▼                   ▼                │
│  ┌─────┴─────┐  ┌──────────────┐  ┌───────────────────┐    │
│  │ Security  │  │ Knowledge    │  │ Reliability       │    │
│  │ Layer     │  │ Layer        │  │ Layer             │    │
│  │ (JWT,     │  │ (RAG, KG,   │  │ (Circuit Breaker, │    │
│  │  Guard,   │  │  Cache,     │  │  Retry, Timeout,  │    │
│  │  Safety)  │  │  VectorMem) │  │  Watchdog)        │    │
│  └───────────┘  └──────────────┘  └───────────────────┘    │
│        │               │                   │                │
│        ▼               ▼                   ▼                │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              Infrastructure Layer                     │  │
│  │  Redis • PostgreSQL • FAISS/Qdrant • Celery • OTLP   │  │
│  └───────────────────────────────────────────────────────┘  │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐  │
│  │              Background Workers (Celery)              │  │
│  │  Ingestion • Knowledge Crawl • Vector Maintenance     │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

---

## Component Interactions

### Primary Request Path

| Step | Component | Action |
|------|-----------|--------|
| 1 | API Gateway | Authenticate, rate limit, validate request |
| 2 | ChatOrchestrator | Coordinate all subsystems |
| 3 | PromptGuard | Check for prompt injection |
| 4 | SemanticCache | Check for cached response |
| 5 | ChatPipeline | Gather context (RAG + memory + web) |
| 6 | RefusalGuard | Decide if confidence is sufficient |
| 7 | PlannerAgent | Decompose query into reasoning DAG |
| 8 | ReasoningGraphEngine | Execute DAG nodes via agents |
| 9 | CriticAgent | Evaluate response quality |
| 10 | ResponseValidator | Sanitize and validate output |
| 11 | Memory Persistence | Store interaction across tiers |
| 12 | SemanticCache | Cache response for future queries |

### Swimlane Diagram: Full Request Lifecycle

```
  Client          API Gateway      Orchestrator     Agents          Infrastructure
    │                 │                │               │                │
    │  POST /chat     │                │               │                │
    │────────────────▶│                │               │                │
    │                 │ Auth + Rate    │               │                │
    │                 │ Limit Check    │               │                │
    │                 │───────────────▶│               │                │
    │                 │                │ PromptGuard   │                │
    │                 │                │──────────────▶│                │
    │                 │                │  (injection   │                │
    │                 │                │   check)      │                │
    │                 │                │◀──────────────│                │
    │                 │                │               │                │
    │                 │                │ Cache Lookup  │                │
    │                 │                │──────────────────────────────▶│ Redis
    │                 │                │◀──────────────────────────────│
    │                 │                │               │                │
    │                 │                │  [CACHE HIT]──────────────────│──▶ Response (< 50ms)
    │                 │                │               │                │
    │                 │                │  [CACHE MISS] │                │
    │                 │                │               │                │
    │                 │                │ RAG Retrieve  │                │
    │                 │                │──────────────────────────────▶│ FAISS/Qdrant
    │                 │                │ Memory Fetch  │                │
    │                 │                │──────────────────────────────▶│ Redis + PG
    │                 │                │◀──────────────────────────────│
    │                 │                │               │                │
    │                 │                │ Plan + Execute│                │
    │                 │                │──────────────▶│ PlannerAgent   │
    │                 │                │               │──────────────▶│ LLM API
    │                 │                │               │◀──────────────│
    │                 │                │               │ Execute DAG    │
    │                 │                │               │──────────────▶│ LLM API
    │                 │                │◀──────────────│               │
    │                 │                │               │                │
    │                 │                │ Critic Review  │                │
    │                 │                │──────────────▶│ CriticAgent    │
    │                 │                │◀──────────────│               │
    │                 │                │               │                │
    │                 │                │ Validate +    │                │
    │                 │                │ Cache + Save  │                │
    │                 │                │──────────────────────────────▶│ Redis + PG
    │                 │◀───────────────│               │                │
    │◀────────────────│               │               │                │
    │   JSON Response │               │               │                │
```

### When Does Each Component Activate?

| Component | Activates When | Skipped When |
|-----------|---------------|--------------|
| PromptGuard | Always (every request) | Never |
| SemanticCache | Always checked first | N/A — returns early on hit |
| RAG Retriever | `use_rag=true` (default) | `use_rag=false` in request |
| Web Search | `use_web=true` in request | `use_web=false` (default) |
| Memory Service | `use_memory=true` (default) | `use_memory=false` in request |
| PlannerAgent | Cache miss (always on miss) | Cache hit |
| SwarmExecution | Planner creates a DAG with independent nodes | DAG is linear (all nodes depend on previous) |
| CriticAgent | Always (after agent execution) | Never |
| Knowledge Graph | Automatically during context building | Graph is empty |

### Three Request Paths

```
Path 1 — CACHE HIT (< 50ms):
  Auth → Guard → Cache ✓ → Return cached answer

Path 2 — SIMPLE QUERY (2-5s):
  Auth → Guard → Cache ✗ → RAG + Memory → Single Agent → Critic → Cache + Return

Path 3 — COMPLEX QUERY (5-15s):
  Auth → Guard → Cache ✗ → RAG + Memory → Planner → DAG (multi-agent swarm) → Merger → Critic → Cache + Return
```

### Cross-Cutting Concerns

Every step above is instrumented with:
- **Prometheus metrics** — Counters, histograms, gauges for latency, throughput, errors
- **OpenTelemetry spans** — Distributed traces with parent-child relationships
- **Structured logging** — ELK-compatible JSON with correlation IDs
- **Circuit breakers** — Automatic failure isolation for external calls

---

## Request Processing Model

Nimbus processes all requests asynchronously using Python's `asyncio` event loop via Uvicorn and FastAPI. The concurrency model is:

```
Uvicorn (ASGI)
  └── FastAPI Event Loop
       ├── Concurrent HTTP requests (async)
       ├── LLM API calls (async httpx)
       ├── Redis operations (async redis)
       ├── PostgreSQL queries (async asyncpg)
       ├── Vector search (FAISS: sync in threadpool / Qdrant: async gRPC)
       └── Plugin execution (subprocess with asyncio.wait_for)
```

### Concurrency Limits

| Resource | Limit | Mechanism |
|----------|-------|-----------|
| API requests | 100/min per identity | Token bucket rate limiter |
| Agent execution | 10/min per identity | Agent rate bucket |
| Concurrent agents | 20 max | AgentExecutionLimiter (semaphore) |
| Agent iterations | 10 max per execution | AgentWatchdog |
| Agent tool calls | 20 max per execution | AgentWatchdog |
| Agent wall-clock | 30s max per execution | AgentWatchdog |
| Swarm parallelism | 5 agents max | MAX_SWARM_AGENTS |
| Request body | 1 MB max | RequestSizeLimitMiddleware |
| Request timeout | 60s | TimeoutMiddleware |

---

## Background Processing

Background tasks run via Celery workers with Redis as the message broker:

| Task | Schedule | Worker | Description |
|------|----------|--------|-------------|
| `run_vector_maintenance` | Daily 03:00 UTC | `maintenance_worker` | Deduplicate, remove stale vectors, reindex |
| `crawl_and_ingest` | Daily 04:00 UTC | `knowledge_builder` | Crawl trusted sources, evaluate trust, ingest |
| `ingest_documents` | On-demand | `ingestion_worker` | Process uploaded documents into RAG index |
| `rebuild_index` | On-demand | `ingestion_worker` | Force full FAISS index rebuild |
| `index_vectors` | On-demand | `indexing_worker` | Batch insert vector records |
| `run_migration_task` | On-demand | `indexing_worker` | Migrate FAISS to Qdrant |
| `expand_knowledge_graph` | On-demand | `knowledge_builder` | Extract entities from recent ingestions |

---

## External Dependencies

| Service | Required | Purpose | Fallback |
|---------|----------|---------|----------|
| **Redis** | Yes | Session cache, rate limiter, Celery broker, semantic cache | None — required for operation |
| **PostgreSQL** | No | Long-term conversation storage | Degrades to Redis-only (session-scoped) |
| **HuggingFace API** | Yes (primary) | LLM inference, embeddings | Falls back to OpenAI |
| **OpenAI API** | No | Fallback LLM provider | Fallback of last resort |
| **Qdrant** | No | Production vector storage | Falls back to FAISS (local) |
| **DuckDuckGo** | No | Web search | Feature disabled per-request |
| **OTLP Collector** | No | Distributed tracing | Tracing disabled silently |
| **Elasticsearch** | No | Log aggregation | Console/file logging |
| **Prometheus** | No | Metrics collection | Metrics exposed but not scraped |

---

## Key Subsystem Summaries

### Agent System

The agent system uses a **Plan → Execute → Evaluate** pattern:

1. **PlannerAgent** decomposes a user query into a `ReasoningGraph` (DAG of task nodes)
2. **ReasoningGraphEngine** executes the graph using one of two strategies:
   - `SequentialExecution` — processes nodes in dependency order
   - `SwarmExecution` — runs independent nodes in parallel (max 5 agents)
3. **AgentRouter** dispatches each node to the correct specialist agent
4. **CriticAgent** evaluates the final response for quality and hallucinations

Agents communicate via `AgentState`, which tracks intermediate results, reasoning traces, and execution budgets.

### Memory System

Four memory tiers operate in parallel:

| Tier | Storage | Latency | Scope |
|------|---------|---------|-------|
| Session Cache | Redis | < 1ms | Current conversation (TTL 1h) |
| Conversation Store | PostgreSQL | < 5ms | Full history |
| Vector Memory | FAISS / Qdrant | 5-50ms | Semantic recall (episodic, semantic, profile) |
| Knowledge Graph | JSON on disk | < 1ms | Entity-relationship facts |

The `UnifiedMemoryController` fetches from all tiers in parallel and uses `MemoryAuthorityResolver` to resolve conflicts (priority: Conversation > KG > Vector).

### RAG Pipeline

```
Documents → Extract → Chunk → Embed → Index → Search → Rerank → Safety → Context
```

- **Chunking**: Sentence-based, 512 chars with 50-char overlap
- **Embedding**: all-MiniLM-L6-v2 (384 dimensions, L2-normalized)
- **Indexing**: FAISS IndexFlatIP (inner product search)
- **Reranking**: cross-encoder/ms-marco-MiniLM-L-6-v2
- **Safety**: Content safety filter on ingestion; refusal guard at query time

### Reliability Layer

Every external call is wrapped in three nested reliability primitives:

```
CircuitBreaker → RetryPolicy → TimeoutController → External Call
```

Additionally:
- `AgentWatchdog` enforces per-execution budgets (iterations, tool calls, wall-clock)
- `AgentExecutionLimiter` bounds total concurrent agent executions
- `ResponseValidator` catches hallucinated tool calls and injection in outputs
- `FailureTracker` shares failure intelligence across components

### Security Model

Defense in depth across the request lifecycle:

| Phase | Components | What It Catches |
|-------|-----------|-----------------|
| **Pre-processing** | PromptGuard, ContentSafety | Prompt injection, harmful input |
| **Authentication** | JWT validator, API key check | Unauthorized access |
| **Rate Limiting** | Token bucket (Redis-backed) | Abuse, DDoS |
| **Execution** | Plugin sandbox, tool whitelisting | Code execution escapes |
| **Post-processing** | ResponseValidator, RefusalGuard | Hallucinated actions, injection in output |

### Prompt Evolution

Prompts are managed as versioned artifacts:

```
Prompt v1.0 → A/B test → LLM-generated mutation → v1.1
     │                                               │
     └── Evaluation dataset ──▶ ResponseGrader ──────┘
                                (automated scoring)
```

- Each prompt version has a unique ID and metadata
- A/B testing compares new vs. old prompts on the evaluation dataset
- The `ResponseGrader` scores answers on accuracy, relevance, and safety
- Best-performing prompts are promoted; worse ones are deprecated

---

## Glossary

| Term | Definition |
|------|-----------|
| **DAG** | Directed Acyclic Graph — the reasoning plan created by the PlannerAgent |
| **RAG** | Retrieval-Augmented Generation — grounding LLM answers with retrieved documents |
| **Swarm** | Parallel execution of independent agent tasks |
| **Circuit Breaker** | Pattern that stops calling a failing service to let it recover |
| **Semantic Cache** | Cache that matches queries by meaning (cosine similarity) instead of exact text |
| **Memory Authority** | Priority system that resolves conflicting facts across memory tiers |
| **Knowledge Graph** | Structured store of entity-relationship triples extracted from text |
| **Vector Memory** | Embedding-based memory with three tiers: episodic, semantic, profile |
| **AgentWatchdog** | Safety mechanism that kills runaway agent executions |
|-------|-----------|
| **Transport** | TLS termination, HSTS, security headers |
| **Authentication** | JWT (HS256) + API key validation |
| **Rate Limiting** | Token bucket per user (100/min general, 10/min agent) |
| **Input Validation** | Request size limit (1MB), timeout (60s), abuse detection |
| **Prompt Safety** | PromptGuard (10 patterns), ContentSafetyFilter (19 rules) |
| **Output Safety** | ResponseValidator, RefusalGuard, HTML sanitization |
| **Plugin Isolation** | Subprocess sandbox, env sanitization, module restrictions |

### Observability

Three observability pillars are built into every operation:

| Pillar | Implementation | Destination |
|--------|---------------|-------------|
| **Metrics** | Prometheus client (80+ metrics) | `GET /metrics` → Prometheus → Grafana |
| **Tracing** | OpenTelemetry SDK with `@traced` decorator | OTLP → Jaeger |
| **Logging** | ELK-compatible JSON (ECS format) | Filebeat → Elasticsearch → Kibana |

### Evaluation & Continuous Improvement

The platform continuously evaluates and improves its own performance:

1. **ResponseGrader** — LLM-as-a-judge scores every response (correctness, completeness, reasoning)
2. **DatasetBuilder** — Logs all interactions with grades for analysis
3. **PromptEvolutionManager** — A/B tests prompt variants (80/20 split), promotes winners, rejects losers
4. **CriticAgent** — Catches hallucinations and requests revision before delivery
|---------|----------|---------|----------|
| **Redis** | Yes | Session cache, rate limiting, semantic cache, Celery broker | Fail-open for rate limiting; no cache |
| **PostgreSQL** | No | Long-term conversation storage | In-memory only |
| **HuggingFace API** | Yes (one LLM required) | Primary LLM provider | Falls back to OpenAI |
| **OpenAI API** | No | Fallback LLM provider | Error if both fail |
| **Qdrant** | No (production) | Production vector database | FAISS in-process |
| **Prometheus** | No | Metrics collection | Metrics still emitted, just not scraped |
| **OTLP Endpoint** | No | Distributed tracing | No-op tracer |
| **Elasticsearch** | No | Log aggregation | Logs go to stdout |

---

## Key Subsystem Summaries

### API Layer (`app/api/`)
FastAPI application with 6 middleware layers (security headers, correlation tracking, CORS, timeouts, size limits, JWT auth). Exposes `/api/v1/chat` and `/api/v1/chat/stream` endpoints with Pydantic request/response validation.

### Agent System (`app/agents/`)
Five specialized agents (Planner, Research, Coding, Reasoning, Critic) orchestrated via a DAG-based reasoning graph. The planner decomposes queries into nodes, agents execute nodes in dependency order, and the critic validates the aggregated result.

### Orchestrator (`app/orchestrator/`)
Central coordination layer. The `ChatOrchestrator` manages the complete request lifecycle: security checks, cache lookup, context gathering, agent execution, response validation, memory persistence, and cache population.

### RAG Pipeline (`app/rag/`)
Document ingestion pipeline: extract text (PDF/TXT/MD) → sentence-based chunking with overlap → embedding via SentenceTransformers → FAISS/Qdrant indexing. Query-time retrieval with cross-encoder reranking and content safety filtering.

### Memory System (`app/memory/`, `app/vector_memory/`)
Three-tier memory architecture: Redis-backed session cache (short-term), PostgreSQL conversation store (long-term), and vector memory with episodic/semantic/profile tiers. Memory authority model resolves conflicts: conversation > knowledge graph > vector.

### Knowledge Graph (`app/knowledge_graph/`)
LLM-based entity extraction produces structured relationships stored in JSON. Trust scoring evaluates source reliability before ingestion. Keyword-based context retrieval enriches agent reasoning.

### Plugin System (`app/plugins/`)
Subprocess-isolated plugin execution with sanitized environments. Length-prefixed JSON IPC protocol, 30-second timeouts, and an in-process sandbox with module whitelisting for lightweight code execution.

### Reliability Layer (`app/reliability/`)
Production-hardened primitives: three-state circuit breaker, exponential backoff with decorrelated jitter, timeout controllers with fallback support, failure tracking with sliding windows, and load guards for admission control.

### Security Model (`app/security/`)
Multi-layer defense: JWT authentication, API key validation, prompt injection detection (19 weighted regex rules), content safety scoring (injection + trust + quality), response validation, and abuse detection.

### Observability (`app/shared/`)
80+ Prometheus metrics, OpenTelemetry distributed tracing with OTLP export, ELK-compatible JSON structured logging, and Filebeat log shipping configuration.

### Background Workers (`workers/`)
Celery-based workers for document ingestion, knowledge graph expansion, web crawling with trust evaluation, and vector index maintenance (deduplication, stale removal, reindexing).

### Prompt Evolution (`app/prompts/evolution/`)
Automated prompt improvement: version tracking, performance scoring, A/B testing (20% traffic split to candidates), LLM-driven mutation, and automatic promotion/rejection based on score thresholds.
