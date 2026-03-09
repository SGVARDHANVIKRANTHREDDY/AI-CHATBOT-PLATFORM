"""Integration test fixtures — shared state isolation."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config.settings import settings

_EVOLUTION_PATH = settings.DATA_DIR / "prompts" / "evolution.json"


@pytest.fixture(autouse=True)
def _reset_prompt_evolution():
    """Delete prompt evolution state before each integration test.

    This prevents prompt-mutation side-effects from leaking between
    tests when mock LLM responses are inadvertently adopted as
    candidate templates.
    """
    if _EVOLUTION_PATH.exists():
        _EVOLUTION_PATH.unlink()
    yield
    # Clean up after as well so the suite finishes with a clean state
    if _EVOLUTION_PATH.exists():
        _EVOLUTION_PATH.unlink()
