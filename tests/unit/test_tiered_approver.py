"""Tests for the 3-tier approval model."""

import subprocess
from unittest.mock import patch

import pytest

from duh.adapters.approvers import ApprovalMode, TieredApprover


class TestApprovalMode:
    def test_suggest_mode(self):
        assert ApprovalMode.SUGGEST.value == "suggest"

    def test_auto_edit_mode(self):
        assert ApprovalMode.AUTO_EDIT.value == "auto-edit"

    def test_full_auto_mode(self):
        assert ApprovalMode.FULL_AUTO.value == "full-auto"


class TestTieredApproverSuggestMode:
    """SUGGEST mode: only reads are auto-approved."""

    async def test_read_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("Read", {"file_path": "/tmp/x"})
        assert result["allowed"] is True

    async def test_glob_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("Glob", {"pattern": "*.py"})
        assert result["allowed"] is True

    async def test_grep_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("Grep", {"pattern": "foo"})
        assert result["allowed"] is True

    async def test_write_needs_approval(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("Write", {"file_path": "/tmp/x"})
        assert result["allowed"] is False
        assert "approval" in result["reason"].lower() or "suggest" in result["reason"].lower()

    async def test_bash_needs_approval(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("Bash", {"command": "ls"})
        assert result["allowed"] is False

    async def test_edit_needs_approval(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("Edit", {"file_path": "/tmp/x"})
        assert result["allowed"] is False


class TestTieredApproverAutoEditMode:
    """AUTO_EDIT mode: reads + writes auto-approved, commands need approval."""

    async def test_read_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        result = await approver.check("Read", {"file_path": "/tmp/x"})
        assert result["allowed"] is True

    async def test_write_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        result = await approver.check("Write", {"file_path": "/tmp/x"})
        assert result["allowed"] is True

    async def test_edit_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        result = await approver.check("Edit", {"file_path": "/tmp/x"})
        assert result["allowed"] is True

    async def test_multi_edit_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        result = await approver.check("MultiEdit", {"file_path": "/tmp/x"})
        assert result["allowed"] is True

    async def test_bash_needs_approval(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        result = await approver.check("Bash", {"command": "npm test"})
        assert result["allowed"] is False
        assert "approval" in result["reason"].lower() or "auto-edit" in result["reason"].lower()

    async def test_web_fetch_needs_approval(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        result = await approver.check("WebFetch", {"url": "https://example.com"})
        assert result["allowed"] is False


class TestTieredApproverFullAutoMode:
    """FULL_AUTO mode: everything auto-approved."""

    async def test_read_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("Read", {})
        assert result["allowed"] is True

    async def test_write_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("Write", {"file_path": "/tmp/x"})
        assert result["allowed"] is True

    async def test_bash_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("Bash", {"command": "rm -rf /"})
        assert result["allowed"] is True

    async def test_web_fetch_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("WebFetch", {"url": "https://example.com"})
        assert result["allowed"] is True

    async def test_unknown_tool_auto_approved(self):
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("SomeFutureTool", {"x": 1})
        assert result["allowed"] is True


class TestTieredApproverToolClassification:
    """Verify tool classification is correct."""

    async def test_read_tools_are_read_only(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        read_tools = ["Read", "Glob", "Grep", "ToolSearch", "WebSearch"]
        for tool in read_tools:
            result = await approver.check(tool, {})
            assert result["allowed"] is True, f"{tool} should be auto-approved in SUGGEST"

    async def test_write_tools_classified(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        write_tools = ["Write", "Edit", "MultiEdit", "NotebookEdit"]
        for tool in write_tools:
            result = await approver.check(tool, {})
            assert result["allowed"] is True, f"{tool} should be auto-approved in AUTO_EDIT"

    async def test_command_tools_classified(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        cmd_tools = ["Bash", "WebFetch"]
        for tool in cmd_tools:
            result = await approver.check(tool, {})
            assert result["allowed"] is False, f"{tool} should need approval in AUTO_EDIT"


class TestTieredApproverGitSafety:
    @patch("duh.adapters.approvers._is_git_repo", return_value=False)
    def test_warns_without_git(self, mock_git):
        """Should emit a warning if not in a git repo with auto-edit or full-auto."""
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            TieredApprover(mode=ApprovalMode.AUTO_EDIT, cwd="/tmp/no-git")
            git_warnings = [x for x in w if "git" in str(x.message).lower()]
            assert len(git_warnings) >= 1

    @patch("duh.adapters.approvers._is_git_repo", return_value=True)
    def test_no_warn_with_git(self, mock_git):
        """No warning when in a git repo."""
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            TieredApprover(mode=ApprovalMode.AUTO_EDIT, cwd="/tmp/has-git")
            git_warnings = [x for x in w if "git" in str(x.message).lower()]
            assert len(git_warnings) == 0

    def test_suggest_mode_no_git_warning(self):
        """SUGGEST mode doesn't need git safety warning."""
        import warnings
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            TieredApprover(mode=ApprovalMode.SUGGEST)
            git_warnings = [x for x in w if "git" in str(x.message).lower()]
            assert len(git_warnings) == 0
