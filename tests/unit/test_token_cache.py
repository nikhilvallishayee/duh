"""Tests for incremental token cache in Engine (PERF-1).

Verifies that the Engine's _msg_token_cache avoids O(N*M) re-scanning:
1. Running total matches full-scan total
2. After appending N messages, total is correct
3. After compaction, cache invalidated and total recalculated correctly
4. Per-message cache survives across turns
5. Model change triggers cache rebuild
"""

from __future__ import annotations

from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message
from duh.kernel.tokens import count_tokens_for_model


# ═══════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════

MODEL = "claude-sonnet-4-6"


def _make_deps():
    """Create a minimal Deps for Engine construction."""
    from duh.kernel.deps import Deps
    return Deps(call_model=None, compact=None)


def _make_engine(**kwargs) -> Engine:
    """Create an Engine with minimal deps."""
    config = EngineConfig(model=MODEL, **kwargs)
    return Engine(deps=_make_deps(), config=config)


def _full_scan_tokens(messages: list[Message], model: str) -> int:
    """Reference implementation: scan every message to get total tokens."""
    total = 0
    for m in messages:
        text = m.text if isinstance(m, Message) else str(m)
        total += count_tokens_for_model(text, model)
    return total


# ═══════════════════════════════════════════════════════════════════
# Running total matches full-scan total
# ═══════════════════════════════════════════════════════════════════


class TestRunningTotalMatchesFullScan:
    """_cached_token_total must equal a from-scratch full scan."""

    def test_empty(self):
        engine = _make_engine()
        engine._rebuild_token_cache(MODEL)
        assert engine._cached_token_total == 0
        assert engine._estimate_messages_tokens(MODEL) == 0

    def test_single_message(self):
        engine = _make_engine()
        msg = Message(role="user", content="Hello, world!")
        engine._messages.append(msg)
        engine._rebuild_token_cache(MODEL)
        expected = _full_scan_tokens(engine._messages, MODEL)
        assert engine._cached_token_total == expected

    def test_multiple_messages(self):
        engine = _make_engine()
        texts = [
            "Short message",
            "A longer message with more content to tokenize accurately",
            "Third message",
            "Fourth message with even more text " * 20,
        ]
        for i, t in enumerate(texts):
            role = "user" if i % 2 == 0 else "assistant"
            engine._messages.append(Message(role=role, content=t))
        engine._rebuild_token_cache(MODEL)
        expected = _full_scan_tokens(engine._messages, MODEL)
        assert engine._cached_token_total == expected


# ═══════════════════════════════════════════════════════════════════
# Incremental tracking after appending N messages
# ═══════════════════════════════════════════════════════════════════


class TestIncrementalTracking:
    """_track_new_message maintains an accurate running total."""

    def test_append_one(self):
        engine = _make_engine()
        engine._rebuild_token_cache(MODEL)
        msg = Message(role="user", content="Hello, world!")
        engine._messages.append(msg)
        engine._track_new_message(msg, MODEL)
        expected = _full_scan_tokens(engine._messages, MODEL)
        assert engine._cached_token_total == expected

    def test_append_many(self):
        engine = _make_engine()
        engine._rebuild_token_cache(MODEL)
        for i in range(50):
            role = "user" if i % 2 == 0 else "assistant"
            msg = Message(role=role, content=f"Message number {i} with some extra text.")
            engine._messages.append(msg)
            engine._track_new_message(msg, MODEL)
        expected = _full_scan_tokens(engine._messages, MODEL)
        assert engine._cached_token_total == expected

    def test_estimate_uses_cache_not_full_scan(self):
        """_estimate_messages_tokens should return the cached total."""
        engine = _make_engine()
        engine._rebuild_token_cache(MODEL)
        for i in range(10):
            msg = Message(role="user", content=f"Message {i}")
            engine._messages.append(msg)
            engine._track_new_message(msg, MODEL)
        # The estimate should match the full scan
        full_scan = _full_scan_tokens(engine._messages, MODEL)
        cached = engine._estimate_messages_tokens(MODEL)
        assert cached == full_scan
        # And the cache should be populated for all messages
        assert len(engine._msg_token_cache) == 10

    def test_per_message_cache_correct(self):
        """Individual message token counts should be correct."""
        engine = _make_engine()
        engine._rebuild_token_cache(MODEL)
        msgs = []
        for i in range(5):
            msg = Message(role="user", content=f"Test message number {i}")
            engine._messages.append(msg)
            engine._track_new_message(msg, MODEL)
            msgs.append(msg)
        for msg in msgs:
            expected = count_tokens_for_model(msg.text, MODEL)
            assert engine._msg_token_cache[msg.id] == expected


