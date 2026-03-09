"""
Distributed Tracing — OpenTelemetry integration for the AI platform.

Provides distributed tracing across the multi-agent reasoning pipeline,
enabling end-to-end visibility from API request to LLM call to tool
execution to response.

Components:
    init_tracing()  — Set up OTLP exporter and tracer provider.
    @traced         — Decorator for async functions that creates spans.
    get_tracer()    — Get the configured tracer instance.
    start_span()    — Context manager for manual span creation.

Span naming convention:
    <component>.<operation>   e.g. "api.request", "agent.plan",
    "tool.execute", "rag.retrieve", "llm.ask"
"""
from __future__ import annotations

import asyncio
import functools
import time
from contextlib import contextmanager
from typing import Any, Callable, Optional, TypeVar

from app.shared.utils import get_logger

_LOG = get_logger(__name__)

T = TypeVar("T")

# ── Global state ──────────────────────────────────────────────────
_tracer = None
_initialized = False


def init_tracing(
    service_name: str = "ai-platform",
    otlp_endpoint: Optional[str] = None,
    enabled: bool = True,
) -> None:
    """Initialize OpenTelemetry tracing.

    Args:
        service_name: Name of this service in traces.
        otlp_endpoint: OTLP collector endpoint (e.g. "http://localhost:4317").
                       If None, uses a no-op tracer (safe for dev).
        enabled: Master switch for tracing.
    """
    global _tracer, _initialized

    if _initialized:
        return

    if not enabled:
        _LOG.info("Tracing disabled — using no-op tracer")
        _initialized = True
        return

    try:
        from opentelemetry import trace
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.resources import Resource

        resource = Resource.create({"service.name": service_name})
        provider = TracerProvider(resource=resource)

        if otlp_endpoint:
            try:
                from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                    OTLPSpanExporter,
                )
                from opentelemetry.sdk.trace.export import BatchSpanProcessor

                exporter = OTLPSpanExporter(endpoint=otlp_endpoint)
                provider.add_span_processor(BatchSpanProcessor(exporter))
                _LOG.info("OTLP exporter configured: %s", otlp_endpoint)
            except ImportError:
                _LOG.warning(
                    "opentelemetry-exporter-otlp not installed — tracing to console only"
                )
        else:
            _LOG.info("No OTLP endpoint configured — traces available in-process only")

        trace.set_tracer_provider(provider)
        _tracer = trace.get_tracer(service_name)
        _initialized = True
        _LOG.info("OpenTelemetry tracing initialized for '%s'", service_name)

    except ImportError:
        _LOG.info(
            "OpenTelemetry SDK not installed — tracing unavailable. "
            "Install with: pip install opentelemetry-sdk opentelemetry-api"
        )
        _initialized = True


def get_tracer():
    """Get the configured tracer, or None if tracing is not available."""
    return _tracer


@contextmanager
def start_span(name: str, attributes: Optional[dict] = None):
    """Manual context-manager span for non-decorator usage.

    Yields a span object (or None when tracing is off).  The span is
    automatically ended and annotated with ``duration_ms``.

    Usage::

        with start_span("rag.retrieve", {"query": q}) as span:
            results = retriever.search(q)
            if span:
                span.set_attribute("result_count", len(results))
    """
    tracer = get_tracer()
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(name) as span:
        if attributes:
            for k, v in attributes.items():
                span.set_attribute(k, v)
        t0 = time.perf_counter()
        try:
            yield span
            span.set_attribute("status", "ok")
        except Exception as exc:
            span.set_attribute("status", "error")
            span.set_attribute("error.type", type(exc).__name__)
            span.set_attribute("error.message", str(exc)[:256])
            span.record_exception(exc)
            raise
        finally:
            span.set_attribute("duration_ms", round((time.perf_counter() - t0) * 1000, 2))


def traced(
    operation_name: Optional[str] = None,
    attributes: Optional[dict] = None,
) -> Callable:
    """Decorator that creates an OpenTelemetry span for an async function.

    Args:
        operation_name: Custom span name. Defaults to function name.
        attributes: Static attributes to add to every span.

    Example::

        @traced("llm.ask")
        async def ask(self, prompt: str) -> str:
            ...

        @traced(attributes={"component": "tool_runner"})
        async def run_tool(self, name: str) -> str:
            ...
    """

    def decorator(func: Callable) -> Callable:
        span_name = operation_name or f"{func.__module__}.{func.__qualname__}"

        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            tracer = get_tracer()
            if tracer is None:
                return await func(*args, **kwargs)

            with tracer.start_as_current_span(span_name) as span:
                if attributes:
                    for k, v in attributes.items():
                        span.set_attribute(k, v)

                t0 = time.perf_counter()
                try:
                    result = await func(*args, **kwargs)
                    span.set_attribute("status", "ok")
                    span.set_attribute("duration_ms", round((time.perf_counter() - t0) * 1000, 2))
                    return result
                except Exception as exc:
                    span.set_attribute("status", "error")
                    span.set_attribute("error.type", type(exc).__name__)
                    span.set_attribute("error.message", str(exc)[:256])
                    span.set_attribute("duration_ms", round((time.perf_counter() - t0) * 1000, 2))
                    span.record_exception(exc)
                    raise

        return wrapper

    # Allow @traced without parentheses
    if callable(operation_name):
        func = operation_name
        operation_name = None
        return decorator(func)

    return decorator
