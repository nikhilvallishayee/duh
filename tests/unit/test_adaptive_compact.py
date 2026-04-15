"""Tests for the adaptive compaction system (ADR-056).

Tests cover:
- Microcompact: clearing old tool results
- Dedup: removing duplicate reads
- Summarize: producing shorter context
- AdaptiveCompactor: running tiers in order with early exit
- Circuit breaker: stopping after consecutive failures
- Backward compat: old compact interface still works
- Protocol conformance: all compactors satisfy CompactionStrategy
"""

from __future__ import annotations

import asyncio
import pytest
from typing import Any
from datetime import datetime, timezone, timedelta

from duh.kernel.messages import (
    Message,
    TextBlock,
    ToolUseBlock,
    ToolResultBlock,
)
from duh.adapters.compact import (
    AdaptiveCompactor,
    CompactionResult,
    CompactionStrategy,
)
from duh.adapters.compact.microcompact import MicroCompactor, _CLEARED_PLACEHOLDER
from duh.adapters.compact.dedup import DedupCompactor
from duh.adapters.compact.summarize import SummarizeCompactor
from duh.adapters.simple_compactor import SimpleCompactor
from duh.adapters.model_compactor import ModelCompactor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_pair(tool_name: str, tool_input: dict, result_content: str, tool_id: str = "tu_1") -> tuple[Message, Message]:
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


def _make_messages_with_tool_results(count: int) -> list[Message]:
    """Create a conversation with N Read tool result pairs."""
    messages: list[Message] = []
    for i in range(count):
        assistant, user = _make_tool_pair(
            "Read",
            {"file_path": f"/path/file_{i}.py"},
            f"content of file_{i} " * 100,  # ~2000 chars each
            tool_id=f"tu_{i}",
        )
        messages.append(assistant)
        messages.append(user)
    return messages


def _total_content_length(messages: list[Any]) -> int:
    """Rough measure of total content size in chars."""
    total = 0
    for msg in messages:
        if isinstance(msg, Message):
            if isinstance(msg.content, str):
                total += len(msg.content)
            else:
                for block in msg.content:
                    if isinstance(block, dict):
                        total += len(str(block.get("content", "")))
                    elif isinstance(block, ToolResultBlock):
                        total += len(str(block.content))
                    elif isinstance(block, TextBlock):
                        total += len(block.text)
        elif isinstance(msg, dict):
            total += len(str(msg.get("content", "")))
    return total


# ===========================================================================
# Microcompact Tests
# ===========================================================================

