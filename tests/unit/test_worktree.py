"""Tests for duh.tools.worktree — EnterWorktree and ExitWorktree tools."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from duh.kernel.tool import Tool, ToolContext, ToolResult
from duh.tools.worktree import (
    EnterWorktreeTool,
    ExitWorktreeTool,
    _META_IN_WORKTREE,
    _META_WORKTREE_BRANCH,
    _META_WORKTREE_ORIGINAL_CWD,
    _META_WORKTREE_PATH,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ctx(cwd: str = "/fake/repo", **meta: object) -> ToolContext:
    return ToolContext(cwd=cwd, metadata=dict(meta))


def _git_ok(stdout: str = "") -> tuple[int, str, str]:
    """Simulate a successful git command."""
    return (0, stdout, "")


def _git_fail(stderr: str = "error") -> tuple[int, str, str]:
    """Simulate a failed git command."""
    return (1, "", stderr)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocol:
    def test_enter_satisfies_tool_protocol(self):
        tool = EnterWorktreeTool()
        assert isinstance(tool, Tool)

    def test_exit_satisfies_tool_protocol(self):
        tool = ExitWorktreeTool()
        assert isinstance(tool, Tool)

    def test_enter_has_schema(self):
        tool = EnterWorktreeTool()
        assert tool.name == "EnterWorktree"
        assert tool.input_schema["type"] == "object"
        assert "properties" in tool.input_schema
        assert "required" in tool.input_schema

    def test_exit_has_schema(self):
        tool = ExitWorktreeTool()
        assert tool.name == "ExitWorktree"
        assert tool.input_schema["type"] == "object"
        assert "properties" in tool.input_schema
        assert "required" in tool.input_schema

    def test_enter_not_read_only(self):
        assert EnterWorktreeTool().is_read_only is False

    def test_exit_not_read_only(self):
        assert ExitWorktreeTool().is_read_only is False

    def test_enter_not_destructive(self):
        assert EnterWorktreeTool().is_destructive is False

    def test_exit_not_destructive(self):
        assert ExitWorktreeTool().is_destructive is False


# ---------------------------------------------------------------------------
# EnterWorktreeTool
# ---------------------------------------------------------------------------

class TestEnterWorktree:
    tool = EnterWorktreeTool()

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    @patch("duh.tools.worktree.Path.mkdir")
    async def test_creates_worktree_with_explicit_branch(self, mock_mkdir, mock_git):
        mock_git.side_effect = [
            _git_ok("true"),          # rev-parse --is-inside-work-tree
            _git_ok(""),              # worktree add
        ]
        c = ctx()
        result = await self.tool.call(
            {"branch": "feat-x", "path": "/tmp/duh-worktrees/feat-x"}, c
        )
        assert result.is_error is False
        assert "feat-x" in result.output
        assert c.cwd == "/tmp/duh-worktrees/feat-x"
        assert c.metadata[_META_IN_WORKTREE] is True
        assert c.metadata[_META_WORKTREE_BRANCH] == "feat-x"
        assert result.metadata["branch"] == "feat-x"

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    @patch("duh.tools.worktree.Path.mkdir")
    async def test_auto_generates_branch_name(self, mock_mkdir, mock_git):
        mock_git.side_effect = [
            _git_ok("true"),
            _git_ok(""),
        ]
        c = ctx()
        result = await self.tool.call({}, c)
        assert result.is_error is False
        branch = c.metadata[_META_WORKTREE_BRANCH]
        assert branch.startswith("duh-worktree-")
        assert len(branch) > len("duh-worktree-")

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    @patch("duh.tools.worktree.Path.mkdir")
    async def test_default_path_uses_branch_name(self, mock_mkdir, mock_git):
        mock_git.side_effect = [
            _git_ok("true"),
            _git_ok(""),
        ]
        c = ctx()
        result = await self.tool.call({"branch": "my-branch"}, c)
        assert result.is_error is False
        assert c.cwd == "/tmp/duh-worktrees/my-branch"

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    async def test_not_in_git_repo(self, mock_git):
        mock_git.return_value = _git_fail("not a git repo")
        result = await self.tool.call({}, ctx())
        assert result.is_error is True
        assert "not inside a git repository" in result.output.lower()

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    @patch("duh.tools.worktree.Path.mkdir")
    async def test_git_worktree_add_fails(self, mock_mkdir, mock_git):
        mock_git.side_effect = [
            _git_ok("true"),
            _git_fail("branch already exists"),
        ]
        result = await self.tool.call({"branch": "existing"}, ctx())
        assert result.is_error is True
        assert "failed to create worktree" in result.output.lower()

    async def test_rejects_nested_worktree(self):
        c = ctx()
        c.metadata[_META_IN_WORKTREE] = True
        result = await self.tool.call({}, c)
        assert result.is_error is True
        assert "already inside" in result.output.lower()

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    @patch("duh.tools.worktree.Path.mkdir")
    async def test_stores_original_cwd(self, mock_mkdir, mock_git):
        mock_git.side_effect = [
            _git_ok("true"),
            _git_ok(""),
        ]
        c = ctx(cwd="/projects/myapp")
        await self.tool.call({"branch": "b1"}, c)
        assert c.metadata[_META_WORKTREE_ORIGINAL_CWD] == "/projects/myapp"

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    @patch("duh.tools.worktree.Path.mkdir")
    async def test_metadata_in_result(self, mock_mkdir, mock_git):
        mock_git.side_effect = [
            _git_ok("true"),
            _git_ok(""),
        ]
        c = ctx(cwd="/orig")
        result = await self.tool.call({"branch": "b2", "path": "/tmp/wt/b2"}, c)
        assert result.metadata["worktree_path"] == "/tmp/wt/b2"
        assert result.metadata["branch"] == "b2"
        assert result.metadata["original_cwd"] == "/orig"

    async def test_check_permissions_allowed(self):
        perm = await self.tool.check_permissions({}, ctx())
        assert perm["allowed"] is True


# ---------------------------------------------------------------------------
# ExitWorktreeTool
# ---------------------------------------------------------------------------

class TestExitWorktree:
    tool = ExitWorktreeTool()

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    async def test_exits_and_removes_worktree(self, mock_git):
        mock_git.return_value = _git_ok("")  # worktree remove succeeds
        c = ctx()
        c.metadata[_META_IN_WORKTREE] = True
        c.metadata[_META_WORKTREE_PATH] = "/tmp/duh-worktrees/feat"
        c.metadata[_META_WORKTREE_BRANCH] = "feat"
        c.metadata[_META_WORKTREE_ORIGINAL_CWD] = "/orig"

        result = await self.tool.call({"cleanup": True}, c)
        assert result.is_error is False
        assert c.cwd == "/orig"
        assert _META_IN_WORKTREE not in c.metadata
        assert result.metadata["cleaned_up"] is True
        assert "Removed worktree" in result.output

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    async def test_exits_without_cleanup(self, mock_git):
        c = ctx()
        c.metadata[_META_IN_WORKTREE] = True
        c.metadata[_META_WORKTREE_PATH] = "/tmp/duh-worktrees/feat"
        c.metadata[_META_WORKTREE_BRANCH] = "feat"
        c.metadata[_META_WORKTREE_ORIGINAL_CWD] = "/orig"

        result = await self.tool.call({"cleanup": False}, c)
        assert result.is_error is False
        assert c.cwd == "/orig"
        assert _META_IN_WORKTREE not in c.metadata
        # git worktree remove should NOT have been called
        mock_git.assert_not_called()

    async def test_error_when_not_in_worktree(self):
        result = await self.tool.call({}, ctx())
        assert result.is_error is True
        assert "not currently inside" in result.output.lower()

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    async def test_cleanup_failure_is_non_fatal(self, mock_git):
        mock_git.return_value = _git_fail("worktree is dirty")
        c = ctx()
        c.metadata[_META_IN_WORKTREE] = True
        c.metadata[_META_WORKTREE_PATH] = "/tmp/duh-worktrees/dirty"
        c.metadata[_META_WORKTREE_BRANCH] = "dirty"
        c.metadata[_META_WORKTREE_ORIGINAL_CWD] = "/orig"

        result = await self.tool.call({"cleanup": True}, c)
        # Still exits successfully even though cleanup failed
        assert result.is_error is False
        assert c.cwd == "/orig"
        assert _META_IN_WORKTREE not in c.metadata
        assert result.metadata["cleaned_up"] is False
        assert "Could not remove" in result.output

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    async def test_default_cleanup_is_true(self, mock_git):
        mock_git.return_value = _git_ok("")
        c = ctx()
        c.metadata[_META_IN_WORKTREE] = True
        c.metadata[_META_WORKTREE_PATH] = "/tmp/duh-worktrees/x"
        c.metadata[_META_WORKTREE_BRANCH] = "x"
        c.metadata[_META_WORKTREE_ORIGINAL_CWD] = "/orig"

        result = await self.tool.call({}, c)
        assert result.is_error is False
        # Should have attempted removal (default cleanup=True)
        mock_git.assert_called_once()
        assert result.metadata["cleaned_up"] is True

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    async def test_clears_all_metadata_keys(self, mock_git):
        mock_git.return_value = _git_ok("")
        c = ctx()
        c.metadata[_META_IN_WORKTREE] = True
        c.metadata[_META_WORKTREE_PATH] = "/tmp/wt"
        c.metadata[_META_WORKTREE_BRANCH] = "b"
        c.metadata[_META_WORKTREE_ORIGINAL_CWD] = "/orig"
        c.metadata["unrelated_key"] = "keep me"

        await self.tool.call({}, c)
        assert _META_IN_WORKTREE not in c.metadata
        assert _META_WORKTREE_PATH not in c.metadata
        assert _META_WORKTREE_BRANCH not in c.metadata
        assert _META_WORKTREE_ORIGINAL_CWD not in c.metadata
        assert c.metadata["unrelated_key"] == "keep me"

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    async def test_result_metadata(self, mock_git):
        mock_git.return_value = _git_ok("")
        c = ctx()
        c.metadata[_META_IN_WORKTREE] = True
        c.metadata[_META_WORKTREE_PATH] = "/tmp/wt/feat"
        c.metadata[_META_WORKTREE_BRANCH] = "feat"
        c.metadata[_META_WORKTREE_ORIGINAL_CWD] = "/orig"

        result = await self.tool.call({}, c)
        assert result.metadata["original_cwd"] == "/orig"
        assert result.metadata["worktree_path"] == "/tmp/wt/feat"
        assert result.metadata["branch"] == "feat"

    async def test_check_permissions_allowed(self):
        perm = await self.tool.check_permissions({}, ctx())
        assert perm["allowed"] is True


# ---------------------------------------------------------------------------
# Round-trip: enter then exit
# ---------------------------------------------------------------------------

class TestRoundTrip:
    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    @patch("duh.tools.worktree.Path.mkdir")
    async def test_enter_then_exit_restores_state(self, mock_mkdir, mock_git):
        # enter succeeds, then exit with cleanup succeeds
        mock_git.side_effect = [
            _git_ok("true"),   # rev-parse
            _git_ok(""),       # worktree add
            _git_ok(""),       # worktree remove
        ]
        c = ctx(cwd="/projects/app")

        enter_result = await EnterWorktreeTool().call({"branch": "wip"}, c)
        assert enter_result.is_error is False
        assert c.cwd == "/tmp/duh-worktrees/wip"
        assert c.metadata[_META_IN_WORKTREE] is True

        exit_result = await ExitWorktreeTool().call({}, c)
        assert exit_result.is_error is False
        assert c.cwd == "/projects/app"
        assert _META_IN_WORKTREE not in c.metadata

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    @patch("duh.tools.worktree.Path.mkdir")
    async def test_enter_exit_enter_works(self, mock_mkdir, mock_git):
        """Can enter a new worktree after exiting a previous one."""
        mock_git.side_effect = [
            _git_ok("true"),   # rev-parse (1st enter)
            _git_ok(""),       # worktree add (1st enter)
            _git_ok(""),       # worktree remove (exit)
            _git_ok("true"),   # rev-parse (2nd enter)
            _git_ok(""),       # worktree add (2nd enter)
        ]
        c = ctx(cwd="/orig")

        await EnterWorktreeTool().call({"branch": "a"}, c)
        assert c.metadata[_META_IN_WORKTREE] is True

        await ExitWorktreeTool().call({}, c)
        assert _META_IN_WORKTREE not in c.metadata

        result = await EnterWorktreeTool().call({"branch": "b"}, c)
        assert result.is_error is False
        assert c.metadata[_META_WORKTREE_BRANCH] == "b"
