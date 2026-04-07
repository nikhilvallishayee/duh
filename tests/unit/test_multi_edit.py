"""Tests for duh.tools.multi_edit — MultiEditTool."""

import pytest

from duh.kernel.tool import Tool, ToolContext, ToolResult
from duh.tools.multi_edit import MultiEditTool


def ctx() -> ToolContext:
    return ToolContext()


class TestMultiEditProtocol:
    """MultiEditTool must satisfy the Tool protocol."""

    def test_satisfies_protocol(self):
        assert isinstance(MultiEditTool(), Tool)

    def test_has_name(self):
        assert MultiEditTool().name == "MultiEdit"

    def test_has_description(self):
        assert MultiEditTool().description

    def test_has_input_schema(self):
        schema = MultiEditTool().input_schema
        assert schema["type"] == "object"
        assert "edits" in schema["properties"]
        assert "edits" in schema["required"]

    def test_is_not_read_only(self):
        assert MultiEditTool().is_read_only is False

    def test_is_destructive(self):
        assert MultiEditTool().is_destructive is True


class TestMultiEditSingleEdit:
    """Single edit behaves like EditTool."""

    tool = MultiEditTool()

    async def test_single_edit_success(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("x = 1\ny = 2\nz = 3\n")
        result = await self.tool.call(
            {"edits": [{"file_path": str(f), "old_string": "y = 2", "new_string": "y = 42"}]},
            ctx(),
        )
        assert result.is_error is False
        assert "1/1" in result.output
        assert "y = 42" in f.read_text()
        assert "y = 2" not in f.read_text()
        assert result.metadata["succeeded"] == 1
        assert result.metadata["failed"] == 0


class TestMultiEditSameFile:
    """Multiple edits to the same file."""

    tool = MultiEditTool()

    async def test_two_edits_same_file(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("alpha\nbeta\ngamma\n")
        result = await self.tool.call(
            {
                "edits": [
                    {"file_path": str(f), "old_string": "alpha", "new_string": "ALPHA"},
                    {"file_path": str(f), "old_string": "gamma", "new_string": "GAMMA"},
                ]
            },
            ctx(),
        )
        assert result.is_error is False
        assert "2/2" in result.output
        text = f.read_text()
        assert "ALPHA" in text
        assert "beta" in text
        assert "GAMMA" in text

    async def test_sequential_edits_see_prior_changes(self, tmp_path):
        """Edit 2 should see the file as modified by edit 1."""
        f = tmp_path / "code.py"
        f.write_text("old_value = 1\n")
        result = await self.tool.call(
            {
                "edits": [
                    {"file_path": str(f), "old_string": "old_value", "new_string": "new_value"},
                    {"file_path": str(f), "old_string": "new_value = 1", "new_string": "new_value = 99"},
                ]
            },
            ctx(),
        )
        assert result.is_error is False
        assert "2/2" in result.output
        assert f.read_text() == "new_value = 99\n"


class TestMultiEditDifferentFiles:
    """Multiple edits to different files."""

    tool = MultiEditTool()

    async def test_edits_across_files(self, tmp_path):
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
        assert "2/2" in result.output
        assert f1.read_text() == "x = 10\n"
        assert f2.read_text() == "y = 20\n"


class TestMultiEditPartialFailure:
    """One edit fails, others succeed."""

    tool = MultiEditTool()

    async def test_middle_edit_fails(self, tmp_path):
        f1 = tmp_path / "a.py"
        f2 = tmp_path / "b.py"
        f1.write_text("alpha\n")
        f2.write_text("beta\n")
        result = await self.tool.call(
            {
                "edits": [
                    {"file_path": str(f1), "old_string": "alpha", "new_string": "ALPHA"},
                    {"file_path": str(f1), "old_string": "NOTFOUND", "new_string": "X"},
                    {"file_path": str(f2), "old_string": "beta", "new_string": "BETA"},
                ]
            },
            ctx(),
        )
        # Partial success is not an error
        assert result.is_error is False
        assert "2/3" in result.output
        assert "edit 2" in result.output.lower()
        assert result.metadata["succeeded"] == 2
        assert result.metadata["failed"] == 1
        assert f1.read_text() == "ALPHA\n"
        assert f2.read_text() == "BETA\n"

    async def test_all_edits_fail(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("hello\n")
        result = await self.tool.call(
            {
                "edits": [
                    {"file_path": str(f), "old_string": "NOPE1", "new_string": "X"},
                    {"file_path": str(f), "old_string": "NOPE2", "new_string": "Y"},
                ]
            },
            ctx(),
        )
        assert result.is_error is True
        assert "0/2" in result.output
        assert result.metadata["succeeded"] == 0
        assert result.metadata["failed"] == 2

    async def test_ambiguous_match_fails_one_edit(self, tmp_path):
        f = tmp_path / "dup.py"
        f.write_text("foo\nfoo\nbar\n")
        result = await self.tool.call(
            {
                "edits": [
                    {"file_path": str(f), "old_string": "foo", "new_string": "baz"},
                    {"file_path": str(f), "old_string": "bar", "new_string": "BAR"},
                ]
            },
            ctx(),
        )
        # edit 1 fails (ambiguous), edit 2 succeeds
        assert result.is_error is False
        assert "1/2" in result.output
        assert "2 times" in result.output
        text = f.read_text()
        assert "foo" in text  # unchanged — ambiguous edit was skipped
        assert "BAR" in text


class TestMultiEditEmptyList:
    """Empty edits list."""

    tool = MultiEditTool()

    async def test_empty_edits(self):
        result = await self.tool.call({"edits": []}, ctx())
        assert result.is_error is True
        assert "empty" in result.output.lower()

    async def test_missing_edits_key(self):
        result = await self.tool.call({}, ctx())
        assert result.is_error is True
        assert "required" in result.output.lower()

    async def test_edits_not_a_list(self):
        result = await self.tool.call({"edits": "not a list"}, ctx())
        assert result.is_error is True
        assert "list" in result.output.lower()


class TestMultiEditInvalidFilePath:
    """Invalid file paths."""

    tool = MultiEditTool()

    async def test_file_not_found(self, tmp_path):
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

    async def test_empty_file_path(self):
        result = await self.tool.call(
            {"edits": [{"file_path": "", "old_string": "a", "new_string": "b"}]},
            ctx(),
        )
        assert result.is_error is True
        assert "file_path" in result.output.lower()

    async def test_empty_old_string(self, tmp_path):
        f = tmp_path / "code.py"
        f.write_text("hello\n")
        result = await self.tool.call(
            {"edits": [{"file_path": str(f), "old_string": "", "new_string": "x"}]},
            ctx(),
        )
        assert result.is_error is True
        assert "old_string" in result.output.lower()


class TestMultiEditPermissions:
    """Permission check."""

    async def test_check_permissions_allowed(self):
        tool = MultiEditTool()
        result = await tool.check_permissions({"edits": []}, ctx())
        assert result["allowed"] is True
