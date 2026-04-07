"""Tests for duh.kernel.tokens — token estimation and cost tracking."""

import pytest

from duh.kernel.tokens import (
    count_tokens,
    estimate_cost,
    format_cost,
    get_context_limit,
    _resolve_pricing,
    _CHARS_PER_TOKEN,
    _DEFAULT_CONTEXT_LIMIT,
    _MODEL_PRICING,
    MODEL_CONTEXT_LIMITS,
)


# ---------------------------------------------------------------------------
# count_tokens
# ---------------------------------------------------------------------------

class TestCountTokens:
    def test_empty_string(self):
        assert count_tokens("") == 0

    def test_short_string(self):
        # "hi" = 2 chars, 2 // 4 = 0, but min is 1
        assert count_tokens("hi") == 1

    def test_exact_boundary(self):
        # 4 chars = exactly 1 token
        assert count_tokens("abcd") == 1

    def test_typical_sentence(self):
        text = "Hello, world! How are you doing today?"
        expected = len(text) // _CHARS_PER_TOKEN
        assert count_tokens(text) == expected

    def test_long_text(self):
        text = "a" * 4000
        assert count_tokens(text) == 1000

    def test_unicode_text(self):
        # Unicode chars may be multiple bytes, but we count characters
        text = "こんにちは世界"  # 7 chars
        assert count_tokens(text) == 7 // _CHARS_PER_TOKEN

    def test_returns_int(self):
        assert isinstance(count_tokens("hello"), int)

    def test_minimum_one_for_nonempty(self):
        # Any non-empty string should return at least 1
        assert count_tokens("a") == 1
        assert count_tokens("ab") == 1
        assert count_tokens("abc") == 1


# ---------------------------------------------------------------------------
# _resolve_pricing
# ---------------------------------------------------------------------------

class TestResolvePricing:
    def test_exact_match_sonnet(self):
        inp, out = _resolve_pricing("claude-sonnet-4-6")
        assert inp == 3.0
        assert out == 15.0

    def test_exact_match_opus(self):
        inp, out = _resolve_pricing("claude-opus-4-6")
        assert inp == 15.0
        assert out == 75.0

    def test_exact_match_haiku(self):
        inp, out = _resolve_pricing("claude-haiku-3-5")
        assert inp == 0.25
        assert out == 1.25

    def test_exact_match_gpt4o(self):
        inp, out = _resolve_pricing("gpt-4o")
        assert inp == 2.50
        assert out == 10.0

    def test_pattern_match_sonnet_variant(self):
        inp, out = _resolve_pricing("claude-sonnet-99")
        assert inp == 3.0
        assert out == 15.0

    def test_pattern_match_opus_variant(self):
        inp, out = _resolve_pricing("my-opus-model")
        assert inp == 15.0
        assert out == 75.0

    def test_pattern_match_haiku_variant(self):
        inp, out = _resolve_pricing("claude-haiku-future")
        assert inp == 0.25
        assert out == 1.25

    def test_local_ollama(self):
        inp, out = _resolve_pricing("ollama/llama3")
        assert inp == 0.0
        assert out == 0.0

    def test_local_qwen(self):
        inp, out = _resolve_pricing("qwen2.5-coder:1.5b")
        assert inp == 0.0
        assert out == 0.0

    def test_local_llama(self):
        inp, out = _resolve_pricing("llama3.1:8b")
        assert inp == 0.0
        assert out == 0.0

    def test_unknown_defaults_to_sonnet(self):
        inp, out = _resolve_pricing("totally-unknown-model-xyz")
        assert inp == 3.0
        assert out == 15.0

    def test_gpt4o_mini(self):
        inp, out = _resolve_pricing("gpt-4o-mini")
        assert inp == 0.15
        assert out == 0.60


# ---------------------------------------------------------------------------
# estimate_cost
# ---------------------------------------------------------------------------

