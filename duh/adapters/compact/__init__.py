"""Pluggable compaction system for D.U.H.

Provides a multi-tier adaptive compactor that runs strategies in order
until context fits within budget. See ADR-056.

    from duh.adapters.compact import AdaptiveCompactor, CompactionResult

    compactor = AdaptiveCompactor()
    result = await compactor.compact(messages, token_limit=100_000)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class CompactionStrategy(Protocol):
    """Protocol for pluggable compaction strategies.

    Any class with ``compact()`` and ``estimate_tokens()`` methods
    satisfies this protocol.  SimpleCompactor, ModelCompactor, and
    all tier implementations conform.
    """

    async def compact(
        self,
        messages: list[Any],
        token_limit: int = 0,
    ) -> list[Any]: ...

    def estimate_tokens(self, messages: list[Any]) -> int: ...


@dataclass
class CompactionResult:
    """Result of a compaction pass.

    Attributes:
        messages: The compacted message list.
        tokens_before: Estimated token count before compaction.
        tokens_after: Estimated token count after compaction.
        strategy_used: Name of the strategy that achieved the final result.
        tiers_run: Number of tiers that were executed.
    """

    messages: list[Any] = field(default_factory=list)
    tokens_before: int = 0
    tokens_after: int = 0
    strategy_used: str = ""
    tiers_run: int = 0


from duh.adapters.compact.adaptive import AdaptiveCompactor  # noqa: E402, F401
from duh.adapters.compact.snip import SnipCompactor  # noqa: E402, F401

__all__ = [
    "CompactionStrategy",
    "CompactionResult",
    "AdaptiveCompactor",
    "SnipCompactor",
]
