"""Tests for duh.adapters.anthropic — Anthropic SDK wrapper.

Uses mocks — no real API calls. Tests the translation logic between
Anthropic SDK format and D.U.H. uniform events.
"""

from unittest.mock import AsyncMock, MagicMock, patch
from types import SimpleNamespace

import pytest

from duh.adapters.anthropic import (
    AnthropicProvider,
    _build_system_text,
    _default_max_tokens,
    _sanitize_block,
    _to_api_messages,
    _to_api_tools,
)
from duh.kernel.messages import Message, TextBlock, ToolUseBlock, ToolResultBlock


# ═══════════════════════════════════════════════════════════════════
# Translation helpers
# ═══════════════════════════════════════════════════════════════════

class TestToApiMessages:
    def test_string_content(self):
        msgs = [Message(role="user", content="hello")]
        result = _to_api_messages(msgs)
        assert result == [{"role": "user", "content": "hello"}]

    def test_block_content(self):
        msgs = [Message(role="assistant", content=[
            {"type": "text", "text": "hi"},
        ])]
        result = _to_api_messages(msgs)
        assert result[0]["role"] == "assistant"
        assert result[0]["content"] == [{"type": "text", "text": "hi"}]

    def test_dataclass_blocks(self):
        msgs = [Message(role="assistant", content=[
            TextBlock(text="hello"),
        ])]
        result = _to_api_messages(msgs)
        assert result[0]["content"][0] == {"type": "text", "text": "hello"}

    def test_tool_result_blocks(self):
        msgs = [Message(role="user", content=[
            {"type": "tool_result", "tool_use_id": "tu1", "content": "data"},
        ])]
        result = _to_api_messages(msgs)
        block = result[0]["content"][0]
        assert block["type"] == "tool_result"
        assert block["tool_use_id"] == "tu1"

    def test_dict_messages(self):
        msgs = [{"role": "user", "content": "hi"}]
        result = _to_api_messages(msgs)
        assert result == [{"role": "user", "content": "hi"}]

    def test_strips_extra_fields(self):
        msgs = [Message(role="assistant", content=[
            {"type": "text", "text": "hi", "parsed_output": "junk"},
        ])]
        result = _to_api_messages(msgs)
        assert "parsed_output" not in result[0]["content"][0]

    def test_empty_messages(self):
        assert _to_api_messages([]) == []

    def test_multiple_messages(self):
        msgs = [
            Message(role="user", content="q1"),
            Message(role="assistant", content="a1"),
            Message(role="user", content="q2"),
        ]
        result = _to_api_messages(msgs)
        assert len(result) == 3
        assert [m["role"] for m in result] == ["user", "assistant", "user"]


class TestSanitizeBlock:
    def test_text_block(self):
        block = {"type": "text", "text": "hi", "extra": "junk"}
        assert _sanitize_block(block) == {"type": "text", "text": "hi"}

    def test_tool_use_block(self):
        block = {"type": "tool_use", "id": "tu1", "name": "Read",
                 "input": {"path": "x"}, "extra": "junk"}
        result = _sanitize_block(block)
        assert "extra" not in result
        assert result["name"] == "Read"

    def test_tool_result_block(self):
        block = {"type": "tool_result", "tool_use_id": "tu1",
                 "content": "data", "is_error": False, "extra": "junk"}
        result = _sanitize_block(block)
        assert "extra" not in result
        assert result["tool_use_id"] == "tu1"

    def test_unknown_type_passes_through(self):
        block = {"type": "custom", "data": "value"}
        assert _sanitize_block(block) == block


class TestToApiTools:
    def test_dict_tools(self):
        tools = [{"name": "Read", "description": "Read files", "input_schema": {}}]
        assert _to_api_tools(tools) == tools

    def test_object_tools(self):
        tool = SimpleNamespace(name="Bash", description="Run commands",
                               input_schema={"type": "object"})
        result = _to_api_tools([tool])
        assert result[0]["name"] == "Bash"
        assert result[0]["description"] == "Run commands"

    def test_callable_description(self):
        tool = SimpleNamespace(name="X", description=lambda: "dynamic desc",
                               input_schema={})
        result = _to_api_tools([tool])
        assert result[0]["description"] == "dynamic desc"

    def test_empty_tools(self):
        assert _to_api_tools([]) == []


class TestBuildSystemText:
    def test_string(self):
        assert _build_system_text("You are helpful") == "You are helpful"

    def test_list(self):
        result = _build_system_text(["Part 1", "Part 2"])
        assert "Part 1" in result
        assert "Part 2" in result

    def test_empty_string(self):
        assert _build_system_text("") == ""

    def test_empty_list(self):
        assert _build_system_text([]) == ""

    def test_list_with_empty_strings(self):
        result = _build_system_text(["Part 1", "", "Part 2"])
        assert "Part 1" in result
        assert "Part 2" in result


class TestDefaultMaxTokens:
    def test_opus(self):
        assert _default_max_tokens("claude-opus-4-6") == 16384

    def test_sonnet(self):
        assert _default_max_tokens("claude-sonnet-4-6") == 16384

    def test_haiku(self):
        assert _default_max_tokens("claude-haiku-4-5") == 8192

    def test_unknown(self):
        assert _default_max_tokens("some-model") == 16384


# ═══════════════════════════════════════════════════════════════════
# Provider construction
# ═══════════════════════════════════════════════════════════════════

class TestProviderConstruction:
    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "test-key"})
    def test_creates_with_env_key(self):
        with patch("anthropic.AsyncAnthropic") as mock:
            provider = AnthropicProvider()
            mock.assert_called_once()

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": ""})
    def test_creates_with_explicit_key(self):
        with patch("anthropic.AsyncAnthropic") as mock:
            provider = AnthropicProvider(api_key="sk-test")
            call_kwargs = mock.call_args
            assert call_kwargs[1]["api_key"] == "sk-test"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"})
    def test_custom_model(self):
        with patch("anthropic.AsyncAnthropic"):
            provider = AnthropicProvider(model="claude-opus-4-6")
            assert provider._default_model == "claude-opus-4-6"

    @patch.dict("os.environ", {"ANTHROPIC_API_KEY": "k"})
    def test_custom_base_url(self):
        with patch("anthropic.AsyncAnthropic") as mock:
            provider = AnthropicProvider(base_url="https://custom.api.com")
            call_kwargs = mock.call_args
            assert call_kwargs[1]["base_url"] == "https://custom.api.com"