class TestEstimateCost:
    def test_zero_tokens(self):
        cost = estimate_cost("claude-sonnet-4-6", 0, 0)
        assert cost == 0.0

    def test_sonnet_cost(self):
        # 1M input + 1M output for Sonnet: $3 + $15 = $18
        cost = estimate_cost("claude-sonnet-4-6", 1_000_000, 1_000_000)
        assert cost == pytest.approx(18.0)

    def test_opus_cost(self):
        # 1M input + 1M output for Opus: $15 + $75 = $90
        cost = estimate_cost("claude-opus-4-6", 1_000_000, 1_000_000)
        assert cost == pytest.approx(90.0)

    def test_haiku_cost(self):
        # 1M input + 1M output for Haiku: $0.25 + $1.25 = $1.50
        cost = estimate_cost("claude-haiku-3-5", 1_000_000, 1_000_000)
        assert cost == pytest.approx(1.50)

    def test_gpt4o_cost(self):
        # 1M input + 1M output for GPT-4o: $2.50 + $10 = $12.50
        cost = estimate_cost("gpt-4o", 1_000_000, 1_000_000)
        assert cost == pytest.approx(12.50)

    def test_ollama_free(self):
        cost = estimate_cost("qwen2.5-coder:1.5b", 1_000_000, 1_000_000)
        assert cost == 0.0

    def test_small_conversation(self):
        # Typical short conversation: ~500 input, ~200 output on Sonnet
        cost = estimate_cost("claude-sonnet-4-6", 500, 200)
        expected = (500 * 3.0 + 200 * 15.0) / 1_000_000
        assert cost == pytest.approx(expected)

    def test_input_only(self):
        cost = estimate_cost("claude-sonnet-4-6", 1000, 0)
        assert cost == pytest.approx(1000 * 3.0 / 1_000_000)

    def test_output_only(self):
        cost = estimate_cost("claude-sonnet-4-6", 0, 1000)
        assert cost == pytest.approx(1000 * 15.0 / 1_000_000)


# ---------------------------------------------------------------------------
# format_cost
# ---------------------------------------------------------------------------

class TestFormatCost:
    def test_zero(self):
        assert format_cost(0.0) == "$0.0000"

    def test_small_cost(self):
        result = format_cost(0.005)
        assert result == "$0.0050"

    def test_larger_cost(self):
        result = format_cost(1.50)
        assert result == "$1.50"

    def test_boundary(self):
        # Exactly $0.01 should use 2 decimals
        assert format_cost(0.01) == "$0.01"

    def test_just_under_boundary(self):
        assert format_cost(0.009) == "$0.0090"


# ---------------------------------------------------------------------------
# Integration: Engine token tracking
# ---------------------------------------------------------------------------

class TestEngineTokenTracking:
    async def test_engine_tracks_tokens(self):
        from typing import AsyncGenerator
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        async def fake_model(**kwargs) -> AsyncGenerator[dict, None]:
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content="Hello there, how can I help you today?",
            )}

        deps = Deps(call_model=fake_model)
        engine = Engine(deps=deps, config=EngineConfig(model="claude-sonnet-4-6"))

        async for _ in engine.run("Hi"):
            pass

        assert engine.total_input_tokens > 0
        assert engine.total_output_tokens > 0

    async def test_engine_cost_summary(self):
        from typing import AsyncGenerator
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        async def fake_model(**kwargs) -> AsyncGenerator[dict, None]:
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content="Sure, I can help with that!",
            )}

        deps = Deps(call_model=fake_model)
        engine = Engine(deps=deps, config=EngineConfig(model="claude-sonnet-4-6"))

        async for _ in engine.run("Help me"):
            pass

        summary = engine.cost_summary()
        assert "Input tokens:" in summary
        assert "Output tokens:" in summary
        assert "Estimated cost:" in summary
        assert "claude-sonnet-4-6" in summary

    async def test_engine_estimated_cost_returns_float(self):
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig

        deps = Deps(call_model=None)
        engine = Engine(deps=deps, config=EngineConfig(model="claude-sonnet-4-6"))
        assert engine.estimated_cost() == 0.0

    async def test_engine_cost_with_model_override(self):
        from typing import AsyncGenerator
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        async def fake_model(**kwargs) -> AsyncGenerator[dict, None]:
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content="a" * 400,  # 100 output tokens
            )}

        deps = Deps(call_model=fake_model)
        engine = Engine(deps=deps, config=EngineConfig(model="claude-sonnet-4-6"))

        async for _ in engine.run("test"):
            pass

        # Cost with default model (sonnet)
        sonnet_cost = engine.estimated_cost()
        # Cost with opus override
        opus_cost = engine.estimated_cost("claude-opus-4-6")
        assert opus_cost > sonnet_cost

    async def test_engine_multi_turn_accumulates(self):
        from typing import AsyncGenerator
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        async def fake_model(**kwargs) -> AsyncGenerator[dict, None]:
            yield {"type": "assistant", "message": Message(
                role="assistant",
                content="Reply.",
            )}

        deps = Deps(call_model=fake_model)
        engine = Engine(deps=deps, config=EngineConfig(model="claude-sonnet-4-6"))

        async for _ in engine.run("turn 1"):
            pass
        tokens_after_1 = engine.total_input_tokens

        async for _ in engine.run("turn 2"):
            pass
        tokens_after_2 = engine.total_input_tokens

        # Second turn should include more input tokens (accumulated context)
        assert tokens_after_2 > tokens_after_1


