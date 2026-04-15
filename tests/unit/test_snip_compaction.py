"""Tests for snip compaction (ADR-060).

Tests cover:
- Snip preserves first user message
- Snip preserves last N messages
- Snip removes complete rounds only
- Snip boundary marker is inserted
- Snip maintains alternation (no consecutive same-role)
- Estimate savings returns reasonable numbers
- Empty/short message lists are not snipped
- CompactionStrategy protocol conformance
- Integration with AdaptiveCompactor (threshold gating)
"""

from __future__ import annotations

import pytest
from typing import Any

from duh.kernel.messages import (
    Message,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)
from duh.adapters.compact import CompactionStrategy
from duh.adapters.compact.snip import SnipCompactor, _SNIP_MARKER_PREFIX


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_round(
    tool_name: str,
    tool_input: dict,
    result_content: str,
    tool_id: str,
) -> tuple[Message, Message]:
    """Create an assistant tool_use message and a user tool_result message."""
    assistant = Message(
        role="assistant",
        content=[ToolUseBlock(id=tool_id, name=tool_name, input=tool_input)],
    )
    user = Message(
        role="user",
        content=[{
            "type": "tool_result",
            "tool_use_id": tool_id,
            "content": result_content,
        }],
    )
    return assistant, user


def _make_conversation(num_rounds: int) -> list[Message]:
    """Build a typical conversation: first user msg + N tool rounds.

    Structure: [user, assistant, user(tool_result), assistant, user(tool_result), ...]
    """
    messages: list[Message] = [
        Message(role="user", content="Please implement the feature."),
    ]
    for i in range(num_rounds):
        assistant, user = _make_tool_round(
            "Read",
            {"file_path": f"/path/file_{i}.py"},
            f"content of file_{i} " * 100,  # ~2000 chars each
            tool_id=f"tu_{i}",
        )
        messages.append(assistant)
        messages.append(user)
    return messages


def _get_role(msg: Any) -> str:
    if isinstance(msg, Message):
        return msg.role
    if isinstance(msg, dict):
        return msg.get("role", "")
    return ""


def _get_text(msg: Any) -> str:
    if isinstance(msg, Message):
        if isinstance(msg.content, str):
            return msg.content
        return msg.text
    return ""


# ===========================================================================
# Core Snip Tests
# ===========================================================================

class TestSnipPreservesFirstUserMessage:
    """The first user message (task context) must never be snipped."""

    def test_first_user_message_preserved(self):
        sc = SnipCompactor(keep_last=2)
        messages = _make_conversation(num_rounds=5)

        result, freed = sc.snip(messages)

        # First message should still be the original user prompt.
        assert _get_role(result[0]) == "user"
        assert "implement the feature" in _get_text(result[0])

    def test_first_user_message_preserved_with_keep_last_1(self):
        sc = SnipCompactor(keep_last=1)
        messages = _make_conversation(num_rounds=5)

        result, freed = sc.snip(messages)

        assert _get_role(result[0]) == "user"
        assert "implement the feature" in _get_text(result[0])


class TestSnipPreservesLastN:
    """The last keep_last messages are never touched."""

    def test_preserves_last_6(self):
        sc = SnipCompactor(keep_last=6)
        messages = _make_conversation(num_rounds=10)  # 21 messages total

        result, freed = sc.snip(messages)

        # Last 6 messages should be identical to originals.
        original_tail = messages[-6:]
        result_tail = result[-6:]
        for orig, got in zip(original_tail, result_tail):
            assert orig.id == got.id

    def test_preserves_last_4(self):
        sc = SnipCompactor(keep_last=4)
        messages = _make_conversation(num_rounds=8)

        result, freed = sc.snip(messages)

        original_tail = messages[-4:]
        result_tail = result[-4:]
        for orig, got in zip(original_tail, result_tail):
            assert orig.id == got.id


class TestSnipRemovesCompleteRoundsOnly:
    """Snip only removes complete assistant+user(tool_result) pairs."""

    def test_complete_rounds_removed(self):
        sc = SnipCompactor(keep_last=2)
        messages = _make_conversation(num_rounds=5)  # 11 messages

        result, freed = sc.snip(messages)

        # The marker is embedded in the first user message, so the
        # result has exactly (original - snipped) messages.
        removed_count = len(messages) - len(result)
        assert removed_count % 2 == 0
        assert removed_count > 0

    def test_odd_message_in_snippable_stops_snip(self):
        """If the snippable window has a lone assistant (no following user),
        snip stops before it to avoid breaking pairs."""
        sc = SnipCompactor(keep_last=2)
        # Conversation where an unpaired assistant sits in the snippable window:
        # [user, assistant(lone), assistant, user, assistant, user]
        # The snippable window (index 1..3) starts with assistant+assistant
        # which is not a valid round, so nothing is snipped.
        messages = [
            Message(role="user", content="Start task."),
            Message(role="assistant", content="Let me think..."),
            Message(role="assistant", content="Actually..."),  # breaks pair
            Message(role="user", content="Go ahead."),
            Message(role="assistant", content="Done."),
            Message(role="user", content="Thanks."),
        ]

        result, freed = sc.snip(messages)

        # No valid round starts at the front of the snippable window,
        # so nothing is snipped and the list is unchanged.
        assert freed == 0
        assert len(result) == len(messages)


