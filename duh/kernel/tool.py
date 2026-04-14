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
# Output size limit
# ---------------------------------------------------------------------------

MAX_TOOL_OUTPUT = 100_000  # 100 KB — prevents runaway output from eating context


# ---------------------------------------------------------------------------
# Per-tool timeout defaults (seconds)
# ---------------------------------------------------------------------------

DEFAULT_TIMEOUT = 120  # fallback for unknown tools

TOOL_TIMEOUTS: dict[str, int] = {
    "Bash": 300,       # 5 min for builds/tests
    "Read": 30,        # 30s for file reads
    "Write": 30,
    "Edit": 30,
    "MultiEdit": 60,
    "Glob": 30,
    "Grep": 60,        # 1 min for large codebases
    "WebFetch": 30,
    "WebSearch": 30,
    "Skill": 120,
    "Task": 5,
    "NotebookEdit": 30,
    "MemoryStore": 10,
    "MemoryRecall": 10,
    "HTTP": 60,
    "Database": 30,
}


def get_tool_timeout(tool_name: str) -> int:
    """Return the configured timeout for a tool, or DEFAULT_TIMEOUT."""
    return TOOL_TIMEOUTS.get(tool_name, DEFAULT_TIMEOUT)


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
    sandbox_policy: Any = None  # SandboxPolicy | None


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
        return False  # pragma: no cover - Protocol default, tools override

    @property
    def is_destructive(self) -> bool:
        """True if this tool makes irreversible changes (needs explicit approval)."""
        return False  # pragma: no cover - Protocol default, tools override

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        """Check if the tool is allowed to run with this input.

        Returns {"allowed": True} or {"allowed": False, "reason": "..."}.
        Default: always allowed.
        """
        return {"allowed": True}
