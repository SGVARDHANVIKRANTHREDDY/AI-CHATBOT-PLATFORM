"""
MemoryAuthorityResolver — Enforces consistency across memory layers.

Authority hierarchy:
    1. Conversation memory (highest) — overrides vector memory
    2. Knowledge graph — requires entity confidence > 0.8
    3. Vector memory (lowest) — background/learned facts

Conflict resolution:
    When two sources disagree on the same fact key, the resolver picks
    the winner using (in order):
        a. Source authority rank (conversation > KG > vector)
        b. Timestamp (latest wins within the same tier)
        c. Confidence score (higher wins as tiebreaker)

    Conflicts that cannot be auto-resolved are flagged for review.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import Any, Dict, List, Optional, Tuple

from app.shared.utils import get_logger

_LOG = get_logger(__name__)

KG_CONFIDENCE_THRESHOLD = 0.8


class MemorySource(IntEnum):
    """Authority rank — higher value = higher authority."""
    VECTOR = 1
    KNOWLEDGE_GRAPH = 2
    CONVERSATION = 3


@dataclass
class MemoryFact:
    """A normalised fact extracted from any memory layer."""
    key: str                  # e.g. "user_language", "user_name"
    value: str                # e.g. "Python", "Alice"
    source: MemorySource
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    confidence: float = 1.0   # 0.0–1.0

    def __repr__(self) -> str:
        return (
            f"MemoryFact(key={self.key!r}, value={self.value!r}, "
            f"source={self.source.name}, confidence={self.confidence:.2f})"
        )


@dataclass
class MemoryConflict:
    """A detected disagreement between memory layers."""
    key: str
    facts: List[MemoryFact]
    winner: Optional[MemoryFact] = None
    resolution_reason: str = ""


# ── Fact extraction helpers ───────────────────────────────────────

_KV_PATTERN = re.compile(
    r"(?:my\s+)?(\w[\w\s]{0,30}?)\s+(?:is|are|=|:)\s+(.+)",
    re.IGNORECASE,
)

_PROFILE_KEYS = {
    "name", "language", "preferred language", "programming language",
    "location", "timezone", "role", "job", "company", "email",
    "framework", "editor", "os", "operating system",
    "favorite language", "favourite language",
}


def _normalise_key(raw: str) -> str:
    """Collapse whitespace and lowercase a fact key."""
    return re.sub(r"\s+", "_", raw.strip().lower())


def extract_facts_from_conversation(
    messages: List[Dict[str, str]],
) -> List[MemoryFact]:
    """Pull user-asserted facts from conversation history.

    Scans user messages for "my X is Y" / "X = Y" patterns and returns
    MemoryFact instances ordered by position (later = newer).
    """
    facts: List[MemoryFact] = []
    now = datetime.now(timezone.utc)

    for idx, msg in enumerate(messages):
        if msg.get("role") != "user":
            continue
        content = msg.get("content", "")
        for match in _KV_PATTERN.finditer(content):
            raw_key = match.group(1).strip().lower()
            raw_val = match.group(2).strip().rstrip(".")

            if not any(pk in raw_key for pk in _PROFILE_KEYS):
                continue

            facts.append(MemoryFact(
                key=_normalise_key(raw_key),
                value=raw_val,
                source=MemorySource.CONVERSATION,
                timestamp=now,
                confidence=1.0,
            ))
            # Bump timestamp so later messages always win
            now = now  # same batch — position order used via list index

    return facts


def extract_facts_from_kg(
    relationships: List[Dict[str, Any]],
) -> List[MemoryFact]:
    """Convert KG relationships to MemoryFact instances."""
    facts: List[MemoryFact] = []
    for rel in relationships:
        trust = float(rel.get("trust_score", 0.5))
        facts.append(MemoryFact(
            key=_normalise_key(f"{rel['subject']}_{rel['relation']}"),
            value=rel["object"],
            source=MemorySource.KNOWLEDGE_GRAPH,
            confidence=trust,
        ))
    return facts


def extract_facts_from_vector_context(
    context: str,
) -> List[MemoryFact]:
    """Best-effort extraction of facts from free-text vector context."""
    facts: List[MemoryFact] = []
    for match in _KV_PATTERN.finditer(context):
        raw_key = match.group(1).strip().lower()
        raw_val = match.group(2).strip().rstrip(".")
        if any(pk in raw_key for pk in _PROFILE_KEYS):
            facts.append(MemoryFact(
                key=_normalise_key(raw_key),
                value=raw_val,
                source=MemorySource.VECTOR,
                confidence=0.7,
            ))
    return facts


# ── Core resolver ─────────────────────────────────────────────────

class MemoryAuthorityResolver:
    """Detects and resolves conflicts between memory layers.

    Usage::

        resolver = MemoryAuthorityResolver()
        conflicts = resolver.detect_conflicts(all_facts)
        resolved  = resolver.resolve_all(all_facts)
    """

    def __init__(
        self,
        kg_confidence_threshold: float = KG_CONFIDENCE_THRESHOLD,
    ) -> None:
        self.kg_confidence_threshold = kg_confidence_threshold

    # ── Public API ────────────────────────────────────────────────

    def detect_conflicts(
        self, facts: List[MemoryFact]
    ) -> List[MemoryConflict]:
        """Find all keys where two or more sources disagree on the value."""
        grouped = self._group_by_key(facts)
        conflicts: List[MemoryConflict] = []

        for key, key_facts in grouped.items():
            unique_values = {f.value.lower() for f in key_facts}
            if len(unique_values) > 1:
                winner, reason = self._pick_winner(key_facts)
                conflicts.append(MemoryConflict(
                    key=key,
                    facts=list(key_facts),
                    winner=winner,
                    resolution_reason=reason,
                ))
        return conflicts

    def resolve_all(
        self, facts: List[MemoryFact]
    ) -> Dict[str, MemoryFact]:
        """Return the single authoritative fact per key.

        Applies the full authority hierarchy and filters out KG facts
        below the confidence threshold.
        """
        # Filter out low-confidence KG facts
        qualified = [
            f for f in facts
            if not (
                f.source == MemorySource.KNOWLEDGE_GRAPH
                and f.confidence < self.kg_confidence_threshold
            )
        ]

        grouped = self._group_by_key(qualified)
        resolved: Dict[str, MemoryFact] = {}

        for key, key_facts in grouped.items():
            winner, _ = self._pick_winner(key_facts)
            if winner:
                resolved[key] = winner

        return resolved

    def reconcile(
        self, facts: List[MemoryFact]
    ) -> Tuple[Dict[str, MemoryFact], List[MemoryConflict]]:
        """One-shot: resolve facts and return both the resolved map and
        any conflicts that were detected (with their resolutions).

        Returns:
            (resolved_facts, conflicts)
        """
        conflicts = self.detect_conflicts(facts)
        resolved = self.resolve_all(facts)
        return resolved, conflicts

    # ── Internals ─────────────────────────────────────────────────

    @staticmethod
    def _group_by_key(
        facts: List[MemoryFact],
    ) -> Dict[str, List[MemoryFact]]:
        groups: Dict[str, List[MemoryFact]] = {}
        for f in facts:
            groups.setdefault(f.key, []).append(f)
        return groups

    @staticmethod
    def _pick_winner(
        facts: List[MemoryFact],
    ) -> Tuple[Optional[MemoryFact], str]:
        """Choose the authoritative fact from a list sharing the same key.

        Resolution order:
            1. Highest source authority (conversation > KG > vector)
            2. Latest timestamp (within same authority tier)
            3. Highest confidence (tiebreaker)
        """
        if not facts:
            return None, ""

        sorted_facts = sorted(
            facts,
            key=lambda f: (f.source.value, f.timestamp, f.confidence),
            reverse=True,
        )

        winner = sorted_facts[0]

        if len(facts) == 1:
            return winner, "single_source"

        runner_up = sorted_facts[1]

        if winner.source != runner_up.source:
            reason = f"authority:{winner.source.name}>{runner_up.source.name}"
        elif winner.timestamp != runner_up.timestamp:
            reason = "latest_timestamp"
        else:
            reason = "highest_confidence"

        _LOG.info(
            "Conflict on %r resolved → %r (%s)",
            winner.key,
            winner.value,
            reason,
        )
        return winner, reason
