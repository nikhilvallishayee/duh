"""AdaptiveCompactor — orchestrates multi-tier compaction (ADR-056, ADR-060).

Runs compaction strategies in order:
    microcompact -> snip (75%) -> dedup -> summarize (85%)
stopping as soon as the context fits within budget.  Includes a circuit
breaker (max 3 consecutive failures) and reserves an output buffer.

Snip compaction (ADR-060) fires at 75% context usage as a free structural
pass.  If snip frees enough tokens, the expensive model summary at 85% is
skipped entirely.

    from duh.adapters.compact import AdaptiveCompactor

    compactor = AdaptiveCompactor()
    result = await compactor.compact(messages, token_limit=100_000)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from duh.kernel.messages import Message

logger = logging.getLogger(__name__)

# Default reserved output buffer (tokens)
DEFAULT_OUTPUT_BUFFER = 20_000

# Circuit breaker: max consecutive failures before giving up
MAX_CONSECUTIVE_FAILURES = 3

# Threshold gates: a strategy only fires when current usage exceeds this
# fraction of the effective limit.  Strategies without an entry always fire.
# ADR-060: snip at 75%, summarize at 85%.
_THRESHOLD_GATES: dict[str, float] = {
    "SnipCompactor": 0.75,
    "SummarizeCompactor": 0.85,
}


class AdaptiveCompactor:
    """Multi-tier adaptive compactor orchestrator.

    Satisfies the CompactionStrategy protocol so it can be used
    as a drop-in replacement for SimpleCompactor or ModelCompactor.

    Args:
        strategies: Ordered list of CompactionStrategy instances to run.
            If None, creates the default 3-tier pipeline (micro, dedup,
            summarize).
        output_buffer: Tokens reserved for model output. Subtracted from
            the token limit before compaction.
        bytes_per_token: For token estimation.
        call_model: Model callable for Tier 2 summarization.
        file_tracker: File tracker for post-compact restoration.
        skill_context: Active skill context string.
    """

    def __init__(
        self,
        strategies: list[Any] | None = None,
        output_buffer: int = DEFAULT_OUTPUT_BUFFER,
        bytes_per_token: int = 4,
        call_model: Any = None,
        file_tracker: Any = None,
        skill_context: str | None = None,
    ):
        self._output_buffer = output_buffer
        self._bytes_per_token = bytes_per_token
        self._consecutive_failures = 0

        if strategies is not None:
            self._strategies = strategies
        else:
            # Build default 4-tier pipeline (ADR-056 + ADR-060)
            from duh.adapters.compact.microcompact import MicroCompactor
            from duh.adapters.compact.snip import SnipCompactor
            from duh.adapters.compact.dedup import DedupCompactor
            from duh.adapters.compact.summarize import SummarizeCompactor

            self._strategies = [
                MicroCompactor(bytes_per_token=bytes_per_token),
                SnipCompactor(bytes_per_token=bytes_per_token),
                DedupCompactor(bytes_per_token=bytes_per_token),
                SummarizeCompactor(
                    call_model=call_model,
                    bytes_per_token=bytes_per_token,
                    file_tracker=file_tracker,
                    skill_context=skill_context,
                ),
            ]

    def estimate_tokens(self, messages: list[Any]) -> int:
        """Estimate token count for messages."""
        total = 0
        for msg in messages:
            total += self._estimate_single(msg)
        return total

    async def compact(
        self,
        messages: list[Any],
        token_limit: int = 0,
    ) -> list[Any]:
        """Run tiers in order until context fits within budget.

        Subtracts output_buffer from token_limit to reserve space for
        the model's response.  Stops early if a tier brings the context
        under budget.

        Circuit breaker: after MAX_CONSECUTIVE_FAILURES exceptions,
        returns the best result so far without retrying further tiers.

        Returns the compacted message list.
        """
        if not messages:
            return []

        limit = token_limit or 100_000
        effective_limit = max(0, limit - self._output_buffer)

        # Check if already under budget
        current_tokens = self.estimate_tokens(messages)
        if current_tokens <= effective_limit:
            self._consecutive_failures = 0
            return list(messages)

        current_messages = list(messages)
        tiers_run = 0

        for strategy in self._strategies:
            # Circuit breaker check
            if self._consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
                logger.warning(
                    "Circuit breaker: %d consecutive failures, "
                    "stopping compaction",
                    self._consecutive_failures,
                )
                break

            strategy_name = type(strategy).__name__

            # Threshold gate (ADR-060): skip strategies whose threshold
            # hasn't been reached yet.
            threshold = _THRESHOLD_GATES.get(strategy_name)
            if threshold is not None and effective_limit > 0:
                usage_ratio = current_tokens / effective_limit
                if usage_ratio < threshold:
                    logger.debug(
                        "Tier %s skipped: usage %.1f%% < threshold %.0f%%",
                        strategy_name,
                        usage_ratio * 100,
                        threshold * 100,
                    )
                    continue

            tiers_run += 1

            try:
                result = await strategy.compact(
                    current_messages, token_limit=effective_limit,
                )
                # Reset on success
                self._consecutive_failures = 0
            except Exception:
                self._consecutive_failures += 1
                logger.debug(
                    "Tier %s failed (failure %d/%d)",
                    strategy_name,
                    self._consecutive_failures,
                    MAX_CONSECUTIVE_FAILURES,
                    exc_info=True,
                )
                continue

            current_messages = result
            result_tokens = self.estimate_tokens(current_messages)

            logger.debug(
                "Tier %s: %d -> %d tokens (limit %d)",
                strategy_name,
                current_tokens,
                result_tokens,
                effective_limit,
            )

            # Early exit if under budget
            if result_tokens <= effective_limit:
                return current_messages

            current_tokens = result_tokens

        # Best effort — return whatever we have
        return current_messages

    def _estimate_single(self, msg: Any) -> int:
        text = _serialize_message(msg)
        return len(text) // self._bytes_per_token

    @property
    def output_buffer(self) -> int:
        return self._output_buffer

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    def reset_circuit_breaker(self) -> None:
        """Reset the circuit breaker counter."""
        self._consecutive_failures = 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _serialize_message(msg: Any) -> str:
    if isinstance(msg, Message):
        if isinstance(msg.content, str):
            return msg.content
        return json.dumps(
            [_block_to_dict(b) for b in msg.content],
            ensure_ascii=False,
        )
    if isinstance(msg, dict):
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(msg)


def _block_to_dict(block: Any) -> Any:
    if isinstance(block, dict):
        return block
    if hasattr(block, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(block)
    return str(block)