class TestSnipBoundaryMarker:
    """A snip boundary marker is embedded in the first user message."""

    def test_marker_embedded_in_first_user(self):
        sc = SnipCompactor(keep_last=2)
        messages = _make_conversation(num_rounds=5)

        result, freed = sc.snip(messages)

        # The first user message should contain the snip marker text.
        first_user = result[0]
        assert isinstance(first_user, Message)
        assert first_user.role == "user"
        text = first_user.content if isinstance(first_user.content, str) else first_user.text
        assert _SNIP_MARKER_PREFIX in text

    def test_marker_metadata(self):
        sc = SnipCompactor(keep_last=2)
        messages = _make_conversation(num_rounds=5)

        result, freed = sc.snip(messages)

        markers = [
            m for m in result
            if isinstance(m, Message) and m.metadata.get("subtype") == "snip_boundary"
        ]
        assert len(markers) == 1
        assert markers[0].metadata["snipped_count"] > 0

    def test_marker_preserves_original_content(self):
        """The original first user message content is still present."""
        sc = SnipCompactor(keep_last=2)
        messages = _make_conversation(num_rounds=5)

        result, freed = sc.snip(messages)

        text = result[0].content if isinstance(result[0].content, str) else result[0].text
        assert "implement the feature" in text

    def test_marker_preserves_original_id(self):
        """The first user message retains its original id."""
        sc = SnipCompactor(keep_last=2)
        messages = _make_conversation(num_rounds=5)
        original_id = messages[0].id

        result, freed = sc.snip(messages)

        assert result[0].id == original_id

    def test_no_marker_when_nothing_snipped(self):
        """No marker if nothing was snipped."""
        sc = SnipCompactor(keep_last=20)
        messages = _make_conversation(num_rounds=3)  # 7 messages, keep 20

        result, freed = sc.snip(messages)

        # No message should contain the snip marker text.
        for m in result:
            if isinstance(m, Message):
                text = m.content if isinstance(m.content, str) else m.text
                assert _SNIP_MARKER_PREFIX not in text
        assert freed == 0


class TestSnipMaintainsAlternation:
    """After snip, no two consecutive messages share the same role."""

    def test_alternation_preserved(self):
        sc = SnipCompactor(keep_last=4)
        messages = _make_conversation(num_rounds=10)

        result, freed = sc.snip(messages)

        for i in range(1, len(result)):
            prev_role = _get_role(result[i - 1])
            curr_role = _get_role(result[i])
            assert prev_role != curr_role, (
                f"Consecutive same-role at index {i-1},{i}: "
                f"{prev_role}, {curr_role}"
            )

    def test_alternation_after_large_snip(self):
        sc = SnipCompactor(keep_last=2)
        messages = _make_conversation(num_rounds=20)

        result, freed = sc.snip(messages)

        for i in range(1, len(result)):
            prev_role = _get_role(result[i - 1])
            curr_role = _get_role(result[i])
            assert prev_role != curr_role


class TestEstimateSavings:
    """estimate_savings returns reasonable numbers."""

    def test_returns_positive_for_long_conversation(self):
        sc = SnipCompactor(keep_last=4)
        messages = _make_conversation(num_rounds=10)

        savings = sc.estimate_savings(messages)

        assert savings > 0

    def test_returns_zero_for_short_conversation(self):
        sc = SnipCompactor(keep_last=6)
        messages = _make_conversation(num_rounds=2)  # 5 messages, keep 6

        savings = sc.estimate_savings(messages)

        assert savings == 0

    def test_matches_actual_snip(self):
        """Estimate should match the tokens actually freed by snip."""
        sc = SnipCompactor(keep_last=4)
        messages = _make_conversation(num_rounds=8)

        savings = sc.estimate_savings(messages)
        _, actual_freed = sc.snip(messages)

        assert savings == actual_freed

    def test_zero_for_empty(self):
        sc = SnipCompactor(keep_last=6)
        assert sc.estimate_savings([]) == 0

    def test_zero_when_keep_last_exceeds_length(self):
        sc = SnipCompactor(keep_last=100)
        messages = _make_conversation(num_rounds=3)
        assert sc.estimate_savings(messages) == 0


class TestEmptyAndShort:
    """Empty and short message lists should not be snipped."""

    def test_empty_list(self):
        sc = SnipCompactor(keep_last=6)
        result, freed = sc.snip([])
        assert result == []
        assert freed == 0

    def test_single_message(self):
        sc = SnipCompactor(keep_last=6)
        messages = [Message(role="user", content="Hello")]
        result, freed = sc.snip(messages)
        assert len(result) == 1
        assert freed == 0

    def test_two_messages(self):
        sc = SnipCompactor(keep_last=6)
        messages = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi"),
        ]
        result, freed = sc.snip(messages)
        assert len(result) == 2
        assert freed == 0

    def test_messages_equal_to_keep_last(self):
        sc = SnipCompactor(keep_last=5)
        messages = _make_conversation(num_rounds=2)  # 5 messages
        result, freed = sc.snip(messages)
        assert len(result) == 5
        assert freed == 0

    def test_no_user_messages(self):
        """A list with no user messages should not be snipped."""
        sc = SnipCompactor(keep_last=2)
        messages = [
            Message(role="assistant", content="I'm thinking"),
            Message(role="assistant", content="Still thinking"),
        ]
        result, freed = sc.snip(messages)
        assert len(result) == 2
        assert freed == 0


