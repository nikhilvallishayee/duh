"""Tests for duh.tools — all 6 built-in tools."""

import asyncio
import os
import sys

import pytest

from duh.kernel.tool import Tool, ToolContext, ToolResult
from duh.tools.read import ReadTool
from duh.tools.write import WriteTool
from duh.tools.edit import EditTool
from duh.tools.bash import BashTool
from duh.tools.glob_tool import GlobTool
from duh.tools.grep import GrepTool
from duh.tools import ALL_TOOLS


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ctx(cwd: str = ".") -> ToolContext:
    return ToolContext(cwd=cwd)


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocolConformance:
    """Every tool must satisfy the Tool protocol."""

    @pytest.mark.parametrize("cls", ALL_TOOLS)
    def test_satisfies_protocol(self, cls):
        tool = cls()
        assert isinstance(tool, Tool)

    @pytest.mark.parametrize("cls", ALL_TOOLS)
    def test_has_name(self, cls):
        tool = cls()
        assert isinstance(tool.name, str) and tool.name

    @pytest.mark.parametrize("cls", ALL_TOOLS)
    def test_has_description(self, cls):
        tool = cls()
        assert isinstance(tool.description, str) and tool.description

    @pytest.mark.parametrize("cls", ALL_TOOLS)
    def test_has_input_schema(self, cls):
        tool = cls()
        assert tool.input_schema["type"] == "object"
        assert "properties" in tool.input_schema

    @pytest.mark.parametrize("cls", ALL_TOOLS)
    def test_has_required_field(self, cls):
        tool = cls()
        assert "required" in tool.input_schema

    def test_read_only_tools(self):
        assert ReadTool().is_read_only is True
        assert GlobTool().is_read_only is True
        assert GrepTool().is_read_only is True

    def test_non_read_only_tools(self):
        assert WriteTool().is_read_only is False
        assert EditTool().is_read_only is False
        assert BashTool().is_read_only is False

    def test_destructive_tools(self):
        assert WriteTool().is_destructive is True
        assert EditTool().is_destructive is True

    def test_non_destructive_tools(self):
        assert ReadTool().is_destructive is False
        assert BashTool().is_destructive is False
        assert GlobTool().is_destructive is False
        assert GrepTool().is_destructive is False


# ===========================================================================
# ReadTool
# ===========================================================================

