from __future__ import annotations
import time
import uuid
import contextvars
from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.shared.utils import get_logger, emit_observability_event
from app.shared.tracing import start_span
from app.shared.monitoring import REQUEST_LATENCY

_LOG = get_logger(__name__)

# Context variable to store the request ID for the current async task
request_id_ctx = contextvars.ContextVar("request_id", default=None)

class CorrelationMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        # 1. Get request ID from header or generate a new one
        rid = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        
        # 2. Set the context variable
        token = request_id_ctx.set(rid)
        
        try:
            endpoint = f"{request.method} {request.url.path}"
            t0 = time.perf_counter()

            emit_observability_event(
                _LOG, event="api.request.start", category="api",
                method=request.method, path=request.url.path,
                client_host=request.client.host if request.client else "unknown",
            )

            with start_span("api.request", {"http.method": request.method, "http.url": str(request.url.path)}) as span:
                # 3. Process the request
                response = await call_next(request)

                elapsed = time.perf_counter() - t0
                REQUEST_LATENCY.labels(endpoint=endpoint).observe(elapsed)

                emit_observability_event(
                    _LOG, event="api.request.complete", category="api",
                    duration_ms=elapsed * 1000,
                    method=request.method, path=request.url.path,
                    status_code=response.status_code,
                )

                if span:
                    span.set_attribute("http.status_code", response.status_code)

                # 4. Return the ID in the response header
                response.headers["X-Request-ID"] = rid
                return response
        finally:
            # 5. Reset context
            request_id_ctx.reset(token)

def get_request_id() -> str:
    return request_id_ctx.get() or "system"
