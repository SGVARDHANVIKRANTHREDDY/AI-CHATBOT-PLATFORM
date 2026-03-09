"""
Request Protection Middleware — Size limits, timeout, and abuse detection.

Features:
    • Request body size cap (default 1 MB)
    • Per-request timeout (default 60 s)
    • Abuse detection heuristics with structured logging
"""

from __future__ import annotations

import asyncio
import time

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.status import HTTP_408_REQUEST_TIMEOUT, HTTP_413_REQUEST_ENTITY_TOO_LARGE

from app.shared.utils import get_logger

_LOG = get_logger(__name__)

# ── Defaults (overridable via settings / env) ─────────────────────

MAX_BODY_BYTES: int = 1 * 1024 * 1024  # 1 MB
REQUEST_TIMEOUT_SECONDS: float = 60.0  # 60 s


# ── Request size limit middleware ─────────────────────────────────


class RequestSizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests whose Content-Length exceeds the cap."""

    def __init__(self, app, max_bytes: int = MAX_BODY_BYTES):
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_bytes:
            _LOG.warning(
                "Request too large: %s bytes from %s %s",
                content_length,
                request.client.host if request.client else "unknown",
                request.url.path,
            )
            return JSONResponse(
                status_code=HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                content={
                    "ok": False,
                    "error": "Request body too large",
                    "detail": f"Request body exceeds {self.max_bytes} bytes",
                },
            )
        return await call_next(request)


# ── Timeout middleware ────────────────────────────────────────────


class TimeoutMiddleware(BaseHTTPMiddleware):
    """Cancel requests that exceed the timeout threshold."""

    def __init__(self, app, timeout: float = REQUEST_TIMEOUT_SECONDS):
        super().__init__(app)
        self.timeout = timeout

    async def dispatch(self, request: Request, call_next):
        try:
            return await asyncio.wait_for(call_next(request), timeout=self.timeout)
        except TimeoutError:
            client = request.client.host if request.client else "unknown"
            _LOG.warning(
                "Request timed out after %.1fs: %s %s from %s",
                self.timeout,
                request.method,
                request.url.path,
                client,
            )
            return JSONResponse(
                status_code=HTTP_408_REQUEST_TIMEOUT,
                content={
                    "ok": False,
                    "error": "Request timed out",
                    "detail": f"Processing exceeded {self.timeout}s limit",
                },
            )


# ── Abuse detection middleware ────────────────────────────────────

# Thresholds for abuse heuristics
_ABUSE_RAPID_WINDOW = 5.0  # seconds
_ABUSE_RAPID_COUNT = 20  # requests in window
_ABUSE_LARGE_BODY_THRESHOLD = 512 * 1024  # 512 KB

# In-memory per-IP tracker (lightweight; heavy abuse goes to rate limiter)
_ip_tracker: dict[str, list[float]] = {}


class AbuseDetectionMiddleware(BaseHTTPMiddleware):
    """Log structured abuse signals for downstream SIEM / alerting."""

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        now = time.time()

        signals: list[str] = []

        # 1. Rapid-fire detection
        timestamps = _ip_tracker.setdefault(client_ip, [])
        # Prune old entries
        timestamps[:] = [t for t in timestamps if now - t < _ABUSE_RAPID_WINDOW]
        timestamps.append(now)
        if len(timestamps) > _ABUSE_RAPID_COUNT:
            signals.append("rapid_fire")

        # 2. Oversized body
        cl = request.headers.get("content-length")
        if cl and int(cl) > _ABUSE_LARGE_BODY_THRESHOLD:
            signals.append("oversized_body")

        # 3. Missing / suspicious user-agent
        ua = request.headers.get("user-agent", "")
        if not ua or len(ua) < 5:
            signals.append("missing_user_agent")

        # 4. Path traversal attempt
        path = request.url.path
        if ".." in path or "%2e%2e" in path.lower():
            signals.append("path_traversal")

        # 5. Excessive header count
        if len(request.headers) > 50:
            signals.append("excessive_headers")

        if signals:
            _LOG.warning(
                "Abuse signal detected: client=%s path=%s signals=%s method=%s user_agent=%s",
                client_ip,
                path,
                signals,
                request.method,
                ua[:100],
            )

        response = await call_next(request)
        return response
