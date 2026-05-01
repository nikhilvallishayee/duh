"""Tests for duh.duhwave.coordinator.role — Role + BUILTIN_ROLES."""

from __future__ import annotations

import pytest

from duh.duhwave.coordinator.role import BUILTIN_ROLES, Role


# ---------------------------------------------------------------------------
# Built-in presets
# ---------------------------------------------------------------------------


class TestBuiltinRoles:
    def test_coordinator_present(self):
        assert "coordinator" in BUILTIN_ROLES
        c = BUILTIN_ROLES["coordinator"]
        assert c.name == "coordinator"
        assert c.spawn_depth == 1

    def test_worker_present(self):
        assert "worker" in BUILTIN_ROLES
        w = BUILTIN_ROLES["worker"]
        assert w.name == "worker"
        assert w.spawn_depth == 0

    def test_coordinator_excludes_execution_tools(self):
        c = BUILTIN_ROLES["coordinator"]
        for forbidden in ("Bash", "Edit", "Write"):
            assert forbidden not in c.tool_allowlist, (
                f"coordinator must not have {forbidden} in allowlist"
            )

    def test_coordinator_includes_delegation_tools(self):
        c = BUILTIN_ROLES["coordinator"]
        for required in ("Spawn",):
            assert required in c.tool_allowlist

    def test_coordinator_includes_readonly_rlm_tools(self):
        c = BUILTIN_ROLES["coordinator"]
        for required in ("Peek", "Search", "Slice"):
            assert required in c.tool_allowlist

    def test_worker_includes_execution_tools(self):
        w = BUILTIN_ROLES["worker"]
        for required in ("Bash", "Edit", "Write", "Read"):
            assert required in w.tool_allowlist

    def test_worker_excludes_spawn(self):
        w = BUILTIN_ROLES["worker"]
        assert "Spawn" not in w.tool_allowlist


# ---------------------------------------------------------------------------
# from_dict round-trip
# ---------------------------------------------------------------------------


class TestFromDict:
    def test_round_trip(self):
        original = Role(
            name="reviewer",
            system_prompt="be picky",
            tool_allowlist=("Read", "Grep"),
            spawn_depth=0,
        )
        d = {
            "name": original.name,
            "system_prompt": original.system_prompt,
            "tool_allowlist": list(original.tool_allowlist),
            "spawn_depth": original.spawn_depth,
        }
        restored = Role.from_dict(d)
        assert restored == original

    def test_from_dict_defaults(self):
        # Only name is required; other fields default.
        r = Role.from_dict({"name": "minimal"})
        assert r.name == "minimal"
        assert r.system_prompt == ""
        assert r.tool_allowlist == ()
        assert r.spawn_depth == 0

    def test_from_dict_coerces_tool_list_to_tuple_of_str(self):
        r = Role.from_dict({"name": "x", "tool_allowlist": ["Read", 7]})
        assert r.tool_allowlist == ("Read", "7")


# ---------------------------------------------------------------------------
# child_role
# ---------------------------------------------------------------------------


class TestChildRole:
    def test_coordinator_can_spawn_one_child(self):
        c = BUILTIN_ROLES["coordinator"]
        assert c.spawn_depth == 1
        child = c.child_role()
        assert child.spawn_depth == 0
        assert child.name == "worker"
        # Child has worker tools, not coordinator tools.
        assert "Bash" in child.tool_allowlist
        assert "Spawn" not in child.tool_allowlist

    def test_child_role_with_custom_name(self):
        c = BUILTIN_ROLES["coordinator"]
        child = c.child_role(name="researcher")
        assert child.name == "researcher"
        # Still gets worker tools by default.
        assert "Read" in child.tool_allowlist

    def test_worker_cannot_spawn(self):
        w = BUILTIN_ROLES["worker"]
        with pytest.raises(ValueError, match="no spawn budget left"):
            w.child_role()

    def test_child_cannot_spawn_grandchild(self):
        c = BUILTIN_ROLES["coordinator"]
        child = c.child_role()
        # Child has spawn_depth=0 — so it cannot spawn its own child.
        with pytest.raises(ValueError, match="no spawn budget left"):
            child.child_role()
