"""Message data model — the lingua franca of D.U.H.

Every message flowing through the kernel is one of these types.
Provider adapters translate to/from these. Tools produce these.
The UI renders these. Sessions persist these.

Deliberately minimal. No provider-specific fields leak into the kernel.
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Content blocks (what messages carry)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TextBlock:
    """A block of text content."""
    text: str
    type: str = "text"


@dataclass(frozen=True)
class ToolUseBlock:
    """A request to use a tool."""
    id: str
    name: str
    input: dict[str, Any]
    type: str = "tool_use"


@dataclass(frozen=True)
class ToolResultBlock:
    """The result of a tool execution."""
    tool_use_id: str
    content: str | list[Any]
    is_error: bool = False
    type: str = "tool_result"


@dataclass(frozen=True)
class ThinkingBlock:
    """A thinking/reasoning block (extended thinking)."""
    thinking: str
    type: str = "thinking"


@dataclass(frozen=True)
class ImageBlock:
    """An image content block (base64-encoded)."""
    media_type: str  # image/png, image/jpeg, image/gif, image/webp
    data: str  # base64-encoded image data
    type: str = "image"


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock | ThinkingBlock | ImageBlock | dict[str, Any]


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

@dataclass
class Message:
    """Base message in a conversation turn."""
    role: str  # "user" | "assistant" | "system"
    content: str | list[ContentBlock] = ""
    id: str = field(default_factory=lambda: str(_uuid.uuid4()))
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def text(self) -> str:
        """Extract plain text from content."""
        if isinstance(self.content, str):
            return self.content
        return "".join(
            b.text if isinstance(b, TextBlock)
            else b.get("text", "") if isinstance(b, dict) and b.get("type") == "text"
            else ""
            for b in self.content
        )

    @property
    def tool_use_blocks(self) -> list[ToolUseBlock | dict[str, Any]]:
        """Extract tool_use blocks from content."""
        if isinstance(self.content, str):
            return []
        return [
            b for b in self.content
            if (isinstance(b, ToolUseBlock))
            or (isinstance(b, dict) and b.get("type") == "tool_use")
        ]

    @property
    def has_tool_use(self) -> bool:
        return len(self.tool_use_blocks) > 0


def UserMessage(content: str | list[ContentBlock], **kwargs: Any) -> Message:
    """Create a user message."""
    return Message(role="user", content=content, **kwargs)


def AssistantMessage(content: str | list[ContentBlock], **kwargs: Any) -> Message:
    """Create an assistant message."""
    return Message(role="assistant", content=content, **kwargs)


def SystemMessage(content: str, **kwargs: Any) -> Message:
    """Create a system message."""
    return Message(role="system", content=content, **kwargs)


# ---------------------------------------------------------------------------
# Message validation & repair (API requires strict role alternation)
# ---------------------------------------------------------------------------

def merge_consecutive(messages: list[Message]) -> list[Message]:
    """Merge consecutive messages with the same role into one.

    The Anthropic API returns 400 if two adjacent messages share a role.
    This happens when sessions are resumed from disk where multi-turn
    tool use stored multiple assistant messages in a row.
    """
    if not messages:
        return []
    merged: list[Message] = [messages[0]]
    for msg in messages[1:]:
        if msg.role == merged[-1].role:
            # Merge content — extract text from both
            prev = merged[-1]
            prev_text = prev.content if isinstance(prev.content, str) else prev.text
            new_text = msg.content if isinstance(msg.content, str) else msg.text
            combined = f"{prev_text}\n\n{new_text}".strip()
            if not combined:
                combined = "(continued)"
            merged[-1] = Message(
                role=prev.role,
                content=combined,
                metadata=prev.metadata,
            )
        else:
            merged.append(msg)
    return merged


def validate_alternation(messages: list[Message]) -> list[Message]:
    """Ensure messages strictly alternate user/assistant.

    Inserts synthetic user messages where needed. Merges consecutive
    same-role messages first.
    """
    if not messages:
        return []
    result = merge_consecutive(messages)
    # If first message isn't user, prepend one
    if result[0].role != "user":
        result.insert(0, Message(role="user", content="(resumed session)"))
    # Insert synthetic messages where alternation breaks
    fixed: list[Message] = [result[0]]
    for msg in result[1:]:
        if msg.role == fixed[-1].role:
            # Insert opposite role
            filler_role = "user" if msg.role == "assistant" else "assistant"
            fixed.append(Message(role=filler_role, content="(continued)"))
        fixed.append(msg)
    return fixed
