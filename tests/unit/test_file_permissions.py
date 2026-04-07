"""Tests for file permission validation in Read, Write, Edit, and MultiEdit tools."""

from __future__ import annotations

import os
import stat
import sys

import pytest

from duh.kernel.tool import ToolContext, ToolResult
from duh.tools.read import ReadTool
from duh.tools.write import WriteTool
from duh.tools.edit import EditTool
from duh.tools.multi_edit import MultiEditTool


def ctx() -> ToolContext:
    return ToolContext()


# Skip the entire module when running as root — root bypasses file permissions
pytestmark = pytest.mark.skipif(
    os.getuid() == 0, reason="Permission tests are meaningless when running as root"
)


# ---------------------------------------------------------------------------
# ReadTool — permission checks
# ---------------------------------------------------------------------------

class TestReadPermissions:
    tool = ReadTool()

    async def test_read_unreadable_file(self, tmp_path):
        """Reading a file without read permission returns a clear error."""
        f = tmp_path / "secret.txt"
        f.write_text("classified")
        f.chmod(0o000)
        try:
            result = await self.tool.call({"file_path": str(f)}, ctx())
            assert result.is_error is True
            assert "permission denied" in result.output.lower()
            assert "cannot read" in result.output.lower()
            assert str(f) in result.output
        finally:
            f.chmod(0o644)

    async def test_read_write_only_file(self, tmp_path):
        """A file with write-only permission is not readable."""
        f = tmp_path / "writeonly.txt"
        f.write_text("data")
        f.chmod(0o200)  # write only
        try:
            result = await self.tool.call({"file_path": str(f)}, ctx())
            assert result.is_error is True
            assert "permission denied" in result.output.lower()
        finally:
            f.chmod(0o644)

    async def test_read_readable_file_succeeds(self, tmp_path):
        """Happy path: readable file works fine."""
        f = tmp_path / "ok.txt"
        f.write_text("hello\n")
        result = await self.tool.call({"file_path": str(f)}, ctx())
        assert result.is_error is False
        assert "hello" in result.output

    async def test_read_nonexistent_file(self, tmp_path):
        """Non-existent file gives 'not found', not 'permission denied'."""
        result = await self.tool.call(
            {"file_path": str(tmp_path / "nope.txt")}, ctx()
        )
        assert result.is_error is True
        assert "not found" in result.output.lower()


# ---------------------------------------------------------------------------
# WriteTool — permission checks
# ---------------------------------------------------------------------------

class TestWritePermissions:
    tool = WriteTool()

    async def test_write_to_readonly_directory(self, tmp_path):
        """Writing into a read-only directory is blocked."""
        ro_dir = tmp_path / "readonly_dir"
        ro_dir.mkdir()
        ro_dir.chmod(0o555)  # read + execute, no write
        try:
            target = ro_dir / "file.txt"
            result = await self.tool.call(
                {"file_path": str(target), "content": "hello"}, ctx()
            )
            assert result.is_error is True
            assert "permission denied" in result.output.lower()
        finally:
            ro_dir.chmod(0o755)

    async def test_write_to_readonly_file(self, tmp_path):
        """Overwriting a read-only file is blocked."""
        f = tmp_path / "locked.txt"
        f.write_text("original")
        f.chmod(0o444)  # read only
        try:
            result = await self.tool.call(
                {"file_path": str(f), "content": "overwrite"}, ctx()
            )
            assert result.is_error is True
            assert "permission denied" in result.output.lower()
            # File should be untouched
            f.chmod(0o644)
            assert f.read_text() == "original"
        finally:
            f.chmod(0o644)

    async def test_write_new_file_succeeds(self, tmp_path):
        """Happy path: writing a new file in a writable directory works."""
        f = tmp_path / "newfile.txt"
        result = await self.tool.call(
            {"file_path": str(f), "content": "content"}, ctx()
        )
        assert result.is_error is False
        assert f.read_text() == "content"

    async def test_write_overwrite_writable_file_succeeds(self, tmp_path):
        """Happy path: overwriting a writable file works."""
        f = tmp_path / "writable.txt"
        f.write_text("old")
        result = await self.tool.call(
            {"file_path": str(f), "content": "new"}, ctx()
        )
        assert result.is_error is False
        assert f.read_text() == "new"


# ---------------------------------------------------------------------------
# EditTool — permission checks
# ---------------------------------------------------------------------------

