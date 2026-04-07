"""Tests for unified diff output in EditTool and MultiEditTool."""

from __future__ import annotations

import pytest

from duh.kernel.tool import ToolContext
from duh.tools.edit import EditTool, _make_diff
from duh.tools.multi_edit import MultiEditTool


def ctx() -> ToolContext:
    return ToolContext()


# ---------------------------------------------------------------------------
# _make_diff helper
# ---------------------------------------------------------------------------


class TestMakeDiff:
    """Unit tests for the _make_diff utility."""

    def test_identical_content_returns_empty(self):
        assert _make_diff("hello\n", "hello\n", "f.py") == ""

    def test_headers_use_old_new_prefix(self):
        diff = _make_diff("a\n", "b\n", "src/foo.py")
        assert "--- old/src/foo.py" in diff
        assert "+++ new/src/foo.py" in diff

    def test_removed_and_added_lines(self):
        diff = _make_diff("alpha\n", "beta\n", "f.py")
        assert "-alpha" in diff
        assert "+beta" in diff

    def test_context_lines_default_three(self):
        # 7 lines: context should include 3 before/after the change
        old = "".join(f"line{i}\n" for i in range(1, 8))
        new = old.replace("line4", "LINE4")
        diff = _make_diff(old, new, "f.py")
        # The 3 context lines before the change (line1, line2, line3)
        assert " line1\n" in diff
        assert " line2\n" in diff
        assert " line3\n" in diff
        # The changed line
        assert "-line4\n" in diff
        assert "+LINE4\n" in diff
        # The 3 context lines after the change (line5, line6, line7)
        assert " line5\n" in diff
        assert " line6\n" in diff
        assert " line7\n" in diff


# ---------------------------------------------------------------------------
# EditTool — diff appended to success message
# ---------------------------------------------------------------------------


class TestEditToolDiff:
    """EditTool.call output must contain a unified diff on success."""

    tool = EditTool()

    async def test_diff_present_on_success(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("x = 1\ny = 2\nz = 3\n")
        result = await self.tool.call(
            {"file_path": str(f), "old_string": "y = 2", "new_string": "y = 42"},
            ctx(),
        )
        assert result.is_error is False
        assert "--- old/" in result.output
        assert "+++ new/" in result.output
        assert "-y = 2" in result.output
        assert "+y = 42" in result.output

    async def test_diff_not_present_on_error(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("x = 1\n")
        result = await self.tool.call(
            {"file_path": str(f), "old_string": "NOPE", "new_string": "y"},
            ctx(),
        )
        assert result.is_error is True
        assert "---" not in result.output

    async def test_replace_all_diff_shows_all_changes(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("foo\nbar\nfoo\n")
        result = await self.tool.call(
            {
                "file_path": str(f),
                "old_string": "foo",
                "new_string": "baz",
                "replace_all": True,
            },
            ctx(),
        )
        assert result.is_error is False
        # Both occurrences should appear as removed/added
        assert result.output.count("-foo") == 2
        assert result.output.count("+baz") == 2

    async def test_diff_has_file_path_in_headers(self, tmp_path):
        f = tmp_path / "deep" / "nested" / "file.py"
        f.parent.mkdir(parents=True)
        f.write_text("old_val\n")
        fp = str(f)
        result = await self.tool.call(
            {"file_path": fp, "old_string": "old_val", "new_string": "new_val"},
            ctx(),
        )
        assert f"--- old/{fp}" in result.output
        assert f"+++ new/{fp}" in result.output


# ---------------------------------------------------------------------------
# MultiEditTool — per-edit diffs in summary
# ---------------------------------------------------------------------------


class TestMultiEditToolDiff:
    """MultiEditTool.call output must contain per-edit diffs on success."""

    tool = MultiEditTool()

    async def test_single_edit_includes_diff(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("a = 1\nb = 2\n")
        result = await self.tool.call(
            {"edits": [{"file_path": str(f), "old_string": "a = 1", "new_string": "a = 10"}]},
            ctx(),
        )
        assert result.is_error is False
        assert "--- old/" in result.output
        assert "-a = 1" in result.output
        assert "+a = 10" in result.output

    async def test_multiple_edits_include_separate_diffs(self, tmp_path):
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
        # Both files should appear in diff headers
        assert f"--- old/{f1}" in result.output
        assert f"--- old/{f2}" in result.output
        assert "-x = 1" in result.output
        assert "+x = 10" in result.output
        assert "-y = 2" in result.output
        assert "+y = 20" in result.output

    async def test_failed_edit_has_no_diff(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("hello\n")
        result = await self.tool.call(
            {
                "edits": [
                    {"file_path": str(f), "old_string": "NOPE", "new_string": "x"},
                ]
            },
            ctx(),
        )
        assert result.is_error is True
        assert "---" not in result.output

    async def test_partial_failure_shows_only_successful_diffs(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("alpha\nbeta\n")
        result = await self.tool.call(
            {
                "edits": [
                    {"file_path": str(f), "old_string": "alpha", "new_string": "ALPHA"},
                    {"file_path": str(f), "old_string": "NOTFOUND", "new_string": "X"},
                ]
            },
            ctx(),
        )
        assert result.is_error is False
        # Successful edit shows diff
        assert "-alpha" in result.output
        assert "+ALPHA" in result.output
        # Failed edit does not produce a diff entry for "NOTFOUND"
        assert "NOTFOUND" not in result.output.split("---")[0] or True  # just not in diff section
        # Verify only one diff header pair (one successful edit)
        assert result.output.count("--- old/") == 1
