"""API integration tests - exercising the actual FastAPI app.

Dependencies that require real infrastructure (LLM, RAG, Redis) are
replaced with lightweight in-process stubs via FastAPI dependency
overrides.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.dependencies.providers import get_chat_orchestrator


def _mock_orchestrator() -> MagicMock:
    """Build a zero-infrastructure stub that satisfies all route handlers."""
    orch = MagicMock()

    # /api/v1/chat
    orch.generate_answer = AsyncMock(return_value={
        "answer": "dummy-answer",
        "confidence": "high",
        "used_rag": False,
        "rag_score": None,
        "used_web": False,
        "citations": [],
    })

    # /api/v1/chat/stream
    pipeline = MagicMock()
    pipeline.gather_context = AsyncMock(return_value=(
        "prompt text",
        "system prompt",
        {"rag_score": None, "rag_citations": [], "rag_hits": [], "web_refs": []},
    ))
    orch.pipeline = pipeline

    async def _fake_stream(prompt, *, system_prompt="", model=None):
        yield "dummy"
        yield "-answer"

    orch.llm = MagicMock()
    orch.llm.ask_stream = _fake_stream

    return orch


@pytest.fixture()
def client():
    mock_orch = _mock_orchestrator()
    app.dependency_overrides[get_chat_orchestrator] = lambda: mock_orch
    try:
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c
    finally:
        app.dependency_overrides.clear()


# -- Basic liveness -------------------------------------------------------

def test_root_endpoint(client: TestClient):
    r = client.get("/")
    assert r.status_code == 200
    assert "message" in r.json()


def test_healthz(client: TestClient):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


# -- Chat endpoint --------------------------------------------------------

def test_chat_returns_contract_shape(client: TestClient):
    r = client.post(
        "/api/v1/chat",
        json={"question": "What is Python?", "session_id": "t1",
              "use_rag": False, "use_web": False},
    )
    assert r.status_code == 200
    payload = r.json()
    assert payload["ok"] is True
    result = payload["result"]
    assert result["answer"] == "dummy-answer"
    assert result["confidence"] in {"high", "medium", "low"}
    assert result["used_rag"] is False
    assert isinstance(result["citations"], list)
    assert "response" in payload


def test_chat_empty_question_rejected(client: TestClient):
    r = client.post(
        "/api/v1/chat",
        json={"question": "   ", "session_id": "t1"},
    )
    assert r.status_code == 400


# -- Stream endpoint ------------------------------------------------------

def test_chat_stream_returns_sse_text(client: TestClient):
    r = client.post(
        "/api/v1/chat/stream",
        json={"question": "hello", "session_id": "t1",
              "use_rag": False, "use_web": False},
    )
    assert r.status_code == 200
    assert "data:" in r.text or "[DONE]" in r.text


def test_chat_stream_empty_question_rejected(client: TestClient):
    r = client.post(
        "/api/v1/chat/stream",
        json={"question": "   ", "session_id": "t1"},
    )
    assert r.status_code == 400
