"""Tests for duh.adapters.simple_compactor — context window management."""

import json

import pytest

from duh.adapters.simple_compactor import (
    SimpleCompactor,
    _get_role,
    _serialize_message,
    _block_to_serializable,
)
from duh.kernel.messages import Message, TextBlock, ToolUseBlock, ThinkingBlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(role: str = "user", content: str = "hello") -> Message:
    return Message(role=role, content=content, id="m1", timestamp="t1")


def _sys(content: str = "You are helpful.") -> Message:
    return Message(role="system", content=content, id="sys", timestamp="t0")


# ---------------------------------------------------------------------------
# estimate_tokens — basic
# ---------------------------------------------------------------------------

class TestEstimateTokens:
    def test_empty_list(self):
        c = SimpleCompactor()
        assert c.estimate_tokens([]) == 0

    def test_single_short_message(self):
        c = SimpleCompactor()
        msg = _msg(content="hi")  # 2 chars → 2 // 4 = 0
        assert c.estimate_tokens([msg]) == 0

    def test_single_message_basic_math(self):
        c = SimpleCompactor()
        msg = _msg(content="a" * 100)  # 100 chars → 100 // 4 = 25
        assert c.estimate_tokens([msg]) == 25

    def test_multiple_messages(self):
        c = SimpleCompactor()
        msgs = [_msg(content="a" * 40), _msg(content="b" * 80)]
        # 40//4 + 80//4 = 10 + 20 = 30
        assert c.estimate_tokens(msgs) == 30

    def test_custom_bytes_per_token(self):
        c = SimpleCompactor(bytes_per_token=2)
        msg = _msg(content="a" * 100)  # 100 // 2 = 50
        assert c.estimate_tokens([msg]) == 50

    def test_dict_message(self):
        c = SimpleCompactor()
        msg = {"role": "user", "content": "a" * 100}
        assert c.estimate_tokens([msg]) == 25

    def test_dict_message_with_list_content(self):
        c = SimpleCompactor()
        msg = {"role": "user", "content": [{"type": "text", "text": "hello world"}]}
        tokens = c.estimate_tokens([msg])
        # Should serialize the list content to JSON
        assert tokens > 0

    def test_message_with_content_blocks(self):
        c = SimpleCompactor()
        msg = Message(
            role="assistant",
            content=[
                TextBlock(text="a" * 100),
                ToolUseBlock(id="tu1", name="Read", input={"path": "/tmp"}),
            ],
            id="m1", timestamp="t1",
        )
        tokens = c.estimate_tokens([msg])
        # Serialized JSON of content blocks / 4
        assert tokens > 0

    def test_message_with_thinking_block(self):
        c = SimpleCompactor()
        msg = Message(
            role="assistant",
            content=[ThinkingBlock(thinking="x" * 200)],
            id="m1", timestamp="t1",
        )
        tokens = c.estimate_tokens([msg])
        assert tokens > 0

    def test_plain_string_fallback(self):
        c = SimpleCompactor()
        # Non-Message, non-dict falls back to str()
        tokens = c.estimate_tokens(["a raw string of 40 characters!!!!!!!!!!"])
        assert tokens > 0


# ---------------------------------------------------------------------------
# compact — tail-window truncation
# ---------------------------------------------------------------------------

