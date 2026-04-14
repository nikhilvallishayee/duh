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


def test_scan_baseline_only_reports_new(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When baseline and head both contain finding X, only net-new findings surface."""
    from duh.security import cli as sec_cli

    async def fake_scan_head(root, scanner_filter):
        from duh.security.finding import Finding, Location, Severity
        return [
            Finding.create(
                id="OLD-1", aliases=(), scanner="ok", severity=Severity.HIGH,
                message="m", description="",
                location=Location(file="a.py", line_start=1, line_end=1, snippet=""),
            ),
            Finding.create(
                id="NEW-1", aliases=(), scanner="ok", severity=Severity.HIGH,
                message="m", description="",
                location=Location(file="b.py", line_start=1, line_end=1, snippet=""),
            ),
        ]

    async def fake_scan_base(root, scanner_filter):
        from duh.security.finding import Finding, Location, Severity
        return [
            Finding.create(
                id="OLD-1", aliases=(), scanner="ok", severity=Severity.HIGH,
                message="m", description="",
                location=Location(file="a.py", line_start=1, line_end=1, snippet=""),
            ),
        ]

    calls = {"n": 0}
    async def fake_run_scan(root, scanner_filter):
        calls["n"] += 1
        return await (fake_scan_head if calls["n"] == 1 else fake_scan_base)(root, scanner_filter)

    monkeypatch.setattr(sec_cli, "_run_scan", fake_run_scan)
    monkeypatch.setattr(sec_cli, "_checkout_baseline", lambda ref, root: root)

    out_file = tmp_path / "findings.sarif"
    exit_code = security_main([
        "scan",
        "--baseline", "origin/main",
        "--sarif-out", str(out_file),
        "--project-root", str(tmp_path),
    ])
    import json as _json
    sarif = _json.loads(out_file.read_text())
    rule_ids = [r["ruleId"] for r in sarif["runs"][0]["results"]]
    assert "NEW-1" in rule_ids
    assert "OLD-1" not in rule_ids


def test_hook_install_writes_pre_push(tmp_path: Path) -> None:
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    exit_code = security_main([
        "hook", "install", "git",
        "--project-root", str(tmp_path),
    ])
    assert exit_code == 0
    hook = tmp_path / ".git" / "hooks" / "pre-push"
    assert hook.exists()
    assert hook.stat().st_mode & 0o111  # executable
    body = hook.read_text()
    assert "duh security scan" in body
    assert "--no-verify" in body


def test_hook_uninstall_removes_hook(tmp_path: Path) -> None:
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    security_main(["hook", "install", "git", "--project-root", str(tmp_path)])
    exit_code = security_main(["hook", "uninstall", "git", "--project-root", str(tmp_path)])
    assert exit_code == 0
    assert not (tmp_path / ".git" / "hooks" / "pre-push").exists()


from datetime import datetime, timedelta, timezone

from duh.security.exceptions import ExceptionStore


def test_exception_add_persists(tmp_path: Path) -> None:
    expires = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    exit_code = security_main([
        "exception", "add", "CVE-2025-12345",
        "--reason", "patch pending",
        "--expires", expires,
        "--project-root", str(tmp_path),
    ])
    assert exit_code == 0
    store = ExceptionStore.load(tmp_path / ".duh" / "security-exceptions.json")
    assert any(e.id == "CVE-2025-12345" for e in store.all())


def test_exception_list_prints(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    expires = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    security_main([
        "exception", "add", "CVE-2025-12345",
        "--reason", "r", "--expires", expires,
        "--project-root", str(tmp_path),
    ])
    exit_code = security_main(["exception", "list", "--project-root", str(tmp_path)])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "CVE-2025-12345" in out


def test_exception_remove(tmp_path: Path) -> None:
    expires = (datetime.now(tz=timezone.utc) + timedelta(days=30)).isoformat()
    security_main([
        "exception", "add", "CVE-2025-12345",
        "--reason", "r", "--expires", expires,
        "--project-root", str(tmp_path),
    ])
    exit_code = security_main([
        "exception", "remove", "CVE-2025-12345",
        "--project-root", str(tmp_path),
    ])
    assert exit_code == 0