class TestEditPermissions:
    tool = EditTool()

    async def test_edit_readonly_file(self, tmp_path):
        """Editing a read-only file returns a clear permission error."""
        f = tmp_path / "readonly.py"
        f.write_text("x = 1\n")
        f.chmod(0o444)
        try:
            result = await self.tool.call(
                {"file_path": str(f), "old_string": "x = 1", "new_string": "x = 2"},
                ctx(),
            )
            assert result.is_error is True
            assert "permission denied" in result.output.lower()
            assert "need read+write" in result.output.lower()
            # File should be untouched
            f.chmod(0o644)
            assert f.read_text() == "x = 1\n"
        finally:
            f.chmod(0o644)

    async def test_edit_writeonly_file(self, tmp_path):
        """Editing a write-only file (no read) returns a permission error."""
        f = tmp_path / "writeonly.py"
        f.write_text("a = 1\n")
        f.chmod(0o200)  # write only
        try:
            result = await self.tool.call(
                {"file_path": str(f), "old_string": "a = 1", "new_string": "a = 2"},
                ctx(),
            )
            assert result.is_error is True
            assert "permission denied" in result.output.lower()
        finally:
            f.chmod(0o644)

    async def test_edit_writable_file_succeeds(self, tmp_path):
        """Happy path: editing a read+write file works."""
        f = tmp_path / "editable.py"
        f.write_text("x = 1\ny = 2\n")
        result = await self.tool.call(
            {"file_path": str(f), "old_string": "x = 1", "new_string": "x = 99"},
            ctx(),
        )
        assert result.is_error is False
        assert "x = 99" in f.read_text()


# ---------------------------------------------------------------------------
# MultiEditTool — permission checks (fail-early)
# ---------------------------------------------------------------------------

class TestMultiEditPermissions:
    tool = MultiEditTool()

    async def test_multi_edit_one_bad_permission_fails_early(self, tmp_path):
        """If one file lacks permissions, ALL edits are rejected before any apply."""
        good_file = tmp_path / "good.py"
        bad_file = tmp_path / "bad.py"
        good_file.write_text("alpha\n")
        bad_file.write_text("beta\n")
        bad_file.chmod(0o444)  # read only, no write
        try:
            result = await self.tool.call(
                {
                    "edits": [
                        {"file_path": str(good_file), "old_string": "alpha", "new_string": "ALPHA"},
                        {"file_path": str(bad_file), "old_string": "beta", "new_string": "BETA"},
                    ]
                },
                ctx(),
            )
            assert result.is_error is True
            assert "permission" in result.output.lower()
            # The good file should NOT have been modified (fail-early)
            assert good_file.read_text() == "alpha\n"
        finally:
            bad_file.chmod(0o644)

    async def test_multi_edit_all_files_writable_succeeds(self, tmp_path):
        """Happy path: all files writable, edits apply."""
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("x = 1\n")
        f2.write_text("y = 2\n")
        result = await self.tool.call(
            {
                "edits": [
                    {"file_path": str(f1), "old_string": "x = 1", "new_string": "x = 10"},
                    {"file_path": str(f2), "old_string": "y = 2", "new_string": "y = 20"},
                ]
            },
            ctx(),
        )
        assert result.is_error is False
        assert f1.read_text() == "x = 10\n"
        assert f2.read_text() == "y = 20\n"

    async def test_multi_edit_multiple_bad_permissions_lists_all(self, tmp_path):
        """All permission issues are reported in the error message."""
        f1 = tmp_path / "locked1.py"
        f2 = tmp_path / "locked2.py"
        f1.write_text("a\n")
        f2.write_text("b\n")
        f1.chmod(0o444)
        f2.chmod(0o444)
        try:
            result = await self.tool.call(
                {
                    "edits": [
                        {"file_path": str(f1), "old_string": "a", "new_string": "A"},
                        {"file_path": str(f2), "old_string": "b", "new_string": "B"},
                    ]
                },
                ctx(),
            )
            assert result.is_error is True
            # Both files should be mentioned
            assert "locked1.py" in result.output
            assert "locked2.py" in result.output
        finally:
            f1.chmod(0o644)
            f2.chmod(0o644)

    async def test_multi_edit_nonexistent_file_skips_perm_check(self, tmp_path):
        """Non-existent files are not flagged by the permission check
        (they'll be caught later as 'file not found')."""
        result = await self.tool.call(
            {
                "edits": [
                    {
                        "file_path": str(tmp_path / "missing.py"),
                        "old_string": "a",
                        "new_string": "b",
                    }
                ]
            },
            ctx(),
        )
        assert result.is_error is True
        assert "not found" in result.output.lower()
