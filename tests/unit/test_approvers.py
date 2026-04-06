"""Exhaustive tests for duh.adapters.approvers — 100% coverage."""

import pytest
from duh.adapters.approvers import AutoApprover, RuleApprover


class TestAutoApprover:
    async def test_allows_any_tool(self):
        a = AutoApprover()
        result = await a.check("Bash", {"command": "rm -rf /"})
        assert result["allowed"] is True

    async def test_allows_empty_input(self):
        result = await AutoApprover().check("Read", {})
        assert result["allowed"] is True

    async def test_allows_unknown_tool(self):
        result = await AutoApprover().check("NonexistentTool", {"x": 1})
        assert result["allowed"] is True


class TestRuleApprover:
    async def test_no_rules_allows_all(self):
        a = RuleApprover()
        result = await a.check("Bash", {"command": "ls"})
        assert result["allowed"] is True

    async def test_denied_tool(self):
        a = RuleApprover(denied_tools={"Bash", "Write"})
        result = await a.check("Bash", {"command": "ls"})
        assert result["allowed"] is False
        assert "denied by policy" in result["reason"]

    async def test_allowed_tool_not_in_deny_list(self):
        a = RuleApprover(denied_tools={"Write"})
        result = await a.check("Read", {"path": "/tmp"})
        assert result["allowed"] is True

    async def test_denied_command_pattern(self):
        a = RuleApprover(denied_commands={"rm -rf", "curl|sh"})
        result = await a.check("Bash", {"command": "rm -rf /important"})
        assert result["allowed"] is False
        assert "rm -rf" in result["reason"]

    async def test_safe_command_allowed(self):
        a = RuleApprover(denied_commands={"rm -rf"})
        result = await a.check("Bash", {"command": "ls -la"})
        assert result["allowed"] is True

    async def test_denied_command_only_checks_bash(self):
        a = RuleApprover(denied_commands={"rm"})
        result = await a.check("Read", {"command": "rm"})  # not Bash tool
        assert result["allowed"] is True

    async def test_path_restriction_blocks(self):
        a = RuleApprover(allowed_paths=["/tmp", "/home/user/project"])
        result = await a.check("Read", {"path": "/etc/passwd"})
        assert result["allowed"] is False
        assert "outside allowed" in result["reason"]

    async def test_path_restriction_allows(self):
        a = RuleApprover(allowed_paths=["/tmp"])
        result = await a.check("Read", {"path": "/tmp/test.txt"})
        assert result["allowed"] is True

    async def test_path_checks_file_path_key(self):
        a = RuleApprover(allowed_paths=["/tmp"])
        result = await a.check("Write", {"file_path": "/etc/shadow"})
        assert result["allowed"] is False

    async def test_path_checks_file_path_allows(self):
        a = RuleApprover(allowed_paths=["/tmp"])
        result = await a.check("Write", {"file_path": "/tmp/output.txt"})
        assert result["allowed"] is True

    async def test_no_path_restriction_allows_any_path(self):
        a = RuleApprover()  # no allowed_paths
        result = await a.check("Read", {"path": "/etc/passwd"})
        assert result["allowed"] is True

    async def test_no_path_in_input_passes(self):
        a = RuleApprover(allowed_paths=["/tmp"])
        result = await a.check("Bash", {"command": "echo hi"})
        assert result["allowed"] is True

    async def test_combined_rules(self):
        a = RuleApprover(
            denied_tools={"Write"},
            denied_commands={"rm -rf"},
            allowed_paths=["/tmp"],
        )
        # Denied tool
        assert (await a.check("Write", {}))["allowed"] is False
        # Denied command
        assert (await a.check("Bash", {"command": "rm -rf /"}))["allowed"] is False
        # Path outside allowed
        assert (await a.check("Read", {"path": "/etc/x"}))["allowed"] is False
        # All rules pass
        assert (await a.check("Read", {"path": "/tmp/x"}))["allowed"] is True

    async def test_empty_denied_sets(self):
        a = RuleApprover(denied_tools=set(), denied_commands=set())
        result = await a.check("Bash", {"command": "anything"})
        assert result["allowed"] is True
