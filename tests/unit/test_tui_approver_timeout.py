"""Tests for TUIApprover auto-deny timeout (ADR-073 Wave 1 task 3).

These tests exercise ``TUIApprover.check()`` in isolation, using a fake
``app`` object that provides a controllable ``push_screen_wait`` coroutine.
No real Textual app is constructed; the approver only needs:

- ``app.push_screen_wait(modal)`` → awaitable resolving to a response
- ``app.query_one(...)`` → only called on the auto-deny path for the log
  widget; we stub it to raise and rely on TUIApprover's best-effort
  suppression.
"""

from __future__ import annotations

import asyncio
import logging
import os
from unittest.mock import patch

import pytest

from duh.config import Config, load_config, _merge_into
from duh.kernel.permission_cache import SessionPermissionCache
from duh.ui.tui_approver import TUIApprover


# ---------------------------------------------------------------------------
# Fake app — just enough surface for TUIApprover.check()
# ---------------------------------------------------------------------------


class _FakeApp:
    """Minimal stand-in for DuhApp.

    ``push_screen_wait`` is a coroutine controlled by the test: it waits on
    an ``asyncio.Event`` until the test sets a response, at which point it
    returns the chosen vocabulary letter ('y' / 'a' / 'n' / 'N').  A test
    that never sets the response simulates a walk-away user.
    """

    def __init__(self) -> None:
        self._responded: asyncio.Event = asyncio.Event()
        self._response: str = "n"
        self.push_calls: int = 0

    def respond(self, value: str) -> None:
        self._response = value
        self._responded.set()

    async def push_screen_wait(self, modal) -> str:  # noqa: ARG002
        self.push_calls += 1
        await self._responded.wait()
        return self._response

    def query_one(self, *_args, **_kwargs):
        # Approver's _notify_auto_deny tries to mount a Static into the log;
        # we don't have a real widget tree here, so raise and rely on the
        # approver's best-effort exception suppression.
        raise RuntimeError("no message log in test fake")


# ---------------------------------------------------------------------------
# Core timeout behaviour
# ---------------------------------------------------------------------------


class TestTUIApproverTimeout:
    async def test_auto_denies_when_user_does_not_respond(self):
        """timeout=1s, user never responds → allowed=False within 1.5s."""
        app = _FakeApp()
        cache = SessionPermissionCache()
        approver = TUIApprover(app=app, permission_cache=cache, timeout_seconds=1.0)

        loop = asyncio.get_running_loop()
        started = loop.time()
        result = await approver.check("Bash", {"command": "ls"})
        elapsed = loop.time() - started

        assert result["allowed"] is False
        assert "Auto-denied" in result["reason"]
        assert "1" in result["reason"]  # contains timeout value
        # Must return *around* the timeout, not hang.
        assert elapsed < 1.5, f"timeout took too long: {elapsed:.2f}s"
        assert elapsed >= 0.9, f"returned too early: {elapsed:.2f}s"
        assert app.push_calls == 1

    async def test_auto_deny_is_cached(self):
        """After auto-deny, the same tool is denied from cache (no re-prompt)."""
        app = _FakeApp()
        cache = SessionPermissionCache()
        approver = TUIApprover(app=app, permission_cache=cache, timeout_seconds=0.2)

        first = await approver.check("Bash", {"command": "ls"})
        assert first["allowed"] is False
        assert app.push_calls == 1

        # The auto-deny should have recorded "N" in the cache.
        assert cache.check("Bash") == "deny"

        # Second request for the same tool must not push the modal again.
        second = await approver.check("Bash", {"command": "pwd"})
        assert second["allowed"] is False
        assert "cached" in second["reason"].lower()
        assert app.push_calls == 1  # unchanged — no re-prompt

    async def test_timeout_none_disables_wait_for(self):
        """timeout=None: user can take forever; no asyncio.TimeoutError fires."""
        app = _FakeApp()
        approver = TUIApprover(app=app, timeout_seconds=None)

        async def _runner():
            return await approver.check("Bash", {"command": "ls"})

        task = asyncio.create_task(_runner())
        # Give the approver time to enter the modal await.
        await asyncio.sleep(0.3)
        assert not task.done(), "check() should still be waiting"

        # Respond after a delay that would have tripped any sensible timeout.
        app.respond("y")
        result = await asyncio.wait_for(task, timeout=1.0)
        assert result["allowed"] is True

    async def test_normal_flow_user_responds_in_time(self):
        """User responds before timeout → original behaviour unchanged."""
        app = _FakeApp()
        approver = TUIApprover(app=app, timeout_seconds=5.0)

        async def _runner():
            return await approver.check("Bash", {"command": "ls"})

        task = asyncio.create_task(_runner())
        await asyncio.sleep(0.05)
        app.respond("y")
        result = await asyncio.wait_for(task, timeout=1.0)

        assert result["allowed"] is True
        assert "reason" not in result or not result.get("reason")

    async def test_user_denies_reason_preserved(self):
        """User explicitly denies — reason is 'User denied', not 'Auto-denied'."""
        app = _FakeApp()
        approver = TUIApprover(app=app, timeout_seconds=5.0)

        task = asyncio.create_task(approver.check("Bash", {"command": "x"}))
        await asyncio.sleep(0.05)
        app.respond("n")
        result = await asyncio.wait_for(task, timeout=1.0)

        assert result["allowed"] is False
        assert result["reason"] == "User denied"

    async def test_warning_logged_on_timeout(self, caplog):
        """Timeout logs at WARNING level (not ERROR)."""
        app = _FakeApp()
        approver = TUIApprover(app=app, timeout_seconds=0.2)

        with caplog.at_level(logging.WARNING, logger="duh.ui.tui_approver"):
            await approver.check("Bash", {"command": "ls"})

        warn_records = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any("auto-deny" in r.message.lower() for r in warn_records), (
            f"expected WARNING log, got: {[r.message for r in caplog.records]}"
        )
        # Must NOT be logged at ERROR.
        assert not any(r.levelno >= logging.ERROR for r in caplog.records)

    async def test_cached_allow_short_circuits_timeout_path(self):
        """A pre-cached allow bypasses the modal entirely — no timeout race."""
        app = _FakeApp()
        cache = SessionPermissionCache()
        cache.record("Read", "a")  # always allow

        approver = TUIApprover(app=app, permission_cache=cache, timeout_seconds=0.1)
        result = await approver.check("Read", {"path": "/etc"})

        assert result["allowed"] is True
        assert app.push_calls == 0  # modal never pushed


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