class TestReadTool:
    tool = ReadTool()

    async def test_read_existing_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("line one\nline two\nline three\n")
        result = await self.tool.call({"file_path": str(f)}, ctx())
        assert result.is_error is False
        assert "1\tline one" in result.output
        assert "2\tline two" in result.output
        assert "3\tline three" in result.output

    async def test_read_missing_file(self, tmp_path):
        result = await self.tool.call(
            {"file_path": str(tmp_path / "nope.txt")}, ctx()
        )
        assert result.is_error is True
        assert "not found" in result.output.lower()

    async def test_read_with_offset(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("a\nb\nc\nd\ne\n")
        result = await self.tool.call(
            {"file_path": str(f), "offset": 2}, ctx()
        )
        assert result.is_error is False
        # offset=2 means skip first 2 lines, start numbering at 3
        assert "3\tc" in result.output
        assert "1\ta" not in result.output

    async def test_read_with_limit(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("a\nb\nc\nd\ne\n")
        result = await self.tool.call(
            {"file_path": str(f), "limit": 2}, ctx()
        )
        assert result.is_error is False
        assert "1\ta" in result.output
        assert "2\tb" in result.output
        assert "3\tc" not in result.output

    async def test_read_with_offset_and_limit(self, tmp_path):
        f = tmp_path / "data.txt"
        f.write_text("a\nb\nc\nd\ne\n")
        result = await self.tool.call(
            {"file_path": str(f), "offset": 1, "limit": 2}, ctx()
        )
        assert result.is_error is False
        assert "2\tb" in result.output
        assert "3\tc" in result.output
        assert "4\td" not in result.output

    async def test_read_empty_file(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.write_text("")
        result = await self.tool.call({"file_path": str(f)}, ctx())
        assert result.is_error is False
        assert "empty" in result.output.lower()

    async def test_read_no_path(self):
        result = await self.tool.call({}, ctx())
        assert result.is_error is True
        assert "required" in result.output.lower()

    async def test_read_metadata(self, tmp_path):
        f = tmp_path / "m.txt"
        f.write_text("one\ntwo\n")
        result = await self.tool.call({"file_path": str(f)}, ctx())
        assert result.metadata["line_count"] == 2


# ===========================================================================
# WriteTool
# ===========================================================================

class TestWriteTool:
    tool = WriteTool()

    async def test_write_new_file(self, tmp_path):
        f = tmp_path / "out.txt"
        result = await self.tool.call(
            {"file_path": str(f), "content": "hello world"}, ctx()
        )
        assert result.is_error is False
        assert f.read_text() == "hello world"

    async def test_overwrite_existing(self, tmp_path):
        f = tmp_path / "out.txt"
        f.write_text("old")
        result = await self.tool.call(
            {"file_path": str(f), "content": "new"}, ctx()
        )
        assert result.is_error is False
        assert f.read_text() == "new"

    async def test_create_parent_dirs(self, tmp_path):
        f = tmp_path / "a" / "b" / "c" / "deep.txt"
        result = await self.tool.call(
            {"file_path": str(f), "content": "deep"}, ctx()
        )
        assert result.is_error is False
        assert f.read_text() == "deep"

    async def test_write_empty_content(self, tmp_path):
        f = tmp_path / "empty.txt"
        result = await self.tool.call(
            {"file_path": str(f), "content": ""}, ctx()
        )
        assert result.is_error is False
        assert f.read_text() == ""

    async def test_write_no_path(self):
        result = await self.tool.call({"content": "x"}, ctx())
        assert result.is_error is True

    async def test_write_metadata(self, tmp_path):
        f = tmp_path / "m.txt"
        result = await self.tool.call(
            {"file_path": str(f), "content": "abcde"}, ctx()
        )
        assert result.metadata["bytes_written"] == 5

    async def test_write_unicode(self, tmp_path):
        f = tmp_path / "uni.txt"
        content = "Hello\n"
        result = await self.tool.call(
            {"file_path": str(f), "content": content}, ctx()
        )
        assert result.is_error is False
        assert f.read_text() == content


# ===========================================================================
# EditTool
# ===========================================================================

class TestEditTool:
    tool = EditTool()

    async def test_successful_edit(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("x = 1\ny = 2\nz = 3\n")
        result = await self.tool.call(
            {"file_path": str(f), "old_string": "y = 2", "new_string": "y = 42"},
            ctx(),
        )
        assert result.is_error is False
        assert "y = 42" in f.read_text()
        assert "y = 2" not in f.read_text()

    async def test_string_not_found(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("x = 1\n")
        result = await self.tool.call(
            {"file_path": str(f), "old_string": "nope", "new_string": "yes"},
            ctx(),
        )
        assert result.is_error is True
        assert "not found" in result.output.lower()

    async def test_file_not_found(self, tmp_path):
        result = await self.tool.call(
            {
                "file_path": str(tmp_path / "missing.py"),
                "old_string": "a",
                "new_string": "b",
            },
            ctx(),
        )
        assert result.is_error is True
        assert "not found" in result.output.lower()

    async def test_ambiguous_match_fails(self, tmp_path):
        f = tmp_path / "dup.py"
        f.write_text("foo\nfoo\nbar\n")
        result = await self.tool.call(
            {"file_path": str(f), "old_string": "foo", "new_string": "baz"},
            ctx(),
        )
        assert result.is_error is True
        assert "2 times" in result.output

    async def test_replace_all(self, tmp_path):
        f = tmp_path / "dup.py"
        f.write_text("foo\nfoo\nbar\n")
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
        assert f.read_text() == "baz\nbaz\nbar\n"
        assert result.metadata["replacements"] == 2

    async def test_empty_old_string(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("x = 1\n")
        result = await self.tool.call(
            {"file_path": str(f), "old_string": "", "new_string": "y"},
            ctx(),
        )
        assert result.is_error is True
        assert "required" in result.output.lower()

    async def test_edit_preserves_other_content(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("alpha\nbeta\ngamma\n")
        await self.tool.call(
            {"file_path": str(f), "old_string": "beta", "new_string": "BETA"},
            ctx(),
        )
        text = f.read_text()
        assert "alpha" in text
        assert "gamma" in text


# ===========================================================================
# BashTool
# ===========================================================================

class TestBashTool:
    tool = BashTool()

    async def test_simple_command(self):
        result = await self.tool.call({"command": "echo hello"}, ctx())
        assert result.is_error is False
        assert "hello" in result.output

    async def test_failing_command(self):
        result = await self.tool.call({"command": "false"}, ctx())
        assert result.is_error is True
        assert result.metadata["returncode"] != 0

    async def test_timeout(self):
        result = await self.tool.call(
            {"command": "sleep 30", "timeout": 1}, ctx()
        )
        assert result.is_error is True
        assert "timed out" in result.output.lower()

    async def test_stderr_captured(self):
        result = await self.tool.call(
            {"command": "echo err >&2"}, ctx()
        )
        assert "err" in result.output

    async def test_empty_command(self):
        result = await self.tool.call({"command": ""}, ctx())
        assert result.is_error is True
        assert "required" in result.output.lower()

    async def test_cwd_respected(self, tmp_path):
        result = await self.tool.call(
            {"command": "pwd"}, ToolContext(cwd=str(tmp_path))
        )
        assert result.is_error is False
        assert str(tmp_path) in result.output

    async def test_multiline_output(self):
        result = await self.tool.call(
            {"command": "printf 'a\\nb\\nc\\n'"}, ctx()
        )
        assert result.is_error is False
        lines = result.output.strip().splitlines()
        assert len(lines) == 3

    async def test_returncode_zero(self):
        result = await self.tool.call({"command": "true"}, ctx())
        assert result.is_error is False
        assert result.metadata["returncode"] == 0


# ===========================================================================
# GlobTool
# ===========================================================================

class TestGlobTool:
    tool = GlobTool()

    async def test_find_files(self, tmp_path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("y")
        (tmp_path / "c.txt").write_text("z")
        result = await self.tool.call(
            {"pattern": "*.py", "path": str(tmp_path)}, ctx()
        )
        assert result.is_error is False
        assert "a.py" in result.output
        assert "b.py" in result.output
        assert "c.txt" not in result.output

    async def test_no_matches(self, tmp_path):
        (tmp_path / "a.txt").write_text("x")
        result = await self.tool.call(
            {"pattern": "*.rs", "path": str(tmp_path)}, ctx()
        )
        assert result.is_error is False
        assert "no files" in result.output.lower()

    async def test_recursive_pattern(self, tmp_path):
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "deep.py").write_text("x")
        result = await self.tool.call(
            {"pattern": "**/*.py", "path": str(tmp_path)}, ctx()
        )
        assert result.is_error is False
        assert "deep.py" in result.output

    async def test_missing_directory(self, tmp_path):
        result = await self.tool.call(
            {"pattern": "*.py", "path": str(tmp_path / "nope")}, ctx()
        )
        assert result.is_error is True
        assert "not found" in result.output.lower()

    async def test_empty_pattern(self):
        result = await self.tool.call({"pattern": ""}, ctx())
        assert result.is_error is True

    async def test_metadata_count(self, tmp_path):
        (tmp_path / "a.py").write_text("x")
        (tmp_path / "b.py").write_text("y")
        result = await self.tool.call(
            {"pattern": "*.py", "path": str(tmp_path)}, ctx()
        )
        assert result.metadata["count"] == 2

    async def test_defaults_to_cwd(self, tmp_path):
        (tmp_path / "f.py").write_text("x")
        result = await self.tool.call(
            {"pattern": "*.py"}, ToolContext(cwd=str(tmp_path))
        )
        assert "f.py" in result.output


# ===========================================================================
# GrepTool
# ===========================================================================

class TestGrepTool:
    tool = GrepTool()

    async def test_match_found(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("def hello():\n    return 42\n")
        result = await self.tool.call(
            {"pattern": "return", "path": str(f)}, ctx()
        )
        assert result.is_error is False
        assert "return 42" in result.output
        assert ":2:" in result.output  # line number

    async def test_no_match(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("def hello():\n    pass\n")
        result = await self.tool.call(
            {"pattern": "nonexistent", "path": str(f)}, ctx()
        )
        assert "no matches" in result.output.lower()

    async def test_regex_pattern(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("x = 1\ny = 2\nz = 3\n")
        result = await self.tool.call(
            {"pattern": r"^[xy]\s*=", "path": str(f)}, ctx()
        )
        assert result.is_error is False
        assert result.metadata["match_count"] == 2

    async def test_case_insensitive(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("Hello\nhello\nHELLO\n")
        result = await self.tool.call(
            {"pattern": "hello", "path": str(f), "case_insensitive": True},
            ctx(),
        )
        assert result.metadata["match_count"] == 3

    async def test_case_sensitive_default(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("Hello\nhello\nHELLO\n")
        result = await self.tool.call(
            {"pattern": "hello", "path": str(f)}, ctx()
        )
        assert result.metadata["match_count"] == 1

    async def test_directory_search(self, tmp_path):
        (tmp_path / "a.py").write_text("foo\nbar\n")
        (tmp_path / "b.py").write_text("baz\nfoo\n")
        result = await self.tool.call(
            {"pattern": "foo", "path": str(tmp_path)}, ctx()
        )
        assert result.metadata["match_count"] == 2

    async def test_directory_with_glob_filter(self, tmp_path):
        (tmp_path / "a.py").write_text("foo\n")
        (tmp_path / "b.txt").write_text("foo\n")
        result = await self.tool.call(
            {"pattern": "foo", "path": str(tmp_path), "glob": "*.py"}, ctx()
        )
        assert result.metadata["match_count"] == 1

    async def test_invalid_regex(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("x\n")
        result = await self.tool.call(
            {"pattern": "[invalid", "path": str(f)}, ctx()
        )
        assert result.is_error is True
        assert "invalid regex" in result.output.lower()

    async def test_empty_pattern(self):
        result = await self.tool.call({"pattern": ""}, ctx())
        assert result.is_error is True

    async def test_missing_path(self, tmp_path):
        result = await self.tool.call(
            {"pattern": "x", "path": str(tmp_path / "nope")}, ctx()
        )
        assert result.is_error is True
        assert "not found" in result.output.lower()
