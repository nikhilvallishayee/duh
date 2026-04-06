"""ContextManager port — how D.U.H. manages the context window."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ContextManager(Protocol):
    """Abstract interface for context window management."""

    async def compact(
        self,
        messages: list[Any],
        token_limit: int,
    ) -> list[Any]:
        """Compact messages to fit within token limit.

        Preserves the most recent and most important messages.
        Returns a new list (does not mutate the input).
        """
        ...

    def estimate_tokens(self, messages: list[Any]) -> int:
        """Estimate token count for a list of messages."""
        ...
