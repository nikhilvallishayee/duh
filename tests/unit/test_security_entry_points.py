"""Verify the Minimal-tier scanner entry points are registered."""

from __future__ import annotations

from duh.security.engine import ScannerRegistry


def test_entry_points_discover_minimal_scanners() -> None:
    reg = ScannerRegistry()
    reg.load_entry_points()
    names = set(reg.names())
    assert "ruff-sec" in names
    assert "pip-audit" in names
    assert "detect-secrets" in names
    assert "cyclonedx-sbom" in names
