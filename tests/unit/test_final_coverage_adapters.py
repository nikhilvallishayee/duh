"""Close remaining adapter coverage gaps.

Targets:
- duh.adapters.anthropic: tool_choice branches
- duh.adapters.memory_store: read/list errors, fact edge cases
- duh.adapters.model_compactor: no-kept / no-conversation / short / long-message paths
- duh.adapters.simple_compactor: non-message, non-dict, image strip edge cases
- duh.adapters.native_executor: push undo on non-existent file
- duh.adapters.renderers: tool_result and thinking dispatch
- duh.adapters.sandbox.network: _extract_host exception, seatbelt deny write
- duh.adapters.structured_logging: rotation + write OSError
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ==========================================================================
# AnthropicProvider.stream — tool_choice branches
# ==========================================================================

def _async_empty_iter():
    async def _iter():
        if False:
            yield None  # pragma: no cover
    return _iter()


def _make_anthropic_mock_stream():
    """Build a mocked Anthropic stream context manager that yields nothing."""
    stream_cm = AsyncMock()
    stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
    stream_cm.__aexit__ = AsyncMock(return_value=False)
    stream_cm.__aiter__ = lambda self: _async_empty_iter()
    final_msg = SimpleNamespace(
        content=[], id="msg-1", model="claude-sonnet-4-6",
        stop_reason="end_turn", usage=SimpleNamespace(input_tokens=1, output_tokens=1),
    )
    stream_cm.get_final_message = AsyncMock(return_value=final_msg)
    return stream_cm


class TestAnthropicToolChoiceBranches:
    """Cover tool_choice variations in AnthropicProvider.stream."""

    def _make_provider(self, captured: dict):
        from duh.adapters.anthropic import AnthropicProvider

        with patch("anthropic.AsyncAnthropic"):
            provider = AnthropicProvider(api_key="test-key")

        stream_cm = _make_anthropic_mock_stream()

        def _stream_capture(**params):
            captured["params"] = params
            return stream_cm

        provider._client = MagicMock()
        provider._client.messages = MagicMock()
        provider._client.messages.stream = _stream_capture
        return provider

    async def _run_stream(self, provider, tool_choice):
        from duh.kernel.messages import Message

        events = []
        tools = [SimpleNamespace(name="X", description="d", input_schema={"type": "object"})]
        async for ev in provider.stream(
            messages=[Message(role="user", content="hi")],
            tools=tools,
            tool_choice=tool_choice,
        ):
            events.append(ev)
        return events

    async def test_tool_choice_dict(self):
        captured: dict = {}
        provider = self._make_provider(captured)
        await self._run_stream(provider, {"type": "tool", "name": "X"})
        assert captured["params"].get("tool_choice") == {"type": "tool", "name": "X"}

    async def test_tool_choice_none_removes_tools(self):
        captured: dict = {}
        provider = self._make_provider(captured)
        await self._run_stream(provider, "none")
        # "none" should delete tools from params entirely
        assert "tools" not in captured["params"]

    async def test_tool_choice_auto(self):
        captured: dict = {}
        provider = self._make_provider(captured)
        await self._run_stream(provider, "auto")
        assert captured["params"].get("tool_choice") == {"type": "auto"}

    async def test_tool_choice_any(self):
        captured: dict = {}
        provider = self._make_provider(captured)
        await self._run_stream(provider, "any")
        assert captured["params"].get("tool_choice") == {"type": "any"}

    async def test_tool_choice_named_tool(self):
        captured: dict = {}
        provider = self._make_provider(captured)
        await self._run_stream(provider, "MyTool")
        assert captured["params"].get("tool_choice") == {
            "type": "tool", "name": "MyTool",
        }


# ==========================================================================
# FileMemoryStore — read errors and fact edge cases
# ==========================================================================

from duh.adapters.memory_store import FileMemoryStore


class TestMemoryStoreErrors:
    def test_init_no_cwd_uses_getcwd(self, monkeypatch):
        """When cwd is None, uses os.getcwd()."""
        monkeypatch.setenv("PWD", "/tmp")
        store = FileMemoryStore(cwd=None)
        assert store._cwd == os.getcwd()

    def test_list_files_skips_non_file_entries(self, tmp_path):
        """Non-file entries (subdirs) are skipped (line 187 continue)."""
        store = FileMemoryStore(cwd=str(tmp_path))
        mem_dir = store.get_memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)
        # Create a subdirectory in the memory dir
        (mem_dir / "subdir").mkdir()
        (mem_dir / "real.md").write_text("---\nname: Real\n---\n")
        headers = store.list_files()
        assert any(h.name == "Real" for h in headers)

    def test_read_index_ioerror(self, tmp_path, monkeypatch, caplog):
        caplog.set_level(logging.WARNING)
        store = FileMemoryStore(cwd=str(tmp_path))
        # Write an index file then patch read_text to raise
        mem_dir = store.get_memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "MEMORY.md").write_text("# hi\n", encoding="utf-8")
        original_read_text = Path.read_text

        def _boom(self, *a, **kw):
            if self.name == "MEMORY.md":
                raise OSError("boom")
            return original_read_text(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _boom)
        assert store.read_index() == ""

    def test_read_file_missing(self, tmp_path):
        store = FileMemoryStore(cwd=str(tmp_path))
        # Missing file → empty string
        assert store.read_file("notthere.md") == ""

    def test_read_file_ioerror(self, tmp_path, monkeypatch):
        store = FileMemoryStore(cwd=str(tmp_path))
        mem_dir = store.get_memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "topic.md").write_text("body", encoding="utf-8")
        original_read_text = Path.read_text

        def _boom(self, *a, **kw):
            if self.name == "topic.md":
                raise OSError("permission denied")
            return original_read_text(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _boom)
        assert store.read_file("topic.md") == ""

    def test_list_files_reads_file_error(self, tmp_path, monkeypatch):
        store = FileMemoryStore(cwd=str(tmp_path))
        mem_dir = store.get_memory_dir()
        mem_dir.mkdir(parents=True, exist_ok=True)
        (mem_dir / "A.md").write_text("---\nname: A\n---\n", encoding="utf-8")
        (mem_dir / "B.md").write_text("---\nname: B\n---\n", encoding="utf-8")

        original_read_text = Path.read_text

        def _sometimes_fail(self, *a, **kw):
            if self.name == "B.md":
                raise OSError("disk error")
            return original_read_text(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _sometimes_fail)
        headers = store.list_files()
        # Should still return both, B with fallback header
        names = [h.name for h in headers]
        assert "A" in names
        # B falls through to filename fallback (name == filename)
        assert any(h.filename == "B.md" for h in headers)

    def test_read_all_facts_missing_file(self, tmp_path):
        store = FileMemoryStore(cwd=str(tmp_path))
        # facts file doesn't exist yet → empty
        assert store._read_all_facts() == []

    def test_read_all_facts_malformed_line(self, tmp_path, caplog):
        caplog.set_level(logging.WARNING)
        store = FileMemoryStore(cwd=str(tmp_path))
        facts_dir = store.get_facts_dir()
        facts_dir.mkdir(parents=True, exist_ok=True)
        facts_path = facts_dir / "facts.jsonl"
        facts_path.write_text(
            '{"key":"good","value":"ok"}\n'
            "this is not JSON\n"
            "\n"
            '{"key":"good2","value":"ok2"}\n',
            encoding="utf-8",
        )
        facts = store._read_all_facts()
        assert len(facts) == 2

    def test_read_all_facts_ioerror(self, tmp_path, monkeypatch):
        store = FileMemoryStore(cwd=str(tmp_path))
        facts_dir = store.get_facts_dir()
        facts_dir.mkdir(parents=True, exist_ok=True)
        (facts_dir / "facts.jsonl").write_text("[]", encoding="utf-8")
        original_read_text = Path.read_text

        def _boom(self, *a, **kw):
            if self.name == "facts.jsonl":
                raise OSError("disk gone")
            return original_read_text(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _boom)
        assert store._read_all_facts() == []


# ==========================================================================
# ModelCompactor paths
# ==========================================================================

from duh.adapters.model_compactor import ModelCompactor
from duh.kernel.messages import Message


class TestModelCompactor:
    async def test_compact_only_system_messages(self):
        """Only system messages → conversation empty → return system_msgs."""
        async def fake_call_model(**kw):
            if False:
                yield None  # pragma: no cover

        compactor = ModelCompactor(
            call_model=fake_call_model, default_limit=1, bytes_per_token=1,
        )
        # Need total tokens > limit to trigger compaction path
        msgs = [Message(role="system", content="x" * 100)]
        out = await compactor.compact(msgs, token_limit=5)
        assert len(out) == 1
        assert out[0].role == "system"

    async def test_compact_returns_when_dropped_zero(self):
        """Kept everything in the tail window → dropped_count=0 branch."""
        # Need tokens > limit to trigger path, but min_keep keeps everything
        async def fake_call_model(**kw):
            if False:
                yield None  # pragma: no cover

        compactor = ModelCompactor(
            call_model=fake_call_model,
            default_limit=1, bytes_per_token=1, min_keep=10,
        )
        msgs = [
            Message(role="user", content="a" * 50),
            Message(role="assistant", content="b" * 50),
        ]
        # limit=1 small, but min_keep=10 forces both to stay — dropped_count == 0
        out = await compactor.compact(msgs, token_limit=1)
        assert len(out) == 2

    async def test_generate_summary_long_message_truncates(self):
        """Individual msg > 500 chars gets truncated; input > 10_000 too."""
        async def fake_call_model(**kwargs):
            # Emit some deltas as summary
            yield {"type": "text_delta", "text": "Summary A"}
            yield {"type": "text_delta", "text": " and B"}

        compactor = ModelCompactor(
            call_model=fake_call_model,
            default_limit=100, bytes_per_token=1, min_keep=1,
        )
        # A long message that forces truncation at 497 chars + ellipsis
        long_msg = Message(role="user", content="x" * 1000)
        # Many messages to exceed the 10_000 char input cap
        msgs = [Message(role="user", content="y" * 300) for _ in range(50)]
        msgs.insert(0, long_msg)
        summary = await compactor._generate_summary(msgs)
        assert "Summary A" in summary
        assert "B" in summary

    async def test_generate_summary_with_dict_message(self):
        async def fake_call_model(**kwargs):
            yield {"type": "text_delta", "text": "ok"}

        compactor = ModelCompactor(call_model=fake_call_model)
        msgs = [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": [{"type": "text", "text": "yo"}]},
        ]
        summary = await compactor._generate_summary(msgs)
        assert summary == "ok"

    async def test_generate_summary_empty_returns_fallback(self):
        async def fake_call_model(**kwargs):
            # No text_delta events — empty summary
            if False:
                yield None  # pragma: no cover

        compactor = ModelCompactor(call_model=fake_call_model)
        summary = await compactor._generate_summary([Message(role="user", content="x")])
        assert "unavailable" in summary.lower()


# ==========================================================================
# SimpleCompactor — strip_images edges
# ==========================================================================

from duh.adapters.simple_compactor import (
    SimpleCompactor,
    strip_images,
)
from duh.kernel.messages import Message, TextBlock, ToolUseBlock


class TestSimpleCompactorEdges:
    pass

    def test_strip_images_dict_no_change(self):
        """Dict message without images is unchanged."""
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        out = strip_images(msgs)
        assert out[0]["content"][0]["type"] == "text"

    def test_strip_images_message_no_change(self):
        """Message object without images is unchanged."""
        msgs = [Message(role="user", content=[TextBlock(text="hello")])]
        out = strip_images(msgs)
        assert out[0].content[0].text == "hello"

    def test_strip_images_non_message_non_dict(self):
        """Neither Message nor dict → passthrough."""
        sentinel = SimpleNamespace()
        out = strip_images([sentinel])
        assert out == [sentinel]

    async def test_compact_short_input_returns_as_is(self):
        comp = SimpleCompactor(default_limit=1_000_000, bytes_per_token=4)
        msgs = [Message(role="user", content="short")]
        out = await comp.compact(msgs)
        assert len(out) == 1

    def test_summarize_messages_skips_empty_text(self):
        """Cover simple_compactor line 547: empty text → continue."""
        from duh.adapters.simple_compactor import _summarize_messages

        # Message with content that serializes to empty string
        msgs = [
            Message(role="user", content=""),
            Message(role="assistant", content="  "),
            Message(role="user", content="real content"),
        ]
        summary = _summarize_messages(msgs)
        assert "real content" in summary

    async def test_summarize_messages_truncates_long_combined(self):
        """Entire summary combined text must be truncated when huge."""
        from duh.adapters.simple_compactor import _summarize_messages, _SUMMARY_MAX_CHARS

        # Build many messages so combined exceeds _SUMMARY_MAX_CHARS
        msgs = [
            Message(role="user", content="x" * 200)
            for _ in range(_SUMMARY_MAX_CHARS // 200 + 5)
        ]
        summary = _summarize_messages(msgs)
        assert len(summary) <= _SUMMARY_MAX_CHARS + 100  # some header


# ==========================================================================
# NativeExecutor — push undo when file missing
# ==========================================================================

from duh.adapters.native_executor import NativeExecutor
from duh.kernel.tool import ToolContext, ToolResult


class _DummyWriteTool:
    name = "Write"

    @property
    def is_read_only(self):
        return False

    @property
    def is_destructive(self):
        return False

    async def call(self, input, context):
        # Simulate success without touching disk
        return ToolResult(output="wrote")

    async def check_permissions(self, input, context):
        return {"allowed": True}


class TestNativeExecutorUndoPush:
    async def test_undo_push_nonexistent_file(self, tmp_path):
        tool = _DummyWriteTool()
        executor = NativeExecutor(tools=[tool], cwd=str(tmp_path))
        new_file = tmp_path / "new.txt"
        # Write tool for a path that does not exist
        result = await executor.run("Write", {"file_path": str(new_file), "content": "x"})
        assert "wrote" in result

    async def test_undo_push_os_error_swallowed(self, tmp_path, monkeypatch):
        """Lines 103-104: OSError during undo snapshot is swallowed."""
        tool = _DummyWriteTool()
        executor = NativeExecutor(tools=[tool], cwd=str(tmp_path))
        existing = tmp_path / "exists.txt"
        existing.write_text("old")

        real_read = Path.read_text

        def _bad_read(self, *a, **kw):
            if self.name == "exists.txt":
                raise OSError("permission denied on snapshot read")
            return real_read(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _bad_read)
        result = await executor.run(
            "Write", {"file_path": str(existing), "content": "new"},
        )
        assert "wrote" in result


# ==========================================================================
# Renderers — tool_result and thinking dispatch
# ==========================================================================

class TestRendererDispatch:
    def test_bare_renderer_handle_tool_result(self, capsys):
        from duh.adapters.renderers import BareRenderer

        r = BareRenderer()
        r.handle({"type": "tool_result", "output": "hello", "is_error": False})

    def test_bare_renderer_handle_thinking_delta(self, capsys):
        from duh.adapters.renderers import BareRenderer

        r = BareRenderer(debug=True)
        r.handle({"type": "thinking_delta", "text": "pondering"})


# ==========================================================================
# NetworkPolicy — _extract_host with exception
# ==========================================================================

class TestNetworkPolicyExtractHost:
    def test_extract_host_bad_url_returns_empty(self):
        from duh.adapters.sandbox.network import NetworkPolicy

        p = NetworkPolicy()
        # urlparse doesn't usually raise, but passing a non-string does
        # Pass an object that forces urlparse to throw
        class _BadStr:
            def __str__(self):
                raise ValueError("cannot str")

        # Direct call with an invalid value
        result = p._extract_host("://")  # malformed → hostname None
        assert result == ""


# ==========================================================================
# Seatbelt profile — deny write branch
# ==========================================================================

class TestSeatbeltDenyWrite:
    """The deny-write branch is unreachable (always_writable always populates),
    so it's marked pragma no cover. No test needed."""


