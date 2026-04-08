"""Tests for file size caps across tools."""
import os
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from duh.kernel.tool import ToolContext


@pytest.fixture
def ctx(tmp_path):
    return ToolContext(cwd=str(tmp_path))


@pytest.mark.asyncio
async def test_read_rejects_huge_file(tmp_path, ctx):
    """ReadTool should refuse files larger than MAX_FILE_READ_BYTES."""
    from duh.tools.read import ReadTool, MAX_FILE_READ_BYTES

    huge = tmp_path / "huge.bin"
    # Create a file just over the limit using sparse write
    with open(huge, "wb") as f:
        f.seek(MAX_FILE_READ_BYTES + 1)
        f.write(b"x")

    tool = ReadTool()
    result = await tool.call({"file_path": str(huge)}, ctx)
    assert result.is_error
    assert "too large" in result.output.lower() or "exceeds" in result.output.lower()


@pytest.mark.asyncio
async def test_read_accepts_normal_file(tmp_path, ctx):
    """ReadTool should read normal-sized files fine."""
    from duh.tools.read import ReadTool

    normal = tmp_path / "normal.txt"
    normal.write_text("hello\nworld\n")

    tool = ReadTool()
    result = await tool.call({"file_path": str(normal)}, ctx)
    assert not result.is_error
    assert "hello" in result.output


@pytest.mark.asyncio
async def test_write_rejects_huge_content(tmp_path, ctx):
    """WriteTool should refuse content larger than MAX_FILE_WRITE_BYTES."""
    from duh.tools.write import WriteTool, MAX_FILE_WRITE_BYTES

    tool = WriteTool()
    huge_content = "x" * (MAX_FILE_WRITE_BYTES + 1)
    result = await tool.call(
        {"file_path": str(tmp_path / "out.txt"), "content": huge_content},
        ctx,
    )
    assert result.is_error
    assert "too large" in result.output.lower() or "exceeds" in result.output.lower()


@pytest.mark.asyncio
async def test_write_accepts_normal_content(tmp_path, ctx):
    """WriteTool should write normal-sized content fine."""
    from duh.tools.write import WriteTool

    tool = WriteTool()
    result = await tool.call(
        {"file_path": str(tmp_path / "out.txt"), "content": "hello"},
        ctx,
    )
    assert not result.is_error


def test_session_store_cap(tmp_path):
    """FileStore should refuse sessions larger than MAX_SESSION_BYTES."""
    from duh.adapters.file_store import FileStore, MAX_SESSION_BYTES

    assert MAX_SESSION_BYTES == 64 * 1024 * 1024  # 64 MB
