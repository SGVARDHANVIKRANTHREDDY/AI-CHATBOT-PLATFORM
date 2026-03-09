"""
Prometheus Telemetry — Unified metrics for the AI platform.

Extends the baseline metrics with production-hardening counters and
histograms for reliability, tool execution, RAG retrieval, vector
queries, and agent crashes.

All new metrics follow the Prometheus naming conventions:
    <namespace>_<subsystem>_<name>_<unit>
"""
from __future__ import annotations
from prometheus_client import Counter, Histogram, Summary, Gauge, generate_latest, CONTENT_TYPE_LATEST
from fastapi import Response
import time

# ─── Core Metrics ─────────────────────────────────────────────────
REQUEST_LATENCY = Histogram(
    "request_latency_seconds", "Latency of API requests", ["endpoint"]
)
LLM_TOKEN_USAGE = Counter(
    "llm_token_usage_total", "Total tokens consumed", ["model", "type"]
)
RAG_HIT_COUNT = Counter(
    "rag_hits_total", "Total RAG hits retrieved", ["status"]
)
CHAT_ERRORS = Counter(
    "chat_errors_total", "Total errors in chat pipeline", ["type"]
)
SEMANTIC_CACHE_HITS = Counter(
    "semantic_cache_hits_total", "Total semantic cache hits", ["status"]
)
SEMANTIC_CACHE_LATENCY = Histogram(
    "semantic_cache_latency_seconds",
    "Latency of semantic cache lookups",
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5],
)
SEMANTIC_CACHE_LLM_SAVINGS = Counter(
    "semantic_cache_llm_savings_total",
    "LLM calls avoided thanks to semantic cache hits",
)
SEMANTIC_CACHE_SIZE = Gauge(
    "semantic_cache_entries",
    "Current number of entries in the semantic cache",
)

# ─── Observability: Requested Metrics ─────────────────────────────

AGENT_EXECUTION_TIME = Histogram(
    "agent_execution_time_seconds",
    "End-to-end wall-clock time of an agent execution loop",
    ["agent_type"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 20, 30, 60],
)

VECTOR_SEARCH_LATENCY = Histogram(
    "vector_search_latency_seconds",
    "Latency of a single vector similarity search",
    ["backend"],
    buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
)

TOOL_CALL_COUNT = Counter(
    "tool_call_count_total",
    "Total number of tool calls executed",
    ["tool_name", "status"],
)

LLM_CALL_DURATION = Histogram(
    "llm_call_duration_seconds",
    "Duration of individual LLM inference calls",
    ["model", "provider"],
    buckets=[0.1, 0.5, 1, 2, 5, 10, 20, 30],
)

# ─── AI Telemetry Metrics (Frontier Layer) ────────────────────────
AI_AGENT_ITERATIONS = Histogram(
    "ai_agent_iterations", 
    "Number of reasoning iterations per request",
    buckets=[1, 2, 3, 5, 8, 10, 15, 20]
)
TOOL_SELECTION_LATENCY = Histogram(
    "tool_selection_latency_seconds",
    "Latency of neural tool selection",
    ["tool_name"],
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0]
)
REASONING_GRAPH_NODES = Histogram(
    "reasoning_graph_nodes",
    "Number of nodes in the reasoning graph per request",
    buckets=[1, 3, 5, 10, 20, 30, 50]
)
REASONING_GRAPH_DEPTH = Histogram(
    "reasoning_graph_depth",
    "Maximum depth of reasoning graph per request",
    buckets=[1, 2, 3, 5, 7, 10]
)
HALLUCINATION_RATE = Counter(
    "hallucination_detections_total",
    "Total count of hallucination detections by the critic",
    ["severity"]
)
PROMPT_EVOLUTION_SCORE = Histogram(
    "prompt_evolution_score",
    "Performance score of evolved prompts",
    ["prompt_key"],
    buckets=[0.0, 0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.9, 1.0]
)
SWARM_AGENT_COUNT = Histogram(
    "swarm_agents_active",
    "Number of concurrent swarm agents per execution",
    buckets=[1, 2, 3, 5, 8, 10]
)
KNOWLEDGE_BUILDER_DOCS = Counter(
    "knowledge_builder_documents_ingested_total",
    "Total documents ingested by the knowledge builder"
)

