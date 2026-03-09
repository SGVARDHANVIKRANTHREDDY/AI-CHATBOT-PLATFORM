from typing import Literal

from pydantic import BaseModel, Field

Confidence = Literal["high", "medium", "low"]


class Citation(BaseModel):
    source: str
    chunk_id: int | None = None
    score: float | None = None


class AnswerContract(BaseModel):
    answer: str
    confidence: Confidence
    used_rag: bool
    rag_score: float | None = None
    used_web: bool
    citations: list[Citation] = Field(default_factory=list)


class ChatResponse(BaseModel):
    ok: bool = True
    result: AnswerContract
    response: str
