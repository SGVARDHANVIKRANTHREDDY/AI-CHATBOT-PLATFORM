from __future__ import annotations
import os
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.api.routes import chat
from app.api.middleware.auth import get_api_key
from app.api.middleware.correlation import CorrelationMiddleware
from app.api.middleware.jwt_auth import JWTAuthMiddleware
from app.api.middleware.request_protection import (
    AbuseDetectionMiddleware,
    RequestSizeLimitMiddleware,
    TimeoutMiddleware,
)
from app.api.middleware.token_bucket import (
    AGENT_BUCKET,
    GENERAL_BUCKET,
    BucketConfig,
    TokenBucketRateLimiter,
)
from app.config.settings import settings
from app.shared.utils import log_event, get_logger
from app.shared.tracing import init_tracing

_LOG = get_logger(__name__)

# Initialize distributed tracing on module load
init_tracing(
    service_name="ai-platform",
    otlp_endpoint=os.getenv("OTLP_ENDPOINT"),
    enabled=os.getenv("TRACING_ENABLED", "true").lower() == "true",
)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

app = FastAPI(
    title=f"{settings.ASSISTANT_NAME} API",
    version="3.0 (Elite AI Assistant Architecture)"
)

# 1. Global Exception Handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    log_event(_LOG, "unhandled_exception", error=str(exc))
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": "Internal Server Error",
            "detail": str(exc) if settings.DEBUG else "An unexpected error occurred."
        }
    )

# 2. Add Middlewares (outermost → innermost execution order)
#    Starlette processes these bottom-to-top, so bottom = first executed.

# — Security headers (runs last, adds headers to every response)
app.add_middleware(SecurityHeadersMiddleware)

# — Correlation / tracing (request ID + observability)
app.add_middleware(CorrelationMiddleware)

# — CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# — Timeout protection
app.add_middleware(TimeoutMiddleware, timeout=settings.REQUEST_TIMEOUT_SECONDS)

# — Request size limit
app.add_middleware(RequestSizeLimitMiddleware, max_bytes=settings.MAX_REQUEST_BODY_BYTES)

# — JWT authentication (populates request.state.user_id)
app.add_middleware(JWTAuthMiddleware, secret_key=settings.JWT_SECRET_KEY)

# — Abuse detection (structured logging for SIEM)
app.add_middleware(AbuseDetectionMiddleware)

# 3. Token-bucket rate limiter (uses identity set by JWT middleware)
rate_limiter = TokenBucketRateLimiter(
    redis_url=settings.REDIS_URL,
    general=BucketConfig(
        capacity=settings.API_RATE_LIMIT,
        refill_per_second=settings.API_RATE_LIMIT / 60,
    ),
    agent=BucketConfig(
        capacity=settings.API_AGENT_RATE_LIMIT,
        refill_per_second=settings.API_AGENT_RATE_LIMIT / 60,
    ),
)


@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    """General rate-limit check on every request."""
    # Skip public endpoints
    if request.url.path in ("/", "/healthz", "/metrics", "/docs", "/openapi.json"):
        return await call_next(request)
    await rate_limiter.check(request, bucket_name="general")
    # Agent-execution endpoints get a stricter bucket too
    if request.url.path in ("/api/v1/chat", "/api/v1/chat/stream"):
        await rate_limiter.check(request, bucket_name="agent")
    return await call_next(request)


# Include routers with global API Key protection
app.include_router(
    chat.router,
    prefix="/api/v1",
    tags=["Chat"],
    dependencies=[Depends(get_api_key)]
)

from app.shared.monitoring import metrics_endpoint

@app.get("/metrics")
def get_metrics():
    return metrics_endpoint()

@app.get("/")
def root():
    return {"message": f"{settings.ASSISTANT_NAME} API is running!"}

@app.get("/healthz")
def healthz():
    return {"status": "ok", "version": "3.0"}
