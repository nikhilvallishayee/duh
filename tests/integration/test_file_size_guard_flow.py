"""Integration tests for the file-size guard through ReadTool.

These tests exercise the full ReadTool call path with a ToolContext that
carries a ``model`` name — verifying that:

* Large files are refused with a user-facing message (is_error=False)
* Supplying ``offset``/``limit`` bypasses the guard (explicit slicing)
* When the context has no model set, the guard is dormant (backward compat)
* Small files are unaffected
"""

from __future__ import annotations

import pytest

from duh.kernel.tool import ToolContext
from duh.tools.read import ReadTool


SMALL_CTX_MODEL = "gpt-4o"  # 128K context


def _make_large_file(path, size_bytes: int) -> None:
    """Write a text file of exactly *size_bytes* bytes using printable chars."""
    # Use printable ascii so the file decodes cleanly as UTF-8 when read.
    chunk = "a" * 1024 + "\n"
    n_chunks = size_bytes // len(chunk)
    remainder = size_bytes - n_chunks * len(chunk)
    with open(path, "w", encoding="utf-8") as f:
        for _ in range(n_chunks):
            f.write(chunk)
        if remainder:
            f.write("a" * remainder)


async def test_large_file_on_small_model_is_refused(tmp_path):
    """A 1 MB file on a 128K-context model should trip the guard."""
    big = tmp_path / "big.txt"
    _make_large_file(big, 1_000_000)  # 1 MB → 250K tokens, > 64K budget

    tool = ReadTool()
    ctx = ToolContext(cwd=str(tmp_path), model=SMALL_CTX_MODEL)
    result = await tool.call({"file_path": str(big)}, ctx)

    # Informational skip, NOT an error — the model should be able to see the
    # message and react (try a slice, switch model, skip the file).
    assert result.is_error is False
    assert "exceeds" in result.output
    assert "context window" in result.output
    assert SMALL_CTX_MODEL in result.output
    assert result.metadata.get("skipped_due_to_size") is True
    assert result.metadata.get("estimated_tokens") > 0
    assert result.metadata.get("budget_tokens") > 0


async def test_large_file_with_offset_and_limit_is_allowed(tmp_path):
    """User-supplied offset/limit indicates explicit slicing — guard skipped."""
    big = tmp_path / "big.txt"
    _make_large_file(big, 1_000_000)

    tool = ReadTool()
    ctx = ToolContext(cwd=str(tmp_path), model=SMALL_CTX_MODEL)
    result = await tool.call(
        {"file_path": str(big), "offset": 0, "limit": 10}, ctx
    )

    # When the user slices, we must not refuse with the size message.
    assert result.is_error is False
    assert "exceeds" not in result.output
    assert result.metadata.get("skipped_due_to_size") is not True


async def test_large_file_without_model_passes_through(tmp_path):
    """Backward-compat: when ctx.model is None the guard is dormant and the
    file is read normally (possibly truncated by MAX_TOOL_OUTPUT)."""
    big = tmp_path / "big.txt"
    _make_large_file(big, 1_000_000)  # 1 MB — still under MAX_FILE_READ_BYTES

    tool = ReadTool()
    ctx = ToolContext(cwd=str(tmp_path), model=None)  # explicit None
    result = await tool.call({"file_path": str(big)}, ctx)

    # No refusal message — either full content or MAX_TOOL_OUTPUT truncation.
    assert result.is_error is False
    assert "exceeds 50%" not in result.output
    assert result.metadata.get("skipped_due_to_size") is not True


async def test_small_file_on_small_model_is_unaffected(tmp_path):
    """A 1 KB file on any model should be read normally."""
    small = tmp_path / "small.txt"
    small.write_text("hello world\nsecond line\n")

    tool = ReadTool()
    ctx = ToolContext(cwd=str(tmp_path), model=SMALL_CTX_MODEL)
    result = await tool.call({"file_path": str(small)}, ctx)

    assert result.is_error is False
    assert "hello world" in result.output
    assert "exceeds" not in result.output
    assert result.metadata.get("skipped_due_to_size") is not True


async def test_large_file_with_large_context_model_is_allowed(tmp_path):
    """1 MB file on claude-opus-4-6 (1M context, 500K-token budget) is OK."""
    big = tmp_path / "big.txt"
    _make_large_file(big, 1_000_000)  # 1 MB → 250K tokens < 500K budget

    tool = ReadTool()
    ctx = ToolContext(cwd=str(tmp_path), model="claude-opus-4-6")
    result = await tool.call({"file_path": str(big)}, ctx)

    # Under budget for this model → no refusal. (May still be truncated by
    # MAX_TOOL_OUTPUT = 100 KB, but that's a separate layer.)
    assert result.is_error is False
    assert "exceeds 50%" not in result.output
    assert result.metadata.get("skipped_due_to_size") is not True
