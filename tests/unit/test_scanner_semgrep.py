"""Tests for SemgrepScanner."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from duh.security.config import ScannerConfig
from duh.security.finding import Severity
from duh.security.scanners.semgrep_ext import SemgrepScanner


_FAKE_OUTPUT = {
    "results": [
        {
            "check_id": "python.lang.security.audit.eval-detected",
            "path": "app/views.py",
            "start": {"line": 42, "col": 5},
            "end": {"line": 42, "col": 30},
            "extra": {
                "severity": "ERROR",
                "message": "Use of eval() detected. This is dangerous.",
                "lines": "    eval(user_input)",
                "metadata": {
                    "cwe": ["CWE-95: Improper Neutralization"],
                    "category": "security",
                },
            },
        },
        {
            "check_id": "python.requests.security.no-auth.no-auth-missing",
            "path": "app/api.py",
            "start": {"line": 10, "col": 1},
            "end": {"line": 10, "col": 50},
            "extra": {
                "severity": "WARNING",
                "message": "Missing auth on requests call.",
                "lines": "requests.get(url)",
                "metadata": {},
            },
        },
    ],
    "errors": [],
    "stats": {},
}


def _make_proc(output: bytes, returncode: int = 0):
    async def fake_run(*args, **kwargs):
        class _Proc:
            async def communicate(self):
                return (output, b"")

        return _Proc()

    return fake_run


def test_semgrep_name_and_tier() -> None:
    s = SemgrepScanner()
    assert s.name == "semgrep"
    assert s.tier == "extended"


def test_semgrep_parses_json_output(tmp_path: Path) -> None:
    s = SemgrepScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(json.dumps(_FAKE_OUTPUT).encode())):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert len(findings) == 2
    f0 = findings[0]
    assert f0.id == "python.lang.security.audit.eval-detected"
    assert f0.scanner == "semgrep"
    assert f0.severity == Severity.HIGH
    assert f0.location.file == "app/views.py"
    assert f0.location.line_start == 42
    assert 95 in f0.cwe
    f1 = findings[1]
    assert f1.severity == Severity.MEDIUM


def test_semgrep_empty_results(tmp_path: Path) -> None:
    s = SemgrepScanner()
    payload = json.dumps({"results": [], "errors": []}).encode()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(payload)):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []


def test_semgrep_handles_bad_json(tmp_path: Path) -> None:
    s = SemgrepScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(b"not json")):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []


def test_semgrep_handles_empty_stdout(tmp_path: Path) -> None:
    s = SemgrepScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(b"")):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []


def test_semgrep_changed_files_passed(tmp_path: Path) -> None:
    """Verify changed_files are forwarded to argv."""
    s = SemgrepScanner()
    captured_argv: list[str] = []

    async def fake_run(*args, **kwargs):
        captured_argv.extend(args)

        class _Proc:
            async def communicate(self):
                return (b'{"results":[], "errors":[]}', b"")

        return _Proc()

    changed = [tmp_path / "foo.py"]
    with patch("asyncio.create_subprocess_exec", side_effect=fake_run):
        asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=changed))
    assert str(changed[0]) in captured_argv


def test_semgrep_info_severity_maps_correctly(tmp_path: Path) -> None:
    payload = {
        "results": [
            {
                "check_id": "some.inventory.rule",
                "path": "foo.py",
                "start": {"line": 1, "col": 1},
                "end": {"line": 1, "col": 10},
                "extra": {
                    "severity": "INVENTORY",
                    "message": "informational",
                    "lines": "",
                    "metadata": {},
                },
            }
        ],
        "errors": [],
    }
    s = SemgrepScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(json.dumps(payload).encode())):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings[0].severity == Severity.INFO
