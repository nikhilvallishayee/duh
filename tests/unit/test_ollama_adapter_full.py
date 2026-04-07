"""Full coverage for duh.adapters.ollama — streaming, error paths, branching."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from duh.adapters.ollama import OllamaProvider
from duh.kernel.messages import Message


class MockResponse:
    """Mock httpx streaming response."""

    def __init__(self, status_code: int, lines: list[str] | None = None,
                 body: bytes = b""):
        self.status_code = status_code
        self._lines = lines or []
        self._body = body

    async def aread(self):
        return self._body

    async def aiter_lines(self):
        for line in self._lines:
            yield line

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class MockClient:
    """Mock httpx.AsyncClient."""

    def __init__(self, response: MockResponse):
        self._response = response

    def stream(self, method, url, **kwargs):
        return self._response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


class TestOllamaStreamTextResponse:
    async def test_simple_text_stream(self):
        lines = [
            json.dumps({"message": {"content": "Hello"}, "done": False}),
            json.dumps({"message": {"content": " world"}, "done": True}),
        ]
        response = MockResponse(200, lines)

        with patch("httpx.AsyncClient", return_value=MockClient(response)):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(
                messages=[Message(role="user", content="hi")],
            ):
                events.append(evt)

        types = [e["type"] for e in events]
        assert "text_delta" in types
        assert "assistant" in types

        text_deltas = [e for e in events if e["type"] == "text_delta"]
        assert text_deltas[0]["text"] == "Hello"
        assert text_deltas[1]["text"] == " world"


class TestOllamaStreamErrorPaths:
    async def test_http_error(self):
        response = MockResponse(404, body=b'{"error": "model not found"}')

        with patch("httpx.AsyncClient", return_value=MockClient(response)):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[]):
                events.append(evt)

        assert events[0]["type"] == "assistant"
        assert events[0]["message"].metadata["is_error"] is True
        assert "Pull it first" in events[0]["message"].text

    async def test_chunk_error(self):
        lines = [
            json.dumps({"error": "out of memory"}),
        ]
        response = MockResponse(200, lines)

        with patch("httpx.AsyncClient", return_value=MockClient(response)):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[]):
                events.append(evt)

        assert events[0]["type"] == "assistant"
        assert "out of memory" in events[0]["message"].text

    async def test_connect_error(self):
        with patch("httpx.AsyncClient", side_effect=httpx.ConnectError("refused")):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[]):
                events.append(evt)

        assert events[0]["type"] == "assistant"
        assert events[0]["message"].metadata["is_error"] is True
        assert "Cannot connect" in events[0]["message"].text

    async def test_generic_exception(self):
        with patch("httpx.AsyncClient", side_effect=RuntimeError("unexpected")):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[]):
                events.append(evt)

        assert events[0]["type"] == "assistant"
        assert "unexpected" in events[0]["message"].text

    async def test_json_decode_error_skipped(self):
        lines = [
            "not valid json",
            json.dumps({"message": {"content": "ok"}, "done": True}),
        ]
        response = MockResponse(200, lines)

        with patch("httpx.AsyncClient", return_value=MockClient(response)):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[]):
                events.append(evt)

        text_deltas = [e for e in events if e["type"] == "text_delta"]
        assert len(text_deltas) == 1  # only the valid line

    async def test_empty_line_skipped(self):
        lines = [
            "",
            "  ",
            json.dumps({"message": {"content": "ok"}, "done": True}),
        ]
        response = MockResponse(200, lines)

        with patch("httpx.AsyncClient", return_value=MockClient(response)):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[]):
                events.append(evt)

        text_deltas = [e for e in events if e["type"] == "text_delta"]
        assert len(text_deltas) == 1


class TestOllamaStreamToolCalls:
    async def test_tool_calls_in_response(self):
        lines = [
            json.dumps({
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "Read", "arguments": {"path": "x"}}},
                    ],
                },
                "done": True,
            }),
        ]
        response = MockResponse(200, lines)

        with patch("httpx.AsyncClient", return_value=MockClient(response)):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[], tools=[{"name": "Read"}]):
                events.append(evt)

        assistant = [e for e in events if e["type"] == "assistant"][0]
        content = assistant["message"].content
        assert any(b.get("type") == "tool_use" for b in content if isinstance(b, dict))


class TestOllamaStreamWithTools:
    async def test_tools_passed_to_payload(self):
        lines = [
            json.dumps({"message": {"content": "ok"}, "done": True}),
        ]
        response = MockResponse(200, lines)

        captured_kwargs = {}

        class CapturingClient:
            def stream(self, method, url, **kwargs):
                captured_kwargs.update(kwargs)
                return response

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        from types import SimpleNamespace
        tool = SimpleNamespace(name="Read", description="Read files",
                               input_schema={"type": "object"})

        with patch("httpx.AsyncClient", return_value=CapturingClient()):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[], tools=[tool]):
                events.append(evt)

        payload = captured_kwargs.get("json", {})
        assert "tools" in payload


class TestOllamaStreamMaxTokens:
    async def test_max_tokens_passed(self):
        lines = [
            json.dumps({"message": {"content": "ok"}, "done": True}),
        ]
        response = MockResponse(200, lines)

        captured_kwargs = {}

        class CapturingClient:
            def stream(self, method, url, **kwargs):
                captured_kwargs.update(kwargs)
                return response

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=CapturingClient()):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[], max_tokens=100):
                events.append(evt)

        payload = captured_kwargs.get("json", {})
        assert payload["options"]["num_predict"] == 100


class TestOllamaStreamModelOverride:
    async def test_model_override(self):
        lines = [
            json.dumps({"message": {"content": "ok"}, "done": True}),
        ]
        response = MockResponse(200, lines)

        captured_kwargs = {}

        class CapturingClient:
            def stream(self, method, url, **kwargs):
                captured_kwargs.update(kwargs)
                return response

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=CapturingClient()):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[], model="llama3:8b"):
                events.append(evt)

        payload = captured_kwargs.get("json", {})
        assert payload["model"] == "llama3:8b"


class TestOllamaStreamEmptyTools:
    async def test_empty_tools_not_in_payload(self):
        lines = [
            json.dumps({"message": {"content": "ok"}, "done": True}),
        ]
        response = MockResponse(200, lines)

        captured_kwargs = {}

        class CapturingClient:
            def stream(self, method, url, **kwargs):
                captured_kwargs.update(kwargs)
                return response

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

        with patch("httpx.AsyncClient", return_value=CapturingClient()):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[], tools=[]):
                events.append(evt)

        payload = captured_kwargs.get("json", {})
        assert "tools" not in payload


class TestOllamaNoTextFullText:
    async def test_no_text_response(self):
        """Response with only tool calls, no text."""
        lines = [
            json.dumps({
                "message": {
                    "content": "",
                    "tool_calls": [
                        {"function": {"name": "Read", "arguments": {"path": "x"}}},
                    ],
                },
                "done": True,
            }),
        ]
        response = MockResponse(200, lines)

        with patch("httpx.AsyncClient", return_value=MockClient(response)):
            provider = OllamaProvider()
            events = []
            async for evt in provider.stream(messages=[]):
                events.append(evt)

        assistant = [e for e in events if e["type"] == "assistant"][0]
        # Content should have tool_use blocks but no text block
        content = assistant["message"].content
        assert isinstance(content, list)
        text_blocks = [b for b in content if isinstance(b, dict) and b.get("type") == "text"]
        assert len(text_blocks) == 0


class TestOllamaDictMessages:
    async def test_dict_with_non_string_content(self):
        """Dict message with non-string content should be stringified."""
        from duh.adapters.ollama import _to_ollama_messages
        msgs = [{"role": "user", "content": [{"type": "text", "text": "hi"}]}]
        result = _to_ollama_messages(msgs, "")
        # Content is a list, so str() is used
        assert isinstance(result[0]["content"], str)
