# Configuration

> Complete reference for all environment variables and configuration settings.

---

## Table of Contents

- [Configuration System](#configuration-system)
- [LLM Configuration](#llm-configuration)
- [RAG Configuration](#rag-configuration)
- [Memory Configuration](#memory-configuration)
- [API Configuration](#api-configuration)
- [Authentication Configuration](#authentication-configuration)
- [Security Configuration](#security-configuration)
- [Performance Configuration](#performance-configuration)
- [Vector Memory Configuration](#vector-memory-configuration)
- [Agent Configuration](#agent-configuration)
- [Observability Configuration](#observability-configuration)
- [UX Configuration](#ux-configuration)
- [Storage Paths](#storage-paths)

---

## Configuration System

Configuration is managed via Pydantic Settings (`app/config/settings.py`). Values are loaded from:

1. **Environment variables** (highest priority)
2. **`.env` file** in the project root
3. **Default values** in the `Settings` class

The settings object is instantiated once at module load as `settings = Settings()` and imported throughout the codebase.

### Configuration File

```python
# app/config/settings.py
class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )
```

---

## LLM Configuration

### Primary Provider (HuggingFace)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `HF_TOKEN` | str \| None | `None` | HuggingFace API token |
| `HF_MODEL` | str | `mistralai/Mistral-7B-Instruct-v0.2` | Model identifier |
| `HF_API_URL` | str \| None | `None` | Custom inference endpoint URL |
| `HF_TEMPERATURE` | float | `0.7` | Sampling temperature (0.0–1.0) |
| `HF_MAX_TOKENS` | int | `1024` | Maximum generation tokens |

### Fallback Provider (OpenAI)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `OPENAI_API_KEY` | str \| None | `None` | OpenAI API key |
| `OPENAI_MODEL` | str | `gpt-4-turbo-preview` | Model identifier |
| `OPENAI_TEMPERATURE` | float | `0.7` | Sampling temperature (0.0–1.0) |

### Global LLM Settings

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `LLM_RETRY_ATTEMPTS` | int | `3` | Number of retry attempts for LLM calls |
| `LLM_TIMEOUT` | int | `60` | Timeout in seconds for LLM API calls |

### Specialized Models (Agent Brain Layer)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MODEL_REASONING` | str | `mistralai/Mistral-7B-Instruct-v0.2` | Model for reasoning tasks |
| `MODEL_CODING` | str | `codellama/CodeLlama-13b-Instruct-hf` | Model for code generation |
| `MODEL_SUMMARIZATION` | str | `sshleifer/distilbart-cnn-12-6` | Model for summarization |

---

## RAG Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `EMBEDDING_MODEL` | str | `all-MiniLM-L6-v2` | SentenceTransformer embedding model |
| `RAG_CHUNK_MAX_WORDS` | int | `220` | Maximum words per chunk |
| `RAG_CHUNK_OVERLAP_SENTENCES` | int | `2` | Overlapping sentences between chunks |
| `RAG_TOP_K` | int | `3` | Number of chunks to retrieve |
| `RAG_ENFORCE_GROUNDING` | bool | `True` | Enforce RAG grounding in responses |
| `RAG_MIN_SCORE` | float | `0.35` | Minimum retrieval score to accept |
| `RAG_REFUSAL_MESSAGE` | str | `"I don't have enough..."` | Message when confidence is too low |

### Reranking

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `RERANKING_ENABLED` | bool | `True` | Enable cross-encoder reranking |
| `RERANKER_MODEL` | str | `cross-encoder/ms-marco-MiniLM-L-6-v2` | Cross-encoder model |
| `RERANKER_TOP_K` | int | `5` | Top results after reranking |
| `RERANKER_CANDIDATES` | int | `20` | Candidates to consider for reranking |

---

## Memory Configuration

### Infrastructure

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `REDIS_URL` | str | `redis://localhost:6379/0` | Redis connection URL |
| `POSTGRES_URL` | str | `postgresql://user:password@localhost:5432/chatbot` | PostgreSQL connection URL |

**Validation:** `REDIS_URL` must start with `redis://` or `rediss://`. `POSTGRES_URL` must start with `postgresql://` or `postgres://`.

### Conversation Memory

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MEMORY_MAX_TURNS` | int | `20` | Maximum conversation turns to retain |
| `MEMORY_SUMMARY_EVERY_N_TURNS` | int | `12` | Summarize after N turns |
| `MEMORY_CONTEXT_MAX_CHARS` | int | `8000` | Max context characters for memory |
| `MEMORY_CONTEXT_TAIL_TURNS` | int | `6` | Recent turns to include in context |

---

## API Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `API_HOST` | str | `0.0.0.0` | Bind address |
| `API_PORT` | int | `8000` | Bind port |
| `API_RATE_LIMIT` | int | `100` | General rate limit (requests/minute) |
| `API_AGENT_RATE_LIMIT` | int | `10` | Agent endpoint rate limit (requests/minute) |
| `DEBUG` | bool | `False` | Enable debug mode (auto-reload, verbose errors) |

### Request Protection

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `MAX_REQUEST_BODY_BYTES` | int | `1048576` (1 MB) | Maximum request body size |
| `REQUEST_TIMEOUT_SECONDS` | float | `60.0` | Per-request timeout |

---

## Authentication Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `JWT_SECRET_KEY` | str | `""` | HMAC signing key for JWT tokens |
| `JWT_ALGORITHM` | str | `HS256` | JWT signing algorithm |
| `JWT_EXPIRY_MINS` | int | `60` | Token expiration in minutes |
| `API_KEY` | str \| None | `None` | Expected value for `X-API-Key` header (env var, not in Settings) |

**Note:** If `JWT_SECRET_KEY` is empty, JWT auth is disabled (dev mode). If `API_KEY` env var is not set, API key auth is bypassed.

---

## Security Configuration

### Content Safety

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `CONTENT_SAFETY_ENABLED` | bool | `True` | Enable content safety filtering |
| `CONTENT_SAFETY_INJECTION_THRESHOLD` | float | `0.6` | Rejection threshold for injection score |
| `CONTENT_SAFETY_QUARANTINE_THRESHOLD` | float | `0.35` | Quarantine threshold for borderline content |
| `CONTENT_SAFETY_QUALITY_FLOOR` | float | `0.3` | Minimum content quality score |

---

## Performance Configuration

### Semantic Cache

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `SEMANTIC_CACHE_ENABLED` | bool | `True` | Enable response caching |
| `SEMANTIC_CACHE_THRESHOLD` | float | `0.92` | Cosine similarity threshold for cache hits |
| `SEMANTIC_CACHE_TTL` | int | `86400` (24h) | Cache entry time-to-live (seconds) |
| `SEMANTIC_CACHE_MAX_ENTRIES` | int | `1000` | Maximum cache entries (LRU eviction) |

### Token Management

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `UX_TOKEN_LIMIT` | int | `4096` | Total token budget for prompt assembly |

---

## Vector Memory Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `VECTOR_MEMORY_ENABLED` | bool | `True` | Enable vector memory storage |
| `VECTOR_MEMORY_DIM` | int | `384` | Embedding dimensionality |
| `VECTOR_MEMORY_TOP_K` | int | `5` | Results per vector search |
| `VECTOR_BACKEND` | str | `faiss` | Backend: `faiss` (dev) or `qdrant` (prod) |

### Qdrant (Production)

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `QDRANT_URL` | str | `http://localhost:6333` | Qdrant server URL |
| `QDRANT_API_KEY` | str \| None | `None` | Qdrant API key |

---

## Agent Configuration

Agent limits are defined as constants in the respective modules:

| Parameter | Default | Location | Description |
|-----------|---------|----------|-------------|
| `MAX_AGENT_ITERATIONS` | 10 | `orchestrator/watchdog.py` | Max DAG iterations per execution |
| `MAX_TOOL_CALLS` | 20 | `orchestrator/watchdog.py` | Max tool calls per execution |
| `MAX_RUNTIME_SECONDS` | 30 | `orchestrator/watchdog.py` | Max wall-clock time per execution |
| `MAX_SWARM_AGENTS` | 5 | `swarm/execution.py` | Max parallel agents in swarm mode |
| `MAX_PARALLEL_TASKS` | 10 | `swarm/execution.py` | Max parallel tasks per batch |
| `max_steps` | 8 | `agents/agent_state.py` | Max steps in agent state |
| `max_tool_calls` | 10 | `agents/agent_state.py` | Max tool calls in agent state |

---

## Observability Configuration

| Variable | Type | Default | Source |
|----------|------|---------|--------|
| `LOG_LEVEL` | str | `INFO` | Env var |
| `LOG_FORMAT` | str | `json` | Env var (`json` or `text`) |
| `OTLP_ENDPOINT` | str \| None | `None` | Env var (e.g., `http://otel-collector:4317`) |
| `TRACING_ENABLED` | str | `true` | Env var (`true` or `false`) |

---

## UX Configuration

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `ASSISTANT_NAME` | str | `Nimbus` | Assistant display name |
| `DEFAULT_SYSTEM_PROMPT` | str | (see below) | Default system prompt |

```
You are Nimbus, a privacy-first local AI assistant.
Core guarantees:
- Offline-first and privacy-preserving (local by default; web search is optional).
- Grounded: do not hallucinate facts. If unsure, say so.
- Citation-aware: when sources are provided, prefer them and be explicit about uncertainty.
- Follow the user's instructions unless they conflict with these guarantees.
```

### Web Search

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `WEB_MAX_RESULTS` | int | `5` | Maximum web search results |
| `WEB_PAGE_MAX_CHARS` | int | `1800` | Max characters per page extraction |
| `WEB_CONTEXT_MAX_CHARS` | int | `4000` | Max total web context characters |

---

## Storage Paths

| Variable | Type | Default | Description |
|----------|------|---------|-------------|
| `BASE_DIR` | Path | Project root | Base project directory |
| `DATA_DIR` | Path | `<root>/data` | Data directory |
| `UPLOADED_DOCS_DIR` | Path | `<root>/data/raw_docs` | Raw document uploads |
| `PROCESSED_DOCS_DIR` | Path | `<root>/data/processed_docs` | Processed document outputs |
| `VECTOR_INDEX_DIR` | Path | `<root>/data/vector_index` | FAISS index storage |

Directories are auto-created via `settings.ensure_dirs()`.

---

## Example `.env` File

```bash
# === LLM Providers ===
HF_TOKEN=hf_xxxxxxxxxxxx
HF_MODEL=mistralai/Mistral-7B-Instruct-v0.2
HF_TEMPERATURE=0.7
HF_MAX_TOKENS=1024
OPENAI_API_KEY=sk-xxxxxxxxxxxx

# === Infrastructure ===
REDIS_URL=redis://localhost:6379/0
POSTGRES_URL=postgresql://user:password@localhost:5432/chatbot

# === Vector Backend ===
VECTOR_BACKEND=faiss
# VECTOR_BACKEND=qdrant
# QDRANT_URL=http://localhost:6333

# === API ===
API_HOST=0.0.0.0
API_PORT=8000
API_RATE_LIMIT=100
API_AGENT_RATE_LIMIT=10
DEBUG=false

# === Auth ===
API_KEY=your-api-key-here
JWT_SECRET_KEY=your-32-char-secret-key-here
JWT_EXPIRY_MINS=60

# === Content Safety ===
CONTENT_SAFETY_ENABLED=true
CONTENT_SAFETY_INJECTION_THRESHOLD=0.6

# === Performance ===
SEMANTIC_CACHE_ENABLED=true
SEMANTIC_CACHE_THRESHOLD=0.92
RERANKING_ENABLED=true

# === Observability ===
LOG_LEVEL=INFO
LOG_FORMAT=json
TRACING_ENABLED=true
# OTLP_ENDPOINT=http://otel-collector:4317
```

---

## Configuration Rationale

Why specific defaults were chosen:

| Setting | Default | Why This Value |
|---------|---------|----------------|
| `SEMANTIC_CACHE_THRESHOLD` | `0.92` | Cosine similarity 0.92 means questions must be nearly identical in meaning. Lower (e.g., 0.85) increases cache hits but risks returning wrong answers for similar-but-different questions. Higher (e.g., 0.95) is safer but fewer hits. 0.92 was validated against a test set of 500 query pairs. |
| `RAG_MIN_SCORE` | `0.35` | Retrieval score below 0.35 means the best-matching document chunk is barely related. Returning unrelated context causes hallucination. This threshold triggers the refusal guard instead. |
| `CONTENT_SAFETY_INJECTION_THRESHOLD` | `0.6` | Injection detection score of 0.6 balances false positives (blocking legitimate queries) vs. false negatives (letting attacks through). Tested against OWASP prompt injection payloads. |
| `HF_TEMPERATURE` | `0.7` | Controls LLM randomness. 0.0 = deterministic; 1.0 = maximum creativity. 0.7 gives useful variety while staying coherent for Q&A tasks. |
| `MEMORY_MAX_TURNS` | `20` | Each conversation turn consumes ~200 tokens of context window. 20 turns × 200 = 4000 tokens, leaving room for RAG context and system prompt. |
| `API_RATE_LIMIT` | `100/min` | Prevents a single API key from consuming all resources. 100/min allows burst usage while protecting against accidental loops. |
| `LLM_TIMEOUT` | `60s` | HuggingFace cold starts take 10-30s. 60s accommodates cold starts while preventing infinite hangs. |
| `MAX_AGENT_ITERATIONS` | `10` | Prevents infinite agent loops. Most queries resolve in 1-3 iterations. 10 covers complex multi-step reasoning. |

---

## Units Reference

All time values use **seconds** unless otherwise noted:

| Setting | Unit | Example |
|---------|------|---------|
| `LLM_TIMEOUT` | seconds | `60` = 1 minute |
| `SEMANTIC_CACHE_TTL` | seconds | `86400` = 24 hours |
| `REQUEST_TIMEOUT_SECONDS` | seconds | `60.0` = 1 minute |
| `JWT_EXPIRY_MINS` | **minutes** (exception) | `60` = 1 hour |
| `MAX_RUNTIME_SECONDS` | seconds | `30` = 30 seconds |
| `MAX_REQUEST_BODY_BYTES` | bytes | `1048576` = 1 MB |
| `WEB_PAGE_MAX_CHARS` | characters | `1800` |

---

## Common Misconfiguration Consequences

| If You Misconfigure | What Happens | How to Diagnose |
|---------------------|-------------|-----------------|
| `REDIS_URL` missing or wrong | **Entire system fails** — no caching, no rate limiting, no Celery | Error on startup: `ConnectionRefusedError` to Redis |
| `HF_TOKEN` invalid | LLM calls fail → 503 responses | Log: `401 Unauthorized from HuggingFace API` |
| `JWT_SECRET_KEY` empty in production | **Auth is disabled** — all endpoints are open | Check startup log for `JWT auth disabled` warning |
| `SEMANTIC_CACHE_THRESHOLD` too low (< 0.80) | Wrong cached answers returned for different questions | Monitor `semantic_cache_hits_total` — high hit rate with user complaints |
| `SEMANTIC_CACHE_THRESHOLD` too high (> 0.97) | Cache barely helps — almost every query is a miss | Monitor `semantic_cache_misses_total` — near 100% miss rate |
| `RAG_MIN_SCORE` too low (< 0.20) | Irrelevant documents injected into context → hallucinations | Check `rag_retrieval_results_count` — results returned but answers are wrong |
| `RAG_MIN_SCORE` too high (> 0.70) | Valid documents rejected → refusal messages for good queries | Frequent `"I don't have enough information"` responses |
| `MAX_AGENT_ITERATIONS` = 1 | Complex queries fail — agent can't do multi-step reasoning | Log: `AgentWatchdog: budget exceeded (iterations)` |
| `API_RATE_LIMIT` too low (< 10) | Legitimate users get rate-limited during normal use | `429 Too Many Requests` errors in client |
| `VECTOR_MEMORY_DIM` ≠ embedding model dimensions | Vector search crashes or returns garbage | Error: `ValueError: dimension mismatch` |

---

## Environment-Specific Configurations

### Development

```bash
DEBUG=true
LOG_FORMAT=text                          # Human-readable logs
SEMANTIC_CACHE_ENABLED=false             # Disable cache for testing
JWT_SECRET_KEY=                          # Auth disabled
VECTOR_BACKEND=faiss                     # No Qdrant needed
HF_TEMPERATURE=0.0                       # Deterministic output for testing
```

### Staging

```bash
DEBUG=false
LOG_FORMAT=json
SEMANTIC_CACHE_ENABLED=true
JWT_SECRET_KEY=staging-secret-32-chars-min
VECTOR_BACKEND=qdrant
CONTENT_SAFETY_ENABLED=true
```

### Production

```bash
DEBUG=false
LOG_FORMAT=json
SEMANTIC_CACHE_ENABLED=true
SEMANTIC_CACHE_TTL=43200                 # 12h (shorter for freshness)
JWT_SECRET_KEY=<strong-random-32-char>   # Generate: python -c "import secrets; print(secrets.token_urlsafe(32))"
VECTOR_BACKEND=qdrant
QDRANT_URL=http://qdrant:6333
POSTGRES_URL=postgresql://prod_user:strong_pass@db:5432/chatbot
CONTENT_SAFETY_ENABLED=true
API_RATE_LIMIT=50                         # More conservative in prod
LOG_LEVEL=WARNING                         # Less verbose
OTLP_ENDPOINT=http://otel-collector:4317  # Enable tracing
```
