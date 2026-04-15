"""Prompt cache hit-rate tracker (ADR-061 Phase 3).

Tracks ``cache_creation_input_tokens`` and ``cache_read_input_tokens``
from Anthropic API usage metadata across turns, and detects unexpected
cache breaks (sudden drops in cache read ratio).

Usage::

    tracker = CacheTracker()
    # after each API response:
    tracker.record_usage(msg.metadata["usage"])
    # after compaction:
    tracker.notify_compaction()
    # check for cache break:
    if tracker.is_break_detected():
        logger.warning("Cache break detected: %s", tracker.summary())
"""

from __future__ import annotations

from dataclasses import dataclass, field

# A drop of more than this fraction between consecutive turns
# (without a compaction in between) signals an unexpected cache break.
_BREAK_THRESHOLD = 0.40


@dataclass
class CacheTracker:
    """Track prompt cache hit rates across turns.

    After each API response, record cache_creation_input_tokens and
    cache_read_input_tokens from the usage metadata.  A high cache_read
    ratio means the prefix is cached.  A sudden drop indicates a break.
    """

    _history: list[dict] = field(default_factory=list)
    _compaction_pending: bool = False

    def record_usage(self, usage: dict) -> None:
        """Record usage from an API response.

        Parameters
        ----------
        usage:
            The ``usage`` dict from assistant message metadata. Expected
            keys: ``cache_creation_input_tokens``,
            ``cache_read_input_tokens``, ``input_tokens``.
        """
        self._history.append({
            "cache_creation": usage.get("cache_creation_input_tokens", 0),
            "cache_read": usage.get("cache_read_input_tokens", 0),
            "input": usage.get("input_tokens", 0),
        })

    def notify_compaction(self) -> None:
        """Mark that compaction just happened — expect a cache break.

        The next turn will have a low cache read ratio because the
        message prefix changed.  Calling this suppresses the false
        positive that would otherwise be reported by
        :meth:`is_break_detected`.
        """
        self._compaction_pending = True

    def cache_read_ratio(self) -> float:
        """Return the fraction of input tokens served from cache (0.0–1.0).

        Uses the most recent usage entry.  Returns 0.0 when there is no
        history or no input tokens were recorded.
        """
        if not self._history:
            return 0.0
        latest = self._history[-1]
        total_input = latest["input"] + latest["cache_read"] + latest["cache_creation"]
        if total_input <= 0:
            return 0.0
        return latest["cache_read"] / total_input

    def is_break_detected(self) -> bool:
        """Return True if cache read ratio dropped unexpectedly.

        A "break" is defined as:
        - At least 2 entries in history.
        - The previous entry had a non-trivial cache read ratio (> 0.1).
        - The current entry's ratio dropped by more than
          ``_BREAK_THRESHOLD`` compared to the previous entry.
        - No compaction happened between the two entries.

        After a compaction notification, the first post-compaction entry
        consumes the flag and is not considered a break.
        """
        if len(self._history) < 2:
            return False

        # Consume the compaction flag — the post-compaction drop is expected.
        if self._compaction_pending:
            self._compaction_pending = False
            return False

        prev = self._history[-2]
        curr = self._history[-1]

        prev_total = prev["input"] + prev["cache_read"] + prev["cache_creation"]
        curr_total = curr["input"] + curr["cache_read"] + curr["cache_creation"]

        if prev_total <= 0 or curr_total <= 0:
            return False

        prev_ratio = prev["cache_read"] / prev_total
        curr_ratio = curr["cache_read"] / curr_total

        # Only flag a break if the previous ratio was meaningful.
        if prev_ratio <= 0.1:
            return False

        return (prev_ratio - curr_ratio) > _BREAK_THRESHOLD

    def total_cache_read_tokens(self) -> int:
        """Return the total cache_read tokens across all recorded turns."""
        return sum(entry["cache_read"] for entry in self._history)

    def total_cache_creation_tokens(self) -> int:
        """Return the total cache_creation tokens across all recorded turns."""
        return sum(entry["cache_creation"] for entry in self._history)

    def summary(self) -> str:
        """Return human-readable cache stats."""
        if not self._history:
            return "Cache: no data yet"

        ratio = self.cache_read_ratio()
        total_read = self.total_cache_read_tokens()
        total_create = self.total_cache_creation_tokens()
        turns = len(self._history)
        status = "BREAK" if self.is_break_detected() else "OK"

        return (
            f"Cache: {ratio:.0%} read ratio | "
            f"{total_read:,} read / {total_create:,} created | "
            f"{turns} turn(s) | {status}"
        )
