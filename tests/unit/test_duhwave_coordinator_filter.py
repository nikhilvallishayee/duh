"""Tests for duh.duhwave.coordinator.tool_filter — filter_tools_for_role."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from duh.duhwave.coordinator.role import BUILTIN_ROLES, Role
from duh.duhwave.coordinator.tool_filter import filter_tools_for_role


@dataclass
class _FakeTool:
    name: str


@dataclass
class _NamelessTool:
    """Defensive: registry entry without ``.name`` is silently skipped."""
    description: str = ""


# ---------------------------------------------------------------------------
# Basic filtering
# ---------------------------------------------------------------------------


class TestFiltering:
    def test_filters_to_allowlist(self):
        tools = [
            _FakeTool("Read"),
            _FakeTool("Bash"),
            _FakeTool("Edit"),
            _FakeTool("Spawn"),
        ]
        out = filter_tools_for_role(tools, BUILTIN_ROLES["coordinator"])
        names = [t.name for t in out]
        # Coordinator gets Spawn but not Bash/Edit/Read (Read isn't in the
        # coordinator preset; only Peek/Search/Slice/Spawn/SendMessage/Stop).
        assert "Spawn" in names
        assert "Bash" not in names
        assert "Edit" not in names

    def test_worker_keeps_execution_tools(self):
        tools = [
            _FakeTool("Read"),
            _FakeTool("Bash"),
            _FakeTool("Edit"),
            _FakeTool("Spawn"),
        ]
        out = filter_tools_for_role(tools, BUILTIN_ROLES["worker"])
        names = [t.name for t in out]
        assert "Read" in names
        assert "Bash" in names
        assert "Edit" in names
        # Worker cannot Spawn.
        assert "Spawn" not in names


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_allowlist_returns_empty(self):
        empty_role = Role(name="muted", system_prompt="", tool_allowlist=())
        tools = [_FakeTool("Read"), _FakeTool("Bash")]
        assert filter_tools_for_role(tools, empty_role) == []

    def test_empty_input_returns_empty(self):
        assert filter_tools_for_role([], BUILTIN_ROLES["coordinator"]) == []

    def test_preserves_input_order(self):
        # The role's allowlist contains all of these — verify the filter
        # respects input order rather than allowlist order.
        role = Role(
            name="rev",
            system_prompt="",
            tool_allowlist=("A", "B", "C", "D"),
        )
        tools = [_FakeTool("D"), _FakeTool("A"), _FakeTool("C"), _FakeTool("B")]
        out = filter_tools_for_role(tools, role)
        assert [t.name for t in out] == ["D", "A", "C", "B"]

    def test_skips_nameless_tools(self):
        role = Role(name="r", system_prompt="", tool_allowlist=("Read",))
        tools = [_NamelessTool(), _FakeTool("Read"), _NamelessTool()]
        out = filter_tools_for_role(tools, role)
        # Only the Read tool survives; the nameless ones are dropped without
        # crashing.
        assert len(out) == 1
        assert out[0].name == "Read"

    def test_unknown_tool_dropped(self):
        role = Role(name="r", system_prompt="", tool_allowlist=("Read",))
        tools = [_FakeTool("Read"), _FakeTool("UnknownTool")]
        out = filter_tools_for_role(tools, role)
        assert [t.name for t in out] == ["Read"]
