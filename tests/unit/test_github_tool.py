"""Tests for GitHubTool — PR workflow via gh CLI."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from duh.kernel.tool import Tool, ToolContext, ToolResult
from duh.tools.github_tool import GitHubTool, _gh_available, _run_gh, _GH_MISSING_MSG


def ctx() -> ToolContext:
    return ToolContext(cwd="/tmp")


# ===========================================================================
# Protocol conformance
# ===========================================================================

class TestGitHubToolProtocol:

    def test_satisfies_tool_protocol(self):
        tool = GitHubTool()
        assert isinstance(tool, Tool)

    def test_name(self):
        assert GitHubTool().name == "GitHub"

    def test_description_non_empty(self):
        assert GitHubTool().description

    def test_input_schema_structure(self):
        schema = GitHubTool().input_schema
        assert schema["type"] == "object"
        assert "action" in schema["properties"]
        assert "action" in schema["required"]

    def test_input_schema_enum_actions(self):
        actions = GitHubTool().input_schema["properties"]["action"]["enum"]
        assert set(actions) == {"pr_list", "pr_create", "pr_view", "pr_diff", "pr_checks"}

    def test_is_not_read_only(self):
        # pr_create mutates, so the tool overall is not read-only
        assert GitHubTool().is_read_only is False

    def test_is_not_destructive(self):
        assert GitHubTool().is_destructive is False

    async def test_check_permissions_read_actions(self):
        tool = GitHubTool()
        for action in ("pr_list", "pr_view", "pr_diff", "pr_checks"):
            result = await tool.check_permissions({"action": action}, ctx())
            assert result["allowed"] is True

    async def test_check_permissions_create_needs_approval(self):
        tool = GitHubTool()
        result = await tool.check_permissions({"action": "pr_create"}, ctx())
        assert result["allowed"] is True
        assert result.get("needs_approval") is True


# ===========================================================================
# gh CLI availability
# ===========================================================================

class TestGhAvailability:

    async def test_gh_not_installed_returns_error(self):
        tool = GitHubTool()
        with patch("duh.tools.github_tool._gh_available", return_value=False):
            result = await tool.call({"action": "pr_list"}, ctx())
        assert result.is_error is True
        assert "brew install gh" in result.output

    def test_gh_available_uses_shutil_which(self):
        with patch("duh.tools.github_tool.shutil.which", return_value="/usr/local/bin/gh"):
            assert _gh_available() is True
        with patch("duh.tools.github_tool.shutil.which", return_value=None):
            assert _gh_available() is False


# ===========================================================================
# Unknown action
# ===========================================================================

class TestUnknownAction:

    async def test_unknown_action_is_error(self):
        tool = GitHubTool()
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            result = await tool.call({"action": "pr_merge"}, ctx())
        assert result.is_error is True
        assert "Unknown action" in result.output
        assert "pr_merge" in result.output


# ===========================================================================
# pr_list
# ===========================================================================

class TestPrList:
    tool = GitHubTool()

    async def test_pr_list_success(self):
        prs = [
            {"number": 1, "title": "Add feature", "state": "OPEN",
             "author": {"login": "alice"}},
            {"number": 2, "title": "Fix bug", "state": "MERGED",
             "author": {"login": "bob"}},
        ]
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            with patch("duh.tools.github_tool._run_gh",
                       return_value=(json.dumps(prs), "", 0)):
                result = await self.tool.call({"action": "pr_list"}, ctx())

        assert result.is_error is False
        assert "#1" in result.output
        assert "Add feature" in result.output
        assert "alice" in result.output
        assert "#2" in result.output
        assert result.metadata["count"] == 2

    async def test_pr_list_empty(self):
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            with patch("duh.tools.github_tool._run_gh",
                       return_value=("[]", "", 0)):
                result = await self.tool.call({"action": "pr_list"}, ctx())

        assert result.is_error is False
        assert "No pull requests" in result.output

    async def test_pr_list_with_state_filter(self):
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            with patch("duh.tools.github_tool._run_gh",
                       return_value=("[]", "", 0)) as mock_run:
                await self.tool.call(
                    {"action": "pr_list", "state": "closed"}, ctx()
                )
        args_passed = mock_run.call_args[0][0]
        assert "--state" in args_passed
        assert "closed" in args_passed

    async def test_pr_list_with_limit(self):
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            with patch("duh.tools.github_tool._run_gh",
                       return_value=("[]", "", 0)) as mock_run:
                await self.tool.call(
                    {"action": "pr_list", "limit": 5}, ctx()
                )
        args_passed = mock_run.call_args[0][0]
        assert "--limit" in args_passed
        assert "5" in args_passed

    async def test_pr_list_gh_failure(self):
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            with patch("duh.tools.github_tool._run_gh",
                       return_value=("", "not a git repository", 1)):
                result = await self.tool.call({"action": "pr_list"}, ctx())

        assert result.is_error is True
        assert "not a git repository" in result.output


# ===========================================================================
# pr_create
# ===========================================================================

class TestPrCreate:
    tool = GitHubTool()

    async def test_pr_create_success(self):
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            with patch("duh.tools.github_tool._run_gh",
                       return_value=("https://github.com/user/repo/pull/42\n", "", 0)):
                result = await self.tool.call(
                    {"action": "pr_create", "title": "My PR", "body": "Description"},
                    ctx(),
                )

        assert result.is_error is False
        assert "github.com" in result.output
        assert result.metadata["action"] == "pr_create"

    async def test_pr_create_missing_title(self):
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            result = await self.tool.call({"action": "pr_create"}, ctx())

        assert result.is_error is True
        assert "title is required" in result.output

    async def test_pr_create_with_base(self):
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            with patch("duh.tools.github_tool._run_gh",
                       return_value=("PR created\n", "", 0)) as mock_run:
                await self.tool.call(
                    {"action": "pr_create", "title": "My PR", "base": "develop"},
                    ctx(),
                )
        args_passed = mock_run.call_args[0][0]
        assert "--base" in args_passed
        assert "develop" in args_passed

    async def test_pr_create_gh_failure(self):
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            with patch("duh.tools.github_tool._run_gh",
                       return_value=("", "no commits between main and feature", 1)):
                result = await self.tool.call(
                    {"action": "pr_create", "title": "My PR"}, ctx()
                )

        assert result.is_error is True
        assert "no commits" in result.output


# ===========================================================================
# pr_view
# ===========================================================================

class TestPrView:
    tool = GitHubTool()

    async def test_pr_view_success(self):
        data = {
            "title": "Add feature X",
            "body": "This adds feature X.",
            "state": "OPEN",
            "reviews": [
                {"author": {"login": "reviewer1"}, "state": "APPROVED"},
            ],
        }
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            with patch("duh.tools.github_tool._run_gh",
                       return_value=(json.dumps(data), "", 0)):
                result = await self.tool.call(
                    {"action": "pr_view", "number": 42}, ctx()
                )

        assert result.is_error is False
        assert "Add feature X" in result.output
        assert "OPEN" in result.output
        assert "reviewer1" in result.output
        assert "APPROVED" in result.output

    async def test_pr_view_missing_number(self):
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            result = await self.tool.call({"action": "pr_view"}, ctx())

        assert result.is_error is True
        assert "number is required" in result.output


# ===========================================================================
# pr_diff
# ===========================================================================

class TestPrDiff:
    tool = GitHubTool()

    async def test_pr_diff_success(self):
        diff_output = "diff --git a/file.py b/file.py\n+added line\n"
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            with patch("duh.tools.github_tool._run_gh",
                       return_value=(diff_output, "", 0)):
                result = await self.tool.call(
                    {"action": "pr_diff", "number": 10}, ctx()
                )

        assert result.is_error is False
        assert "+added line" in result.output
        assert result.metadata["number"] == 10

    async def test_pr_diff_missing_number(self):
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            result = await self.tool.call({"action": "pr_diff"}, ctx())

        assert result.is_error is True
        assert "number is required" in result.output


# ===========================================================================
# pr_checks
# ===========================================================================

class TestPrChecks:
    tool = GitHubTool()

    async def test_pr_checks_success(self):
        checks_output = "CI / build (push)\tpass\t1m30s\nhttps://...\n"
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            with patch("duh.tools.github_tool._run_gh",
                       return_value=(checks_output, "", 0)):
                result = await self.tool.call(
                    {"action": "pr_checks", "number": 7}, ctx()
                )

        assert result.is_error is False
        assert "CI / build" in result.output
        assert result.metadata["number"] == 7

    async def test_pr_checks_missing_number(self):
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            result = await self.tool.call({"action": "pr_checks"}, ctx())

        assert result.is_error is True
        assert "number is required" in result.output

    async def test_pr_checks_gh_failure(self):
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            with patch("duh.tools.github_tool._run_gh",
                       return_value=("", "no checks reported", 1)):
                result = await self.tool.call(
                    {"action": "pr_checks", "number": 99}, ctx()
                )

        assert result.is_error is True
        assert "no checks reported" in result.output


# ===========================================================================
# _run_gh helper
# ===========================================================================

class TestRunGh:

    def test_timeout_handling(self):
        import subprocess
        with patch("duh.tools.github_tool.subprocess.run",
                   side_effect=subprocess.TimeoutExpired(cmd="gh", timeout=30)):
            stdout, stderr, rc = _run_gh(["pr", "list"])
        assert rc == 1
        assert "timed out" in stderr

    def test_oserror_handling(self):
        with patch("duh.tools.github_tool.subprocess.run",
                   side_effect=OSError("Permission denied")):
            stdout, stderr, rc = _run_gh(["pr", "list"])
        assert rc == 1
        assert "Permission denied" in stderr


# ===========================================================================
# /pr REPL command
# ===========================================================================

class TestPrReplCommand:

    def test_pr_in_slash_commands(self):
        from duh.cli.repl import SLASH_COMMANDS
        assert "/pr" in SLASH_COMMANDS

    def test_handle_pr_no_args_shows_usage(self, capsys):
        from duh.cli.repl import _handle_pr_command
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            _handle_pr_command("")
        captured = capsys.readouterr()
        assert "/pr list" in captured.out
        assert "/pr view" in captured.out

    def test_handle_pr_gh_missing(self, capsys):
        from duh.cli.repl import _handle_pr_command
        with patch("duh.tools.github_tool._gh_available", return_value=False):
            _handle_pr_command("list")
        captured = capsys.readouterr()
        assert "brew install gh" in captured.out

    def test_handle_pr_unknown_sub(self, capsys):
        from duh.cli.repl import _handle_pr_command
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            _handle_pr_command("merge")
        captured = capsys.readouterr()
        assert "Unknown" in captured.out

    def test_handle_pr_view_missing_number(self, capsys):
        from duh.cli.repl import _handle_pr_command
        with patch("duh.tools.github_tool._gh_available", return_value=True):
            _handle_pr_command("view")
        captured = capsys.readouterr()
        assert "Usage" in captured.out


# ===========================================================================
# Registry integration
# ===========================================================================

class TestRegistryIntegration:

    def test_github_tool_in_registry(self):
        from duh.tools.registry import get_all_tools
        tools = get_all_tools()
        names = [t.name for t in tools]
        assert "GitHub" in names
