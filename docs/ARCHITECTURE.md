# Architecture

> Complete architectural reference for the Nimbus AI Chatbot Platform.

---

## Table of Contents

- [System Purpose](#system-purpose)
- [System Design Principles](#system-design-principles)
- [Core Design Philosophy](#core-design-philosophy)
- [Architectural Layers](#architectural-layers)
- [Service Boundaries](#service-boundaries)
- [Execution Flow](#execution-flow)
- [Data Flow Diagrams](#data-flow-diagrams)
- [Memory Consistency Model](#memory-consistency-model)
- [Component Dependency Graph](#component-dependency-graph)
- [Design Decisions](#design-decisions)
- [Technology Stack](#technology-stack)

---

## System Purpose

Nimbus is a production-grade AI assistant platform that orchestrates multiple specialized agents to answer user queries with high accuracy, transparency, and reliability. The system:

1. **Decomposes** complex queries into directed acyclic graphs (DAGs) of reasoning steps
2. **Retrieves** relevant knowledge from documents (RAG), web search, and knowledge graphs
3. **Executes** specialized agents (research, coding, reasoning) in parallel or sequentially
4. **Validates** responses through a critic agent and response guard
5. **Persists** knowledge across sessions via multi-tier vector memory
6. **Evolves** its own prompts through automated A/B testing and mutation

---

## System Design Principles

The Nimbus platform is built on five foundational engineering principles that guide every architectural decision:

| Principle | Description | Enforcement |
|-----------|-------------|-------------|
| **Separation of Concerns** | Each module has a single, well-defined responsibility. Agents reason, tools execute, memory persists, reliability wraps. No component crosses boundaries. | Enforced via 7-layer architecture with clear interfaces (ABC base classes, protocols). |
| **Fail-Safe Defaults** | Every external interaction defaults to the safest behavior on failure. LLMs fall back. Cache misses degrade gracefully. Agents time out. | Circuit breakers, retry policies, timeout controllers wrap all external calls. |
| **Composition Over Inheritance** | Components are composed via dependency injection rather than deep inheritance hierarchies. `ChatOrchestrator` receives its pipeline, agents, and tools via constructors. | Factory functions in `app/api/dependencies/providers.py` wire everything together. |
| **Explicit Over Implicit** | No hidden state mutations. Agent state flows through `AgentState`; memory operations are explicit `add`/`get` calls; configuration comes from typed `Settings`. | Pydantic settings with validation; explicit context passing; no global mutable state. |
| **Observable by Construction** | Every operation emits telemetry. Spans, metrics, and structured logs are not afterthoughts — they're built into the execution path. | `@traced` decorator, `PrometheusMetrics` singletons, `emit_observability_event` calls. |

### Layered Isolation

Each layer communicates only with the layer directly below it. The API layer never directly accesses the vector store; it goes through the orchestrator, which uses the memory layer. This ensures:

- **Testability** — Each layer is independently testable with mocked dependencies
- **Replaceability** — Swap FAISS for Qdrant, HuggingFace for OpenAI, or Redis for Memcached without touching upper layers
- **Security** — The API layer enforces authentication before any business logic runs

---

## Core Design Philosophy

### Privacy-First
All computation defaults to local execution. Web search is opt-in per request. No user data leaves the system unless explicitly configured.

### Grounded Intelligence
RAG enforcement ensures the system refuses to answer when confidence is below threshold (default 0.35). Hallucination detection runs on every response via the critic agent.

### Resilience Over Availability
Every external dependency (LLM providers, Redis, PostgreSQL, Qdrant) is wrapped in circuit breakers, retry policies, and timeout controllers. The system degrades gracefully rather than failing catastrophically.

### Observable by Default
Every significant operation emits structured logs (ELK-compatible JSON), Prometheus metrics, and OpenTelemetry trace spans. No operation is a black box.

---

## Architectural Layers

The system is organized into seven distinct layers, each with clear responsibilities and boundaries:

```
┌─────────────────────────────────────────────────────────────────┐
│                     Layer 7: API Gateway                        │
│  FastAPI • JWT Auth • Rate Limiting • CORS • Request Protection │
├─────────────────────────────────────────────────────────────────┤
│                     Layer 6: Orchestration                      │
│  ChatOrchestrator • ChatPipeline • ContextBuilder               │
├─────────────────────────────────────────────────────────────────┤
│                     Layer 5: Intelligence                       │
│  PlannerAgent • ReasoningGraphEngine • AgentRouter • Swarm      │
├─────────────────────────────────────────────────────────────────┤
│                     Layer 4: Knowledge                          │
│  RAG Retriever • Knowledge Graph • Semantic Cache • Reranker    │
├─────────────────────────────────────────────────────────────────┤
│                     Layer 3: Memory                             │
│  VectorMemory • ConversationStore • SessionCache • Authority    │
├─────────────────────────────────────────────────────────────────┤
│                     Layer 2: Reliability                        │
│  CircuitBreaker • RetryPolicy • TimeoutController • LoadGuard   │
├─────────────────────────────────────────────────────────────────┤
│                     Layer 1: Infrastructure                     │
│  Redis • PostgreSQL • FAISS/Qdrant • Celery • Prometheus • OTLP │
└─────────────────────────────────────────────────────────────────┘
```

### Layer 7: API Gateway

**Location:** `app/api/`

The API gateway handles all inbound HTTP traffic. It enforces authentication, rate limiting, request size limits, timeout protection, and abuse detection before any request reaches business logic.

| Component | File | Responsibility |
|-----------|------|---------------|
| FastAPI App | `main.py` | Application bootstrap, middleware stack, route registration |
| JWT Auth | `middleware/jwt_auth.py` | Stateless Bearer token validation (HS256) |
| API Key Auth | `middleware/auth.py` | `X-API-Key` header validation |
| Token Bucket | `middleware/token_bucket.py` | Redis-backed per-identity rate limiting |
| Request Protection | `middleware/request_protection.py` | Size limits, timeouts, abuse detection |
| Correlation | `middleware/correlation.py` | Request ID propagation, latency tracking |

### Layer 6: Orchestration

**Location:** `app/orchestrator/`

The orchestration layer is the central nervous system. It coordinates all subsystems to produce a final answer from a user query.

| Component | File | Responsibility |
|-----------|------|---------------|
| ChatOrchestrator | `chat_orchestrator.py` | Main entry point; coordinates all subsystems |
| ChatPipeline | `pipeline.py` | Context gathering (RAG + memory + web) |
| ContextBuilder | `context_builder.py` | Prompt assembly with token budgeting |
| StreamingToolRunner | `tool_runner.py` | Tool call detection and execution in LLM streams |
| AgentWatchdog | `watchdog.py` | Execution budget enforcement and runaway detection |

### Layer 5: Intelligence

**Location:** `app/agents/`, `app/reasoning_graph/`, `app/swarm/`

The intelligence layer implements the multi-agent system that decomposes and solves complex queries.

| Component | File | Responsibility |
|-----------|------|---------------|
| PlannerAgent | `agents/planner_agent.py` | Decomposes queries into DAGs |
| ReasoningAgent | `agents/reasoning_agent.py` | Analysis and summarization |
| CodingAgent | `agents/coding_agent.py` | Code generation with tool loops |
| ResearchAgent | `agents/research_agent.py` | Information gathering with web/tool access |
| CriticAgent | `agents/critic_agent.py` | Response evaluation and hallucination detection |
| AgentRouter | `agents/agent_router.py` | Dispatches tasks to registered agents |
| ReasoningGraphEngine | `reasoning_graph/engine.py` | DAG execution with strategy pattern |
| SwarmExecution | `swarm/execution.py` | Parallel agent spawning |

### Layer 4: Knowledge

**Location:** `app/rag/`, `app/knowledge_graph/`, `app/cache/`, `app/tool_router/`

The knowledge layer provides retrieval, storage, and selection of information.

| Component | File | Responsibility |
|-----------|------|---------------|
| RAGRetriever | `rag/retriever.py` | Document ingestion, embedding, vector search |
| CrossEncoderReranker | `rag/reranker.py` | Reranking retrieved chunks |
| KnowledgeCrawler | `rag/crawler.py` | Automated web crawling for knowledge |
| EntityExtractor | `knowledge_graph/entity_extractor.py` | LLM-based entity/relationship extraction |
| GraphStore | `knowledge_graph/graph_store.py` | Structured knowledge persistence |
| SourceTrustEvaluator | `knowledge_graph/trust.py` | Pre-ingestion content trust scoring |
| SemanticCache | `cache/semantic_cache.py` | Redis-backed response caching |
| NeuralToolRouter | `tool_router/neural_router.py` | Semantic tool selection via FAISS |

### Layer 3: Memory

**Location:** `app/memory/`, `app/vector_memory/`

The memory layer provides multi-tier persistent storage for conversations, learned facts, and user profiles.

| Component | File | Responsibility |
|-----------|------|---------------|
| MemoryService | `memory/memory_service.py` | Redis + PostgreSQL memory coordination |
| SessionCache | `memory/session_cache.py` | Redis-backed short-term session state |
| ConversationStore | `memory/conversation_store.py` | PostgreSQL long-term conversation history |
| Summarizer | `memory/summarizer.py` | Conversation compression via LLM |
| MemoryAuthorityResolver | `memory/authority.py` | Three-tier conflict resolution |
| UnifiedMemoryController | `memory/memory_controller.py` | Cross-layer memory coordination |
| VectorMemory | `vector_memory/vector_store.py` | Pluggable vector storage facade |
| MemoryRetriever | `vector_memory/memory_retriever.py` | Multi-tier memory retrieval |

### Layer 2: Reliability

**Location:** `app/reliability/`

The reliability layer wraps all external interactions with production-hardened primitives.

| Component | File | Responsibility |
|-----------|------|---------------|
| CircuitBreaker | `circuit_breaker.py` | Three-state failure isolation (CLOSED/OPEN/HALF_OPEN) |
| RetryPolicy | `retry_policy.py` | Exponential backoff with decorrelated jitter |
| TimeoutController | `timeout_controller.py` | Bounded execution time for external calls |
| FailureTracker | `failure_tracker.py` | Sliding-window failure statistics |
| ResponseValidator | `response_guard.py` | LLM response validation and sanitization |
| RequestQueueLimiter | `load_guard.py` | Semaphore-based admission control |
| AgentExecutionLimiter | `load_guard.py` | Concurrent agent execution bounds |
| SwarmThrottle | `load_guard.py` | Dynamic parallelism throttling |

### Layer 1: Infrastructure

**Location:** `infra/`, `workers/`

External services and background processing.

| Component | Location | Responsibility |
|-----------|----------|---------------|
| Redis | External | Session cache, rate limiting, semantic cache, Celery broker |
| PostgreSQL | External | Conversation history, long-term storage |
| FAISS | In-process | Development vector index |
| Qdrant | External | Production vector database |
| Celery | `workers/` | Background task execution |
| Prometheus | External | Metrics collection and alerting |
| OpenTelemetry | In-process | Distributed tracing |

---

## Service Boundaries

```
┌──────────────────────────────────────────────────────────────┐
│                    Process: API Server                        │
│                                                              │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ FastAPI  │──│ Orchestrator │──│ Agents + Reasoning     │ │
│  │ (async)  │  │              │  │ Graph Engine           │ │
│  └──────────┘  └──────────────┘  └────────────────────────┘ │
│       │              │                     │                 │
│       ▼              ▼                     ▼                 │
│  ┌──────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │ Redis    │  │ LLM APIs     │  │ FAISS / Qdrant         │ │
│  │ (async)  │  │ (httpx async)│  │ (in-proc / async)      │ │
│  └──────────┘  └──────────────┘  └────────────────────────┘ │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                    Process: Celery Worker                     │
│                                                              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ Ingestion    │  │ Knowledge    │  │ Maintenance      │   │
│  │ Worker       │  │ Builder      │  │ Worker           │   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
│         │                │                    │              │
│         ▼                ▼                    ▼              │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────────┐   │
│  │ RAGRetriever │  │ KG + Trust   │  │ VectorMaintenance│   │
│  └──────────────┘  └──────────────┘  └──────────────────┘   │
└──────────────────────────────────────────────────────────────┘

┌──────────────────────────────────────────────────────────────┐
│                    Process: Plugin Subprocess                 │
│                                                              │
│  ┌──────────────────────────────────────────────────────┐    │
│  │ Sanitized Environment (PATH only)                    │    │
│  │ Length-prefixed JSON IPC                              │    │
│  │ 30-second timeout with hard kill                     │    │
│  └──────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────┘
```

---

## Execution Flow

### Complete Request Lifecycle

```
1. HTTP Request arrives at FastAPI
   │
2. Middleware Stack (bottom-to-top):
   ├── AbuseDetectionMiddleware    → Log abuse signals
   ├── JWTAuthMiddleware           → Validate token, set user_id
   ├── RequestSizeLimitMiddleware  → Reject oversized bodies (>1MB)
   ├── TimeoutMiddleware           → Cancel after 60s
   ├── CORSMiddleware              → Handle preflight
   ├── CorrelationMiddleware       → Assign X-Request-ID, start trace span
   └── SecurityHeadersMiddleware   → Add HSTS, X-Frame-Options, etc.
   │
3. Rate Limiting
   ├── General bucket: 100 req/min per identity
   └── Agent bucket: 10 req/min per identity (chat endpoints only)
   │
4. Route Handler (POST /api/v1/chat)
   ├── Validate ChatRequest schema
   └── Create ChatOrchestrator via dependency injection
   │
5. ChatOrchestrator.generate_answer()
   │
   ├── 5a. Prompt Security Check (PromptGuard.scan)
   │       → Reject if injection detected
   │
   ├── 5b. Semantic Cache Lookup
   │       → Return cached response if cosine similarity ≥ 0.92
   │
   ├── 5c. Context Gathering (ChatPipeline)
   │       ├── RAG retrieval (if use_rag=True)
   │       │   ├── Vector search (FAISS/Qdrant)
   │       │   ├── Content safety filtering
   │       │   └── Cross-encoder reranking
   │       ├── Web search (if use_web=True)
   │       │   ├── DuckDuckGo search
   │       │   ├── URL canonicalization and dedup
   │       │   ├── Trust scoring per domain
   │       │   └── Content extraction with injection redaction
   │       ├── Memory retrieval
   │       │   ├── Session cache (Redis)
   │       │   ├── Conversation store (PostgreSQL)
   │       │   └── Vector memory (episodic/semantic/profile)
   │       └── Context assembly with adaptive token budgeting
   │
   ├── 5d. Refusal Check
   │       → If RAG score < 0.35, return refusal message
   │
   ├── 5e. Agent Intelligence Loop (under AgentWatchdog)
   │       ├── PlannerAgent → Generate ReasoningGraph (DAG)
   │       ├── ReasoningGraphEngine.execute()
   │       │   ├── For each ready node:
   │       │   │   ├── REASONING → LLM call
   │       │   │   ├── TOOL_CALL → Parse and execute tool
   │       │   │   └── MEMORY_LOOKUP → Retrieve from memory
   │       │   └── Repeat until graph complete or budget exceeded
   │       ├── Aggregate results from all agents
   │       ├── CriticAgent.evaluate() → Quality scoring
   │       └── If score < 0.6, iterate with corrections
   │
   ├── 5f. Response Validation (ResponseValidator)
   │       ├── Length check and truncation
   │       ├── Hallucinated tool call removal
   │       ├── Prompt injection detection
   │       └── JSON schema validation
   │
   ├── 5g. Knowledge Graph Extraction
   │       └── EntityExtractor → GraphStore persistence
   │
   ├── 5h. Response Grading
   │       └── ResponseGrader → DatasetBuilder logging
   │
   ├── 5i. Memory Persistence
   │       ├── Redis session cache
   │       ├── PostgreSQL conversation store
   │       └── Vector memory (episodic)
   │
   └── 5j. Semantic Cache Population
           └── Store response for future similarity matches
   │
6. Return ChatResponse
   ├── answer: str
   ├── confidence: "high" | "medium" | "low"
   ├── citations: [{source, chunk_id, score}]
   ├── used_rag: bool
   └── used_web: bool
```

---

## Data Flow Diagrams

### Request Lifecycle

```
User
  ↓
API Layer (FastAPI + Middleware)
  ↓
Chat Orchestrator
  ↓
Planner Agent (DAG Generation)
  ↓
Agent Swarm / Sequential Execution
  ↓
Tool Router (FAISS-based Selection)
  ↓
RAG Retrieval (Vector Search + Reranking)
  ↓
Vector Memory (Episodic / Semantic / Profile)
  ↓
Knowledge Graph (Entity Extraction + Trust)
  ↓
Critic Agent (Validation + Quality Gate)
  ↓
Response Generation
  ↓
User
```

### Context Assembly Data Flow

```
┌──────────────────────────────────────────────────────────────┐
│                    Context Assembly                           │
│                                                              │
│   RAG Retriever ──────────┐                                  │
│     (top_k=20 → rerank    │                                  │
│      → top_n=5)           │                                  │
│                           ├──→ Adaptive Token Budgeter ──→ LLM│
│   Memory Retriever ───────┤    (tiktoken cl100k_base)        │
│     (episodic + semantic  │    60% RAG / 40% memory          │
│      + profile)           │                                  │
│                           │                                  │
│   Web Search ─────────────┘                                  │
│     (DuckDuckGo + trust   │                                  │
│      scoring)             │                                  │
└──────────────────────────────────────────────────────────────┘
```

### Agent DAG Execution Flow

```
Query: "Compare Python and Rust performance"
         ↓
    PlannerAgent
         ↓
    ReasoningGraph (DAG)
         ↓
┌────────────────────────────────────────────────┐
│  mem_lookup     search_py     search_rust      │
│  (memory)       (web_search)  (web_search)     │
│     │               │              │           │
│     └───────────────┼──────────────┘           │
│                     ↓                          │
│              compare_results                   │
│              (reasoning)                       │
│                     ↓                          │
│              format_answer                     │
│              (reasoning)                       │
└────────────────────────────────────────────────┘
         ↓
    CriticAgent (evaluate quality)
         ↓
    Final Response
```

### Plugin Execution Data Flow

```
Tool Call Detected in LLM Output
         ↓
    NeuralToolRouter
    (FAISS cosine similarity)
         ↓
┌─────────────────────────────────────┐
│  Tool Registry     Plugin Registry  │
│  (in-process)      (subprocess)     │
│       ↓                  ↓          │
│  Direct call      SandboxRunner     │
│                   ┌─────────────┐   │
│                   │ Subprocess  │   │
│                   │ • Clean env │   │
│                   │ • 30s limit │   │
│                   │ • JSON IPC  │   │
│                   └─────────────┘   │
└─────────────────────────────────────┘
         ↓
    Result injected into agent context
```

### Vector Search Pipeline

```
Query Text
  ↓
SentenceTransformer (all-MiniLM-L6-v2)
  ↓
384-dim L2-normalized embedding
  ↓
┌──────────────────────────────┐
│ FAISS (dev) / Qdrant (prod)  │
│ top_k=20 nearest neighbors   │
└──────────────────────────────┘
  ↓
CrossEncoder Reranker (ms-marco-MiniLM-L-6-v2)
  ↓
top_n=5 reranked results
  ↓
Content Safety Filter
  ↓
RAG Context (injected into prompt)
```

---

## Memory Consistency Model

The platform maintains three distinct memory systems that interact during every request:

### Memory Tiers

| Tier | Storage | Scope | TTL | Used For |
|------|---------|-------|-----|----------|
| **Session Cache** | Redis | Per-session | 30 minutes | Recent conversation turns |
| **Conversation Store** | PostgreSQL | Per-session | Permanent | Full conversation history |
| **Vector Memory** | FAISS/Qdrant | Cross-session | Configurable | Learned facts, user profile |
| **Knowledge Graph** | JSON/NetworkX | Global | Permanent | Entity relationships, trust-scored facts |

### Interaction Model

During context assembly, the `UnifiedMemoryController` fetches from all tiers in parallel:

```
┌──────────────────────────────────────────────────────────────────┐
│                   UnifiedMemoryController                        │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐   │
│  │SessionCache │  │Conversation  │  │ VectorMemory          │   │
│  │  (Redis)    │  │Store (PG)    │  │ (FAISS/Qdrant)        │   │
│  │  30min TTL  │  │  permanent   │  │ episodic+semantic     │   │
│  └──────┬──────┘  └──────┬───────┘  └───────────┬───────────┘   │
│         │                │                      │               │
│         └────────────────┼──────────────────────┘               │
│                          ↓                                      │
│                  MemoryAuthorityResolver                         │
│           (explicit > corrected > recent > summarized)           │
│                          ↓                                      │
│                  Deduplicated Context                            │
└──────────────────────────────────────────────────────────────────┘
```

### Conflict Resolution

When multiple memory tiers contain conflicting information about the same topic:

1. **Explicit user corrections** always win (highest authority)
2. **Recent conversation turns** override older summaries
3. **High-confidence RAG results** override low-confidence vector memory
4. **Trust-scored knowledge graph facts** are weighted by source reliability

The `MemoryAuthorityResolver` implements a four-tier priority system:

```
Priority 1: EXPLICIT    — User said "Actually, my name is X"
Priority 2: CORRECTED   — System corrected a previous fact
Priority 3: RECENT      — From current session (Redis)
Priority 4: SUMMARIZED  — From compressed history or vector memory
```

### Write Path

Every response triggers a dual-write:
1. **Redis** — Immediate session cache update (async, fire-and-forget)
2. **PostgreSQL** — Durable conversation store write (awaited)
3. **Vector Memory** — Background Celery task for embedding and indexing
4. **Knowledge Graph** — Entity extraction runs post-response if entities detected

---

## Component Dependency Graph

```
ChatOrchestrator
├── ChatPipeline
│   ├── RAGRetriever
│   │   ├── SentenceTransformer (embeddings)
│   │   ├── FAISS / Qdrant (vector index)
│   │   ├── CrossEncoderReranker
│   │   └── ContentSafetyFilter
│   ├── MemoryService
│   │   ├── SessionCache (Redis)
│   │   └── ConversationStore (PostgreSQL)
│   └── WebSearch (DuckDuckGo + httpx)
│
├── LLM Provider Chain
│   ├── FallbackProvider
│   │   ├── HuggingFaceProvider (primary)
│   │   └── OpenAIProvider (fallback)
│   └── CircuitBreaker (per provider)
│
├── Agent System
│   ├── PlannerAgent
│   ├── AgentRouter
│   │   ├── ReasoningAgent
│   │   ├── CodingAgent
│   │   └── ResearchAgent
│   ├── ReasoningGraphEngine
│   │   ├── SequentialExecution
│   │   └── SwarmExecution
│   └── CriticAgent
│
├── Security
│   ├── PromptGuard
│   ├── ContentSafetyFilter
│   └── RefusalGuard
│
├── Reliability
│   ├── CircuitBreaker
│   ├── RetryPolicy
│   ├── TimeoutController
│   ├── AgentExecutionLimiter
│   └── AgentWatchdog
│
├── Knowledge Systems
│   ├── EntityExtractor → GraphStore
│   ├── SourceTrustEvaluator
│   └── SemanticCache (Redis)
│
├── Prompt Evolution
│   └── PromptEvolutionManager
│
├── Evaluation
│   ├── ResponseGrader
│   └── DatasetBuilder
│
└── Observability
    ├── Prometheus Metrics (80+)
    ├── OpenTelemetry Tracing
    └── ELK JSON Logging
```

---

## Design Decisions

### Why DAG-Based Agent Orchestration?

Linear agent chains cannot handle queries requiring parallel information gathering followed by synthesis. A DAG allows the planner to express dependencies naturally:

```
Memory Lookup ─┐
               ├─→ Synthesize → Critique
Web Search ────┘
```

### Why Dual Vector Backends (FAISS + Qdrant)?

- **FAISS**: Zero-dependency development; in-process, file-persisted, no external service needed
- **Qdrant**: Production-grade with gRPC, horizontal scaling, and persistence guarantees
- The `VectorMemory` facade abstracts backend selection via `VECTOR_BACKEND` setting

### Why Subprocess Plugin Isolation?

Plugins execute arbitrary code. Running them in the API process would expose secrets, file system access, and network access. Subprocess isolation provides:
- Clean environment (only PATH, TEMP, PYTHONPATH)
- Hard timeout via `proc.kill()`
- Memory tracking via `tracemalloc`
- No access to API secrets or file system

### Why Redis for Semantic Cache?

In-memory or FAISS-based caches are local to one process. Redis enables:
- Shared cache across all API replicas
- LRU eviction with configurable bounds
- TTL-based expiration (24 hours default)
- Horizontal scaling without cache fragmentation

### Why Circuit Breaker + Retry + Timeout Composition?

Each primitive solves a different failure mode:

```
TimeoutController → "This call is taking too long"
       │
       ▼
RetryPolicy → "This call failed, try again with backoff"
       │
       ▼
CircuitBreaker → "Too many failures, stop trying for 30s"
```

Composing them creates layered protection: fast failure detection, intelligent retry, and cascading failure prevention.

---

## Technology Stack

| Category | Technology | Purpose |
|----------|-----------|---------|
| **Runtime** | Python 3.12 | Primary language |
| **Web Framework** | FastAPI 0.109+ | Async HTTP server |
| **ASGI Server** | Uvicorn 0.27+ | Production server |
| **Validation** | Pydantic 2.6+ | Request/response schemas |
| **LLM (Primary)** | HuggingFace Inference API | Mistral-7B-Instruct, CodeLlama |
| **LLM (Fallback)** | OpenAI API | GPT-4 Turbo |
| **Embeddings** | SentenceTransformers | all-MiniLM-L6-v2 (384d) |
| **Reranking** | CrossEncoder | ms-marco-MiniLM-L-6-v2 |
| **Vector DB (Dev)** | FAISS-CPU | In-process vector search |
| **Vector DB (Prod)** | Qdrant | Distributed vector database |
| **Cache/Queue** | Redis 5+ | Session cache, rate limiting, Celery broker |
| **Database** | PostgreSQL 15+ | Conversation history |
| **Task Queue** | Celery 5.3+ | Background workers |
| **Metrics** | Prometheus Client | 80+ application metrics |
| **Tracing** | OpenTelemetry SDK | Distributed tracing with OTLP export |
| **Logging** | Python logging + JSON | ELK-compatible structured logs |
| **Log Shipping** | Filebeat | Log aggregation to Elasticsearch |
| **PDF Parsing** | pdfminer.six | Document text extraction |
| **Web Scraping** | BeautifulSoup4 + httpx | Content extraction |
| **Search** | duckduckgo-search | Web search API |
| **Tokenization** | tiktoken | Token counting for budget management |
| **Sanitization** | bleach | HTML/injection sanitization |
| **Container** | Docker + Docker Compose | Containerized deployment |
| **CI/CD** | GitHub Actions | Automated pipeline |
| **Linting** | Ruff | Fast Python linter |
| **Type Checking** | mypy | Static type analysis |
| **Security Scan** | Bandit | Static security analysis |
| **Dep Scan** | pip-audit | Dependency vulnerability scanning |
