# Deployment

> Complete deployment guide: Docker containerization, service orchestration, CI/CD pipeline, observability stack, and production operations.

---

## Table of Contents

- [Overview](#overview)
- [Docker Build](#docker-build)
- [Docker Compose — Application](#docker-compose--application)
- [Docker Compose — Observability](#docker-compose--observability)
- [CI/CD Pipeline](#cicd-pipeline)
- [Environment Variables](#environment-variables)
- [Health Checks](#health-checks)
- [Production Deployment](#production-deployment)
- [Rollback Procedures](#rollback-procedures)

---

## Overview

The platform ships as a **multi-stage Docker image** orchestrated via Docker Compose. CI/CD runs through a **9-stage GitHub Actions pipeline** that gates on lint, type-check, security, dependency audit, unit tests, integration tests, E2E tests, Docker build, and staging deployment.

### Service Topology

```
┌──────────────────────────────────────────────────────────────────┐
│                      docker-compose.yml                          │
│                                                                  │
│  ┌─────────┐  ┌────────┐  ┌───────┐  ┌──────────┐  ┌─────────┐ │
│  │   api   │  │ worker │  │ redis │  │ postgres │  │promethe-│ │
│  │  :8000  │  │ celery │  │ :6379 │  │  :5432   │  │us :9090 │ │
│  └─────────┘  └────────┘  └───────┘  └──────────┘  └─────────┘ │
└──────────────────────────────────────────────────────────────────┘
```

---

## Docker Build

**File:** `infra/docker/Dockerfile`

### Multi-Stage Architecture

```dockerfile
# ── Build stage ──────────────────────────────────────────────────
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ────────────────────────────────────────────────
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PYTHONPATH=/app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd -r appuser && useradd -r -g appuser -d /app appuser

WORKDIR /app

COPY --from=builder /install /usr/local

COPY app/ ./app/
COPY workers/ ./workers/
COPY run.py pytest.ini ./

RUN chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Design Decisions

| Decision | Rationale |
|----------|-----------|
| Multi-stage build | Build tools excluded from runtime → smaller image |
| `python:3.12-slim` | Minimal base with glibc support |
| Non-root user (`appuser`) | Container security best practice |
| `--no-cache-dir` | No pip cache in layer → smaller image |
| `--prefix=/install` | Isolates installed packages for COPY |
| Built-in `HEALTHCHECK` | Docker/orchestrator liveness probe |

### Build Commands

```bash
# Standard build
docker build -f infra/docker/Dockerfile -t ai-chatbot:latest .

# With build arguments
docker build -f infra/docker/Dockerfile \
  --build-arg PYTHON_VERSION=3.12 \
  -t ai-chatbot:v1.0.0 .

# Multi-platform build
docker buildx build --platform linux/amd64,linux/arm64 \
  -f infra/docker/Dockerfile \
  -t ai-chatbot:latest --push .
```

---

## Docker Compose — Application

**File:** `infra/docker/docker-compose.yml`

```yaml
version: '3.8'

services:
  api:
    build:
      context: ../../
      dockerfile: infra/docker/Dockerfile
    ports:
      - "8000:8000"
    environment:
      - REDIS_URL=redis://redis:6379/0
      - POSTGRES_URL=postgresql://user:password@postgres:5432/chatbot
      - HF_TOKEN=${HF_TOKEN}
      - OPENAI_API_KEY=${OPENAI_API_KEY}
    depends_on:
      - redis
      - postgres
    networks:
      - assistant-network

  worker:
    build:
      context: ../../
      dockerfile: infra/docker/Dockerfile
    command: ["celery", "-A", "workers.celery_app", "worker", "--loglevel=info"]
    environment:
      - REDIS_URL=redis://redis:6379/0
      - POSTGRES_URL=postgresql://user:password@postgres:5432/chatbot
    depends_on:
      - redis
      - postgres
    networks:
      - assistant-network

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"
    networks:
      - assistant-network

  postgres:
    image: postgres:15-alpine
    environment:
      - POSTGRES_USER=user
      - POSTGRES_PASSWORD=password
      - POSTGRES_DB=chatbot
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    networks:
      - assistant-network

  prometheus:
    image: prom/prometheus:latest
    volumes:
      - ../monitoring/prometheus.yml:/etc/prometheus/prometheus.yml
    ports:
      - "9090:9090"
    networks:
      - assistant-network

networks:
  assistant-network:
    driver: bridge

volumes:
  postgres_data:
```

### Service Details

| Service | Image | Port | Role |
|---------|-------|------|------|
| `api` | Custom (Dockerfile) | 8000 | FastAPI application server |
| `worker` | Custom (Dockerfile) | — | Celery background worker |
| `redis` | redis:7-alpine | 6379 | Cache, session store, Celery broker |
| `postgres` | postgres:15-alpine | 5432 | Conversation history, persistent storage |
| `prometheus` | prom/prometheus | 9090 | Metrics collection |

### Launch Commands

```bash
# Start all services
cd infra/docker
docker compose up -d

# Rebuild after code changes
docker compose up -d --build

# View logs
docker compose logs -f api
docker compose logs -f worker

# Stop all
docker compose down

# Stop and remove volumes
docker compose down -v
```

---

## Docker Compose — Observability

**File:** `infra/monitoring/docker-compose.observability.yml`

A separate compose file for the full observability stack:

```yaml
version: "3.9"

services:
  prometheus:
    image: prom/prometheus:v2.51.0
    ports: ["9090:9090"]
    volumes:
      - ./prometheus.yml:/etc/prometheus/prometheus.yml:ro
      - prometheus_data:/prometheus
    command:
      - "--config.file=/etc/prometheus/prometheus.yml"
      - "--storage.tsdb.retention.time=30d"

  grafana:
    image: grafana/grafana:10.4.1
    ports: ["3000:3000"]
    environment:
      GF_SECURITY_ADMIN_PASSWORD: admin
    volumes:
      - ./grafana/provisioning:/etc/grafana/provisioning:ro
      - ./grafana/dashboards:/var/lib/grafana/dashboards:ro

  jaeger:
    image: jaegertracing/all-in-one:1.55
    ports:
      - "16686:16686"   # Jaeger UI
      - "4317:4317"     # OTLP gRPC
      - "4318:4318"     # OTLP HTTP
    environment:
      COLLECTOR_OTLP_ENABLED: "true"

  elasticsearch:
    image: docker.elastic.co/elasticsearch/elasticsearch:8.13.0
    ports: ["9200:9200"]
    environment:
      discovery.type: single-node
      xpack.security.enabled: "false"
      ES_JAVA_OPTS: "-Xms512m -Xmx512m"

  kibana:
    image: docker.elastic.co/kibana/kibana:8.13.0
    ports: ["5601:5601"]
    depends_on: [elasticsearch]

  filebeat:
    image: docker.elastic.co/beats/filebeat:8.13.0
    volumes:
      - ./filebeat.yml:/usr/share/filebeat/filebeat.yml:ro
      - /var/log/ai-platform:/var/log/ai-platform:ro
    depends_on: [elasticsearch]
```

### Launch

```bash
cd infra/monitoring
docker compose -f docker-compose.observability.yml up -d
```

### Access Points

| Service | URL | Purpose |
|---------|-----|---------|
| Prometheus | http://localhost:9090 | Metrics queries |
| Grafana | http://localhost:3000 | Dashboards (admin/admin) |
| Jaeger | http://localhost:16686 | Distributed tracing |
| Kibana | http://localhost:5601 | Log exploration |
| Elasticsearch | http://localhost:9200 | Log storage API |

---

## CI/CD Pipeline

**File:** `.github/workflows/ci-cd.yml`

### Pipeline Stages

```
push to main/develop OR PR to main
        │
        ▼
┌───────────────┐  ┌────────────────┐  ┌────────────────┐  ┌──────────────┐
│  1. Lint      │  │ 2. Type Check  │  │ 3. Security    │  │ 4. Dep Scan  │
│  (ruff)       │  │ (mypy)         │  │ (bandit)       │  │ (pip-audit)  │
└───────┬───────┘  └───────┬────────┘  └────────┬───────┘  └──────┬───────┘
        │                  │                    │                  │
        ▼                  ▼                    │                  │
┌───────────────────────────┐                   │                  │
│     5. Unit Tests         │                   │                  │
│  (pytest tests/unit/)     │                   │                  │
└───────────┬───────────────┘                   │                  │
            ▼                                   │                  │
┌───────────────────────────┐                   │                  │
│   6. Integration Tests    │                   │                  │
│  (pytest tests/integration)│                  │                  │
│  [Redis service container] │                  │                  │
└───────────┬───────────────┘                   │                  │
            ▼                                   │                  │
┌───────────────────────────┐                   │                  │
│     7. E2E Tests          │                   │                  │
│  (pytest tests/e2e/ +     │                   │                  │
│   tests/chaos/)           │                   │                  │
└───────────┬───────────────┘                   │                  │
            │                                   │                  │
            ▼                                   ▼                  ▼
┌────────────────────────────────────────────────────────────────────┐
│                   8. Build Docker Image                            │
│   (docker/build-push-action → ghcr.io, only on push)              │
└────────────────────────────────┬───────────────────────────────────┘
                                 ▼
┌────────────────────────────────────────────────────────────────────┐
│              9. Deploy to Staging                                  │
│   (only on push to main, requires staging environment approval)    │
└────────────────────────────────────────────────────────────────────┘
```

### Stage Details

| Stage | Tool | Trigger | Depends On |
|-------|------|---------|------------|
| 1. Lint | ruff check + ruff format | Always | — |
| 2. Type Check | mypy | Always | — |
| 3. Security Scan | bandit | Always | — |
| 4. Dependency Scan | pip-audit | Always | — |
| 5. Unit Tests | pytest + coverage | Always | Lint, Type Check |
| 6. Integration Tests | pytest + Redis service | Always | Unit Tests |
| 7. E2E Tests | pytest + Redis service | Always | Integration Tests |
| 8. Docker Build | docker/build-push-action | Push only | E2E, Security, Dep Scan |
| 9. Deploy Staging | Custom deployment | Push to main | Docker Build |

### Pipeline Features

- **Concurrency control:** Superseded runs on the same branch/PR are cancelled
- **Pip caching:** `actions/cache@v4` using `requirements.txt` hash key
- **Artifact uploads:** Test results (JUnit XML), coverage reports, bandit/pip-audit reports
- **Container registry:** GitHub Container Registry (`ghcr.io`)
- **Image tagging:** SHA-based, branch-based, and semver tags via `docker/metadata-action`
- **GitHub Actions cache for Docker layers:** `cache-from/cache-to: type=gha`
- **Staging environment protection:** Requires `staging` environment approval

---

## Environment Variables

### Required (Production)

| Variable | Description | Example |
|----------|-------------|---------|
| `HF_TOKEN` | HuggingFace API token | `hf_...` |
| `OPENAI_API_KEY` | OpenAI API key (fallback provider) | `sk-...` |
| `REDIS_URL` | Redis connection URL | `redis://redis:6379/0` |
| `POSTGRES_URL` | PostgreSQL connection URL | `postgresql://user:pass@host/db` |

### Optional

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `ENVIRONMENT` | `development` | `development` / `staging` / `production` |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | — | OpenTelemetry collector endpoint |
| `RATE_LIMIT_PER_MINUTE` | `20` | API rate limit per IP |

---

## Health Checks

### Application Health Endpoint

```
GET /health → 200 {"status": "healthy"}
```

### Docker HEALTHCHECK

```dockerfile
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1
```

### Staging Smoke Test

After deployment, the CI pipeline runs:

```bash
curl -sf "$STAGING_URL/health" || exit 1
```

---

## Production Deployment

### Prerequisites

1. Container registry access (ghcr.io)
2. `HF_TOKEN` and `OPENAI_API_KEY` as GitHub Actions secrets
3. `staging` environment configured with `STAGING_URL` variable
4. Target infrastructure (Kubernetes, ECS, Azure Container Apps, or bare Docker)

### Deployment Steps

```bash
# 1. Pull the latest image
docker pull ghcr.io/<org>/chatbot:<sha>

# 2. Deploy with compose
cd infra/docker
HF_TOKEN=... OPENAI_API_KEY=... docker compose up -d

# 3. Start observability stack
cd ../monitoring
docker compose -f docker-compose.observability.yml up -d

# 4. Verify health
curl -f http://localhost:8000/health
```

### Kubernetes Example

```bash
# Update deployment image
kubectl set image deployment/chatbot-api \
  api=ghcr.io/<org>/chatbot:<sha> \
  --namespace=production

# Watch rollout
kubectl rollout status deployment/chatbot-api -n production
```

---

## Rollback Procedures

### Docker Compose

```bash
# Revert to previous image tag
docker compose up -d --no-build \
  -e IMAGE_TAG=<previous-sha>
```

### Kubernetes

```bash
# Undo last rollout
kubectl rollout undo deployment/chatbot-api -n production

# Rollback to specific revision
kubectl rollout undo deployment/chatbot-api --to-revision=3 -n production
```

### GitHub Actions

Re-run the CI/CD workflow for the commit you want to deploy, or manually trigger a workflow dispatch targeting the desired SHA.
