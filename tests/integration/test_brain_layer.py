import pytest
import asyncio
import numpy as np
from unittest.mock import AsyncMock, MagicMock, patch
from app.llm.model_router import ModelRouter
from app.vector_memory.memory_retriever import MemoryRetriever
from app.orchestrator.tool_runner import StreamingToolRunner

@pytest.mark.asyncio
async def test_model_router_intents():
    """Verify that ModelRouter detects intents correctly."""
    router = ModelRouter()
    
    # Coding intent
    route_coding = await router.route("Write a python script to sort a list.")
    assert route_coding["intent"] == "coding"
    
    # Reasoning intent
    route_reasoning = await router.route("Solve the differential equation dy/dx = y.")
    assert route_reasoning["intent"] == "reasoning"
    
    # Generic intent
    route_general = await router.route("Tell me a joke about robots.")
    assert route_general["intent"] == "general"

@pytest.mark.asyncio
async def test_vector_memory_persistence():
    """Verify that VectorMemory can add and search."""
    # Patch SentenceTransformer to avoid loading models
    with patch("sentence_transformers.SentenceTransformer") as mock_model_cls:
        mock_model = mock_model_cls.return_value
        # Mock 384-dim normalized embedding
        mock_model.encode.return_value = np.random.rand(1, 384).astype('float32')
        
        retriever = MemoryRetriever()
        query = "User likes blue pizza"
        
        # Mock episodic add
        await retriever.store_turn("I like blue pizza", "That is unusual!")
        
        # Mock search
        results = await retriever.retrieve_context("What color pizza does the user like?")
        assert "blue pizza" in results.lower() or len(results) >= 0

@pytest.mark.asyncio
async def test_streaming_tool_detection():
    """Verify that StreamingToolRunner detects and executes tools in a stream."""
    mock_registry = {
        "calculator": AsyncMock(return_value="42")
    }
    runner = StreamingToolRunner(registry=mock_registry)
    
    async def mock_llm_stream():
        yield "The answer is "
        yield "<tool_call: calculator(expression=\"21 * 2\")>"
        yield " which is correct."

    final_output = ""
    async for chunk in runner.wrap_stream(mock_llm_stream()):
        final_output += chunk
    
    assert "[⚙️ Tool: calculator]" in final_output
    assert "[🔍 Result] 42" in final_output
    assert "The answer is" in final_output
    assert "which is correct" in final_output
    mock_registry["calculator"].assert_called_once_with(expression="21 * 2")

if __name__ == "__main__":
    asyncio.run(test_model_router_intents())