class TestMicroCompactor:
    """Test Tier 0: clearing old tool results."""

    @pytest.mark.asyncio
    async def test_clears_old_tool_results(self):
        """Old Read results beyond keep_last should be replaced with placeholder."""
        mc = MicroCompactor(keep_last=2)
        messages = _make_messages_with_tool_results(5)

        result = await mc.compact(messages)

        # The last 2 tool results should be kept, first 3 cleared
        cleared_count = 0
        for msg in result:
            if isinstance(msg, Message) and msg.role == "user":
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            if block.get("content") == _CLEARED_PLACEHOLDER:
                                cleared_count += 1

        assert cleared_count == 3, f"Expected 3 cleared, got {cleared_count}"

    @pytest.mark.asyncio
    async def test_keeps_last_n_results(self):
        """The last N tool results should remain intact."""
        mc = MicroCompactor(keep_last=3)
        messages = _make_messages_with_tool_results(5)

        result = await mc.compact(messages)

        # Count intact results
        intact_count = 0
        for msg in result:
            if isinstance(msg, Message) and msg.role == "user":
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            if block.get("content") != _CLEARED_PLACEHOLDER:
                                intact_count += 1

        assert intact_count == 3

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        """Empty list should return empty list."""
        mc = MicroCompactor()
        result = await mc.compact([])
        assert result == []

    @pytest.mark.asyncio
    async def test_no_tool_results(self):
        """Messages without tool results should pass through unchanged."""
        mc = MicroCompactor()
        messages = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        ]
        result = await mc.compact(messages)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_preserves_system_messages(self):
        """System messages should never be modified."""
        mc = MicroCompactor(keep_last=0)
        messages = [
            Message(role="system", content="System prompt"),
        ] + _make_messages_with_tool_results(2)

        result = await mc.compact(messages)

        system_msgs = [m for m in result if isinstance(m, Message) and m.role == "system"]
        assert len(system_msgs) == 1
        assert system_msgs[0].content == "System prompt"

    @pytest.mark.asyncio
    async def test_non_clearable_tools_preserved(self):
        """Tool results from non-clearable tools should be kept."""
        mc = MicroCompactor(keep_last=0)
        assistant = Message(
            role="assistant",
            content=[ToolUseBlock(id="tu_custom", name="CustomTool", input={"x": 1})],
        )
        user = Message(
            role="user",
            content=[{
                "type": "tool_result",
                "tool_use_id": "tu_custom",
                "content": "custom result data",
            }],
        )
        result = await mc.compact([assistant, user])

        # CustomTool result should be intact
        for msg in result:
            if isinstance(msg, Message) and msg.role == "user":
                if isinstance(msg.content, list):
                    for block in msg.content:
                        if isinstance(block, dict) and block.get("type") == "tool_result":
                            assert block.get("content") == "custom result data"

    @pytest.mark.asyncio
    async def test_reduces_token_count(self):
        """Microcompact should reduce the estimated token count."""
        mc = MicroCompactor(keep_last=1)
        messages = _make_messages_with_tool_results(10)

        tokens_before = mc.estimate_tokens(messages)
        result = await mc.compact(messages)
        tokens_after = mc.estimate_tokens(result)

        assert tokens_after < tokens_before

    @pytest.mark.asyncio
    async def test_estimate_tokens(self):
        """estimate_tokens should return a positive number for non-empty messages."""
        mc = MicroCompactor()
        messages = [Message(role="user", content="Hello world")]
        assert mc.estimate_tokens(messages) > 0
        assert mc.estimate_tokens([]) == 0


# ===========================================================================
# Dedup Tests
# ===========================================================================

class TestDedupCompactor:
    """Test Tier 1: deduplication and image stripping."""

    @pytest.mark.asyncio
    async def test_removes_duplicate_reads(self):
        """Reading the same file twice should keep only the latest."""
        dc = DedupCompactor()

        # Two reads of the same file
        a1, u1 = _make_tool_pair("Read", {"file_path": "/a.py"}, "old content", "tu_1")
        a2, u2 = _make_tool_pair("Read", {"file_path": "/a.py"}, "new content", "tu_2")

        messages = [a1, u1, a2, u2]
        result = await dc.compact(messages)

        # The dedup should have removed the first read
        assert len(result) < len(messages)

    @pytest.mark.asyncio
    async def test_strips_images_from_old_messages(self):
        """Image blocks in old messages should be replaced with placeholders."""
        dc = DedupCompactor(keep_recent_images=1)

        from duh.kernel.messages import ImageBlock
        messages = [
            Message(role="user", content=[
                ImageBlock(media_type="image/png", data="base64data"),
                TextBlock(text="Look at this"),
            ]),
            Message(role="assistant", content="I see the image"),
            Message(role="user", content="What about it?"),  # recent
        ]

        result = await dc.compact(messages)

        # The old image should be replaced with placeholder
        first_msg = result[0]
        assert isinstance(first_msg, Message)
        found_image = False
        found_placeholder = False
        for block in first_msg.content:
            if isinstance(block, TextBlock) and block.text == "[image removed for compaction]":
                found_placeholder = True
            if hasattr(block, "type") and block.type == "image":
                found_image = True

        assert found_placeholder
        assert not found_image

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        dc = DedupCompactor()
        assert await dc.compact([]) == []

    @pytest.mark.asyncio
    async def test_estimate_tokens(self):
        dc = DedupCompactor()
        messages = [Message(role="user", content="Hello world")]
        assert dc.estimate_tokens(messages) > 0


# ===========================================================================
# Summarize Tests
# ===========================================================================

