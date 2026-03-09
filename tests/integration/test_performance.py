from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest
from app.orchestrator.chat_orchestrator import ChatOrchestrator


@pytest.fixture
def mock_semantic_cache():
    with patch("app.orchestrator.chat_orchestrator.SemanticCache") as mock:
        m = mock.return_value
        m.get = AsyncMock()
        m.set = AsyncMock()
        yield m


@pytest.mark.asyncio
async def test_semantic_cache_effectiveness(mock_semantic_cache):
    """Test 1: Verify that asking the same question twice triggers a semantic cache hit."""
    mock_pipeline = MagicMock()
    mock_pipeline.gather_context = AsyncMock()
    mock_pipeline.memory_service = None

    mock_llm = AsyncMock()
    mock_llm.ask.return_value = "Tokyo"

    orchestrator = ChatOrchestrator(mock_pipeline, mock_llm)
    question = "Japan?"

    # 1. First call - miss
    mock_semantic_cache.get.return_value = None
    mock_pipeline.gather_context.return_value = ("prompt", "system", {"rag_score": 0.9})

    res1 = await orchestrator.generate_answer(question)
    assert res1["answer"] == "Tokyo"
    assert "cached" not in res1
    mock_semantic_cache.set.assert_called_with(question, "Tokyo")

    # Record call count after first (non-cached) request
    calls_after_first = mock_llm.ask.call_count

    # 2. Second call - hit
    mock_semantic_cache.get.return_value = "Tokyo"
    res2 = await orchestrator.generate_answer(question)
    assert res2["answer"] == "Tokyo"
    assert res2["cached"] is True
    # Verify LLM was NOT called again during the cached run
    assert mock_llm.ask.call_count == calls_after_first


@pytest.mark.asyncio
async def test_rag_reranking_logic():
    """Test 2: Verify RAG Re-Ranking logic is triggered."""
    from app.rag.retriever import RAGRetriever

    with patch("sentence_transformers.SentenceTransformer"), patch("sentence_transformers.CrossEncoder"):
        retriever = RAGRetriever()
        retriever.index = MagicMock()
        retriever.chunks = [{"text": f"chunk {i}", "source": "doc.txt", "chunk_id": i} for i in range(30)]

        mock_idxs = np.array([[i for i in range(20)]])
        mock_scores = np.array([[0.9 for _ in range(20)]])
        retriever.index.search.return_value = (mock_scores, mock_idxs)

        with (
            patch.object(
                retriever.reranker, "rerank", return_value=[{"text": "best chunk", "score": 0.99}]
            ) as mock_rerank,
            patch("app.rag.retriever.settings.RERANKING_ENABLED", True),
        ):
            results = retriever.search("Explain Redis", top_k=5)
            assert mock_rerank.called
            assert len(results) == 1


@pytest.mark.asyncio
async def test_token_budget_enforcement():
    """Test 3: Verify that a long prompt is clipped to stay in budget."""
    from app.llm.tokenizer.adaptive_budget import AdaptiveTokenBudgeter
    from app.orchestrator.context_builder import build_final_prompt

    budgeter = AdaptiveTokenBudgeter(max_tokens=600)

    long_doc = "information " * 1000  # ~1000 tokens
    question = "Help?"
    system_prompt = "Be brief."

    with (
        patch("app.orchestrator.context_builder._BUDGETER", budgeter),
        patch("app.orchestrator.context_builder.settings.UX_TOKEN_LIMIT", 600),
    ):
        final_prompt, sys_prompt = build_final_prompt(
            question, system_prompt=system_prompt, rag_context=long_doc, use_rag=True
        )

        total = budgeter.count_tokens(final_prompt) + budgeter.count_tokens(sys_prompt)
        assert total <= 650
        assert len(final_prompt) < len(long_doc)
