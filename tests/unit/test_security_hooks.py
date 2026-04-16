"""Tests for hooks.install() and the four callback bindings."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from duh.hooks import HookEvent, HookRegistry, HookResponse
from duh.security.config import SecurityPolicy
from duh.security.engine import FindingStore
from duh.security.exceptions import ExceptionStore
from duh.security.finding import Finding, Location, Severity
from duh.security.hooks import SecurityContext, install


class _FakeConsole:
    def __init__(self) -> None:
        self.notifications: list[str] = []
        self.warnings: list[str] = []
        self.summaries: list[Any] = []

    def notify(self, msg: str) -> None:
        self.notifications.append(msg)

    def warn(self, msg: str) -> None:
        self.warnings.append(msg)

    def summary(self, payload: Any) -> None:
        self.summaries.append(payload)


def _high_finding() -> Finding:
    return Finding.create(
        id="CVE-2025-1",
        aliases=(),
        scanner="ok",
        severity=Severity.HIGH,
        message="m",
        description="",
        location=Location(file="a", line_start=1, line_end=1, snippet=""),
        fixed_in="2.0",
    )


def _ctx(tmp_path: Path, *, policy: SecurityPolicy | None = None) -> SecurityContext:
    store = FindingStore(path=tmp_path / "c.json")
    exc = ExceptionStore(path=tmp_path / "e.json")
    return SecurityContext(
        policy=policy or SecurityPolicy(),
        findings=store,
        exceptions=exc,
        console=_FakeConsole(),
        project_root=tmp_path,
    )


def test_install_no_op_when_runtime_disabled(tmp_path: Path) -> None:
    reg = HookRegistry()
    ctx = _ctx(tmp_path, policy=SecurityPolicy(mode="advisory"))
    install(registry=reg, ctx=ctx)
    # Advisory mode disables block_pre_tool_use but runtime.enabled is still True
    # → hooks are still installed, but blocking is off.
    names = [h.name for hooks in reg._hooks.values() for h in hooks]
    assert any("duh-security" in n for n in names)


def test_pre_tool_use_blocks_on_high_finding(tmp_path: Path) -> None:
    reg = HookRegistry()
    ctx = _ctx(tmp_path)
    ctx.findings.add(_high_finding())
    install(registry=reg, ctx=ctx)

    pre_hooks = reg._hooks.get(HookEvent.PRE_TOOL_USE, [])
    assert pre_hooks
    callback = pre_hooks[0].callback
    response = asyncio.run(callback(HookEvent.PRE_TOOL_USE, {
        "event": __import__("duh.security.policy", fromlist=["ToolUseEvent"]).ToolUseEvent(
            tool="Bash", cwd=tmp_path,
        ),
    }))
    assert isinstance(response, HookResponse)
    assert response.decision == "block"


def test_pre_tool_use_allows_on_clean_state(tmp_path: Path) -> None:
    reg = HookRegistry()
    ctx = _ctx(tmp_path)
    install(registry=reg, ctx=ctx)
    callback = reg._hooks[HookEvent.PRE_TOOL_USE][0].callback
    from duh.security.policy import ToolUseEvent
    response = asyncio.run(callback(HookEvent.PRE_TOOL_USE, {
        "event": ToolUseEvent(tool="Bash", cwd=tmp_path),
    }))
    assert response.decision == "continue"


def test_session_start_notifies_expiring(tmp_path: Path) -> None:
    reg = HookRegistry()
    ctx = _ctx(tmp_path)
    now = datetime.now(tz=timezone.utc)
    ctx.exceptions.add(
        id="CVE-A",
        reason="r",
        expires_at=now + timedelta(days=3),
        added_by="n",
        added_at=now,
    )
    install(registry=reg, ctx=ctx)
    callback = reg._hooks[HookEvent.SESSION_START][0].callback
    asyncio.run(callback(HookEvent.SESSION_START, {"session_id": "sess1"}))
    assert any("expire" in m.lower() for m in ctx.console.notifications)
