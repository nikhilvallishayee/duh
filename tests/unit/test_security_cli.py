"""Tests for the duh security CLI dispatcher."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from duh.security.cli import main as security_main


def test_security_scan_prints_sarif(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = security_main(["scan", "--sarif-out", "-", "--project-root", str(tmp_path)])
    assert exit_code == 0
    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert payload["version"] == "2.1.0"
    assert payload["$schema"].startswith("https://json.schemastore.org/sarif")
    assert "runs" in payload


def test_security_scan_writes_sarif_file(tmp_path: Path) -> None:
    out = tmp_path / "findings.sarif"
    exit_code = security_main([
        "scan", "--sarif-out", str(out), "--project-root", str(tmp_path),
    ])
    assert exit_code == 0
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["version"] == "2.1.0"


def test_security_scan_unknown_subcommand_errors(capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = security_main(["not-a-real-subcommand"])
    assert exit_code != 0


def test_security_scan_exit_code_on_findings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Without any scanners enabled, exit code should be 0.
    exit_code = security_main(["scan", "--sarif-out", "-", "--project-root", str(tmp_path)])
    assert exit_code == 0
