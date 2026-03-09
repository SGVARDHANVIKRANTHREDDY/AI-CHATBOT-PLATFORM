"""
E2E Test: RAG Retrieval with Trust Scoring

Tests the trust evaluation pipeline for knowledge ingestion,
verifying that low-quality and untrusted content is rejected.
"""
from __future__ import annotations

import pytest

from app.knowledge_graph.trust import (
    SourceTrustEvaluator,
    TrustResult,
    VerificationStatus,
)


# ── Fixtures ──────────────────────────────────────────────────────

@pytest.fixture
def trust_evaluator():
    return SourceTrustEvaluator(trust_threshold=0.5)


@pytest.fixture
def strict_evaluator():
    return SourceTrustEvaluator(trust_threshold=0.8)


# ── Trust Evaluator Tests ────────────────────────────────────────

class TestSourceTrustEvaluator:
    """Tests for the SourceTrustEvaluator."""

    def test_trusted_domain_scores_high(self, trust_evaluator):
        """Known-good domains get high source scores."""
        result = trust_evaluator.evaluate(
            "https://docs.python.org/3/tutorial/index.html",
            "Python is a programming language. " * 20,
        )
        assert result.source_score == 1.0
        assert result.overall_score >= 0.5

    def test_unknown_domain_scores_neutral(self, trust_evaluator):
        """Unknown domains get neutral source scores."""
        result = trust_evaluator.evaluate(
            "https://example-blog.com/article",
            "This is a well-written article about technology. " * 20,
        )
        assert 0.3 <= result.source_score <= 0.7

    def test_suspicious_domain_scores_low(self, trust_evaluator):
        """Suspicious TLDs (.tk, .ml etc.) score low."""
        result = trust_evaluator.evaluate(
            "http://spam-site.tk/page",
            "Click here for free stuff. " * 10,
        )
        assert result.source_score <= 0.2

    def test_empty_content_rejected(self, trust_evaluator):
        """Empty content is rejected."""
        result = trust_evaluator.evaluate(
            "https://docs.python.org/3/",
            "",
        )
        assert result.content_quality_score == 0.0
        assert not trust_evaluator.should_ingest(result)

    def test_very_short_content_penalized(self, trust_evaluator):
        """Very short content gets lower quality scores."""
        result = trust_evaluator.evaluate(
            "https://example.com/page",
            "Short text only.",
        )
        assert result.content_quality_score < 0.5

    def test_good_content_scores_well(self, trust_evaluator):
        """Well-formed prose scores well on quality."""
        good_content = (
            "Python is a high-level programming language created by Guido van Rossum. "
            "It was first released in 1991 and has since become one of the most popular languages. "
            "The language emphasizes code readability through significant whitespace conventions. "
            "Python supports object-oriented, procedural, and functional programming paradigms. "
            "Its comprehensive standard library covers networking, file handling, and text processing. "
            "The package ecosystem includes frameworks like Django, Flask, and FastAPI for web development. "
            "Data science practitioners rely on NumPy, pandas, and scikit-learn for analysis tasks. "
            "Machine learning workflows leverage TensorFlow and PyTorch for model training. "
            "Python interpreters are available on every major operating system platform. "
            "The community maintains thorough documentation and provides extensive tutorials online. "
        )
        result = trust_evaluator.evaluate(
            "https://docs.python.org/3/",
            good_content,
        )
        assert result.content_quality_score >= 0.5
        assert result.overall_score >= 0.7

    def test_repetitive_content_penalized(self, trust_evaluator):
        """Highly repetitive content is penalized."""
        repetitive = "spam spam spam " * 100
        result = trust_evaluator.evaluate(
            "https://example.com/page",
            repetitive,
        )
        assert result.content_quality_score < 0.5

    def test_should_ingest_respects_threshold(self, trust_evaluator):
        """should_ingest correctly filters by threshold."""
        good = trust_evaluator.evaluate(
            "https://docs.python.org/3/",
            "Well-written documentation content. " * 30,
        )
        assert trust_evaluator.should_ingest(good) is True

        bad = trust_evaluator.evaluate("http://spam.tk/", "")
        assert trust_evaluator.should_ingest(bad) is False

    def test_verification_status_values(self, trust_evaluator):
        """Results have valid verification status."""
        result = trust_evaluator.evaluate(
            "https://docs.python.org/3/",
            "Quality documentation content with multiple sentences. " * 20,
        )
        assert result.verification_status in (
            VerificationStatus.VERIFIED,
            VerificationStatus.UNVERIFIED,
        )

    def test_rejected_status(self, strict_evaluator):
        """Content below strict threshold is REJECTED."""
        result = strict_evaluator.evaluate(
            "http://unknown-site.xyz/page",
            "Short content only.",
        )
        assert result.verification_status == VerificationStatus.REJECTED

    def test_edu_gov_domains_trusted(self, trust_evaluator):
        """Educational and government domains get higher trust."""
        result = trust_evaluator.evaluate(
            "https://cs.stanford.edu/research",
            "Machine learning research paper content. " * 20,
        )
        assert result.source_score >= 0.8

    def test_custom_trusted_domains(self):
        """Custom trusted domains are supported."""
        evaluator = SourceTrustEvaluator(
            custom_trusted_domains={"internal-wiki.company.com"}
        )
        result = evaluator.evaluate(
            "https://internal-wiki.company.com/page",
            "Internal knowledge base article. " * 20,
        )
        assert result.source_score == 1.0

    def test_https_bonus(self, trust_evaluator):
        """HTTPS gives a small trust bonus over HTTP."""
        https_result = trust_evaluator.evaluate(
            "https://example.com/page",
            "Content for testing. " * 20,
        )
        http_result = trust_evaluator.evaluate(
            "http://example.com/page",
            "Content for testing. " * 20,
        )
        assert https_result.source_score >= http_result.source_score

    def test_trust_result_dataclass(self, trust_evaluator):
        """TrustResult has all expected fields."""
        result = trust_evaluator.evaluate(
            "https://example.com", "Test content. " * 20
        )
        assert hasattr(result, "url")
        assert hasattr(result, "source_score")
        assert hasattr(result, "content_quality_score")
        assert hasattr(result, "verification_status")
        assert hasattr(result, "overall_score")
        assert hasattr(result, "rejection_reasons")
        assert hasattr(result, "is_trusted")
