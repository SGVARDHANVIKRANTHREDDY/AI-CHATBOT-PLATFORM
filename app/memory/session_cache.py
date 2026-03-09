from __future__ import annotations

import json

import redis.asyncio as redis

from app.config.settings import settings
from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class SessionCache:
    """Redis-based short-term session cache for low-latency retrieval."""

    def __init__(self, url: str = settings.REDIS_URL):
        self._url = url
        self._client: redis.Redis | None = None

    async def _get_client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(self._url, decode_responses=True)
        return self._client

    async def get_messages(self, session_id: str, limit: int = 10) -> list[dict[str, str]]:
        try:
            client = await self._get_client()
            key = f"chat:session:{session_id}"
            data = await client.lrange(key, -limit, -1)
            return [json.loads(m) for m in data]
        except Exception as e:
            _LOG.error(f"Redis get failed: {e}")
            return []

    async def add_message(self, session_id: str, role: str, content: str, ttl: int = 3600):
        try:
            client = await self._get_client()
            key = f"chat:session:{session_id}"
            payload = json.dumps({"role": role, "content": content})
            async with client.pipeline() as pipe:
                await pipe.rpush(key, payload)
                await pipe.expire(key, ttl)
                await pipe.execute()
        except Exception as e:
            _LOG.error(f"Redis add failed: {e}")

    async def clear_session(self, session_id: str):
        try:
            client = await self._get_client()
            await client.delete(f"chat:session:{session_id}")
        except Exception as e:
            _LOG.error(f"Redis delete failed: {e}")