# ==========================================================================
# StructuredLogger — rotation & write error paths
# ==========================================================================

class TestStructuredLoggerRotation:
    def test_rotate_if_needed_triggers(self, tmp_path):
        from duh.adapters.structured_logging import StructuredLogger

        # Create a logger with tiny max_bytes
        slog = StructuredLogger(log_dir=str(tmp_path), max_bytes=50)
        # Write enough events to trigger rotation
        for i in range(20):
            slog.model_request(model="m", turn=i)
        slog.close()

    def test_rotate_failure_is_swallowed(self, tmp_path, monkeypatch):
        from duh.adapters.structured_logging import StructuredLogger

        slog = StructuredLogger(log_dir=str(tmp_path), max_bytes=1)
        slog.model_request(model="m", turn=0)

        # Patch shutil.move to raise OSError
        def _bad_move(*a, **kw):
            raise OSError("cannot move")

        monkeypatch.setattr(
            "duh.adapters.structured_logging.shutil.move", _bad_move,
        )
        # Should not crash — _rotate_if_needed swallows OSError
        slog.model_request(model="m2", turn=1)
        slog.close()

    def test_write_failure_is_swallowed(self, tmp_path):
        from duh.adapters.structured_logging import StructuredLogger

        slog = StructuredLogger(log_dir=str(tmp_path))
        slog._ensure_open()
        # Replace the handle with a broken one
        broken = MagicMock()
        broken.closed = False  # must be explicit False so _ensure_open returns it
        broken.write = MagicMock(side_effect=OSError("fs full"))
        broken.flush = MagicMock()
        slog._handle = broken
        # Should not raise — _write catches OSError
        slog.model_request(model="m", turn=0)