# ═══════════════════════════════════════════════════════════════════
# Cache invalidation after compaction
# ═══════════════════════════════════════════════════════════════════


class TestCompactionInvalidation:
    """_invalidate_token_cache resets state; next estimate rebuilds correctly."""

    def test_invalidate_clears_cache(self):
        engine = _make_engine()
        for i in range(5):
            msg = Message(role="user", content=f"Message {i}")
            engine._messages.append(msg)
        engine._rebuild_token_cache(MODEL)
        assert engine._cached_token_total > 0
        assert len(engine._msg_token_cache) == 5

        engine._invalidate_token_cache()
        assert engine._cached_token_total == 0
        assert len(engine._msg_token_cache) == 0
        assert engine._cache_model == ""

    def test_estimate_after_invalidation_rebuilds(self):
        engine = _make_engine()
        for i in range(10):
            msg = Message(role="user", content=f"Message {i} " * 10)
            engine._messages.append(msg)
        engine._rebuild_token_cache(MODEL)
        original_total = engine._cached_token_total

        # Simulate compaction: replace messages with a smaller set
        engine._messages = engine._messages[:3]
        engine._invalidate_token_cache()

        # Next estimate should rebuild and return correct total
        new_total = engine._estimate_messages_tokens(MODEL)
        expected = _full_scan_tokens(engine._messages, MODEL)
        assert new_total == expected
        assert new_total < original_total
        # Cache should be rebuilt
        assert len(engine._msg_token_cache) == 3
        assert engine._cache_model == MODEL

    def test_compaction_then_append(self):
        """After compaction + invalidation, new messages tracked correctly."""
        engine = _make_engine()
        for i in range(10):
            msg = Message(role="user", content=f"Old message {i}")
            engine._messages.append(msg)
        engine._rebuild_token_cache(MODEL)

        # Simulate compaction: keep first 2 messages
        engine._messages = engine._messages[:2]
        engine._invalidate_token_cache()

        # Rebuild via estimate
        engine._estimate_messages_tokens(MODEL)

        # Now append new messages
        for i in range(5):
            msg = Message(role="user", content=f"New message {i} with extra content")
            engine._messages.append(msg)
            engine._track_new_message(msg, MODEL)

        expected = _full_scan_tokens(engine._messages, MODEL)
        assert engine._cached_token_total == expected
        assert len(engine._msg_token_cache) == 7  # 2 old + 5 new

    def test_compaction_with_new_summary_message(self):
        """After compaction, a summary message replaces old messages."""
        engine = _make_engine()
        for i in range(20):
            msg = Message(role="user", content=f"Long message {i} " * 50)
            engine._messages.append(msg)
        engine._rebuild_token_cache(MODEL)
        old_total = engine._cached_token_total

        # Simulate compaction: replace all with a summary
        summary = Message(
            role="system",
            content="Summary of the previous 20 messages: the user discussed various topics.",
        )
        engine._messages = [summary]
        engine._invalidate_token_cache()

        new_total = engine._estimate_messages_tokens(MODEL)
        expected = _full_scan_tokens(engine._messages, MODEL)
        assert new_total == expected
        assert new_total < old_total


# ═══════════════════════════════════════════════════════════════════
# Per-message cache survives across turns
# ═══════════════════════════════════════════════════════════════════


