"""Integration test fixtures — shared state isolation."""

from __future__ import annotations

import pathlib

import pytest
from app.config.settings import settings

_INTEGRATION_DIR = pathlib.Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark every test collected under tests/integration/ as 'integration'."""
    integration_marker = pytest.mark.integration
    for item in items:
        if _INTEGRATION_DIR in item.path.parents or item.path.parent == _INTEGRATION_DIR:
            item.add_marker(integration_marker)


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
