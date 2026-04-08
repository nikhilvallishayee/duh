# tests/unit/test_partial_compaction.py
"""Tests for partial compaction and image stripping in SimpleCompactor."""

from __future__ import annotations

import pytest

from duh.adapters.simple_compactor import SimpleCompactor, strip_images
from duh.kernel.messages import Message, TextBlock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _msg(role: str = "user", content: str = "hello", **kw) -> Message:
    return Message(role=role, content=content, id=kw.get("id", "m"), timestamp="t")


def _sys(content: str = "system prompt") -> Message:
    return Message(role="system", content=content, id="sys", timestamp="t0")


# ===========================================================================
# partial_compact
# ===========================================================================

class TestPartialCompact:
    async def test_partial_range_summarized(self):
        """Only messages in [from_idx, to_idx) should be summarized."""
        c = SimpleCompactor(bytes_per_token=1, min_keep=0)
        msgs = [
            _msg(content="aaa", id="m0"),  # idx 0 -- keep
            _msg(content="bbb", id="m1"),  # idx 1 -- compact
            _msg(content="ccc", id="m2"),  # idx 2 -- compact
            _msg(content="ddd", id="m3"),  # idx 3 -- keep
        ]
        result = await c.partial_compact(msgs, from_idx=1, to_idx=3, token_limit=10)
        # msg[0] and msg[3] should be preserved exactly
        assert result[0].content == "aaa"
        assert result[-1].content == "ddd"
        # The middle should be replaced by a summary
        assert len(result) == 3  # before + summary + after
        assert result[1].role == "system"
        assert "summary" in result[1].content.lower() or "previous" in result[1].content.lower()

    async def test_partial_from_start(self):
        c = SimpleCompactor(bytes_per_token=1, min_keep=0)
        msgs = [
            _msg(content="aaa", id="m0"),
            _msg(content="bbb", id="m1"),
            _msg(content="ccc", id="m2"),
        ]
        result = await c.partial_compact(msgs, from_idx=0, to_idx=2, token_limit=10)
        assert result[-1].content == "ccc"
        assert len(result) == 2  # summary + kept

    async def test_partial_to_end(self):
        c = SimpleCompactor(bytes_per_token=1, min_keep=0)
        msgs = [
            _msg(content="aaa", id="m0"),
            _msg(content="bbb", id="m1"),
            _msg(content="ccc", id="m2"),
        ]
        result = await c.partial_compact(msgs, from_idx=1, to_idx=3, token_limit=10)
        assert result[0].content == "aaa"
        assert len(result) == 2  # kept + summary

    async def test_partial_empty_range(self):
        """If from_idx == to_idx, nothing is compacted."""
        c = SimpleCompactor()
        msgs = [_msg(content="aaa"), _msg(content="bbb")]
        result = await c.partial_compact(msgs, from_idx=1, to_idx=1, token_limit=10)
        assert len(result) == 2
        assert result[0].content == "aaa"
        assert result[1].content == "bbb"

    async def test_partial_invalid_range(self):
        """from_idx > to_idx should raise ValueError."""
        c = SimpleCompactor()
        msgs = [_msg(content="aaa")]
        with pytest.raises(ValueError, match="from_idx"):
            await c.partial_compact(msgs, from_idx=2, to_idx=1, token_limit=10)

    async def test_partial_out_of_bounds(self):
        """to_idx beyond len(messages) is clamped."""
        c = SimpleCompactor(bytes_per_token=1, min_keep=0)
        msgs = [_msg(content="aaa"), _msg(content="bbb")]
        result = await c.partial_compact(msgs, from_idx=0, to_idx=100, token_limit=10)
        assert len(result) == 1  # just a summary
        assert result[0].role == "system"

    async def test_partial_does_not_mutate_input(self):
        c = SimpleCompactor(bytes_per_token=1, min_keep=0)
        msgs = [_msg(content="aaa"), _msg(content="bbb"), _msg(content="ccc")]
        original_len = len(msgs)
        await c.partial_compact(msgs, from_idx=0, to_idx=2, token_limit=10)
        assert len(msgs) == original_len

    async def test_partial_with_system_messages(self):
        """System messages inside the range should be included in the summary."""
        c = SimpleCompactor(bytes_per_token=1, min_keep=0)
        msgs = [
            _msg(content="aaa"),
            _sys("sys note"),
            _msg(content="bbb"),
            _msg(content="ccc"),
        ]
        result = await c.partial_compact(msgs, from_idx=1, to_idx=3, token_limit=10)
        assert result[0].content == "aaa"
        assert result[-1].content == "ccc"


# ===========================================================================
# strip_images
# ===========================================================================

class TestStripImages:
    def test_no_images(self):
        msgs = [_msg(content="hello"), _msg(content="world")]
        result = strip_images(msgs)
        assert len(result) == 2
        assert result[0].content == "hello"

    def test_image_block_replaced(self):
        msg = Message(
            role="user",
            content=[
                TextBlock(text="Look at this:"),
                {"type": "image", "source": {"type": "base64", "data": "abc123"}},
            ],
            id="m1", timestamp="t1",
        )
        result = strip_images([msg])
        assert len(result) == 1
        content = result[0].content
        assert isinstance(content, list)
        # The image block should be replaced with a text placeholder
        texts = [
            b.text if isinstance(b, TextBlock) else b.get("text", "")
            for b in content
        ]
        assert any("[image removed for compaction]" in t for t in texts)

    def test_multiple_images(self):
        msg = Message(
            role="user",
            content=[
                {"type": "image", "source": {"data": "a"}},
                TextBlock(text="between"),
                {"type": "image", "source": {"data": "b"}},
            ],
            id="m1", timestamp="t1",
        )
        result = strip_images([msg])
        content = result[0].content
        assert isinstance(content, list)
        # Count image placeholders
        placeholder_count = sum(
            1 for b in content
            if isinstance(b, (TextBlock, dict))
            and "[image removed for compaction]" in (
                b.text if isinstance(b, TextBlock) else b.get("text", "")
            )
        )
        assert placeholder_count == 2

    def test_string_content_unchanged(self):
        msg = _msg(content="just text")
        result = strip_images([msg])
        assert result[0].content == "just text"

    def test_does_not_mutate_input(self):
        orig_block = {"type": "image", "source": {"data": "x"}}
        msg = Message(
            role="user",
            content=[orig_block],
            id="m1", timestamp="t1",
        )
        msgs = [msg]
        strip_images(msgs)
        # Original message should be untouched
        assert msgs[0].content[0]["type"] == "image"

    def test_dict_messages(self):
        msg = {
            "role": "user",
            "content": [
                {"type": "text", "text": "look:"},
                {"type": "image", "source": {"data": "abc"}},
            ],
        }
        result = strip_images([msg])
        content = result[0]["content"]
        texts = [b.get("text", "") for b in content]
        assert any("[image removed for compaction]" in t for t in texts)
