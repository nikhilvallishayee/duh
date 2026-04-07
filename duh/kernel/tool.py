"""Tool protocol — the contract every tool implements.

Deliberately simpler than typical 30-method interfaces.
A tool needs: a name, a schema, a call method, and a safety classification.
Everything else is optional.

    class ReadFile(Tool):
        name = "Read"
        description = "Read a file from disk"
        input_schema = {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}

        async def call(self, input: dict, context: ToolContext) -> ToolResult:
            content = Path(input["path"]).read_text()
            return ToolResult(output=content)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Tool result
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    """What a tool returns after execution."""
    output: str | list[Any] = ""
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tool context (passed to call)
# ---------------------------------------------------------------------------

@dataclass
class ToolContext:
    """Runtime context available to tools during execution."""
    cwd: str = "."
    tool_use_id: str = ""
    abort_signal: Any = None
    permissions: Any = None  # ApprovalGate adapter
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Tool protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class Tool(Protocol):
    """The contract every tool implements.

    Required: name, description, input_schema, call.
    Optional: is_read_only, is_destructive, check_permissions.
    """

    name: str
    description: str
    input_schema: dict[str, Any]

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        """Execute the tool with the given input."""
        ...

    @property
    def is_read_only(self) -> bool:
        """True if this tool only reads (safe for concurrent execution)."""
        return False

    @property
    def is_destructive(self) -> bool:
        """True if this tool makes irreversible changes (needs explicit approval)."""
        return False

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        """Check if the tool is allowed to run with this input.

        Returns {"allowed": True} or {"allowed": False, "reason": "..."}.
        Default: always allowed.
        """
        return {"allowed": True}