class TestCachePersistsAcrossTurns:
    """Cache entries for earlier messages remain valid on later turns."""

    def test_cache_entries_persist(self):
        engine = _make_engine()
        engine._rebuild_token_cache(MODEL)

        # Simulate turn 1: append user + assistant
        user1 = Message(role="user", content="First user message")
        engine._messages.append(user1)
        engine._track_new_message(user1, MODEL)
        asst1 = Message(role="assistant", content="First assistant response")
        engine._messages.append(asst1)
        engine._track_new_message(asst1, MODEL)

        # Record cache state after turn 1
        user1_tokens = engine._msg_token_cache[user1.id]
        asst1_tokens = engine._msg_token_cache[asst1.id]
        total_after_turn1 = engine._cached_token_total

        # Simulate turn 2: append more messages
        user2 = Message(role="user", content="Second user message, slightly longer")
        engine._messages.append(user2)
        engine._track_new_message(user2, MODEL)
        asst2 = Message(role="assistant", content="Second assistant response, also longer")
        engine._messages.append(asst2)
        engine._track_new_message(asst2, MODEL)

        # Turn 1 cache entries should be unchanged
        assert engine._msg_token_cache[user1.id] == user1_tokens
        assert engine._msg_token_cache[asst1.id] == asst1_tokens

        # Total should have grown
        assert engine._cached_token_total > total_after_turn1

        # And total should still match full scan
        expected = _full_scan_tokens(engine._messages, MODEL)
        assert engine._cached_token_total == expected

    def test_many_turns_accumulate(self):
        engine = _make_engine()
        engine._rebuild_token_cache(MODEL)
        all_ids = []

        for turn in range(20):
            user_msg = Message(role="user", content=f"Turn {turn} user message")
            engine._messages.append(user_msg)
            engine._track_new_message(user_msg, MODEL)
            all_ids.append(user_msg.id)

            asst_msg = Message(role="assistant", content=f"Turn {turn} response")
            engine._messages.append(asst_msg)
            engine._track_new_message(asst_msg, MODEL)
            all_ids.append(asst_msg.id)

        # All message ids should be in cache
        for mid in all_ids:
            assert mid in engine._msg_token_cache

        # Total should match
        expected = _full_scan_tokens(engine._messages, MODEL)
        assert engine._cached_token_total == expected


# ═══════════════════════════════════════════════════════════════════
# Model change triggers cache rebuild
# ═══════════════════════════════════════════════════════════════════


class TestModelChange:
    """Changing the model invalidates the cache and rebuilds."""

    def test_different_model_triggers_rebuild(self):
        engine = _make_engine()
        for i in range(5):
            engine._messages.append(Message(role="user", content=f"Message {i}"))
        engine._rebuild_token_cache(MODEL)
        original_total = engine._cached_token_total

        # Estimate with a different model
        other_model = "gpt-4o"
        new_total = engine._estimate_messages_tokens(other_model)
        expected = _full_scan_tokens(engine._messages, other_model)
        assert new_total == expected
        assert engine._cache_model == other_model
        # Totals may differ because different models have different char/token ratios
        # (anthropic=3.5, openai=4.0)

    def test_track_new_message_with_model_change(self):
        engine = _make_engine()
        engine._rebuild_token_cache(MODEL)
        msg = Message(role="user", content="Hello")
        engine._messages.append(msg)

        # Track with a different model — should trigger rebuild
        other_model = "gpt-4o"
        engine._track_new_message(msg, other_model)
        assert engine._cache_model == other_model
        expected = _full_scan_tokens(engine._messages, other_model)
        assert engine._cached_token_total == expected


# ═══════════════════════════════════════════════════════════════════
# _token_count_for_message
# ═══════════════════════════════════════════════════════════════════


class TestTokenCountForMessage:
    """_token_count_for_message returns correct counts and uses cache."""

    def test_returns_correct_count(self):
        engine = _make_engine()
        engine._rebuild_token_cache(MODEL)
        msg = Message(role="user", content="Hello, this is a test message.")
        expected = count_tokens_for_model(msg.text, MODEL)
        result = engine._token_count_for_message(msg, MODEL)
        assert result == expected

    def test_caches_result(self):
        engine = _make_engine()
        engine._rebuild_token_cache(MODEL)
        msg = Message(role="user", content="Cache me please")
        engine._token_count_for_message(msg, MODEL)
        assert msg.id in engine._msg_token_cache

    def test_uses_cache_on_second_call(self):
        engine = _make_engine()
        engine._rebuild_token_cache(MODEL)
        msg = Message(role="user", content="Cached message")
        first = engine._token_count_for_message(msg, MODEL)
        # Manually verify the cache entry exists
        assert msg.id in engine._msg_token_cache
        # Second call should return the same value from cache
        second = engine._token_count_for_message(msg, MODEL)
        assert first == second
