"""Tests for PathPolicy wiring from CLI runners to tools (SEC-HIGH-3).

Verifies that:
- WriteTool, EditTool, ReadTool, MultiEditTool block operations outside the project
  boundary when constructed with a PathPolicy.
- check_permissions returns allowed=False for out-of-boundary paths.
- get_all_tools() propagates path_policy to file tools.
- Tools constructed without path_policy still work (backward compat).
"""

from __future__ import annotations

import pytest

from duh.kernel.tool import ToolContext
from duh.security.path_policy import PathPolicy
from duh.tools.read import ReadTool
from duh.tools.write import WriteTool
from duh.tools.edit import EditTool
from duh.tools.multi_edit import MultiEditTool
from duh.tools.registry import get_all_tools


def _ctx(cwd: str = "/tmp") -> ToolContext:
    return ToolContext(cwd=cwd)


@pytest.fixture()
def project_dir(tmp_path):
    """Create a minimal project with a file inside."""
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hello')")
    return tmp_path


@pytest.fixture()
def policy(project_dir):
    return PathPolicy(str(project_dir), allowed_paths=[])


# ---------------------------------------------------------------------------
# WriteTool boundary enforcement
# ---------------------------------------------------------------------------


class TestWriteToolBoundary:
    async def test_write_outside_project_blocked_by_call(self, policy):
        tool = WriteTool(path_policy=policy)
        result = await tool.call(
            {"file_path": "/etc/evil.txt", "content": "pwned"},
            _ctx(),
        )
        assert result.is_error
        assert "outside project boundary" in result.output

    async def test_write_inside_project_allowed(self, policy, project_dir):
        tool = WriteTool(path_policy=policy)
        target = str(project_dir / "new_file.txt")
        result = await tool.call(
            {"file_path": target, "content": "ok"},
            _ctx(str(project_dir)),
        )
        assert not result.is_error

    async def test_check_permissions_blocks_outside(self, policy):
        tool = WriteTool(path_policy=policy)
        perm = await tool.check_permissions(
            {"file_path": "/etc/passwd", "content": "x"},
            _ctx(),
        )
        assert perm["allowed"] is False

    async def test_check_permissions_allows_inside(self, policy, project_dir):
        tool = WriteTool(path_policy=policy)
        perm = await tool.check_permissions(
            {"file_path": str(project_dir / "new.txt"), "content": "x"},
            _ctx(str(project_dir)),
        )
        assert perm["allowed"] is True


# ---------------------------------------------------------------------------
# EditTool boundary enforcement
# ---------------------------------------------------------------------------


class TestEditToolBoundary:
    async def test_edit_outside_project_blocked_by_call(self, policy):
        tool = EditTool(path_policy=policy)
        result = await tool.call(
            {
                "file_path": "/etc/passwd",
                "old_string": "root",
                "new_string": "pwned",
            },
            _ctx(),
        )
        assert result.is_error
        assert "outside project boundary" in result.output

    async def test_check_permissions_blocks_outside(self, policy):
        tool = EditTool(path_policy=policy)
        perm = await tool.check_permissions(
            {
                "file_path": "/etc/passwd",
                "old_string": "x",
                "new_string": "y",
            },
            _ctx(),
        )
        assert perm["allowed"] is False


# ---------------------------------------------------------------------------
# ReadTool boundary enforcement
# ---------------------------------------------------------------------------


class TestReadToolBoundary:
    async def test_read_outside_project_blocked_by_call(self, policy):
        tool = ReadTool(path_policy=policy)
        result = await tool.call(
            {"file_path": "/etc/passwd"},
            _ctx(),
        )
        assert result.is_error
        assert "outside project boundary" in result.output

    async def test_check_permissions_blocks_outside(self, policy):
        tool = ReadTool(path_policy=policy)
        perm = await tool.check_permissions(
            {"file_path": "/etc/passwd"},
            _ctx(),
        )
        assert perm["allowed"] is False

    async def test_read_inside_project_allowed(self, policy, project_dir):
        tool = ReadTool(path_policy=policy)
        target = str(project_dir / "src" / "main.py")
        result = await tool.call(
            {"file_path": target},
            _ctx(str(project_dir)),
        )
        assert not result.is_error
        assert "hello" in result.output


# ---------------------------------------------------------------------------
# MultiEditTool boundary enforcement
# ---------------------------------------------------------------------------


class TestMultiEditToolBoundary:
    async def test_multi_edit_outside_project_blocked(self, policy):
        tool = MultiEditTool(path_policy=policy)
        result = await tool.call(
            {
                "edits": [{
                    "file_path": "/etc/passwd",
                    "old_string": "root",
                    "new_string": "pwned",
                }],
            },
            _ctx(),
        )
        assert result.is_error
        assert "outside project boundary" in result.output

    async def test_check_permissions_blocks_outside(self, policy):
        tool = MultiEditTool(path_policy=policy)
        perm = await tool.check_permissions(
            {
                "edits": [{
                    "file_path": "/etc/passwd",
                    "old_string": "x",
                    "new_string": "y",
                }],
            },
            _ctx(),
        )
        assert perm["allowed"] is False


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


class TestRegistryPathPolicyWiring:
    def test_tools_have_path_policy_when_provided(self, policy):
        tools = get_all_tools(path_policy=policy)
        names = {t.name: t for t in tools}

        for tool_name in ("Read", "Write", "Edit", "MultiEdit"):
            tool = names.get(tool_name)
            assert tool is not None, f"{tool_name} missing from registry"
            assert tool._path_policy is policy, f"{tool_name} lacks PathPolicy"

    def test_tools_have_no_path_policy_by_default(self):
        tools = get_all_tools()
        names = {t.name: t for t in tools}

        for tool_name in ("Read", "Write", "Edit", "MultiEdit"):
            tool = names.get(tool_name)
            assert tool is not None
            assert tool._path_policy is None


# ---------------------------------------------------------------------------
# Backward compat — no policy = no boundary check
# ---------------------------------------------------------------------------


class TestNoPolicyBackwardCompat:
    async def test_write_no_policy(self, tmp_path):
        tool = WriteTool()
        result = await tool.call(
            {"file_path": str(tmp_path / "any.txt"), "content": "ok"},
            _ctx(str(tmp_path)),
        )
        assert not result.is_error

    async def test_edit_no_policy(self, tmp_path):
        target = tmp_path / "target.txt"
        target.write_text("before")
        tool = EditTool()
        result = await tool.call(
            {
                "file_path": str(target),
                "old_string": "before",
                "new_string": "after",
            },
            _ctx(str(tmp_path)),
        )
        assert not result.is_error

    async def test_read_no_policy(self, tmp_path):
        target = tmp_path / "any.txt"
        target.write_text("content")
        tool = ReadTool()
        result = await tool.call(
            {"file_path": str(target)},
            _ctx(str(tmp_path)),
        )
        assert not result.is_error

    async def test_multi_edit_no_policy(self, tmp_path):
        target = tmp_path / "target.txt"
        target.write_text("before")
        tool = MultiEditTool()
        result = await tool.call(
            {
                "edits": [{
                    "file_path": str(target),
                    "old_string": "before",
                    "new_string": "after",
                }],
            },
            _ctx(str(tmp_path)),
        )
        assert not result.is_error
