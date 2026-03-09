# Observability

> Complete documentation of metrics, tracing, logging, monitoring infrastructure, and alerting.

---

## Table of Contents

- [Overview](#overview)
- [Prometheus Metrics](#prometheus-metrics)
  - [Request Metrics](#request-metrics)
  - [LLM Metrics](#llm-metrics)
  - [RAG Metrics](#rag-metrics)
  - [Memory Metrics](#memory-metrics)
  - [Agent Metrics](#agent-metrics)
  - [Cache Metrics](#cache-metrics)
  - [Reliability Metrics](#reliability-metrics)
  - [Security Metrics](#security-metrics)
- [OpenTelemetry Tracing](#opentelemetry-tracing)
- [Structured Logging](#structured-logging)
- [Monitoring Infrastructure](#monitoring-infrastructure)
- [Configuration](#configuration)
- [Dashboards](#dashboards)
- [Observability Stack Deployment](#observability-stack-deployment)
- [Telemetry Summary](#telemetry-summary)

---

## Overview

The observability stack provides three pillars:

| Pillar | Technology | Purpose |
|--------|-----------|---------|
| **Metrics** | Prometheus Client Python | Quantitative system health (80+ metrics) |
| **Tracing** | OpenTelemetry SDK | Distributed request tracing |
| **Logging** | Python logging + ELK | Event-level debugging and audit |

```
┌──────────────────────────────────────────────┐
│                Application                    │
│                                              │
│  ┌──────────┐  ┌──────────┐  ┌────────────┐ │
│  │Prometheus│  │OpenTel   │  │ Structured │ │
│  │ Client   │  │ SDK      │  │ JSON Logs  │ │
│  └────┬─────┘  └────┬─────┘  └─────┬──────┘ │
└───────┼──────────────┼──────────────┼────────┘
        │              │              │
   ┌────▼─────┐  ┌─────▼────┐  ┌─────▼──────┐
   │Prometheus│  │OTLP      │  │Filebeat    │
   │ Server   │  │Collector │  │            │
   └────┬─────┘  └──────────┘  └─────┬──────┘
        │                             │
   ┌────▼─────┐               ┌──────▼──────┐
   │ Grafana  │               │Elasticsearch│
   └──────────┘               └──────┬──────┘
                                     │
                              ┌──────▼──────┐
                              │   Kibana    │
                              └─────────────┘
```

---

## Prometheus Metrics

**Location:** `app/shared/monitoring.py`

### Request Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `http_requests_total` | Counter | `method`, `endpoint`, `status` | Total HTTP requests |
| `http_request_duration_seconds` | Histogram | `method`, `endpoint` | Request latency |
| `http_request_size_bytes` | Histogram | `method`, `endpoint` | Request body size |
| `http_response_size_bytes` | Histogram | `method`, `endpoint` | Response body size |
| `http_active_connections` | Gauge | — | Currently active connections |
| `rate_limit_rejections_total` | Counter | `endpoint` | Rate limit rejections |

### LLM Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `llm_request_duration_seconds` | Histogram | `provider`, `model` | LLM API call latency |
| `llm_requests_total` | Counter | `provider`, `model`, `status` | Total LLM requests |
| `llm_tokens_used_total` | Counter | `provider`, `type` | Token consumption (prompt/completion) |
| `llm_token_budget_utilization` | Histogram | — | Token budget usage ratio |
| `llm_fallback_total` | Counter | `from_provider`, `to_provider` | Provider fallback events |
| `llm_circuit_breaker_state` | Gauge | `provider` | Circuit breaker state (0=closed, 1=open) |
| `llm_cost_estimate_usd` | Counter | `provider`, `model` | Estimated LLM cost |

### RAG Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `rag_retrieval_duration_seconds` | Histogram | — | Retrieval latency |
| `rag_retrieval_results_count` | Histogram | — | Results per query |
| `rag_reranking_duration_seconds` | Histogram | — | Reranking latency |
| `rag_index_size` | Gauge | — | Total indexed vectors |
| `rag_ingestion_total` | Counter | `format` | Documents ingested |
| `rag_content_safety_rejections` | Counter | — | Chunks rejected by safety filter |

### Memory Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `memory_save_duration_seconds` | Histogram | `tier` | Memory save latency |
| `memory_retrieve_duration_seconds` | Histogram | `tier` | Memory retrieve latency |
| `memory_session_count` | Gauge | — | Active session count |
| `memory_authority_resolution_source` | Counter | `source` | Winning source in authority resolution |
| `memory_vector_search_duration` | Histogram | — | Vector search latency |

### Agent Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `agent_execution_duration_seconds` | Histogram | `agent_type` | Agent step execution time |
| `agent_steps_total` | Counter | `agent_type`, `status` | Steps completed/failed |
| `agent_tool_calls_total` | Counter | `tool_name`, `status` | Tool invocations |
| `agent_budget_exceeded_total` | Counter | `reason` | Watchdog terminations |
| `agent_concurrent_count` | Gauge | — | Currently executing agents |
| `agent_plan_nodes_count` | Histogram | — | Nodes per planning output |
| `swarm_execution_duration_seconds` | Histogram | — | Swarm wave execution time |
| `swarm_merge_duration_seconds` | Histogram | — | Result merging time |

### Cache Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `semantic_cache_hits_total` | Counter | — | Cache hits |
| `semantic_cache_misses_total` | Counter | — | Cache misses |
| `semantic_cache_hit_rate` | Gauge | — | Hit rate ratio |
| `semantic_cache_lookup_duration_seconds` | Histogram | — | Cache lookup time |
| `semantic_cache_llm_savings_total` | Counter | — | LLM calls saved by cache |
| `semantic_cache_size` | Gauge | — | Current cache entries |

### Reliability Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `circuit_breaker_state` | Gauge | `name` | CB state (0/1/2) |
| `circuit_breaker_transitions_total` | Counter | `name`, `from`, `to` | State transitions |
| `retry_attempts_total` | Counter | `component` | Retry attempts |
| `timeout_exceeded_total` | Counter | `component` | Timeout events |
| `failure_rate_1m` | Gauge | `component` | 1-minute failure rate |
| `response_validation_errors_total` | Counter | `type` | Validation errors |
| `request_queue_size` | Gauge | — | Current queue depth |

### Security Metrics

| Metric | Type | Labels | Description |
|--------|------|--------|-------------|
| `injection_attempts_total` | Counter | `type` | Detected injection attempts |
| `content_safety_score` | Histogram | — | Content safety scores |
| `quarantine_entries_total` | Counter | — | Content quarantined |
| `auth_failures_total` | Counter | `type` | Authentication failures |
| `abuse_detections_total` | Counter | — | Abuse events |
| `rate_limit_hits_total` | Counter | `tier` | Rate limit enforcements |

---

## OpenTelemetry Tracing

**Location:** `app/shared/tracing.py`

### Setup

```python
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

def setup_tracing(service_name: str = "ai-chatbot"):
    provider = TracerProvider(
        resource=Resource.create({"service.name": service_name})
    )

    exporter = OTLPSpanExporter(
        endpoint=settings.OTLP_ENDPOINT,  # e.g., "localhost:4317"
    )

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
```

### Traced Decorator

```python
def traced(span_name: str):
    """Decorator to automatically trace function execution."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            tracer = trace.get_tracer(__name__)
            with tracer.start_as_current_span(span_name) as span:
                span.set_attribute("function", func.__name__)
                try:
                    result = await func(*args, **kwargs)
                    span.set_status(StatusCode.OK)
                    return result
                except Exception as e:
                    span.set_status(StatusCode.ERROR, str(e))
                    span.record_exception(e)
                    raise
        return wrapper
    return decorator
```

### Trace Spans

| Span | Parent | Description |
|------|--------|-------------|
| `http.request` | Root | Incoming HTTP request |
| `orchestrator.generate` | `http.request` | Full orchestration pipeline |
| `security.check` | `orchestrator.generate` | Security validation |
| `cache.lookup` | `orchestrator.generate` | Semantic cache check |
| `rag.retrieve` | `orchestrator.generate` | RAG retrieval |
| `memory.retrieve` | `orchestrator.generate` | Memory context retrieval |
| `agent.plan` | `orchestrator.generate` | Planner agent |
| `agent.execute` | `agent.plan` | Individual agent execution |
| `llm.call` | Various | LLM API call |
| `tool.execute` | `agent.execute` | Tool invocation |
| `critic.evaluate` | `orchestrator.generate` | Critic evaluation |
| `cache.store` | `orchestrator.generate` | Semantic cache write |

---

## Structured Logging

**Location:** `app/config/logging_config.py`

### ELK-Compatible JSON Format

```python
class ELKJsonFormatter(logging.Formatter):
    def format(self, record):
        log_entry = {
            "@timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
            "correlation_id": getattr(record, "correlation_id", None),
            "service": "ai-chatbot",
        }

        # Add exception info if present
        if record.exc_info:
            log_entry["exception"] = self.formatException(record.exc_info)

        # Add extra fields
        for key, value in record.__dict__.items():
            if key not in STANDARD_FIELDS:
                log_entry[key] = value

        return json.dumps(log_entry)
```

### Logging Configuration

```python
LOGGING_CONFIG = {
    "version": 1,
    "formatters": {
        "json": {"()": "app.config.logging_config.ELKJsonFormatter"},
        "console": {"format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s"},
    },
    "handlers": {
        "console": {"class": "logging.StreamHandler", "formatter": "console"},
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": "logs/app.log",
            "maxBytes": 10_485_760,    # 10MB
            "backupCount": 5,
            "formatter": "json",
        },
    },
    "root": {"level": "INFO", "handlers": ["console", "file"]},
}
```

### Event Logging

```python
def emit_observability_event(event_name: str, data: dict):
    """Emit a structured observability event."""
    logger = logging.getLogger("observability")
    logger.info(
        event_name,
        extra={
            "event": event_name,
            "data": data,
            "timestamp": datetime.utcnow().isoformat(),
        }
    )
```

---

## Monitoring Infrastructure

### Prometheus Server

**Location:** `infra/monitoring/prometheus.yml`

```yaml
global:
  scrape_interval: 15s
  evaluation_interval: 15s

scrape_configs:
  - job_name: "ai-chatbot"
    static_configs:
      - targets: ["api:8000"]
    metrics_path: /metrics
```

### Filebeat Log Shipping

**Location:** `infra/monitoring/filebeat.yml`

```yaml
filebeat.inputs:
  - type: log
    enabled: true
    paths:
      - /app/logs/*.log
    json.keys_under_root: true
    json.add_error_key: true

output.elasticsearch:
  hosts: ["elasticsearch:9200"]
  index: "chatbot-logs-%{+yyyy.MM.dd}"
```

### Health Check Endpoint

```python
@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "version": "1.0.0",
        "checks": {
            "redis": await redis_health(),
            "postgres": await pg_health(),
            "llm": await llm_health(),
        }
    }
```

---

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `LOG_LEVEL` | `INFO` | Application log level |
| `LOG_FORMAT` | `json` | Log format (json/console) |
| `OTLP_ENDPOINT` | `localhost:4317` | OpenTelemetry collector endpoint |
| Prometheus scrape | `15s` | Scrape interval |
| Prometheus target | `api:8000` | Metrics endpoint |
| Log rotation size | 10MB | Max log file size |
| Log rotation count | 5 | Number of rotated files |
| Filebeat output | Elasticsearch | Log shipping destination |

---

## Dashboards

### Recommended Grafana Panels

**Overview Dashboard:**
- Request rate (req/s)
- P50/P95/P99 latency
- Error rate (%)
- Active connections

**LLM Dashboard:**
- Provider latency comparison
- Token consumption rate
- Fallback frequency
- Circuit breaker states
- Cost estimate (USD/hour)

**RAG Dashboard:**
- Retrieval latency
- Results per query distribution
- Index size trend
- Content safety rejection rate

**Agent Dashboard:**
- Agent execution time by type
- Tool call frequency
- Watchdog termination rate
- Swarm parallelism

---

## Alerting Guide

### Recommended Alerts

| Alert Name | Metric | Condition | Severity | Action |
|------------|--------|-----------|----------|--------|
| **High Error Rate** | `http_requests_total{status=~"5.."}` / `http_requests_total` | > 5% for 5 min | Critical | Check LLM provider health and circuit breaker states |
| **LLM Provider Down** | `llm_circuit_breaker_state` | = 1 (OPEN) for > 2 min | Critical | Verify provider status; check fallback is working |
| **High Latency** | `http_request_duration_seconds` P95 | > 10s for 5 min | Warning | Check LLM latency, cache hit rate, agent count |
| **Cache Not Helping** | `semantic_cache_hit_rate` | < 10% for 1 hour | Warning | Review cache threshold; check if queries are too diverse |
| **Agent Watchdog Kills** | `agent_budget_exceeded_total` | > 5/hour | Warning | Agents are looping. Review DAG complexity and MAX_AGENT_ITERATIONS |
| **Injection Attempts** | `injection_attempts_total` | > 10/hour | Warning | Possible attack. Review source IPs and consider blocking |
| **Queue Saturation** | `request_queue_size` | > 80 | Critical | System overloaded. Scale horizontally or shed load |
| **Memory Exhaustion** | Process RSS | > 80% of container limit | Critical | Memory leak or too many cached vectors. Restart and investigate |

### PromQL Examples

```promql
# Error rate over 5 minutes
sum(rate(http_requests_total{status=~"5.."}[5m]))
/ sum(rate(http_requests_total[5m]))

# P95 request latency
histogram_quantile(0.95, sum(rate(http_request_duration_seconds_bucket[5m])) by (le))

# LLM cost per hour
sum(rate(llm_cost_estimate_usd[1h]))

# Cache hit rate
semantic_cache_hits_total / (semantic_cache_hits_total + semantic_cache_misses_total)

# Circuit breaker flapping (transitions per hour)
sum(increase(circuit_breaker_transitions_total[1h]))
```

### "I see a problem — which metric do I check?"

| Symptom | Check These Metrics | Likely Cause |
|---------|-------------------|-------------|
| Users report slow responses | `http_request_duration_seconds`, `llm_request_duration_seconds` | LLM provider latency or cold start |
| Users report wrong answers | `semantic_cache_hit_rate`, `rag_retrieval_results_count` | Cache returning stale answers, or RAG not finding relevant docs |
| API returning 429 errors | `rate_limit_hits_total`, `request_queue_size` | Rate limit or system overload |
| API returning 503 errors | `llm_circuit_breaker_state`, `llm_requests_total{status="error"}` | All LLM providers down |
| High LLM costs | `llm_tokens_used_total`, `llm_cost_estimate_usd`, `semantic_cache_hit_rate` | Low cache hit rate or verbose prompts |
| Celery tasks stuck | Worker process memory, Redis queue length | Worker crashed or Redis connection lost |

**Cache Dashboard:**
- Hit rate trend
- LLM savings counter
- Lookup latency
- Cache size

**Security Dashboard:**
- Injection attempt rate
- Auth failure rate
- Rate limit enforcement
- Quarantine entries

### Grafana Dashboard Files

Pre-built dashboards are included in the repository:

| File | Description |
|------|-------------|
| `infra/monitoring/grafana/dashboards/ai-platform-overview.json` | High-level system health: request rates, latency percentiles, error rates, LLM usage, cache hit rates |
| `infra/monitoring/grafana/dashboards/ai-platform-agent-deepdive.json` | Agent execution details: per-agent latency, DAG node counts, reasoning depth, tool call distribution |

### Grafana Provisioning

Dashboards and datasources are auto-provisioned via:

```
infra/monitoring/grafana/provisioning/
├── dashboards/
│   └── dashboards.yml        # Dashboard auto-discovery config
└── datasources/
    └── datasources.yml       # Prometheus datasource auto-config
```

Grafana starts with `GF_DASHBOARDS_DEFAULT_HOME_DASHBOARD_PATH` pointing to the overview dashboard.

---

## Observability Stack Deployment

The complete observability stack is deployed via a dedicated Docker Compose file:

**File:** `infra/monitoring/docker-compose.observability.yml`

### Stack Components

```
┌──────────────────────────────────────────────────────────────────┐
│              Observability Stack (docker-compose)                │
│                                                                  │
│  ┌────────────┐  ┌─────────┐  ┌───────────────┐  ┌───────────┐ │
│  │ Prometheus │  │ Grafana │  │ Elasticsearch │  │  Kibana   │ │
│  │   :9090    │  │  :3000  │  │    :9200      │  │  :5601    │ │
│  └────────────┘  └─────────┘  └───────────────┘  └───────────┘ │
│  ┌────────────┐  ┌─────────┐                                    │
│  │   Jaeger   │  │Filebeat │                                    │
│  │   :16686   │  │ (agent) │                                    │
│  │ OTLP:4317  │  └─────────┘                                    │
│  └────────────┘                                                  │
└──────────────────────────────────────────────────────────────────┘
```

| Service | Image | Port | Purpose |
|---------|-------|------|---------|
| Prometheus | `prom/prometheus:v2.51.0` | 9090 | Metrics collection with 30d retention |
| Grafana | `grafana/grafana:10.4.1` | 3000 | Dashboard UI with auto-provisioned panels |
| Jaeger | `jaegertracing/all-in-one:1.55` | 16686 (UI), 4317 (OTLP gRPC) | Distributed tracing backend |
| Elasticsearch | `elasticsearch:8.13.0` | 9200 | Log storage and search |
| Kibana | `kibana:8.13.0` | 5601 | Log visualization UI |
| Filebeat | `filebeat:8.13.0` | — | Ships JSON logs from `/var/log/ai-platform/` to Elasticsearch |

### Launch Commands

```bash
# Start the observability stack
cd infra/monitoring
docker compose -f docker-compose.observability.yml up -d

# Access points:
#   Prometheus:    http://localhost:9090
#   Grafana:       http://localhost:3000 (admin/admin)
#   Jaeger UI:     http://localhost:16686
#   Kibana:        http://localhost:5601
#   Elasticsearch: http://localhost:9200
```

---

## Telemetry Summary

The platform emits telemetry across three channels:

| Channel | Technology | Transport | Destination | Data |
|---------|-----------|-----------|-------------|------|
| **Metrics** | Prometheus client | HTTP scrape (`:8000/metrics`) | Prometheus → Grafana | Counters, histograms, gauges |
| **Traces** | OpenTelemetry SDK | OTLP gRPC (`:4317`) | Jaeger | Spans with attributes |
| **Logs** | Python logging | JSON files → Filebeat | Elasticsearch → Kibana | Structured events |

All three channels share a common request ID (`X-Request-ID`) set by the `CorrelationMiddleware`, enabling cross-channel correlation.
