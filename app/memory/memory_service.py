from __future__ import annotations

from typing import Any

from app.memory.conversation_store import ConversationStore
from app.memory.session_cache import SessionCache
from app.memory.summarizer import Summarizer
from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class MemoryService:
    """Production-grade memory service coordinating Redis and PostgreSQL."""

    def __init__(self, session_id: str, llm_provider: Any = None):
        self.session_id = session_id
        self.cache = SessionCache()
        self.store = ConversationStore()
        self.summarizer = Summarizer(llm_provider) if llm_provider else None

    async def add_message(self, role: str, content: str):
        # 1. Add to Redis (short-term)
        await self.cache.add_message(self.session_id, role, content)

        # 2. Add to Postgres (long-term)
        await self.store.save_interaction(self.session_id, role, content)

    async def get_messages(self, limit: int = 10) -> list[dict[str, str]]:
        # 1. Try Redis first
        messages = await self.cache.get_messages(self.session_id, limit=limit)
        if messages:
            return messages

        # 2. Fallback to Postgres
        _LOG.info(f"Redis cache miss for session {self.session_id}, falling back to Postgres.")
        history = await self.store.get_history(self.session_id, limit=limit)
        return [{"role": h["role"], "content": h["content"]} for h in history]

    async def get_context_string(self, limit: int = 6) -> str:
        messages = await self.get_messages(limit=limit)
        return "\n".join([f"{m['role']}: {m['content']}" for m in messages])

    async def clear_session(self):
        await self.cache.clear_session(self.session_id)
        # Note: We usually don't delete from long-term store unless requested
