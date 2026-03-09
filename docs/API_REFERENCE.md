# API Reference

> Complete documentation for all HTTP endpoints, request/response schemas, authentication, and rate limiting.

---

## Table of Contents

- [Base URL](#base-url)
- [Authentication](#authentication)
- [Rate Limiting](#rate-limiting)
- [Endpoints](#endpoints)
  - [GET /](#get-)
  - [GET /healthz](#get-healthz)
  - [GET /metrics](#get-metrics)
  - [POST /api/v1/chat](#post-apiv1chat)
  - [POST /api/v1/chat/stream](#post-apiv1chatstream)
- [Request and Response Schemas](#request-and-response-schemas)
- [Error Responses](#error-responses)
- [Middleware Stack](#middleware-stack)

---

## Base URL

```
http://localhost:8000
```

Production: Use your deployment URL with HTTPS (e.g., `https://api.example.com`).

---

## Authentication

The API supports two authentication mechanisms, checked in order:

### 1. JWT Bearer Token

```
Authorization: Bearer <jwt_token>
```

JWT tokens use HS256 signing with the `JWT_SECRET_KEY` from configuration. Tokens contain:

| Claim | Description |
|-------|-------------|
| `sub` | User ID (used for rate limiting and audit) |
| `iat` | Issued-at timestamp |
| `exp` | Expiration timestamp |

**Generating a token** (for testing):

```python
from app.api.middleware.jwt_auth import create_jwt

token = create_jwt(
    payload={"sub": "user-123", "role": "admin"},
    secret="your-jwt-secret-key",
    expiry_minutes=60
)
```

### 2. API Key

```
X-API-Key: your-api-key
```

Set via the `API_KEY` environment variable. If not set, API key auth is bypassed.

### Public Endpoints (No Auth Required)

| Endpoint | Description |
|----------|-------------|
| `GET /` | Root status |
| `GET /healthz` | Health check |
| `GET /metrics` | Prometheus metrics |
| `GET /docs` | Swagger UI |
| `GET /openapi.json` | OpenAPI schema |
| `GET /redoc` | ReDoc UI |

### Auth Behavior

- If both `JWT_SECRET_KEY` and `API_KEY` are unset → all endpoints are open (development mode)
- If `JWT_SECRET_KEY` is set → Bearer token required on protected endpoints
- If only `API_KEY` is set → `X-API-Key` header required on protected endpoints
- JWT takes precedence: if JWT is provided, API key is not checked

---

## Rate Limiting

Rate limiting uses a Redis-backed token bucket algorithm with per-identity tracking.

### Identity Resolution Order

1. Authenticated user ID (from JWT `sub` claim)
2. API key (last 8 characters of `X-API-Key` header)
3. Client IP address (fallback)

### Rate Limit Tiers

| Tier | Limit | Applies To |
|------|-------|-----------|
| General | 100 requests/minute | All protected endpoints |
| Agent | 10 requests/minute | `/api/v1/chat`, `/api/v1/chat/stream` |

### Rate Limit Headers

When rate limited, the API returns:

```http
HTTP/1.1 429 Too Many Requests
Retry-After: 60
Content-Type: application/json

{
  "detail": "Rate limit exceeded (general: 100/min). Retry later."
}
```

---

## Endpoints

### GET /

Root endpoint. Returns API status.

**Request:**
```http
GET / HTTP/1.1
```

**Response:**
```json
{
  "message": "Nimbus API is running!"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `message` | string | Status message with assistant name |

---

### GET /healthz

Health check endpoint for monitoring and load balancers.

**Request:**
```http
GET /healthz HTTP/1.1
```

**Response:**
```json
{
  "status": "ok",
  "version": "3.0"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | Always `"ok"` if server is running |
| `version` | string | API version |

---

### GET /metrics

Prometheus-formatted metrics endpoint.

**Request:**
```http
GET /metrics HTTP/1.1
```

**Response:**
```
# HELP request_latency_seconds API request latency
# TYPE request_latency_seconds histogram
request_latency_seconds_bucket{endpoint="POST /api/v1/chat",le="0.1"} 42
...
```

Returns Prometheus text format with 80+ metrics. See [OBSERVABILITY.md](OBSERVABILITY.md) for the complete metrics catalog.

---

### POST /api/v1/chat

Submit a question and receive a structured response.

**Request:**

```http
POST /api/v1/chat HTTP/1.1
Content-Type: application/json
Authorization: Bearer <jwt_token>
X-API-Key: <api_key>

{
  "question": "What is retrieval-augmented generation?",
  "session_id": "user-123-session",
  "use_rag": true,
  "use_web": false,
  "use_memory": true,
  "rag_top_k": 5,
  "system_prompt": null
}
```

**Request Schema: `ChatRequest`**

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `question` | string | Yes | — | User question (min 1 character) |
| `session_id` | string | No | `"default"` | Conversation session identifier |
| `use_rag` | boolean | No | `true` | Enable RAG retrieval |
| `use_web` | boolean | No | `false` | Enable web search |
| `use_memory` | boolean | No | `true` | Enable conversation memory |
| `rag_top_k` | integer \| null | No | `null` (uses config default) | Number of RAG results (1–50) |
| `system_prompt` | string \| null | No | `null` (uses default) | Override system prompt |

**Response:**

```json
{
  "ok": true,
  "result": {
    "answer": "Retrieval-Augmented Generation (RAG) is a technique that combines...",
    "confidence": "high",
    "used_rag": true,
    "rag_score": 0.87,
    "used_web": false,
    "citations": [
      {
        "source": "data/raw_docs/rag_overview.txt",
        "chunk_id": "chunk_3",
        "score": 0.87
      }
    ]
  },
  "response": "Retrieval-Augmented Generation (RAG) is a technique that combines..."
}
```

**Response Schema: `ChatResponse`**

| Field | Type | Description |
|-------|------|-------------|
| `ok` | boolean | Request success |
| `result` | AnswerContract | Structured answer data |
| `response` | string | Plain text answer (convenience field) |

**AnswerContract Schema:**

| Field | Type | Description |
|-------|------|-------------|
| `answer` | string | The generated answer |
| `confidence` | `"high"` \| `"medium"` \| `"low"` | Confidence level |
| `used_rag` | boolean | Whether RAG was used |
| `rag_score` | float \| null | Top RAG retrieval score |
| `used_web` | boolean | Whether web search was used |
| `citations` | Citation[] | Source citations |

**Citation Schema:**

| Field | Type | Description |
|-------|------|-------------|
| `source` | string | Source file path or URL |
| `chunk_id` | string | Chunk identifier |
| `score` | float | Retrieval score |

**Example with cURL:**

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{
    "question": "How does FAISS indexing work?",
    "use_rag": true,
    "use_web": false,
    "session_id": "dev-session"
  }'
```

**Example with Python httpx:**

```python
import httpx

async with httpx.AsyncClient() as client:
    resp = await client.post(
        "http://localhost:8000/api/v1/chat",
        json={
            "question": "Explain circuit breaker pattern",
            "use_rag": True,
            "session_id": "user-456"
        },
        headers={"X-API-Key": "your-api-key"}
    )
    data = resp.json()
    print(data["result"]["answer"])
```

---

### POST /api/v1/chat/stream

Submit a question and receive a Server-Sent Events (SSE) stream.

**Request:**

Same as `POST /api/v1/chat`.

```http
POST /api/v1/chat/stream HTTP/1.1
Content-Type: application/json
Authorization: Bearer <jwt_token>

{
  "question": "Write a Python decorator for retries"
}
```

**Response:**

```
Content-Type: text/event-stream

data: {"content": "Here"}

data: {"content": " is"}

data: {"content": " a"}

data: {"content": " retry"}

data: {"content": " decorator"}

data: {"content": ":\n\n```python\n..."}

data: [DONE]
```

Each SSE event contains a JSON object with a `content` field. The stream terminates with `data: [DONE]`.

**SSE Event Format:**

```
data: {"content": "<token>"}     ← Normal token
data: [DONE]                      ← Stream complete
```

**Error during stream:**

If an error occurs mid-stream, the server sends an error event before closing:

```
data: {"content": "Partial answer so far..."}
data: {"error": "LLM provider timeout after 30s"}
data: [DONE]
```

Your client should check each event for the `error` key.

**Example with cURL:**

```bash
curl -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"question": "Write a binary search in Python"}' \
  --no-buffer
```

**Example with Python httpx:**

```python
import httpx
import json

async with httpx.AsyncClient() as client:
    async with client.stream(
        "POST",
        "http://localhost:8000/api/v1/chat/stream",
        json={"question": "Write a binary search in Python"},
        headers={"X-API-Key": "your-api-key"}
    ) as resp:
        async for line in resp.aiter_lines():
            if line.startswith("data: ") and line != "data: [DONE]":
                chunk = json.loads(line[6:])
                if "error" in chunk:
                    print(f"\nStream error: {chunk['error']}")
                    break
                print(chunk["content"], end="", flush=True)
        print()  # Newline after stream
```

**Example with JavaScript (EventSource pattern):**

```javascript
async function streamChat(question) {
  const response = await fetch('/api/v1/chat/stream', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'X-API-Key': 'your-api-key'
    },
    body: JSON.stringify({ question })
  });

  if (!response.ok) {
    throw new Error(`HTTP ${response.status}: ${await response.text()}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let fullResponse = '';

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Process complete SSE lines
    const lines = buffer.split('\n');
    buffer = lines.pop();  // Keep incomplete line in buffer

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const data = line.slice(6);
        if (data === '[DONE]') return fullResponse;

        try {
          const parsed = JSON.parse(data);
          if (parsed.error) throw new Error(parsed.error);
          fullResponse += parsed.content;
          // Update UI with each token:
          document.getElementById('output').textContent = fullResponse;
        } catch (e) {
          console.error('Parse error:', e);
        }
      }
    }
  }

  return fullResponse;
}
```

**Example with Python `requests` (synchronous):**

```python
import requests
import json

response = requests.post(
    "http://localhost:8000/api/v1/chat/stream",
    json={"question": "What is Python?"},
    headers={"X-API-Key": "your-api-key"},
    stream=True,
)

for line in response.iter_lines(decode_unicode=True):
    if line.startswith("data: ") and line != "data: [DONE]":
        chunk = json.loads(line[6:])
        print(chunk["content"], end="", flush=True)
print()
```

---

## Request and Response Schemas

### Full Schema Definitions

```python
# app/api/schemas/chat.py

class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1)
    session_id: str = Field(default="default")
    use_rag: bool = True
    use_web: bool = False
    use_memory: bool = True
    rag_top_k: Optional[int] = Field(default=None, ge=1, le=50)
    system_prompt: Optional[str] = None

class ChatResponse(BaseModel):
    ok: bool = True
    result: AnswerContract
    response: str

class ErrorResponse(BaseModel):
    ok: bool = False
    error: str
```

```python
# app/shared/types.py

class Citation(BaseModel):
    source: str
    chunk_id: str
    score: float

class AnswerContract(BaseModel):
    answer: str
    confidence: Literal["high", "medium", "low"]
    used_rag: bool
    rag_score: Optional[float]
    used_web: bool
    citations: List[Citation] = []
```

---

## Error Responses

### HTTP 400 — Bad Request

```json
{
  "detail": "Question cannot be empty"
}
```

### HTTP 401 — Unauthorized

```json
{
  "detail": "Missing Authorization header"
}
```

or

```json
{
  "detail": "JWT expired"
}
```

### HTTP 403 — Forbidden

```json
{
  "detail": "Could not validate API Key"
}
```

### HTTP 408 — Request Timeout

```json
{
  "ok": false,
  "error": "Request timed out",
  "detail": "Processing exceeded 60.0s limit"
}
```

### HTTP 413 — Payload Too Large

```json
{
  "ok": false,
  "error": "Request body too large",
  "detail": "Request body exceeds 1048576 bytes"
}
```

### HTTP 429 — Too Many Requests

```json
{
  "detail": "Rate limit exceeded (general: 100/min). Retry later."
}
```

### HTTP 500 — Internal Server Error

```json
{
  "ok": false,
  "error": "Internal Server Error",
  "detail": "An unexpected error occurred."
}
```

In debug mode (`DEBUG=true`), the `detail` field contains the actual exception message.

---

## Middleware Stack

Middleware is processed bottom-to-top (first added = last executed):

```
Request →
  SecurityHeadersMiddleware      (adds HSTS, X-Frame-Options, etc.)
  CorrelationMiddleware          (assigns X-Request-ID, starts trace)
  CORSMiddleware                 (handles preflight, allows origins)
  TimeoutMiddleware              (cancels after 60s)
  RequestSizeLimitMiddleware     (rejects > 1MB)
  JWTAuthMiddleware              (validates Bearer token)
  AbuseDetectionMiddleware       (logs abuse signals)
  rate_limit_middleware           (token bucket per identity)
→ Route Handler

Response ←
  SecurityHeadersMiddleware      (adds security headers)
  CorrelationMiddleware          (adds X-Request-ID to response)
← Client
```

### Response Headers

Every response includes:

| Header | Value | Purpose |
|--------|-------|---------|
| `X-Content-Type-Options` | `nosniff` | Prevent MIME sniffing |
| `X-Frame-Options` | `DENY` | Prevent framing |
| `X-XSS-Protection` | `1; mode=block` | XSS protection |
| `Strict-Transport-Security` | `max-age=31536000; includeSubDomains` | HTTPS enforcement |
| `X-Request-ID` | UUID | Request correlation ID |

---

## Rate Limit Recovery Patterns

### Detecting Rate Limits

Watch for `429` status code and `Retry-After` header:

```python
import httpx
import asyncio

async def chat_with_retry(question: str, max_retries: int = 3):
    async with httpx.AsyncClient() as client:
        for attempt in range(max_retries):
            resp = await client.post(
                "http://localhost:8000/api/v1/chat",
                json={"question": question},
                headers={"X-API-Key": "your-key"},
            )
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"Rate limited. Retrying in {retry_after}s...")
                await asyncio.sleep(retry_after)
                continue
            resp.raise_for_status()
            return resp.json()
    raise Exception("Max retries exceeded")
```

### Rate Limit Tiers Explained

```
General rate limit (100/min) applies to ALL endpoints.
Agent rate limit  (10/min)  applies ONLY to /api/v1/chat and /api/v1/chat/stream.

Both limits reset independently. You can hit the agent limit while
still having general requests remaining.

Identity is resolved in order:
  1. JWT "sub" claim  (authenticated user)
  2. API key          (last 8 chars)
  3. Client IP        (fallback)
```

---

## Expected Latency

| Scenario | Typical Latency | Notes |
|----------|----------------|-------|
| Cache hit | < 50ms | Semantic cache answers immediately |
| Simple query (no RAG) | 2-5s | Single LLM call |
| RAG query | 3-8s | Vector search + rerank + LLM |
| Complex multi-agent query | 5-15s | Planner → DAG → multiple LLM calls |
| Streaming (first token) | 1-3s | Time to first token in stream |
| Cold start (model loading) | 10-30s | First request after HuggingFace model eviction |

---

## Session Management

Sessions persist conversation context across requests. Use `session_id` to maintain a conversation:

```bash
# First message in a conversation
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Python?", "session_id": "user-alice-001"}'

# Follow-up in the same conversation (has context of previous messages)
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "What about its type system?", "session_id": "user-alice-001"}'

# Different session (no context from alice's conversation)
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "What about its type system?", "session_id": "user-bob-001"}'
```

- Sessions are stored in Redis (TTL: 1 hour of inactivity)
- If PostgreSQL is configured, full conversation history is also persisted
- The `session_id` defaults to `"default"` if omitted
