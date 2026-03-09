from __future__ import annotations
from fastapi import APIRouter, Depends, HTTPException
from app.api.schemas.chat import ChatRequest, ChatResponse, ErrorResponse
from app.api.dependencies.providers import get_chat_orchestrator
from app.orchestrator.chat_orchestrator import ChatOrchestrator
from app.shared.types import AnswerContract, Citation

router = APIRouter()

@router.post("/chat", response_model=ChatResponse)
async def chat(
    request: ChatRequest,
    orchestrator: ChatOrchestrator = Depends(get_chat_orchestrator)
):
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    result = await orchestrator.generate_answer(
        request.question,
        session_id=request.session_id,
        use_rag=request.use_rag,
        use_web=request.use_web,
        rag_top_k=request.rag_top_k,
        system_prompt=request.system_prompt
    )

    raw_citations = result.get("citations") or []
    citations = [
        Citation(**c) if isinstance(c, dict) else c
        for c in raw_citations
    ]

    return ChatResponse(
        ok=True,
        result=AnswerContract(
            answer=result.get("answer", ""),
            confidence=result.get("confidence", "low"),
            used_rag=result.get("used_rag", False),
            rag_score=result.get("rag_score"),
            used_web=result.get("used_web", False),
            citations=citations,
        ),
        response=result.get("answer", ""),
    )


from fastapi.responses import StreamingResponse
import json

@router.post("/chat/stream")
async def chat_stream(
    request: ChatRequest,
    orchestrator: ChatOrchestrator = Depends(get_chat_orchestrator)
):
    if not request.question.strip():
        raise HTTPException(status_code=400, detail="Question cannot be empty")

    async def event_generator():
        final_prompt, sys_prompt, context_data = await orchestrator.pipeline.gather_context(
            request.question,
            use_rag=request.use_rag,
            use_web=request.use_web,
            rag_top_k=request.rag_top_k,
            system_prompt=request.system_prompt
        )

        async for chunk in orchestrator.llm.ask_stream(final_prompt, system_prompt=sys_prompt):
            yield f"data: {json.dumps({'content': chunk})}\n\n"

        yield "data: [DONE]\n\n"

    return StreamingResponse(event_generator(), media_type="text/event-stream")

