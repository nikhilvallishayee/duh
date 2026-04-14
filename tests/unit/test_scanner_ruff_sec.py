"""Tests for RuffSecScanner."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from duh.security.config import ScannerConfig
from duh.security.scanners.ruff_sec import RuffSecScanner


def _fixture(name: str) -> Path:
    return Path(__file__).resolve().parent.parent / "fixtures" / "security" / name


def test_ruff_sec_name_and_tier() -> None:
    s = RuffSecScanner()
    assert s.name == "ruff-sec"
    assert s.tier == "minimal"


def test_ruff_sec_available_requires_ruff_module() -> None:
    s = RuffSecScanner()
    # ruff is in dev deps; this should be True locally
    assert isinstance(s.available(), bool)


def test_ruff_sec_detects_s_rule_in_vulnerable_fixture() -> None:
    s = RuffSecScanner()
    if not s.available():
        pytest.skip("ruff not installed")
    target = _fixture("vulnerable")
    findings = asyncio.run(s.scan(target, ScannerConfig(), changed_files=None))
    assert any(f.id.startswith("S") for f in findings), f"no S* findings in {[f.id for f in findings]}"


def test_ruff_sec_empty_on_safe_fixture(tmp_path: Path) -> None:
    s = RuffSecScanner()
    if not s.available():
        pytest.skip("ruff not installed")
    (tmp_path / "safe.py").write_text("x = 1\n")
    findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert all(not f.id.startswith("S") for f in findings)
