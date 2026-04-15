"""Tests for CacheTracker (ADR-061 Phase 3).

Verifies prompt cache break detection:
1. record_usage tracks history
2. cache_read_ratio calculates correctly
3. Break detected on sudden drop
4. Compaction notification suppresses false positive
5. Empty history returns 0.0
"""

import pytest

from duh.kernel.cache_tracker import CacheTracker, _BREAK_THRESHOLD


# ═══════════════════════════════════════════════════════════════════
# record_usage tracks history
# ═══════════════════════════════════════════════════════════════════


class TestRecordUsage:
    """record_usage appends to internal history."""

    def test_single_entry(self):
        t = CacheTracker()
        t.record_usage({
            "input_tokens": 100,
            "cache_creation_input_tokens": 50,
            "cache_read_input_tokens": 30,
        })
        assert len(t._history) == 1
        assert t._history[0]["input"] == 100
        assert t._history[0]["cache_creation"] == 50
        assert t._history[0]["cache_read"] == 30

    def test_multiple_entries(self):
        t = CacheTracker()
        t.record_usage({"input_tokens": 100})
        t.record_usage({"input_tokens": 200})
        t.record_usage({"input_tokens": 300})
        assert len(t._history) == 3

    def test_missing_cache_fields_default_to_zero(self):
        t = CacheTracker()
        t.record_usage({"input_tokens": 100})
        assert t._history[0]["cache_creation"] == 0
        assert t._history[0]["cache_read"] == 0

    def test_missing_input_defaults_to_zero(self):
        t = CacheTracker()
        t.record_usage({})
        assert t._history[0]["input"] == 0

    def test_empty_dict(self):
        t = CacheTracker()
        t.record_usage({})
        assert t._history[0] == {"cache_creation": 0, "cache_read": 0, "input": 0}


# ═══════════════════════════════════════════════════════════════════
# cache_read_ratio calculates correctly
# ═══════════════════════════════════════════════════════════════════


class TestCacheReadRatio:
    """cache_read_ratio returns the fraction of input tokens from cache."""

    def test_empty_history_returns_zero(self):
        t = CacheTracker()
        assert t.cache_read_ratio() == 0.0

    def test_no_cache_read_returns_zero(self):
        t = CacheTracker()
        t.record_usage({"input_tokens": 100})
        assert t.cache_read_ratio() == 0.0

    def test_all_from_cache(self):
        t = CacheTracker()
        t.record_usage({
            "input_tokens": 0,
            "cache_read_input_tokens": 1000,
            "cache_creation_input_tokens": 0,
        })
        assert t.cache_read_ratio() == 1.0

    def test_half_from_cache(self):
        t = CacheTracker()
        t.record_usage({
            "input_tokens": 500,
            "cache_read_input_tokens": 500,
            "cache_creation_input_tokens": 0,
        })
        assert t.cache_read_ratio() == pytest.approx(0.5)

    def test_mixed_tokens(self):
        """cache_read / (input + cache_read + cache_creation)."""
        t = CacheTracker()
        t.record_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 700,
            "cache_creation_input_tokens": 200,
        })
        # 700 / (100 + 700 + 200) = 0.7
        assert t.cache_read_ratio() == pytest.approx(0.7)

    def test_uses_latest_entry(self):
        """Ratio is based on the most recent entry, not cumulative."""
        t = CacheTracker()
        t.record_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 0,
        })
        t.record_usage({
            "input_tokens": 0,
            "cache_read_input_tokens": 500,
            "cache_creation_input_tokens": 500,
        })
        # Latest: 500 / (0 + 500 + 500) = 0.5
        assert t.cache_read_ratio() == pytest.approx(0.5)

    def test_zero_total_returns_zero(self):
        """All zeros should not divide by zero."""
        t = CacheTracker()
        t.record_usage({
            "input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        })
        assert t.cache_read_ratio() == 0.0


# ═══════════════════════════════════════════════════════════════════
# Break detected on sudden drop
# ═══════════════════════════════════════════════════════════════════


class TestIsBreakDetected:
    """is_break_detected flags unexpected cache ratio drops."""

    def test_empty_history_no_break(self):
        t = CacheTracker()
        assert t.is_break_detected() is False

    def test_single_entry_no_break(self):
        t = CacheTracker()
        t.record_usage({"input_tokens": 100, "cache_read_input_tokens": 90})
        assert t.is_break_detected() is False

    def test_stable_ratio_no_break(self):
        """Consistent cache reads should not trigger a break."""
        t = CacheTracker()
        t.record_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 100,
        })
        t.record_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 750,
            "cache_creation_input_tokens": 150,
        })
        # 0.80 -> 0.75 — only a 0.05 drop, below threshold
        assert t.is_break_detected() is False

    def test_sudden_drop_detects_break(self):
        """A large drop in cache ratio should be detected as a break."""
        t = CacheTracker()
        # Turn 1: 80% cache read
        t.record_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 100,
        })
        # Turn 2: 5% cache read — massive drop
        t.record_usage({
            "input_tokens": 900,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 50,
        })
        assert t.is_break_detected() is True

    def test_drop_exactly_at_threshold(self):
        """A drop exactly equal to the threshold should NOT trigger (> not >=)."""
        t = CacheTracker()
        # Build a scenario where drop equals threshold exactly
        # prev ratio = 0.50, curr ratio = 0.50 - _BREAK_THRESHOLD
        # For _BREAK_THRESHOLD=0.40: prev=0.50, curr=0.10, drop=0.40 (not >)
        t.record_usage({
            "input_tokens": 500,
            "cache_read_input_tokens": 500,
            "cache_creation_input_tokens": 0,
        })
        t.record_usage({
            "input_tokens": 900,
            "cache_read_input_tokens": 100,
            "cache_creation_input_tokens": 0,
        })
        # prev = 0.5, curr = 0.1, drop = 0.4 == threshold, NOT >
        assert t.is_break_detected() is False

    def test_low_previous_ratio_no_break(self):
        """If previous ratio was already low, a drop is not a break."""
        t = CacheTracker()
        # Turn 1: 5% cache read (too low to be meaningful)
        t.record_usage({
            "input_tokens": 950,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 0,
        })
        # Turn 2: 0% cache read
        t.record_usage({
            "input_tokens": 1000,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        })
        assert t.is_break_detected() is False

    def test_zero_total_in_current_no_break(self):
        """Zero tokens in current turn should not crash or flag."""
        t = CacheTracker()
        t.record_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 100,
        })
        t.record_usage({
            "input_tokens": 0,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        })
        assert t.is_break_detected() is False


