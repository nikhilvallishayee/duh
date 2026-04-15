"""Tests for the context gate (ADR-059)."""

from __future__ import annotations

import pytest

from duh.kernel.context_gate import ContextGate


class TestContextGate:
    """ContextGate blocks queries when context usage >= 95%."""

    def test_below_threshold_allowed(self):
        gate = ContextGate(context_limit=100_000)
        allowed, reason = gate.check(50_000)
        assert allowed is True
        assert reason == ""

    def test_at_50_percent_allowed(self):
        gate = ContextGate(context_limit=200_000)
        allowed, reason = gate.check(100_000)
        assert allowed is True
        assert reason == ""

    def test_at_95_percent_blocked(self):
        gate = ContextGate(context_limit=100_000)
        allowed, reason = gate.check(95_000)
        assert allowed is False
        assert "95%" in reason
        assert "/compact" in reason

    def test_at_100_percent_blocked(self):
        gate = ContextGate(context_limit=100_000)
        allowed, reason = gate.check(100_000)
        assert allowed is False
        assert "100%" in reason
        assert "/compact" in reason

    def test_over_100_percent_blocked(self):
        gate = ContextGate(context_limit=100_000)
        allowed, reason = gate.check(120_000)
        assert allowed is False
        assert "/compact" in reason

    def test_just_below_95_allowed(self):
        gate = ContextGate(context_limit=100_000)
        allowed, reason = gate.check(94_999)
        assert allowed is True
        assert reason == ""

    def test_zero_tokens_allowed(self):
        gate = ContextGate(context_limit=100_000)
        allowed, reason = gate.check(0)
        assert allowed is True
        assert reason == ""

    def test_zero_context_limit_allowed(self):
        """A zero context limit should not block (defensive)."""
        gate = ContextGate(context_limit=0)
        allowed, reason = gate.check(50_000)
        assert allowed is True
        assert reason == ""

    def test_reason_includes_token_counts(self):
        gate = ContextGate(context_limit=100_000)
        allowed, reason = gate.check(96_000)
        assert "96,000" in reason
        assert "100,000" in reason

    def test_block_threshold_constant(self):
        assert ContextGate.BLOCK_THRESHOLD == 0.95
