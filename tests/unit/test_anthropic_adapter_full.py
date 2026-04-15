"""Full coverage tests for duh.adapters.anthropic — stream method + helpers.

Covers the streaming path, error path, _block_to_dict, _normalize_content,
and all branches in the translation helpers not hit by test_anthropic_adapter.py.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.adapters.anthropic import (
    AnthropicProvider,
    _block_to_dict,
    _normalize_content,
    _to_api_messages,
    _to_api_tools,
)
from duh.kernel.messages import Message, TextBlock


# ===================================================================
# _block_to_dict
# ===================================================================

class TestBlockToDict:
    def test_dict_passthrough(self):
        d = {"type": "text", "text": "hi"}
        assert _block_to_dict(d) is d

    def test_object_with_model_dump(self):
        obj = MagicMock()
        obj.model_dump.return_value = {"type": "text", "text": "hello"}
        assert _block_to_dict(obj) == {"type": "text", "text": "hello"}

    def test_generic_object_without_model_dump(self):
        obj = SimpleNamespace(type="text", text="hi", thinking=None, id=None,
                              name=None, input=None, signature=None)
        result = _block_to_dict(obj)
        assert result["type"] == "text"
        assert result["text"] == "hi"
        # None values should not be included
        assert "thinking" not in result

    def test_generic_object_with_all_attrs(self):
        obj = SimpleNamespace(type="tool_use", text=None, thinking=None,
                              id="tu1", name="Read", input={"path": "x"},
                              signature=None)
        result = _block_to_dict(obj)
        assert result["type"] == "tool_use"
        assert result["id"] == "tu1"
        assert result["name"] == "Read"
        assert result["input"] == {"path": "x"}

    def test_generic_object_type_unknown(self):
        obj = SimpleNamespace()
        # No type attribute
        result = _block_to_dict(obj)
        assert result["type"] == "unknown"

    def test_generic_object_with_signature(self):
        obj = SimpleNamespace(type="thinking", text=None, thinking="hmm",
                              id=None, name=None, input=None,
                              signature="sig123")
        result = _block_to_dict(obj)
        assert result["signature"] == "sig123"
        assert result["thinking"] == "hmm"


# ===================================================================
# _normalize_content
# ===================================================================

class TestNormalizeContent:
    def test_empty_list(self):
        assert _normalize_content([]) == []

    def test_single_dict(self):
        result = _normalize_content([{"type": "text", "text": "hello"}])
        assert result == [{"type": "text", "text": "hello"}]

    def test_single_object(self):
        obj = MagicMock()
        obj.model_dump.return_value = {"type": "text", "text": "x"}
        result = _normalize_content([obj])
        assert result == [{"type": "text", "text": "x"}]

    def test_mixed(self):
        d = {"type": "text", "text": "a"}
        obj = MagicMock()
        obj.model_dump.return_value = {"type": "text", "text": "b"}
        result = _normalize_content([d, obj])
        assert len(result) == 2


# ===================================================================
# _to_api_messages — edge cases
# ===================================================================

class TestToApiMessagesEdgeCases:
    def test_non_message_non_dict(self):
        """Fallback: plain string or other object → user message."""
        result = _to_api_messages(["just a string"])
        assert result == [{"role": "user", "content": "just a string"}]

    def test_non_string_block_in_content(self):
        """Content list item that is not a dict and not a dataclass → str()."""
        msg = Message(role="user", content=["plain string item"])
        result = _to_api_messages([msg])
        block = result[0]["content"][0]
        assert block == {"type": "text", "text": "plain string item"}


# ===================================================================
# _to_api_tools — edge cases
# ===================================================================

class TestToApiToolsEdgeCases:
    def test_object_without_required_attrs(self):
        """Objects lacking name/input_schema are skipped."""
        obj = SimpleNamespace(foo="bar")
        result = _to_api_tools([obj])
        assert result == []

    def test_empty_description(self):
        tool = SimpleNamespace(name="X", description="", input_schema={})
        result = _to_api_tools([tool])
        assert result[0]["description"] == ""


# ===================================================================
# AnthropicProvider.stream — mocked
# ===================================================================

class TestAnthropicProviderStream:
    """Test stream() with full mocking of the Anthropic SDK."""

    @pytest.fixture
    def provider(self):
        with patch("anthropic.AsyncAnthropic") as mock_cls:
            p = AnthropicProvider(api_key="test-key")
            p._client = mock_cls.return_value
            return p

    async def test_stream_text_response(self, provider):
        """Basic text response streaming."""
        # Create mock event objects
        text_block = SimpleNamespace(type="text", text="Hello world")
        text_delta = SimpleNamespace(type="text_delta", text="Hello")
        block_start_event = SimpleNamespace(
            type="content_block_start",
            content_block=text_block,
            index=0,
        )
        block_delta_event = SimpleNamespace(
            type="content_block_delta",
            delta=text_delta,
        )
        block_stop_event = SimpleNamespace(
            type="content_block_stop",
            index=0,
        )
        msg_usage = SimpleNamespace(input_tokens=10, output_tokens=5)
        msg_start_msg = SimpleNamespace(usage=msg_usage)
        msg_start_event = SimpleNamespace(
            type="message_start",
            message=msg_start_msg,
        )
        msg_delta_usage = SimpleNamespace(output_tokens=20)
        msg_delta_event = SimpleNamespace(
            type="message_delta",
            usage=msg_delta_usage,
        )

        # Final message
        final_block = SimpleNamespace(type="text", text="Hello world")
        final_msg = SimpleNamespace(
            content=[final_block],
            id="msg-1",
            model="claude-sonnet-4-6",
            stop_reason="end_turn",
        )

        # Create async context manager for stream
        events = [msg_start_event, block_start_event, block_delta_event,
                  block_stop_event, msg_delta_event]

        async def async_event_iter():
            for e in events:
                yield e

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        result_events = []
        async for evt in provider.stream(
            messages=[Message(role="user", content="Hi")],
        ):
            result_events.append(evt)

        types = [e["type"] for e in result_events]
        assert "content_block_start" in types
        assert "text_delta" in types
        assert "content_block_stop" in types
        assert "assistant" in types

        # Verify the assistant message
        asst = [e for e in result_events if e["type"] == "assistant"][0]
        assert isinstance(asst["message"], Message)
        assert asst["message"].role == "assistant"

    async def test_stream_thinking_delta(self, provider):
        """Thinking delta events are yielded."""
        thinking_delta = SimpleNamespace(type="thinking_delta", thinking="hmm")
        block_delta_event = SimpleNamespace(
            type="content_block_delta",
            delta=thinking_delta,
        )

        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="test", stop_reason="end_turn",
        )

        async def async_event_iter():
            yield block_delta_event

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(messages=[]):
            events.append(evt)

        thinking_evts = [e for e in events if e["type"] == "thinking_delta"]
        assert len(thinking_evts) == 1
        assert thinking_evts[0]["text"] == "hmm"

    async def test_stream_input_json_delta(self, provider):
        """input_json_delta events are yielded."""
        json_delta = SimpleNamespace(type="input_json_delta", partial_json='{"x":')
        block_delta_event = SimpleNamespace(
            type="content_block_delta",
            delta=json_delta,
        )

        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="test", stop_reason="end_turn",
        )

        async def async_event_iter():
            yield block_delta_event

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(messages=[]):
            events.append(evt)

        json_evts = [e for e in events if e["type"] == "input_json_delta"]
        assert len(json_evts) == 1
        assert json_evts[0]["partial_json"] == '{"x":'

    async def test_stream_signature_delta_ignored(self, provider):
        """signature_delta events should be silently ignored."""
        sig_delta = SimpleNamespace(type="signature_delta", signature="abc")
        block_delta_event = SimpleNamespace(
            type="content_block_delta",
            delta=sig_delta,
        )

        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="test", stop_reason="end_turn",
        )

        async def async_event_iter():
            yield block_delta_event

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(messages=[]):
            events.append(evt)

        # Only assistant event, no delta events
        types = [e["type"] for e in events]
        assert "signature_delta" not in types

    async def test_stream_error_path(self, provider):
        """Exception during streaming yields error assistant message."""
        provider._client.messages.stream = MagicMock(
            side_effect=Exception("API down")
        )

        events = []
        async for evt in provider.stream(messages=[]):
            events.append(evt)

        assert len(events) == 1
        assert events[0]["type"] == "assistant"
        msg = events[0]["message"]
        assert msg.metadata.get("is_error") is True
        assert "API down" in msg.text

    async def test_stream_content_block_start_no_block(self, provider):
        """content_block_start with no content_block attribute."""
        event = SimpleNamespace(
            type="content_block_start",
            content_block=None,
            index=0,
        )

        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="test", stop_reason="end_turn",
        )

        async def async_event_iter():
            yield event

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(messages=[]):
            events.append(evt)

        block_starts = [e for e in events if e["type"] == "content_block_start"]
        assert len(block_starts) == 1
        assert block_starts[0]["content_block"] == {}

    async def test_stream_content_block_delta_no_delta(self, provider):
        """content_block_delta with no delta attribute."""
        event = SimpleNamespace(
            type="content_block_delta",
            delta=None,
        )

        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="test", stop_reason="end_turn",
        )

        async def async_event_iter():
            yield event

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(messages=[]):
            events.append(evt)

        # Should not crash, only assistant event at end
        types = [e["type"] for e in events]
        assert "assistant" in types

    async def test_stream_message_start_no_usage(self, provider):
        """message_start with no usage."""
        event = SimpleNamespace(
            type="message_start",
            message=SimpleNamespace(usage=None),
        )

        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="test", stop_reason="end_turn",
        )

        async def async_event_iter():
            yield event

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(messages=[]):
            events.append(evt)

        assert any(e["type"] == "assistant" for e in events)

    async def test_stream_message_start_no_message(self, provider):
        """message_start with no message attribute."""
        event = SimpleNamespace(type="message_start", message=None)

        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="test", stop_reason="end_turn",
        )

        async def async_event_iter():
            yield event

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(messages=[]):
            events.append(evt)

        assert any(e["type"] == "assistant" for e in events)

    async def test_stream_message_delta_no_usage(self, provider):
        """message_delta with no usage attribute."""
        event = SimpleNamespace(type="message_delta", usage=None)

        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="test", stop_reason="end_turn",
        )

        async def async_event_iter():
            yield event

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(messages=[]):
            events.append(evt)

        assert any(e["type"] == "assistant" for e in events)

    async def test_stream_with_system_prompt(self, provider):
        """System prompt is included in params."""
        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="test", stop_reason="end_turn",
        )

        async def async_event_iter():
            return
            yield  # noqa

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(
            messages=[], system_prompt="Be helpful",
        ):
            events.append(evt)

        call_kwargs = provider._client.messages.stream.call_args[1]
        # System prompt is now a structured list with cache_control (ADR-061)
        assert call_kwargs["system"] == [
            {"type": "text", "text": "Be helpful", "cache_control": {"type": "ephemeral"}}
        ]

    async def test_stream_with_tools(self, provider):
        """Tools are translated and included."""
        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="test", stop_reason="end_turn",
        )

        async def async_event_iter():
            return
            yield  # noqa

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        tools = [{"name": "Read", "description": "r", "input_schema": {}}]
        events = []
        async for evt in provider.stream(messages=[], tools=tools):
            events.append(evt)

        call_kwargs = provider._client.messages.stream.call_args[1]
        assert "tools" in call_kwargs

    async def test_stream_with_thinking_adaptive(self, provider):
        """Adaptive thinking for supported models."""
        provider._default_model = "claude-sonnet-4-6"
        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="claude-sonnet-4-6",
            stop_reason="end_turn",
        )

        async def async_event_iter():
            return
            yield  # noqa

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(
            messages=[], thinking={"type": "adaptive"},
        ):
            events.append(evt)

        call_kwargs = provider._client.messages.stream.call_args[1]
        assert call_kwargs["thinking"] == {"type": "adaptive"}

    async def test_stream_with_thinking_enabled_non_adaptive_model(self, provider):
        """Enabled thinking for a model that doesn't support adaptive."""
        provider._default_model = "claude-haiku-4-5"
        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="claude-haiku-4-5",
            stop_reason="end_turn",
        )

        async def async_event_iter():
            return
            yield  # noqa

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(
            messages=[], thinking={"type": "enabled", "budget_tokens": 5000},
        ):
            events.append(evt)

        call_kwargs = provider._client.messages.stream.call_args[1]
        assert call_kwargs["thinking"]["type"] == "enabled"
        assert call_kwargs["thinking"]["budget_tokens"] == 5000

    async def test_stream_with_thinking_disabled(self, provider):
        """Disabled thinking is not passed."""
        provider._default_model = "claude-sonnet-4-6"
        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="test", stop_reason="end_turn",
        )

        async def async_event_iter():
            return
            yield  # noqa

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(
            messages=[], thinking={"type": "disabled"},
        ):
            events.append(evt)

        call_kwargs = provider._client.messages.stream.call_args[1]
        assert "thinking" not in call_kwargs

    async def test_stream_with_custom_model(self, provider):
        """Custom model overrides default."""
        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="claude-opus-4-6",
            stop_reason="end_turn",
        )

        async def async_event_iter():
            return
            yield  # noqa

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(
            messages=[], model="claude-opus-4-6",
        ):
            events.append(evt)

        call_kwargs = provider._client.messages.stream.call_args[1]
        assert call_kwargs["model"] == "claude-opus-4-6"

    async def test_stream_with_max_tokens(self, provider):
        """Custom max_tokens."""
        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="test", stop_reason="end_turn",
        )

        async def async_event_iter():
            return
            yield  # noqa

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(messages=[], max_tokens=4096):
            events.append(evt)

        call_kwargs = provider._client.messages.stream.call_args[1]
        assert call_kwargs["max_tokens"] == 4096

    async def test_stream_thinking_adaptive_non_supported_model_enabled(self, provider):
        """adaptive thinking on a non-supported model falls back to checking enabled."""
        provider._default_model = "some-other-model"
        final_msg = SimpleNamespace(
            content=[], id="msg-1", model="some-other-model",
            stop_reason="end_turn",
        )

        async def async_event_iter():
            return
            yield  # noqa

        stream_cm = AsyncMock()
        stream_cm.__aenter__ = AsyncMock(return_value=stream_cm)
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        stream_cm.__aiter__ = lambda self: async_event_iter()
        stream_cm.get_final_message = AsyncMock(return_value=final_msg)

        provider._client.messages.stream = MagicMock(return_value=stream_cm)

        events = []
        async for evt in provider.stream(
            messages=[], thinking={"type": "adaptive"},
        ):
            events.append(evt)

        # For non-supported model with type=adaptive, thinking_type == "adaptive"
        # but supports_adaptive is False, and thinking_type is not "enabled"
        # so thinking should NOT be set
        call_kwargs = provider._client.messages.stream.call_args[1]
        assert "thinking" not in call_kwargs
