"""
Content Safety Filter for RAG Ingestion Pipeline.

Scores ingested documents for prompt injection, source trustworthiness,
and content quality.  Documents that exceed the injection threshold are
rejected or routed to quarantine for manual review.
"""

from __future__ import annotations

import math
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from app.shared.utils import get_logger

_LOG = get_logger(__name__)

# ── Prompt-injection detection patterns ───────────────────────────
# Each tuple: (compiled regex, weight contribution)
_INJECTION_RULES: list[tuple[re.Pattern[str], float]] = [
    # Direct instruction override
    (re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.I), 0.9),
    (re.compile(r"disregard\s+(all\s+)?(prior|previous|above)\s+(instructions|rules|context)", re.I), 0.9),
    (re.compile(r"forget\s+(everything|all|prior|previous)", re.I), 0.7),
    # System prompt exfiltration
    (re.compile(r"reveal\s+(your\s+)?(system\s+prompt|instructions|rules)", re.I), 0.85),
    (re.compile(r"(show|print|output|repeat)\s+(your\s+)?(system\s+prompt|hidden\s+prompt)", re.I), 0.85),
    (re.compile(r"what\s+(is|are)\s+your\s+(system\s+prompt|instructions|rules)", re.I), 0.6),
    # Tool / code execution
    (re.compile(r"execute\s+tool", re.I), 0.8),
    (re.compile(r"run\s+(this\s+)?(code|command|script|shell)", re.I), 0.7),
    (re.compile(r"(call|invoke|use)\s+(the\s+)?tool", re.I), 0.65),
    # Role hijacking
    (re.compile(r"you\s+are\s+now\b", re.I), 0.75),
    (re.compile(r"act(ing)?\s+as\s+(a\s+)?", re.I), 0.5),
    (re.compile(r"\bDAN\b"), 0.8),
    (re.compile(r"do\s+anything\s+now", re.I), 0.85),
    (re.compile(r"jailbreak", re.I), 0.9),
    # Delimiter / formatting tricks
    (re.compile(r"\[system\]", re.I), 0.7),
    (re.compile(r"\[developer\]", re.I), 0.7),
    (re.compile(r"<\|im_start\|>", re.I), 0.8),
    (re.compile(r"###\s*(SYSTEM|INSTRUCTION)", re.I), 0.7),
    # Data exfiltration
    (re.compile(r"(send|post|exfiltrate|leak)\s+(data|info|response)\s+to", re.I), 0.85),
    (re.compile(r"base64\s+(encode|decode)", re.I), 0.4),
]

# ── Content-quality heuristics ────────────────────────────────────
_MIN_ALPHA_RATIO = 0.40  # at least 40 % alphabetic chars
_MIN_WORD_COUNT = 10  # trivially short docs are low quality
_MAX_REPEAT_RATIO = 0.60  # repeated trigram ratio ceiling
_ENTROPY_FLOOR = 2.5  # Shannon entropy floor (bits)


@dataclass(frozen=True)
class SafetyVerdict:
    """Result of scanning a single document."""

    source: str
    prompt_injection_score: float
    source_trust_score: float
    content_quality_score: float
    rejected: bool
    quarantined: bool
    reasons: tuple[str, ...]


@dataclass
class DomainReputation:
    """Tracks per-domain reputation scores over time."""

    # Domains explicitly trusted by the operator
    trusted_domains: set[str] = field(
        default_factory=lambda: {
            "docs.python.org",
            "arxiv.org",
            "github.com",
            "en.wikipedia.org",
            "redis.io",
            "fastapi.tiangolo.com",
            "learn.microsoft.com",
            "developer.mozilla.org",
            "stackoverflow.com",
        }
    )
    # Domains explicitly blocked
    blocked_domains: set[str] = field(default_factory=set)
    # Per-domain cumulative scores: domain -> (passes, flags)
    _history: dict[str, list[int]] = field(default_factory=dict)

    def score(self, domain: str) -> float:
        """Return a trust score in [0.0, 1.0] for *domain*."""
        domain = domain.lower().strip()
        if domain in self.blocked_domains:
            return 0.0
        if domain in self.trusted_domains:
            return 1.0
        passes, flags = self._history.get(domain, [0, 0])
        total = passes + flags
        if total == 0:
            return 0.5  # unknown domain → neutral
        return passes / total

    def record(self, domain: str, *, passed: bool) -> None:
        domain = domain.lower().strip()
        entry = self._history.setdefault(domain, [0, 0])
        if passed:
            entry[0] += 1
        else:
            entry[1] += 1

    def add_trusted(self, domain: str) -> None:
        self.trusted_domains.add(domain.lower().strip())

    def block(self, domain: str) -> None:
        self.blocked_domains.add(domain.lower().strip())
        self.trusted_domains.discard(domain.lower().strip())


@dataclass
class QuarantineStore:
    """In-memory quarantine for suspicious documents pending review."""

    _items: list[dict[str, Any]] = field(default_factory=list)

    def add(self, doc: dict[str, Any], verdict: SafetyVerdict) -> None:
        self._items.append(
            {
                "doc": doc,
                "verdict": {
                    "source": verdict.source,
                    "prompt_injection_score": verdict.prompt_injection_score,
                    "source_trust_score": verdict.source_trust_score,
                    "content_quality_score": verdict.content_quality_score,
                    "reasons": list(verdict.reasons),
                },
                "quarantined_at": time.time(),
            }
        )
        _LOG.warning(
            "Document quarantined: source=%s injection=%.2f reasons=%s",
            verdict.source,
            verdict.prompt_injection_score,
            verdict.reasons,
        )

    @property
    def items(self) -> list[dict[str, Any]]:
        return list(self._items)

    def release(self, index: int) -> dict[str, Any] | None:
        """Release a document from quarantine (manual review passed)."""
        if 0 <= index < len(self._items):
            return self._items.pop(index)
        return None

    def clear(self) -> int:
        n = len(self._items)
        self._items.clear()
        return n

    def __len__(self) -> int:
        return len(self._items)


