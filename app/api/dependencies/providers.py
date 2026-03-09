from __future__ import annotations
from fastapi import Request
from app.orchestrator.chat_orchestrator import ChatOrchestrator
from app.orchestrator.pipeline import ChatPipeline
from app.rag.retriever import RAGRetriever
from app.memory.memory_service import MemoryService
from app.tools import web_search

from app.llm.providers.huggingface_provider import HuggingFaceProvider
from app.llm.providers.openai_provider import OpenAIProvider
from app.llm.providers.fallback_provider import FallbackProvider
from app.config.settings import settings

def get_llm_provider() -> FallbackProvider:
    primary = HuggingFaceProvider(
        model_id=settings.HF_MODEL,
        token=settings.HF_TOKEN,
        api_url=settings.HF_API_URL
    )
    fallback = OpenAIProvider(
        api_key=settings.OPENAI_API_KEY,
        model=settings.OPENAI_MODEL
    )
    return FallbackProvider(primary=primary, fallbacks=[fallback])

def get_retriever() -> RAGRetriever:
    return RAGRetriever()

def get_memory_service(session_id: str = "default") -> MemoryService:
    return MemoryService(session_id=session_id)

def get_chat_orchestrator(request: Request) -> ChatOrchestrator:
    retriever = get_retriever()
    memory = get_memory_service() 
    pipeline = ChatPipeline(retriever=retriever, memory_service=memory, tool_service=web_search)
    llm = get_llm_provider()
    return ChatOrchestrator(pipeline=pipeline, llm_provider=llm)
