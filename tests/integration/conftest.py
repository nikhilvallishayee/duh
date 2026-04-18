"""Shared conftest for integration tests.

Skips any test marked ``@pytest.mark.tmux`` when the ``tmux`` binary is not
available on PATH. This keeps the tmux-based Tier C suite (ADR-074) from
erroring out on machines where tmux isn't installed (e.g. Windows runners).
"""

from __future__ import annotations

import shutil

import pytest


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    if not shutil.which("tmux"):
        skip_tmux = pytest.mark.skip(reason="tmux not installed")
        for item in items:
            if "tmux" in item.keywords:
                item.add_marker(skip_tmux)
