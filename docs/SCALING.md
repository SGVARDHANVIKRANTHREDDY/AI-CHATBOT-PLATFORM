# Scaling

> Strategies for horizontal and vertical scaling of the AI Chatbot Platform — API servers, workers, vector databases, caches, and databases.

---

## Table of Contents

- [Architecture for Scale](#architecture-for-scale)
- [API Server Scaling](#api-server-scaling)
- [Worker Scaling](#worker-scaling)
- [Vector Database Scaling](#vector-database-scaling)
- [Redis Scaling](#redis-scaling)
- [PostgreSQL Scaling](#postgresql-scaling)
- [Load Guard System](#load-guard-system)
- [Monitoring for Scale](#monitoring-for-scale)
- [Capacity Planning](#capacity-planning)

---

## Architecture for Scale

```
                    ┌──────────────┐
                    │ Load Balancer│
                    └──────┬───────┘
         ┌─────────────────┼─────────────────┐
         ▼                 ▼                 ▼
  ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
  │  API Pod 1   │ │  API Pod 2   │ │  API Pod N   │
  │  (uvicorn)   │ │  (uvicorn)   │ │  (uvicorn)   │
  └──────┬───────┘ └──────┬───────┘ └──────┬───────┘
         │                │                │
    ┌────┴────────────────┴────────────────┴───┐
    │              Redis Cluster               │
    │  (sessions, cache, Celery broker)        │
    └────┬─────────────────────────────────────┘
         │
    ┌────┴────────────────────────────────────────┐
    │              Celery Workers (1..M)           │
    │  ingestion │ indexing │ knowledge │ maint    │
    └────┬────────────────────────────────────────┘
         │
    ┌────┴────────────────────────────────────────┐
    │  Vector DB         │  PostgreSQL             │
    │  (FAISS/Qdrant)    │  (conversations)        │
    └─────────────────────────────────────────────┘
```

---

## API Server Scaling

### Horizontal Scaling

The FastAPI server is stateless — all session state lives in Redis and PostgreSQL. Deploy multiple instances behind a load balancer.

#### Docker Compose

```yaml
services:
  api:
    deploy:
      replicas: 4
      resources:
        limits:
          cpus: '2.0'
          memory: 4G
```

#### Kubernetes

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: chatbot-api
spec:
  replicas: 4
  strategy:
    type: RollingUpdate
    rollingUpdate:
      maxSurge: 1
      maxUnavailable: 0
  template:
    spec:
      containers:
        - name: api
          image: ghcr.io/org/chatbot:latest
          resources:
            requests:
              cpu: "500m"
              memory: "1Gi"
            limits:
              cpu: "2000m"
              memory: "4Gi"
          readinessProbe:
            httpGet:
              path: /health
              port: 8000
            initialDelaySeconds: 5
            periodSeconds: 10
```

#### Horizontal Pod Autoscaler

```yaml
apiVersion: autoscaling/v2
kind: HorizontalPodAutoscaler
metadata:
  name: chatbot-api-hpa
spec:
  scaleTargetRef:
    apiVersion: apps/v1
    kind: Deployment
    name: chatbot-api
  minReplicas: 2
  maxReplicas: 20
  metrics:
    - type: Resource
      resource:
        name: cpu
        target:
          type: Utilization
          averageUtilization: 70
    - type: Pods
      pods:
        metric:
          name: http_requests_active
        target:
          type: AverageValue
          averageValue: 50
```

### Vertical Scaling

Each API instance uses:
- **CPU:** Embedding computation (SentenceTransformers), token budgeting
- **Memory:** FAISS index (in-memory), model caches, active sessions

| Load Level | Recommended Resources |
|------------|----------------------|
| Low (< 10 req/s) | 1 CPU, 2 GB RAM |
| Medium (10–50 req/s) | 2 CPU, 4 GB RAM |
| High (50–200 req/s) | 4 CPU, 8 GB RAM, GPU optional |

---

## Worker Scaling

### Celery Worker Pools

```bash
# Scale by adding worker instances
celery -A workers.celery_app worker --concurrency=8 --loglevel=info -Q default

# Dedicated queues for different workloads
celery -A workers.celery_app worker -Q ingestion --concurrency=4
celery -A workers.celery_app worker -Q indexing --concurrency=2
celery -A workers.celery_app worker -Q knowledge --concurrency=2
celery -A workers.celery_app worker -Q maintenance --concurrency=1
```

### Queue Isolation

| Queue | Worker Type | Purpose | Concurrency |
|-------|------------|---------|-------------|
| `ingestion` | I/O-bound | Document parsing, chunking | 4–8 |
| `indexing` | CPU-bound | Embedding generation, FAISS | 2–4 |
| `knowledge` | Network-bound | Web crawling, knowledge graphs | 2–4 |
| `maintenance` | Mixed | Vector maintenance, cleanup | 1–2 |

### Kubernetes Worker Scaling

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: chatbot-workers
spec:
  replicas: 3
  template:
    spec:
      containers:
        - name: worker
          command: ["celery", "-A", "workers.celery_app", "worker",
                    "--concurrency=4", "--loglevel=info"]
          resources:
            requests:
              cpu: "1000m"
              memory: "2Gi"
```

---

## Vector Database Scaling

### FAISS (Development / Small Scale)

FAISS runs in-process with the API server. Each API replica loads its own copy of the index.

**Limitations:**
- Index is per-process — no shared writes across replicas
- Memory usage grows linearly with document count
- Suitable for < 1M vectors

**Mitigation:**
- Use a single writer process (maintenance worker) that builds the index
- API replicas load the index from persistent storage at startup
- Rebuild on schedule via `VectorMaintenanceManager`

### Qdrant (Production / Large Scale)

Qdrant runs as a separate cluster with built-in horizontal scaling:

```yaml
# Qdrant cluster configuration
storage:
  storage_path: /qdrant/storage
  optimizers:
    default_segment_number: 4
  wal:
    wal_capacity_mb: 64

cluster:
  enabled: true
  p2p:
    port: 6335
```

**Scaling strategy:**
- **Sharding:** Qdrant automatically distributes collections across nodes
- **Replication:** Configure replication factor ≥ 2 for high availability
- **Async gRPC:** The `QdrantVectorStore` uses async gRPC for maximum throughput

```python
# Production Qdrant settings
QDRANT_HOST = "qdrant-cluster.internal"
QDRANT_PORT = 6334      # gRPC port
QDRANT_GRPC = True
VECTOR_STORE = "qdrant"  # Switch from FAISS
```

---

## Redis Scaling

### Session & Cache Partitioning

Use separate Redis databases or instances for different concerns:

| Database | Purpose | Eviction Policy |
|----------|---------|-----------------|
| `db0` | Celery broker | noeviction |
| `db1` | Session cache | allkeys-lru |
| `db2` | Semantic cache | allkeys-lru |
| `db3` | Rate limiting | volatile-ttl |

### Redis Cluster (High Scale)

```bash
# 6-node cluster (3 masters + 3 replicas)
redis-cli --cluster create \
  redis-1:6379 redis-2:6379 redis-3:6379 \
  redis-4:6379 redis-5:6379 redis-6:6379 \
  --cluster-replicas 1
```

### Redis Sentinel (High Availability)

```yaml
# docker-compose sentinel overlay
services:
  redis-sentinel:
    image: redis:7-alpine
    command: redis-sentinel /etc/sentinel.conf
    volumes:
      - ./sentinel.conf:/etc/sentinel.conf
```

---

## PostgreSQL Scaling

### Connection Pooling

Use PgBouncer in front of PostgreSQL:

```yaml
services:
  pgbouncer:
    image: edoburu/pgbouncer:latest
    environment:
      DATABASE_URL: postgresql://user:password@postgres:5432/chatbot
      MAX_CLIENT_CONN: 200
      DEFAULT_POOL_SIZE: 25
      POOL_MODE: transaction
```

### Read Replicas

For read-heavy workloads (conversation history retrieval):

```
                ┌──────────────┐
                │   Primary    │ ← Writes
                │  PostgreSQL  │
                └──────┬───────┘
           ┌───────────┼───────────┐
           ▼           ▼           ▼
     ┌──────────┐ ┌──────────┐ ┌──────────┐
     │ Replica 1│ │ Replica 2│ │ Replica 3│  ← Reads
     └──────────┘ └──────────┘ └──────────┘
```

---

## Load Guard System

The platform ships with three built-in load guards (see `app/reliability/load_guard.py`):

### RequestQueueLimiter

Bounds total concurrent API requests using an asyncio `Semaphore`:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_concurrent` | 100 | Maximum simultaneous requests |
| `queue_timeout` | 10.0 | Seconds to wait for a slot |

### AgentExecutionLimiter

Bounds total concurrent agent loops across all sessions:

| Parameter | Default | Description |
|-----------|---------|-------------|
| `max_concurrent_agents` | 20 | Maximum simultaneous agent executions |

### SwarmThrottle

Dynamically reduces swarm parallelism when load increases:

```python
# Under high load, swarm parallelism reduces automatically
if utilization > 0.8:
    max_parallel_agents = max(1, default_parallel // 2)
```

---

## Monitoring for Scale

### Key Metrics to Watch

| Metric | Alert Threshold | Action |
|--------|----------------|--------|
| `http_requests_active` | > 80% of limit | Scale API pods |
| `agent_executions_active` | > 80% of limit | Scale workers |
| `llm_request_duration_seconds` | p99 > 30s | Check provider health |
| `redis_connected_clients` | > 80% max | Scale Redis |
| `vector_search_duration_seconds` | p95 > 2s | Optimize indexes |
| `celery_queue_length` | > 100 tasks | Scale workers |

### Grafana Alerts

```yaml
# Example alert rule
- alert: HighAPIUtilization
  expr: http_requests_active / http_requests_max > 0.8
  for: 5m
  labels:
    severity: warning
  annotations:
    summary: "API utilization above 80%"
```

---

## Capacity Planning

### Sizing Formula

```
API Pods = ceil(peak_rps / rps_per_pod)
Workers  = ceil(peak_tasks_per_hour / (tasks_per_worker_per_hour * 0.7))
Redis    = session_count * avg_session_size + cache_entries * avg_entry_size
Postgres = conversations * avg_conversation_size * retention_days
Vector   = document_count * embedding_dimension * 4 bytes
```

### Reference Benchmarks

| Configuration | Sustained Throughput | P99 Latency |
|---------------|---------------------|-------------|
| 1 API + 1 Worker + FAISS | 5 req/s | 3.2s |
| 4 API + 2 Workers + Qdrant | 40 req/s | 2.1s |
| 8 API + 4 Workers + Qdrant cluster | 120 req/s | 1.8s |
