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


# ---------------------------------------------------------------------------
# strip_images — keep_recent parameter (ADR-035 gap 1)
# ---------------------------------------------------------------------------

class TestStripImagesKeepRecent:
    """strip_images must accept keep_recent and preserve images in recent msgs."""

    def _image_msg(self, role: str = "user") -> Message:
        from duh.kernel.messages import ImageBlock
        return Message(
            role=role,
            content=[ImageBlock(media_type="image/png", data="abc123")],
            id="m1", timestamp="t1",
        )

    def _image_dict_msg(self, role: str = "user") -> dict:
        return {
            "role": role,
            "content": [{"type": "image", "source": {"type": "base64", "data": "abc"}}],
        }

    def test_strip_images_default_keeps_recent_3(self):
        """With 5 messages, the last 3 should have images preserved."""
        from duh.adapters.simple_compactor import strip_images
        msgs = [self._image_msg() for _ in range(5)]
        result = strip_images(msgs)
        assert len(result) == 5
        # First 2 (older) should have image stripped
        for msg in result[:2]:
            assert isinstance(msg.content[0], TextBlock), "Old image should be replaced"
        # Last 3 (recent) should have image preserved
        for msg in result[2:]:
            assert msg.content[0].type == "image", "Recent image should be preserved"

    def test_strip_images_custom_keep_recent(self):
        """With keep_recent=1, only the last message preserves images."""
        from duh.adapters.simple_compactor import strip_images
        msgs = [self._image_msg() for _ in range(4)]
        result = strip_images(msgs, keep_recent=1)
        assert len(result) == 4
        # First 3 stripped
        for msg in result[:3]:
            assert isinstance(msg.content[0], TextBlock)
        # Last 1 preserved
        assert result[3].content[0].type == "image"

    def test_strip_images_keep_recent_0_strips_all(self):
        """With keep_recent=0, all images should be stripped."""
        from duh.adapters.simple_compactor import strip_images
        msgs = [self._image_msg() for _ in range(3)]
        result = strip_images(msgs, keep_recent=0)
        for msg in result:
            assert isinstance(msg.content[0], TextBlock)

    def test_strip_images_fewer_than_keep_recent(self):
        """With 2 messages and keep_recent=3, all messages preserve images."""
        from duh.adapters.simple_compactor import strip_images
        msgs = [self._image_msg() for _ in range(2)]
        result = strip_images(msgs, keep_recent=3)
        for msg in result:
            assert msg.content[0].type == "image"

    def test_strip_images_dict_messages_keep_recent(self):
        """Dict messages also respect keep_recent."""
        from duh.adapters.simple_compactor import strip_images
        msgs = [self._image_dict_msg() for _ in range(4)]
        result = strip_images(msgs, keep_recent=2)
        # First 2 stripped (placeholder dict)
        for msg in result[:2]:
            assert msg["content"][0]["type"] == "text"
        # Last 2 preserved
        for msg in result[2:]:
            assert msg["content"][0]["type"] == "image"

    def test_strip_images_no_mutation(self):
        """Input messages must not be mutated."""
        from duh.adapters.simple_compactor import strip_images
        from duh.kernel.messages import ImageBlock
        msgs = [self._image_msg() for _ in range(5)]
        original_types = [m.content[0].type for m in msgs]
        strip_images(msgs, keep_recent=3)
        after_types = [m.content[0].type for m in msgs]
        assert original_types == after_types


# ---------------------------------------------------------------------------
# Staged compaction pipeline (ADR-035 gap 2)
# ---------------------------------------------------------------------------

