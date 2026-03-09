from typing import Any, Dict, List, Optional, Literal
from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]

class Citation(BaseModel):
    source: str
    chunk_id: Optional[int] = None
    score: Optional[float] = None

class AnswerContract(BaseModel):
    answer: str
    confidence: Confidence
    used_rag: bool
    rag_score: Optional[float] = None
    used_web: bool
    citations: List[Citation] = Field(default_factory=list)

class ChatResponse(BaseModel):
    ok: bool = True
    result: AnswerContract
    response: str
