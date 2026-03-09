from unittest.mock import AsyncMock, MagicMock

import pytest
from app.orchestrator.chat_orchestrator import ChatOrchestrator
from app.orchestrator.pipeline import ChatPipeline


@pytest.mark.asyncio
async def test_multi_step_reasoning_flow():
    """Verify that ChatOrchestrator executes a multi-step plan correctly."""
    mock_llm = AsyncMock()
    # 1. Planner, 2. Step1, 3. Step2, 4. Aggregation, 5. Critic, 6. KG, 7. Grader
    mock_llm.ask.side_effect = [
        '{"nodes": [{"id": "step1", "agent": "research_agent", "task": "research facts", "dependencies": []}, {"id": "step2", "agent": "reasoning_agent", "task": "analyze facts", "dependencies": ["step1"]}]}',
        "Fact A result.",
        "Analysis result.",
        "Final summarized answer.",
        '{"score": 0.9, "needs_revision": false, "hallucinations_detected": false}',
        '{"entities": ["Fact A"], "relationships": []}',
        '{"score": 0.95, "feedback": "Great work"}',
    ]

    mock_pipeline = MagicMock(spec=ChatPipeline)
    mock_pipeline.gather_context = AsyncMock(return_value=("prompt", "sys", {"rag_score": 0.9}))
    mock_pipeline.memory_service = None

    orchestrator = ChatOrchestrator(mock_pipeline, mock_llm)
    orchestrator.memory_retriever = AsyncMock()
    orchestrator.memory_retriever.retrieve_context.return_value = ""
    orchestrator.memory_retriever.kg = MagicMock()

    # Mock all I/O bound components
    orchestrator.sem_cache = AsyncMock()
    orchestrator.sem_cache.get.return_value = None
    orchestrator.dataset_builder = MagicMock()
    orchestrator.guard = MagicMock()
    orchestrator.guard.scan.return_value = False

    result = await orchestrator.generate_answer("Research Fact A and analyze it.", session_id="test_session")

    assert "Final summarized answer" in result["answer"]
    assert result["agent_stats"]["grade_score"] == 0.95


@pytest.mark.asyncio
async def test_agent_failure_and_correction():
    """Verify that CriticAgent can trigger a correction."""
    mock_llm = AsyncMock()
    mock_llm.ask.side_effect = [
        '{"nodes": [{"id": "t1", "agent": "reasoning_agent", "task": "calculate", "dependencies": []}]}',
        "2+2=5",
        "Result is 5",
        '{"score": 0.2, "needs_revision": true, "hallucinations_detected": true, "corrected_response": "Correct result is 4"}',
        '{"entities": [], "relationships": []}',
        '{"score": 0.8, "feedback": "Corrected by critic"}',
    ]

    mock_pipeline = MagicMock(spec=ChatPipeline)
    mock_pipeline.gather_context = AsyncMock(return_value=("prompt", "sys", {"rag_score": 0.9}))
    mock_pipeline.memory_service = None

    orchestrator = ChatOrchestrator(mock_pipeline, mock_llm)
    orchestrator.memory_retriever = AsyncMock()
    orchestrator.memory_retriever.retrieve_context.return_value = ""
    orchestrator.memory_retriever.kg = MagicMock()

    orchestrator.sem_cache = AsyncMock()
    orchestrator.sem_cache.get.return_value = None
    orchestrator.dataset_builder = MagicMock()
    orchestrator.guard = MagicMock()
    orchestrator.guard.scan.return_value = False

    result = await orchestrator.generate_answer("Calculate 2+2", session_id="test_fail_recover")

    assert "result is 4" in result["answer"].lower()
    assert result["agent_stats"]["is_hallucination_free"] is False
