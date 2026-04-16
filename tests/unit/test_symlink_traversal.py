"""Tests for SEC-CRITICAL-1: symlink traversal prevention.

Tools (Read, Write, Edit) must resolve symlinks before operating,
so that a symlink like  project/link -> /etc/shadow  is blocked
by the filesystem boundary (PathPolicy).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from duh.kernel.tool import ToolContext, ToolResult
from duh.security.path_policy import PathPolicy
from duh.tools.read import ReadTool
from duh.tools.write import WriteTool
from duh.tools.edit import EditTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _context(cwd: str) -> ToolContext:
    return ToolContext(cwd=cwd)


def _policy(root: str) -> PathPolicy:
    return PathPolicy(project_root=root, allowed_paths=[])


# ---------------------------------------------------------------------------
# ReadTool
# ---------------------------------------------------------------------------

class TestReadToolSymlinkTraversal:
    """ReadTool must resolve symlinks and reject those pointing outside."""

    @pytest.mark.asyncio
    async def test_symlink_outside_project_blocked(self, tmp_path: Path):
        """A symlink pointing outside the project root must be rejected."""
        project = tmp_path / "project"
        project.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "secret.txt"
        secret.write_text("top-secret")

        link = project / "innocent.txt"
        os.symlink(str(secret), str(link))

        tool = ReadTool(path_policy=_policy(str(project)))
        result = await tool.call(
            {"file_path": str(link)},
            _context(str(project)),
        )

        assert result.is_error is True
        assert "outside project boundary" in result.output

    @pytest.mark.asyncio
    async def test_symlink_inside_project_allowed(self, tmp_path: Path):
        """A symlink pointing within the project root must be allowed."""
        project = tmp_path / "project"
        project.mkdir()

        real_file = project / "real.txt"
        real_file.write_text("hello\n")

        link = project / "alias.txt"
        os.symlink(str(real_file), str(link))

        tool = ReadTool(path_policy=_policy(str(project)))
        result = await tool.call(
            {"file_path": str(link)},
            _context(str(project)),
        )

        assert result.is_error is not True
        assert "hello" in result.output

    @pytest.mark.asyncio
    async def test_regular_file_inside_project_allowed(self, tmp_path: Path):
        """A regular (non-symlink) file in the project must still work."""
        project = tmp_path / "project"
        project.mkdir()

        f = project / "normal.txt"
        f.write_text("fine\n")

        tool = ReadTool(path_policy=_policy(str(project)))
        result = await tool.call(
            {"file_path": str(f)},
            _context(str(project)),
        )

        assert result.is_error is not True
        assert "fine" in result.output

    @pytest.mark.asyncio
    async def test_dotdot_resolved_correctly(self, tmp_path: Path):
        """Path with .. components must resolve to the real location."""
        project = tmp_path / "project"
        project.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "data.txt"
        secret.write_text("nope")

        # project/sub/../../../outside/data.txt  resolves outside project
        sub = project / "sub"
        sub.mkdir()

        crafted = str(sub / ".." / ".." / ".." / "outside" / "data.txt")

        tool = ReadTool(path_policy=_policy(str(project)))
        result = await tool.call(
            {"file_path": crafted},
            _context(str(project)),
        )

        assert result.is_error is True
        assert "outside project boundary" in result.output


# ---------------------------------------------------------------------------
# WriteTool
# ---------------------------------------------------------------------------

class TestWriteToolSymlinkTraversal:
    """WriteTool must resolve symlinks and reject those pointing outside."""

    @pytest.mark.asyncio
    async def test_symlink_outside_project_blocked(self, tmp_path: Path):
        """Writing through a symlink that points outside must be blocked."""
        project = tmp_path / "project"
        project.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "victim.txt"
        target.write_text("original")

        link = project / "harmless.txt"
        os.symlink(str(target), str(link))

        tool = WriteTool(path_policy=_policy(str(project)))
        result = await tool.call(
            {"file_path": str(link), "content": "pwned"},
            _context(str(project)),
        )

        assert result.is_error is True
        assert "outside project boundary" in result.output
        # Confirm the target was NOT modified
        assert target.read_text() == "original"

    @pytest.mark.asyncio
    async def test_symlink_inside_project_allowed(self, tmp_path: Path):
        """Writing through a symlink inside the project must succeed."""
        project = tmp_path / "project"
        project.mkdir()

        real_file = project / "real.txt"
        real_file.write_text("old")

        link = project / "alias.txt"
        os.symlink(str(real_file), str(link))

        tool = WriteTool(path_policy=_policy(str(project)))
        result = await tool.call(
            {"file_path": str(link), "content": "new"},
            _context(str(project)),
        )

        assert result.is_error is not True
        assert real_file.read_text() == "new"

    @pytest.mark.asyncio
    async def test_regular_file_inside_project_allowed(self, tmp_path: Path):
        """Writing a regular file in the project must work."""
        project = tmp_path / "project"
        project.mkdir()

        f = project / "file.txt"

        tool = WriteTool(path_policy=_policy(str(project)))
        result = await tool.call(
            {"file_path": str(f), "content": "hello"},
            _context(str(project)),
        )

        assert result.is_error is not True
        assert f.read_text() == "hello"

    @pytest.mark.asyncio
    async def test_dotdot_resolved_correctly(self, tmp_path: Path):
        """Path with .. components must resolve and be blocked if outside."""
        project = tmp_path / "project"
        project.mkdir()
        sub = project / "sub"
        sub.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()

        crafted = str(sub / ".." / ".." / "outside" / "escape.txt")

        tool = WriteTool(path_policy=_policy(str(project)))
        result = await tool.call(
            {"file_path": crafted, "content": "pwned"},
            _context(str(project)),
        )

        assert result.is_error is True
        assert "outside project boundary" in result.output


# ---------------------------------------------------------------------------
# EditTool
# ---------------------------------------------------------------------------

class TestEditToolSymlinkTraversal:
    """EditTool must resolve symlinks and reject those pointing outside."""

    @pytest.mark.asyncio
    async def test_symlink_outside_project_blocked(self, tmp_path: Path):
        """Editing through a symlink that points outside must be blocked."""
        project = tmp_path / "project"
        project.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "config.txt"
        target.write_text("safe_value=true")

        link = project / "config.txt"
        os.symlink(str(target), str(link))

        tool = EditTool(path_policy=_policy(str(project)))
        result = await tool.call(
            {
                "file_path": str(link),
                "old_string": "safe_value=true",
                "new_string": "safe_value=false",
            },
            _context(str(project)),
        )

        assert result.is_error is True
        assert "outside project boundary" in result.output
        # Confirm target was NOT modified
        assert target.read_text() == "safe_value=true"

    @pytest.mark.asyncio
    async def test_symlink_inside_project_allowed(self, tmp_path: Path):
        """Editing through a symlink inside the project must succeed."""
        project = tmp_path / "project"
        project.mkdir()

        real_file = project / "real.txt"
        real_file.write_text("old content here")

        link = project / "alias.txt"
        os.symlink(str(real_file), str(link))

        tool = EditTool(path_policy=_policy(str(project)))
        result = await tool.call(
            {
                "file_path": str(link),
                "old_string": "old content here",
                "new_string": "new content here",
            },
            _context(str(project)),
        )

        assert result.is_error is not True
        assert real_file.read_text() == "new content here"

    @pytest.mark.asyncio
    async def test_regular_file_inside_project_allowed(self, tmp_path: Path):
        """Editing a regular file in the project must work."""
        project = tmp_path / "project"
        project.mkdir()

        f = project / "normal.txt"
        f.write_text("aaa bbb ccc")

        tool = EditTool(path_policy=_policy(str(project)))
        result = await tool.call(
            {
                "file_path": str(f),
                "old_string": "bbb",
                "new_string": "BBB",
            },
            _context(str(project)),
        )

        assert result.is_error is not True
        assert f.read_text() == "aaa BBB ccc"

    @pytest.mark.asyncio
    async def test_dotdot_resolved_correctly(self, tmp_path: Path):
        """Path with .. components must resolve and be blocked if outside."""
        project = tmp_path / "project"
        project.mkdir()
        sub = project / "sub"
        sub.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "escape.txt"
        target.write_text("original")

        crafted = str(sub / ".." / ".." / "outside" / "escape.txt")

        tool = EditTool(path_policy=_policy(str(project)))
        result = await tool.call(
            {
                "file_path": crafted,
                "old_string": "original",
                "new_string": "modified",
            },
            _context(str(project)),
        )

        assert result.is_error is True
        assert "outside project boundary" in result.output
        assert target.read_text() == "original"


# ---------------------------------------------------------------------------
# Chained symlinks (extra depth)
# ---------------------------------------------------------------------------

class TestChainedSymlinks:
    """Chained symlinks (link -> link -> outside) must also be caught."""

    @pytest.mark.asyncio
    async def test_chained_symlink_read_blocked(self, tmp_path: Path):
        project = tmp_path / "project"
        project.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        secret = outside / "deep_secret.txt"
        secret.write_text("classified")

        # link1 -> link2 -> outside file
        link2 = tmp_path / "link2.txt"
        os.symlink(str(secret), str(link2))
        link1 = project / "link1.txt"
        os.symlink(str(link2), str(link1))

        tool = ReadTool(path_policy=_policy(str(project)))
        result = await tool.call(
            {"file_path": str(link1)},
            _context(str(project)),
        )

        assert result.is_error is True
        assert "outside project boundary" in result.output

    @pytest.mark.asyncio
    async def test_chained_symlink_write_blocked(self, tmp_path: Path):
        project = tmp_path / "project"
        project.mkdir()

        outside = tmp_path / "outside"
        outside.mkdir()
        target = outside / "victim.txt"
        target.write_text("safe")

        link2 = tmp_path / "link2.txt"
        os.symlink(str(target), str(link2))
        link1 = project / "link1.txt"
        os.symlink(str(link2), str(link1))

        tool = WriteTool(path_policy=_policy(str(project)))
        result = await tool.call(
            {"file_path": str(link1), "content": "pwned"},
            _context(str(project)),
        )

        assert result.is_error is True
        assert "outside project boundary" in result.output
        assert target.read_text() == "safe"
