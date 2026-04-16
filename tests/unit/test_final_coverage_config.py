"""Close remaining config.py and hooks.py coverage gaps."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ==========================================================================
# duh.config
# ==========================================================================

from duh.config import (
    Config,
    _merge_into,
    _dirs_root_to_cwd,
    load_instructions,
)


class TestMergeInto:
    def test_merge_system_prompt(self):
        """Line 104: merge system_prompt from data."""
        cfg = Config()
        _merge_into(cfg, {"system_prompt": "custom prompt"})
        assert cfg.system_prompt == "custom prompt"

    def test_merge_approval_mode(self):
        """Line 106: merge approval_mode from data."""
        cfg = Config()
        _merge_into(cfg, {"approval_mode": "never"})
        assert cfg.approval_mode == "never"


class TestDirsRootToCwd:
    def test_walk_past_filesystem_root(self, tmp_path, monkeypatch):
        """Line 246: walk hits filesystem root without finding git_root."""
        # Mock _find_git_root to return a path outside of cwd walk
        from duh import config as config_mod

        def _fake_git_root(cwd):
            # Return a path that's a sibling — walk from cwd won't reach it
            return Path("/nonexistent/sibling")

        monkeypatch.setattr(config_mod, "_find_git_root", _fake_git_root)
        dirs = _dirs_root_to_cwd(str(tmp_path))
        # Walk should have terminated via filesystem root (parent == current)
        assert len(dirs) >= 1

    def test_nested_under_git_root(self, tmp_path):
        """Lines 244-247: walk up from cwd to git root."""
        # Create fake git root
        root = tmp_path / "proj"
        root.mkdir()
        (root / ".git").mkdir()
        # Create nested directories
        nested = root / "a" / "b" / "c"
        nested.mkdir(parents=True)

        dirs = _dirs_root_to_cwd(str(nested))
        # Should contain root, a, a/b, a/b/c
        assert len(dirs) >= 3
        assert dirs[0] == root
        assert dirs[-1] == nested


class TestLoadInstructions:

    def test_load_success_happy_path(self, tmp_path):
        """Happy path for load_instructions — loads multiple files."""
        root = tmp_path / "proj"
        root.mkdir()
        (root / ".git").mkdir()
        (root / "DUH.md").write_text("duh instructions")
        (root / "AGENTS.md").write_text("agents instructions")
        rules = root / ".duh" / "rules"
        rules.mkdir(parents=True)
        (rules / "a.md").write_text("rule a")

        instructions = load_instructions(cwd=str(root))
        joined = "\n".join(instructions)
        assert "duh instructions" in joined
        assert "rule a" in joined
        assert "agents instructions" in joined


# ==========================================================================
# duh.hooks
# ==========================================================================

from duh.hooks import (
    HookConfig,
    HookEvent,
    HookResult,
    HookRegistry,
    HookType,
    execute_hooks,
    _execute_command_hook,
)


class TestHooksExecution:
    async def test_command_hook_kill_error_swallowed(self, monkeypatch):
        """Lines 232-233: proc.kill() raises Exception on timeout cleanup."""
        hook = HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.COMMAND,
            command="sleep 100",
            timeout=0.1,
            name="slow",
        )

        class _FakeProc:
            returncode = None

            async def communicate(self, input=None):
                await asyncio.sleep(10)
                return b"", b""

            def kill(self):
                raise OSError("kill failed")

        async def _fake_exec(*a, **kw):
            return _FakeProc()

        monkeypatch.setattr(
            "duh.hooks.asyncio.create_subprocess_shell", _fake_exec,
        )
        result = await _execute_command_hook(
            hook, HookEvent.PRE_TOOL_USE, {}, timeout=0.05,
        )
        assert result.success is False
        assert "timed out" in (result.error or "").lower()

    async def test_command_hook_general_exception(self, monkeypatch):
        """Lines 240-241: general Exception path in command hook execution."""
        hook = HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type=HookType.COMMAND,
            command="whatever",
            timeout=5,
            name="bad",
        )

        async def _raise(*a, **kw):
            raise RuntimeError("shell crashed")

        monkeypatch.setattr(
            "duh.hooks.asyncio.create_subprocess_shell", _raise,
        )
        result = await _execute_command_hook(
            hook, HookEvent.PRE_TOOL_USE, {}, timeout=5,
        )
        assert result.success is False
        assert "shell crashed" in (result.error or "")

    async def test_execute_hooks_no_executor_for_hook_type(self, monkeypatch):
        """Lines 452-459: hook_type has no registered executor."""
        registry = HookRegistry()
        # Create a hook with a fake hook_type not in _EXECUTORS
        hook = HookConfig(
            event=HookEvent.PRE_TOOL_USE,
            hook_type="FAKE_TYPE_NOT_REAL",  # type: ignore
            command="",
            name="noexec",
        )
        registry.register(hook)

        results = await execute_hooks(
            registry, HookEvent.PRE_TOOL_USE, {},
        )
        assert len(results) == 1
        assert results[0].success is False
        assert "No executor" in (results[0].error or "")
