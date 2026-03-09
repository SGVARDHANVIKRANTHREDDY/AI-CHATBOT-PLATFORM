from __future__ import annotations

import contextlib
from typing import Any

from app.config.settings import settings
from app.orchestrator.context_builder import build_final_prompt, format_rag_context
from app.shared.utils import get_logger

_LOG = get_logger(__name__)


class ChatPipeline:
    def __init__(self, retriever, memory_service, tool_service=None):
        self.retriever = retriever
        self.memory_service = memory_service
        self.tool_service = tool_service

    async def gather_context(
        self,
        question: str,
        *,
        use_rag: bool = True,
        use_web: bool = False,
        rag_top_k: int = 3,
        system_prompt: str | None = None,
        memory_vector_context: str = "",
    ) -> tuple[str, str, dict[str, Any]]:
        rag_hits = []
        rag_citations = []
        rag_context = ""

        if use_rag and self.retriever and self.retriever.ensure_loaded():
            rag_hits = self.retriever.search(question, top_k=rag_top_k)
            rag_context, rag_citations = format_rag_context(rag_hits)

        web_refs = []
        web_context = ""
        if use_web and self.tool_service:
            with contextlib.suppress(Exception):
                web_context, web_refs = await self.tool_service.get_web_context(
                    question,
                    max_results=settings.WEB_MAX_RESULTS,
                )

        memory_block = ""
        if self.memory_service:
            msgs = self.memory_service.get_messages()
            memory_block = "\n".join([f"{m['role']}: {m['content']}" for m in msgs])

        final_prompt, finalized_sys_prompt = build_final_prompt(
            question,
            system_prompt=system_prompt or settings.DEFAULT_SYSTEM_PROMPT,
            memory_block=memory_block,
            rag_context=rag_context,
            web_context=web_context,
            memory_vector_context=memory_vector_context,
            use_rag=use_rag,
            use_web=use_web,
        )

        context_data = {
            "rag_hits": rag_hits,
            "rag_citations": rag_citations,
            "web_refs": web_refs,
            "rag_score": float(rag_hits[0]["score"]) if rag_hits else None,
        }

        return final_prompt, finalized_sys_prompt, context_data
