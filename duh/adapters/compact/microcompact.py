"""Tier 0: Microcompact — clear old tool results for context management.

The cheapest compaction pass: no model call, <1ms.  Replaces the content
of old tool-result blocks with a short placeholder.  Keeps the last N
tool results intact (default 3).

    from duh.adapters.compact.microcompact import MicroCompactor

    mc = MicroCompactor(keep_last=3)
    compacted = await mc.compact(messages, token_limit=100_000)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from duh.kernel.messages import Message, TextBlock, ToolResultBlock

# Tool names whose results are safe to clear.
_CLEARABLE_TOOLS = frozenset({
    "Read", "read", "ReadFile", "read_file", "cat",
    "Bash", "bash",
    "Grep", "grep", "rg",
    "Glob", "glob",
    "WebFetch", "web_fetch", "WebSearch", "web_search",
})

_CLEARED_PLACEHOLDER = "[tool result cleared for context management]"

# If the gap between two assistant messages exceeds this many seconds,
# also clear tool results in between (even if within keep_last window).
_TIME_GAP_SECONDS = 300  # 5 minutes


class MicroCompactor:
    """Tier 0 compactor — clear stale tool results.

    Satisfies the CompactionStrategy protocol.
    """

    def __init__(
        self,
        keep_last: int = 3,
        time_gap_seconds: int = _TIME_GAP_SECONDS,
        bytes_per_token: int = 4,
    ):
        self._keep_last = keep_last
        self._time_gap_seconds = time_gap_seconds
        self._bytes_per_token = bytes_per_token

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
        """Replace old tool results with placeholders.

        Scans backward to find the last N tool-result blocks from clearable
        tools.  All earlier ones are replaced with the placeholder text.
        Also applies time-gap clearing: if a tool result is separated from
        the most recent assistant message by more than time_gap_seconds,
        it is cleared regardless of the keep_last window.
        """
        if not messages:
            return []

        # --- Identify the most recent assistant message timestamp ---
        last_assistant_ts = _find_last_assistant_timestamp(messages)

        # --- Scan backward, track which tool_use_ids to keep ---
        # We keep the last N tool-result blocks from clearable tools.
        keep_tool_result_ids: set[str] = set()
        clearable_count = 0

        for msg in reversed(messages):
            role = _get_role(msg)
            if role != "user":
                continue
            for block in _iter_tool_result_blocks(msg):
                tool_use_id = _get_tool_use_id(block)
                tool_name = _find_tool_name_for_id(messages, tool_use_id)
                if tool_name not in _CLEARABLE_TOOLS:
                    continue
                clearable_count += 1
                if clearable_count <= self._keep_last:
                    # Check time gap
                    if last_assistant_ts and self._time_gap_seconds > 0:
                        msg_ts = _get_timestamp(msg)
                        if msg_ts and _seconds_between(msg_ts, last_assistant_ts) > self._time_gap_seconds:
                            continue  # time-gap: don't keep this one
                    keep_tool_result_ids.add(tool_use_id)

        if not keep_tool_result_ids and clearable_count == 0:
            return list(messages)

        # --- Rebuild messages, replacing cleared tool results ---
        result: list[Any] = []
        for msg in messages:
            role = _get_role(msg)
            if role == "system":
                result.append(msg)
                continue

            content = msg.content if isinstance(msg, Message) else msg.get("content", "")
            if isinstance(content, str):
                result.append(msg)
                continue

            new_blocks: list[Any] = []
            changed = False
            for block in content:
                tool_use_id = _get_tool_use_id(block)
                block_type = _get_block_type(block)

                if block_type == "tool_result" and tool_use_id:
                    tool_name = _find_tool_name_for_id(messages, tool_use_id)
                    if tool_name in _CLEARABLE_TOOLS and tool_use_id not in keep_tool_result_ids:
                        # Replace with placeholder
                        if isinstance(block, ToolResultBlock):
                            new_blocks.append(ToolResultBlock(
                                tool_use_id=tool_use_id,
                                content=_CLEARED_PLACEHOLDER,
                                is_error=False,
                            ))
                        elif isinstance(block, dict):
                            new_blocks.append({
                                "type": "tool_result",
                                "tool_use_id": tool_use_id,
                                "content": _CLEARED_PLACEHOLDER,
                            })
                        changed = True
                        continue

                new_blocks.append(block)

            if changed:
                if isinstance(msg, Message):
                    result.append(Message(
                        role=msg.role,
                        content=new_blocks,
                        id=msg.id,
                        timestamp=msg.timestamp,
                        metadata=msg.metadata,
                    ))
                elif isinstance(msg, dict):
                    new_msg = dict(msg)
                    new_msg["content"] = new_blocks
                    result.append(new_msg)
                else:
                    result.append(msg)
            else:
                result.append(msg)

        return result

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


def _get_timestamp(msg: Any) -> str | None:
    if isinstance(msg, Message):
        return msg.timestamp
    if isinstance(msg, dict):
        return msg.get("timestamp")
    return None


def _get_block_type(block: Any) -> str:
    if isinstance(block, dict):
        return block.get("type", "")
    return getattr(block, "type", "")


def _get_tool_use_id(block: Any) -> str | None:
    """Extract tool_use_id from a tool_result block."""
    if isinstance(block, ToolResultBlock):
        return block.tool_use_id
    if isinstance(block, dict):
        return block.get("tool_use_id")
    return None


def _iter_tool_result_blocks(msg: Any):
    """Yield tool_result blocks from a message."""
    content = msg.content if isinstance(msg, Message) else msg.get("content", "")
    if isinstance(content, str):
        return
    for block in content:
        if _get_block_type(block) == "tool_result":
            yield block


def _find_tool_name_for_id(messages: list[Any], tool_use_id: str | None) -> str:
    """Find the tool name for a given tool_use_id by scanning assistant messages."""
    if not tool_use_id:
        return ""
    for msg in messages:
        role = _get_role(msg)
        if role != "assistant":
            continue
        content = msg.content if isinstance(msg, Message) else msg.get("content", "")
        if isinstance(content, str):
            continue
        for block in content:
            from duh.kernel.messages import ToolUseBlock
            if isinstance(block, ToolUseBlock) and block.id == tool_use_id:
                return block.name
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("id") == tool_use_id:
                return block.get("name", "")
    return ""


def _find_last_assistant_timestamp(messages: list[Any]) -> str | None:
    """Find the timestamp of the most recent assistant message."""
    for msg in reversed(messages):
        if _get_role(msg) == "assistant":
            return _get_timestamp(msg)
    return None


def _seconds_between(ts1: str, ts2: str) -> float:
    """Return the absolute difference in seconds between two ISO timestamps."""
    try:
        dt1 = datetime.fromisoformat(ts1)
        dt2 = datetime.fromisoformat(ts2)
        # Ensure both are UTC-aware
        if dt1.tzinfo is None:
            dt1 = dt1.replace(tzinfo=timezone.utc)
        if dt2.tzinfo is None:
            dt2 = dt2.replace(tzinfo=timezone.utc)
        return abs((dt2 - dt1).total_seconds())
    except (ValueError, TypeError):
        return 0.0


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
