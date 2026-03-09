# Performance

> Performance characteristics, optimization strategies, caching layers, token budgeting, and bottleneck analysis.

---

## Table of Contents

- [Performance Architecture](#performance-architecture)
- [Semantic Cache](#semantic-cache)
- [Adaptive Token Budgeting](#adaptive-token-budgeting)
- [RAG Retrieval Performance](#rag-retrieval-performance)
- [Embedding Performance](#embedding-performance)
- [LLM Provider Latency](#llm-provider-latency)
- [Connection Pooling](#connection-pooling)
- [Bottleneck Analysis](#bottleneck-analysis)
- [Performance Tuning](#performance-tuning)

---

## Performance Architecture

```
Request → Rate Limiter → Semantic Cache ──hit──→ Response (< 50ms)
                              │ miss
                              ▼
                      Context Builder
                    ┌─────────┴──────────┐
                    │                    │
              RAG Retriever        Memory Service
              (FAISS/Qdrant)       (Redis + PG)
                    │                    │
                    ▼                    ▼
              Cross-Encoder        Token Budgeter
              Reranking            (tiktoken)
                    │                    │
                    └─────────┬──────────┘
                              ▼
                        LLM Provider
                     (HF API / OpenAI)
                              │
                              ▼
                      Response Guard
                              │
                              ▼
                    Cache Write + Response
```

---

## Semantic Cache

**File:** `app/cache/semantic_cache.py`

The semantic cache avoids redundant LLM calls by matching incoming queries against previously answered questions using cosine similarity on embeddings.

### How It Works

1. Query arrives → compute embedding (384-dim, `all-MiniLM-L6-v2`)
2. Compare against cached query embeddings using cosine similarity
3. If similarity ≥ **0.92** → return cached response (cache hit)
4. If similarity < 0.92 → proceed with full pipeline (cache miss)
5. After LLM response → store (query_embedding, response) in cache

### Configuration

| Parameter | Value | Rationale |
|-----------|-------|-----------|
| Similarity threshold | 0.92 | Balances hit rate vs. accuracy |
| Max entries | 1000 | LRU eviction beyond this |
| Backend | Redis | Shared across API replicas |
| TTL | Configurable | Prevents stale responses |
| Embedding model | `all-MiniLM-L6-v2` | Fast, 384-dim, good semantic quality |

### Performance Impact

| Metric | Without Cache | With Cache (warm) |
|--------|--------------|-------------------|
| Avg response time | 2–5s | < 50ms |
| LLM API calls/hour | 100% | ~40–60% (depending on query diversity) |
| Cost reduction | Baseline | 40–60% fewer API calls |

### Cache Hit Criteria

```python
cosine_similarity = dot(query_embedding, cached_embedding)
                     / (norm(query_embedding) * norm(cached_embedding))

if cosine_similarity >= 0.92:
    return cached_response  # Cache hit
```

---

## Adaptive Token Budgeting

**File:** `app/llm/tokenizer/`

The adaptive token budgeter dynamically allocates token budget across prompt components using `tiktoken` with the `cl100k_base` encoding.

### Budget Allocation

```
Total Context Window (e.g., 4096 tokens)
├── System Prompt:    ~300 tokens (fixed)
├── User Query:       variable (measured)
├── RAG Context:      60% of remaining budget
├── Memory Context:   40% of remaining budget
└── Response Reserve: ~500 tokens (fixed)
```

### Priority System

When total context exceeds the budget:

| Priority | Component | Strategy |
|----------|-----------|----------|
| 1 (highest) | System prompt | Never truncated |
| 2 | User query | Never truncated |
| 3 | RAG context | Truncated by relevance score (lowest-scoring chunks dropped first) |
| 4 | Memory context | Summarized or truncated |

### Token Counting

```python
import tiktoken

encoder = tiktoken.get_encoding("cl100k_base")

def count_tokens(text: str) -> int:
    return len(encoder.encode(text))

# Budget calculation
remaining = max_tokens - count_tokens(system_prompt) - count_tokens(user_query) - response_reserve
rag_budget = int(remaining * 0.6)
memory_budget = int(remaining * 0.4)
```

---

## RAG Retrieval Performance

### Pipeline Latency Breakdown

| Stage | Typical Latency | Description |
|-------|----------------|-------------|
| Query embedding | 5–15ms | SentenceTransformers encode |
| Vector search | 1–5ms (FAISS) / 10–30ms (Qdrant) | Top-k nearest neighbors |
| Cross-encoder reranking | 20–80ms | `ms-marco-MiniLM-L-6-v2` on top-k results |
| Total retrieval | 30–100ms | End-to-end |

### Optimization Techniques

1. **Pre-computed embeddings:** Documents embedded at ingestion time, not query time
2. **FAISS IVF indexes:** For large collections, use IVF (Inverted File) for sub-linear search
3. **Reranking top-k:** Only rerank top 20 results (not entire collection)
4. **Metadata filtering:** Pre-filter by source trust score before vector search
5. **Batch embedding:** Batch multiple chunks in a single forward pass

### Reranker Configuration

```python
reranker = CrossEncoderReranker(
    model_name="cross-encoder/ms-marco-MiniLM-L-6-v2",
    top_k=5,          # Return top 5 after reranking
    batch_size=32,     # Batch cross-encoder inference
)
```

---

## Embedding Performance

### Model: `all-MiniLM-L6-v2`

| Metric | Value |
|--------|-------|
| Embedding dimension | 384 |
| Single query latency | 5–15ms (CPU) |
| Batch throughput | ~500 texts/s (CPU) |
| Model size | ~80 MB |
| Normalization | L2-normalized |

### Optimization

```python
embedding_service = EmbeddingService(
    model_name="all-MiniLM-L6-v2",
    normalize=True,        # L2 normalize for cosine similarity
    batch_size=64,         # Optimal batch size for CPU
    show_progress=False,   # Disable progress bar in production
)
```

---

## LLM Provider Latency

### HuggingFace Inference API (Primary)

| Metric | Value |
|--------|-------|
| Model | Mistral-7B-Instruct-v0.2 |
| Cold start | 10–30s (model loading) |
| Warm request | 1–5s (depending on output length) |
| Max tokens | 1024 (configurable) |
| Timeout | 30s (TimeoutController) |

### OpenAI (Fallback)

| Metric | Value |
|--------|-------|
| Model | GPT-4-turbo |
| Typical latency | 2–8s |
| Max tokens | 4096 |
| Timeout | 30s |

### Fallback Behavior

```
Primary (HuggingFace)
    │ failure (5 failures in 60s window)
    ▼
Circuit Breaker OPEN
    │
    ▼
Fallback (OpenAI)
    │ recovery after 30s
    ▼
Circuit Breaker HALF_OPEN → probe → CLOSED (primary restored)
```

---

## Connection Pooling

### Redis (aioredis)

```python
redis_pool = aioredis.ConnectionPool.from_url(
    REDIS_URL,
    max_connections=20,
    decode_responses=True,
)
```

### PostgreSQL (asyncpg)

```python
pool = await asyncpg.create_pool(
    DATABASE_URL,
    min_size=5,
    max_size=20,
    command_timeout=30,
)
```

### HTTP (httpx for LLM APIs)

```python
async with httpx.AsyncClient(
    timeout=httpx.Timeout(30.0, connect=5.0),
    limits=httpx.Limits(
        max_connections=50,
        max_keepalive_connections=20,
    ),
) as client:
    response = await client.post(...)
```

---

## Bottleneck Analysis

### Common Bottlenecks

| Bottleneck | Symptom | Diagnosis | Solution |
|-----------|---------|-----------|----------|
| LLM API latency | High p99 response time | `llm_request_duration_seconds` > 10s | Enable semantic cache, tune timeout |
| Vector search | Slow retrieval | `vector_search_duration_seconds` > 1s | Optimize index, increase RAM |
| Redis connection exhaustion | Timeouts on cache/session | `redis_connected_clients` near max | Increase pool size, scale Redis |
| PostgreSQL slow queries | Memory service latency | Query logs show > 100ms | Add indexes, use connection pool |
| Token budget overflow | Truncated context | Low answer quality | Increase model context window |
| Embedding computation | High CPU during ingestion | Worker CPU at 100% | Batch embeddings, add GPU |

### Profiling

```python
# Simple timing decorator for diagnosis
import time
import functools

def timed(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.perf_counter()
        result = await func(*args, **kwargs)
        elapsed = time.perf_counter() - start
        print(f"{func.__name__}: {elapsed:.3f}s")
        return result
    return wrapper
```

---

## Performance Tuning

### Quick Wins

1. **Enable semantic cache** — saves 40–60% of LLM calls
2. **Tune reranker top-k** — reducing from 20 to 10 halves reranker latency
3. **Increase Redis pool** — prevents connection wait times under load
4. **Use Qdrant in production** — async gRPC is faster than in-process FAISS for multi-replica setups

### Advanced Tuning

1. **GPU acceleration** — Run embedding model on GPU for 10x throughput
2. **Model quantization** — Use ONNX or quantized models for faster inference
3. **Pre-warm cache** — Populate semantic cache with common queries on startup
4. **Async everywhere** — All I/O operations use `async/await` to maximize concurrency
5. **Token budget tuning** — Adjust RAG/memory split based on query type

---

## Performance Tuning Playbook

Use this playbook to diagnose and fix common performance issues.

### "Response times are too slow" (> 5s for simple queries)

```
Step 1: Check semantic cache hit rate
  → Metric: semantic_cache_hit_rate
  → If < 30%: Cache isn't helping. Check SEMANTIC_CACHE_THRESHOLD (default 0.92).
    Try lowering to 0.88 and monitor for incorrect cached answers.

Step 2: Check LLM latency
  → Metric: llm_request_duration_seconds
  → If > 5s: HuggingFace model may be cold. Consider:
    a) Switch to a faster model (Mistral-7B → smaller variant)
    b) Use a dedicated inference endpoint (avoid shared queue)
    c) Add OpenAI as fallback (lower latency, higher cost)

Step 3: Check RAG retrieval latency
  → Metric: rag_retrieval_duration_seconds
  → If > 100ms: Index may be too large for FAISS.
    a) Switch to Qdrant for async gRPC search
    b) Reduce RERANKER_CANDIDATES from 20 to 10
    c) Check vector index fragmentation (run maintenance worker)
```

### "Semantic cache returns wrong answers"

```
Symptom: Users report getting answers to different questions
Cause:   SEMANTIC_CACHE_THRESHOLD is too low

Fix:
  → Increase threshold: 0.92 → 0.95
  → Trade-off: fewer cache hits, but much higher accuracy
  → Monitor: semantic_cache_hits_total should decrease; user complaints should stop

Debug: Check what's being matched:
  → Set LOG_LEVEL=DEBUG
  → Search logs for "cache_hit" events
  → Compare the cached query vs. the new query
```

### "System becomes unresponsive under load"

```
Step 1: Check request queue
  → Metric: request_queue_size
  → If > 80: System is overloaded.
    a) Scale horizontally (add API replicas)
    b) Lower API_RATE_LIMIT to shed load
    c) Check for retry storms (retry_attempts_total spike)

Step 2: Check agent execution limiter
  → Metric: agent_concurrent_count
  → If near 20: All agent slots are occupied.
    a) Check for stuck agents (agent_budget_exceeded_total)
    b) Reduce MAX_AGENT_ITERATIONS to fail faster
    c) Reduce MAX_SWARM_AGENTS to limit parallel resource usage

Step 3: Check circuit breaker state
  → Metric: circuit_breaker_state
  → If = 1 (OPEN): A provider is down and requests are backing up.
    a) Verify provider health
    b) Ensure fallback provider is configured
```

---

## SLA Targets

Recommended SLA targets based on system design:

| Metric | Target | Measurement |
|--------|--------|-------------|
| **Cache hit response time** | < 100ms (P99) | Time from request to response when cache hits |
| **Simple query response time** | < 5s (P95) | Single-agent, no-swarm queries |
| **Complex query response time** | < 15s (P95) | Multi-agent swarm queries |
| **Availability** | 99.5% | Excluding planned maintenance |
| **Error rate** | < 2% | 5xx responses / total requests |
| **Cache hit rate** | > 30% | After warm-up period (1000+ queries) |
| **Streaming first token** | < 3s (P95) | Time to first `data:` event |

### Capacity Planning

| Resource | Capacity Per Instance | Scaling Strategy |
|----------|----------------------|-----------------|
| API server | ~50 concurrent requests | Horizontal (add replicas behind load balancer) |
| Celery worker | ~10 concurrent tasks | Horizontal (add workers) |
| Redis | ~10,000 cached entries | Vertical (increase memory) |
| FAISS index | ~100,000 vectors (CPU) | Switch to Qdrant for > 100K |
| Qdrant | ~10M vectors per node | Horizontal (sharding) |

---

## Semantic Cache Tuning Guide

The `SEMANTIC_CACHE_THRESHOLD` is the single most impactful performance setting:

| Threshold | Cache Hit Rate | Accuracy Risk | Best For |
|-----------|---------------|---------------|----------|
| 0.85 | Very high (~60%) | **High** — different questions may match | Internal tools with limited query diversity |
| 0.88 | High (~50%) | Moderate — occasional mismatches | Low-risk applications |
| 0.92 (default) | Moderate (~35%) | Low — good balance | Most production deployments |
| 0.95 | Low (~15%) | Very low — only near-duplicates match | High-stakes applications (medical, legal) |
| 0.98 | Very low (~5%) | Minimal — essentially exact match only | When accuracy is critical and latency is secondary |
