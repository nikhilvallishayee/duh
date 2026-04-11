"""Approval gate adapters — permission checking implementations.

AutoApprover: allows everything (sandbox/bypass mode)
InteractiveApprover: asks the user y/n in the terminal
RuleApprover: deny rules from config (path restrictions, command blocklists)
TieredApprover: 3-tier model (SUGGEST / AUTO_EDIT / FULL_AUTO)
"""

from __future__ import annotations

import sys
import warnings
from enum import Enum
from pathlib import Path
from typing import Any

from duh.kernel.tool_categories import COMMAND_TOOLS, READ_TOOLS, WRITE_TOOLS


class AutoApprover:
    """Allows all tool calls without prompting. For sandboxed environments."""

    async def check(self, tool_name: str, input: dict[str, Any]) -> dict[str, Any]:
        return {"allowed": True}


class InteractiveApprover:
    """Asks the user for permission before tool execution."""

    def __init__(self, *, default_allow: bool = False):
        self._default_allow = default_allow

    async def check(self, tool_name: str, tool_input: dict[str, Any]) -> dict[str, Any]:
        import builtins

        # Format input summary
        summary = ", ".join(f"{k}={v!r}" for k, v in list(tool_input.items())[:3])
        if len(summary) > 120:
            summary = summary[:117] + "..."

        # Show prompt
        sys.stderr.write(f"\n  Tool: {tool_name}\n")
        if summary:
            sys.stderr.write(f"  Input: {summary}\n")
        sys.stderr.write("  Allow? [y/n] ")
        sys.stderr.flush()

        try:
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

        # Check path restrictions (resolve symlinks and .. to prevent traversal)
        if self._allowed_paths is not None:
            from pathlib import Path as _Path
            resolved_allowed = [str(_Path(p).resolve()) for p in self._allowed_paths]
            for key in ("path", "file_path"):
                path = input.get(key)
                if path:
                    resolved = str(_Path(path).resolve())
                    if not any(resolved.startswith(a) for a in resolved_allowed):
                        return {"allowed": False, "reason": f"Path '{path}' outside allowed directories"}

        return {"allowed": True}


# ---------------------------------------------------------------------------
# 3-Tier Approval Model (Phase 3: Codex Steals)
# ---------------------------------------------------------------------------


class ApprovalMode(Enum):
    """Three-tier approval model.

    SUGGEST:   Only reads auto-approved. Writes and commands need human approval.
    AUTO_EDIT: Reads + writes auto-approved. Commands (Bash, WebFetch) need approval.
    FULL_AUTO: Everything auto-approved. Use only in sandboxed environments.
    """
    SUGGEST = "suggest"
    AUTO_EDIT = "auto-edit"
    FULL_AUTO = "full-auto"


# Tool classification aliases (imported from duh.kernel.tool_categories)
_READ_TOOLS = READ_TOOLS
_WRITE_TOOLS = WRITE_TOOLS
_COMMAND_TOOLS = COMMAND_TOOLS


def _is_git_repo(cwd: str) -> bool:
    """Check if the given directory is inside a git repository."""
    current = Path(cwd).resolve()
    for _ in range(100):
        if (current / ".git").exists():
            return True
        parent = current.parent
        if parent == current:
            break
        current = parent
    return False


class TieredApprover:
    """3-tier approval gate: SUGGEST / AUTO_EDIT / FULL_AUTO.

    Tool calls are classified into three tiers:
        Read:    Read, Glob, Grep, ToolSearch, WebSearch, MemoryRecall, Skill
        Write:   Write, Edit, MultiEdit, NotebookEdit, worktree tools, MemoryStore
        Command: Bash, WebFetch, Task, HTTP, Database, Docker, GitHub

    Approval behavior per mode:
        SUGGEST:   Read auto-approved; Write and Command need approval
        AUTO_EDIT: Read and Write auto-approved; Command needs approval
        FULL_AUTO: Everything auto-approved

    On construction, warns if mode is AUTO_EDIT or FULL_AUTO and cwd is
    not inside a git repo (safety net for recovering from bad edits).
    """

    def __init__(
        self,
        mode: ApprovalMode = ApprovalMode.SUGGEST,
        cwd: str | None = None,
    ):
        self._mode = mode

        # Git safety check for permissive modes
        if mode in (ApprovalMode.AUTO_EDIT, ApprovalMode.FULL_AUTO):
            check_cwd = cwd or "."
            if not _is_git_repo(check_cwd):
                warnings.warn(
                    f"--approval-mode {mode.value} without a git repo is risky. "
                    f"Changes cannot be reverted via git. Consider initializing "
                    f"a git repo first: git init",
                    UserWarning,
                    stacklevel=2,
                )

    @property
    def mode(self) -> ApprovalMode:
        return self._mode

    async def check(self, tool_name: str, input: dict[str, Any]) -> dict[str, Any]:
        """Check if a tool call is approved under the current mode."""
        # FULL_AUTO: approve everything
        if self._mode == ApprovalMode.FULL_AUTO:
            return {"allowed": True}

        # Classify the tool
        if tool_name in _READ_TOOLS:
            # Reads are always auto-approved
            return {"allowed": True}

        if tool_name in _WRITE_TOOLS:
            if self._mode == ApprovalMode.AUTO_EDIT:
                return {"allowed": True}
            # SUGGEST mode: writes need approval
            return {
                "allowed": False,
                "reason": (
                    f"Tool '{tool_name}' requires approval in suggest mode. "
                    f"Use --approval-mode auto-edit to auto-approve file edits."
                ),
            }

        if tool_name in _COMMAND_TOOLS:
            # Both SUGGEST and AUTO_EDIT need approval for commands
            # (FULL_AUTO already returned above)
            return {
                "allowed": False,
                "reason": (
                    f"Tool '{tool_name}' requires approval in {self._mode.value} mode. "
                    f"Use --approval-mode full-auto to auto-approve all operations."
                ),
            }

        # Unknown tool: follow the most restrictive applicable rule
        if self._mode == ApprovalMode.SUGGEST:
            return {
                "allowed": False,
                "reason": f"Unknown tool '{tool_name}' requires approval in suggest mode.",
            }
        # AUTO_EDIT: unknown tools need approval (conservative)
        return {
            "allowed": False,
            "reason": f"Unknown tool '{tool_name}' requires approval in {self._mode.value} mode.",
        }