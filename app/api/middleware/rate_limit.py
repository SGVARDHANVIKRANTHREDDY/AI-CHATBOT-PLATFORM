from __future__ import annotations
import time
from fastapi import Request, HTTPException
from starlette.status import HTTP_429_TOO_MANY_REQUESTS
from app.config.settings import settings
import redis.asyncio as redis
from typing import Optional

class RateLimiter:
    """Redis-based sliding window rate limiter."""
    
    def __init__(self, url: str = settings.REDIS_URL, requests_per_minute: int = 60):
        self._url = url
        self.limit = requests_per_minute
        self._client: Optional[redis.Redis] = None

    async def _get_client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(self._url, decode_responses=True)
        return self._client

    async def check_rate_limit(self, client_id: str):
        try:
            client = await self._get_client()
            now = time.time()
            key = f"rate_limit:{client_id}"
            
            async with client.pipeline() as pipe:
                # Remove timestamps older than 60 seconds
                await pipe.zremrangebyscore(key, 0, now - 60)
                await pipe.zadd(key, {str(now): now})
                await pipe.zcard(key)
                await pipe.expire(key, 60)
                results = await pipe.execute()
                
                request_count = results[2]
                if request_count > self.limit:
                    raise HTTPException(
                        status_code=HTTP_429_TOO_MANY_REQUESTS,
                        detail="Rate limit exceeded. Try again in a minute."
                    )
        except HTTPException:
            raise
        except Exception as e:
            # Fallback: allow request if Redis fails but log it
            from app.shared.utils import get_logger
            get_logger(__name__).error(f"Rate limiter failed: {e}")
