from unittest.mock import AsyncMock, MagicMock

import pytest
from app.llm.providers.fallback_provider import FallbackProvider
from app.llm.providers.huggingface_provider import HuggingFaceProvider


@pytest.mark.asyncio
async def test_fallback_logic():
    # Setup mocks
    primary = MagicMock()
    primary.ask = AsyncMock()
    primary.ask.side_effect = Exception("API down")

    fallback = MagicMock()
    fallback.ask = AsyncMock(return_value="Success from fallback")

    provider = FallbackProvider(primary=primary, fallbacks=[fallback])

    # Execute
    result = await provider.ask("Hello")

    # Assert
    assert result == "Success from fallback"
    primary.ask.assert_called_once()
    fallback.ask.assert_called_once()


@pytest.mark.asyncio
async def test_huggingface_streaming():
    # Setup mock for AsyncInferenceClient
    mock_client = MagicMock()

    # Mock chat_completion to return an async iterator
    async def mock_stream():
        yield MagicMock(choices=[MagicMock(delta=MagicMock(content="Hello "))])
        yield MagicMock(choices=[MagicMock(delta=MagicMock(content="World"))])

    mock_client.chat_completion = AsyncMock(return_value=mock_stream())

    provider = HuggingFaceProvider(model_id="test-model")
    provider.client = mock_client

    # Execute
    chunks = []
    async for chunk in provider.ask_stream("Hello"):
        chunks.append(chunk)

    # Assert
    assert "".join(chunks) == "Hello World"
