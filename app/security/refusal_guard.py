from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

@dataclass(frozen=True)
class RefusalDecision:
    refuse: bool
    reason: str
    message: str = "I don’t have enough reliable information to answer this question."

def decide_refusal(*, use_rag: bool, top_rag_score: Optional[float]) -> Optional[RefusalDecision]:
    if not use_rag: return None
    score = float(top_rag_score) if top_rag_score is not None else None
    if score is None or score < 0.35:
        return RefusalDecision(refuse=True, reason="rag_score_below_threshold")
    return None
