from __future__ import annotations

from pydantic import BaseModel, Field

from app.shared.types import AnswerContract


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, description="User question")
    session_id: str = Field(default="default", description="Conversation session")
    use_rag: bool = True
    use_web: bool = False
    use_memory: bool = True
    rag_top_k: int | None = Field(default=None, ge=1, le=50)
    system_prompt: str | None = None


class ChatResponse(BaseModel):
    ok: bool = True
    result: AnswerContract
    response: str


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str
