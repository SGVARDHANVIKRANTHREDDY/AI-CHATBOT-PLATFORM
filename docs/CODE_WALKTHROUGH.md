# Code Walkthrough

> Module-by-module walkthrough of the AI Chatbot Platform codebase — from entry point through every subsystem.

---

## Table of Contents

- [Entry Point](#entry-point)
- [API Layer](#api-layer)
- [Middleware Stack](#middleware-stack)
- [Chat Orchestrator](#chat-orchestrator)
- [Request Processing Pipeline](#request-processing-pipeline)
- [Agent System](#agent-system)
- [Reasoning Graph Engine](#reasoning-graph-engine)
- [LLM Providers](#llm-providers)
- [RAG Pipeline](#rag-pipeline)
- [Memory System](#memory-system)
- [Vector Memory](#vector-memory)
- [Reliability Layer](#reliability-layer)
- [Security Layer](#security-layer)
- [Plugin System](#plugin-system)
- [Background Workers](#background-workers)
- [Observability](#observability)
- [Evaluation System](#evaluation-system)
- [Reasoning Graph](#reasoning-graph)

---

## Entry Point

**File:** `run.py`

```python
import uvicorn
from app.api.main import app
from app.config.settings import settings

if __name__ == "__main__":
    uvicorn.run(
        "app.api.main:app",
        host=settings.API_HOST,
        port=settings.API_PORT,
        reload=settings.DEBUG
    )
```

Starts a Uvicorn ASGI server hosting the FastAPI application. In development, `reload=True` enables hot-reloading.

---

## API Layer

**File:** `app/api/main.py`

The FastAPI application factory does three things:

### 1. Initialize Tracing

```python
init_tracing(
    service_name="ai-platform",
    otlp_endpoint=os.getenv("OTLP_ENDPOINT"),
    enabled=os.getenv("TRACING_ENABLED", "true").lower() == "true",
)
```

OpenTelemetry tracing is initialized at module import time, before any requests are processed.

### 2. Create the FastAPI App

```python
app = FastAPI(
    title=f"{settings.ASSISTANT_NAME} API",
    version="3.0 (Elite AI Assistant Architecture)"
)
```

### 3. Register Middleware and Routes

Middleware is added in reverse execution order (Starlette processes bottom-to-top):

```python
app.add_middleware(SecurityHeadersMiddleware)      # Adds security headers
app.add_middleware(CorrelationMiddleware)           # Injects correlation ID
app.add_middleware(CORSMiddleware, ...)             # CORS handling
app.add_middleware(TimeoutMiddleware, ...)          # Request timeout
app.add_middleware(RequestSizeLimitMiddleware, ...) # Body size limit
app.add_middleware(JWTAuthMiddleware, ...)          # JWT authentication
app.add_middleware(AbuseDetectionMiddleware)        # Abuse logging for SIEM
```

Routes are included from `app/api/routes/chat.py`.

---

## Middleware Stack

Request execution flows through middleware in this order:

```
Request
  │
  ▼ AbuseDetectionMiddleware — logs suspicious patterns for SIEM
  ▼ JWTAuthMiddleware — validates JWT, populates request.state.user_id
  ▼ RequestSizeLimitMiddleware — rejects bodies > MAX_REQUEST_BODY_BYTES
  ▼ TimeoutMiddleware — enforces REQUEST_TIMEOUT_SECONDS
  ▼ CORSMiddleware — handles preflight and CORS headers
  ▼ CorrelationMiddleware — generates X-Request-ID for tracing
  ▼ SecurityHeadersMiddleware — adds X-Content-Type-Options, HSTS, etc.
  ▼ Route Handler (chat endpoint)
  │
  ▼ Response (same middleware in reverse)
```

### Token Bucket Rate Limiter

The rate limiter uses Redis-backed token buckets with two tiers:

```python
rate_limiter = TokenBucketRateLimiter(
    redis_url=settings.REDIS_URL,
    general=BucketConfig(capacity=API_RATE_LIMIT, refill_per_second=...),
    agent=BucketConfig(capacity=AGENT_RATE_LIMIT, refill_per_second=...),
)
```

- **GENERAL_BUCKET:** Standard API requests
- **AGENT_BUCKET:** Agent-mode requests (higher cost per request)

---

## Chat Orchestrator

**File:** `app/orchestrator/chat_orchestrator.py`

The `ChatOrchestrator` is the brain of the platform. It initializes and wires together every subsystem:

### Constructor — Subsystem Initialization

```python
class ChatOrchestrator:
    def __init__(self, pipeline: ChatPipeline, llm_provider: Any):
        # Core infrastructure
        self.pipeline = pipeline              # Context gathering pipeline
        self.llm = llm_provider               # Primary LLM provider
        self.guard = PromptGuard()            # Prompt injection scanner
        self.sem_cache = SemanticCache()       # Semantic similarity cache

        # Model routing
        self.router = ModelRouter()            # Routes to specialized models

        # Tool execution
        self.tool_runner = StreamingToolRunner()
        self.neural_tool_router = NeuralToolRouter()

        # Multi-agent system
        self.planner = PlannerAgent(...)       # Query decomposition
        self.agent_router = AgentRouter()      # Agent selection
        self.critic = CriticAgent(...)         # Self-correction

        # Knowledge & plugins
        self.kg_extractor = EntityExtractor(...)
        self.plugins = PluginRegistry()

        # Reasoning engine
        self.graph_engine = ReasoningGraphEngine(...)

        # Evaluation
        self.grader = ResponseGrader(...)
        self.dataset = DatasetBuilder()

        # Reliability hardening
        self._llm_circuit = CircuitBreaker("llm_provider", ...)
        self._llm_retry = RetryPolicy("llm_provider", ...)
        self._llm_timeout = TimeoutController("llm_ask", ...)
        self._response_validator = ResponseValidator(...)
        self._agent_limiter = AgentExecutionLimiter(...)
        self._watchdog = AgentWatchdog(...)
        self._memory_controller = UnifiedMemoryController(...)
```

---

## Request Processing Pipeline

The `generate_answer` method implements a **10-step pipeline**:

```
Step 0:  Prompt Security Check (PromptGuard)
         └── Rejects prompt injection attempts

Step 0b: Semantic Cache Lookup
         └── Returns cached response if cosine similarity ≥ 0.92

Step 1:  Intelligence & Context Gathering
         ├── ModelRouter.route() → selects specialized model
         ├── MemoryRetriever.retrieve_context() → long-term memory
         └── ChatPipeline.gather_context() → RAG + web + memory

Step 2:  Safety Refusal (Pre-LLM)
         └── decide_refusal() → blocks low-confidence RAG answers

Step 3:  Agentic Intelligence Execution
         ├── Stream mode: LLM stream + tool wrapping
         └── Non-stream: Full agent loop (bounded by AgentExecutionLimiter)
             ├── PlannerAgent → decomposes into reasoning graph
             ├── ReasoningGraphEngine → executes DAG nodes
             ├── Agent dispatching (research/reasoning/coding)
             └── CriticAgent → self-correction

Step 4:  Response Validation (ResponseValidator)
         ├── Hallucinated tool call detection
         ├── Prompt injection in output detection
         └── Length limit enforcement

Step 5:  Post-Processing
         ├── Knowledge graph extraction
         ├── Response grading (LLM-as-judge)
         └── Prompt evolution feedback recording

Step 6:  Memory Persistence
         ├── UnifiedMemoryController.store_interaction()
         ├── MemoryService session append
         └── Semantic cache write
```

### Reliability-Wrapped LLM Call

Every LLM call goes through a three-layer protection stack:

```python
async def _safe_llm_ask(self, prompt, system_prompt="", model=None):
    # Call chain: CircuitBreaker → RetryPolicy → TimeoutController → LLM
    async def _timed_ask():
        return await self._llm_timeout.execute(self.llm.ask, prompt, ...)

    async def _retried_ask():
        return await self._llm_retry.execute(_timed_ask)

    return await self._llm_circuit.call(_retried_ask)
```

---

## Agent System

### Agent Router (`app/agents/agent_router.py`)

Routes queries to specialist agents based on intent classification:

```python
class AgentRouter:
    def register_agent(self, name: str, execute_fn: Callable):
        self.agents[name] = execute_fn

    async def route_and_execute(self, agent_name: str, task: str, state: AgentState):
        return await self.agents[agent_name](task, state)
```

### Agent State (`app/agents/agent_state.py`)

Shared mutable state passed through agent execution:

```python
class AgentState:
    session_id: str
    completed_steps: List[str]      # Trace of completed steps
    task_graph: Optional[dict]      # Current task plan
    context: Dict[str, Any]         # Accumulated context
```

### Specialist Agents

| Agent | File | Responsibilities |
|-------|------|-----------------|
| `PlannerAgent` | `planner_agent.py` | Decomposes complex queries into reasoning graph DAGs |
| `ResearchAgent` | `research_agent.py` | Information synthesis using RAG + tools |
| `ReasoningAgent` | `reasoning_agent.py` | Analytical and logical reasoning |
| `CodingAgent` | `coding_agent.py` | Code generation, review, and explanation |
| `CriticAgent` | `critic_agent.py` | Response quality evaluation and self-correction |

### Watchdog (`app/orchestrator/watchdog.py`)

Enforces hard limits on agent execution:

```python
class AgentWatchdog:
    async def guarded_execute(self, exec_id, guarded_fn, session_id):
        ctx = AgentExecutionContext(execution_id=exec_id)
        # Monitors: MAX_ITERATIONS, MAX_TOOL_CALLS, WALL_CLOCK_TIMEOUT
        # Terminates loop if budget exceeded → returns partial results
```

---

## Reasoning Graph Engine

**File:** `app/reasoning_graph/engine.py`

Executes PlannerAgent's DAG using topological ordering:

```python
class ReasoningGraphEngine:
    async def execute(self, graph: ReasoningGraph, state: AgentState, ctx):
        # 1. Topological sort of DAG nodes
        # 2. For each node (respecting dependencies):
        #    a. Select agent via AgentRouter
        #    b. Execute agent with task description
        #    c. Store result in node
        #    d. Check watchdog budget (ctx.check())
        # 3. Return list of (prompt_key, version_id) pairs
```

### Node Types

```python
class NodeType(Enum):
    RESEARCH = "research"    # → ResearchAgent
    REASONING = "reasoning"  # → ReasoningAgent
    CODING = "coding"        # → CodingAgent
    TOOL = "tool"            # → Direct tool execution
    SYNTHESIS = "synthesis"  # → LLM synthesis call
```

---

## LLM Providers

**File:** `app/llm/base.py`

```python
class LLMProvider(ABC):
    async def ask(self, prompt: str, system_prompt: str = "", **kwargs) -> str: ...
    async def ask_stream(self, prompt: str, **kwargs) -> AsyncIterator[str]: ...
    async def health_check(self) -> bool: ...
```

### Provider Chain

```
ModelRouter.route(query)
    │
    ├── intent: "code"    → MODEL_CODING (CodeLlama-13b)
    ├── intent: "reason"  → MODEL_REASONING (Mistral-7B)
    ├── intent: "summary" → MODEL_SUMMARIZATION (distilbart)
    └── intent: "general" → Default model
    │
    ▼
FallbackProvider
    ├── Primary: HuggingFaceProvider (Inference API)
    └── Fallback: OpenAIProvider (GPT-4-turbo)
        └── Protected by: CircuitBreaker per provider
```

### Adaptive Token Budgeting

**File:** `app/llm/tokenizer/adaptive_budget.py`

Uses `tiktoken` (cl100k_base encoding) to dynamically allocate context budget:
- System prompt → fixed (never truncated)
- User query → fixed (never truncated)
- RAG context → 60% of remaining budget
- Memory context → 40% of remaining budget
- Response reserve → ~500 tokens

---

## RAG Pipeline

### Retrieval Flow

```
Query → EmbeddingService.embed(query)
         │ (384-dim, all-MiniLM-L6-v2)
         ▼
     VectorStore.search(embedding, top_k=20)
         │ (FAISS or Qdrant)
         ▼
     CrossEncoderReranker.rerank(query, results, top_k=5)
         │ (ms-marco-MiniLM-L-6-v2)
         ▼
     Return top-5 reranked chunks with scores
```

### Knowledge Crawler (`app/rag/crawler.py`)

Runs as a Celery beat task (04:00 UTC daily):
- Fetches configured web sources
- Chunks documents
- Generates embeddings
- Stores in vector database

---

## Memory System

### Architecture

```
UnifiedMemoryController
    │
    ├── SessionCache (Redis)
    │   └── Short-term: current conversation turns
    │
    ├── ConversationStore (PostgreSQL)
    │   └── Long-term: full conversation history
    │
    ├── MemoryRetriever (VectorMemory)
    │   └── Semantic: embedding-based memory search
    │
    ├── Summarizer
    │   └── Compresses long conversations for context window
    │
    └── MemoryAuthorityResolver
        └── Resolves conflicts between memory sources
```

### Store Interaction Flow

```python
async def store_interaction(self, session_id, question, answer, kg_data):
    # 1. Append to session cache (Redis)
    # 2. Persist to conversation store (PostgreSQL)
    # 3. Embed and store in vector memory (for semantic retrieval)
    # 4. Store knowledge graph entities
```

---

## Vector Memory

### VectorStore ABC

```python
class VectorStore(ABC):
    async def store(self, doc_id, embedding, metadata) -> None: ...
    async def search(self, query_embedding, top_k=5) -> List[dict]: ...
    async def delete(self, doc_id) -> None: ...
    async def count(self) -> int: ...
```

### Backend Selection

```python
# In VectorMemory facade
if settings.VECTOR_STORE == "faiss":
    self.store = FAISSVectorStore(dimension=384, persist_dir=...)
elif settings.VECTOR_STORE == "qdrant":
    self.store = QdrantVectorStore(host=..., port=..., collection=...)
```

- **FAISS:** In-process, single-machine, development use
- **Qdrant:** Async gRPC, distributed, production use

---

## Reliability Layer

### Composition Pattern

The orchestrator composes three reliability primitives around every LLM call:

```python
async def _safe_llm_ask(self, prompt, system_prompt="", model=None):
    # Layer 1: Circuit Breaker — fail fast if provider is down
    # Layer 2: Retry Policy — exponential backoff with jitter
    # Layer 3: Timeout Controller — hard wall-clock limit

    async def _inner():
        return await self.retry_policy.execute(
            self.timeout_controller.execute,
            self.llm.ask, prompt, system_prompt, model
        )
    return await self.circuit_breaker.call(_inner)
```

### Circuit Breaker States

```
CLOSED ──(failure count ≥ 5)──→ OPEN ──(30s elapsed)──→ HALF_OPEN
   ↑                                                        │
   └─────────── (probe succeeds) ──────────────────────────┘
   
HALF_OPEN ──(probe fails)──→ OPEN   (back to waiting)
```

- **CLOSED** — Normal operation; failures counted
- **OPEN** — All calls rejected immediately; returns fallback or raises `CircuitOpenError`
- **HALF_OPEN** — Limited probe calls allowed (3 max); success resets to CLOSED

### Retry Policy

```python
class RetryPolicy:
    # Decorrelated jitter prevents thundering herd
    delay = min(max_delay, random.uniform(base_delay, previous_delay * 3))
    
    # Default retryable exceptions
    retryable = [ConnectionError, TimeoutError, asyncio.TimeoutError, OSError]
```

### Timeout Controller

```python
class TimeoutController:
    async def execute(self, coro_fn, *args, **kwargs):
        return await asyncio.wait_for(coro_fn(*args, **kwargs), timeout=self.timeout)
```

### Response Validator

After the LLM responds, `ResponseValidator` checks:

1. **Length** — Truncates at sentence boundary if > 16,384 chars
2. **Hallucinated tools** — Detects `<tool_call: unknown_tool(...)>` referencing non-existent tools
3. **JSON conformance** — Validates against expected schema (if provided)
4. **Injection in output** — Scans for `<script>`, `javascript:`, `on*=` patterns
5. **Auto-fix** — Applies automatic corrections for truncation

### Load Guard

```python
class AgentExecutionLimiter:
    # Bounds concurrent agent executions to prevent resource exhaustion
    # Queues overflow with configurable timeout
    async def acquire(self):
        await asyncio.wait_for(self.semaphore.acquire(), timeout=self.queue_timeout)
```

### Failure Tracker

Shared intelligence between circuit breaker and retry policy:

```python
class FailureTracker:
    # Sliding window of pass/fail counts per component
    def record_success(self): self.passes += 1
    def record_failure(self): self.failures += 1
    def failure_rate(self) -> float: return self.failures / (self.passes + self.failures)
```

---

## Security Layer

### Three-Layer Defense

```
Input Defense          Processing Defense       Output Defense
─────────────         ───────────────────       ──────────────
PromptGuard           ContentSafetyFilter       ResponseValidator
  │                     │                         │
  ├─ 10 regex          ├─ 19 weighted rules      ├─ Length truncation
  │  patterns          ├─ Domain reputation       ├─ Tool call strip
  └─ Quick scan        ├─ Content quality         ├─ Injection scan
                       └─ Quarantine store        └─ HTML sanitize
```

### PromptGuard (Input Layer)

```python
class PromptGuard:
    _PATTERNS = [
        r"ignore\s+(all\s+)?(previous|prior|above)\s+(instructions|rules)",
        r"you\s+are\s+now\s+(a|an)\s+",
        r"(reveal|show|print|output)\s+(your\s+)?(system\s+)?prompt",
        r"DAN\s+mode",
        r"jailbreak",
        # ... 10 patterns total
    ]
    
    def scan(self, text: str) -> bool:
        return any(re.search(p, text, re.IGNORECASE) for p in self._PATTERNS)
```

### ContentSafetyFilter (Processing Layer)

```python
class ContentSafetyFilter:
    def scan(self, document) -> SafetyVerdict:
        injection_score = self._check_injection(document.text)  # 19 weighted rules
        trust_score = self._check_source(document.url)          # Domain reputation
        quality_score = self._check_quality(document.text)      # Text statistics
        
        return SafetyVerdict(
            rejected=(injection_score > INJECTION_THRESHOLD),
            quarantined=(injection_score > QUARANTINE_THRESHOLD),
        )
```

### RefusalGuard (Output Layer)

```python
def decide_refusal(use_rag: bool, top_rag_score: float) -> Optional[RefusalDecision]:
    if use_rag and top_rag_score < RAG_MIN_SCORE:  # 0.35 default
        return RefusalDecision(
            should_refuse=True,
            message="I don't have enough reliable information to answer this question."
        )
```

### Request Flow Through Security

```
User Input
  │
  ▼ PromptGuard.scan(input)           — blocks injection patterns
  ▼ ContentSafetyFilter.analyze(input) — threat scoring
  ▼ [LLM processes request]
  ▼ ResponseValidator.validate(output) — catches injection in output
  ▼ RefusalGuard.decide_refusal(...)   — blocks low-confidence answers
  │
  ▼ Safe response to user
```

### Output Injection Detection

The ResponseValidator scans LLM output for injection patterns before returning to the user:

```python
_INJECTION_PATTERNS = [
    r"ignore\s+(all\s+)?(previous|above|prior)\s+(instructions|prompts|rules)",
    r"you\s+are\s+now\s+(a|an|the)\s+",
    r"system\s*:\s*",
    r"act\s+as\s+(if|though)\s+you",
    r"(jailbreak|DAN|do anything now)",
    r"<\s*/?script",
]
```

---

## Plugin System

### Lifecycle

```
PluginRegistry.discover()
  │
  ├── Scans plugin directory
  ├── Validates PluginProtocol compliance
  ├── Calls plugin.initialize()
  └── Registers plugin tools into ToolRunner
```

### Sandbox Execution

Plugins run in a restricted environment:
- Blocked imports: `os`, `sys`, `socket`, `subprocess`
- No file system access
- Memory and CPU limits
- Execution timeout

---

## Background Workers

### Celery Configuration

```python
# workers/celery_app.py
app = Celery("chatbot", broker=REDIS_URL, backend=REDIS_URL)
app.conf.task_serializer = "json"
app.conf.beat_schedule = {
    "daily-vector-maintenance": {
        "task": "run_vector_maintenance",
        "schedule": crontab(hour=3, minute=0),      # 03:00 UTC
    },
    "daily-knowledge-crawl": {
        "task": "crawl_and_ingest",
        "schedule": crontab(hour=4, minute=0),      # 04:00 UTC
    },
}
```

### Worker Tasks

| Task | Module | Purpose | Schedule |
|------|--------|---------|----------|
| `ingest_documents` | `ingestion_worker` | Build RAG index from uploaded docs | On demand |
| `rebuild_index` | `ingestion_worker` | Force full FAISS rebuild | On demand |
| `crawl_and_ingest` | `knowledge_builder` | Crawl approved URLs, trust-score, ingest | Daily 04:00 UTC |
| `expand_knowledge_graph` | `knowledge_builder` | Extract entities from recent ingestions | On demand |
| `run_vector_maintenance` | `maintenance_worker` | Deduplicate, remove stale, reindex | Daily 03:00 UTC |

### Ingestion Worker

```python
@celery_app.task(name="ingest_documents")
def ingest_documents():
    retriever = RAGRetriever()
    retriever.extract_texts()      # Parse PDFs, TXT, MD
    retriever.chunk_text(...)      # Sentence-based chunking
    retriever.embed_documents()    # Encode chunks → FAISS index
    return {"status": "ok", "chunks": len(retriever.chunks)}
```

### Knowledge Builder

```python
@celery_app.task(name="crawl_and_ingest")
def crawl_and_ingest(urls=None, trust_threshold=0.5):
    crawler = KnowledgeCrawler()
    evaluator = SourceTrustEvaluator()
    for url in urls:
        trust = evaluator.evaluate(url, content)
        if trust.overall_score >= trust_threshold:
            # Accept: ingest into RAG pipeline
        else:
            # Reject: log rejection with reasons
```

### Maintenance Worker

```python
@celery_app.task(name="run_vector_maintenance", soft_time_limit=600, time_limit=660)
def run_vector_maintenance(memory_types=None):
    # 1. Deduplicate vectors (cosine similarity ≥ 0.98)
    # 2. Remove stale entries (> 90 days old)
    # 3. Reindex for optimal search performance
    # 4. Compress old vectors to reduce storage
```

---

## Observability

### Prometheus Metrics

```python
# app/shared/monitoring.py — 80+ metrics across 8 categories

# Latency (Histogram)
REQUEST_LATENCY           # End-to-end request duration
RAG_RETRIEVAL_LATENCY     # Vector search + reranking
LLM_CALL_DURATION         # Per-provider LLM call time
TOOL_SELECTION_LATENCY    # Neural tool routing time

# Throughput (Counter)
LLM_TOKEN_USAGE           # Tokens consumed per model
TOOL_CALL_COUNT            # Tool invocations by name
RAG_HIT_COUNT              # RAG retrieval hit/miss
KNOWLEDGE_BUILDER_DOCS     # Documents processed by workers

# Cache (Gauge/Counter)
SEMANTIC_CACHE_HITS        # Cache hit rate
SEMANTIC_CACHE_SIZE        # Current cache entries
SEMANTIC_CACHE_LLM_SAVINGS # LLM calls avoided

# Reliability (Counter)
LLM_REQUEST_FAILURES       # Failed LLM calls by provider
AGENT_CRASHES              # Agent execution failures
RESPONSE_VALIDATION_ISSUES # Validator-detected problems

# AI Quality (Histogram/Gauge)
HALLUCINATION_RATE         # Critic-detected hallucinations
PROMPT_EVOLUTION_SCORE     # A/B test performance scores
SWARM_AGENT_COUNT          # Concurrent agent count
```

### Distributed Tracing

```python
# app/shared/tracing.py
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

def init_tracing(service_name="nimbus", otlp_endpoint="http://localhost:4317"):
    provider = TracerProvider(resource=Resource({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_endpoint)))
    trace.set_tracer_provider(provider)

# Usage via decorator
@traced("llm_call", attributes={"provider": "huggingface"})
async def ask(prompt):
    ...
```

### Structured Logging

```python
# app/config/logging_config.py
class ELKJsonFormatter(logging.Formatter):
    def format(self, record):
        return json.dumps({
            "@timestamp": datetime.utcnow().isoformat(),
            "log.level": record.levelname,
            "log.logger": record.name,
            "message": record.getMessage(),
            "service.name": "nimbus",
            "event_data": getattr(record, "event_data", {}),
        })
```

### Monitoring Stack Deployment

```bash
cd infra/monitoring
docker compose -f docker-compose.observability.yml up -d

# Services:
# - Prometheus     → http://localhost:9090  (metrics collection)
# - Grafana        → http://localhost:3000  (dashboards)
# - Jaeger         → http://localhost:16686 (trace visualization)
# - Elasticsearch  → http://localhost:9200  (log aggregation)
# - Kibana         → http://localhost:5601  (log dashboards)
# - Filebeat       → log shipping to Elasticsearch
```

---

## Evaluation System

### Response Grader

```python
class ResponseGrader:
    async def grade(self, query, response) -> dict:
        # Uses LLM-as-a-judge to evaluate:
        # - Correctness:          Is the answer factually accurate?
        # - Completeness:         Does it cover all aspects?
        # - Reasoning Soundness:  Is the logic valid?
        return {
            "score": 0.85,                  # 0.0 to 1.0
            "logical_consistency": True,
            "hallucinations_detected": False,
            "feedback": "Clear and accurate response."
        }
```

### Dataset Builder

```python
class DatasetBuilder:
    def log_interaction(self, query, response, grade, plan):
        # Records every interaction for continuous improvement
        # Low-score responses are flagged for manual review
        # Data persisted as JSON for model fine-tuning
```

### Prompt Evolution Integration

The evaluation system feeds directly into prompt evolution:

```
Query → Agent Response → Critic Evaluation → ResponseGrader Score
                                                    │
                                              PromptEvolutionManager
                                                    │
                                    ┌───────────────┴───────────────┐
                                    ▼                               ▼
                              Promote Candidate              Reject Candidate
                            (outperforms active)           (underperforms active)
```

---

## Reasoning Graph

### Graph Execution Flow

```python
class ReasoningGraphEngine:
    async def execute(self, graph, state, exec_ctx):
        # 1. Strategy selects execution order (sequential or swarm)
        # 2. For each ready node:
        #    a. Route to appropriate agent via AgentRouter
        #    b. Execute node (REASONING, TOOL_CALL, or MEMORY_LOOKUP)
        #    c. Record result in AgentState
        #    d. Update watchdog counters
        # 3. Return version pairs for prompt evolution tracking
```

### Node Types

| Type | Handler | Description |
|------|---------|-------------|
| `REASONING` | ReasoningAgent or CodingAgent | Pure analytical reasoning |
| `TOOL_CALL` | StreamingToolRunner | Execute a tool and return result |
| `MEMORY_LOOKUP` | MemoryRetriever | Search vector/conversation memory |

### Graph Limits

- `MAX_NODES = 50` — Maximum nodes per reasoning graph
- `MAX_DEPTH = 10` — Maximum dependency chain depth
- Enforced at `add_node()` time to prevent unbounded graphs
