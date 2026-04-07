"""Tests for duh.kernel.tokens — token estimation and cost tracking."""

import pytest

from duh.kernel.tokens import (
    count_tokens,
    estimate_cost,
    format_cost,
    _resolve_pricing,
    _CHARS_PER_TOKEN,
    _MODEL_PRICING,
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