class ContentSafetyFilter:
    """
    Scans documents destined for the RAG index and assigns three scores:

    * **prompt_injection_score** - likelihood the text contains prompt
      injection / jailbreak payloads  (0 = clean, 1 = malicious).
    * **source_trust_score** - reputation of the originating domain
      (0 = untrusted, 1 = fully trusted).
    * **content_quality_score** - lexical quality heuristic
      (0 = garbage, 1 = excellent).

    Documents whose injection score exceeds *injection_threshold* are
    **rejected** outright.  Documents from unknown domains with borderline
    scores are routed to the :class:`QuarantineStore`.
    """

    def __init__(
        self,
        *,
        injection_threshold: float = 0.6,
        quarantine_threshold: float = 0.35,
        quality_floor: float = 0.3,
        domain_reputation: DomainReputation | None = None,
        quarantine: QuarantineStore | None = None,
    ) -> None:
        self.injection_threshold = injection_threshold
        self.quarantine_threshold = quarantine_threshold
        self.quality_floor = quality_floor
        self.domain_reputation = domain_reputation or DomainReputation()
        self.quarantine = quarantine or QuarantineStore()

    # ── public API ────────────────────────────────────────────────

    def scan(self, doc: dict[str, Any]) -> SafetyVerdict:
        """Score a single document dict (expects ``text``, ``source``)."""
        text: str = doc.get("text", "")
        source: str = doc.get("source", "")
        domain: str = doc.get("domain", "") or self._extract_domain(source)

        injection = self._score_injection(text)
        trust = self.domain_reputation.score(domain)
        quality = self._score_quality(text)

        reasons: list[str] = []
        rejected = False
        quarantined = False

        if injection >= self.injection_threshold:
            reasons.append(f"injection_score {injection:.2f} >= {self.injection_threshold}")
            rejected = True
        if trust == 0.0:
            reasons.append(f"domain blocked: {domain}")
            rejected = True
        if quality < self.quality_floor:
            reasons.append(f"quality_score {quality:.2f} < {self.quality_floor}")

        # Borderline docs → quarantine instead of hard reject
        if not rejected and injection >= self.quarantine_threshold:
            reasons.append(f"injection_score {injection:.2f} >= quarantine threshold {self.quarantine_threshold}")
            quarantined = True

        verdict = SafetyVerdict(
            source=source,
            prompt_injection_score=injection,
            source_trust_score=trust,
            content_quality_score=quality,
            rejected=rejected,
            quarantined=quarantined,
            reasons=tuple(reasons),
        )

        # Side-effects
        self.domain_reputation.record(domain, passed=not rejected)
        if quarantined:
            self.quarantine.add(doc, verdict)
        if rejected:
            _LOG.warning(
                "Document REJECTED: %s (injection=%.2f trust=%.2f quality=%.2f)", source, injection, trust, quality
            )

        return verdict

    def scan_batch(self, docs: list[dict[str, Any]]) -> list[SafetyVerdict]:
        """Scan multiple documents, return verdicts list-aligned with input."""
        return [self.scan(d) for d in docs]

    def filter_safe(self, docs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Return only docs that are neither rejected nor quarantined."""
        safe: list[dict[str, Any]] = []
        for doc in docs:
            v = self.scan(doc)
            if not v.rejected and not v.quarantined:
                safe.append(doc)
        return safe

    # ── scoring internals ─────────────────────────────────────────

    def _score_injection(self, text: str) -> float:
        """Aggregate injection score from weighted pattern matches."""
        if not text:
            return 0.0
        total = 0.0
        for pattern, weight in _INJECTION_RULES:
            matches = pattern.findall(text)
            if matches:
                total += weight * min(len(matches), 3)  # cap repeated hits
        return min(total, 1.0)

    @staticmethod
    def _score_quality(text: str) -> float:
        """Heuristic content-quality in [0, 1]."""
        if not text or not text.strip():
            return 0.0

        signals: list[float] = []

        # 1. Alpha ratio
        alpha = sum(c.isalpha() for c in text)
        ratio = alpha / max(len(text), 1)
        signals.append(min(ratio / _MIN_ALPHA_RATIO, 1.0))

        # 2. Word count
        words = text.split()
        wc = len(words)
        signals.append(min(wc / _MIN_WORD_COUNT, 1.0))

        # 3. Trigram repetition ratio (lower is better)
        if wc >= 6:
            trigrams = [" ".join(words[i : i + 3]) for i in range(len(words) - 2)]
            unique = len(set(trigrams))
            repeat_ratio = 1 - (unique / max(len(trigrams), 1))
            signals.append(max(1 - repeat_ratio / _MAX_REPEAT_RATIO, 0.0))
        else:
            signals.append(0.5)

        # 4. Shannon entropy
        freq: dict[str, int] = {}
        for ch in text.lower():
            freq[ch] = freq.get(ch, 0) + 1
        total_chars = len(text)
        entropy = -sum((c / total_chars) * math.log2(c / total_chars) for c in freq.values())
        signals.append(min(entropy / _ENTROPY_FLOOR, 1.0))

        return sum(signals) / len(signals)

    @staticmethod
    def _extract_domain(source: str) -> str:
        """Best-effort domain extraction from source string or URL."""
        if "://" in source:
            try:
                return urlparse(source).netloc.lower()
            except Exception:  # noqa: S110
                pass  # best-effort domain extraction
        if "/" in source:
            parts = source.split("/")
            if len(parts) >= 3:
                return parts[2].lower()
        return source.lower()