class TestConfigIntegration:
    def test_default_is_sixty_seconds(self):
        c = Config()
        assert c.approval_timeout_seconds == 60.0

    def test_load_config_defaults_to_sixty(self, tmp_path, monkeypatch):
        # Isolate from user config, project config, and env vars.
        monkeypatch.setattr("duh.config.config_dir", lambda: tmp_path / "no-config")
        monkeypatch.delenv("DUH_APPROVAL_TIMEOUT_SECONDS", raising=False)
        config = load_config(cwd=str(tmp_path))
        assert config.approval_timeout_seconds == 60.0

    def test_override_via_env_var(self, tmp_path, monkeypatch):
        monkeypatch.setattr("duh.config.config_dir", lambda: tmp_path / "no-config")
        monkeypatch.setenv("DUH_APPROVAL_TIMEOUT_SECONDS", "30")
        config = load_config(cwd=str(tmp_path))
        assert config.approval_timeout_seconds == 30.0

    def test_override_via_cli_args(self, tmp_path, monkeypatch):
        monkeypatch.setattr("duh.config.config_dir", lambda: tmp_path / "no-config")
        monkeypatch.delenv("DUH_APPROVAL_TIMEOUT_SECONDS", raising=False)
        config = load_config(
            cwd=str(tmp_path),
            cli_args={"approval_timeout_seconds": 5.0},
        )
        assert config.approval_timeout_seconds == 5.0

    def test_disable_via_env_var_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("duh.config.config_dir", lambda: tmp_path / "no-config")
        monkeypatch.setenv("DUH_APPROVAL_TIMEOUT_SECONDS", "none")
        config = load_config(cwd=str(tmp_path))
        assert config.approval_timeout_seconds is None

    def test_merge_into_accepts_explicit_none(self):
        c = Config()
        _merge_into(c, {"approval_timeout_seconds": None})
        assert c.approval_timeout_seconds is None

    def test_approver_uses_config_timeout(self):
        """Wiring path: Config.approval_timeout_seconds → TUIApprover.timeout_seconds."""
        config = Config(approval_timeout_seconds=15.0)
        approver = TUIApprover(
            app=_FakeApp(),
            timeout_seconds=config.approval_timeout_seconds,
        )
        assert approver.timeout_seconds == 15.0

    def test_approver_normalises_nonpositive_to_none(self):
        """0 or negative timeout is treated as disabled (no timeout)."""
        approver_zero = TUIApprover(app=_FakeApp(), timeout_seconds=0)
        approver_neg = TUIApprover(app=_FakeApp(), timeout_seconds=-1.0)
        assert approver_zero.timeout_seconds is None
        assert approver_neg.timeout_seconds is None
