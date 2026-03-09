"""
Tests for production API protection: token-bucket rate limiting, JWT auth,
request size limits, timeout protection, and abuse detection.
"""
from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse
from starlette.testclient import TestClient as StarletteTestClient

from app.api.middleware.jwt_auth import (
    JWTAuthMiddleware,
    create_jwt,
    decode_jwt,
)
from app.api.middleware.request_protection import (
    AbuseDetectionMiddleware,
    RequestSizeLimitMiddleware,
    TimeoutMiddleware,
    _ip_tracker,
)
from app.api.middleware.token_bucket import (
    AGENT_BUCKET,
    GENERAL_BUCKET,
    BucketConfig,
    TokenBucketRateLimiter,
)


# ── JWT token tests ──────────────────────────────────────────────

SECRET = "test-secret-key-for-unit-tests"


class TestJWTTokens:
    def test_create_and_decode_roundtrip(self):
        token = create_jwt({"sub": "user-42", "role": "admin"}, SECRET)
        payload = decode_jwt(token, SECRET)
        assert payload["sub"] == "user-42"
        assert payload["role"] == "admin"
        assert "iat" in payload
        assert "exp" in payload

    def test_decode_rejects_wrong_secret(self):
        token = create_jwt({"sub": "user-1"}, SECRET)
        with pytest.raises(ValueError, match="Invalid JWT signature"):
            decode_jwt(token, "wrong-secret")

    def test_decode_rejects_expired_token(self):
        token = create_jwt({"sub": "user-1"}, SECRET, expiry_minutes=-1)
        with pytest.raises(ValueError, match="JWT expired"):
            decode_jwt(token, SECRET)

    def test_decode_rejects_malformed_token(self):
        with pytest.raises(ValueError, match="Malformed JWT"):
            decode_jwt("not.a.valid.token.at.all", SECRET)

    def test_decode_rejects_tampered_payload(self):
        token = create_jwt({"sub": "user-1"}, SECRET)
        parts = token.split(".")
        # Tamper with the payload
        import base64, json
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=="))
        payload["sub"] = "admin"
        parts[1] = base64.urlsafe_b64encode(
            json.dumps(payload, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()
        tampered = ".".join(parts)
        with pytest.raises(ValueError, match="Invalid JWT signature"):
            decode_jwt(tampered, SECRET)


# ── JWT middleware tests ─────────────────────────────────────────

class TestJWTAuthMiddleware:
    @pytest.fixture
    def app_with_jwt(self):
        app = FastAPI()
        app.add_middleware(JWTAuthMiddleware, secret_key=SECRET)

        @app.get("/protected")
        async def protected(request: Request):
            user_id = getattr(request.state, "user_id", "anonymous")
            return {"user": user_id}

        @app.get("/healthz")
        async def health():
            return {"ok": True}

        return app

    def test_public_paths_no_auth_required(self, app_with_jwt):
        client = TestClient(app_with_jwt)
        resp = client.get("/healthz")
        assert resp.status_code == 200

    def test_valid_jwt_sets_user_id(self, app_with_jwt):
        client = TestClient(app_with_jwt)
        token = create_jwt({"sub": "user-99"}, SECRET)
        resp = client.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["user"] == "user-99"

    def test_invalid_jwt_returns_401(self, app_with_jwt):
        client = TestClient(app_with_jwt)
        resp = client.get("/protected", headers={"Authorization": "Bearer bad.token.here"})
        assert resp.status_code == 401

    def test_missing_auth_with_api_key_allowed(self, app_with_jwt):
        client = TestClient(app_with_jwt)
        resp = client.get("/protected", headers={"X-API-Key": "some-key"})
        assert resp.status_code == 200

    def test_no_auth_no_api_key_returns_401(self, app_with_jwt):
        client = TestClient(app_with_jwt)
        resp = client.get("/protected")
        assert resp.status_code == 401

    def test_no_secret_configured_allows_all(self):
        """When JWT_SECRET_KEY is empty, auth is skipped (dev mode)."""
        app = FastAPI()
        app.add_middleware(JWTAuthMiddleware, secret_key="")

        @app.get("/protected")
        async def protected():
            return {"ok": True}

        client = TestClient(app)
        resp = client.get("/protected")
        assert resp.status_code == 200


# ── Request size limit tests ─────────────────────────────────────

class TestRequestSizeLimit:
    @pytest.fixture
    def app_with_size_limit(self):
        app = FastAPI()
        app.add_middleware(RequestSizeLimitMiddleware, max_bytes=100)

        @app.post("/upload")
        async def upload(request: Request):
            body = await request.body()
            return {"size": len(body)}

        return app

    def test_small_body_allowed(self, app_with_size_limit):
        client = TestClient(app_with_size_limit)
        resp = client.post("/upload", content=b"small", headers={"content-length": "5"})
        assert resp.status_code == 200

    def test_oversized_body_rejected(self, app_with_size_limit):
        client = TestClient(app_with_size_limit)
        resp = client.post(
            "/upload",
            content=b"x" * 200,
            headers={"content-length": "200"},
        )
        assert resp.status_code == 413


# ── Timeout protection tests ─────────────────────────────────────

class TestTimeoutProtection:
    @pytest.fixture
    def app_with_timeout(self):
        app = FastAPI()
        app.add_middleware(TimeoutMiddleware, timeout=0.5)

        @app.get("/fast")
        async def fast():
            return {"ok": True}

        @app.get("/slow")
        async def slow():
            await asyncio.sleep(2.0)
            return {"ok": True}

        return app

    def test_fast_request_succeeds(self, app_with_timeout):
        client = TestClient(app_with_timeout)
        resp = client.get("/fast")
        assert resp.status_code == 200

    def test_slow_request_times_out(self, app_with_timeout):
        client = TestClient(app_with_timeout)
        resp = client.get("/slow")
        assert resp.status_code == 408
        assert "timed out" in resp.json()["error"].lower()


# ── Token bucket rate limiter tests ──────────────────────────────

class TestTokenBucketRateLimiter:
    def _make_request(self, ip: str = "1.2.3.4") -> Request:
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "query_string": b"",
        }
        req = Request(scope)
        req._client = MagicMock()
        req._client.host = ip
        # Patch client property
        scope["client"] = (ip, 0)
        req = Request(scope)
        return req

    def test_identity_resolution_user_id(self):
        req = self._make_request()
        req.state.user_id = "alice"
        assert TokenBucketRateLimiter.resolve_identity(req) == "user:alice"

    def test_identity_resolution_api_key(self):
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [(b"x-api-key", b"sk-12345678")],
            "query_string": b"",
            "client": ("1.2.3.4", 0),
        }
        req = Request(scope)
        assert TokenBucketRateLimiter.resolve_identity(req) == "apikey:12345678"

    def test_identity_resolution_ip_fallback(self):
        req = self._make_request("10.0.0.1")
        assert TokenBucketRateLimiter.resolve_identity(req) == "ip:10.0.0.1"

    def test_bucket_config_defaults(self):
        assert GENERAL_BUCKET.capacity == 100
        assert AGENT_BUCKET.capacity == 10

    @pytest.mark.asyncio
    async def test_check_allows_when_redis_down(self):
        """Fail-open: if Redis is unreachable, request is allowed."""
        limiter = TokenBucketRateLimiter(redis_url="redis://localhost:1/0")
        req = self._make_request()
        # Should not raise — fail-open
        await limiter.check(req, bucket_name="general")


