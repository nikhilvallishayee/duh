"""Tier 2: Tail-Window + Model Summary.

Keeps the N most recent messages that fit within the token budget and
summarizes discarded messages.  If a model is available, uses it for
intelligent summarization; otherwise falls back to mechanical
concatenation.  Applies post-compact restoration to re-inject recently
accessed files.

    from duh.adapters.compact.summarize import SummarizeCompactor

    sc = SummarizeCompactor(call_model=provider.stream)
    compacted = await sc.compact(messages, token_limit=100_000)
"""

from __future__ import annotations

import json
from typing import Any

from duh.adapters.compact.handoff import HANDOFF_PROMPT
from duh.kernel.messages import Message

# Post-restoration limits (ADR-056)
POST_COMPACT_MAX_FILES: int = 5
POST_COMPACT_TOKEN_BUDGET: int = 50_000


class SummarizeCompactor:
    """Tier 2 compactor — tail-window with model-backed summarization.

    Satisfies the CompactionStrategy protocol.
    """

    def __init__(
        self,
        call_model: Any = None,
        bytes_per_token: int = 4,
        min_keep: int = 2,
        file_tracker: Any = None,
        skill_context: str | None = None,
    ):
        self._call_model = call_model
        self._bytes_per_token = bytes_per_token
        self._min_keep = min_keep
        self._file_tracker = file_tracker
        self._skill_context = skill_context

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
        """Tail-window compaction with summarization and post-restoration.

        1. Partition into system and conversation messages.
        2. Walk backward to find messages that fit within budget.
        3. Summarize dropped messages (model or mechanical).
        4. Re-inject recently accessed files (post-restoration).
        """
        if not messages:
            return []

        limit = token_limit or 100_000

        # Partition
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

        system_tokens = self.estimate_tokens(system_msgs)
        budget = max(0, limit - system_tokens)

        # Tail-window: walk backward
        kept: list[Any] = []
        used = 0
        for msg in reversed(conversation):
            msg_tokens = self._estimate_single(msg)
            if used + msg_tokens > budget and len(kept) >= self._min_keep:
                break
            kept.append(msg)
            used += msg_tokens
        kept.reverse()

        dropped_count = len(conversation) - len(kept)

        if dropped_count > 0:
            dropped = conversation[:dropped_count]
            # Try model summary, fall back to mechanical
            summary_text = await self._summarize(dropped)
            summary_msg = Message(
                role="system",
                content=f"Previous conversation summary:\n{summary_text}",
            )
            result = system_msgs + [summary_msg] + kept
        else:
            result = system_msgs + kept

        # Post-compact restoration
        result = _restore_context(
            result,
            file_tracker=self._file_tracker,
            skill_context=self._skill_context,
        )

        return result

    async def _summarize(self, messages: list[Any]) -> str:
        """Summarize dropped messages using model or mechanical fallback."""
        if self._call_model:
            try:
                return await self._model_summarize(messages)
            except Exception:
                pass  # fall through to mechanical

        return _mechanical_summarize(messages)

    async def _model_summarize(self, messages: list[Any]) -> str:
        """Use the model to generate a summary."""
        parts: list[str] = []
        for msg in messages:
            role = _get_role(msg) or "unknown"
            text = _serialize_message(msg).strip()
            if len(text) > 500:
                text = text[:497] + "..."
            parts.append(f"[{role}] {text}")

        conversation_text = "\n".join(parts)
        if len(conversation_text) > 10_000:
            conversation_text = conversation_text[:10_000] + "\n... (truncated)"

        prompt = f"{HANDOFF_PROMPT}\n\n{conversation_text}"

        summary_parts: list[str] = []
        async for event in self._call_model(
            messages=[Message(role="user", content=prompt)],
            system_prompt="You produce structured handoff summaries. Use the section format exactly as requested.",
            model="",
        ):
            if isinstance(event, dict) and event.get("type") == "text_delta":
                summary_parts.append(event.get("text", ""))

        return "".join(summary_parts) or "Conversation context (summary unavailable)."

    def _estimate_single(self, msg: Any) -> int:
        text = _serialize_message(msg)
        return len(text) // self._bytes_per_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SUMMARY_MAX_CHARS = 2000


def _mechanical_summarize(messages: list[Any]) -> str:
    """Deterministic summary by concatenating message texts with role labels."""
    parts: list[str] = []
    for msg in messages:
        role = _get_role(msg) or "unknown"
        text = _serialize_message(msg).strip()
        if not text:
            continue
        if len(text) > 300:
            text = text[:297] + "..."
        parts.append(f"[{role}] {text}")

    combined = "\n".join(parts)
    if len(combined) > _SUMMARY_MAX_CHARS:
        combined = combined[:_SUMMARY_MAX_CHARS - 3] + "..."

    return combined


def _restore_context(
    messages: list[Any],
    *,
    file_tracker: Any = None,
    skill_context: str | None = None,
    token_budget: int = POST_COMPACT_TOKEN_BUDGET,
) -> list[Any]:
    """Re-inject recently accessed files and active skills after compaction."""
    parts: list[str] = []

    if file_tracker is not None:
        ops = file_tracker.ops if hasattr(file_tracker, "ops") else []
        if ops:
            seen: set[str] = set()
            recent_paths: list[str] = []
            for op in reversed(ops):
                path = op.path if hasattr(op, "path") else str(op)
                if path not in seen:
                    seen.add(path)
                    recent_paths.append(path)
                if len(recent_paths) >= POST_COMPACT_MAX_FILES:
                    break

            if recent_paths:
                file_section = "Recently accessed files:\n"
                file_section += "\n".join(f"- {p}" for p in recent_paths)
                parts.append(file_section)

    if skill_context and skill_context.strip():
        parts.append(f"Active context:\n{skill_context.strip()}")

    if not parts:
        return list(messages)

    combined = "\n\n".join(parts)
    max_chars = token_budget * 4
    if len(combined) > max_chars:
        combined = combined[:max_chars - 3] + "..."

    restore_msg = Message(
        role="system",
        content=f"[Post-compaction context restoration]\n{combined}",
    )

    return list(messages) + [restore_msg]


def _get_role(msg: Any) -> str:
    if isinstance(msg, Message):
        return msg.role
    if isinstance(msg, dict):
        return msg.get("role", "")
    return ""


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
