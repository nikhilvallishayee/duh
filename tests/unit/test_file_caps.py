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


def test_session_cap_constant():
    """MAX_SESSION_BYTES constant must be exactly 64 MB."""
    from duh.adapters.file_store import MAX_SESSION_BYTES

    assert MAX_SESSION_BYTES == 64 * 1024 * 1024  # 64 MB


@pytest.mark.asyncio
async def test_file_store_save_raises_when_over_session_cap(tmp_path, monkeypatch):
    """FileStore.save() must raise ValueError when projected size exceeds MAX_SESSION_BYTES."""
    import duh.adapters.file_store as fs_module
    from duh.adapters.file_store import FileStore
    from duh.kernel.messages import Message

    # Use a tiny cap (200 bytes) so we can trigger it without writing a huge file.
    # A two-message save with 50-char content fields exceeds 200 bytes of JSON easily.
    monkeypatch.setattr(fs_module, "MAX_SESSION_BYTES", 200)

    store = FileStore(base_dir=tmp_path)

    msgs = [
        Message(role="user", content="a" * 50, id="m0", timestamp="t0"),
        Message(role="assistant", content="b" * 50, id="m1", timestamp="t1"),
    ]

    with pytest.raises(ValueError, match="session cap"):
        await store.save("s1", msgs)


@pytest.mark.asyncio
async def test_file_store_save_allows_session_under_cap(tmp_path):
    """FileStore.save() must not raise for sessions within the 64 MB cap."""
    from duh.adapters.file_store import FileStore
    from duh.kernel.messages import Message

    store = FileStore(base_dir=tmp_path)
    msg = Message(role="user", content="small message", id="m1", timestamp="t1")
    # Should not raise
    await store.save("normal", [msg])
