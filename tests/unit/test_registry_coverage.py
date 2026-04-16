"""Tests for duh.tools.registry — get_all_tools() coverage.

Focuses on the ImportError branches (except ImportError: pass) that are
currently uncovered, plus verifying skills/deferred_tools passthrough.
"""

from __future__ import annotations

import builtins
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

from duh.tools.registry import get_all_tools


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# All tool module paths imported in get_all_tools(), in order
_TOOL_MODULES = [
    "duh.tools.read",
    "duh.tools.write",
    "duh.tools.edit",
    "duh.tools.multi_edit",
    "duh.tools.bash",
    "duh.tools.glob_tool",
    "duh.tools.grep",
    "duh.tools.skill_tool",
    "duh.tools.tool_search",
    "duh.tools.web_fetch",
    "duh.tools.web_search",
    "duh.tools.task_tool",
    "duh.tools.worktree",
    "duh.tools.notebook_edit",
    "duh.tools.test_impact",
    "duh.tools.memory_tool",
    "duh.tools.http_tool",
    "duh.tools.docker_tool",
    "duh.tools.db_tool",
    "duh.tools.github_tool",
    "duh.tools.lsp_tool",
]


def _make_import_blocker(blocked_module: str):
    """Return a side_effect for builtins.__import__ that raises ImportError
    for exactly one module, letting everything else import normally."""
    real_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == blocked_module:
            raise ImportError(f"Simulated: {name} not available")
        return real_import(name, *args, **kwargs)

    return guarded_import


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestGetAllToolsHappyPath:

    def test_returns_nonempty(self):
        tools = get_all_tools()
        assert len(tools) > 0

    def test_all_tools_have_name(self):
        tools = get_all_tools()
        for t in tools:
            assert hasattr(t, "name"), f"{t} missing .name"

    def test_includes_core_tools(self):
        tools = get_all_tools()
        names = {t.name for t in tools}
        for expected in ["Read", "Write", "Edit", "Bash", "Glob", "Grep"]:
            assert expected in names, f"Expected {expected} in {names}"

    def test_includes_meta_tools(self):
        tools = get_all_tools()
        names = {t.name for t in tools}
        assert "Skill" in names
        assert "ToolSearch" in names


# ---------------------------------------------------------------------------
# ImportError branches (individual tool failures)
# ---------------------------------------------------------------------------


class TestImportErrorBranches:
    """Each test blocks one tool module and verifies the rest still load."""

    def test_read_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.read")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "Read" not in names
        # Other tools still present
        assert len(tools) > 0

    def test_write_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.write")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "Write" not in names
        assert len(tools) > 0

    def test_edit_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.edit")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "Edit" not in names

    def test_multi_edit_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.multi_edit")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "MultiEdit" not in names

    def test_bash_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.bash")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "Bash" not in names

    def test_glob_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.glob_tool")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "Glob" not in names

    def test_grep_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.grep")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "Grep" not in names

    def test_skill_tool_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.skill_tool")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "Skill" not in names

    def test_tool_search_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.tool_search")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "ToolSearch" not in names

    def test_web_fetch_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.web_fetch")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "WebFetch" not in names

    def test_web_search_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.web_search")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "WebSearch" not in names

    def test_task_tool_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.task_tool")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "Task" not in names

    def test_worktree_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.worktree")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "EnterWorktree" not in names
        assert "ExitWorktree" not in names

    def test_notebook_edit_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.notebook_edit")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "NotebookEdit" not in names

    def test_test_impact_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.test_impact")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "TestImpact" not in names

    def test_memory_tool_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.memory_tool")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "MemoryStore" not in names
        assert "MemoryRecall" not in names

    def test_http_tool_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.http_tool")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "HTTP" not in names

    def test_docker_tool_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.docker_tool")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "Docker" not in names

    def test_db_tool_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.db_tool")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "Database" not in names

    def test_github_tool_import_failure(self):
        with patch("builtins.__import__", side_effect=_make_import_blocker("duh.tools.github_tool")):
            tools = get_all_tools()
        names = {t.name for t in tools}
        assert "GitHub" not in names


# ---------------------------------------------------------------------------
# skills / deferred_tools passthrough
# ---------------------------------------------------------------------------


class TestParamsPassthrough:
    def test_skills_passed_to_skill_tool(self):
        """skills kwarg reaches the SkillTool instance."""
        fake_skill = MagicMock()
        fake_skill.name = "test-skill"
        tools = get_all_tools(skills=[fake_skill])
        skill_tools = [t for t in tools if getattr(t, "name", "") == "Skill"]
        assert len(skill_tools) == 1
        # SkillTool stores skills in _skills dict keyed by name
        st = skill_tools[0]
        assert "test-skill" in st._skills
        assert st._skills["test-skill"] is fake_skill

    def test_deferred_tools_passed_to_tool_search(self):
        """deferred_tools kwarg reaches the ToolSearchTool instance."""
        from duh.tools.tool_search import DeferredTool

        dt = DeferredTool(
            name="FakeTool",
            description="A fake tool",
            input_schema={"type": "object", "properties": {}},
            source="test",
        )
        tools = get_all_tools(deferred_tools=[dt])
        ts_tools = [t for t in tools if getattr(t, "name", "") == "ToolSearch"]
        assert len(ts_tools) == 1
        ts = ts_tools[0]
        # _tools is a dict keyed by name
        assert "FakeTool" in ts._tools
        assert ts._tools["FakeTool"] is dt

    def test_none_skills_defaults_to_empty(self):
        """skills=None results in SkillTool getting an empty list."""
        tools = get_all_tools(skills=None)
        skill_tools = [t for t in tools if getattr(t, "name", "") == "Skill"]
        assert len(skill_tools) == 1
        st = skill_tools[0]
        stored = getattr(st, "_skills", None) or getattr(st, "skills", None)
        assert stored is not None
        assert len(stored) == 0

    def test_none_deferred_tools_defaults_to_empty(self):
        """deferred_tools=None results in ToolSearchTool getting an empty list
        (though LSP may add itself)."""
        tools = get_all_tools(deferred_tools=None)
        ts_tools = [t for t in tools if getattr(t, "name", "") == "ToolSearch"]
        assert len(ts_tools) == 1


# ---------------------------------------------------------------------------
# All-failures edge case
# ---------------------------------------------------------------------------


class TestAllImportsFail:
    def test_returns_empty_list_when_everything_fails(self):
        """If every single tool module fails to import, we get an empty list."""
        real_import = builtins.__import__

        def block_all_tools(name, *args, **kwargs):
            if name.startswith("duh.tools."):
                raise ImportError(f"Simulated: {name}")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=block_all_tools):
            tools = get_all_tools()

        assert tools == []
