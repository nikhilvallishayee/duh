"""Tests for GrepTool bounds — max_results, binary detection, streaming reads."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path
from unittest.mock import patch

import pytest

from duh.kernel.tool import ToolContext, ToolResult
from duh.tools.grep import GrepTool, _DEFAULT_MAX_RESULTS, _is_binary


@pytest.fixture
def tool():
    return GrepTool()


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(cwd=str(tmp_path))


# ---------------------------------------------------------------------------
# max_results
# ---------------------------------------------------------------------------


class TestMaxResults:
    """max_results caps the number of returned matches."""

    async def test_limits_output(self, tool, ctx, tmp_path):
        """When a file has more matches than max_results, only max_results are returned."""
        big = tmp_path / "big.txt"
        big.write_text("\n".join(f"match line {i}" for i in range(100)))

        result = await tool.call(
            {"pattern": "match", "path": str(tmp_path), "max_results": 10},
            ctx,
        )
        assert not result.is_error
        lines = [l for l in result.output.splitlines() if l and not l.startswith("...")]
        assert len(lines) == 10
        assert result.metadata["truncated"] is True

    async def test_default_is_500(self, tool):
        """The schema default and the runtime default are both 500."""
        schema_default = tool.input_schema["properties"]["max_results"]["default"]
        assert schema_default == 500
        assert _DEFAULT_MAX_RESULTS == 500

    async def test_no_truncation_below_limit(self, tool, ctx, tmp_path):
        """When matches < max_results, no truncation happens."""
        f = tmp_path / "small.txt"
        f.write_text("aaa\nbbb\naaa\n")

        result = await tool.call(
            {"pattern": "aaa", "path": str(tmp_path), "max_results": 100},
            ctx,
        )
        assert not result.is_error
        assert result.metadata.get("truncated") is False
        assert "truncated" not in result.output


# ---------------------------------------------------------------------------
# Truncation note
# ---------------------------------------------------------------------------


class TestTruncationNote:
    """When results are truncated, the output includes a human-readable note."""

    async def test_truncation_note_present(self, tool, ctx, tmp_path):
        f = tmp_path / "lines.txt"
        f.write_text("\n".join(f"hit {i}" for i in range(50)))

        result = await tool.call(
            {"pattern": "hit", "path": str(tmp_path), "max_results": 5},
            ctx,
        )
        assert "results truncated" in result.output
        assert "showing 5 of 5+ matches" in result.output

    async def test_no_note_when_not_truncated(self, tool, ctx, tmp_path):
        f = tmp_path / "lines.txt"
        f.write_text("one\ntwo\nthree\n")

        result = await tool.call(
            {"pattern": "o", "path": str(tmp_path), "max_results": 100},
            ctx,
        )
        assert "truncated" not in result.output


# ---------------------------------------------------------------------------
# Binary file detection
# ---------------------------------------------------------------------------


class TestBinaryDetection:
    """Binary files (containing null bytes in first 8 KB) are skipped."""

    async def test_binary_file_skipped(self, tool, ctx, tmp_path):
        """A file with null bytes in the first 8 KB is treated as binary and skipped."""
        binf = tmp_path / "image.dat"
        binf.write_bytes(b"match\x00\x00\x00 more match data")

        result = await tool.call(
            {"pattern": "match", "path": str(tmp_path)},
            ctx,
        )
        assert result.output == "No matches found."

    async def test_text_file_not_skipped(self, tool, ctx, tmp_path):
        """A normal text file is searched normally."""
        txt = tmp_path / "readme.txt"
        txt.write_text("hello world\n")

        result = await tool.call(
            {"pattern": "hello", "path": str(tmp_path)},
            ctx,
        )
        assert not result.is_error
        assert "hello" in result.output

    def test_is_binary_helper_with_null_bytes(self, tmp_path):
        f = tmp_path / "bin.dat"
        f.write_bytes(b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR")
        assert _is_binary(f) is True

    def test_is_binary_helper_with_text(self, tmp_path):
        f = tmp_path / "text.py"
        f.write_text("print('hello')\n")
        assert _is_binary(f) is False

    def test_is_binary_returns_false_on_error(self, tmp_path):
        """If the file can't be read, _is_binary returns False (not an exception)."""
        fake = tmp_path / "nonexistent"
        assert _is_binary(fake) is False


# ---------------------------------------------------------------------------
# Line-by-line reading (no full-file load)
# ---------------------------------------------------------------------------


class TestLineByLineReading:
    """Verify grep reads files line-by-line, not via read_text() + splitlines()."""

    async def test_does_not_call_read_text(self, tool, ctx, tmp_path):
        """The tool uses open() iterator, NOT Path.read_text()."""
        f = tmp_path / "data.txt"
        f.write_text("needle in haystack\nhaystack\nneedle again\n")

        with patch.object(Path, "read_text", side_effect=AssertionError(
            "read_text should not be called — grep must use line-by-line reading"
        )):
            result = await tool.call(
                {"pattern": "needle", "path": str(f)},
                ctx,
            )

        assert not result.is_error
        assert result.metadata["match_count"] == 2

    async def test_large_file_with_early_exit(self, tool, ctx, tmp_path):
        """With max_results=3 on a 10000-line file, we stop early."""
        big = tmp_path / "huge.txt"
        # Write a large file
        big.write_text("\n".join(f"match line {i}" for i in range(10_000)))

        result = await tool.call(
            {"pattern": "match", "path": str(big), "max_results": 3},
            ctx,
        )
        assert not result.is_error
        lines = [l for l in result.output.splitlines() if l and not l.startswith("...")]
        assert len(lines) == 3
        assert result.metadata["truncated"] is True
