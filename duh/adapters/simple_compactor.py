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

from duh.kernel.messages import Message, TextBlock, ToolUseBlock


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
        0. Deduplicate: remove redundant file reads and tool results.
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

        # Step 0: remove duplicate file reads and redundant tool results
        messages = _deduplicate_messages(messages)

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

    async def partial_compact(
        self,
        messages: list[Any],
        from_idx: int,
        to_idx: int,
        token_limit: int = 0,
    ) -> list[Any]:
        """Compact only messages in the range [from_idx, to_idx).

        Messages before from_idx and from to_idx onward are kept intact.
        The messages in the range are summarized into a single system message.

        Args:
            messages: Full message list.
            from_idx: Start of range to compact (inclusive).
            to_idx: End of range to compact (exclusive).
            token_limit: Token budget for the summary (0 = use default).

        Returns a new list (does not mutate the input).
        Raises ValueError if from_idx > to_idx.
        """
        if from_idx > to_idx:
            raise ValueError(
                f"from_idx ({from_idx}) must be <= to_idx ({to_idx})"
            )

        # Clamp to_idx to message length
        to_idx = min(to_idx, len(messages))

        if from_idx == to_idx:
            return list(messages)

        before = list(messages[:from_idx])
        to_compact = list(messages[from_idx:to_idx])
        after = list(messages[to_idx:])

        if not to_compact:
            return before + after

        summary_text = _summarize_messages(to_compact)
        summary_msg = Message(role="system", content=summary_text)

        return before + [summary_msg] + after


# ---------------------------------------------------------------------------
# Deduplication helpers
# ---------------------------------------------------------------------------

# Tool names that read files — duplicates of these are safe to collapse.
_FILE_READ_TOOLS = frozenset({"Read", "read", "cat", "ReadFile", "read_file"})


def _extract_tool_uses(msg: Any) -> list[dict[str, Any]]:
    """Extract tool_use entries from a message's content blocks.

    Returns a list of dicts with at least {id, name, input, type}.
    Works for both Message objects and plain dicts.
    """
    content = msg.content if isinstance(msg, Message) else msg.get("content", "")
    if isinstance(content, str):
        return []
    results: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, ToolUseBlock):
            results.append(
                {"id": block.id, "name": block.name,
                 "input": block.input, "type": "tool_use"}
            )
        elif isinstance(block, dict) and block.get("type") == "tool_use":
            results.append(block)
    return results


def _extract_tool_results(msg: Any) -> list[dict[str, Any]]:
    """Extract tool_result entries from a message's content blocks."""
    content = msg.content if isinstance(msg, Message) else msg.get("content", "")
    if isinstance(content, str):
        return []
    results: list[dict[str, Any]] = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            results.append(block)
    return results


def _tool_use_signature(name: str, tool_input: Any) -> str:
    """Return a hashable signature for a tool call (name + canonical input)."""
    return f"{name}::{json.dumps(tool_input, sort_keys=True, default=str)}"