class TestCompact:
    async def test_empty_messages(self):
        c = SimpleCompactor()
        result = await c.compact([], token_limit=100)
        assert result == []

    async def test_all_fit_within_limit(self):
        c = SimpleCompactor()
        msgs = [_msg(content="hi"), _msg(role="assistant", content="hey")]
        result = await c.compact(msgs, token_limit=100_000)
        assert len(result) == 2

    async def test_truncates_oldest(self):
        c = SimpleCompactor(bytes_per_token=1, min_keep=1)
        msgs = [
            _msg(content="a" * 50),  # 50 tokens
            _msg(content="b" * 50),  # 50 tokens
            _msg(content="c" * 50),  # 50 tokens
        ]
        # Limit 80 → only the last message fits (50) before second would push to 100
        # Dropped messages become a summary system message
        result = await c.compact(msgs, token_limit=80)
        assert len(result) == 2  # summary + kept
        assert result[0].role == "system"
        assert "Previous conversation summary" in result[0].content
        assert result[1].content == "c" * 50

    async def test_keeps_system_messages(self):
        c = SimpleCompactor(bytes_per_token=1, min_keep=1)
        msgs = [
            _sys("x" * 10),          # 10 tokens, system — always kept
            _msg(content="a" * 50),   # 50 tokens
            _msg(content="b" * 50),   # 50 tokens
        ]
        # Limit 65: system uses 10, budget=55, last msg uses 50 → fits.
        # Dropped "a" message becomes a summary system message.
        result = await c.compact(msgs, token_limit=65)
        assert len(result) == 3  # original system + summary + kept
        assert result[0].role == "system"  # original
        assert result[1].role == "system"  # summary
        assert "Previous conversation summary" in result[1].content
        assert result[2].content == "b" * 50

    async def test_system_messages_always_first(self):
        c = SimpleCompactor()
        msgs = [
            _msg(content="user first"),
            _sys("system"),
            _msg(content="user second"),
        ]
        result = await c.compact(msgs, token_limit=100_000)
        assert result[0].role == "system"

    async def test_min_keep_overrides_limit(self):
        c = SimpleCompactor(bytes_per_token=1, min_keep=2)
        msgs = [
            _msg(content="a" * 100),  # 100 tokens
            _msg(content="b" * 100),  # 100 tokens
        ]
        # Limit 10 → normally nothing fits, but min_keep=2 forces both
        result = await c.compact(msgs, token_limit=10)
        assert len(result) == 2

    async def test_min_keep_zero(self):
        c = SimpleCompactor(bytes_per_token=1, min_keep=0)
        msgs = [_msg(content="a" * 100)]
        # Limit 10 → message doesn't fit, min_keep=0 → only summary
        result = await c.compact(msgs, token_limit=10)
        assert len(result) == 1  # summary of dropped message
        assert result[0].role == "system"
        assert "Previous conversation summary" in result[0].content

    async def test_uses_default_limit(self):
        c = SimpleCompactor(default_limit=50_000)
        msgs = [_msg(content="short")]
        # No explicit limit → uses default (50K is plenty for "short")
        result = await c.compact(msgs)
        assert len(result) == 1

    async def test_only_system_messages(self):
        c = SimpleCompactor()
        msgs = [_sys("sys1"), _sys("sys2")]
        result = await c.compact(msgs, token_limit=100_000)
        assert len(result) == 2
        assert all(r.role == "system" for r in result)

    async def test_does_not_mutate_input(self):
        c = SimpleCompactor(bytes_per_token=1, min_keep=1)
        msgs = [_msg(content="a" * 50), _msg(content="b" * 50)]
        original_len = len(msgs)
        await c.compact(msgs, token_limit=60)
        assert len(msgs) == original_len  # Input not mutated

    async def test_dict_messages_compact(self):
        c = SimpleCompactor(bytes_per_token=1, min_keep=1)
        msgs = [
            {"role": "user", "content": "a" * 50},
            {"role": "assistant", "content": "b" * 50},
        ]
        result = await c.compact(msgs, token_limit=60)
        assert len(result) == 2  # summary + kept
        assert result[0].role == "system"  # summary is a Message
        assert result[1]["content"] == "b" * 50

    async def test_mixed_message_types(self):
        c = SimpleCompactor()
        msgs = [
            _sys("system prompt"),
            {"role": "user", "content": "dict msg"},
            _msg(role="assistant", content="Message obj"),
        ]
        result = await c.compact(msgs, token_limit=100_000)
        assert len(result) == 3

    async def test_large_system_prompt_eats_budget(self):
        c = SimpleCompactor(bytes_per_token=1, min_keep=0)
        msgs = [
            _sys("x" * 90),          # 90 tokens
            _msg(content="a" * 20),   # 20 tokens
        ]
        # Limit 100: system=90, budget=10, conversation msg=20 > 10
        # Dropped conversation msg becomes summary
        result = await c.compact(msgs, token_limit=100)
        assert len(result) == 2  # original system + summary
        assert result[0].role == "system"  # original
        assert result[1].role == "system"  # summary
        assert "Previous conversation summary" in result[1].content


# ---------------------------------------------------------------------------
# Constructor validation
# ---------------------------------------------------------------------------

class TestConstructor:
    def test_invalid_bytes_per_token(self):
        with pytest.raises(ValueError, match="bytes_per_token"):
            SimpleCompactor(bytes_per_token=0)

    def test_invalid_min_keep(self):
        with pytest.raises(ValueError, match="min_keep"):
            SimpleCompactor(min_keep=-1)

    def test_properties(self):
        c = SimpleCompactor(default_limit=50_000, bytes_per_token=2, min_keep=3)
        assert c.default_limit == 50_000
        assert c.bytes_per_token == 2
        assert c.min_keep == 3


# ---------------------------------------------------------------------------
# Protocol conformance
# ---------------------------------------------------------------------------

class TestProtocol:
    def test_implements_context_manager(self):
        from duh.ports.context import ContextManager
        c = SimpleCompactor()
        assert isinstance(c, ContextManager)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class TestHelpers:
    def test_get_role_message(self):
        assert _get_role(_msg(role="user")) == "user"

    def test_get_role_dict(self):
        assert _get_role({"role": "assistant"}) == "assistant"

    def test_get_role_unknown(self):
        assert _get_role(42) == ""

    def test_get_role_dict_missing(self):
        assert _get_role({}) == ""

    def test_serialize_message_str_content(self):
        s = _serialize_message(_msg(content="hello"))
        assert s == "hello"

    def test_serialize_message_list_content(self):
        msg = Message(
            role="assistant",
            content=[TextBlock(text="hello")],
            id="m1", timestamp="t1",
        )
        s = _serialize_message(msg)
        parsed = json.loads(s)
        assert parsed[0]["text"] == "hello"

    def test_serialize_dict_str_content(self):
        s = _serialize_message({"role": "user", "content": "hi"})
        assert s == "hi"

    def test_serialize_dict_list_content(self):
        s = _serialize_message({"content": [{"type": "text", "text": "x"}]})
        parsed = json.loads(s)
        assert parsed[0]["text"] == "x"

    def test_serialize_non_message(self):
        s = _serialize_message(12345)
        assert s == "12345"

    def test_block_to_serializable_dict(self):
        d = {"type": "text", "text": "hi"}
        assert _block_to_serializable(d) is d

    def test_block_to_serializable_dataclass(self):
        b = TextBlock(text="hello")
        result = _block_to_serializable(b)
        assert isinstance(result, dict)
        assert result["text"] == "hello"

    def test_block_to_serializable_other(self):
        result = _block_to_serializable(42)
        assert result == "42"
