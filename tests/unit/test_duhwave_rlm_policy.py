"""Tests for duh.duhwave.rlm.policy — choose_context_mode + explain.

Pure-function tests; no I/O, no fixtures. Each test asserts one route
from the policy table in ADR-028 §"When the engine activates".
"""

from __future__ import annotations

import pytest

from duh.duhwave.rlm.policy import ContextMode, choose_context_mode, explain


# ---------------------------------------------------------------------------
# Explicit overrides
# ---------------------------------------------------------------------------


class TestExplicit:
    def test_explicit_rlm_passes_through(self):
        mode = choose_context_mode(
            explicit=ContextMode.RLM,
            bulk_input_tokens=0,
            context_window=200_000,
            model_supports_tool_calls=True,
        )
        assert mode == ContextMode.RLM

    def test_explicit_compact_passes_through(self):
        mode = choose_context_mode(
            explicit=ContextMode.COMPACT,
            bulk_input_tokens=10_000_000,  # massive — should still be compact
            context_window=200_000,
            model_supports_tool_calls=True,
        )
        assert mode == ContextMode.COMPACT

    def test_explicit_rlm_overrides_no_tool_calls(self):
        # Explicit RLM ignores model capability; the caller asked for it.
        mode = choose_context_mode(
            explicit=ContextMode.RLM,
            bulk_input_tokens=0,
            context_window=100_000,
            model_supports_tool_calls=False,
        )
        assert mode == ContextMode.RLM


# ---------------------------------------------------------------------------
# AUTO branch
# ---------------------------------------------------------------------------


class TestAuto:
    def test_auto_small_bulk_picks_compact(self):
        # 10% of window — well below the 25% threshold.
        mode = choose_context_mode(
            explicit=ContextMode.AUTO,
            bulk_input_tokens=20_000,
            context_window=200_000,
            model_supports_tool_calls=True,
        )
        assert mode == ContextMode.COMPACT

    def test_auto_large_bulk_picks_rlm(self):
        # 50% of window — over the threshold.
        mode = choose_context_mode(
            explicit=ContextMode.AUTO,
            bulk_input_tokens=100_000,
            context_window=200_000,
            model_supports_tool_calls=True,
        )
        assert mode == ContextMode.RLM

    def test_auto_exact_threshold_picks_rlm(self):
        # Exactly 25% triggers RLM (the comparator is >=).
        mode = choose_context_mode(
            explicit=ContextMode.AUTO,
            bulk_input_tokens=50_000,
            context_window=200_000,
            model_supports_tool_calls=True,
        )
        assert mode == ContextMode.RLM

    def test_auto_no_tool_calls_forces_compact(self):
        # Even 90% bulk — RLM tools are tool calls, so no tool calls = no RLM.
        mode = choose_context_mode(
            explicit=ContextMode.AUTO,
            bulk_input_tokens=180_000,
            context_window=200_000,
            model_supports_tool_calls=False,
        )
        assert mode == ContextMode.COMPACT

    def test_auto_zero_context_window_picks_compact(self):
        # Edge case: window=0 → guard avoids div-by-zero.
        mode = choose_context_mode(
            explicit=ContextMode.AUTO,
            bulk_input_tokens=10_000,
            context_window=0,
            model_supports_tool_calls=True,
        )
        assert mode == ContextMode.COMPACT

    def test_auto_zero_bulk_picks_compact(self):
        mode = choose_context_mode(
            explicit=ContextMode.AUTO,
            bulk_input_tokens=0,
            context_window=200_000,
            model_supports_tool_calls=True,
        )
        assert mode == ContextMode.COMPACT

    def test_custom_activate_ratio_lower(self):
        # 10% bulk, but threshold lowered to 5% — should pick RLM.
        mode = choose_context_mode(
            explicit=ContextMode.AUTO,
            bulk_input_tokens=20_000,
            context_window=200_000,
            model_supports_tool_calls=True,
            activate_ratio=0.05,
        )
        assert mode == ContextMode.RLM


# ---------------------------------------------------------------------------
# explain()
# ---------------------------------------------------------------------------


class TestExplain:
    def test_explain_explicit_rlm(self):
        d = explain(
            explicit=ContextMode.RLM,
            bulk_input_tokens=0,
            context_window=100_000,
            model_supports_tool_calls=True,
        )
        assert d.mode == ContextMode.RLM
        assert "explicit" in d.reason

    def test_explain_explicit_compact(self):
        d = explain(
            explicit=ContextMode.COMPACT,
            bulk_input_tokens=0,
            context_window=100_000,
            model_supports_tool_calls=True,
        )
        assert d.mode == ContextMode.COMPACT
        assert "explicit" in d.reason

    def test_explain_no_tool_calls(self):
        d = explain(
            explicit=ContextMode.AUTO,
            bulk_input_tokens=100_000,
            context_window=200_000,
            model_supports_tool_calls=False,
        )
        assert d.mode == ContextMode.COMPACT
        assert "tool-calling" in d.reason

    def test_explain_threshold_triggered(self):
        d = explain(
            explicit=ContextMode.AUTO,
            bulk_input_tokens=100_000,  # 50% of window
            context_window=200_000,
            model_supports_tool_calls=True,
        )
        assert d.mode == ContextMode.RLM
        assert "25%" in d.reason

    def test_explain_below_threshold(self):
        d = explain(
            explicit=ContextMode.AUTO,
            bulk_input_tokens=10_000,  # 5% of window
            context_window=200_000,
            model_supports_tool_calls=True,
        )
        assert d.mode == ContextMode.COMPACT
        assert "25%" in d.reason
