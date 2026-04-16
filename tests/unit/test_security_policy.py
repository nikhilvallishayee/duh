"""Tests for resolve() and PolicyDecision."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from duh.security.config import SecurityPolicy
from duh.security.engine import FindingStore
from duh.security.exceptions import ExceptionStore
from duh.security.finding import Finding, Location, Severity
from duh.security.policy import PolicyDecision, ToolUseEvent, resolve


def _finding(id: str, sev: Severity) -> Finding:
    return Finding.create(
        id=id, aliases=(), scanner="ok", severity=sev, message="m",
        description="", location=Location(file="a", line_start=1, line_end=1, snippet=""),
    )


def test_allow_when_no_findings(tmp_path: Path) -> None:
    store = FindingStore(path=tmp_path / "c.json")
    exc = ExceptionStore(path=tmp_path / "e.json")
    event = ToolUseEvent(tool="Bash", cwd=tmp_path)
    decision = resolve(event, SecurityPolicy(), store, exc)
    assert decision.action == "allow"


def test_block_on_high_severity_and_dangerous_tool(tmp_path: Path) -> None:
    store = FindingStore(path=tmp_path / "c.json")
    store.add(_finding("CVE-1", Severity.HIGH))
    exc = ExceptionStore(path=tmp_path / "e.json")
    event = ToolUseEvent(tool="Bash", cwd=tmp_path)
    decision = resolve(event, SecurityPolicy(), store, exc)
    assert decision.action == "block"
    assert decision.remediation is not None
    assert "CVE-1" in decision.remediation


def test_warn_on_medium_below_fail_threshold(tmp_path: Path) -> None:
    store = FindingStore(path=tmp_path / "c.json")
    store.add(_finding("CVE-2", Severity.MEDIUM))
    exc = ExceptionStore(path=tmp_path / "e.json")
    event = ToolUseEvent(tool="Bash", cwd=tmp_path)
    decision = resolve(event, SecurityPolicy(), store, exc)
    assert decision.action == "warn"


def test_allow_when_non_dangerous_tool(tmp_path: Path) -> None:
    store = FindingStore(path=tmp_path / "c.json")
    store.add(_finding("CVE-1", Severity.CRITICAL))
    exc = ExceptionStore(path=tmp_path / "e.json")
    event = ToolUseEvent(tool="Read", cwd=tmp_path)
    decision = resolve(event, SecurityPolicy(), store, exc)
    # Read is not in _DANGEROUS_TOOLS → not blocked even on CRITICAL
    assert decision.action in ("allow", "warn")


def test_exception_suppresses_finding(tmp_path: Path) -> None:
    store = FindingStore(path=tmp_path / "c.json")
    store.add(_finding("CVE-1", Severity.HIGH))
    exc = ExceptionStore(path=tmp_path / "e.json")
    exc.add(
        id="CVE-1",
        reason="accepted",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(days=10),
        added_by="n",
        added_at=datetime.now(tz=timezone.utc),
    )
    event = ToolUseEvent(tool="Bash", cwd=tmp_path)
    decision = resolve(event, SecurityPolicy(), store, exc)
    assert decision.action == "allow"


def test_policy_decision_is_frozen() -> None:
    d = PolicyDecision(action="allow", reason="ok", findings=(), remediation=None)
    with pytest.raises(Exception):
        d.action = "block"  # type: ignore[misc]
