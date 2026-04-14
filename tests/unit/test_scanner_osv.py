"""Tests for OSVScanner."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from duh.security.config import ScannerConfig
from duh.security.finding import Severity
from duh.security.scanners.osv_scanner import OSVScanner, _cvss_to_severity


_FAKE_OUTPUT = {
    "results": [
        {
            "source": {"path": "requirements.txt", "type": "lockfile"},
            "packages": [
                {
                    "package": {
                        "name": "requests",
                        "version": "2.27.0",
                        "ecosystem": "PyPI",
                    },
                    "vulnerabilities": [
                        {
                            "id": "GHSA-j8r2-6x86-q33q",
                            "aliases": ["CVE-2023-32681"],
                            "summary": "Unintended leak of Proxy-Authorization header",
                            "details": "Requests forwards Proxy-Authorization headers on redirect.",
                            "severity": [{"type": "CVSS_V3", "score": "6.1"}],
                            "affected": [
                                {
                                    "ranges": [
                                        {
                                            "type": "ECOSYSTEM",
                                            "events": [
                                                {"introduced": "0"},
                                                {"fixed": "2.31.0"},
                                            ],
                                        }
                                    ]
                                }
                            ],
                        }
                    ],
                },
                {
                    "package": {"name": "certifi", "version": "2022.5.18", "ecosystem": "PyPI"},
                    "vulnerabilities": [],
                },
            ],
        }
    ]
}


def _make_proc(output: bytes):
    async def fake_run(*args, **kwargs):
        class _Proc:
            async def communicate(self):
                return (output, b"")

        return _Proc()

    return fake_run


def test_osv_name_and_tier() -> None:
    s = OSVScanner()
    assert s.name == "osv-scanner"
    assert s.tier == "extended"


def test_osv_parses_json_output(tmp_path: Path) -> None:
    s = OSVScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(json.dumps(_FAKE_OUTPUT).encode())):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert len(findings) == 1
    f = findings[0]
    assert f.id == "CVE-2023-32681"
    assert "GHSA-j8r2-6x86-q33q" in f.aliases
    assert f.package == "requests"
    assert f.version == "2.27.0"
    assert f.fixed_in == "2.31.0"
    assert f.severity == Severity.MEDIUM  # CVSS 6.1


def test_osv_empty_results(tmp_path: Path) -> None:
    s = OSVScanner()
    payload = json.dumps({"results": []}).encode()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(payload)):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []


def test_osv_handles_bad_json(tmp_path: Path) -> None:
    s = OSVScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(b"not json")):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []


def test_osv_handles_empty_stdout(tmp_path: Path) -> None:
    s = OSVScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(b"")):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings == []


@pytest.mark.parametrize(
    "score,expected",
    [
        (None, Severity.MEDIUM),
        (0.0, Severity.INFO),
        (1.0, Severity.LOW),
        (4.0, Severity.MEDIUM),
        (7.0, Severity.HIGH),
        (9.0, Severity.CRITICAL),
        (10.0, Severity.CRITICAL),
    ],
)
def test_cvss_to_severity(score, expected) -> None:
    assert _cvss_to_severity(score) == expected


def test_osv_critical_cvss(tmp_path: Path) -> None:
    payload = {
        "results": [
            {
                "source": {"path": "requirements.txt"},
                "packages": [
                    {
                        "package": {"name": "foo", "version": "1.0.0", "ecosystem": "PyPI"},
                        "vulnerabilities": [
                            {
                                "id": "GHSA-xxxx-yyyy-zzzz",
                                "aliases": [],
                                "summary": "Critical vuln",
                                "details": "very bad",
                                "severity": [{"type": "CVSS_V3", "score": "9.8"}],
                                "affected": [],
                            }
                        ],
                    }
                ],
            }
        ]
    }
    s = OSVScanner()
    with patch("asyncio.create_subprocess_exec", side_effect=_make_proc(json.dumps(payload).encode())):
        findings = asyncio.run(s.scan(tmp_path, ScannerConfig(), changed_files=None))
    assert findings[0].severity == Severity.CRITICAL
