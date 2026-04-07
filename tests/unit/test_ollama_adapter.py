"""Exhaustive tests for duh.adapters.ollama — Ollama HTTP wrapper."""

from unittest.mock import AsyncMock, MagicMock, patch
import json

import pytest

from duh.adapters.ollama import (
    OllamaProvider,
    _interpret_ollama_error,
    _to_ollama_messages,
    _to_ollama_tools,
)
from duh.kernel.messages import Message


# ═══════════════════════════════════════════════════════════════════
# Translation helpers
# ═══════════════════════════════════════════════════════════════════

class TestToOllamaMessages:
    def test_string_content(self):
        msgs = [Message(role="user", content="hello")]
        result = _to_ollama_messages(msgs, "")
        assert result == [{"role": "user", "content": "hello"}]

    def test_system_prompt_prepended(self):
        msgs = [Message(role="user", content="hi")]
        result = _to_ollama_messages(msgs, "You are helpful")
        assert result[0] == {"role": "system", "content": "You are helpful"}
        assert result[1] == {"role": "user", "content": "hi"}

    def test_system_prompt_list(self):
        msgs = [Message(role="user", content="hi")]
        result = _to_ollama_messages(msgs, ["Part 1", "Part 2"])
        assert "Part 1" in result[0]["content"]
        assert "Part 2" in result[0]["content"]

    def test_empty_system_prompt(self):
        msgs = [Message(role="user", content="hi")]
        result = _to_ollama_messages(msgs, "")
        assert len(result) == 1  # no system message

    def test_dict_messages(self):
        msgs = [{"role": "user", "content": "hi"}]
        result = _to_ollama_messages(msgs, "")
        assert result == [{"role": "user", "content": "hi"}]

    def test_multiple_messages(self):
        msgs = [
            Message(role="user", content="q1"),
            Message(role="assistant", content="a1"),
            Message(role="user", content="q2"),
        ]
        result = _to_ollama_messages(msgs, "")
        assert len(result) == 3
        assert [m["role"] for m in result] == ["user", "assistant", "user"]

    def test_block_content_extracts_text(self):
        msgs = [Message(role="assistant", content=[
            {"type": "text", "text": "hello"},
        ])]
        result = _to_ollama_messages(msgs, "")
        assert result[0]["content"] == "hello"

    def test_empty_messages(self):
        result = _to_ollama_messages([], "sys")
        assert len(result) == 1  # just system


class TestToOllamaTools:
    def test_dict_tools(self):
        tools = [{"type": "function", "function": {"name": "test"}}]
        assert _to_ollama_tools(tools) == tools

    def test_object_tools(self):
        from types import SimpleNamespace
        tool = SimpleNamespace(name="Read", description="Read files",
                               input_schema={"type": "object"})
        result = _to_ollama_tools([tool])
        assert result[0]["function"]["name"] == "Read"
        assert result[0]["type"] == "function"

    def test_callable_description(self):
        from types import SimpleNamespace
        tool = SimpleNamespace(name="X", description=lambda: "dynamic",
                               input_schema={})
        result = _to_ollama_tools([tool])
        assert result[0]["function"]["description"] == "dynamic"

    def test_empty_tools(self):
        assert _to_ollama_tools([]) == []


class TestInterpretOllamaError:
    def test_not_found(self):
        result = _interpret_ollama_error(404, b'{"error": "model not found"}')
        assert "Pull it first" in result

    def test_connection_refused(self):
        result = _interpret_ollama_error(500, b'{"error": "connection refused"}')
        assert "ollama serve" in result

    def test_generic_error(self):
        result = _interpret_ollama_error(500, b'{"error": "out of memory"}')
        assert "500" in result
        assert "out of memory" in result

    def test_non_json_body(self):
        result = _interpret_ollama_error(500, b'Internal Server Error')
        assert "500" in result


# ═══════════════════════════════════════════════════════════════════
# Provider construction
# ═══════════════════════════════════════════════════════════════════

class TestOllamaProviderConstruction:
    def test_defaults(self):
        p = OllamaProvider()
        assert p._default_model == "qwen2.5-coder:1.5b"
        assert p._base_url == "http://localhost:11434"

    def test_custom_model(self):
        p = OllamaProvider(model="llama3.2:3b")
        assert p._default_model == "llama3.2:3b"

    def test_custom_base_url(self):
        p = OllamaProvider(base_url="http://remote:11434/")
        assert p._base_url == "http://remote:11434"  # trailing slash stripped

    def test_custom_timeout(self):
        p = OllamaProvider(timeout=60)
        assert p._timeout == 60


# ═══════════════════════════════════════════════════════════════════
# Integration tests (with real Ollama if available)
# ═══════════════════════════════════════════════════════════════════

class TestOllamaIntegration:
    """These tests hit the real Ollama API if running."""

    @pytest.fixture
    def ollama_available(self):
        import httpx
        try:
            r = httpx.get("http://localhost:11434/api/tags", timeout=2)
            return r.status_code == 200
        except Exception:
            return False

    @pytest.mark.asyncio
    async def test_simple_prompt(self, ollama_available):
        if not ollama_available:
            pytest.skip("Ollama not running")

        p = OllamaProvider(model="qwen2.5-coder:1.5b")
        events = []
        async for e in p.stream(
            messages=[Message(role="user", content="Say exactly: TEST_OK")],
        ):
            events.append(e)

        types = [e.get("type") for e in events]
        assert "assistant" in types
        # Should have at least one text_delta or an assistant message
        has_content = any(e.get("type") == "text_delta" for e in events) or \
                      any(e.get("type") == "assistant" for e in events)
        assert has_content

    @pytest.mark.asyncio
    async def test_streaming_deltas(self, ollama_available):
        if not ollama_available:
            pytest.skip("Ollama not running")

        p = OllamaProvider(model="qwen2.5-coder:1.5b")
        deltas = []
        async for e in p.stream(
            messages=[Message(role="user", content="Count to 3")],
        ):
            if e.get("type") == "text_delta":
                deltas.append(e["text"])

        assert len(deltas) > 0  # should stream multiple chunks

    @pytest.mark.asyncio
    async def test_connection_error(self):
        """Connecting to a non-existent server should return an error event."""
        p = OllamaProvider(base_url="http://localhost:99999")
        events = []
        async for e in p.stream(
            messages=[Message(role="user", content="hi")],
        ):
            events.append(e)

        assert len(events) == 1
        assert events[0]["type"] == "assistant"
        msg = events[0]["message"]
        assert msg.metadata.get("is_error") is True
