"""Tests for prompt caching (ADR-061 Phase 1 & 2).

Verifies that the Anthropic adapter:
1. Wraps system prompts with cache_control markers
2. Marks the conversation prefix boundary for caching
3. Preserves cache_control through block sanitization
"""

import pytest

from duh.adapters.anthropic import (
    _add_prefix_cache_marker,
    _build_cached_system,
    _build_system_text,
    _sanitize_block,
    _to_api_messages,
)
from duh.kernel.messages import Message


CACHE_MARKER = {"type": "ephemeral"}


# ═══════════════════════════════════════════════════════════════════
# Phase 1: System prompt caching
# ═══════════════════════════════════════════════════════════════════


class TestBuildCachedSystem:
    """_build_cached_system wraps system text with cache_control."""

    def test_returns_list_with_one_block(self):
        result = _build_cached_system("You are helpful.")
        assert isinstance(result, list)
        assert len(result) == 1

    def test_block_has_text_type(self):
        result = _build_cached_system("You are helpful.")
        assert result[0]["type"] == "text"

    def test_block_preserves_text(self):
        prompt = "You are a coding assistant.\nBe concise."
        result = _build_cached_system(prompt)
        assert result[0]["text"] == prompt

    def test_block_has_cache_control(self):
        result = _build_cached_system("You are helpful.")
        assert result[0]["cache_control"] == CACHE_MARKER

    def test_long_system_prompt(self):
        """Large system prompts (the common case) should still get the marker."""
        prompt = "x" * 10_000
        result = _build_cached_system(prompt)
        assert result[0]["cache_control"] == CACHE_MARKER
        assert result[0]["text"] == prompt


class TestSystemPromptIntegration:
    """Verify the full system prompt pipeline produces cached output.

    _build_system_text -> _build_cached_system -> params["system"]
    """

    def test_string_prompt_gets_cached(self):
        text = _build_system_text("Hello")
        result = _build_cached_system(text)
        assert result[0]["cache_control"] == CACHE_MARKER
        assert result[0]["text"] == "Hello"

    def test_list_prompt_gets_cached(self):
        text = _build_system_text(["Part A", "Part B"])
        result = _build_cached_system(text)
        assert "Part A" in result[0]["text"]
        assert "Part B" in result[0]["text"]
        assert result[0]["cache_control"] == CACHE_MARKER

    def test_empty_prompt_not_cached(self):
        """Empty system prompts should not reach _build_cached_system.

        The adapter skips the system param when text is empty, so the
        cache function itself should still work but the adapter won't call it.
        """
        text = _build_system_text("")
        assert text == ""
        # Verify _build_cached_system still works if called with empty
        result = _build_cached_system("")
        assert result[0]["text"] == ""
        assert result[0]["cache_control"] == CACHE_MARKER


# ═══════════════════════════════════════════════════════════════════
# Phase 2: Message prefix caching
# ═══════════════════════════════════════════════════════════════════


