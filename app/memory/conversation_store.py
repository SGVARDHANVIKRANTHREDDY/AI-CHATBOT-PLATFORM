from __future__ import annotations

from typing import Any

import asyncpg

from app.config.settings import settings
from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class ConversationStore:
    """PostgreSQL-based long-term conversation store."""

    def __init__(self, dsn: str = settings.POSTGRES_URL):
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(dsn=self._dsn)
        return self._pool

    async def initialize(self):
        """Initialize schema if not exists."""
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute("""
                    CREATE TABLE IF NOT EXISTS interactions (
                        id SERIAL PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        role TEXT NOT NULL,
                        content TEXT NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT NOW()
                    );
                    CREATE INDEX IF NOT EXISTS idx_interactions_session_id ON interactions(session_id);
                """)
        except Exception as e:
            _LOG.error(f"Postgres init failed: {e}")

    async def save_interaction(self, session_id: str, role: str, content: str):
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO interactions(session_id, role, content) VALUES($1, $2, $3)", session_id, role, content
                )
        except Exception as e:
            _LOG.error(f"Postgres save failed: {e}")

    async def get_history(self, session_id: str, limit: int = 20) -> list[dict[str, Any]]:
        try:
            pool = await self._get_pool()
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    "SELECT role, content, created_at FROM interactions WHERE session_id = $1 ORDER BY created_at DESC LIMIT $2",
                    session_id,
                    limit,
                )
                return [
                    {"role": r["role"], "content": r["content"], "created_at": r["created_at"]} for r in reversed(rows)
                ]
        except Exception as e:
            _LOG.error(f"Postgres get history failed: {e}")
            return []