# ===========================================================================
# CompactionStrategy Protocol Conformance
# ===========================================================================

class TestProtocolConformance:
    """SnipCompactor satisfies the CompactionStrategy protocol."""

    def test_satisfies_protocol(self):
        sc = SnipCompactor()
        assert isinstance(sc, CompactionStrategy)

    @pytest.mark.asyncio
    async def test_compact_returns_list(self):
        sc = SnipCompactor(keep_last=2)
        messages = _make_conversation(num_rounds=5)
        result = await sc.compact(messages, token_limit=100_000)
        assert isinstance(result, list)

    def test_estimate_tokens_positive(self):
        sc = SnipCompactor()
        messages = [Message(role="user", content="Hello world")]
        assert sc.estimate_tokens(messages) > 0

    def test_estimate_tokens_empty(self):
        sc = SnipCompactor()
        assert sc.estimate_tokens([]) == 0

    @pytest.mark.asyncio
    async def test_compact_empty(self):
        sc = SnipCompactor()
        result = await sc.compact([], token_limit=100_000)
        assert result == []


# ===========================================================================
# Integration with AdaptiveCompactor
# ===========================================================================

class TestAdaptiveIntegration:
    """Snip is wired into the default AdaptiveCompactor pipeline."""

    def test_default_pipeline_includes_snip(self):
        """AdaptiveCompactor default pipeline should include SnipCompactor."""
        from duh.adapters.compact.adaptive import AdaptiveCompactor

        ac = AdaptiveCompactor()
        strategy_names = [type(s).__name__ for s in ac._strategies]
        assert "SnipCompactor" in strategy_names

    def test_snip_is_between_micro_and_dedup(self):
        """Snip should come after microcompact, before dedup."""
        from duh.adapters.compact.adaptive import AdaptiveCompactor

        ac = AdaptiveCompactor()
        names = [type(s).__name__ for s in ac._strategies]
        micro_idx = names.index("MicroCompactor")
        snip_idx = names.index("SnipCompactor")
        dedup_idx = names.index("DedupCompactor")
        assert micro_idx < snip_idx < dedup_idx

    @pytest.mark.asyncio
    async def test_snip_skipped_below_threshold(self):
        """Snip should be skipped if usage is below 75% of limit."""
        from duh.adapters.compact.adaptive import AdaptiveCompactor

        snip_called = False

        class MockSnip:
            async def compact(self, messages, token_limit=0):
                nonlocal snip_called
                snip_called = True
                return messages

            def estimate_tokens(self, messages):
                return sum(len(str(m)) for m in messages) // 4

        class AlwaysFit:
            """Tier that makes things fit."""
            async def compact(self, messages, token_limit=0):
                return [Message(role="user", content="short")]

            def estimate_tokens(self, messages):
                return sum(len(str(m)) for m in messages) // 4

        # Override _THRESHOLD_GATES to apply to our mock
        from duh.adapters.compact import adaptive as adaptive_mod
        old_gates = adaptive_mod._THRESHOLD_GATES.copy()
        adaptive_mod._THRESHOLD_GATES["MockSnip"] = 0.75

        try:
            ac = AdaptiveCompactor(
                strategies=[MockSnip(), AlwaysFit()],
                output_buffer=0,
            )
            # Messages use ~50% of limit → snip should NOT fire
            messages = [Message(role="user", content="x" * 2000)]
            await ac.compact(messages, token_limit=2000)

            assert not snip_called
        finally:
            adaptive_mod._THRESHOLD_GATES = old_gates

    @pytest.mark.asyncio
    async def test_snip_fires_above_threshold(self):
        """Snip should fire when usage is above 75% of limit."""
        from duh.adapters.compact.adaptive import AdaptiveCompactor

        snip_called = False

        class MockSnip:
            async def compact(self, messages, token_limit=0):
                nonlocal snip_called
                snip_called = True
                return [Message(role="user", content="snipped")]

            def estimate_tokens(self, messages):
                return sum(len(str(m)) for m in messages) // 4

        from duh.adapters.compact import adaptive as adaptive_mod
        old_gates = adaptive_mod._THRESHOLD_GATES.copy()
        adaptive_mod._THRESHOLD_GATES["MockSnip"] = 0.75

        try:
            ac = AdaptiveCompactor(
                strategies=[MockSnip()],
                output_buffer=0,
            )
            # Messages use ~200% of limit → snip should fire
            messages = [Message(role="user", content="x" * 40000)]
            await ac.compact(messages, token_limit=5000)

            assert snip_called
        finally:
            adaptive_mod._THRESHOLD_GATES = old_gates