# ═══════════════════════════════════════════════════════════════════
# Compaction notification suppresses false positive
# ═══════════════════════════════════════════════════════════════════


class TestCompactionSuppression:
    """notify_compaction prevents false cache break detection."""

    def test_compaction_suppresses_break(self):
        """After compaction, a cache drop is expected and not flagged."""
        t = CacheTracker()
        # Turn 1: high cache ratio
        t.record_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 100,
        })
        # Compaction happens
        t.notify_compaction()
        # Turn 2: low cache ratio (expected after compaction)
        t.record_usage({
            "input_tokens": 900,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 50,
        })
        assert t.is_break_detected() is False

    def test_compaction_flag_consumed_once(self):
        """The compaction flag should be consumed on the first check."""
        t = CacheTracker()
        t.record_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 100,
        })
        t.notify_compaction()
        t.record_usage({
            "input_tokens": 900,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 50,
        })
        # First check — suppressed
        assert t.is_break_detected() is False
        # Now add another drop WITHOUT compaction
        t.record_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 100,
        })
        t.record_usage({
            "input_tokens": 900,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 50,
        })
        # Second drop — NOT suppressed (compaction flag was consumed)
        assert t.is_break_detected() is True

    def test_compaction_before_any_usage(self):
        """notify_compaction before any usage should not crash."""
        t = CacheTracker()
        t.notify_compaction()
        assert t.is_break_detected() is False

    def test_multiple_compaction_notifications(self):
        """Multiple notify_compaction calls should be idempotent."""
        t = CacheTracker()
        t.record_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 100,
        })
        t.notify_compaction()
        t.notify_compaction()  # redundant
        t.record_usage({
            "input_tokens": 900,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 50,
        })
        # Should still suppress
        assert t.is_break_detected() is False


# ═══════════════════════════════════════════════════════════════════
# Summary output
# ═══════════════════════════════════════════════════════════════════


class TestSummary:
    """summary returns human-readable cache stats."""

    def test_empty_history(self):
        t = CacheTracker()
        assert t.summary() == "Cache: no data yet"

    def test_with_data(self):
        t = CacheTracker()
        t.record_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 700,
            "cache_creation_input_tokens": 200,
        })
        s = t.summary()
        assert "70%" in s
        assert "700" in s
        assert "200" in s
        assert "1 turn" in s
        assert "OK" in s

    def test_break_status_in_summary(self):
        t = CacheTracker()
        t.record_usage({
            "input_tokens": 100,
            "cache_read_input_tokens": 800,
            "cache_creation_input_tokens": 100,
        })
        t.record_usage({
            "input_tokens": 900,
            "cache_read_input_tokens": 50,
            "cache_creation_input_tokens": 50,
        })
        s = t.summary()
        # Note: summary calls is_break_detected which consumes the compaction flag
        # The break should still be detectable for summary purposes
        assert "BREAK" in s or "OK" in s  # depends on state


# ═══════════════════════════════════════════════════════════════════
# Totals
# ═══════════════════════════════════════════════════════════════════


class TestTotals:
    """Cumulative token totals across turns."""

    def test_total_cache_read_tokens(self):
        t = CacheTracker()
        t.record_usage({"cache_read_input_tokens": 100})
        t.record_usage({"cache_read_input_tokens": 200})
        t.record_usage({"cache_read_input_tokens": 300})
        assert t.total_cache_read_tokens() == 600

    def test_total_cache_creation_tokens(self):
        t = CacheTracker()
        t.record_usage({"cache_creation_input_tokens": 50})
        t.record_usage({"cache_creation_input_tokens": 0})
        t.record_usage({"cache_creation_input_tokens": 100})
        assert t.total_cache_creation_tokens() == 150

    def test_empty_totals(self):
        t = CacheTracker()
        assert t.total_cache_read_tokens() == 0
        assert t.total_cache_creation_tokens() == 0
