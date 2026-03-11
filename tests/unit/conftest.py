"""Unit test fixtures — auto-mark all tests in this directory."""

from __future__ import annotations

import pathlib

import pytest

_UNIT_DIR = pathlib.Path(__file__).parent


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Auto-mark every test collected under tests/unit/ as 'unit'."""
    unit_marker = pytest.mark.unit
    for item in items:
        if _UNIT_DIR in item.path.parents or item.path.parent == _UNIT_DIR:
            item.add_marker(unit_marker)
