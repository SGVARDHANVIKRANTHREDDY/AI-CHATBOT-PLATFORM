"""E2E test fixtures — auto-mark all tests in this directory."""

from __future__ import annotations

import pathlib

import pytest

_E2E_DIR = pathlib.Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark every test collected under tests/e2e/ as 'e2e'."""
    e2e_marker = pytest.mark.e2e
    for item in items:
        if _E2E_DIR in item.path.parents or item.path.parent == _E2E_DIR:
            item.add_marker(e2e_marker)