# ---------------------------------------------------------------------------
# Model pricing table completeness
# ---------------------------------------------------------------------------

class TestModelPricingTable:
    def test_all_pricing_entries_have_two_floats(self):
        for model, (inp, out) in _MODEL_PRICING.items():
            assert isinstance(inp, (int, float)), f"{model} input not numeric"
            assert isinstance(out, (int, float)), f"{model} output not numeric"
            assert inp >= 0, f"{model} input price negative"
            assert out >= 0, f"{model} output price negative"

    def test_output_price_gte_input_for_paid(self):
        """For paid models, output is typically more expensive than input."""
        for model, (inp, out) in _MODEL_PRICING.items():
            if inp > 0:
                assert out >= inp, f"{model}: output should cost >= input"


# ---------------------------------------------------------------------------
# get_context_limit
# ---------------------------------------------------------------------------

class TestGetContextLimit:
    def test_known_claude_model(self):
        assert get_context_limit("claude-sonnet-4-6") == 200_000

    def test_known_opus(self):
        assert get_context_limit("claude-opus-4-6") == 200_000

    def test_known_gpt4o(self):
        assert get_context_limit("gpt-4o") == 128_000

    def test_known_gpt4o_mini(self):
        assert get_context_limit("gpt-4o-mini") == 128_000

    def test_known_o1(self):
        assert get_context_limit("o1") == 200_000

    def test_unknown_model_returns_default(self):
        assert get_context_limit("totally-unknown-xyz") == _DEFAULT_CONTEXT_LIMIT

    def test_pattern_match_claude_variant(self):
        assert get_context_limit("claude-sonnet-99") == 200_000

    def test_pattern_match_gpt4o_variant(self):
        assert get_context_limit("gpt-4o-2099-01-01") == 128_000

    def test_all_table_entries_are_positive(self):
        for model, limit in MODEL_CONTEXT_LIMITS.items():
            assert limit > 0, f"{model} has non-positive context limit"

    def test_default_is_safe(self):
        assert _DEFAULT_CONTEXT_LIMIT == 100_000


# ---------------------------------------------------------------------------
# Auto-compaction in Engine
# ---------------------------------------------------------------------------