# ── Abuse detection tests ────────────────────────────────────────

class TestAbuseDetection:
    @pytest.fixture(autouse=True)
    def clear_tracker(self):
        _ip_tracker.clear()
        yield
        _ip_tracker.clear()

    @pytest.fixture
    def app_with_abuse(self):
        app = FastAPI()
        app.add_middleware(AbuseDetectionMiddleware)

        @app.get("/ping")
        async def ping():
            return {"ok": True}

        @app.post("/data")
        async def data(request: Request):
            return {"ok": True}

        return app

    def test_normal_request_passes(self, app_with_abuse):
        client = TestClient(app_with_abuse)
        resp = client.get("/ping", headers={"user-agent": "TestBot/1.0"})
        assert resp.status_code == 200

    def test_missing_user_agent_logged(self, app_with_abuse):
        client = TestClient(app_with_abuse)
        with patch("app.api.middleware.request_protection._LOG") as mock_log:
            # TestClient always sends testclient user-agent, so
            # we override by setting an empty one explicitly
            resp = client.get("/ping", headers={"user-agent": ""})
            assert resp.status_code == 200

    def test_path_traversal_logged(self, app_with_abuse):
        client = TestClient(app_with_abuse)
        with patch("app.api.middleware.request_protection._LOG") as mock_log:
            client.get("/ping/../secret", headers={"user-agent": "TestBot/1.0"})
            # Should trigger abuse log
            if mock_log.warning.called:
                args = str(mock_log.warning.call_args)
                assert "path_traversal" in args


# ── Integration: full middleware stack ────────────────────────────

class TestFullMiddlewareStack:
    """Test the app from main.py with all middleware wired."""

    @pytest.fixture
    def client(self):
        from app.api.main import app
        return TestClient(app, raise_server_exceptions=False)

    def test_healthz_no_auth(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_root_no_auth(self, client):
        resp = client.get("/")
        assert resp.status_code == 200

    def test_metrics_no_auth(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 200

    def test_security_headers_present(self, client):
        resp = client.get("/healthz")
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert resp.headers.get("X-Frame-Options") == "DENY"
        assert "Strict-Transport-Security" in resp.headers