class TestSummarizeCompactor:
    """Test Tier 2: tail-window + summarization."""

    @pytest.mark.asyncio
    async def test_produces_shorter_context(self):
        """Summarization should reduce message count for large conversations."""
        sc = SummarizeCompactor(bytes_per_token=4)

        # Create a large conversation
        messages = []
        for i in range(20):
            messages.append(Message(role="user", content=f"Message {i} " * 50))
            messages.append(Message(role="assistant", content=f"Response {i} " * 50))

        tokens_before = sc.estimate_tokens(messages)
        # Use a tight limit to force summarization
        result = await sc.compact(messages, token_limit=tokens_before // 3)

        assert len(result) < len(messages)

    @pytest.mark.asyncio
    async def test_preserves_system_messages(self):
        """System messages should always be preserved."""
        sc = SummarizeCompactor()
        messages = [
            Message(role="system", content="You are helpful"),
            Message(role="user", content="Hello " * 1000),
            Message(role="assistant", content="Hi " * 1000),
            Message(role="user", content="More " * 1000),
        ]

        result = await sc.compact(messages, token_limit=500)

        system_msgs = [m for m in result if isinstance(m, Message) and m.role == "system"]
        assert any("You are helpful" in m.content for m in system_msgs if isinstance(m.content, str))

    @pytest.mark.asyncio
    async def test_summary_message_when_messages_dropped(self):
        """When messages are dropped, a summary message should be inserted."""
        sc = SummarizeCompactor(bytes_per_token=4)

        messages = []
        for i in range(10):
            messages.append(Message(role="user", content=f"Long message {i} " * 200))
            messages.append(Message(role="assistant", content=f"Response {i} " * 200))

        tokens_before = sc.estimate_tokens(messages)
        result = await sc.compact(messages, token_limit=tokens_before // 4)

        # Should have a summary system message
        has_summary = any(
            isinstance(m, Message) and m.role == "system"
            and "Previous conversation summary" in (m.content if isinstance(m.content, str) else "")
            for m in result
        )
        assert has_summary

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        sc = SummarizeCompactor()
        assert await sc.compact([]) == []

    @pytest.mark.asyncio
    async def test_post_restoration(self):
        """When file_tracker is provided, post-restoration should add context."""

        class FakeTracker:
            ops = [type("Op", (), {"path": "/a.py"})(), type("Op", (), {"path": "/b.py"})()]

        sc = SummarizeCompactor(file_tracker=FakeTracker())

        messages = []
        for i in range(10):
            messages.append(Message(role="user", content=f"Msg {i} " * 200))
            messages.append(Message(role="assistant", content=f"Rsp {i} " * 200))

        tokens = sc.estimate_tokens(messages)
        result = await sc.compact(messages, token_limit=tokens // 4)

        # Should have a restoration message
        has_restoration = any(
            isinstance(m, Message) and m.role == "system"
            and "Post-compaction context restoration" in (m.content if isinstance(m.content, str) else "")
            for m in result
        )
        assert has_restoration


# ===========================================================================
# AdaptiveCompactor Tests
# ===========================================================================

class TestAdaptiveCompactor:
    """Test the orchestrator."""

    @pytest.mark.asyncio
    async def test_runs_tiers_in_order_with_early_exit(self):
        """If first tier brings context under budget, skip remaining tiers."""
        tier_calls: list[str] = []

        class MockTier1:
            async def compact(self, messages, token_limit=0):
                tier_calls.append("tier1")
                # Return much shorter messages
                return [Message(role="user", content="short")]

            def estimate_tokens(self, messages):
                return sum(len(str(m)) for m in messages) // 4

        class MockTier2:
            async def compact(self, messages, token_limit=0):
                tier_calls.append("tier2")
                return messages

            def estimate_tokens(self, messages):
                return sum(len(str(m)) for m in messages) // 4

        ac = AdaptiveCompactor(
            strategies=[MockTier1(), MockTier2()],
            output_buffer=0,
        )

        # 40K chars / 4 = 10K tokens, limit 5K => must compact
        big_messages = [Message(role="user", content="x" * 40000)]
        result = await ac.compact(big_messages, token_limit=5000)

        assert "tier1" in tier_calls
        assert "tier2" not in tier_calls
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_runs_all_tiers_when_needed(self):
        """If first tier is insufficient, run the next."""
        tier_calls: list[str] = []

        class MockTier1:
            async def compact(self, messages, token_limit=0):
                tier_calls.append("tier1")
                return messages  # no reduction

            def estimate_tokens(self, messages):
                return sum(len(str(m)) for m in messages) // 4

        class MockTier2:
            async def compact(self, messages, token_limit=0):
                tier_calls.append("tier2")
                return [Message(role="user", content="short")]

            def estimate_tokens(self, messages):
                return sum(len(str(m)) for m in messages) // 4

        ac = AdaptiveCompactor(
            strategies=[MockTier1(), MockTier2()],
            output_buffer=0,
        )

        # 40K chars / 4 = 10K tokens, limit 5K => must compact
        big_messages = [Message(role="user", content="x" * 40000)]
        result = await ac.compact(big_messages, token_limit=5000)

        assert tier_calls == ["tier1", "tier2"]

    @pytest.mark.asyncio
    async def test_already_under_budget_skips_all_tiers(self):
        """Messages already under budget should not trigger any compaction."""
        tier_calls: list[str] = []

        class MockTier:
            async def compact(self, messages, token_limit=0):
                tier_calls.append("called")
                return messages

            def estimate_tokens(self, messages):
                return 0

        ac = AdaptiveCompactor(
            strategies=[MockTier()],
            output_buffer=0,
        )

        small_messages = [Message(role="user", content="hi")]
        result = await ac.compact(small_messages, token_limit=100_000)

        assert tier_calls == []
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_output_buffer_subtracted(self):
        """The output buffer should be subtracted from the token limit."""
        ac = AdaptiveCompactor(strategies=[], output_buffer=20_000)

        # If limit is 50K and buffer is 20K, effective limit is 30K
        # Messages estimating to 25K should NOT trigger compaction
        messages = [Message(role="user", content="x" * 100_000)]  # ~25K tokens at 4 bytes/token
        result = await ac.compact(messages, token_limit=50_000)
        # With 100K chars / 4 = 25K tokens, effective limit = 30K, so no compaction
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_empty_messages(self):
        ac = AdaptiveCompactor(strategies=[])
        assert await ac.compact([]) == []


# ===========================================================================
# Circuit Breaker Tests
# ===========================================================================

class TestCircuitBreaker:
    """Test the circuit breaker in AdaptiveCompactor."""

    @pytest.mark.asyncio
    async def test_stops_after_3_consecutive_failures(self):
        """After 3 failures, the circuit breaker should stop trying."""
        call_count = 0

        class FailingTier:
            async def compact(self, messages, token_limit=0):
                nonlocal call_count
                call_count += 1
                raise RuntimeError("compaction failed")

            def estimate_tokens(self, messages):
                return 999_999  # always over budget

        ac = AdaptiveCompactor(
            strategies=[FailingTier(), FailingTier(), FailingTier(), FailingTier(), FailingTier()],
            output_buffer=0,
        )

        messages = [Message(role="user", content="x" * 10000)]
        result = await ac.compact(messages, token_limit=100)

        # Should have stopped after 3 failures
        assert call_count == 3
        assert ac.consecutive_failures == 3

    @pytest.mark.asyncio
    async def test_resets_on_success(self):
        """A successful tier should reset the failure counter."""

        class FailingTier:
            async def compact(self, messages, token_limit=0):
                raise RuntimeError("fail")

            def estimate_tokens(self, messages):
                return 999_999

        class SucceedingTier:
            async def compact(self, messages, token_limit=0):
                return messages

            def estimate_tokens(self, messages):
                return 999_999

        ac = AdaptiveCompactor(
            strategies=[FailingTier(), SucceedingTier()],
            output_buffer=0,
        )

        messages = [Message(role="user", content="x" * 10000)]
        await ac.compact(messages, token_limit=100)

        # Should be 0 after the success
        assert ac.consecutive_failures == 0

    @pytest.mark.asyncio
    async def test_reset_circuit_breaker(self):
        """Manual reset should clear the failure counter."""
        ac = AdaptiveCompactor(strategies=[], output_buffer=0)
        ac._consecutive_failures = 5
        ac.reset_circuit_breaker()
        assert ac.consecutive_failures == 0


# ===========================================================================
# Backward Compatibility Tests
# ===========================================================================

class TestBackwardCompat:
    """Test that old compact interface still works."""

    @pytest.mark.asyncio
    async def test_simple_compactor_as_strategy(self):
        """SimpleCompactor should satisfy CompactionStrategy protocol."""
        sc = SimpleCompactor(default_limit=1000)
        assert isinstance(sc, CompactionStrategy)

        messages = [Message(role="user", content="hello " * 100)]
        result = await sc.compact(messages, token_limit=1000)
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_model_compactor_as_strategy(self):
        """ModelCompactor should satisfy CompactionStrategy protocol."""
        mc = ModelCompactor(default_limit=1000)
        assert isinstance(mc, CompactionStrategy)

        messages = [Message(role="user", content="hello " * 100)]
        result = await mc.compact(messages, token_limit=1000)
        assert len(result) >= 1

    @pytest.mark.asyncio
    async def test_adaptive_compactor_as_strategy(self):
        """AdaptiveCompactor itself should satisfy CompactionStrategy."""
        ac = AdaptiveCompactor()
        assert isinstance(ac, CompactionStrategy)

    @pytest.mark.asyncio
    async def test_microcompact_as_strategy(self):
        """MicroCompactor should satisfy CompactionStrategy."""
        mc = MicroCompactor()
        assert isinstance(mc, CompactionStrategy)

    @pytest.mark.asyncio
    async def test_dedup_as_strategy(self):
        """DedupCompactor should satisfy CompactionStrategy."""
        dc = DedupCompactor()
        assert isinstance(dc, CompactionStrategy)

    @pytest.mark.asyncio
    async def test_summarize_as_strategy(self):
        """SummarizeCompactor should satisfy CompactionStrategy."""
        sc = SummarizeCompactor()
        assert isinstance(sc, CompactionStrategy)


# ===========================================================================
# Default Pipeline Integration Tests
# ===========================================================================

class TestDefaultPipeline:
    """Test the default 3-tier pipeline end-to-end."""

    @pytest.mark.asyncio
    async def test_default_pipeline_reduces_tokens(self):
        """The default pipeline should reduce tokens for a large conversation."""
        ac = AdaptiveCompactor(output_buffer=0)

        # Build a large conversation with tool results
        messages = _make_messages_with_tool_results(15)

        tokens_before = ac.estimate_tokens(messages)
        # Set limit well below current size to force compaction
        result = await ac.compact(messages, token_limit=tokens_before // 3)
        tokens_after = ac.estimate_tokens(result)

        assert tokens_after < tokens_before

    @pytest.mark.asyncio
    async def test_default_pipeline_preserves_some_messages(self):
        """The pipeline should always preserve at least min_keep messages."""
        ac = AdaptiveCompactor(output_buffer=0)

        messages = _make_messages_with_tool_results(10)
        result = await ac.compact(messages, token_limit=100)

        # Should have at least some messages
        assert len(result) >= 1


# ===========================================================================
# Engine Integration Tests
# ===========================================================================

class TestEngineIntegration:
    """Test that the engine correctly uses AdaptiveCompactor."""

    def test_engine_creates_adaptive_compactor_lazily(self):
        """Engine should create AdaptiveCompactor when deps.compact is None."""
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.deps import Deps

        deps = Deps()  # compact is None
        engine = Engine(deps=deps, config=EngineConfig())

        # Access the lazy property
        ac = engine._get_adaptive_compactor()
        assert isinstance(ac, AdaptiveCompactor)

    def test_engine_caches_adaptive_compactor(self):
        """The same AdaptiveCompactor should be returned on subsequent calls."""
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.deps import Deps

        deps = Deps()
        engine = Engine(deps=deps, config=EngineConfig())

        ac1 = engine._get_adaptive_compactor()
        ac2 = engine._get_adaptive_compactor()
        assert ac1 is ac2

    def test_ptl_triggers_expanded(self):
        """Check that new PTL triggers are recognized."""
        from duh.kernel.engine import _is_ptl_error

        assert _is_ptl_error("request entity too large")
        assert _is_ptl_error("input is too long for this model")
        # Existing ones still work
        assert _is_ptl_error("prompt is too long")
        assert _is_ptl_error("context length exceeded")
        assert _is_ptl_error("content too large")
