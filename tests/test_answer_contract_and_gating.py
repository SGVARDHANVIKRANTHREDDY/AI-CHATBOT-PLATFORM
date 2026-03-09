"""Unit tests for RAG gating logic and answer contract shape.

These tests exercise decide_refusal() (the pre-LLM gating gate) and the
AnswerContract Pydantic model, both of which define the public contract
for every response produced by the orchestrator.
"""

from __future__ import annotations

import pytest
from app.security.refusal_guard import decide_refusal
from app.shared.types import AnswerContract, Citation
from pydantic import ValidationError

# -- RAG gating -----------------------------------------------------------


def test_rag_gating_refuses_low_score():
    """A RAG score below 0.35 must produce a refusal decision."""
    decision = decide_refusal(use_rag=True, top_rag_score=0.2)
    assert decision is not None
    assert decision.refuse is True
    assert decision.message  # non-empty


def test_rag_gating_refuses_none_score():
    """Missing RAG score (no documents retrieved) must also refuse."""
    decision = decide_refusal(use_rag=True, top_rag_score=None)
    assert decision is not None
    assert decision.refuse is True


def test_rag_gating_passes_above_threshold():
    """Scores at or above the 0.35 threshold must NOT produce a refusal."""
    assert decide_refusal(use_rag=True, top_rag_score=0.35) is None
    assert decide_refusal(use_rag=True, top_rag_score=0.9) is None


def test_rag_gating_skipped_when_disabled():
    """With use_rag=False gating should never refuse regardless of score."""
    assert decide_refusal(use_rag=False, top_rag_score=0.0) is None
    assert decide_refusal(use_rag=False, top_rag_score=None) is None


def test_refusal_decision_is_frozen():
    """RefusalDecision must be immutable (frozen dataclass)."""
    dec = decide_refusal(use_rag=True, top_rag_score=0.1)
    assert dec is not None
    with pytest.raises((AttributeError, TypeError)):
        dec.refuse = False  # type: ignore[misc]


# -- Answer contract shape ------------------------------------------------


def test_answer_contract_shape_is_stable():
    """AnswerContract must expose exactly the required set of fields."""
    contract = AnswerContract(
        answer="Python is great.",
        confidence="high",
        used_rag=True,
        rag_score=0.9,
        used_web=False,
        citations=[Citation(source="doc.txt", chunk_id=1, score=0.9)],
    )
    required = {"answer", "confidence", "used_rag", "rag_score", "used_web", "citations"}
    assert required.issubset(set(AnswerContract.model_fields.keys()))
    assert isinstance(contract.answer, str)
    assert contract.confidence in {"high", "medium", "low"}
    assert isinstance(contract.citations, list)
    assert contract.used_rag is True
    assert contract.rag_score == pytest.approx(0.9)


def test_answer_contract_requires_mandatory_fields():
    """Constructing AnswerContract without required fields must raise."""
    with pytest.raises(ValidationError):
        AnswerContract()  # type: ignore[call-arg]


@pytest.mark.parametrize("conf", ["high", "medium", "low"])
def test_answer_contract_valid_confidence_values(conf: str):
    c = AnswerContract(answer="x", confidence=conf, used_rag=False, used_web=False)
    assert c.confidence == conf


def test_answer_contract_rejects_invalid_confidence():
    with pytest.raises(ValidationError):
        AnswerContract(answer="x", confidence="very_high", used_rag=False, used_web=False)  # type: ignore[arg-type]


def test_answer_contract_citations_default_empty():
    c = AnswerContract(answer="ok", confidence="low", used_rag=False, used_web=False)
    assert c.citations == []
    assert c.rag_score is None
