"""
JWT Authentication Middleware — Stateless token-based auth for the API.

Validates ``Authorization: Bearer <token>`` headers using HS256.
On success, sets ``request.state.user_id`` for downstream use
(rate limiting, audit logging, etc.).

Configuration (via env / settings):
    JWT_SECRET_KEY   — HMAC signing key (required in production)
    JWT_ALGORITHM    — default HS256
    JWT_EXPIRY_MINS  — default 60

Public paths (health, metrics, docs) are excluded automatically.
"""
from __future__ import annotations

import hmac
import time
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, Optional, Set

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.status import HTTP_401_UNAUTHORIZED

from app.config.settings import settings
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

# Paths that never require a JWT
_PUBLIC_PATHS: Set[str] = {
    "/",
    "/healthz",
    "/metrics",
    "/docs",
    "/openapi.json",
    "/redoc",
}


def _is_public(path: str) -> bool:
    return path in _PUBLIC_PATHS


# ── Minimal JWT helpers (HS256, no heavy dependency) ──────────────

import base64
import hashlib
import json


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    padding = 4 - len(s) % 4
    if padding != 4:
        s += "=" * padding
    return base64.urlsafe_b64decode(s)


def create_jwt(
    payload: Dict[str, Any],
    secret: str,
    algorithm: str = "HS256",
    expiry_minutes: int = 60,
) -> str:
    """Create a signed JWT token."""
    header = {"alg": algorithm, "typ": "JWT"}
    now = datetime.now(timezone.utc)
    payload = {
        **payload,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=expiry_minutes)).timestamp()),
    }
    segments = [
        _b64url_encode(json.dumps(header, separators=(",", ":")).encode()),
        _b64url_encode(json.dumps(payload, separators=(",", ":")).encode()),
    ]
    signing_input = f"{segments[0]}.{segments[1]}"
    signature = hmac.new(
        secret.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    segments.append(_b64url_encode(signature))
    return ".".join(segments)


def decode_jwt(token: str, secret: str) -> Dict[str, Any]:
    """Decode and verify a JWT token.  Raises ValueError on failure."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed JWT: expected 3 segments")

    signing_input = f"{parts[0]}.{parts[1]}"
    expected_sig = hmac.new(
        secret.encode(), signing_input.encode(), hashlib.sha256
    ).digest()
    actual_sig = _b64url_decode(parts[2])

    if not hmac.compare_digest(expected_sig, actual_sig):
        raise ValueError("Invalid JWT signature")

    payload = json.loads(_b64url_decode(parts[1]))

    exp = payload.get("exp")
    if exp and time.time() > exp:
        raise ValueError("JWT expired")

    return payload


# ── Middleware ─────────────────────────────────────────────────────

class JWTAuthMiddleware(BaseHTTPMiddleware):
    """Validates Bearer tokens and populates ``request.state.user_id``."""

    def __init__(self, app, secret_key: Optional[str] = None):
        super().__init__(app)
        self._secret = secret_key or getattr(settings, "JWT_SECRET_KEY", "")

    async def dispatch(self, request: Request, call_next):
        # Skip public endpoints
        if _is_public(request.url.path):
            return await call_next(request)

        # If no secret is configured, JWT is optional (dev mode)
        if not self._secret:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")

        if not auth_header.startswith("Bearer "):
            # Allow API-key-only auth when JWT isn't provided
            api_key = request.headers.get("X-API-Key")
            if api_key:
                return await call_next(request)

            return JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED,
                content={"detail": "Missing Authorization header"},
                headers={"WWW-Authenticate": "Bearer"},
            )

        token = auth_header[7:]
        try:
            payload = decode_jwt(token, self._secret)
            request.state.user_id = payload.get("sub", "anonymous")
        except ValueError as exc:
            _LOG.warning("JWT rejected: %s", exc)
            return JSONResponse(
                status_code=HTTP_401_UNAUTHORIZED,
                content={"detail": str(exc)},
                headers={"WWW-Authenticate": "Bearer"},
            )

        return await call_next(request)
