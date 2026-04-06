"""ApprovalGate port — how D.U.H. checks tool permissions.

Called before every tool execution. Returns allow/deny with reason.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ApprovalGate(Protocol):
    """Abstract interface for tool approval."""

    async def check(
        self,
        tool_name: str,
        input: dict[str, Any],
    ) -> dict[str, Any]:
        """Check if a tool call is approved.

        Returns:
            {"allowed": True} — proceed with execution
            {"allowed": False, "reason": "..."} — deny with explanation
        """
        ...
