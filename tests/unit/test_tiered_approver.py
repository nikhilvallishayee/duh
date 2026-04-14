"""Tests for the 3-tier approval model."""

import subprocess
from unittest.mock import patch

import pytest

from duh.adapters.approvers import ApprovalMode, TieredApprover, _is_dangerous_git_command


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


class TestIsDangerousGitCommand:
    """Unit tests for the _is_dangerous_git_command helper."""

    def test_force_push_long_flag(self):
        assert _is_dangerous_git_command("git push origin main --force") is True

    def test_force_push_short_flag(self):
        assert _is_dangerous_git_command("git push -f") is True

    def test_force_push_with_remote_branch(self):
        assert _is_dangerous_git_command("git push origin feature/my-branch --force") is True

    def test_reset_hard(self):
        assert _is_dangerous_git_command("git reset --hard HEAD~1") is True

    def test_reset_hard_short(self):
        assert _is_dangerous_git_command("git reset --hard") is True

    def test_clean_f(self):
        assert _is_dangerous_git_command("git clean -f") is True

    def test_clean_fd(self):
        assert _is_dangerous_git_command("git clean -fd") is True

    def test_clean_fxd(self):
        assert _is_dangerous_git_command("git clean -fxd") is True

    def test_branch_delete_capital(self):
        assert _is_dangerous_git_command("git branch -D my-feature") is True

    def test_safe_push(self):
        assert _is_dangerous_git_command("git push origin main") is False

    def test_safe_reset_soft(self):
        assert _is_dangerous_git_command("git reset --soft HEAD~1") is False

    def test_safe_reset_mixed(self):
        assert _is_dangerous_git_command("git reset HEAD~1") is False

    def test_safe_branch_delete_lowercase(self):
        """git branch -d (lowercase) is safe — it refuses to delete unmerged branches."""
        assert _is_dangerous_git_command("git branch -d old-branch") is False

    def test_safe_commit(self):
        assert _is_dangerous_git_command("git commit -m 'fix'") is False

    def test_safe_status(self):
        assert _is_dangerous_git_command("git status") is False

    def test_non_git_command(self):
        assert _is_dangerous_git_command("rm -rf /") is False

    def test_empty_command(self):
        assert _is_dangerous_git_command("") is False


class TestTieredApproverGitSafetyCheck:
    """The git safety check must block dangerous commands across ALL tiers."""

    async def test_force_push_blocked_in_suggest(self):
        approver = TieredApprover(mode=ApprovalMode.SUGGEST)
        result = await approver.check("Bash", {"command": "git push --force"})
        assert result["allowed"] is False
        assert "git safety" in result["reason"].lower() or "dangerous git" in result["reason"].lower()

    async def test_force_push_blocked_in_auto_edit(self):
        approver = TieredApprover(mode=ApprovalMode.AUTO_EDIT)
        result = await approver.check("Bash", {"command": "git push origin main --force"})
        assert result["allowed"] is False

    async def test_force_push_blocked_in_full_auto(self):
        """Critical: even FULL_AUTO must not allow force push."""
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("Bash", {"command": "git push --force"})
        assert result["allowed"] is False

    async def test_reset_hard_blocked_in_full_auto(self):
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("Bash", {"command": "git reset --hard HEAD~3"})
        assert result["allowed"] is False

    async def test_clean_blocked_in_full_auto(self):
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("Bash", {"command": "git clean -fd"})
        assert result["allowed"] is False

    async def test_branch_delete_blocked_in_full_auto(self):
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("Bash", {"command": "git branch -D feature/old"})
        assert result["allowed"] is False

    async def test_safe_git_allowed_in_full_auto(self):
        """Safe git commands should not be blocked."""
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("Bash", {"command": "git status"})
        assert result["allowed"] is True

    async def test_safe_git_push_allowed_in_full_auto(self):
        """Normal push without --force should pass."""
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("Bash", {"command": "git push origin main"})
        assert result["allowed"] is True

    async def test_non_bash_tool_not_affected(self):
        """Git safety check only applies to the Bash tool."""
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        # Write tool named "git push --force" (contrived) should not be caught
        result = await approver.check("Write", {"file_path": "git push --force"})
        assert result["allowed"] is True

    async def test_reason_mentions_blocked_commands(self):
        """Error message should be informative."""
        approver = TieredApprover(mode=ApprovalMode.FULL_AUTO)
        result = await approver.check("Bash", {"command": "git reset --hard"})
        assert result["allowed"] is False
        reason = result["reason"]
        assert "git" in reason.lower()
        assert len(reason) > 20  # substantive message
