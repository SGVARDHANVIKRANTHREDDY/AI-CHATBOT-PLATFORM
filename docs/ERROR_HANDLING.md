# Error Handling

> Exception hierarchy, failure modes, recovery patterns, and graceful degradation across all subsystems.

---

## Table of Contents

- [Exception Hierarchy](#exception-hierarchy)
- [Error Propagation Model](#error-propagation-model)
- [Reliability Layer](#reliability-layer)
- [Component Failure Modes](#component-failure-modes)
- [Recovery Patterns](#recovery-patterns)
- [Graceful Degradation](#graceful-degradation)
- [API Error Responses](#api-error-responses)
- [Logging and Alerting](#logging-and-alerting)

---

## Exception Hierarchy

**File:** `app/shared/exceptions.py`

```
ChatBotError (base)
├── LLMProviderError        — LLM API failures (timeout, rate limit, model error)
├── RAGError                — Retrieval pipeline failures (vector search, reranking)
├── MemoryError             — Memory service failures (Redis, PostgreSQL)
├── ConfigurationError      — Invalid settings or missing environment variables
└── SecurityError           — Content safety violations, prompt injection
```

All platform exceptions inherit from `ChatBotError`, enabling catch-all error handling at the orchestrator level while allowing specific handling where needed.

### Reliability-Specific Exceptions

```
CircuitOpenError            — Circuit breaker is open, call rejected
├── component: str          — Which component's circuit is open
└── retry_after: float      — Seconds until HALF_OPEN probe

RetryExhaustedError         — All retry attempts failed
├── component: str          — Component name
├── attempts: int           — Number of attempts made
└── last_error: Exception   — The final error

LoadGuardRejection          — Request rejected due to load limits
├── limiter_name: str       — Which limiter rejected
├── current: int            — Current usage
└── limit: int              — Maximum allowed

TimeoutError                — Operation exceeded time limit
├── operation: str          — Operation name
└── timeout_seconds: float  — The timeout that was exceeded
```

---

## Error Propagation Model

```
User Request
    │
    ▼
┌─────────────────────────────────────────────────┐
│             API Layer (FastAPI)                  │
│  Catches: all exceptions → HTTP error responses  │
│  Returns: structured JSON with error codes       │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│           Orchestrator (ChatOrchestrator)        │
│  Catches: ChatBotError → graceful degradation    │
│  Falls back to: simpler responses                │
└─────────────────┬───────────────────────────────┘
                  │
    ┌─────────────┼──────────────┐
    ▼             ▼              ▼
┌─────────┐ ┌─────────┐  ┌──────────┐
│  LLM    │ │  RAG    │  │  Memory  │
│Provider │ │Retriever│  │ Service  │
└─────────┘ └─────────┘  └──────────┘
    │             │              │
    ▼             ▼              ▼
┌─────────────────────────────────────────────────┐
│           Reliability Layer                      │
│  CircuitBreaker → RetryPolicy → TimeoutController│
│  Wraps every external call                       │
└─────────────────────────────────────────────────┘
```

---

## Reliability Layer

### Circuit Breaker

**File:** `app/reliability/circuit_breaker.py`

Three-state pattern protecting all external calls:

```
CLOSED ──(≥5 failures in 60s)──→ OPEN ──(30s wait)──→ HALF_OPEN
  ▲                                                       │
  └──────────(probe succeeds)──────────────────────────────┘
                                                          │
                              OPEN ◄──(probe fails)───────┘
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `failure_threshold` | 5 | Failures in window to trip |
| `recovery_timeout` | 30.0s | Time in OPEN before HALF_OPEN |
| `half_open_max_calls` | 3 | Probe calls allowed |

**Behavior:**
- **CLOSED:** All calls pass through. Failures counted via `FailureTracker`.
- **OPEN:** All calls immediately rejected with `CircuitOpenError`. Fallback invoked if configured.
- **HALF_OPEN:** Limited probe calls allowed. Success → CLOSED. Failure → OPEN.

### Retry Policy

**File:** `app/reliability/retry_policy.py`

Exponential backoff with decorrelated jitter:

```python
delay = min(max_delay, random.uniform(base_delay, previous_delay * 3))
```

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_retries` | 3 | Maximum retry attempts |
| `base_delay` | 1.0s | Initial backoff |
| `max_delay` | 30.0s | Maximum backoff cap |
| `retryable_exceptions` | `ConnectionError, TimeoutError, OSError` | What to retry |

Non-retryable exceptions (e.g., `SecurityError`, `ValueError`) are re-raised immediately.

### Timeout Controller

**File:** `app/reliability/timeout_controller.py`

Hard timeout for every external call:

```python
tc = TimeoutController("llm_ask", timeout_seconds=30.0)
result = await tc.execute(llm.ask, prompt)

# Or with fallback for non-critical calls
result = await tc.execute_with_fallback(enrich_fn, default_value, query)
```

### Failure Tracker

**File:** `app/reliability/failure_tracker.py`

Sliding-window failure accounting shared between circuit breaker and retry policy:

- 60-second sliding window
- Thread-safe (uses `threading.Lock`)
- Tracks: total failures, total successes, failure rate, window failures
- Maximum 1000 failure records in memory

### Response Guard

**File:** `app/reliability/response_guard.py`

Validates LLM outputs before they reach users:

| Check | Category | Severity | Action |
|-------|----------|----------|--------|
| Hallucinated tool calls | `hallucinated_tool` | error | Strip invalid tool calls |
| Malformed JSON | `invalid_json` | warning | Return raw text |
| Prompt injection in output | `injection` | error | Block response |
| Excessive length | `length` | warning | Truncate |

### Load Guard

**File:** `app/reliability/load_guard.py`

Three admission control layers:

| Guard | Purpose | Default Limit |
|-------|---------|---------------|
| `RequestQueueLimiter` | Total concurrent API requests | 100 |
| `AgentExecutionLimiter` | Total concurrent agent loops | 20 |
| `SwarmThrottle` | Dynamic swarm parallelism | Adaptive |

---

## Component Failure Modes

### LLM Provider

| Failure | Detection | Recovery |
|---------|-----------|----------|
| API timeout | `TimeoutController` after 30s | Retry with backoff, then fallback provider |
| Rate limit (429) | HTTP status code | Backoff with `Retry-After` header |
| Model error (500) | LLMProviderError | Circuit breaker → fallback provider |
| Invalid response | ResponseGuard | Sanitize or re-prompt |
| Provider down | 5+ failures in 60s | Circuit breaker opens → fallback |

### Vector Store

| Failure | Detection | Recovery |
|---------|-----------|----------|
| FAISS index corruption | Load error | Rebuild from persistent storage |
| Qdrant connection refused | ConnectionError | Retry with backoff |
| Empty results | Zero results returned | Fall back to keyword search or skip RAG |
| Slow search | TimeoutController | Return partial results |

### Redis

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Connection refused | ConnectionError | Retry, then operate without cache |
| Timeout | TimeoutError | Return cache miss, continue pipeline |
| Memory exhaustion | OOM error | LRU eviction handles automatically |

### PostgreSQL

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Connection pool exhausted | Timeout on acquire | Queue request, increase pool |
| Slow query | TimeoutController | Return without conversation history |
| Connection dropped | asyncpg.ConnectionDoesNotExistError | Reconnect via pool |

### Tool Failures

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Tool not found | NeuralToolRouter returns no match above threshold | Skip tool call, proceed with LLM-only response |
| Tool execution error | Exception during `ToolRegistry.execute()` | Log error, inject error context into agent state, continue reasoning |
| Tool timeout | `StreamingToolRunner` timeout (30s default) | Kill execution, return partial result or error message |
| Plugin sandbox crash | Subprocess exit code ≠ 0 | `SandboxRunner` catches, returns error JSON via IPC |
| Plugin resource exhaustion | `tracemalloc` memory cap exceeded | Hard kill via `proc.kill()`, return resource error |
| Web search failure | `httpx.TimeoutException` or DNS failure | Return empty results, agent continues without web context |

### Agent Crashes

| Failure | Detection | Recovery |
|---------|-----------|----------|
| Agent LLM call fails | `LLMProviderError` propagated from provider | Circuit breaker → fallback provider → retry with backoff |
| Agent exceeds iteration budget | `AgentWatchdog` counter > `max_iterations` (default: 10) | `AgentBudgetExceeded` raised → return best partial result |
| Agent exceeds time budget | `AgentWatchdog` timer > `max_duration_seconds` (default: 120s) | Watchdog terminates agent, returns accumulated results |
| Agent produces invalid DAG | `PlannerAgent` returns unparseable JSON | Fallback to simple single-node graph with direct LLM call |
| Swarm agent deadlock | All agents blocked on dependencies | `TaskGraph.get_ready_tasks()` returns empty → force-complete stalled nodes |
| Critic agent rejects response | Quality score < 0.6 threshold | Re-prompt with critic feedback, max 2 retries before accepting best attempt |

---

## Recovery Patterns

### Pattern 1: Fallback Chain

```python
# LLM call with full reliability stack
result = await circuit_breaker.call(
    retry_policy.execute,
    timeout_controller.execute,
    primary_provider.ask,
    prompt
)
# If primary fails → circuit opens → fallback provider invoked
```

### Pattern 2: Degrade and Continue

```python
try:
    rag_context = await rag_retriever.retrieve(query)
except RAGError:
    rag_context = []  # Continue without RAG context
    logger.warning("RAG unavailable, proceeding without context")
```

### Pattern 3: Cache Shield

```python
# Semantic cache shields LLM from redundant calls
cached = await semantic_cache.get(query)
if cached:
    return cached  # Avoids calling potentially degraded LLM

response = await llm.ask(query)
await semantic_cache.put(query, response)  # Protect future calls
```

---

## Graceful Degradation

The system degrades in tiers rather than failing completely:

| Tier | Components Down | User Experience |
|------|----------------|-----------------|
| **Full** | All healthy | Complete response with RAG context + memory |
| **Degraded-1** | RAG unavailable | Response without retrieved context |
| **Degraded-2** | RAG + Memory down | Direct LLM response (no context) |
| **Degraded-3** | Primary LLM down | Fallback provider (possibly different quality) |
| **Degraded-4** | All LLMs down | Cached response if available |
| **Unavailable** | All systems down | HTTP 503 with retry-after |

---

## API Error Responses

### Standard Error Format

```json
{
  "error": {
    "code": "LLM_PROVIDER_ERROR",
    "message": "Service temporarily unavailable",
    "retry_after": 30
  }
}
```

### HTTP Status Codes

| Status | Condition |
|--------|-----------|
| 400 | Invalid request body |
| 401 | Missing or invalid authentication |
| 413 | Request body too large |
| 422 | Validation error (Pydantic) |
| 429 | Rate limit exceeded |
| 500 | Internal server error |
| 503 | Service unavailable (all providers down) |

---

## Logging and Alerting

### Error Logging

All errors are logged with structured JSON including:
- `error_type`: Exception class name
- `component`: Which subsystem failed
- `session_id`: Request correlation
- `trace_id`: OpenTelemetry trace ID
- `stack_trace`: Full traceback (error level only)

### Alert Triggers

| Condition | Severity | Alert |
|-----------|----------|-------|
| Circuit breaker opens | Warning | PagerDuty notification |
| All retries exhausted | Error | Immediate alert |
| Load guard rejecting > 10% | Warning | Slack notification |
| Error rate > 5% (5-min window) | Critical | PagerDuty escalation |
| Response guard blocking > 1% | Warning | Log review needed |
