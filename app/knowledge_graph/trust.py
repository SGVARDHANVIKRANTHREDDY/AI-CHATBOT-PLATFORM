"""
Trust Scoring — Source and content quality evaluation for knowledge ingestion.

Evaluates documents before they enter the RAG pipeline, preventing
low-quality, adversarial, or unverifiable content from polluting
the knowledge base.

Scoring dimensions:
    source_score         - Domain reputation heuristic (0.0-1.0)
    content_quality_score - Text quality via structural heuristics (0.0-1.0)
    verification_status   - UNVERIFIED | VERIFIED | REJECTED

Design rationale:
    The knowledge crawler ingests anything it fetches.  Without trust
    scoring, hallucinated, outdated, or adversarial content enters the
    RAG store and poisons agent reasoning.  This evaluator sits between
    crawling and embedding.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class VerificationStatus(Enum):
    """Trust verification state of a document."""

    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    REJECTED = "rejected"


@dataclass
class TrustResult:
    """Trust evaluation result for a single document."""

    url: str
    source_score: float  # 0.0-1.0
    content_quality_score: float  # 0.0-1.0
    verification_status: VerificationStatus
    overall_score: float  # Weighted composite
    rejection_reasons: list[str]

    @property
    def is_trusted(self) -> bool:
        return self.verification_status != VerificationStatus.REJECTED


# ─── Domain reputation lists ─────────────────────────────────────
HIGH_TRUST_DOMAINS: set[str] = {
    "docs.python.org",
    "redis.io",
    "fastapi.tiangolo.com",
    "arxiv.org",
    "github.com",
    "docs.microsoft.com",
    "learn.microsoft.com",
    "cloud.google.com",
    "aws.amazon.com",
    "developer.mozilla.org",
    "stackoverflow.com",
    "wikipedia.org",
    "pytorch.org",
    "tensorflow.org",
    "huggingface.co",
    "openai.com",
    "docs.docker.com",
    "kubernetes.io",
}

LOW_TRUST_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?i)(spam|phishing|malware|clickbait)"),
    re.compile(r"(?i)\.(tk|ml|ga|cf|gq)$"),  # Free TLDs often abused
]


class SourceTrustEvaluator:
    """Evaluates trustworthiness of documents for knowledge ingestion.

    Args:
        trust_threshold: Minimum overall score for ingestion (0.0-1.0).
        source_weight: Weight of source_score in overall calculation.
        quality_weight: Weight of content_quality_score in overall calculation.
        custom_trusted_domains: Additional domains to trust.
    """

    def __init__(
        self,
        trust_threshold: float = 0.5,
        source_weight: float = 0.4,
        quality_weight: float = 0.6,
        custom_trusted_domains: set[str] | None = None,
    ) -> None:
        self.trust_threshold = trust_threshold
        self.source_weight = source_weight
        self.quality_weight = quality_weight
        self.trusted_domains = HIGH_TRUST_DOMAINS.copy()
        if custom_trusted_domains:
            self.trusted_domains.update(custom_trusted_domains)

    def evaluate(self, url: str, content: str) -> TrustResult:
        """Evaluate a document's trustworthiness.

        Args:
            url: Source URL of the document.
            content: Full text content of the document.

        Returns:
            TrustResult with all scores and verification status.
        """
        reasons: list[str] = []

        source_score = self._evaluate_source(url, reasons)
        quality_score = self._evaluate_content_quality(content, reasons)
        overall = source_score * self.source_weight + quality_score * self.quality_weight

        if overall >= self.trust_threshold and not reasons:
            status = VerificationStatus.VERIFIED
        elif overall < self.trust_threshold:
            status = VerificationStatus.REJECTED
            if not reasons:
                reasons.append(f"Overall score {overall:.2f} below threshold {self.trust_threshold}")
        else:
            status = VerificationStatus.UNVERIFIED

        result = TrustResult(
            url=url,
            source_score=source_score,
            content_quality_score=quality_score,
            verification_status=status,
            overall_score=overall,
            rejection_reasons=reasons,
        )

        _LOG.info(
            "Trust evaluation for %s: source=%.2f quality=%.2f overall=%.2f status=%s",
            url,
            source_score,
            quality_score,
            overall,
            status.value,
        )

        return result

    def should_ingest(self, result: TrustResult) -> bool:
        """Determine whether a document should be ingested.

        Args:
            result: TrustResult from evaluate().

        Returns:
            True if the document passes the trust threshold.
        """
        return (
            result.verification_status != VerificationStatus.REJECTED and result.overall_score >= self.trust_threshold
        )

    # ── Private scoring methods ───────────────────────────────────

    def _evaluate_source(self, url: str, reasons: list[str]) -> float:
        """Score the source URL based on domain reputation."""
        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()
            if not domain:
                reasons.append("Could not parse domain from URL")
                return 0.2

            # Check against known-good domains
            for trusted in self.trusted_domains:
                if domain == trusted or domain.endswith(f".{trusted}"):
                    return 1.0

            # Check against low-trust patterns
            for pattern in LOW_TRUST_PATTERNS:
                if pattern.search(domain):
                    reasons.append(f"Domain matches low-trust pattern: {domain}")
                    return 0.1

            # HTTPS gives a small bonus
            scheme_bonus = 0.1 if parsed.scheme == "https" else 0.0

            # Known TLDs get moderate trust
            if domain.endswith((".edu", ".gov", ".org")):
                return 0.8 + scheme_bonus

            # Default: neutral trust
            return 0.5 + scheme_bonus

        except Exception as e:
            _LOG.warning("Source evaluation error for %s: %s", url, e)
            reasons.append(f"Source evaluation error: {e}")
            return 0.3

    def _evaluate_content_quality(self, content: str, reasons: list[str]) -> float:
        """Score content quality based on structural heuristics."""
        if not content or not content.strip():
            reasons.append("Empty content")
            return 0.0

        score = 0.5  # Baseline
        text = content.strip()
        word_count = len(text.split())

        # Length checks
        if word_count < 20:
            reasons.append(f"Content too short: {word_count} words")
            score -= 0.3
        elif word_count > 100:
            score += 0.1
        if word_count > 500:
            score += 0.1

        # Sentence structure (presence of periods indicates proper prose)
        sentence_count = len(re.findall(r"[.!?]+", text))
        if sentence_count == 0 and word_count > 20:
            reasons.append("No sentence structure detected")
            score -= 0.2
        elif sentence_count >= 3:
            score += 0.1

        # Excessive special characters (possible garbage/encoding issues)
        special_ratio = len(re.findall(r"[^\w\s.,;:!?'\"-]", text)) / max(len(text), 1)
        if special_ratio > 0.3:
            reasons.append(f"High special character ratio: {special_ratio:.2f}")
            score -= 0.3

        # Repetition detection (same phrase repeated many times)
        words = text.lower().split()
        if len(words) > 10:
            unique_ratio = len(set(words)) / len(words)
            if unique_ratio < 0.3:
                reasons.append(f"High word repetition: unique ratio {unique_ratio:.2f}")
                score -= 0.3

        return max(0.0, min(1.0, score))