def _deduplicate_messages(messages: list[Any]) -> list[Any]:
    """Remove duplicate file reads and redundant tool results.

    Two deduplication passes (both backward so "latest wins"):

    1. **Duplicate file reads**: When the same file-reading tool (Read, cat,
       etc.) is called multiple times with identical input, keep only the
       *latest* tool_use block and its corresponding tool_result. Earlier
       read pairs are stripped from their respective messages.

    2. **Redundant tool results**: When *any* tool is called again later with
       the same (name, input), the earlier tool_result is stale. Strip it
       (and its originating tool_use block) from the conversation.

    Messages that become empty after block removal are dropped entirely.
    System messages are never touched.
    """
    if not messages:
        return []

    # --- Pass 1: Build a map of tool_use_id → (name, input, msg_index) ---
    #     by scanning all assistant messages for tool_use blocks.
    tool_use_info: dict[str, tuple[str, Any, int]] = {}  # id → (name, input, idx)
    for idx, msg in enumerate(messages):
        for tu in _extract_tool_uses(msg):
            tool_use_info[tu["id"]] = (tu["name"], tu.get("input", {}), idx)

    # --- Pass 2: Walk backward, track which (name, input) we've seen. ---
    #     The latest occurrence wins; earlier ones are marked for removal.
    seen_signatures: set[str] = set()
    stale_tool_use_ids: set[str] = set()  # tool_use IDs to remove

    for idx in range(len(messages) - 1, -1, -1):
        msg = messages[idx]
        for tu in reversed(_extract_tool_uses(msg)):
            sig = _tool_use_signature(tu["name"], tu.get("input", {}))
            if sig in seen_signatures:
                # This is a duplicate — mark for removal
                stale_tool_use_ids.add(tu["id"])
            else:
                seen_signatures.add(sig)

    if not stale_tool_use_ids:
        return list(messages)  # nothing to do — return a copy

    # --- Pass 3: Rebuild messages, stripping stale blocks. ---
    result: list[Any] = []
    for msg in messages:
        role = _get_role(msg)
        if role == "system":
            result.append(msg)
            continue

        # Get the content blocks
        if isinstance(msg, Message):
            content = msg.content
        elif isinstance(msg, dict):
            content = msg.get("content", "")
        else:
            result.append(msg)
            continue

        if isinstance(content, str):
            result.append(msg)
            continue

        # Filter out stale tool_use and their matching tool_result blocks
        new_blocks: list[Any] = []
        for block in content:
            # Check tool_use blocks
            if isinstance(block, ToolUseBlock):
                if block.id in stale_tool_use_ids:
                    continue
            elif isinstance(block, dict) and block.get("type") == "tool_use":
                if block.get("id") in stale_tool_use_ids:
                    continue
            # Check tool_result blocks
            elif isinstance(block, dict) and block.get("type") == "tool_result":
                if block.get("tool_use_id") in stale_tool_use_ids:
                    continue
            new_blocks.append(block)

        # Drop messages that became empty after block removal
        if not new_blocks:
            continue

        # Rebuild the message with filtered content
        if isinstance(msg, Message):
            result.append(
                Message(
                    role=msg.role,
                    content=new_blocks,
                    id=msg.id,
                    timestamp=msg.timestamp,
                    metadata=msg.metadata,
                )
            )
        elif isinstance(msg, dict):
            new_msg = dict(msg)
            new_msg["content"] = new_blocks
            result.append(new_msg)
        else:
            result.append(msg)

    return result


# ---------------------------------------------------------------------------
# Image stripping (pre-compaction)
# ---------------------------------------------------------------------------

def strip_images(messages: list[Any]) -> list[Any]:
    """Replace image content blocks with text placeholders.

    Image blocks (type="image") are replaced with
    ``[image removed for compaction]`` to prevent prompt-too-long
    errors during the compaction summarization call.

    Returns a new list (does not mutate the input).
    """
    result: list[Any] = []
    for msg in messages:
        if isinstance(msg, Message):
            content = msg.content
            if isinstance(content, str):
                result.append(msg)
                continue
            new_blocks: list[Any] = []
            changed = False
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    new_blocks.append(TextBlock(text="[image removed for compaction]"))
                    changed = True
                else:
                    new_blocks.append(block)
            if changed:
                result.append(Message(
                    role=msg.role,
                    content=new_blocks,
                    id=msg.id,
                    timestamp=msg.timestamp,
                    metadata=msg.metadata,
                ))
            else:
                result.append(msg)
        elif isinstance(msg, dict):
            content = msg.get("content", "")
            if isinstance(content, str):
                result.append(msg)
                continue
            new_blocks_d: list[Any] = []
            changed_d = False
            for block in content:
                if isinstance(block, dict) and block.get("type") == "image":
                    new_blocks_d.append({"type": "text", "text": "[image removed for compaction]"})
                    changed_d = True
                else:
                    new_blocks_d.append(block)
            if changed_d:
                new_msg = dict(msg)
                new_msg["content"] = new_blocks_d
                result.append(new_msg)
            else:
                result.append(msg)
        else:
            result.append(msg)
    return result


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
