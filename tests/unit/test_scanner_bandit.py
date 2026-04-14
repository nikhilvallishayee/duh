"""Tests for BanditScanner."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from duh.security.config import ScannerConfig
from duh.security.finding import Severity
from duh.security.scanners.bandit_fallback import BanditScanner


_FAKE_OUTPUT = {
    "errors": [],
    "generated_at": "2024-01-01T00:00:00Z",
    "metrics": {},
    "results": [
        {
            "code": "    subprocess.call(cmd, shell=True)\n",
            "col_offset": 4,
            "filename": "app/runner.py",
            "issue_cwe": {"id": 78, "link": "https://cwe.mitre.org/data/definitions/78.html"},
            "issue_severity": "HIGH",
            "issue_confidence": "HIGH",
            "issue_text": "subprocess call with shell=True is a security risk.",
            "line_number": 42,
            "line_range": [42, 42],
            "more_info": "https://bandit.readthedocs.io/en/latest/plugins/b602.html",
            "test_id": "B602",
            "test_name": "subprocess_popen_with_shell_equals_true",
        },
        {
            "code": "    hashlib.md5(data)\n",
            "col_offset": 4,
            "filename": "app/crypto.py",
            "issue_cwe": {"id": 327, "link": "https://cwe.mitre.org/data/definitions/327.html"},
            "issue_severity": "MEDIUM",
            "issue_confidence": "HIGH",
            "issue_text": "Use of weak MD5 hash algorithm.",
            "line_number": 10,
            "line_range": [10, 10],
            "more_info": "https://bandit.readthedocs.io/en/latest/plugins/b303.html",
            "test_id": "B303",
            "test_name": "blacklist",
        },
        {
            "code": "    print(password)\n",
            "col_offset": 4,
            "filename": "app/auth.py",
            "issue_cwe": {"id": 200, "link": "https://cwe.mitre.org/data/definitions/200.html"},
            "issue_severity": "LOW",
            "issue_confidence": "MEDIUM",
            "issue_text": "Possible exposure of sensitive information.",
            "line_number": 88,
            "line_range": [88, 88],
            "more_info": "",
            "test_id": "B105",
            "test_name": "hardcoded_password_string",
        },
    ],
}


def _make_proc(output: bytes):
    async def fake_run(*args, **kwargs):
        class _Proc:
            async def communicate(self):
                return (output, b"")

        return _Proc()

    return fake_run


def test_bandit_name_and_tier() -> None:
    s = BanditScanner()
    assert s.name == "bandit"
    assert s.tier == "extended"


def test_bandit_parses_json_output(tmp_path: Path) -> None:
    s = BanditScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(json.dumps(_FAKE_OUTPUT).encode())):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert len(findings) == 3


def test_bandit_high_severity_mapping(tmp_path: Path) -> None:
    s = BanditScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(json.dumps(_FAKE_OUTPUT).encode())):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    f0 = findings[0]
    assert f0.id == "B602"
    assert f0.severity == Severity.HIGH
    assert f0.location.file == "app/runner.py"
    assert f0.location.line_start == 42
    assert 78 in f0.cwe
    assert f0.scanner == "bandit"


def test_bandit_medium_severity_mapping(tmp_path: Path) -> None:
    s = BanditScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(json.dumps(_FAKE_OUTPUT).encode())):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    f1 = findings[1]
    assert f1.id == "B303"
    assert f1.severity == Severity.MEDIUM
    assert 327 in f1.cwe


def test_bandit_low_severity_mapping(tmp_path: Path) -> None:
    s = BanditScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(json.dumps(_FAKE_OUTPUT).encode())):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    f2 = findings[2]
    assert f2.id == "B105"
    assert f2.severity == Severity.LOW


def test_bandit_empty_results(tmp_path: Path) -> None:
    s = BanditScanner()
    payload = json.dumps({"results": [], "errors": []}).encode()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(payload)):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []


def test_bandit_handles_bad_json(tmp_path: Path) -> None:
    s = BanditScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(b"not json")):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []


def test_bandit_handles_empty_stdout(tmp_path: Path) -> None:
    s = BanditScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(b"")):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []


def test_bandit_changed_files_passed(tmp_path: Path) -> None:
    """Verify changed_files are forwarded to argv instead of recursive scan."""
    s = BanditScanner()
    captured_argv: list[str] = []

    async def fake_run(*args, **kwargs):
        captured_argv.extend(args)

        class _Proc:
            async def communicate(self):
                return (b'{"results":[], "errors":[]}', b"")

        return _Proc()

    changed = [tmp_path / "app.py", tmp_path / "lib.py"]
    with patch("asyncio.create_subprocess_exec", side_effect=fake_run):
        asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=changed))
    assert str(changed[0]) in captured_argv
    assert str(changed[1]) in captured_argv
    # Should NOT include -r flag when changed_files provided
    assert "-r" not in captured_argv


def test_bandit_metadata_contains_test_name(tmp_path: Path) -> None:
    s = BanditScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(json.dumps(_FAKE_OUTPUT).encode())):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings[0].metadata["rule"] == "subprocess_popen_with_shell_equals_true"
