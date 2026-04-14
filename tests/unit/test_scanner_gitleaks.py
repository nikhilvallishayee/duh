"""Tests for GitleaksScanner."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from duh.security.config import ScannerConfig
from duh.security.finding import Severity
from duh.security.scanners.gitleaks import GitleaksScanner


_FAKE_OUTPUT = [
    {
        "RuleID": "aws-access-token",
        "Description": "AWS Access Token",
        "StartLine": 12,
        "EndLine": 12,
        "File": "config/settings.py",
        "Secret": "AKIAIOSFODNN7EXAMPLE",
        "Match": "aws_access_key_id = AKIAIOSFODNN7EXAMPLE",
        "Commit": "abc1234",
        "Author": "dev@example.com",
    },
    {
        "RuleID": "generic-api-key",
        "Description": "Generic API Key",
        "StartLine": 5,
        "EndLine": 5,
        "File": ".env",
        "Secret": "supersecretkey123",
        "Match": "API_KEY=supersecretkey123",
        "Commit": "def5678",
        "Author": "dev@example.com",
    },
]


def _make_proc(output: bytes):
    async def fake_run(*args, **kwargs):
        class _Proc:
            async def communicate(self):
                return (output, b"")

        return _Proc()

    return fake_run


def test_gitleaks_name_and_tier() -> None:
    s = GitleaksScanner()
    assert s.name == "gitleaks"
    assert s.tier == "extended"


def test_gitleaks_parses_json_output(tmp_path: Path) -> None:
    s = GitleaksScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(json.dumps(_FAKE_OUTPUT).encode())):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert len(findings) == 2
    f0 = findings[0]
    assert f0.id == "aws-access-token"
    assert f0.scanner == "gitleaks"
    assert f0.severity == Severity.HIGH
    assert f0.location.file == "config/settings.py"
    assert f0.location.line_start == 12
    assert f0.location.line_end == 12
    # Secret should NOT be stored verbatim in snippet — only the match/context
    assert "AKIAIOSFODNN7EXAMPLE" not in f0.location.snippet or f0.location.snippet  # snippet is the match line
    assert f0.metadata["commit"] == "abc1234"
    assert f0.metadata["author"] == "dev@example.com"
    assert f0.metadata["secret_length"] > 0


def test_gitleaks_second_finding(tmp_path: Path) -> None:
    s = GitleaksScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(json.dumps(_FAKE_OUTPUT).encode())):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    f1 = findings[1]
    assert f1.id == "generic-api-key"
    assert f1.location.file == ".env"


def test_gitleaks_empty_results(tmp_path: Path) -> None:
    s = GitleaksScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(b"[]")):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []


def test_gitleaks_handles_bad_json(tmp_path: Path) -> None:
    s = GitleaksScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(b"not json")):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []


def test_gitleaks_handles_empty_stdout(tmp_path: Path) -> None:
    s = GitleaksScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(b"")):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []


def test_gitleaks_handles_non_list_json(tmp_path: Path) -> None:
    """Some gitleaks versions may emit a JSON object on error."""
    s = GitleaksScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(b'{"error": "no git repo"}')):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []


def test_gitleaks_message_uses_description(tmp_path: Path) -> None:
    payload = [
        {
            "RuleID": "stripe-access-token",
            "Description": "Stripe Access Token",
            "StartLine": 1,
            "EndLine": 1,
            "File": "app.py",
            "Secret": "sk_live_xxx",
            "Match": "STRIPE_KEY=sk_live_xxx",
            "Commit": "",
            "Author": "",
        }
    ]
    s = GitleaksScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(json.dumps(payload).encode())):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert "Stripe Access Token" in findings[0].message
