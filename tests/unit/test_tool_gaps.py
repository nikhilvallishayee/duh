"""Tests targeting specific uncovered lines/branches in D.U.H. tools.

Each test targets a documented coverage gap with a comment referencing the
source file and approximate line numbers.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from duh.kernel.tool import ToolContext, ToolResult


def ctx(cwd: str = ".") -> ToolContext:
    return ToolContext(cwd=cwd)


# ===========================================================================
# EditTool — line 78: replace_all with no matches (impossible combo, but
#   also covers replace_all=True path where count==1)
# ===========================================================================


class TestEditReplaceAllEdgeCases:
    """Covers: duh/tools/edit.py replace_all path."""

    async def test_replace_all_single_occurrence(self, tmp_path):
        """replace_all=True with exactly 1 match should still work."""
        from duh.tools.edit import EditTool
        tool = EditTool()
        f = tmp_path / "code.py"
        f.write_text("hello world\n")
        result = await tool.call(
            {"file_path": str(f), "old_string": "hello", "new_string": "bye", "replace_all": True},
            ctx(),
        )
        assert result.is_error is False
        assert f.read_text() == "bye world\n"
        assert result.metadata["replacements"] == 1

    async def test_replace_all_no_match_is_error(self, tmp_path):
        """replace_all=True but old_string not found => error (line 99-101)."""
        from duh.tools.edit import EditTool
        tool = EditTool()
        f = tmp_path / "code.py"
        f.write_text("hello world\n")
        result = await tool.call(
            {"file_path": str(f), "old_string": "NOTHERE", "new_string": "x", "replace_all": True},
            ctx(),
        )
        assert result.is_error is True
        assert "not found" in result.output.lower()

    async def test_edit_diff_in_output(self, tmp_path):
        """Verify the unified diff appears in successful output."""
        from duh.tools.edit import EditTool
        tool = EditTool()
        f = tmp_path / "code.py"
        f.write_text("alpha\nbeta\ngamma\n")
        result = await tool.call(
            {"file_path": str(f), "old_string": "beta", "new_string": "BETA"},
            ctx(),
        )
        assert result.is_error is False
        assert "---" in result.output  # unified diff header
        assert "+++ " in result.output

    async def test_edit_permission_denied(self, tmp_path):
        """Verify permission check (line 87-91)."""
        from duh.tools.edit import EditTool
        tool = EditTool()
        f = tmp_path / "readonly.py"
        f.write_text("x = 1\n")
        f.chmod(0o444)
        try:
            result = await tool.call(
                {"file_path": str(f), "old_string": "x = 1", "new_string": "x = 2"},
                ctx(),
            )
            assert result.is_error is True
            assert "permission" in result.output.lower()
        finally:
            f.chmod(0o644)


# ===========================================================================
# GlobTool — lines 54-55: invalid/exception-raising pattern
# ===========================================================================


class TestGlobInvalidPattern:
    """Covers: duh/tools/glob_tool.py lines 54-55 (glob error branch)."""

    async def test_glob_error_on_bad_pattern(self, tmp_path):
        from duh.tools.glob_tool import GlobTool
        tool = GlobTool()
        # Patch root.glob to raise an exception simulating a bad pattern
        with patch.object(Path, "glob", side_effect=ValueError("broken glob")):
            result = await tool.call(
                {"pattern": "**/*.py", "path": str(tmp_path)},
                ctx(),
            )
        assert result.is_error is True
        assert "Glob error" in result.output

    async def test_glob_uses_cwd_when_no_path(self, tmp_path):
        """Covers the fallback to context.cwd."""
        from duh.tools.glob_tool import GlobTool
        tool = GlobTool()
        (tmp_path / "a.txt").write_text("x")
        result = await tool.call(
            {"pattern": "*.txt"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error is False
        assert "a.txt" in result.output


# ===========================================================================
# GrepTool — lines 73-74: invalid regex in glob for directory search
# ===========================================================================


class TestGrepEdgeCases:
    """Covers: duh/tools/grep.py lines 73-74 (glob error during dir search)."""

    async def test_grep_glob_error_in_directory(self, tmp_path):
        from duh.tools.grep import GrepTool
        tool = GrepTool()
        (tmp_path / "a.py").write_text("hello\n")
        # Patch Path.glob to raise inside the file-collection phase
        with patch.object(Path, "glob", side_effect=OSError("glob fail")):
            result = await tool.call(
                {"pattern": "hello", "path": str(tmp_path)},
                ctx(),
            )
        assert result.is_error is True
        assert "Glob error" in result.output

    async def test_grep_unreadable_file_skipped(self, tmp_path):
        """Files that raise on read_text should be silently skipped (line 84-85)."""
        from duh.tools.grep import GrepTool
        tool = GrepTool()
        f = tmp_path / "binary.dat"
        f.write_bytes(b"\x00\x01\x02")
        # Should not crash even with binary content
        result = await tool.call(
            {"pattern": "hello", "path": str(f)},
            ctx(),
        )
        assert "no matches" in result.output.lower()

    async def test_grep_defaults_to_cwd(self, tmp_path):
        from duh.tools.grep import GrepTool
        tool = GrepTool()
        (tmp_path / "code.py").write_text("target_string\n")
        result = await tool.call(
            {"pattern": "target_string"},
            ToolContext(cwd=str(tmp_path)),
        )
        assert result.is_error is False
        assert "target_string" in result.output


# ===========================================================================
# ReadTool — line 64: directory path (not a file)
# ===========================================================================


class TestReadDirectory:
    """Covers: duh/tools/read.py line 63-65 (path.is_file() == False)."""

    async def test_read_directory_is_error(self, tmp_path):
        from duh.tools.read import ReadTool
        tool = ReadTool()
        result = await tool.call({"file_path": str(tmp_path)}, ctx())
        assert result.is_error is True
        assert "Not a file" in result.output

    async def test_read_permission_denied(self, tmp_path):
        """Covers line 67-71: no read permission."""
        from duh.tools.read import ReadTool
        tool = ReadTool()
        f = tmp_path / "noperm.txt"
        f.write_text("secret")
        f.chmod(0o000)
        try:
            result = await tool.call({"file_path": str(f)}, ctx())
            assert result.is_error is True
            assert "permission" in result.output.lower()
        finally:
            f.chmod(0o644)

    async def test_read_empty_range(self, tmp_path):
        """Covers the 'no lines in requested range' branch (line 132)."""
        from duh.tools.read import ReadTool
        tool = ReadTool()
        f = tmp_path / "short.txt"
        f.write_text("one\n")
        result = await tool.call(
            {"file_path": str(f), "offset": 100},
            ctx(),
        )
        assert result.is_error is False
        assert "no lines in requested range" in result.output.lower()


# ===========================================================================
# WriteTool — lines 66-67: permission denied on mkdir
# ===========================================================================


class TestWritePermissionDenied:
    """Covers: duh/tools/write.py lines 52-56 + 66-67."""

    async def test_write_parent_not_writable(self, tmp_path):
        """Parent dir exists but is not writable (line 52-56)."""
        from duh.tools.write import WriteTool
        tool = WriteTool()
        no_write = tmp_path / "locked"
        no_write.mkdir()
        no_write.chmod(0o555)
        try:
            result = await tool.call(
                {"file_path": str(no_write / "test.txt"), "content": "hi"},
                ctx(),
            )
            assert result.is_error is True
            assert "permission" in result.output.lower()
        finally:
            no_write.chmod(0o755)

    async def test_write_existing_file_not_writable(self, tmp_path):
        """Existing file is read-only (line 57-61)."""
        from duh.tools.write import WriteTool
        tool = WriteTool()
        f = tmp_path / "readonly.txt"
        f.write_text("old content")
        f.chmod(0o444)
        try:
            result = await tool.call(
                {"file_path": str(f), "content": "new content"},
                ctx(),
            )
            assert result.is_error is True
            assert "permission" in result.output.lower()
        finally:
            f.chmod(0o644)

    async def test_write_mkdir_exception(self, tmp_path):
        """Covers lines 63-67: exception during mkdir/write_text."""
        from duh.tools.write import WriteTool
        tool = WriteTool()
        # Use a path that will fail during write_text
        target = tmp_path / "sub" / "file.txt"
        with patch.object(Path, "mkdir", side_effect=PermissionError("cannot create")):
            result = await tool.call(
                {"file_path": str(target), "content": "x"},
                ctx(),
            )
        assert result.is_error is True
        assert "Error writing file" in result.output


# ===========================================================================
# NotebookEditTool — error cases
# ===========================================================================


class TestNotebookEditErrorCases:
    """Covers: duh/tools/notebook_edit.py lines 70+ (various error paths)."""

    async def test_missing_cell_index(self):
        from duh.tools.notebook_edit import NotebookEditTool
        tool = NotebookEditTool()
        result = await tool.call(
            {"notebook_path": "/tmp/test.ipynb"},
            ctx(),
        )
        assert result.is_error is True
        assert "cell_index is required" in result.output

    async def test_cell_type_change_code_to_markdown(self, tmp_path):
        """Cover cell type change branches (lines 212-222)."""
        from duh.tools.notebook_edit import NotebookEditTool, _make_cell
        tool = NotebookEditTool()
        nb = {
            "nbformat": 4, "metadata": {},
            "cells": [
                {
                    "cell_type": "code",
                    "metadata": {},
                    "source": ["x = 1"],
                    "execution_count": 1,
                    "outputs": [{"output_type": "stream", "text": ["1"]}],
                },
            ],
        }
        f = tmp_path / "change.ipynb"
        f.write_text(json.dumps(nb, indent=1), encoding="utf-8")

        result = await tool.call(
            {
                "notebook_path": str(f),
                "cell_index": 0,
                "new_source": "# Now markdown",
                "cell_type": "markdown",
            },
            ctx(),
        )
        assert result.is_error is False
        updated = json.loads(f.read_text())
        assert updated["cells"][0]["cell_type"] == "markdown"
        # outputs should be removed for markdown
        assert "outputs" not in updated["cells"][0]

    async def test_cell_type_change_markdown_to_code(self, tmp_path):
        """Cover the code branch addition of outputs/execution_count (lines 217-219)."""
        from duh.tools.notebook_edit import NotebookEditTool
        tool = NotebookEditTool()
        nb = {
            "nbformat": 4, "metadata": {},
            "cells": [
                {
                    "cell_type": "markdown",
                    "metadata": {},
                    "source": ["# title"],
                },
            ],
        }
        f = tmp_path / "tocode.ipynb"
        f.write_text(json.dumps(nb, indent=1), encoding="utf-8")

        result = await tool.call(
            {
                "notebook_path": str(f),
                "cell_index": 0,
                "new_source": "x = 1",
                "cell_type": "code",
            },
            ctx(),
        )
        assert result.is_error is False
        updated = json.loads(f.read_text())
        assert updated["cells"][0]["cell_type"] == "code"
        assert updated["cells"][0]["outputs"] == []
        assert updated["cells"][0]["execution_count"] is None

    async def test_write_notebook_error(self, tmp_path):
        """Cover lines 228-233: error writing notebook back."""
        from duh.tools.notebook_edit import NotebookEditTool
        tool = NotebookEditTool()
        nb = {
            "nbformat": 4, "metadata": {},
            "cells": [{"cell_type": "code", "metadata": {}, "source": ["x"], "outputs": [], "execution_count": None}],
        }
        f = tmp_path / "fail.ipynb"
        f.write_text(json.dumps(nb, indent=1), encoding="utf-8")

        with patch("duh.tools.notebook_edit._write_notebook", side_effect=IOError("disk full")):
            result = await tool.call(
                {"notebook_path": str(f), "cell_index": 0, "new_source": "y"},
                ctx(),
            )
        assert result.is_error is True
        assert "Error writing notebook" in result.output


# ===========================================================================
# NotebookEditTool helpers — render_notebook edge cases
# ===========================================================================


class TestRenderNotebookEdgeCases:
    def test_source_as_string_not_list(self):
        """Cover line 69-70: source is a raw string, not a list."""
        from duh.tools.notebook_edit import render_notebook
        nb = {
            "cells": [
                {"cell_type": "code", "source": "raw string source"},
            ],
        }
        rendered = render_notebook(nb)
        assert "raw string source" in rendered

    def test_unknown_cell_type(self):
        """Cover line 65: cell_type is 'unknown'."""
        from duh.tools.notebook_edit import render_notebook
        nb = {
            "cells": [
                {"source": ["x = 1"]},  # no cell_type key
            ],
        }
        rendered = render_notebook(nb)
        assert "(unknown)" in rendered


# ===========================================================================
# WorktreeTool — auto-branch generation
# ===========================================================================


class TestWorktreeAutoBranch:
    """Covers: duh/tools/worktree.py lines 35-50 (auto-branch)."""

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    @patch("duh.tools.worktree.Path.mkdir")
    async def test_auto_branch_with_empty_string(self, mock_mkdir, mock_git):
        """branch="" should trigger auto-generation (line 114)."""
        from duh.tools.worktree import EnterWorktreeTool
        mock_git.side_effect = [
            (0, "true", ""),   # rev-parse
            (0, "", ""),       # worktree add
        ]
        tool = EnterWorktreeTool()
        c = ToolContext(cwd="/fake/repo", metadata={})
        result = await tool.call({"branch": ""}, c)
        assert result.is_error is False
        branch = c.metadata.get("worktree_branch", "")
        assert branch.startswith("duh-worktree-")

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    @patch("duh.tools.worktree.Path.mkdir")
    async def test_custom_path_override(self, mock_mkdir, mock_git):
        """Explicit path overrides the default."""
        from duh.tools.worktree import EnterWorktreeTool
        mock_git.side_effect = [
            (0, "true", ""),
            (0, "", ""),
        ]
        tool = EnterWorktreeTool()
        c = ToolContext(cwd="/fake/repo", metadata={})
        result = await tool.call(
            {"branch": "feat", "path": "/custom/wt/feat"},
            c,
        )
        assert result.is_error is False
        assert c.cwd == "/custom/wt/feat"

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    async def test_cwd_fallback_to_os_getcwd(self, mock_git):
        """When context.cwd is '.' or empty, fallback to os.getcwd() (line 102)."""
        from duh.tools.worktree import EnterWorktreeTool
        mock_git.side_effect = [
            (0, "true", ""),
            (0, "", ""),
        ]
        tool = EnterWorktreeTool()
        c = ToolContext(cwd=".", metadata={})
        with patch("duh.tools.worktree.Path.mkdir"):
            with patch("os.getcwd", return_value="/resolved/cwd"):
                result = await tool.call({"branch": "test"}, c)
        assert result.is_error is False
        # The original_cwd should be /resolved/cwd, not "."
        assert result.metadata["original_cwd"] == "/resolved/cwd"

    @patch("duh.tools.worktree._run_git_async", new_callable=AsyncMock)
    async def test_git_timeout_handling(self, mock_git):
        """Cover _run_git_async timeout path (line 43-49)."""
        from duh.tools.worktree import EnterWorktreeTool
        mock_git.return_value = (1, "", "git command timed out")
        tool = EnterWorktreeTool()
        c = ToolContext(cwd="/fake/repo", metadata={})
        result = await tool.call({"branch": "t"}, c)
        assert result.is_error is True


# ===========================================================================
# ReadTool — large file truncation
# ===========================================================================


class TestReadLargeFile:
    """Covers: duh/tools/read.py lines 84-110 (large-file guard)."""

    async def test_large_file_truncated(self, tmp_path):
        from duh.tools.read import ReadTool
        from duh.kernel.tool import MAX_TOOL_OUTPUT
        tool = ReadTool()
        f = tmp_path / "huge.txt"
        # Write a file larger than MAX_TOOL_OUTPUT
        line = "x" * 200 + "\n"
        num_lines = (MAX_TOOL_OUTPUT // len(line)) + 100
        f.write_text(line * num_lines)
        result = await tool.call({"file_path": str(f)}, ctx())
        assert result.is_error is False
        assert "File is large" in result.output
        assert result.metadata.get("truncated") is True


# ===========================================================================
# EditTool — _make_diff helper
# ===========================================================================


class TestMakeDiff:
    def test_identical_content_returns_empty(self):
        from duh.tools.edit import _make_diff
        assert _make_diff("same", "same", "file.py") == ""

    def test_diff_contains_headers(self):
        from duh.tools.edit import _make_diff
        result = _make_diff("old\n", "new\n", "file.py")
        assert "--- old/file.py" in result
        assert "+++ new/file.py" in result
        assert "-old" in result
        assert "+new" in result
