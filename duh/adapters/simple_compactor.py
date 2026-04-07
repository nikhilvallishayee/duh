"""SimpleCompactor adapter — context window management via summarize + tail-window.

Implements the ContextManager port. Uses a rough chars-per-token estimate
(chars / 4) for token estimation. When messages exceed the token limit,
older messages are summarized into a single system message while the most
recent messages are kept intact.

    provider = SimpleCompactor(default_limit=100_000)
    estimated = provider.estimate_tokens(messages)
    compacted = await provider.compact(messages, token_limit=100_000)
"""

from __future__ import annotations

import json
from typing import Any

from duh.kernel.messages import Message


class SimpleCompactor:
    """Tail-window context compactor.

    Implements the ContextManager port by estimating tokens via
    chars / bytes_per_token and keeping the most recent messages that
    fit within the limit.
    """

    def __init__(
        self,
        default_limit: int = 100_000,
        bytes_per_token: int = 4,
        min_keep: int = 2,
    ):
        if bytes_per_token < 1:
            raise ValueError("bytes_per_token must be >= 1")
        if min_keep < 0:
            raise ValueError("min_keep must be >= 0")
        self._default_limit = default_limit
        self._bytes_per_token = bytes_per_token
        self._min_keep = min_keep

    # ------------------------------------------------------------------
    # ContextManager protocol
    # ------------------------------------------------------------------

    def estimate_tokens(self, messages: list[Any]) -> int:
        """Estimate token count for a list of messages.

        Uses chars / bytes_per_token as a rough token estimate.
        """
        total = 0
        for msg in messages:
            total += self._estimate_single(msg)
        return total

    async def compact(
        self,
        messages: list[Any],
        token_limit: int = 0,
    ) -> list[Any]:
        """Compact messages to fit within token limit.

        Strategy:
        1. Separate system messages (always kept) from conversation.
        2. Walk backward through non-system messages to find what fits.
        3. Summarize dropped messages into a single system message
           ("Previous conversation summary: ...").
        4. Always keep at least ``min_keep`` recent non-system messages.

        Returns a new list (does not mutate the input).
        """
        limit = token_limit or self._default_limit
        if not messages:
            return []

        # Partition: system vs. conversation
        system_msgs: list[Any] = []
        conversation: list[Any] = []
        for msg in messages:
            role = _get_role(msg)
            if role == "system":
                system_msgs.append(msg)
            else:
                conversation.append(msg)

        if not conversation:
            return list(system_msgs)

        # Budget = limit minus system token cost
        system_tokens = self.estimate_tokens(system_msgs)
        budget = max(0, limit - system_tokens)

        # Walk backward, accumulating the tail window
        kept: list[Any] = []
        used = 0
        for msg in reversed(conversation):
            msg_tokens = self._estimate_single(msg)
            if used + msg_tokens > budget and len(kept) >= self._min_keep:
                break
            kept.append(msg)
            used += msg_tokens

        kept.reverse()

        # How many conversation messages were dropped?
        dropped_count = len(conversation) - len(kept)

        if dropped_count > 0:
            # Summarize dropped messages into a system message
            dropped = conversation[:dropped_count]
            summary = _summarize_messages(dropped)
            summary_msg = Message(role="system", content=summary)
            return system_msgs + [summary_msg] + kept

        return system_msgs + kept

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _estimate_single(self, msg: Any) -> int:
        """Estimate tokens for a single message."""
        text = _serialize_message(msg)
        return len(text) // self._bytes_per_token

    @property
    def default_limit(self) -> int:
        return self._default_limit

    @property
    def bytes_per_token(self) -> int:
        return self._bytes_per_token

    @property
    def min_keep(self) -> int:
        return self._min_keep


def _get_role(msg: Any) -> str:
    """Extract role from a Message or dict."""
    if isinstance(msg, Message):
        return msg.role
    if isinstance(msg, dict):
        return msg.get("role", "")
    return ""


def _serialize_message(msg: Any) -> str:
    """Serialize a message to a string for token estimation."""
    if isinstance(msg, Message):
        if isinstance(msg.content, str):
            return msg.content
        # List content — serialize each block
        return json.dumps(
            [_block_to_serializable(b) for b in msg.content],
            ensure_ascii=False,
        )
    if isinstance(msg, dict):
        content = msg.get("content", "")
        if isinstance(content, str):
            return content
        return json.dumps(content, ensure_ascii=False, default=str)
    return str(msg)


def _block_to_serializable(block: Any) -> Any:
    """Convert a content block to a JSON-serializable form."""
    if isinstance(block, dict):
        return block
    if hasattr(block, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(block)
    return str(block)


# Max chars to keep in a conversation summary
_SUMMARY_MAX_CHARS = 2000


def _summarize_messages(messages: list[Any]) -> str:
    """Summarize a list of messages into a compact text summary.

    Concatenates message texts with role labels and truncates to a
    reasonable length. No model call needed — this is a deterministic
    extraction of the key content from the conversation.
    """
    parts: list[str] = []
    for msg in messages:
        role = _get_role(msg) or "unknown"
        text = _serialize_message(msg).strip()
        if not text:
            continue
        # Truncate individual messages that are very long
        if len(text) > 300:
            text = text[:297] + "..."
        parts.append(f"[{role}] {text}")

    combined = "\n".join(parts)

    # Truncate the whole summary if it's too long
    if len(combined) > _SUMMARY_MAX_CHARS:
        combined = combined[:_SUMMARY_MAX_CHARS - 3] + "..."

    return f"Previous conversation summary:\n{combined}"
