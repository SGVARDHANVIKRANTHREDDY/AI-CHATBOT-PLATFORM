import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest
from app.orchestrator.chat_orchestrator import ChatOrchestrator
from app.orchestrator.pipeline import ChatPipeline


@pytest.mark.asyncio
async def test_concurrent_load():
    """Simulate 100 concurrent queries to check stability."""
    mock_llm = AsyncMock()
    # Mock a simple successful response for all calls
    mock_llm.ask.return_value = '{"nodes": []}'  # Empty plan for speed

    mock_pipeline = MagicMock(spec=ChatPipeline)
    mock_pipeline.gather_context = AsyncMock(return_value=("prompt", "sys", {"rag_score": 0.9}))
    mock_pipeline.memory_service = MagicMock()

    orchestrator = ChatOrchestrator(mock_pipeline, mock_llm)
    orchestrator.sem_cache = AsyncMock()
    orchestrator.sem_cache.get.return_value = None
    orchestrator.guard = MagicMock()
    orchestrator.guard.scan.return_value = False

    # Run 100 queries concurrently
    tasks = [orchestrator.generate_answer(f"Query {i}", session_id=f"session_{i}") for i in range(100)]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    # Verify no exceptions occurred
    for i, res in enumerate(results):
        assert not isinstance(res, Exception), f"Query {i} failed: {res}"
        assert "answer" in res


@pytest.mark.asyncio
async def test_long_reasoning_flow_stress():
    """Test complex reasoning with specific prompt and deep graph."""
    mock_llm = AsyncMock()
    # 1. Planner output for a deep graph
    mock_llm.ask.side_effect = [
        # Planner
        '{"nodes": ['
        '{"id": "eu", "agent": "research_agent", "task": "research EU AI Act", "dependencies": []},'
        '{"id": "us", "agent": "research_agent", "task": "research US AI policy", "dependencies": []},'
        '{"id": "compare", "agent": "reasoning_agent", "task": "compare US and EU", "dependencies": ["eu", "us"]},'
        '{"id": "impact", "agent": "reasoning_agent", "task": "analyze global impact", "dependencies": ["compare"]}'
        "]}",
        "EU AI Act details...",  # eu
        "US Policy details...",  # us
        "Comparison result...",  # compare
        "Global impact analysis...",  # impact
        "Synthesized final answer...",  # Aggregator
        '{"score": 0.6, "needs_revision": true, "hallucinations_detected": false, "corrected_response": "Refined synthesized final answer..."}',  # Critic
        '{"entities": [], "relationships": []}',  # KG extraction
        '{"score": 0.9, "feedback": "Solid"}',  # Grader
    ]

    mock_pipeline = MagicMock(spec=ChatPipeline)
    mock_pipeline.gather_context = AsyncMock(return_value=("prompt", "sys", {"rag_score": 0.9}))
    mock_pipeline.memory_service = MagicMock()

    orchestrator = ChatOrchestrator(mock_pipeline, mock_llm)
    orchestrator.sem_cache = AsyncMock()
    orchestrator.sem_cache.get.return_value = None
    orchestrator.guard = MagicMock()
    orchestrator.guard.scan.return_value = False

    prompt = "Analyze the impact of AI regulation in the EU and compare it with US policy."
    result = await orchestrator.generate_answer(prompt, session_id="stress_reasoning")

    assert "Refined" in result["answer"]
    assert result["agent_stats"]["steps"] == 4
    assert result["agent_stats"]["grade_score"] == 0.9


@pytest.mark.asyncio
async def test_tool_failure_recovery():
    """Verify agents recover gracefully from tool failures."""
    mock_llm = AsyncMock()
    # Planner -> Agent Turn 1 -> Agent Turn 2 -> Aggregator -> Critic -> KG -> Grader
    mock_llm.ask.side_effect = [
        '{"nodes": [{"id": "t1", "agent": "research_agent", "task": "web search failure", "dependencies": []}]}',  # Planner
        '<tool_call: web_search(query="failure-query")>',  # Agent Turn 1
        "The tool failed, so I am answering based on my memory: Recovery Successful.",  # Agent Turn 2
        "Summarized result: Recovery Successful.",  # Aggregator
        '{"score": 0.8, "needs_revision": false}',  # Critic
        '{"entities": [], "relationships": []}',  # KG
        '{"score": 0.9, "feedback": "Good recovery"}',  # Grader
    ]

    mock_pipeline = MagicMock(spec=ChatPipeline)
    mock_pipeline.gather_context = AsyncMock(return_value=("prompt", "sys", {"rag_score": 0.9}))
    mock_pipeline.memory_service = MagicMock()

    orchestrator = ChatOrchestrator(mock_pipeline, mock_llm)
    orchestrator.sem_cache = AsyncMock()
    orchestrator.sem_cache.get.return_value = None
    orchestrator.guard = MagicMock()
    orchestrator.guard.scan.return_value = False

    # Mock tool failure
    orchestrator.tool_runner.run_tool = AsyncMock(return_value="Error: Web search timed out")

    result = await orchestrator.generate_answer("Failing tool test", session_id="stress_tool_fail")

    assert "Recovery Successful" in result["answer"]
    assert result["agent_stats"]["steps"] == 1