# ─── Production Hardening Metrics ─────────────────────────────────

# Reliability: LLM failures
LLM_REQUEST_FAILURES = Counter(
    "llm_request_failures_total",
    "Total LLM request failures by provider and error type",
    ["provider", "error_type"],
)

# Reliability: Tool execution failures
TOOL_EXECUTION_FAILURES = Counter(
    "tool_execution_failures_total",
    "Total tool execution failures by tool name and error type",
    ["tool_name", "error_type"],
)

# RAG: Retrieval latency
RAG_RETRIEVAL_LATENCY = Histogram(
    "rag_retrieval_latency_seconds",
    "Latency of RAG retrieval operations",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0],
)

# Vector: Query latency
VECTOR_QUERY_LATENCY = Histogram(
    "vector_query_latency_seconds",
    "Latency of vector queries",
    buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.25, 0.5],
)

# Vector: Index size gauge
VECTOR_INDEX_SIZE = Gauge(
    "vector_index_size_total",
    "Total number of vectors in the index",
)

# Vector: Query throughput
VECTOR_QUERY_THROUGHPUT = Counter(
    "vector_queries_total",
    "Total number of vector search queries executed",
    ["backend"],
)

# Agent: Crash/failure counter
AGENT_CRASHES = Counter(
    "agent_crashes_total",
    "Total agent execution crashes by agent type",
    ["agent_type"],
)

# Circuit breaker state transitions
CIRCUIT_BREAKER_STATE_CHANGES = Counter(
    "circuit_breaker_state_changes_total",
    "Circuit breaker state transition events",
    ["component", "state"],
)

# Load guard gauges
LOAD_GUARD_ACTIVE_REQUESTS = Gauge(
    "load_guard_active_requests",
    "Current number of active requests through the load guard",
)
LOAD_GUARD_ACTIVE_AGENTS = Gauge(
    "load_guard_active_agents",
    "Current number of active agent executions",
)
LOAD_GUARD_REJECTIONS = Counter(
    "load_guard_rejections_total",
    "Total requests rejected by load guard",
    ["limiter"],
)

# Response validation
RESPONSE_VALIDATION_ISSUES = Counter(
    "response_validation_issues_total",
    "Total response validation issues detected",
    ["category", "severity"],
)

# Trust scoring
TRUST_EVALUATION_RESULTS = Counter(
    "trust_evaluation_results_total",
    "Trust evaluation outcomes for ingested documents",
    ["status"],
)

# ─── Content Safety Metrics ───────────────────────────────────────

CONTENT_SAFETY_SCANS = Counter(
    "content_safety_scans_total",
    "Documents scanned by the content safety filter",
    ["result"],   # accepted | rejected | quarantined
)
CONTENT_SAFETY_INJECTION_SCORE = Histogram(
    "content_safety_injection_score",
    "Distribution of prompt-injection scores across ingested docs",
    buckets=[0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
)
CONTENT_SAFETY_QUARANTINE_SIZE = Gauge(
    "content_safety_quarantine_size",
    "Current number of documents in quarantine",
)

# ─── Watchdog Metrics ─────────────────────────────────────────────

WATCHDOG_TERMINATIONS = Counter(
    "watchdog_terminations_total",
    "Agent executions terminated by the watchdog",
    ["reason"],
)
WATCHDOG_EXECUTION_DURATION = Histogram(
    "watchdog_execution_duration_seconds",
    "Wall-clock duration of watchdog-monitored executions",
    buckets=[1, 5, 10, 15, 20, 30, 45, 60],
)
WATCHDOG_ITERATIONS_USED = Histogram(
    "watchdog_iterations_used",
    "Number of agent iterations consumed per execution",
    buckets=[1, 2, 3, 5, 8, 10, 15, 20],
)
WATCHDOG_TOOL_CALLS_USED = Histogram(
    "watchdog_tool_calls_used",
    "Number of tool calls consumed per execution",
    buckets=[1, 3, 5, 10, 15, 20, 30],
)


def metrics_endpoint():
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


class track_latency:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint

    def __enter__(self):
        self.start = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        duration = time.perf_counter() - self.start
        REQUEST_LATENCY.labels(endpoint=self.endpoint).observe(duration)
