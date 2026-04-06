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


ContentBlock = TextBlock | ToolUseBlock | ToolResultBlock | ThinkingBlock | dict[str, Any]


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
