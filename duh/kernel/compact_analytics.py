"""Compact analytics — tracks compaction statistics per session (ADR-058).

    from duh.kernel.compact_analytics import CompactStats

    stats = CompactStats()
    stats.record("snip", tokens_freed=4200)
    stats.record("summary", tokens_freed=18000)
    print(stats.summary())
"""

from __future__ import annotations

from dataclasses import dataclass, field


# Canonical tier names recognized by ``record()``.
_KNOWN_TIERS = frozenset({
    "micro", "microcompact",
    "snip",
    "dedup",
    "summary", "summarize",
})


@dataclass
class CompactStats:
    """Accumulates compaction statistics for a session."""

    total_compactions: int = 0
    total_tokens_freed: int = 0
    snip_count: int = 0
    summary_count: int = 0
    microcompact_count: int = 0
    dedup_count: int = 0

    # Per-compaction history: list of (tier, tokens_freed)
    _history: list[tuple[str, int]] = field(default_factory=list, repr=False)

    def record(self, tier: str, tokens_freed: int) -> None:
        """Record a compaction event.

        Args:
            tier: The compaction tier that fired (e.g. ``"snip"``,
                ``"summary"``, ``"microcompact"``, ``"dedup"``).
            tokens_freed: Estimated tokens freed by this compaction.
        """
        self.total_compactions += 1
        self.total_tokens_freed += tokens_freed
        self._history.append((tier, tokens_freed))

        lower = tier.lower()
        if lower in ("snip",):
            self.snip_count += 1
        elif lower in ("summary", "summarize"):
            self.summary_count += 1
        elif lower in ("micro", "microcompact"):
            self.microcompact_count += 1
        elif lower in ("dedup",):
            self.dedup_count += 1

    def summary(self) -> str:
        """Return a human-readable summary of compaction statistics."""
        if self.total_compactions == 0:
            return "No compactions performed this session."

        lines = [
            f"Compaction statistics:",
            f"  Total compactions:  {self.total_compactions}",
            f"  Total tokens freed: {self.total_tokens_freed:,}",
            f"",
            f"  By tier:",
        ]
        if self.microcompact_count:
            lines.append(f"    Microcompact: {self.microcompact_count}")
        if self.snip_count:
            lines.append(f"    Snip:         {self.snip_count}")
        if self.dedup_count:
            lines.append(f"    Dedup:        {self.dedup_count}")
        if self.summary_count:
            lines.append(f"    Summary:      {self.summary_count}")

        # Show per-event history if any
        if self._history:
            lines.append("")
            lines.append("  History:")
            for i, (tier, freed) in enumerate(self._history, 1):
                lines.append(f"    {i}. {tier}: ~{freed:,} tokens freed")

        return "\n".join(lines)