class TestAutoCompaction:
    """Test that Engine.run() auto-compacts when nearing context limit."""

    async def test_compaction_triggers_when_over_threshold(self):
        """Messages exceeding 80% of context limit trigger compaction."""
        from typing import Any, AsyncGenerator
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        compact_called_with: list[tuple] = []

        async def fake_model(**kwargs) -> AsyncGenerator[dict, None]:
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok",
            )}

        async def fake_compact(messages, token_limit=0):
            compact_called_with.append((len(messages), token_limit))
            # Return last 2 messages only (simulating compaction)
            return messages[-2:]

        deps = Deps(call_model=fake_model, compact=fake_compact)
        # gpt-4o-mini has 128K limit, 80% = 102,400 tokens
        engine = Engine(deps=deps, config=EngineConfig(model="gpt-4o-mini"))

        # Stuff history with enough messages to exceed 80% of 128K
        # 128K * 0.8 = 102,400 tokens. At ~4 chars/token = 409,600 chars
        big_text = "x" * 420_000  # ~105K tokens, over threshold
        engine._messages.append(Message(role="user", content=big_text))
        engine._messages.append(Message(role="assistant", content="got it"))

        # This run should trigger compaction
        async for _ in engine.run("new question"):
            pass

        assert len(compact_called_with) == 1
        assert compact_called_with[0][1] == int(128_000 * 0.80)

    async def test_no_compaction_when_under_threshold(self):
        """Messages under 80% threshold should NOT trigger compaction."""
        from typing import Any, AsyncGenerator
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        compact_called = False

        async def fake_model(**kwargs) -> AsyncGenerator[dict, None]:
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok",
            )}

        async def fake_compact(messages, token_limit=0):
            nonlocal compact_called
            compact_called = True
            return messages

        deps = Deps(call_model=fake_model, compact=fake_compact)
        engine = Engine(deps=deps, config=EngineConfig(model="claude-sonnet-4-6"))

        # Small conversation — well under 80% of 200K
        async for _ in engine.run("hello"):
            pass

        assert not compact_called

    async def test_no_compaction_when_no_compactor(self):
        """If deps.compact is None, no compaction should happen (no crash)."""
        from typing import AsyncGenerator
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        async def fake_model(**kwargs) -> AsyncGenerator[dict, None]:
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok",
            )}

        deps = Deps(call_model=fake_model, compact=None)
        engine = Engine(deps=deps, config=EngineConfig(model="claude-sonnet-4-6"))

        # Stuff history to exceed threshold
        big_text = "x" * 820_000  # ~205K tokens, way over 200K limit
        engine._messages.append(Message(role="user", content=big_text))

        # Should not crash — just skips compaction
        async for _ in engine.run("hi"):
            pass

    async def test_compaction_with_unknown_model_uses_default(self):
        """Unknown model falls back to 100K limit for compaction threshold."""
        from typing import AsyncGenerator
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        compact_called_with: list[tuple] = []

        async def fake_model(**kwargs) -> AsyncGenerator[dict, None]:
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok",
            )}

        async def fake_compact(messages, token_limit=0):
            compact_called_with.append((len(messages), token_limit))
            return messages[-2:]

        deps = Deps(call_model=fake_model, compact=fake_compact)
        engine = Engine(deps=deps, config=EngineConfig(model="unknown-model-xyz"))

        # 100K default, 80% = 80K tokens. Need > 320K chars
        big_text = "x" * 330_000  # ~82.5K tokens
        engine._messages.append(Message(role="user", content=big_text))

        async for _ in engine.run("hi"):
            pass

        assert len(compact_called_with) == 1
        # Default 100K * 0.80 = 80,000
        assert compact_called_with[0][1] == int(100_000 * 0.80)

    async def test_compaction_with_model_override(self):
        """Model passed to run() should be used for context limit, not config model."""
        from typing import AsyncGenerator
        from duh.kernel.deps import Deps
        from duh.kernel.engine import Engine, EngineConfig
        from duh.kernel.messages import Message

        compact_called_with: list[tuple] = []

        async def fake_model(**kwargs) -> AsyncGenerator[dict, None]:
            yield {"type": "assistant", "message": Message(
                role="assistant", content="ok",
            )}

        async def fake_compact(messages, token_limit=0):
            compact_called_with.append((len(messages), token_limit))
            return messages[-2:]

        deps = Deps(call_model=fake_model, compact=fake_compact)
        # Config says opus (200K) but we'll override to gpt-4o-mini (128K)
        engine = Engine(deps=deps, config=EngineConfig(model="claude-opus-4-6"))

        # 128K * 0.8 = 102,400 tokens, need > ~410K chars to exceed
        # But 200K * 0.8 = 160K tokens = 640K chars — so pick something in between
        # that exceeds gpt-4o-mini threshold but not opus threshold
        big_text = "x" * 420_000  # ~105K tokens
        engine._messages.append(Message(role="user", content=big_text))

        async for _ in engine.run("hi", model="gpt-4o-mini"):
            pass

        assert len(compact_called_with) == 1
        assert compact_called_with[0][1] == int(128_000 * 0.80)
