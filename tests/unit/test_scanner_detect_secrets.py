"""Tests for DetectSecretsScanner."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import patch

import pytest

from duh.security.config import ScannerConfig
from duh.security.scanners.detect_secrets import DetectSecretsScanner


def test_detect_secrets_name_and_tier() -> None:
    s = DetectSecretsScanner()
    assert s.name == "detect-secrets"
    assert s.tier == "minimal"


def test_detect_secrets_finds_planted_secret(tmp_path: Path) -> None:
    s = DetectSecretsScanner()
    if not s.available():
        pytest.skip("detect-secrets not installed")
    (tmp_path / "cfg.py").write_text(
        'aws_key = "AKIAIOSFODNN7EXAMPLE"\n'
        'secret = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"\n'
    )
    findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert len(findings) >= 1
    assert any("secret" in f.message.lower() or "key" in f.message.lower() for f in findings)


def test_detect_secrets_empty_on_clean_file(tmp_path: Path) -> None:
    s = DetectSecretsScanner()
    if not s.available():
        pytest.skip("detect-secrets not installed")
    (tmp_path / "clean.py").write_text("x = 1\ny = 'hello'\n")
    findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []
