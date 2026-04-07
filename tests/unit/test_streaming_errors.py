"""Tests for mid-stream error handling in provider adapters and the query loop.

Validates that:
- Partial content is preserved on mid-stream disconnects
- Malformed JSON chunks are detected and yield partial content
- Complete streams still work normally
- Partial messages carry correct metadata
- The query loop skips tool extraction for partial messages
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

import httpx
import pytest

from duh.kernel.messages import Message
from duh.kernel.loop import query
from duh.kernel.deps import Deps


# ===================================================================
# Helpers
# ===================================================================

async def collect(gen: AsyncGenerator[dict[str, Any], None]) -> list[dict[str, Any]]:
    """Collect all events from an async generator."""
    events = []
    async for event in gen:
        events.append(event)
    return events


def find_events(events: list[dict], event_type: str) -> list[dict]:
    """Filter events by type."""
    return [e for e in events if e.get("type") == event_type]


def find_assistant(events: list[dict]) -> Message | None:
    """Find the first assistant message in events."""
    for e in events:
        if e.get("type") == "assistant":
            return e.get("message")
    return None


# ===================================================================
# Anthropic mid-stream errors
# ===================================================================

class TestAnthropicStreamingErrors:
    """Test mid-stream error handling in the Anthropic adapter."""

    @pytest.mark.asyncio
    async def test_connection_error_with_partial_content(self):
        """ConnectionError mid-stream yields partial message then error event."""

        async def fake_event_stream():
            """Simulate events then raise ConnectionError."""
            events = [
                SimpleNamespace(type="content_block_start",
                                index=0,
                                content_block=SimpleNamespace(type="text", text="")),
                SimpleNamespace(type="content_block_delta",
                                delta=SimpleNamespace(type="text_delta", text="Hello ")),
                SimpleNamespace(type="content_block_delta",
                                delta=SimpleNamespace(type="text_delta", text="world")),
            ]
            for ev in events:
                yield ev
            raise ConnectionError("peer reset connection")

        mock_stream_ctx = AsyncMock()
        mock_stream_obj = MagicMock()
        mock_stream_obj.__aiter__ = lambda self: fake_event_stream().__aiter__()
        mock_stream_obj.__anext__ = fake_event_stream().__anext__
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_obj)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("anthropic.AsyncAnthropic") as MockAnthropic:
            mock_client = MagicMock()
            mock_client.messages.stream = MagicMock(return_value=mock_stream_ctx)
            MockAnthropic.return_value = mock_client

            from duh.adapters.anthropic import AnthropicProvider
            provider = AnthropicProvider(api_key="test-key")
            provider._client = mock_client

            events = await collect(provider.stream(
                messages=[Message(role="user", content="hi")],
            ))

        # Should have text deltas, then a partial assistant, then an error
        deltas = find_events(events, "text_delta")
        assert len(deltas) >= 2
        assert deltas[0]["text"] == "Hello "
        assert deltas[1]["text"] == "world"

        assistant = find_assistant(events)
        assert assistant is not None
        assert assistant.metadata.get("partial") is True
        assert assistant.metadata.get("stop_reason") == "error"
        assert assistant.text == "Hello world"

        errors = find_events(events, "error")
        assert len(errors) == 1
        assert "Stream interrupted" in errors[0]["error"]

    @pytest.mark.asyncio
    async def test_timeout_error_no_content_yields_only_error(self):
        """TimeoutError with no accumulated content yields only error event."""

        async def fake_event_stream():
            raise asyncio.TimeoutError("timed out")
            yield  # pragma: no cover - unreachable, makes this an async generator

        mock_stream_ctx = AsyncMock()
        mock_stream_obj = MagicMock()
        mock_stream_obj.__aiter__ = lambda self: fake_event_stream().__aiter__()
        mock_stream_ctx.__aenter__ = AsyncMock(return_value=mock_stream_obj)
        mock_stream_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("anthropic.AsyncAnthropic") as MockAnthropic:
            mock_client = MagicMock()
            mock_client.messages.stream = MagicMock(return_value=mock_stream_ctx)
            MockAnthropic.return_value = mock_client

            from duh.adapters.anthropic import AnthropicProvider
            provider = AnthropicProvider(api_key="test-key")
            provider._client = mock_client

            events = await collect(provider.stream(
                messages=[Message(role="user", content="hi")],
            ))

        # No partial assistant (nothing accumulated), just error
        assistant = find_assistant(events)
        assert assistant is None

        errors = find_events(events, "error")
        assert len(errors) == 1
        assert "Stream interrupted" in errors[0]["error"]


# ===================================================================
# OpenAI mid-stream errors
# ===================================================================

class TestOpenAIStreamingErrors:
    """Test mid-stream error handling in the OpenAI adapter."""

    @pytest.mark.asyncio
    async def test_connection_error_with_partial_text(self):
        """ConnectionError mid-stream yields partial message with text content."""

        async def fake_chunk_stream():
            """Yield a few chunks then disconnect."""
            for text in ["Hello", " wor"]:
                chunk = MagicMock()
                chunk.choices = [MagicMock()]
                chunk.choices[0].delta = MagicMock()
                chunk.choices[0].delta.content = text
                chunk.choices[0].delta.tool_calls = None
                chunk.choices[0].finish_reason = None
                yield chunk
            raise ConnectionError("connection reset by peer")

        with patch("openai.AsyncOpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=fake_chunk_stream())
            MockOpenAI.return_value = mock_client

            from duh.adapters.openai import OpenAIProvider
            provider = OpenAIProvider(api_key="test-key")
            provider._client = mock_client

            events = await collect(provider.stream(
                messages=[Message(role="user", content="hi")],
            ))

        assistant = find_assistant(events)
        assert assistant is not None
        assert assistant.metadata.get("partial") is True
        assert assistant.text == "Hello wor"

        errors = find_events(events, "error")
        assert len(errors) == 1
        assert "Stream interrupted" in errors[0]["error"]

    @pytest.mark.asyncio
    async def test_read_error_with_partial_tool_calls(self):
        """httpx.ReadError mid-stream preserves partial tool call data."""

        async def fake_chunk_stream():
            # First chunk: start of a tool call
            chunk1 = MagicMock()
            chunk1.choices = [MagicMock()]
            chunk1.choices[0].delta = MagicMock()
            chunk1.choices[0].delta.content = None
            tc = MagicMock()
            tc.index = 0
            tc.id = "call_123"
            tc.function = MagicMock()
            tc.function.name = "Read"
            tc.function.arguments = '{"path":'
            chunk1.choices[0].delta.tool_calls = [tc]
            chunk1.choices[0].finish_reason = None
            yield chunk1

            raise httpx.ReadError("Connection closed")

        with patch("openai.AsyncOpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=fake_chunk_stream())
            MockOpenAI.return_value = mock_client

            from duh.adapters.openai import OpenAIProvider
            provider = OpenAIProvider(api_key="test-key")
            provider._client = mock_client

            events = await collect(provider.stream(
                messages=[Message(role="user", content="hi")],
            ))

        assistant = find_assistant(events)
        assert assistant is not None
        assert assistant.metadata.get("partial") is True
        # Tool call should be present (with empty/partial input since JSON was incomplete)
        tool_blocks = [b for b in assistant.content if isinstance(b, dict) and b.get("type") == "tool_use"]
        assert len(tool_blocks) == 1
        assert tool_blocks[0]["name"] == "Read"

    @pytest.mark.asyncio
    async def test_timeout_error_no_content(self):
        """asyncio.TimeoutError with no content yields only error, no partial message."""

        async def fake_chunk_stream():
            raise asyncio.TimeoutError("timed out")
            yield  # pragma: no cover

        with patch("openai.AsyncOpenAI") as MockOpenAI:
            mock_client = MagicMock()
            mock_client.chat.completions.create = AsyncMock(return_value=fake_chunk_stream())
            MockOpenAI.return_value = mock_client

            from duh.adapters.openai import OpenAIProvider
            provider = OpenAIProvider(api_key="test-key")
            provider._client = mock_client

            events = await collect(provider.stream(
                messages=[Message(role="user", content="hi")],
            ))

        assistant = find_assistant(events)
        assert assistant is None

        errors = find_events(events, "error")
        assert len(errors) == 1


# ===================================================================
# Ollama mid-stream errors
# ===================================================================

class TestOllamaStreamingErrors:
    """Test mid-stream error handling in the Ollama adapter."""

    @pytest.mark.asyncio
    async def test_read_error_with_partial_content(self):
        """httpx.ReadError mid-stream yields partial content then error."""
        from duh.adapters.ollama import OllamaProvider

        chunks = [
            json.dumps({"message": {"content": "Hello "}, "done": False}),
            json.dumps({"message": {"content": "wor"}, "done": False}),
        ]

        async def fake_aiter_lines():
            for line in chunks:
                yield line
            raise httpx.ReadError("Connection lost")

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = fake_aiter_lines

        mock_response_ctx = AsyncMock()
        mock_response_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_response_ctx)
        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client_ctx):
            provider = OllamaProvider()
            events = await collect(provider.stream(
                messages=[Message(role="user", content="hi")],
            ))

        deltas = find_events(events, "text_delta")
        assert len(deltas) == 2

        assistant = find_assistant(events)
        assert assistant is not None
        assert assistant.metadata.get("partial") is True
        assert assistant.text == "Hello wor"

        errors = find_events(events, "error")
        assert len(errors) == 1
        assert "Stream interrupted" in errors[0]["error"]

    @pytest.mark.asyncio
    async def test_malformed_json_with_partial_content(self):
        """Malformed JSON chunk yields partial content then error event."""
        from duh.adapters.ollama import OllamaProvider

        chunks = [
            json.dumps({"message": {"content": "Got so far"}, "done": False}),
            "this is not valid json {{{",
        ]

        async def fake_aiter_lines():
            for line in chunks:
                yield line

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = fake_aiter_lines

        mock_response_ctx = AsyncMock()
        mock_response_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_response_ctx)
        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client_ctx):
            provider = OllamaProvider()
            events = await collect(provider.stream(
                messages=[Message(role="user", content="hi")],
            ))

        assistant = find_assistant(events)
        assert assistant is not None
        assert assistant.metadata.get("partial") is True
        assert assistant.text == "Got so far"

        errors = find_events(events, "error")
        assert len(errors) == 1
        assert "Malformed JSON" in errors[0]["error"]

    @pytest.mark.asyncio
    async def test_malformed_json_no_content_yields_only_error(self):
        """Malformed JSON with no accumulated text yields only error, no partial message."""
        from duh.adapters.ollama import OllamaProvider

        chunks = ["this is garbage"]

        async def fake_aiter_lines():
            for line in chunks:
                yield line

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.aiter_lines = fake_aiter_lines

        mock_response_ctx = AsyncMock()
        mock_response_ctx.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_client = AsyncMock()
        mock_client.stream = MagicMock(return_value=mock_response_ctx)
        mock_client_ctx = AsyncMock()
        mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_ctx.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client_ctx):
            provider = OllamaProvider()
            events = await collect(provider.stream(
                messages=[Message(role="user", content="hi")],
            ))

        assistant = find_assistant(events)
        assert assistant is None

        errors = find_events(events, "error")
        assert len(errors) == 1
        assert "Malformed JSON" in errors[0]["error"]


# ===================================================================
# Query loop with partial messages
# ===================================================================

class TestLoopPartialMessages:
    """Test that the query loop handles partial messages correctly."""

    @pytest.mark.asyncio
    async def test_partial_message_skips_tool_extraction(self):
        """Partial assistant message with tool_use blocks should NOT trigger tool execution."""

        async def fake_stream(**kwargs):
            yield {"type": "text_delta", "text": "partial "}
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[
                        {"type": "text", "text": "partial "},
                        {"type": "tool_use", "id": "tu_1", "name": "Read",
                         "input": {}},  # incomplete tool call
                    ],
                    metadata={"partial": True, "stop_reason": "error"},
                ),
            }
            yield {"type": "error", "error": "Stream interrupted: connection reset"}

        deps = Deps(call_model=fake_stream)
        events = await collect(query(
            messages=[Message(role="user", content="hi")],
            deps=deps,
        ))

        # Should yield done with stop_reason="error", NOT try to execute the tool
        done_events = find_events(events, "done")
        assert len(done_events) == 1
        assert done_events[0]["stop_reason"] == "error"

        # Should NOT have any tool_use or tool_result events
        tool_events = find_events(events, "tool_use")
        assert len(tool_events) == 0
        tool_results = find_events(events, "tool_result")
        assert len(tool_results) == 0

    @pytest.mark.asyncio
    async def test_non_partial_message_still_extracts_tools(self):
        """Normal (non-partial) assistant message with tool_use triggers execution."""

        call_count = 0

        async def fake_stream(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                yield {
                    "type": "assistant",
                    "message": Message(
                        role="assistant",
                        content=[
                            {"type": "tool_use", "id": "tu_1", "name": "Read",
                             "input": {"path": "/tmp/x"}},
                        ],
                        metadata={"stop_reason": "tool_use"},
                    ),
                }
            else:
                yield {
                    "type": "assistant",
                    "message": Message(
                        role="assistant",
                        content=[{"type": "text", "text": "done"}],
                        metadata={"stop_reason": "end_turn"},
                    ),
                }

        async def fake_run_tool(name, inp):
            return "file contents"

        deps = Deps(call_model=fake_stream, run_tool=fake_run_tool)
        events = await collect(query(
            messages=[Message(role="user", content="read /tmp/x")],
            deps=deps,
            tools=[SimpleNamespace(name="Read", description="Read a file",
                                   input_schema={"type": "object"})],
        ))

        # Should have tool_use and tool_result events
        tool_events = find_events(events, "tool_use")
        assert len(tool_events) == 1
        assert tool_events[0]["name"] == "Read"

        tool_results = find_events(events, "tool_result")
        assert len(tool_results) == 1

    @pytest.mark.asyncio
    async def test_complete_stream_works_normally(self):
        """A complete stream without errors works as before."""

        async def fake_stream(**kwargs):
            yield {"type": "text_delta", "text": "Hello world"}
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": "Hello world"}],
                    metadata={"stop_reason": "end_turn"},
                ),
            }

        deps = Deps(call_model=fake_stream)
        events = await collect(query(
            messages=[Message(role="user", content="hi")],
            deps=deps,
        ))

        assistant = find_assistant(events)
        assert assistant is not None
        assert assistant.metadata.get("partial") is None or assistant.metadata.get("partial") is False
        assert assistant.text == "Hello world"

        done_events = find_events(events, "done")
        assert len(done_events) == 1
        assert done_events[0]["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_partial_message_metadata_correct(self):
        """Partial message has partial=True and stop_reason=error in metadata."""

        async def fake_stream(**kwargs):
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": "partial data"}],
                    metadata={"partial": True, "stop_reason": "error", "usage": {}},
                ),
            }

        deps = Deps(call_model=fake_stream)
        events = await collect(query(
            messages=[Message(role="user", content="hi")],
            deps=deps,
        ))

        assistant = find_assistant(events)
        assert assistant is not None
        assert assistant.metadata["partial"] is True
        assert assistant.metadata["stop_reason"] == "error"

        done_events = find_events(events, "done")
        assert len(done_events) == 1
        assert done_events[0]["stop_reason"] == "error"