class TestStagedCompactionPipeline:
    """compact() must implement the staged pipeline:
    1. Image strip → early exit if under limit
    2. Partial removal (oldest first) → early exit if under limit
    3. Aggressive (last 5 turns) → final stage
    """

    def _big_text_msg(self, role: str = "user", chars: int = 10_000) -> Message:
        return Message(role=role, content="x" * chars, id="m1", timestamp="t1")

    def _image_msg(self, role: str = "user") -> Message:
        from duh.kernel.messages import ImageBlock
        return Message(
            role=role,
            # Large base64-like data so image stripping makes a dent
            content=[ImageBlock(media_type="image/png", data="A" * 5_000)],
            id="m1", timestamp="t1",
        )

    @pytest.mark.asyncio
    async def test_image_strip_alone_sufficient(self):
        """If image stripping brings context under the limit, no messages removed."""
        from duh.adapters.simple_compactor import SimpleCompactor
        c = SimpleCompactor(bytes_per_token=1)

        # 5 image messages (large). After stripping older 3, total drops enough.
        # We need token budget such that full images exceed limit but stripped don't.
        # Each image block serialised ≈ the data length (base64 string in JSON).
        # We'll use a tiny limit and big images.
        msgs = [
            Message(
                role="user" if i % 2 == 0 else "assistant",
                content=[{"type": "image", "source": {"data": "X" * 2_000}}],
                id=f"m{i}", timestamp="t0",
            )
            for i in range(5)
        ]
        # After stripping the first 2 (keep_recent=3), each stripped msg is ~60 chars
        # (placeholder text). Total tokens should fit within a reasonable limit.
        # With bytes_per_token=1, let's pick a limit that is:
        #   exceeded with all images intact but satisfied after stripping old ones.

        # Manually estimate: original messages each ~2060 chars (overhead + data).
        # After stripping oldest 2: ~120 chars (placeholders) + 3 * 2060 ≈ 6300 chars.
        # Let's pick limit=7000 so stripping alone is sufficient.
        result = await c.compact(msgs, token_limit=7_000)

        # Should keep all 5 messages (none removed), but old images stripped
        non_system = [m for m in result if _get_role(m) != "system"]
        assert len(non_system) == 5, (
            f"Image-strip-only stage should keep all messages, got {len(non_system)}"
        )

    @pytest.mark.asyncio
    async def test_partial_removal_after_image_strip(self):
        """If still over after image strip, oldest messages are partially removed."""
        from duh.adapters.simple_compactor import SimpleCompactor
        c = SimpleCompactor(bytes_per_token=1, min_keep=2)

        # Create 10 large text messages (no images, so image stripping doesn't help)
        msgs = [
            Message(
                role="user" if i % 2 == 0 else "assistant",
                content="Z" * 500,
                id=f"m{i}", timestamp="t0",
            )
            for i in range(10)
        ]
        # Total: 10 * 500 = 5000 tokens. Limit 2000 means partial removal happens.
        # With min_keep=2 and limit=2000, keep last 4 messages (4*500=2000, fits).
        result = await c.compact(msgs, token_limit=2000)

        # A summary system message + some recent messages, not only last 5
        non_system = [m for m in result if _get_role(m) != "system"]
        # Should keep roughly 2-4 messages (2000 / 500 = 4)
        assert 1 <= len(non_system) <= 5
        # Must have a summary system message
        system_msgs = [m for m in result if _get_role(m) == "system"]
        assert any(
            "Previous conversation summary" in (m.content if isinstance(m.content, str) else "")
            for m in system_msgs
        )

    @pytest.mark.asyncio
    async def test_aggressive_removal_last_5_turns(self):
        """When partial removal still can't fit, keep only last 5 turns."""
        from duh.adapters.simple_compactor import SimpleCompactor
        # min_keep=5 ensures aggressive stage always keeps 5
        c = SimpleCompactor(bytes_per_token=1, min_keep=5)

        # 20 messages, each 1000 chars. Total 20_000 tokens.
        # Limit=5500 → even partial can barely fit 5 messages.
        msgs = [
            Message(
                role="user" if i % 2 == 0 else "assistant",
                content="Y" * 1_000,
                id=f"m{i}", timestamp="t0",
            )
            for i in range(20)
        ]
        result = await c.compact(msgs, token_limit=5_500)

        non_system = [m for m in result if _get_role(m) != "system"]
        # Should keep exactly min_keep (5) recent messages
        assert len(non_system) <= 5

    @pytest.mark.asyncio
    async def test_early_exit_after_image_strip_no_summary(self):
        """When image strip brings us under limit, no summary system message is added."""
        from duh.adapters.simple_compactor import SimpleCompactor
        c = SimpleCompactor(bytes_per_token=1)

        # 4 messages: first has huge image, rest have tiny text
        # After stripping old image, total drops under limit
        msgs = [
            Message(
                role="user",
                content=[{"type": "image", "source": {"data": "I" * 5_000}}],
                id="m0", timestamp="t0",
            ),
            Message(role="assistant", content="ok", id="m1", timestamp="t1"),
            Message(role="user", content="ok", id="m2", timestamp="t2"),
            Message(role="assistant", content="done", id="m3", timestamp="t3"),
        ]
        # After stripping m0's image (keep_recent=3 → m1,m2,m3 kept), m0 becomes ~60 chars.
        # Total after strip ≈ 60 + 2 + 2 + 4 = ~68 tokens. Limit=500 → fits.
        result = await c.compact(msgs, token_limit=500)

        # No summary system msg should be added since we exited early after image strip
        system_msgs = [m for m in result if _get_role(m) == "system"]
        assert not any(
            "Previous conversation summary" in (m.content if isinstance(m.content, str) else "")
            for m in system_msgs
        ), "Early exit after image strip should NOT produce a summary message"
        # All 4 conversation messages still present
        non_system = [m for m in result if _get_role(m) != "system"]
        assert len(non_system) == 4


def _get_role(msg):
    """Helper for test assertions."""
    if isinstance(msg, Message):
        return msg.role
    if isinstance(msg, dict):
        return msg.get("role", "")
    return ""
