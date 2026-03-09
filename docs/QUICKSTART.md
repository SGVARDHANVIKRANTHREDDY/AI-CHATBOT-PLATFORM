# Quick Start

> Get Nimbus running in under 5 minutes — then verify it works and send your first query.

---

## What You're Setting Up

```
┌──────────────────────────────────────────────────────────┐
│                  Your Local Nimbus Stack                  │
│                                                          │
│   Browser / curl ──▶ FastAPI (port 8000)                 │
│                         │                                │
│                    ┌────┴────┐                            │
│                    │  Redis  │  (port 6379)               │
│                    │ session │  Required for caching,     │
│                    │ cache   │  rate limits, and Celery    │
│                    └─────────┘                            │
│                                                          │
│   Optional:  PostgreSQL (port 5432) — for persistent     │
│              conversation history                         │
└──────────────────────────────────────────────────────────┘
```

---

## Option 1: Local (Minimal)

```bash
# 1. Clone
git clone https://github.com/your-org/chatbot.git && cd chatbot

# 2. Setup Python
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Start Redis
docker run -d --name redis -p 6379:6379 redis:7-alpine

# 4. Configure
echo "HF_TOKEN=hf_your_token_here" > .env
echo "REDIS_URL=redis://localhost:6379/0" >> .env

# 5. Run
python run.py
```

## Option 2: Docker (Full Stack)

```bash
git clone https://github.com/your-org/chatbot.git && cd chatbot
export HF_TOKEN=hf_your_token_here        # Linux/macOS
# $env:HF_TOKEN="hf_your_token_here"       # Windows PowerShell
cd infra/docker && docker compose up -d
```

This starts **all** services: API (port 8000), Celery worker, Redis (6379), PostgreSQL (5432), and Prometheus (9090).

---

## Verify Everything Is Running

Run these checks in order. All must pass before sending queries.

### 1. Redis is healthy

```bash
# Must print PONG
docker exec redis redis-cli ping
```

If this fails: Redis isn't running. Re-run `docker run -d --name redis -p 6379:6379 redis:7-alpine`.

### 2. API server is up

```bash
curl http://localhost:8000/healthz
# Expected: {"status":"ok","version":"3.0"}
```

If this fails: Check the terminal running `python run.py` for the error. Common causes:
- Port 8000 already in use → set `API_PORT=8001` in `.env`
- Redis not reachable → verify `REDIS_URL` in `.env`

### 3. Swagger docs load

Open [http://localhost:8000/docs](http://localhost:8000/docs) in your browser. You should see the interactive API documentation.

---

## Send Your First Query

### Basic question (no RAG)

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "What is Python?", "use_rag": false}'
```

Expected response:
```json
{
  "ok": true,
  "result": {
    "answer": "Python is a high-level, interpreted programming language...",
    "confidence": "high",
    "used_rag": false,
    "citations": []
  },
  "response": "Python is a high-level..."
}
```

### Question with RAG (requires documents)

```bash
# First, place a .txt or .pdf file in data/raw_docs/
# Then ask about its contents:
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -d '{"question": "Summarize the document I uploaded", "use_rag": true}'
```

### Streaming response

```bash
curl -X POST http://localhost:8000/api/v1/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"question": "Explain async/await in Python"}' \
  --no-buffer
```

You'll see tokens arriving one at a time as Server-Sent Events:
```
data: {"content": "Async"}
data: {"content": "/await"}
data: {"content": " is"}
...
data: [DONE]
```

### Python example

```python
import httpx, asyncio

async def ask():
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://localhost:8000/api/v1/chat",
            json={"question": "What is a circuit breaker pattern?"},
        )
        print(resp.json()["result"]["answer"])

asyncio.run(ask())
```

---

## Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `ConnectionRefusedError` on startup | Redis not running | Start Redis: `docker run -d --name redis -p 6379:6379 redis:7-alpine` |
| `401 Unauthorized` on `/api/v1/chat` | Auth is enabled but no token provided | Set `JWT_SECRET_KEY=""` in `.env` to disable auth for dev, or pass `X-API-Key` header |
| `429 Too Many Requests` | Rate limit exceeded (100 req/min) | Wait 60 seconds, or increase `API_RATE_LIMIT` in `.env` |
| `503 Service Unavailable` / LLM timeout | HuggingFace API unreachable or model cold-starting | Wait 30s for model warm-up; verify `HF_TOKEN` is valid; check [status.huggingface.co](https://status.huggingface.co) |
| `FAISS index not found` warning | No documents ingested yet | Place files in `data/raw_docs/` and restart, or set `use_rag=false` |
| Port 8000 already in use | Another process on port 8000 | Set `API_PORT=8001` in `.env`, or kill the other process |
| `ModuleNotFoundError` | Dependencies not installed | Re-run `pip install -r requirements.txt` |
| Celery worker won't start | Redis broker not reachable | Verify Redis is running and `REDIS_URL` is correct |

---

## What's Next?

| Goal | Read |
|------|------|
| Understand system architecture | [SYSTEM_OVERVIEW.md](SYSTEM_OVERVIEW.md) |
| Configure all settings | [CONFIGURATION.md](CONFIGURATION.md) |
| Add documents for RAG | [RAG_PIPELINE.md](RAG_PIPELINE.md) |
| Add a new agent or tool | [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md) |
| Deploy to production | [DEPLOYMENT.md](DEPLOYMENT.md) |
| Set up monitoring | [OBSERVABILITY.md](OBSERVABILITY.md) |
| Run tests | [TESTING.md](TESTING.md) |
| Understand the full API | [API_REFERENCE.md](API_REFERENCE.md) |

## What's Running

| Service | URL | Purpose |
|---------|-----|---------|
| API | http://localhost:8000 | Chat endpoint |
| Docs | http://localhost:8000/docs | Swagger UI |
| Health | http://localhost:8000/healthz | Health check |
| Metrics | http://localhost:8000/metrics | Prometheus metrics |
| Redis | localhost:6379 | Cache and queue |
| PostgreSQL | localhost:5432 | Conversation store (Docker only) |
| Prometheus | http://localhost:9090 | Metrics dashboard (Docker only) |

## Next Steps

- [CONFIGURATION.md](CONFIGURATION.md) — Customize settings
- [API_REFERENCE.md](API_REFERENCE.md) — Full API documentation
- [DEVELOPMENT_GUIDE.md](DEVELOPMENT_GUIDE.md) — Developer onboarding
- [DEPLOYMENT.md](DEPLOYMENT.md) — Production deployment
