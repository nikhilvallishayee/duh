"""Per-session permission cache — remembers tool approval decisions.

Prevents repeated prompting for the same tool within a single REPL session.
Cache is never persisted to disk; each new session starts clean.

See ADR-066 Gap 1 for design rationale.
"""

from __future__ import annotations


class SessionPermissionCache:
    """Remembers tool approval decisions for the current session.

    Approval vocabulary:
    - "y" = yes, this time only
    - "a" = always allow this tool for this session
    - "n" = no, this time only
    - "N" = never allow this tool for this session
    """

    def __init__(self) -> None:
        self._always_allow: set[str] = set()  # tool names
        self._never_allow: set[str] = set()   # tool names

    def check(self, tool_name: str) -> str | None:
        """Return "allow" or "deny" if cached, None if not cached."""
        if tool_name in self._always_allow:
            return "allow"
        if tool_name in self._never_allow:
            return "deny"
        return None

    def record(self, tool_name: str, decision: str) -> None:
        """Record a user decision. decision is 'y', 'a', 'n', or 'N'."""
        if decision == "a":
            self._always_allow.add(tool_name)
            self._never_allow.discard(tool_name)
        elif decision == "N":
            self._never_allow.add(tool_name)
            self._always_allow.discard(tool_name)
        # y and n are one-time, don't cache

    def clear(self) -> None:
        """Reset all cached decisions."""
        self._always_allow.clear()
        self._never_allow.clear()
