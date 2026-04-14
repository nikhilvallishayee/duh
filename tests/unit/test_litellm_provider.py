"""Tests for the litellm provider adapter."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.kernel.messages import Message


# ===================================================================
# Provider instantiation
# ===================================================================

class TestLiteLLMProviderInit:
    def test_init_default_model(self):
        from duh.adapters.litellm_provider import LiteLLMProvider
        provider = LiteLLMProvider()
        assert provider._default_model == "gemini/gemini-2.5-flash"

    def test_init_custom_model(self):
        from duh.adapters.litellm_provider import LiteLLMProvider
        provider = LiteLLMProvider(model="bedrock/claude-3-haiku-20240307")
        assert provider._default_model == "bedrock/claude-3-haiku-20240307"

    def test_init_extra_kwargs(self):
        from duh.adapters.litellm_provider import LiteLLMProvider
        provider = LiteLLMProvider(api_base="http://localhost:4000")
        assert provider._litellm_kwargs == {"api_base": "http://localhost:4000"}


# ===================================================================
# _parse_tool_use_block compatibility (ADR-054)
# ===================================================================

class TestParseToolUseBlock:
    def test_parses_standard_block(self):
        from duh.adapters.litellm_provider import LiteLLMProvider
        block = {"id": "tu_1", "name": "Read", "input": {"path": "/tmp/x"}}
        parsed = LiteLLMProvider._parse_tool_use_block(block)
        assert parsed.id == "tu_1"
        assert parsed.name == "Read"
        assert parsed.input == {"path": "/tmp/x"}

    def test_handles_missing_fields(self):
        from duh.adapters.litellm_provider import LiteLLMProvider
        parsed = LiteLLMProvider._parse_tool_use_block({})
        assert parsed.id == ""
        assert parsed.name == ""
        assert parsed.input == {}

    def test_matches_other_providers(self):
        """All providers must produce identical ParsedToolUse for same input."""
        from duh.adapters.litellm_provider import LiteLLMProvider
        from duh.adapters.anthropic import AnthropicProvider
        from duh.adapters.stub_provider import StubProvider

        block = {"id": "tu_99", "name": "Write", "input": {"file_path": "/a", "content": "b"}}
        litellm_result = LiteLLMProvider._parse_tool_use_block(block)
        anthropic_result = AnthropicProvider._parse_tool_use_block(block)
        stub_result = StubProvider._parse_tool_use_block(block)
        assert litellm_result.id == anthropic_result.id == stub_result.id
        assert litellm_result.name == anthropic_result.name == stub_result.name
        assert litellm_result.input == anthropic_result.input == stub_result.input


# ===================================================================
# _wrap_model_output taint tagging
# ===================================================================

class TestWrapModelOutput:
    def test_wraps_plain_string(self):
        from duh.adapters.litellm_provider import _wrap_model_output
        from duh.kernel.untrusted import UntrustedStr, TaintSource
        result = _wrap_model_output("hello")
        assert isinstance(result, UntrustedStr)
        assert result.source == TaintSource.MODEL_OUTPUT
        assert str(result) == "hello"

    def test_passthrough_already_tainted(self):
        from duh.adapters.litellm_provider import _wrap_model_output
        from duh.kernel.untrusted import UntrustedStr, TaintSource
        already = UntrustedStr("hello", TaintSource.MODEL_OUTPUT)
        result = _wrap_model_output(already)
        assert result is already


# ===================================================================
# Message conversion
# ===================================================================

class TestToLiteLLMMessages:
    def test_system_prompt_string(self):
        from duh.adapters.litellm_provider import _to_litellm_messages
        msgs = _to_litellm_messages([], "You are helpful")
        assert msgs[0] == {"role": "system", "content": "You are helpful"}

    def test_system_prompt_list(self):
        from duh.adapters.litellm_provider import _to_litellm_messages
        msgs = _to_litellm_messages([], ["Line 1", "Line 2"])
        assert msgs[0]["role"] == "system"
        assert "Line 1" in msgs[0]["content"]
        assert "Line 2" in msgs[0]["content"]

    def test_empty_system_prompt(self):
        from duh.adapters.litellm_provider import _to_litellm_messages
        msgs = _to_litellm_messages([], "")
        assert len(msgs) == 0

    def test_user_message_string(self):
        from duh.adapters.litellm_provider import _to_litellm_messages
        user = Message(role="user", content="hello")
        msgs = _to_litellm_messages([user], "")
        assert msgs[0] == {"role": "user", "content": "hello"}

    def test_assistant_message_with_tool_use(self):
        from duh.adapters.litellm_provider import _to_litellm_messages
        asst = Message(role="assistant", content=[
            {"type": "text", "text": "Let me read that"},
            {"type": "tool_use", "id": "tu_1", "name": "Read", "input": {"path": "/tmp/x"}},
        ])
        msgs = _to_litellm_messages([asst], "")
        assert msgs[0]["role"] == "assistant"
        assert msgs[0]["content"] == "Let me read that"
        assert len(msgs[0]["tool_calls"]) == 1
        assert msgs[0]["tool_calls"][0]["function"]["name"] == "Read"

    def test_tool_result_becomes_tool_message(self):
        from duh.adapters.litellm_provider import _to_litellm_messages
        user = Message(role="user", content=[
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "file data"},
        ])
        msgs = _to_litellm_messages([user], "")
        assert msgs[0]["role"] == "tool"
        assert msgs[0]["tool_call_id"] == "tu_1"
        assert msgs[0]["content"] == "file data"


# ===================================================================
# Tool conversion
# ===================================================================

class TestToLiteLLMTools:
    def test_converts_tools(self):
        from duh.adapters.litellm_provider import _to_litellm_tools

        class FakeTool:
            name = "Read"
            description = "Read a file"
            input_schema = {"type": "object", "properties": {"path": {"type": "string"}}}

        tools = _to_litellm_tools([FakeTool()])
        assert len(tools) == 1
        assert tools[0]["type"] == "function"
        assert tools[0]["function"]["name"] == "Read"
        assert tools[0]["function"]["description"] == "Read a file"

    def test_skips_unnamed_tools(self):
        from duh.adapters.litellm_provider import _to_litellm_tools

        class NoName:
            name = ""
            description = "no name"
            input_schema = {}

        assert _to_litellm_tools([NoName()]) == []


# ===================================================================
# Streaming with mocked litellm.acompletion
# ===================================================================

def _make_text_chunk(text: str, finish_reason: str | None = None):
    """Create a mock OpenAI-style streaming chunk with text content."""
    delta = SimpleNamespace(content=text, tool_calls=None)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


def _make_tool_chunk(
    index: int = 0,
    tc_id: str | None = None,
    fn_name: str | None = None,
    fn_args: str | None = None,
    finish_reason: str | None = None,
):
    """Create a mock OpenAI-style streaming chunk with tool call data."""
    fn = SimpleNamespace(
        name=fn_name,
        arguments=fn_args,
    )
    tc = SimpleNamespace(index=index, id=tc_id, function=fn)
    delta = SimpleNamespace(content=None, tool_calls=[tc])
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


def _make_usage_chunk(prompt_tokens: int, completion_tokens: int):
    """Create a chunk that carries usage info."""
    delta = SimpleNamespace(content=None, tool_calls=None)
    choice = SimpleNamespace(delta=delta, finish_reason=None)
    usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens)
    return SimpleNamespace(choices=[choice], usage=usage)


async def _async_iter(items):
    """Turn a list into an async iterator."""
    for item in items:
        yield item


class TestLiteLLMStreaming:
    @pytest.mark.asyncio
    async def test_text_streaming(self):
        """Test basic text streaming through the adapter."""
        from duh.adapters.litellm_provider import LiteLLMProvider

        chunks = [
            _make_text_chunk("Hello"),
            _make_text_chunk(" world"),
            _make_text_chunk("!", finish_reason="stop"),
        ]

        mock_acompletion = AsyncMock(return_value=_async_iter(chunks))

        with patch.dict("sys.modules", {"litellm": MagicMock(acompletion=mock_acompletion)}):
            provider = LiteLLMProvider(model="gemini/gemini-2.5-flash")
            events = []
            async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
                events.append(ev)

        # Should have text_delta events + final assistant message
        text_deltas = [e for e in events if e.get("type") == "text_delta"]
        assert len(text_deltas) == 3
        assert text_deltas[0]["text"] == "Hello"
        assert text_deltas[1]["text"] == " world"
        assert text_deltas[2]["text"] == "!"

        assistant_events = [e for e in events if e.get("type") == "assistant"]
        assert len(assistant_events) == 1
        msg = assistant_events[0]["message"]
        assert isinstance(msg, Message)
        assert msg.role == "assistant"
        assert msg.content[0]["text"] == "Hello world!"
        assert msg.metadata["stop_reason"] == "end_turn"

    @pytest.mark.asyncio
    async def test_tool_use_streaming(self):
        """Test tool call extraction from OpenAI-format streaming chunks."""
        from duh.adapters.litellm_provider import LiteLLMProvider

        chunks = [
            _make_text_chunk("Let me read that file."),
            _make_tool_chunk(index=0, tc_id="call_123", fn_name="Read"),
            _make_tool_chunk(index=0, fn_args='{"file_path":'),
            _make_tool_chunk(index=0, fn_args=' "/tmp/x"}', finish_reason="tool_calls"),
        ]

        mock_acompletion = AsyncMock(return_value=_async_iter(chunks))

        with patch.dict("sys.modules", {"litellm": MagicMock(acompletion=mock_acompletion)}):
            provider = LiteLLMProvider(model="gemini/gemini-2.5-flash")
            events = []
            async for ev in provider.stream(messages=[Message(role="user", content="read /tmp/x")]):
                events.append(ev)

        assistant_events = [e for e in events if e.get("type") == "assistant"]
        assert len(assistant_events) == 1
        msg = assistant_events[0]["message"]
        blocks = msg.content
        assert len(blocks) == 2
        assert blocks[0]["type"] == "text"
        assert blocks[0]["text"] == "Let me read that file."
        assert blocks[1]["type"] == "tool_use"
        assert blocks[1]["id"] == "call_123"
        assert blocks[1]["name"] == "Read"
        assert blocks[1]["input"] == {"file_path": "/tmp/x"}

    @pytest.mark.asyncio
    async def test_tool_choice_translation(self):
        """Verify tool_choice values get translated to OpenAI format."""
        from duh.adapters.litellm_provider import LiteLLMProvider

        chunks = [_make_text_chunk("ok", finish_reason="stop")]
        mock_acompletion = AsyncMock(return_value=_async_iter(chunks))

        class FakeTool:
            name = "Read"
            description = "Read"
            input_schema = {}

        with patch.dict("sys.modules", {"litellm": MagicMock(acompletion=mock_acompletion)}):
            provider = LiteLLMProvider(model="test/model")

            # Test "any" -> "required"
            events = []
            async for ev in provider.stream(
                messages=[Message(role="user", content="hi")],
                tools=[FakeTool()],
                tool_choice="any",
            ):
                events.append(ev)

            call_kwargs = mock_acompletion.call_args[1]
            assert call_kwargs["tool_choice"] == "required"

    @pytest.mark.asyncio
    async def test_usage_tracking(self):
        """Test that usage info from chunks is captured in metadata."""
        from duh.adapters.litellm_provider import LiteLLMProvider

        chunks = [
            _make_text_chunk("hi"),
            _make_usage_chunk(prompt_tokens=10, completion_tokens=5),
            _make_text_chunk("", finish_reason="stop"),
        ]

        mock_acompletion = AsyncMock(return_value=_async_iter(chunks))

        with patch.dict("sys.modules", {"litellm": MagicMock(acompletion=mock_acompletion)}):
            provider = LiteLLMProvider(model="test/model")
            events = []
            async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
                events.append(ev)

        assistant_events = [e for e in events if e.get("type") == "assistant"]
        assert assistant_events[0]["message"].metadata["usage"]["input_tokens"] == 10
        assert assistant_events[0]["message"].metadata["usage"]["output_tokens"] == 5

    @pytest.mark.asyncio
    async def test_api_error_handling(self):
        """Test that litellm API errors are surfaced cleanly."""
        from duh.adapters.litellm_provider import LiteLLMProvider

        mock_acompletion = AsyncMock(side_effect=Exception("Rate limit exceeded"))

        with patch.dict("sys.modules", {"litellm": MagicMock(acompletion=mock_acompletion)}):
            provider = LiteLLMProvider(model="test/model")
            events = []
            async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
                events.append(ev)

        assert len(events) == 1
        msg = events[0]["message"]
        assert msg.metadata.get("is_error") is True
        assert "Rate limit exceeded" in msg.content[0]["text"]


# ===================================================================
# Graceful error when litellm not installed
# ===================================================================

class TestLiteLLMNotInstalled:
    @pytest.mark.asyncio
    async def test_import_error_message(self):
        """Provider yields helpful error when litellm is not installed."""
        from duh.adapters.litellm_provider import LiteLLMProvider

        provider = LiteLLMProvider()

        # Temporarily make litellm unimportable
        original = sys.modules.get("litellm")
        sys.modules["litellm"] = None  # type: ignore[assignment]
        try:
            events = []
            async for ev in provider.stream(messages=[Message(role="user", content="hi")]):
                events.append(ev)
        finally:
            if original is not None:
                sys.modules["litellm"] = original
            else:
                del sys.modules["litellm"]

        assert len(events) == 1
        msg = events[0]["message"]
        assert msg.metadata.get("is_error") is True
        assert "litellm is not installed" in msg.content[0]["text"]
        assert "pip install duh-cli[litellm]" in msg.content[0]["text"]


# ===================================================================
# Model string detection (slash convention)
# ===================================================================

class TestModelStringDetection:
    def test_slash_model_infers_litellm(self):
        from duh.providers.registry import infer_provider_from_model
        assert infer_provider_from_model("gemini/gemini-2.5-flash") == "litellm"
        assert infer_provider_from_model("bedrock/claude-3-haiku-20240307") == "litellm"
        assert infer_provider_from_model("together_ai/meta-llama/Llama-3-70b") == "litellm"
        assert infer_provider_from_model("groq/llama-3.1-8b-instant") == "litellm"

    def test_native_providers_not_affected(self):
        from duh.providers.registry import infer_provider_from_model
        assert infer_provider_from_model("claude-sonnet-4-6") == "anthropic"
        assert infer_provider_from_model("gpt-4o") == "openai"
        assert infer_provider_from_model(None) is None
        assert infer_provider_from_model("") is None


# ===================================================================
# Registry integration
# ===================================================================

class TestRegistryIntegration:
    def test_build_model_backend_litellm(self):
        from duh.providers.registry import build_model_backend
        backend = build_model_backend("litellm", "gemini/gemini-2.5-flash")
        assert backend.provider == "litellm"
        assert backend.model == "gemini/gemini-2.5-flash"
        assert backend.ok
        assert backend.auth_mode == "env_vars"

    def test_build_model_backend_default_model(self):
        from duh.providers.registry import build_model_backend
        backend = build_model_backend("litellm", None)
        assert backend.model == "gemini/gemini-2.5-flash"


# ===================================================================
# CLI parser
# ===================================================================

class TestParserLiteLLM:
    def test_litellm_provider_choice(self):
        from duh.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["--provider", "litellm", "-p", "hi"])
        assert args.provider == "litellm"

    def test_litellm_with_model(self):
        from duh.cli.parser import build_parser
        parser = build_parser()
        args = parser.parse_args(["--provider", "litellm", "--model", "bedrock/claude-3-haiku-20240307", "-p", "hi"])
        assert args.provider == "litellm"
        assert args.model == "bedrock/claude-3-haiku-20240307"


# ===================================================================
# _build_content_blocks helper
# ===================================================================

class TestBuildContentBlocks:
    def test_text_only(self):
        from duh.adapters.litellm_provider import _build_content_blocks
        blocks = _build_content_blocks(["hello", " world"], {})
        assert len(blocks) == 1
        assert blocks[0] == {"type": "text", "text": "hello world"}

    def test_tool_call_only(self):
        from duh.adapters.litellm_provider import _build_content_blocks
        tc = {0: {"id": "c1", "name": "Read", "arguments": '{"path": "/tmp"}'}}
        blocks = _build_content_blocks([], tc)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "tool_use"
        assert blocks[0]["name"] == "Read"
        assert blocks[0]["input"] == {"path": "/tmp"}

    def test_invalid_json_arguments(self):
        from duh.adapters.litellm_provider import _build_content_blocks
        tc = {0: {"id": "c1", "name": "Read", "arguments": "not-json"}}
        blocks = _build_content_blocks([], tc)
        assert blocks[0]["input"] == {}

    def test_text_and_tools(self):
        from duh.adapters.litellm_provider import _build_content_blocks
        tc = {0: {"id": "c1", "name": "Read", "arguments": '{"p": 1}'}}
        blocks = _build_content_blocks(["thinking..."], tc)
        assert len(blocks) == 2
        assert blocks[0]["type"] == "text"
        assert blocks[1]["type"] == "tool_use"
