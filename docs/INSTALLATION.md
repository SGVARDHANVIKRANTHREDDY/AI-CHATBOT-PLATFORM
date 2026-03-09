# Installation

> Step-by-step installation instructions for all environments.

---

## Table of Contents

- [Prerequisites](#prerequisites)
- [Local Development Setup](#local-development-setup)
- [Docker Setup](#docker-setup)
- [Production Setup](#production-setup)
- [Service Dependencies](#service-dependencies)
- [Verifying Installation](#verifying-installation)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Required

| Software | Version | Purpose |
|----------|---------|---------|
| Python | 3.12+ | Runtime |
| pip | Latest | Package management |
| Redis | 7+ | Session cache, rate limiting, Celery broker, semantic cache |
| Git | Any | Source control |

### Optional (Production)

| Software | Version | Purpose |
|----------|---------|---------|
| PostgreSQL | 15+ | Long-term conversation storage |
| Qdrant | 1.7+ | Production vector database |
| Docker | 24+ | Containerized deployment |
| Docker Compose | 2.0+ | Multi-service orchestration |

### API Keys

| Key | Required | Provider |
|-----|----------|----------|
| `HF_TOKEN` | Yes (if using HuggingFace) | [HuggingFace](https://huggingface.co/settings/tokens) |
| `OPENAI_API_KEY` | No (fallback provider) | [OpenAI](https://platform.openai.com/api-keys) |
| `API_KEY` | Optional | Self-generated for API key authentication |
| `JWT_SECRET_KEY` | Optional (production required) | Self-generated HMAC key |

---

## Local Development Setup

### 1. Clone the Repository

```bash
git clone https://github.com/your-org/chatbot.git
cd chatbot
```

### 2. Create a Virtual Environment

```bash
python -m venv .venv

# Linux/macOS
source .venv/bin/activate

# Windows
.venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Install Development Dependencies

```bash
pip install pytest pytest-asyncio pytest-cov ruff mypy bandit
pip install types-redis types-requests
```

### 5. Configure Environment Variables

Create a `.env` file in the project root:

```bash
# LLM Configuration (at least one required)
HF_TOKEN=hf_your_huggingface_token
HF_MODEL=mistralai/Mistral-7B-Instruct-v0.2

# Optional fallback
OPENAI_API_KEY=sk-your_openai_key
OPENAI_MODEL=gpt-4-turbo-preview

# Infrastructure
REDIS_URL=redis://localhost:6379/0
POSTGRES_URL=postgresql://user:password@localhost:5432/chatbot

# API Settings
API_HOST=0.0.0.0
API_PORT=8000
DEBUG=true

# Optional Authentication
API_KEY=your-api-key
JWT_SECRET_KEY=your-secret-key

# Vector Backend ("faiss" for dev, "qdrant" for prod)
VECTOR_BACKEND=faiss
```

### 6. Start Redis

```bash
# Using Docker
docker run -d --name redis -p 6379:6379 redis:7-alpine

# Or install natively (macOS)
brew install redis && redis-server

# Or install natively (Ubuntu)
sudo apt install redis-server && sudo systemctl start redis
```

### 7. Create Data Directories

```bash
mkdir -p data/raw_docs data/processed_docs data/vector_index
```

### 8. Start the Development Server

```bash
python run.py
```

The server starts at `http://localhost:8000` with auto-reload enabled in debug mode.

### 9. Verify

```bash
curl http://localhost:8000/healthz
# {"status": "ok", "version": "3.0"}
```

---

## Docker Setup

### Quick Start

```bash
cd infra/docker

# Set required environment variables
export HF_TOKEN=hf_your_token

# Start all services
docker compose up -d
```

This starts:
- **API server** on port 8000
- **Celery worker** for background tasks
- **Redis** on port 6379
- **PostgreSQL** on port 5432
- **Prometheus** on port 9090

### Custom Build

```bash
# Build just the application image
docker build -t nimbus:latest -f infra/docker/Dockerfile .

# Run with custom environment
docker run -d \
  --name nimbus-api \
  -p 8000:8000 \
  -e HF_TOKEN=hf_your_token \
  -e REDIS_URL=redis://host.docker.internal:6379/0 \
  nimbus:latest
```

### Docker Compose Services

| Service | Image | Port | Description |
|---------|-------|------|-------------|
| `api` | Custom (Dockerfile) | 8000 | FastAPI application |
| `worker` | Custom (Dockerfile) | — | Celery worker |
| `redis` | redis:7-alpine | 6379 | Cache and message broker |
| `postgres` | postgres:15-alpine | 5432 | Persistent storage |
| `prometheus` | prom/prometheus | 9090 | Metrics collection |

---

## Production Setup

### 1. Environment Variables

Set all required environment variables. In production, **never use defaults** for:

```bash
# REQUIRED in production
JWT_SECRET_KEY=<strong-random-key-min-32-chars>
API_KEY=<strong-api-key>
HF_TOKEN=<your-huggingface-token>

# Database (use strong credentials)
REDIS_URL=rediss://user:password@redis-host:6380/0
POSTGRES_URL=postgresql://user:strongpass@pg-host:5432/chatbot

# Vector backend
VECTOR_BACKEND=qdrant
QDRANT_URL=http://qdrant-host:6333
QDRANT_API_KEY=<qdrant-api-key>

# Observability
OTLP_ENDPOINT=http://otel-collector:4317
TRACING_ENABLED=true
LOG_FORMAT=json
LOG_LEVEL=INFO

# Production settings
DEBUG=false
API_HOST=0.0.0.0
API_PORT=8000
```

### 2. Run with Gunicorn (Multi-Worker)

```bash
pip install gunicorn

gunicorn app.api.main:app \
  --worker-class uvicorn.workers.UvicornWorker \
  --workers 4 \
  --bind 0.0.0.0:8000 \
  --timeout 120 \
  --access-logfile -
```

### 3. Start Celery Workers

```bash
celery -A workers.celery_app worker \
  --loglevel=info \
  --concurrency=4

# Start beat scheduler for periodic tasks
celery -A workers.celery_app beat --loglevel=info
```

### 4. Run Behind a Reverse Proxy

Nginx example:

```nginx
upstream nimbus {
    server 127.0.0.1:8000;
}

server {
    listen 443 ssl;
    server_name api.example.com;

    ssl_certificate /etc/ssl/certs/cert.pem;
    ssl_certificate_key /etc/ssl/private/key.pem;

    location / {
        proxy_pass http://nimbus;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Request-ID $request_id;
        proxy_read_timeout 120s;
    }
}
```

---

## Service Dependencies

### Redis Setup

Redis is required for session cache, rate limiting, semantic cache, and Celery task brokering.

```bash
# Docker
docker run -d --name redis \
  -p 6379:6379 \
  --restart unless-stopped \
  redis:7-alpine \
  redis-server --maxmemory 256mb --maxmemory-policy allkeys-lru

# Verify
redis-cli ping
# PONG
```

### PostgreSQL Setup (Optional)

PostgreSQL provides durable conversation storage.

```bash
# Docker
docker run -d --name postgres \
  -p 5432:5432 \
  -e POSTGRES_USER=user \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=chatbot \
  --restart unless-stopped \
  postgres:15-alpine

# Verify
psql -h localhost -U user -d chatbot -c "SELECT 1;"
```

The `interactions` table is auto-created on first use by `ConversationStore.initialize()`.

### Qdrant Setup (Production)

```bash
# Docker
docker run -d --name qdrant \
  -p 6333:6333 \
  --restart unless-stopped \
  qdrant/qdrant:latest

# Verify
curl http://localhost:6333/healthz
```

Collections are auto-created on first insert by `QdrantVectorStore`.

---

## Verifying Installation

### Health Check

```bash
curl http://localhost:8000/healthz
# {"status": "ok", "version": "3.0"}
```

### Metrics Endpoint

```bash
curl http://localhost:8000/metrics
# Prometheus-formatted metrics output
```

### Basic Chat Request

```bash
curl -X POST http://localhost:8000/api/v1/chat \
  -H "Content-Type: application/json" \
  -H "X-API-Key: your-api-key" \
  -d '{"question": "Hello, what can you do?"}'
```

### Run Tests

```bash
# Unit tests
python -m pytest tests/unit/ -v

# Integration tests (requires Redis)
python -m pytest tests/integration/ -v -m integration

# All tests
python -m pytest tests/ -v
```

---

## Troubleshooting

### Common Issues

| Issue | Cause | Solution |
|-------|-------|----------|
| `ConnectionRefusedError: redis` | Redis not running | Start Redis: `docker run -d -p 6379:6379 redis:7-alpine` |
| `HuggingFace 401` | Invalid or missing HF_TOKEN | Set valid token in `.env` |
| `ModuleNotFoundError` | Dependencies not installed | Run `pip install -r requirements.txt` |
| `FAISS import error` | faiss-cpu not installed | Run `pip install faiss-cpu` |
| `JWT rejected` | Wrong or expired JWT token | Generate new token; check `JWT_SECRET_KEY` matches |
| `Port 8000 in use` | Another service on port | Change `API_PORT` in `.env` or stop conflicting service |
| `Rate limit exceeded` | Too many requests | Wait 60s or adjust `API_RATE_LIMIT` |
| `Request timed out` | Slow LLM response | Increase `REQUEST_TIMEOUT_SECONDS` or check LLM availability |

### Checking Logs

```bash
# Development (plain text)
LOG_FORMAT=text python run.py

# Production (JSON)
LOG_FORMAT=json python run.py | python -m json.tool
```

### Resetting State

```bash
# Clear Redis cache
redis-cli FLUSHALL

# Clear vector index
rm -rf data/vector_index/*

# Clear processed docs
rm -rf data/processed_docs/*
```
