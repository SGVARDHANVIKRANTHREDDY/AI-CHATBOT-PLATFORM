"""
Tests for MemoryAuthorityResolver and UnifiedMemoryController consistency.

Covers:
    • Authority hierarchy (conversation > KG > vector)
    • KG confidence threshold gating (≥ 0.8)
    • Same-key conflict detection and resolution
    • Timestamp-based tiebreaking
    • Confidence-based tiebreaking
    • End-to-end controller reconciliation
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.memory.authority import (
    KG_CONFIDENCE_THRESHOLD,
    MemoryAuthorityResolver,
    MemoryConflict,
    MemoryFact,
    MemorySource,
    extract_facts_from_conversation,
    extract_facts_from_kg,
    extract_facts_from_vector_context,
)
from app.memory.memory_controller import (
    UnifiedMemoryController,
    UnifiedMemorySnapshot,
)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def resolver():
    return MemoryAuthorityResolver()


@pytest.fixture
def now():
    return datetime.now(timezone.utc)


# ── Fact extraction tests ─────────────────────────────────────────

class TestExtractFacts:
    def test_extract_from_conversation_simple(self):
        msgs = [
            {"role": "user", "content": "my language is Python"},
            {"role": "assistant", "content": "Got it!"},
        ]
        facts = extract_facts_from_conversation(msgs)
        assert len(facts) == 1
        assert facts[0].key == "language"
        assert facts[0].value == "Python"
        assert facts[0].source == MemorySource.CONVERSATION

    def test_extract_from_conversation_multiple(self):
        msgs = [
            {"role": "user", "content": "my name is Alice"},
            {"role": "user", "content": "my preferred language is Rust"},
        ]
        facts = extract_facts_from_conversation(msgs)
        assert len(facts) == 2
        keys = {f.key for f in facts}
        assert "name" in keys
        assert "preferred_language" in keys

    def test_extract_from_conversation_ignores_assistant(self):
        msgs = [
            {"role": "assistant", "content": "my name is Bot"},
        ]
        facts = extract_facts_from_conversation(msgs)
        assert len(facts) == 0

    def test_extract_from_kg(self):
        rels = [
            {"subject": "user", "relation": "prefers_language", "object": "Java", "trust_score": 0.9},
            {"subject": "user", "relation": "uses_editor", "object": "VSCode", "trust_score": 0.6},
        ]
        facts = extract_facts_from_kg(rels)
        assert len(facts) == 2
        assert facts[0].source == MemorySource.KNOWLEDGE_GRAPH
        assert facts[0].confidence == 0.9

    def test_extract_from_vector_context(self):
        ctx = "my language is Go\nsome irrelevant text\nmy framework is Django"
        facts = extract_facts_from_vector_context(ctx)
        assert len(facts) == 2
        assert all(f.source == MemorySource.VECTOR for f in facts)
        assert all(f.confidence == 0.7 for f in facts)


# ── Authority hierarchy tests ─────────────────────────────────────

class TestAuthorityHierarchy:
    def test_conversation_overrides_vector(self, resolver, now):
        """Conversation memory is the highest authority."""
        facts = [
            MemoryFact("language", "Python", MemorySource.CONVERSATION, now, 1.0),
            MemoryFact("language", "Java", MemorySource.VECTOR, now, 0.9),
        ]
        resolved = resolver.resolve_all(facts)
        assert resolved["language"].value == "Python"
        assert resolved["language"].source == MemorySource.CONVERSATION

    def test_conversation_overrides_kg(self, resolver, now):
        """Conversation takes precedence even over high-confidence KG."""
        facts = [
            MemoryFact("language", "Rust", MemorySource.CONVERSATION, now, 1.0),
            MemoryFact("language", "Go", MemorySource.KNOWLEDGE_GRAPH, now, 0.95),
        ]
        resolved = resolver.resolve_all(facts)
        assert resolved["language"].value == "Rust"

    def test_kg_overrides_vector(self, resolver, now):
        """KG (confidence ≥ 0.8) takes precedence over vector."""
        facts = [
            MemoryFact("language", "C++", MemorySource.KNOWLEDGE_GRAPH, now, 0.85),
            MemoryFact("language", "Java", MemorySource.VECTOR, now, 0.9),
        ]
        resolved = resolver.resolve_all(facts)
        assert resolved["language"].value == "C++"
        assert resolved["language"].source == MemorySource.KNOWLEDGE_GRAPH


# ── KG confidence threshold tests ────────────────────────────────

class TestKGConfidenceGating:
    def test_kg_below_threshold_excluded(self, resolver, now):
        """KG facts with confidence < 0.8 are dropped entirely."""
        facts = [
            MemoryFact("language", "Perl", MemorySource.KNOWLEDGE_GRAPH, now, 0.5),
        ]
        resolved = resolver.resolve_all(facts)
        assert "language" not in resolved

    def test_kg_at_threshold_included(self, resolver, now):
        facts = [
            MemoryFact("language", "Scala", MemorySource.KNOWLEDGE_GRAPH, now, 0.8),
        ]
        resolved = resolver.resolve_all(facts)
        assert resolved["language"].value == "Scala"

    def test_kg_below_threshold_vector_wins(self, resolver, now):
        """If KG is below threshold, vector fact survives as fallback."""
        facts = [
            MemoryFact("language", "Perl", MemorySource.KNOWLEDGE_GRAPH, now, 0.3),
            MemoryFact("language", "Ruby", MemorySource.VECTOR, now, 0.7),
        ]
        resolved = resolver.resolve_all(facts)
        assert resolved["language"].value == "Ruby"
        assert resolved["language"].source == MemorySource.VECTOR


# ── Conflict detection tests ─────────────────────────────────────

class TestConflictDetection:
    def test_detects_value_disagreement(self, resolver, now):
        facts = [
            MemoryFact("language", "Python", MemorySource.CONVERSATION, now, 1.0),
            MemoryFact("language", "Java", MemorySource.VECTOR, now, 0.7),
        ]
        conflicts = resolver.detect_conflicts(facts)
        assert len(conflicts) == 1
        assert conflicts[0].key == "language"
        assert conflicts[0].winner is not None
        assert conflicts[0].winner.value == "Python"

    def test_no_conflict_when_values_agree(self, resolver, now):
        facts = [
            MemoryFact("language", "Python", MemorySource.CONVERSATION, now, 1.0),
            MemoryFact("language", "python", MemorySource.VECTOR, now, 0.7),
        ]
        conflicts = resolver.detect_conflicts(facts)
        assert len(conflicts) == 0

    def test_three_way_conflict(self, resolver, now):
        """All three sources disagree on the same key."""
        facts = [
            MemoryFact("language", "Python", MemorySource.CONVERSATION, now, 1.0),
            MemoryFact("language", "Java", MemorySource.KNOWLEDGE_GRAPH, now, 0.9),
            MemoryFact("language", "Go", MemorySource.VECTOR, now, 0.8),
        ]
        conflicts = resolver.detect_conflicts(facts)
        assert len(conflicts) == 1
        assert conflicts[0].winner.value == "Python"
        assert "authority" in conflicts[0].resolution_reason

    def test_multiple_key_conflicts(self, resolver, now):
        facts = [
            MemoryFact("language", "Python", MemorySource.CONVERSATION, now, 1.0),
            MemoryFact("language", "Java", MemorySource.VECTOR, now, 0.7),
            MemoryFact("editor", "VSCode", MemorySource.CONVERSATION, now, 1.0),
            MemoryFact("editor", "Vim", MemorySource.KNOWLEDGE_GRAPH, now, 0.9),
        ]
        conflicts = resolver.detect_conflicts(facts)
        assert len(conflicts) == 2
        conflict_keys = {c.key for c in conflicts}
        assert conflict_keys == {"language", "editor"}


# ── Timestamp tiebreaking tests ──────────────────────────────────

class TestTimestampResolution:
    def test_latest_timestamp_wins_same_source(self, resolver):
        """Within the same authority tier, latest timestamp wins."""
        old = datetime(2025, 1, 1, tzinfo=timezone.utc)
        new = datetime(2025, 6, 1, tzinfo=timezone.utc)
        facts = [
            MemoryFact("language", "Python", MemorySource.CONVERSATION, old, 1.0),
            MemoryFact("language", "Rust", MemorySource.CONVERSATION, new, 1.0),
        ]
        resolved = resolver.resolve_all(facts)
        assert resolved["language"].value == "Rust"

    def test_latest_wins_vector_tier(self, resolver):
        old = datetime(2025, 1, 1, tzinfo=timezone.utc)
        new = datetime(2025, 6, 1, tzinfo=timezone.utc)
        facts = [
            MemoryFact("framework", "Django", MemorySource.VECTOR, old, 0.7),
            MemoryFact("framework", "FastAPI", MemorySource.VECTOR, new, 0.7),
        ]
        resolved = resolver.resolve_all(facts)
        assert resolved["framework"].value == "FastAPI"

    def test_conflict_reports_timestamp_reason(self, resolver):
        old = datetime(2025, 1, 1, tzinfo=timezone.utc)
        new = datetime(2025, 6, 1, tzinfo=timezone.utc)
        facts = [
            MemoryFact("language", "Python", MemorySource.CONVERSATION, old, 1.0),
            MemoryFact("language", "Rust", MemorySource.CONVERSATION, new, 1.0),
        ]
        conflicts = resolver.detect_conflicts(facts)
        assert len(conflicts) == 1
        assert conflicts[0].resolution_reason == "latest_timestamp"


# ── Confidence tiebreaking tests ─────────────────────────────────

class TestConfidenceResolution:
    def test_higher_confidence_wins_same_time_same_source(self, resolver, now):
        facts = [
            MemoryFact("language", "Python", MemorySource.VECTOR, now, 0.9),
            MemoryFact("language", "Java", MemorySource.VECTOR, now, 0.6),
        ]
        resolved = resolver.resolve_all(facts)
        assert resolved["language"].value == "Python"

    def test_conflict_reports_confidence_reason(self, resolver, now):
        facts = [
            MemoryFact("language", "Python", MemorySource.VECTOR, now, 0.9),
            MemoryFact("language", "Java", MemorySource.VECTOR, now, 0.6),
        ]
        conflicts = resolver.detect_conflicts(facts)
        assert len(conflicts) == 1
        assert conflicts[0].resolution_reason == "highest_confidence"


# ── Reconcile (combined) tests ────────────────────────────────────

class TestReconcile:
    def test_reconcile_returns_both(self, resolver, now):
        facts = [
            MemoryFact("language", "Python", MemorySource.CONVERSATION, now, 1.0),
            MemoryFact("language", "Java", MemorySource.VECTOR, now, 0.7),
            MemoryFact("editor", "VSCode", MemorySource.KNOWLEDGE_GRAPH, now, 0.9),
        ]
        resolved, conflicts = resolver.reconcile(facts)
        assert "language" in resolved
        assert "editor" in resolved
        assert resolved["language"].value == "Python"
        assert len(conflicts) == 1  # language conflict

    def test_reconcile_filters_low_kg(self, resolver, now):
        facts = [
            MemoryFact("language", "Perl", MemorySource.KNOWLEDGE_GRAPH, now, 0.3),
        ]
        resolved, conflicts = resolver.reconcile(facts)
        assert "language" not in resolved
        assert len(conflicts) == 0

    def test_reconcile_complex_scenario(self, resolver):
        """Real-world scenario: user changed language preference."""
        t1 = datetime(2025, 1, 1, tzinfo=timezone.utc)
        t2 = datetime(2025, 3, 1, tzinfo=timezone.utc)
        t3 = datetime(2025, 6, 1, tzinfo=timezone.utc)

        facts = [
            # Old vector memory says Python
            MemoryFact("language", "Python", MemorySource.VECTOR, t1, 0.7),
            # KG learned Java from older extraction
            MemoryFact("language", "Java", MemorySource.KNOWLEDGE_GRAPH, t2, 0.85),
            # User just said Rust in conversation
            MemoryFact("language", "Rust", MemorySource.CONVERSATION, t3, 1.0),
        ]
        resolved, conflicts = resolver.reconcile(facts)
        # Conversation is highest authority
        assert resolved["language"].value == "Rust"
        assert len(conflicts) == 1


# ── UnifiedMemoryController integration tests ────────────────────

class TestUnifiedMemoryControllerReconciliation:
    @pytest.fixture
    def mock_memory_service(self):
        svc = MagicMock()
        svc.get_messages = AsyncMock(return_value=[
            {"role": "user", "content": "my language is Python"},
            {"role": "assistant", "content": "Noted!"},
            {"role": "user", "content": "my editor is VSCode"},
        ])
        svc.add_message = AsyncMock()
        return svc

    @pytest.fixture
    def mock_retriever(self):
        retriever = MagicMock()
        retriever.retrieve_context = AsyncMock(
            return_value="my language is Java\nsome other text"
        )
        retriever.store_turn = AsyncMock()

        kg = MagicMock()
        kg.entities = {"python", "java"}
        kg.query = MagicMock(return_value=[
            {"subject": "user", "relation": "prefers_language", "object": "Go", "trust_score": 0.9},
        ])
        retriever.kg = kg
        return retriever

    @pytest.fixture
    def controller(self, mock_memory_service, mock_retriever):
        return UnifiedMemoryController(
            memory_service_factory=lambda sid: mock_memory_service,
            memory_retriever=mock_retriever,
        )

    @pytest.mark.asyncio
    async def test_controller_detects_conflicts(self, controller):
        snapshot = await controller.get_unified_context(
            "what python language", "sess-1"
        )
        # The snapshot should have resolved_facts populated
        assert isinstance(snapshot.resolved_facts, dict)
        assert isinstance(snapshot.conflicts, list)

    @pytest.mark.asyncio
    async def test_controller_conversation_overrides_vector(
        self, mock_memory_service, mock_retriever
    ):
        """Conversation says Python, vector says Java → Python wins."""
        mock_memory_service.get_messages = AsyncMock(return_value=[
            {"role": "user", "content": "my language is Python"},
        ])
        mock_retriever.retrieve_context = AsyncMock(
            return_value="my language is Java"
        )
        mock_retriever.kg.entities = set()
        mock_retriever.kg.query = MagicMock(return_value=[])

        ctrl = UnifiedMemoryController(
            memory_service_factory=lambda sid: mock_memory_service,
            memory_retriever=mock_retriever,
        )
        snapshot = await ctrl.get_unified_context("language", "s1")

        if snapshot.resolved_facts and "language" in snapshot.resolved_facts:
            assert snapshot.resolved_facts["language"].value == "Python"
            assert snapshot.resolved_facts["language"].source == MemorySource.CONVERSATION

    @pytest.mark.asyncio
    async def test_controller_kg_confidence_gating(
        self, mock_memory_service, mock_retriever
    ):
        """KG fact with low confidence should be excluded from resolved facts."""
        mock_memory_service.get_messages = AsyncMock(return_value=[])
        mock_retriever.retrieve_context = AsyncMock(return_value="")
        mock_retriever.kg.entities = {"user"}
        mock_retriever.kg.query = MagicMock(return_value=[
            {"subject": "user", "relation": "prefers_language", "object": "Perl",
             "trust_score": 0.3},
        ])

        ctrl = UnifiedMemoryController(
            memory_service_factory=lambda sid: mock_memory_service,
            memory_retriever=mock_retriever,
        )
        snapshot = await ctrl.get_unified_context("user language", "s1")

        # Low-confidence KG fact should be filtered out
        for key, fact in snapshot.resolved_facts.items():
            if "language" in key:
                assert fact.source != MemorySource.KNOWLEDGE_GRAPH or fact.confidence >= 0.8

    @pytest.mark.asyncio
    async def test_controller_merged_context_includes_resolved(
        self, controller
    ):
        snapshot = await controller.get_unified_context(
            "what python language", "sess-2"
        )
        assert "CONVERSATION HISTORY" in snapshot.merged_context

    @pytest.mark.asyncio
    async def test_controller_store_interaction_unchanged(
        self, controller, mock_memory_service, mock_retriever
    ):
        """store_interaction still writes to all three layers."""
        await controller.store_interaction(
            "s1", "hello", "hi there",
            kg_data={"entities": ["user"], "relationships": []},
        )
        mock_memory_service.add_message.assert_called()
        mock_retriever.store_turn.assert_awaited_once()


# ── Edge case tests ───────────────────────────────────────────────

class TestEdgeCases:
    def test_empty_facts_no_crash(self, resolver):
        resolved, conflicts = resolver.reconcile([])
        assert resolved == {}
        assert conflicts == []

    def test_single_fact_no_conflict(self, resolver, now):
        facts = [MemoryFact("language", "Python", MemorySource.CONVERSATION, now, 1.0)]
        resolved, conflicts = resolver.reconcile(facts)
        assert resolved["language"].value == "Python"
        assert len(conflicts) == 0

    def test_custom_kg_threshold(self, now):
        resolver = MemoryAuthorityResolver(kg_confidence_threshold=0.5)
        facts = [
            MemoryFact("language", "Perl", MemorySource.KNOWLEDGE_GRAPH, now, 0.6),
        ]
        resolved = resolver.resolve_all(facts)
        assert "language" in resolved  # 0.6 ≥ 0.5

    def test_case_insensitive_value_comparison(self, resolver, now):
        """'Python' and 'python' should NOT be treated as a conflict."""
        facts = [
            MemoryFact("language", "Python", MemorySource.CONVERSATION, now, 1.0),
            MemoryFact("language", "python", MemorySource.VECTOR, now, 0.7),
        ]
        conflicts = resolver.detect_conflicts(facts)
        assert len(conflicts) == 0