class TestAddPrefixCacheMarker:
    """_add_prefix_cache_marker marks the prefix boundary."""

    def test_no_messages_is_noop(self):
        msgs: list = []
        _add_prefix_cache_marker(msgs)
        assert msgs == []

    def test_single_message_is_noop(self):
        msgs = [{"role": "user", "content": "hello"}]
        _add_prefix_cache_marker(msgs)
        # Single message has no prefix to cache
        assert msgs == [{"role": "user", "content": "hello"}]

    def test_two_messages_marks_first(self):
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "q1"}]},
            {"role": "user", "content": [{"type": "text", "text": "q2"}]},
        ]
        _add_prefix_cache_marker(msgs)
        # Second-to-last message's last block gets the marker
        assert msgs[0]["content"][-1]["cache_control"] == CACHE_MARKER
        # Last message is untouched
        assert "cache_control" not in msgs[1]["content"][-1]

    def test_multi_turn_marks_second_to_last(self):
        msgs = [
            {"role": "user", "content": [{"type": "text", "text": "q1"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "a1"}]},
            {"role": "user", "content": [{"type": "text", "text": "q2"}]},
        ]
        _add_prefix_cache_marker(msgs)
        # The assistant response (second-to-last) gets the marker
        assert msgs[1]["content"][-1]["cache_control"] == CACHE_MARKER
        # First and last messages are untouched
        assert "cache_control" not in msgs[0]["content"][-1]
        assert "cache_control" not in msgs[2]["content"][-1]

    def test_string_content_gets_converted(self):
        msgs = [
            {"role": "user", "content": "plain text question"},
            {"role": "user", "content": "new question"},
        ]
        _add_prefix_cache_marker(msgs)
        # String content should be converted to list with cache_control
        assert isinstance(msgs[0]["content"], list)
        assert msgs[0]["content"][0]["type"] == "text"
        assert msgs[0]["content"][0]["text"] == "plain text question"
        assert msgs[0]["content"][0]["cache_control"] == CACHE_MARKER

    def test_multi_block_message_marks_last_block(self):
        msgs = [
            {"role": "user", "content": [
                {"type": "text", "text": "first block"},
                {"type": "text", "text": "second block"},
            ]},
            {"role": "user", "content": [{"type": "text", "text": "new"}]},
        ]
        _add_prefix_cache_marker(msgs)
        # Only the LAST block in the prefix message gets the marker
        assert "cache_control" not in msgs[0]["content"][0]
        assert msgs[0]["content"][1]["cache_control"] == CACHE_MARKER

    def test_tool_result_block_gets_marker(self):
        msgs = [
            {"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": "tu1", "content": "data"},
            ]},
            {"role": "user", "content": [{"type": "text", "text": "next"}]},
        ]
        _add_prefix_cache_marker(msgs)
        assert msgs[0]["content"][-1]["cache_control"] == CACHE_MARKER

    def test_empty_content_list_is_noop(self):
        msgs = [
            {"role": "user", "content": []},
            {"role": "user", "content": [{"type": "text", "text": "new"}]},
        ]
        _add_prefix_cache_marker(msgs)
        # Empty content list — nothing to mark
        assert msgs[0]["content"] == []

    def test_empty_string_content_is_noop(self):
        msgs = [
            {"role": "user", "content": ""},
            {"role": "user", "content": "new"},
        ]
        _add_prefix_cache_marker(msgs)
        # Empty string — nothing to mark
        assert msgs[0]["content"] == ""


# ═══════════════════════════════════════════════════════════════════
# Sanitize block preserves cache_control
# ═══════════════════════════════════════════════════════════════════


class TestSanitizeBlockCacheControl:
    """cache_control must survive _sanitize_block."""

    def test_text_block_keeps_cache_control(self):
        block = {"type": "text", "text": "hi", "cache_control": CACHE_MARKER}
        result = _sanitize_block(block)
        assert result["cache_control"] == CACHE_MARKER

    def test_text_block_still_strips_unknown(self):
        block = {
            "type": "text",
            "text": "hi",
            "cache_control": CACHE_MARKER,
            "extra_junk": "bad",
        }
        result = _sanitize_block(block)
        assert "extra_junk" not in result
        assert result["cache_control"] == CACHE_MARKER

    def test_tool_use_block_keeps_cache_control(self):
        block = {
            "type": "tool_use",
            "id": "tu1",
            "name": "Read",
            "input": {},
            "cache_control": CACHE_MARKER,
        }
        result = _sanitize_block(block)
        assert result["cache_control"] == CACHE_MARKER

    def test_tool_result_block_keeps_cache_control(self):
        block = {
            "type": "tool_result",
            "tool_use_id": "tu1",
            "content": "data",
            "cache_control": CACHE_MARKER,
        }
        result = _sanitize_block(block)
        assert result["cache_control"] == CACHE_MARKER

    def test_text_block_without_cache_control_unchanged(self):
        block = {"type": "text", "text": "hi"}
        result = _sanitize_block(block)
        assert result == {"type": "text", "text": "hi"}
        assert "cache_control" not in result


# ═══════════════════════════════════════════════════════════════════
# End-to-end: _to_api_messages preserves cache_control
# ═══════════════════════════════════════════════════════════════════


class TestApiMessagesCacheControl:
    """Verify cache markers survive the full message translation pipeline."""

    def test_cache_control_in_content_block_preserved(self):
        msgs = [Message(role="user", content=[
            {"type": "text", "text": "hello", "cache_control": CACHE_MARKER},
        ])]
        result = _to_api_messages(msgs)
        assert result[0]["content"][0]["cache_control"] == CACHE_MARKER

    def test_prefix_marker_after_translation(self):
        """Full pipeline: translate messages, then add prefix marker."""
        msgs = [
            Message(role="user", content="q1"),
            Message(role="assistant", content="a1"),
            Message(role="user", content="q2"),
        ]
        api_msgs = _to_api_messages(msgs)
        _add_prefix_cache_marker(api_msgs)

        # The assistant message (index 1) should have a cache marker
        content = api_msgs[1].get("content")
        if isinstance(content, list):
            assert content[-1]["cache_control"] == CACHE_MARKER
        elif isinstance(content, str):
            # String was converted — should not happen for "a1" since
            # _add_prefix_cache_marker converts strings
            pytest.fail("Expected string to be converted to list")
