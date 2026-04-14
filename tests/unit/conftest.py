"""Shared pytest config for unit tests.

The single fixture here clears `DUH_STUB_PROVIDER` for every unit test by
default so that test cases which exercise real provider resolution
(`build_model_backend`, `resolve_provider_name`, runner.py / sdk_runner.py
provider auto-detect) are not silently short-circuited by a stub provider
that someone configured at the workflow / shell level.

Tests that *do* want the stub provider (`test_stub_provider.py`,
`test_cli_e2e.py`) opt in by calling ``monkeypatch.setenv`` themselves —
this fixture only neutralises the inherited environment, it never blocks
local opt-in.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_inherited_stub_provider(monkeypatch):
    """Strip DUH_STUB_PROVIDER from the inherited env for every unit test."""
    monkeypatch.delenv("DUH_STUB_PROVIDER", raising=False)
