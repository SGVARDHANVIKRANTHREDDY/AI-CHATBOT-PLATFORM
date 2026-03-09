"""
Unified Memory Controller — Single interface for all memory systems.

Synchronizes and reconciles data across three memory layers:
    • Conversation memory (MemoryService / Redis + Postgres)
    • Vector memory (MemoryRetriever / FAISS)
    • Knowledge graph facts (GraphStore / JSON)

Consistency enforcement:
    Uses ``MemoryAuthorityResolver`` to detect and resolve conflicts
    across memory layers with the following hierarchy:

    1. Conversation memory (highest authority) — user's own assertions
    2. Knowledge graph — only if entity confidence > 0.8
    3. Vector memory (lowest) — background / learned facts

    Conflicting facts (e.g. user_language = Python vs Java) are
    resolved via authority rank → timestamp → confidence score.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from app.memory.authority import (
    MemoryAuthorityResolver,
    MemoryConflict,
    MemoryFact,
    extract_facts_from_conversation,
    extract_facts_from_kg,
    extract_facts_from_vector_context,
)
from app.shared.utils import get_logger

_LOG = get_logger(__name__)


@dataclass
class UnifiedMemorySnapshot:
    """Point-in-time view of all memory layers for a query."""

    conversation_history: list[dict[str, str]] = field(default_factory=list)
    vector_context: str = ""
    kg_facts: list[dict[str, str]] = field(default_factory=list)
    trust_scores: dict[str, float] = field(default_factory=dict)
    merged_context: str = ""
    conflicts_resolved: int = 0
    resolved_facts: dict[str, MemoryFact] = field(default_factory=dict)
    conflicts: list[MemoryConflict] = field(default_factory=list)


class UnifiedMemoryController:
    """Coordinating interface for conversation, vector, and KG memory.

    Args:
        memory_service_factory: Callable(session_id) → MemoryService
        memory_retriever: Shared MemoryRetriever (vector + KG)
        llm_provider: LLM for conflict resolution prompts (optional)
        authority_resolver: Custom resolver; one is created if omitted.
    """

    def __init__(
        self,
        memory_service_factory: Any = None,
        memory_retriever: Any = None,
        llm_provider: Any = None,
        authority_resolver: MemoryAuthorityResolver | None = None,
    ) -> None:
        self._memory_service_factory = memory_service_factory
        self._retriever = memory_retriever
        self._llm = llm_provider
        self._resolver = authority_resolver or MemoryAuthorityResolver()

        # Per-session locks to prevent inconsistent reads
        self._session_locks: dict[str, asyncio.Lock] = {}

    def _get_lock(self, session_id: str) -> asyncio.Lock:
        if session_id not in self._session_locks:
            self._session_locks[session_id] = asyncio.Lock()
        return self._session_locks[session_id]

    async def get_unified_context(
        self,
        query: str,
        session_id: str,
        *,
        conversation_limit: int = 6,
    ) -> UnifiedMemorySnapshot:
        """Retrieve a consistent, conflict-resolved snapshot.

        Steps:
            1. Fetch raw data from each memory layer.
            2. Extract normalised facts from every layer.
            3. Run MemoryAuthorityResolver to detect & resolve conflicts.
            4. Build merged context with only authoritative facts.
        """
        lock = self._get_lock(session_id)
        async with lock:
            snapshot = UnifiedMemorySnapshot()

            # 1. Fetch raw data from each layer
            snapshot.conversation_history = await self._get_conversation(session_id, conversation_limit)
            snapshot.vector_context = await self._get_vector_context(query)
            snapshot.kg_facts = self._get_kg_facts(query)

            # 2. Extract normalised facts
            all_facts = self._extract_all_facts(snapshot)

            # 3. Reconcile via authority resolver
            resolved, conflicts = self._resolver.reconcile(all_facts)
            snapshot.resolved_facts = resolved
            snapshot.conflicts = conflicts
            snapshot.conflicts_resolved = len(conflicts)

            # 4. Build merged context (authority-aware)
            snapshot.merged_context = self._merge_contexts(snapshot)

            if conflicts:
                for c in conflicts:
                    _LOG.info(
                        "Conflict on %r: %s → winner=%r (%s)",
                        c.key,
                        [f"{f.value}({f.source.name})" for f in c.facts],
                        c.winner.value if c.winner else None,
                        c.resolution_reason,
                    )

            return snapshot

    async def store_interaction(
        self,
        session_id: str,
        query: str,
        answer: str,
        kg_data: dict[str, Any] | None = None,
    ) -> None:
        """Atomically store an interaction across all memory layers."""
        lock = self._get_lock(session_id)
        async with lock:
            errors: list[str] = []

            # 1. Conversation memory
            try:
                if self._memory_service_factory:
                    svc = self._memory_service_factory(session_id)
                    await svc.add_message("user", query)
                    await svc.add_message("assistant", answer)
            except Exception as e:
                errors.append(f"conversation: {e}")
                _LOG.error("Failed to store conversation: %s", e)

            # 2. Vector memory
            try:
                if self._retriever:
                    await self._retriever.store_turn(query, answer)
            except Exception as e:
                errors.append(f"vector: {e}")
                _LOG.error("Failed to store vector memory: %s", e)

            # 3. Knowledge graph
            try:
                if kg_data and self._retriever:
                    entities = kg_data.get("entities", [])
                    relationships = kg_data.get("relationships", [])
                    if entities or relationships:
                        self._retriever.kg.add_data(entities, relationships)
            except Exception as e:
                errors.append(f"kg: {e}")
                _LOG.error("Failed to store KG data: %s", e)

            if errors:
                _LOG.warning(
                    "store_interaction partial failures for %s: %s",
                    session_id,
                    errors,
                )

    # ── Fact extraction ───────────────────────────────────────────

    @staticmethod
    def _extract_all_facts(
        snapshot: UnifiedMemorySnapshot,
    ) -> list[MemoryFact]:
        """Extract normalised facts from every memory layer."""
        facts: list[MemoryFact] = []
        facts.extend(extract_facts_from_conversation(snapshot.conversation_history))
        facts.extend(extract_facts_from_vector_context(snapshot.vector_context))
        facts.extend(extract_facts_from_kg(snapshot.kg_facts))
        return facts

    # ── Private helpers ───────────────────────────────────────────

    async def _get_conversation(self, session_id: str, limit: int) -> list[dict[str, str]]:
        try:
            if self._memory_service_factory:
                svc = self._memory_service_factory(session_id)
                return await svc.get_messages(limit=limit)
        except Exception as e:
            _LOG.error("Failed to fetch conversation for %s: %s", session_id, e)
        return []

    async def _get_vector_context(self, query: str) -> str:
        try:
            if self._retriever:
                return await self._retriever.retrieve_context(query)
        except Exception as e:
            _LOG.error("Failed to fetch vector context: %s", e)
        return ""

    def _get_kg_facts(self, query: str) -> list[dict[str, str]]:
        try:
            if self._retriever and hasattr(self._retriever, "kg"):
                entity_words = query.lower().split()
                facts: list[dict[str, str]] = []
                for word in entity_words:
                    if word in self._retriever.kg.entities:
                        facts.extend(self._retriever.kg.query(word))
                return facts
        except Exception as e:
            _LOG.error("Failed to fetch KG facts: %s", e)
        return []

    def _merge_contexts(self, snapshot: UnifiedMemorySnapshot) -> str:
        """Build merged context, applying authority-resolved facts."""
        parts: list[str] = []

        if snapshot.conversation_history:
            conv_text = "\n".join(f"{m['role']}: {m['content']}" for m in snapshot.conversation_history)
            parts.append(f"--- CONVERSATION HISTORY ---\n{conv_text}")

        if snapshot.vector_context:
            parts.append(snapshot.vector_context)

        # Use only KG facts above the confidence threshold
        trusted_kg = [
            f for f in snapshot.kg_facts if f.get("trust_score", 1.0) >= self._resolver.kg_confidence_threshold
        ]
        if trusted_kg:
            facts_text = "\n".join(f"{f['subject']} → {f['relation']} → {f['object']}" for f in trusted_kg[:10])
            parts.append(
                f"--- KNOWLEDGE GRAPH FACTS (confidence ≥ {self._resolver.kg_confidence_threshold}) ---\n{facts_text}"
            )

        # Append authoritative resolved facts section
        if snapshot.resolved_facts:
            resolved_lines = [
                f"{key} = {fact.value}  (source: {fact.source.name}, confidence: {fact.confidence:.2f})"
                for key, fact in sorted(snapshot.resolved_facts.items())
            ]
            parts.append("--- RESOLVED FACTS ---\n" + "\n".join(resolved_lines))

        return "\n\n".join(parts)
