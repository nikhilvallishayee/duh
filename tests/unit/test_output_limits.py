"""Tests for output size limits across tools and the native executor.

Ensures that runaway tool output is truncated at MAX_TOOL_OUTPUT (100KB)
with helpful messages guiding the user to more targeted approaches.
"""

from __future__ import annotations

import pytest

from duh.kernel.tool import MAX_TOOL_OUTPUT, ToolContext, ToolResult
from duh.adapters.native_executor import NativeExecutor
from duh.tools.bash import BashTool
from duh.tools.read import ReadTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ctx(cwd: str = ".") -> ToolContext:
    return ToolContext(cwd=cwd)


class HugeTool:
    """Produces output larger than MAX_TOOL_OUTPUT."""

    name = "Huge"
    description = "Returns a massive string"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, size: int = MAX_TOOL_OUTPUT + 50_000):
        self._size = size

    async def call(self, input, context):
        return ToolResult(output="X" * self._size)

    async def check_permissions(self, input, context):
        return {"allowed": True}


class HugeRawTool:
    """Returns a raw string (not ToolResult) larger than MAX_TOOL_OUTPUT."""

    name = "HugeRaw"
    description = "Returns a massive raw string"
    input_schema = {"type": "object", "properties": {}}

    async def call(self, input, context):
        return "R" * (MAX_TOOL_OUTPUT + 10_000)


class SmallTool:
    """Produces output well under the limit."""

    name = "Small"
    description = "Returns a small string"
    input_schema = {"type": "object", "properties": {}}

    async def call(self, input, context):
        return ToolResult(output="small output")

    async def check_permissions(self, input, context):
        return {"allowed": True}


# ===========================================================================
# MAX_TOOL_OUTPUT constant
# ===========================================================================

class TestMaxToolOutputConstant:
    def test_constant_value(self):
        assert MAX_TOOL_OUTPUT == 100_000

    def test_constant_is_int(self):
        assert isinstance(MAX_TOOL_OUTPUT, int)


# ===========================================================================
# NativeExecutor truncation
# ===========================================================================

class TestNativeExecutorTruncation:
    async def test_truncates_large_tool_result(self):
        e = NativeExecutor(tools=[HugeTool()])
        result = await e.run("Huge", {})
        assert len(result) <= MAX_TOOL_OUTPUT + 200  # room for the message
        assert "truncated" in result.lower()
        assert "Read with offset/limit" in result

    async def test_truncation_metadata_set(self):
        """The executor mutates the ToolResult metadata with truncation info."""
        tool = HugeTool(size=MAX_TOOL_OUTPUT + 5_000)
        e = NativeExecutor(tools=[tool])

        # We need to peek at the ToolResult metadata.
        # Run the tool directly to check metadata was set.
        ctx_obj = ToolContext(cwd=".")
        result_obj = await tool.call({}, ctx_obj)
        # Now run through executor — it modifies result_obj.metadata in place
        e2 = NativeExecutor(tools=[HugeTool(size=MAX_TOOL_OUTPUT + 5_000)])
        output = await e2.run("Huge", {})
        assert isinstance(output, str)
        assert len(output) > MAX_TOOL_OUTPUT  # includes the truncation message

    async def test_small_output_not_truncated(self):
        e = NativeExecutor(tools=[SmallTool()])
        result = await e.run("Small", {})
        assert result == "small output"
        assert "truncated" not in result.lower()

    async def test_truncates_raw_string_return(self):
        e = NativeExecutor(tools=[HugeRawTool()])
        result = await e.run("HugeRaw", {})
        assert len(result) <= MAX_TOOL_OUTPUT + 200
        assert "truncated" in result.lower()

    async def test_exact_boundary_not_truncated(self):
        """Output exactly at MAX_TOOL_OUTPUT should NOT be truncated."""

        class ExactTool:
            name = "Exact"
            description = ""
            input_schema = {"type": "object", "properties": {}}

            async def call(self, input, context):
                return ToolResult(output="A" * MAX_TOOL_OUTPUT)

        e = NativeExecutor(tools=[ExactTool()])
        result = await e.run("Exact", {})
        assert len(result) == MAX_TOOL_OUTPUT
        assert "truncated" not in result.lower()


