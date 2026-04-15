"""Tier 0.5: Snip Compaction — structural message pruning (ADR-060).

Removes completed API rounds (assistant + tool_result user message pairs)
from the oldest end of the conversation.  Zero cost, sub-millisecond.

    from duh.adapters.compact.snip import SnipCompactor

    sc = SnipCompactor(keep_last=6)
    compacted = await sc.compact(messages, token_limit=100_000)
"""

from __future__ import annotations

import json
from typing import Any

from duh.kernel.messages import Message


# ---------------------------------------------------------------------------
# Snip boundary marker text
# ---------------------------------------------------------------------------

_SNIP_MARKER_PREFIX = (
    "(Earlier conversation snipped for context management. "
)


# ---------------------------------------------------------------------------
# SnipCompactor
# ---------------------------------------------------------------------------

class SnipCompactor:
    """Tier 0.5 compactor — structural snip of old API rounds.

    Satisfies the CompactionStrategy protocol.

    Rules:
    1. Never snip the first user message (original prompt / task context).
    2. Never snip the last ``keep_last`` messages (recent context).
    3. Only snip complete rounds (assistant + tool_result user pairs).
    4. Insert a snip boundary marker after snipping.
    5. Track tokens freed for analytics.
    """

    def __init__(
        self,
        keep_last: int = 6,
        bytes_per_token: int = 4,
    ):
        self._keep_last = keep_last
        self._bytes_per_token = bytes_per_token

    # -- CompactionStrategy protocol -----------------------------------------

    def estimate_tokens(self, messages: list[Any]) -> int:
        """Estimate token count for a message list."""
        total = 0
        for msg in messages:
            total += self._estimate_single(msg)
        return total

    async def compact(
        self,
        messages: list[Any],
        token_limit: int = 0,
    ) -> list[Any]:
        """Snip old API rounds from the front of the conversation.

        Returns a new list with the snipped messages removed and a
        boundary marker inserted in their place.
        """
        result, _ = self.snip(messages, keep_last=self._keep_last)
        return result

    # -- Public API ----------------------------------------------------------

    def snip(
        self,
        messages: list[Any],
        keep_last: int | None = None,
    ) -> tuple[list[Any], int]:
        """Remove old API rounds from the front, keep the last N messages.

        A 'round' is: assistant message + user message (tool_result).

        Args:
            messages: The conversation message list.
            keep_last: Number of recent messages to keep.  Falls back to
                the instance default.

        Returns:
            (snipped_messages, tokens_freed) tuple.
        """
        if keep_last is None:
            keep_last = self._keep_last

        if len(messages) <= keep_last or len(messages) < 3:
            return list(messages), 0

        # --- Identify boundaries ---
        # First user message is always at index 0 (or we find it).
        first_user_idx = _find_first_user_index(messages)
        if first_user_idx is None:
            # No user message at all — nothing to snip.
            return list(messages), 0

        # Protected tail: the last `keep_last` messages.
        tail_start = max(0, len(messages) - keep_last)

        # The snippable window is (first_user_idx + 1) .. (tail_start - 1).
        # We must only snip complete rounds.
        snip_start = first_user_idx + 1
        if snip_start >= tail_start:
            # Nothing to snip between the first user message and the tail.
            return list(messages), 0

        # --- Walk the snippable window and collect complete rounds ---
        # A complete round is: assistant msg at index i, followed by a user
        # msg at index i+1 whose content contains tool_result blocks (or is
        # a plain user continuation).
        snippable = messages[snip_start:tail_start]
        rounds_to_snip: list[int] = []  # indices into `snippable`, pairs

        i = 0
        while i + 1 < len(snippable):
            msg_a = snippable[i]
            msg_b = snippable[i + 1]
            role_a = _get_role(msg_a)
            role_b = _get_role(msg_b)

            if role_a == "assistant" and role_b == "user":
                # Complete round found.
                rounds_to_snip.append(i)
                rounds_to_snip.append(i + 1)
                i += 2
            else:
                # Not a complete round — stop snipping here to avoid
                # breaking alternation.
                break

        if not rounds_to_snip:
            return list(messages), 0

        # --- Calculate tokens freed ---
        snipped_msgs = [snippable[idx] for idx in rounds_to_snip]
        tokens_freed = sum(self._estimate_single(m) for m in snipped_msgs)

        # --- Build result ---
        # Keep: first user message (with snip marker appended), un-snipped
        # middle, tail.
        kept_middle_start = snip_start + max(rounds_to_snip) + 1
        kept_middle = messages[kept_middle_start:tail_start]
        tail = messages[tail_start:]

        # Build the snip boundary as an annotated first user message.
        # Embedding the marker in the first user message rather than
        # inserting a standalone message avoids breaking role alternation
        # (the next message after the first user is always assistant).
        first_user = messages[first_user_idx]
        marker_text = (
            f"\n\n{_SNIP_MARKER_PREFIX}"
            f"{len(snipped_msgs)} messages removed, "
            f"~{tokens_freed:,} tokens freed.)"
        )

        first_user_text = (
            first_user.content if isinstance(first_user.content, str)
            else first_user.text
        )

        annotated_first = Message(
            role="user",
            content=first_user_text + marker_text,
            id=first_user.id,
            timestamp=first_user.timestamp,
            metadata={
                **first_user.metadata,
                "subtype": "snip_boundary",
                "snipped_count": len(snipped_msgs),
            },
        )

        result: list[Any] = []
        # Everything before the first user message (system msgs etc).
        result.extend(messages[:first_user_idx])
        # Annotated first user message (with snip boundary).
        result.append(annotated_first)
        # Remaining middle that wasn't snipped.
        result.extend(kept_middle)
        # Protected tail.
        result.extend(tail)

        return result, tokens_freed

    def estimate_savings(
        self,
        messages: list[Any],
        keep_last: int | None = None,
    ) -> int:
        """Estimate tokens that snip would free without actually snipping.

        This is the "snip projection" from ADR-060 — lets the system
        decide whether snip alone is sufficient or if model summary is
        also needed.
        """
        if keep_last is None:
            keep_last = self._keep_last

        if len(messages) <= keep_last or len(messages) < 3:
            return 0

        first_user_idx = _find_first_user_index(messages)
        if first_user_idx is None:
            return 0

        tail_start = max(0, len(messages) - keep_last)
        snip_start = first_user_idx + 1
        if snip_start >= tail_start:
            return 0

        snippable = messages[snip_start:tail_start]

        # Walk the snippable window, summing tokens for complete rounds.
        total = 0
        i = 0
        while i + 1 < len(snippable):
            msg_a = snippable[i]
            msg_b = snippable[i + 1]
            role_a = _get_role(msg_a)
            role_b = _get_role(msg_b)

            if role_a == "assistant" and role_b == "user":
                total += self._estimate_single(msg_a)
                total += self._estimate_single(msg_b)
                i += 2
            else:
                break

        return total

    # -- Internals -----------------------------------------------------------

    def _estimate_single(self, msg: Any) -> int:
        text = _serialize_message(msg)
        return len(text) // self._bytes_per_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_role(msg: Any) -> str:
    if isinstance(msg, Message):
        return msg.role
    if isinstance(msg, dict):
        return msg.get("role", "")
    return ""


def _find_first_user_index(messages: list[Any]) -> int | None:
    """Return the index of the first user message, or None."""
    for i, msg in enumerate(messages):
        if _get_role(msg) == "user":
            return i
    return None


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
