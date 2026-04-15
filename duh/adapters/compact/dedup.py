"""Tier 1: Dedup + Image Strip — deterministic, no model call.

Removes duplicate file reads (same file read multiple times, keep latest),
removes redundant tool results (same tool + same input, keep latest),
and strips image blocks from messages older than keep_recent.

    from duh.adapters.compact.dedup import DedupCompactor

    dc = DedupCompactor(keep_recent_images=3)
    compacted = await dc.compact(messages, token_limit=100_000)
"""

from __future__ import annotations

from typing import Any

from duh.adapters.simple_compactor import (
    _deduplicate_messages,
    strip_images,
    _serialize_message,
)


class DedupCompactor:
    """Tier 1 compactor — deduplication and image stripping.

    Satisfies the CompactionStrategy protocol.
    Delegates to the existing SimpleCompactor helpers for dedup and
    image stripping.
    """

    def __init__(
        self,
        keep_recent_images: int = 3,
        bytes_per_token: int = 4,
    ):
        self._keep_recent_images = keep_recent_images
        self._bytes_per_token = bytes_per_token

    def estimate_tokens(self, messages: list[Any]) -> int:
        """Estimate token count for messages."""
        total = 0
        for msg in messages:
            text = _serialize_message(msg)
            total += len(text) // self._bytes_per_token
        return total

    async def compact(
        self,
        messages: list[Any],
        token_limit: int = 0,
    ) -> list[Any]:
        """Deduplicate and strip images.

        1. Remove duplicate file reads and redundant tool results.
        2. Strip images from old messages (keep_recent_images most recent).
        """
        if not messages:
            return []

        # Stage 1a: Deduplicate
        deduped = _deduplicate_messages(messages)

        # Stage 1b: Strip images from old messages
        stripped = strip_images(deduped, keep_recent=self._keep_recent_images)

        return stripped