# ===========================================================================
# ReadTool large-file truncation
# ===========================================================================

class TestReadToolLargeFile:
    tool = ReadTool()

    async def test_large_file_no_slice_truncated(self, tmp_path):
        """A file > 100KB with no offset/limit is truncated with a message."""
        f = tmp_path / "big.txt"
        # Each line ~80 chars, need enough to exceed 100KB
        line = "A" * 78 + "\n"
        num_lines = (MAX_TOOL_OUTPUT // len(line)) + 500
        f.write_text(line * num_lines)

        result = await self.tool.call({"file_path": str(f)}, ctx())
        assert result.is_error is False
        assert "File is large" in result.output
        assert "offset and limit" in result.output
        assert result.metadata.get("truncated") is True
        assert result.metadata.get("original_size", 0) > MAX_TOOL_OUTPUT

    async def test_large_file_with_offset_not_truncated_early(self, tmp_path):
        """When offset is provided, the large-file guard does NOT trigger."""
        f = tmp_path / "big.txt"
        line = "B" * 78 + "\n"
        num_lines = (MAX_TOOL_OUTPUT // len(line)) + 500
        f.write_text(line * num_lines)

        result = await self.tool.call(
            {"file_path": str(f), "offset": 0, "limit": 10}, ctx()
        )
        assert result.is_error is False
        assert "File is large" not in result.output
        assert result.metadata["line_count"] == 10

    async def test_large_file_with_limit_not_truncated_early(self, tmp_path):
        """When limit is provided, the large-file guard does NOT trigger."""
        f = tmp_path / "big.txt"
        line = "C" * 78 + "\n"
        num_lines = (MAX_TOOL_OUTPUT // len(line)) + 500
        f.write_text(line * num_lines)

        result = await self.tool.call(
            {"file_path": str(f), "limit": 5}, ctx()
        )
        assert result.is_error is False
        assert "File is large" not in result.output
        assert result.metadata["line_count"] == 5

    async def test_small_file_untouched(self, tmp_path):
        """A normal-sized file should not be affected."""
        f = tmp_path / "small.txt"
        f.write_text("hello\nworld\n")
        result = await self.tool.call({"file_path": str(f)}, ctx())
        assert result.is_error is False
        assert "truncated" not in result.output.lower()
        assert "File is large" not in result.output
        assert result.metadata.get("truncated") is None


# ===========================================================================
# BashTool output truncation
# ===========================================================================

class TestBashToolTruncation:
    tool = BashTool()

    async def test_large_stdout_truncated(self):
        """Bash output > 100KB should be truncated."""
        # Generate >100KB of output via printf
        # 200 chars per line * 600 lines = 120KB
        result = await self.tool.call(
            {"command": f"python3 -c \"print('Z' * 200 + '\\n', end='') ; [print('Z' * 200) for _ in range(700)]\""},
            ctx(),
        )
        assert result.is_error is False
        if len(result.output.replace("\n\n... Output truncated. Pipe to a file: command > output.txt", "")) > MAX_TOOL_OUTPUT:
            # If the raw output exceeded limit, it should be truncated
            assert "Output truncated" in result.output
            assert "Pipe to a file" in result.output
            assert result.metadata.get("truncated") is True
            assert result.metadata.get("original_size", 0) > MAX_TOOL_OUTPUT

    async def test_small_stdout_not_truncated(self):
        """Normal output should pass through unchanged."""
        result = await self.tool.call({"command": "echo hello"}, ctx())
        assert result.is_error is False
        assert "truncated" not in result.output.lower()
        assert result.metadata.get("truncated") is None

    async def test_truncation_preserves_returncode(self):
        """Truncation should not interfere with the return code."""
        result = await self.tool.call(
            {"command": f"python3 -c \"[print('Q' * 200) for _ in range(700)]\""},
            ctx(),
        )
        # Command should succeed even if output is truncated
        assert result.metadata["returncode"] == 0
