"""Tests for PipAuditScanner."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from duh.security.config import ScannerConfig
from duh.security.scanners.pip_audit import PipAuditScanner


_FAKE_OUTPUT = {
    "dependencies": [
        {
            "name": "requests",
            "version": "2.31.0",
            "vulns": [
                {
                    "id": "GHSA-9wx4-h78v-vm56",
                    "fix_versions": ["2.32.0"],
                    "description": "HTTP smuggling in requests <2.32",
                    "aliases": ["CVE-2024-35195"],
                }
            ],
        },
        {"name": "rich", "version": "13.0.0", "vulns": []},
    ],
}


def test_pip_audit_name_and_tier() -> None:
    s = PipAuditScanner()
    assert s.name == "pip-audit"
    assert s.tier == "minimal"


def test_pip_audit_parses_json_output(tmp_path: Path) -> None:
    s = PipAuditScanner()

    async def fake_run(*args, **kwargs):
        class _Proc:
            returncode = 0

            async def communicate(self):
                return (json.dumps(_FAKE_OUTPUT).encode(), b"")
        return _Proc()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_run):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert len(findings) == 1
    f = findings[0]
    assert "CVE-2024-35195" in f.aliases
    assert f.package == "requests"
    assert f.version == "2.31.0"
    assert f.fixed_in == "2.32.0"


def test_pip_audit_empty_when_no_vulns(tmp_path: Path) -> None:
    s = PipAuditScanner()

    async def fake_run(*args, **kwargs):
        class _Proc:
            returncode = 0

            async def communicate(self):
                return (json.dumps({"dependencies": []}).encode(), b"")
        return _Proc()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_run):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []


def test_pip_audit_handles_bad_json(tmp_path: Path) -> None:
    s = PipAuditScanner()

    async def fake_run(*args, **kwargs):
        class _Proc:
            returncode = 0

            async def communicate(self):
                return (b"not json at all", b"")
        return _Proc()

    with patch("asyncio.create_subprocess_exec", side_effect=fake_run):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []
