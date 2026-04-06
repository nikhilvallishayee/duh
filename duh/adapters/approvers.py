"""Approval gate adapters — permission checking implementations.

AutoApprover: allows everything (sandbox/bypass mode)
InteractiveApprover: asks the user y/n in the terminal
RuleApprover: deny rules from config (path restrictions, command blocklists)
"""

from __future__ import annotations

import sys
from typing import Any


class AutoApprover:
    """Allows all tool calls without prompting. For sandboxed environments."""

    async def check(self, tool_name: str, input: dict[str, Any]) -> dict[str, Any]:
        return {"allowed": True}


class InteractiveApprover:
    """Asks the user for permission before tool execution."""

    def __init__(self, *, default_allow: bool = False):
        self._default_allow = default_allow

    async def check(self, tool_name: str, input: dict[str, Any]) -> dict[str, Any]:
        # Format input summary
        summary = ", ".join(f"{k}={v!r}" for k, v in list(input.items())[:3])
        if len(summary) > 120:
            summary = summary[:117] + "..."

        # Show prompt
        sys.stderr.write(f"\n  Tool: {tool_name}\n")
        if summary:
            sys.stderr.write(f"  Input: {summary}\n")
        sys.stderr.write("  Allow? [y/n] ")
        sys.stderr.flush()

        try:
            response = input("").strip().lower() if not hasattr(input, '__call__') else ""
            # If input is a dict (parameter shadowing), use builtins
            import builtins
            response = builtins.input("").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return {"allowed": False, "reason": "User cancelled"}

        if response in ("y", "yes", ""):
            return {"allowed": True}
        return {"allowed": False, "reason": "User denied"}


class RuleApprover:
    """Checks tool calls against configurable deny rules.

    Rules can deny by tool name, by input patterns, or by path restrictions.
    """

    def __init__(
        self,
        *,
        denied_tools: set[str] | None = None,
        denied_commands: set[str] | None = None,
        allowed_paths: list[str] | None = None,
    ):
        self._denied_tools = denied_tools or set()
        self._denied_commands = denied_commands or set()
        self._allowed_paths = allowed_paths

    async def check(self, tool_name: str, input: dict[str, Any]) -> dict[str, Any]:
        # Check denied tools
        if tool_name in self._denied_tools:
            return {"allowed": False, "reason": f"Tool '{tool_name}' is denied by policy"}

        # Check denied commands (for Bash tool)
        if tool_name == "Bash":
            cmd = input.get("command", "")
            for denied in self._denied_commands:
                if denied in cmd:
                    return {"allowed": False, "reason": f"Command contains denied pattern: {denied}"}

        # Check path restrictions
        if self._allowed_paths is not None:
            for key in ("path", "file_path"):
                path = input.get(key)
                if path and not any(path.startswith(p) for p in self._allowed_paths):
                    return {"allowed": False, "reason": f"Path '{path}' outside allowed directories"}

        return {"allowed": True}
