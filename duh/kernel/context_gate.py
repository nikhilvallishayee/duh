"""Context gate — blocking gate at 95% context usage (ADR-059).

Architecture:
- 75%: snip fires (already implemented)
- 85%: auto-compact fires (already implemented)
- 95%: BLOCK — refuse new queries, force compaction first
"""

from __future__ import annotations


class ContextGate:
    """Blocks new queries when context usage exceeds the blocking threshold.

    Architecture:
    - 75%: snip fires (already implemented)
    - 85%: auto-compact fires (already implemented)
    - 95%: BLOCK — refuse new queries, force compaction first
    """

    BLOCK_THRESHOLD = 0.95

    def __init__(self, context_limit: int):
        self._context_limit = context_limit

    def check(self, token_estimate: int) -> tuple[bool, str]:
        """Return (allowed, reason). If not allowed, reason explains why."""
        if self._context_limit <= 0:
            return True, ""
        ratio = token_estimate / self._context_limit
        if ratio >= self.BLOCK_THRESHOLD:
            return False, (
                f"Context {ratio:.0%} full "
                f"({token_estimate:,}/{self._context_limit:,} tokens). "
                f"Run /compact to free space."
            )
        return True, ""
