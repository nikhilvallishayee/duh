"""Model-call compactor -- uses the model to summarize conversation context.

Unlike SimpleCompactor which does deterministic text truncation, this adapter
calls the model to produce an intelligent summary of older messages. Falls
back to SimpleCompactor behavior when the model is unavailable.

    compactor = ModelCompactor(call_model=provider.stream)
    compacted = await compactor.compact(messages, token_limit=100_000)
"""

from __future__ import annotations

import json
import logging
from typing import Any

from duh.kernel.messages import Message
from duh.adapters.simple_compactor import SimpleCompactor

logger = logging.getLogger(__name__)

_SUMMARIZE_PROMPT = (
    "Summarize the following conversation context concisely. "
    "Preserve key decisions, file paths, tool results, and any "
    "instructions that are still relevant. Output only the summary."
)


class ModelCompactor:
    """Compactor that uses a model call to produce intelligent summaries.

    Implements the same interface as SimpleCompactor (ContextManager port).
    When the model call fails, falls back to SimpleCompactor's truncation.
    """

    def __init__(
        self,
        call_model: Any = None,
        default_limit: int = 100_000,
        bytes_per_token: int = 4,
        min_keep: int = 2,
    ):
        self._call_model = call_model
        self._simple = SimpleCompactor(
            default_limit=default_limit,
            bytes_per_token=bytes_per_token,
            min_keep=min_keep,
        )

    def estimate_tokens(self, messages: list[Any]) -> int:
        """Estimate token count (delegates to SimpleCompactor)."""
        return self._simple.estimate_tokens(messages)

    async def compact(
        self,
        messages: list[Any],
        token_limit: int = 0,
    ) -> list[Any]:
        """Compact messages using model-generated summary.

        Strategy:
        1. If messages fit within limit, return as-is.
        2. Partition into system messages, droppable, and kept (tail window).
        3. Call the model to summarize the droppable messages.
        4. Return: system + summary + kept.
        5. On model failure, fall back to SimpleCompactor.
        """
        limit = token_limit or self._simple.default_limit

        if not messages:
            return []

        # Check if compaction is needed
        total_tokens = self.estimate_tokens(messages)
        if total_tokens <= limit:
            return list(messages)

        # If no model available, fall back to simple
        if not self._call_model:
            return await self._simple.compact(messages, token_limit=limit)

        # Partition: system vs. conversation
        system_msgs: list[Any] = []
        conversation: list[Any] = []
        for msg in messages:
            role = msg.role if isinstance(msg, Message) else msg.get("role", "")
            if role == "system":
                system_msgs.append(msg)
            else:
                conversation.append(msg)

        if not conversation:
            return list(system_msgs)

        # Determine tail window (walk backward)
        system_tokens = self._simple.estimate_tokens(system_msgs)
        budget = max(0, limit - system_tokens)

        kept: list[Any] = []
        used = 0
        for msg in reversed(conversation):
            msg_tokens = self._simple._estimate_single(msg)
            if used + msg_tokens > budget and len(kept) >= self._simple.min_keep:
                break
            kept.append(msg)
            used += msg_tokens
        kept.reverse()

        dropped_count = len(conversation) - len(kept)
        if dropped_count <= 0:
            return system_msgs + kept

        dropped = conversation[:dropped_count]

        # Try model-generated summary
        try:
            summary_text = await self._generate_summary(dropped)
        except Exception:
            logger.debug(
                "Model summary failed, falling back to simple compaction",
                exc_info=True,
            )
            return await self._simple.compact(messages, token_limit=limit)

        summary_msg = Message(
            role="system",
            content=f"Previous conversation summary:\n{summary_text}",
        )
        return system_msgs + [summary_msg] + kept

    async def _generate_summary(self, messages: list[Any]) -> str:
        """Call the model to summarize a list of messages."""
        # Build a text representation of messages to summarize
        parts: list[str] = []
        for msg in messages:
            if isinstance(msg, Message):
                role = msg.role
                text = msg.text if hasattr(msg, "text") else str(msg.content)
            else:
                role = msg.get("role", "?")
                content = msg.get("content", "")
                text = (
                    content
                    if isinstance(content, str)
                    else json.dumps(content, default=str)
                )

            # Truncate individual messages
            if len(text) > 500:
                text = text[:497] + "..."
            parts.append(f"[{role}] {text}")

        conversation_text = "\n".join(parts)
        # Cap total input to avoid recursive prompt-too-long
        if len(conversation_text) > 10_000:
            conversation_text = conversation_text[:10_000] + "\n... (truncated)"

        summary_parts: list[str] = []
        async for event in self._call_model(
            messages=[
                Message(
                    role="user",
                    content=f"{_SUMMARIZE_PROMPT}\n\n{conversation_text}",
                )
            ],
            system_prompt="You are a concise summarizer. Output only the summary.",
            model="",  # Use default model
        ):
            if isinstance(event, dict):
                if event.get("type") == "text_delta":
                    summary_parts.append(event.get("text", ""))

        return (
            "".join(summary_parts)
            or "Conversation context (summary unavailable)."
        )
