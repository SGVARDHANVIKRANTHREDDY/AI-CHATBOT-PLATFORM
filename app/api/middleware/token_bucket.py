"""
Redis Token-Bucket Rate Limiter — per-user, per-IP, per-API-key.

Algorithm:
    Each bucket stores ``{tokens, last_refill}`` in a Redis hash.
    On every request the bucket is refilled based on elapsed time,
    then one token is consumed.  If no tokens remain → 429.

Buckets
-------
``general``  — 100 requests / minute (per identity)
``agent``    — 10  agent-execution requests / minute (per identity)

Identity resolution order:
    1. Authenticated user ID  (from JWT ``sub`` claim)
    2. API key               (``X-API-Key`` header)
    3. Client IP             (fallback)
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

import redis.asyncio as redis
from fastapi import HTTPException, Request
from starlette.status import HTTP_429_TOO_MANY_REQUESTS

from app.config.settings import settings
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

# Lua script: atomic refill-and-consume in one round trip
_TOKEN_BUCKET_LUA = """
local key       = KEYS[1]
local capacity  = tonumber(ARGV[1])
local refill_rate = tonumber(ARGV[2])   -- tokens per second
local now       = tonumber(ARGV[3])
local ttl       = tonumber(ARGV[4])

local data = redis.call('HMGET', key, 'tokens', 'last')
local tokens = tonumber(data[1])
local last   = tonumber(data[2])

if tokens == nil then
    tokens = capacity
    last   = now
end

-- Refill
local elapsed = math.max(0, now - last)
tokens = math.min(capacity, tokens + elapsed * refill_rate)
last   = now

-- Consume
if tokens < 1 then
    redis.call('HMSET', key, 'tokens', tokens, 'last', last)
    redis.call('EXPIRE', key, ttl)
    return 0
end

tokens = tokens - 1
redis.call('HMSET', key, 'tokens', tokens, 'last', last)
redis.call('EXPIRE', key, ttl)
return 1
"""


@dataclass(frozen=True)
class BucketConfig:
    """Defines a token-bucket rate limit tier."""
    capacity: int
    refill_per_second: float
    ttl: int = 120  # Redis key expiry


# Default tiers
GENERAL_BUCKET = BucketConfig(capacity=100, refill_per_second=100 / 60)
AGENT_BUCKET = BucketConfig(capacity=10, refill_per_second=10 / 60)


class TokenBucketRateLimiter:
    """Production Redis token-bucket rate limiter.

    Supports multiple bucket tiers and resolves caller identity from
    user ID, API key, or IP address.
    """

    def __init__(
        self,
        redis_url: str = settings.REDIS_URL,
        general: BucketConfig = GENERAL_BUCKET,
        agent: BucketConfig = AGENT_BUCKET,
    ) -> None:
        self._url = redis_url
        self.general = general
        self.agent = agent
        self._client: Optional[redis.Redis] = None
        self._script_sha: Optional[str] = None

    async def _get_client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(self._url, decode_responses=True)
        return self._client

    async def _ensure_script(self, client: redis.Redis) -> str:
        if self._script_sha is None:
            self._script_sha = await client.script_load(_TOKEN_BUCKET_LUA)
        return self._script_sha

    # ── Identity resolution ───────────────────────────────────────

    @staticmethod
    def resolve_identity(request: Request) -> str:
        """Determine caller identity: user_id > api_key > IP."""
        # JWT-authenticated user (set by JWTAuthMiddleware)
        user_id = getattr(request.state, "user_id", None)
        if user_id:
            return f"user:{user_id}"

        api_key = request.headers.get("X-API-Key")
        if api_key:
            return f"apikey:{api_key[-8:]}"  # last 8 chars only

        host = request.client.host if request.client else "unknown"
        return f"ip:{host}"

    # ── Public API ────────────────────────────────────────────────

    async def check(
        self,
        request: Request,
        *,
        bucket_name: str = "general",
    ) -> None:
        """Consume one token from the named bucket.  Raises 429 on exhaustion."""
        identity = self.resolve_identity(request)
        bucket = self.agent if bucket_name == "agent" else self.general

        try:
            client = await self._get_client()
            sha = await self._ensure_script(client)

            key = f"rl:{bucket_name}:{identity}"
            allowed = await client.evalsha(
                sha,
                1,
                key,
                str(bucket.capacity),
                str(bucket.refill_per_second),
                str(time.time()),
                str(bucket.ttl),
            )

            if not allowed:
                _LOG.warning(
                    "Rate limit exceeded: bucket=%s identity=%s",
                    bucket_name,
                    identity,
                )
                raise HTTPException(
                    status_code=HTTP_429_TOO_MANY_REQUESTS,
                    detail=f"Rate limit exceeded ({bucket_name}: "
                           f"{bucket.capacity}/min). Retry later.",
                    headers={"Retry-After": "60"},
                )

        except HTTPException:
            raise
        except Exception as exc:
            # Redis down → fail-open but log
            _LOG.error("Rate limiter unavailable: %s", exc)
