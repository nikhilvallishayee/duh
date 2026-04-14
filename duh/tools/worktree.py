"""Git worktree management tools for D.U.H.

Two tools for isolated worktree-based development:

- EnterWorktreeTool: creates a git worktree, switches engine cwd into it
- ExitWorktreeTool: restores original cwd, optionally removes the worktree

Worktree state is stored in ToolContext.metadata so other tools can detect
that execution is happening inside a worktree.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult
from duh.security.trifecta import Capability

_WORKTREE_BASE = "/tmp/duh-worktrees"

# Metadata keys stored in ToolContext.metadata
_META_WORKTREE_PATH = "worktree_path"
_META_WORKTREE_BRANCH = "worktree_branch"
_META_WORKTREE_ORIGINAL_CWD = "worktree_original_cwd"
_META_IN_WORKTREE = "in_worktree"


async def _run_git_async(
    args: list[str], cwd: str, timeout: float = 30
) -> tuple[int, str, str]:
    """Run a git command asynchronously. Returns (returncode, stdout, stderr)."""
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        return 1, "", "git command timed out"
    return (
        proc.returncode or 0,
        stdout.decode("utf-8", errors="replace").strip(),
        stderr.decode("utf-8", errors="replace").strip(),
    )


class EnterWorktreeTool:
    """Create a git worktree and switch the engine's working directory into it."""

    name = "EnterWorktree"
    capabilities = Capability.FS_WRITE | Capability.EXEC
    description = (
        "Create a new git worktree with an isolated branch and switch into it. "
        "Stores worktree state in context metadata so other tools are aware."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "branch": {
                "type": "string",
                "description": (
                    "Branch name for the worktree. "
                    "Auto-generates a unique name if omitted."
                ),
            },
            "path": {
                "type": "string",
                "description": (
                    "Filesystem path for the worktree. "
                    "Defaults to /tmp/duh-worktrees/<branch>."
                ),
            },
        },
        "required": [],
    }

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        # Prevent nesting worktrees
        if context.metadata.get(_META_IN_WORKTREE):
            return ToolResult(
                output="Already inside a worktree. Exit the current worktree first.",
                is_error=True,
            )

        cwd = context.cwd if context.cwd and context.cwd != "." else os.getcwd()

        # Verify we're inside a git repo
        rc, out, err = await _run_git_async(
            ["rev-parse", "--is-inside-work-tree"], cwd
        )
        if rc != 0 or out != "true":
            return ToolResult(
                output=f"Not inside a git repository (cwd={cwd})",
                is_error=True,
            )

        branch = input.get("branch") or f"duh-worktree-{uuid.uuid4().hex[:8]}"
        wt_path = input.get("path") or os.path.join(_WORKTREE_BASE, branch)

        # Create parent directory
        Path(wt_path).parent.mkdir(parents=True, exist_ok=True)

        # Create the worktree with a new branch
        rc, out, err = await _run_git_async(
            ["worktree", "add", wt_path, "-b", branch], cwd
        )
        if rc != 0:
            return ToolResult(
                output=f"Failed to create worktree: {err or out}",
                is_error=True,
            )

        # Store worktree state in context metadata
        context.metadata[_META_WORKTREE_PATH] = wt_path
        context.metadata[_META_WORKTREE_BRANCH] = branch
        context.metadata[_META_WORKTREE_ORIGINAL_CWD] = cwd
        context.metadata[_META_IN_WORKTREE] = True

        # Switch engine working directory
        context.cwd = wt_path

        return ToolResult(
            output=f"Created worktree at {wt_path} on branch '{branch}'",
            metadata={
                "worktree_path": wt_path,
                "branch": branch,
                "original_cwd": cwd,
            },
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}


class ExitWorktreeTool:
    """Leave a git worktree and restore the original working directory."""

    name = "ExitWorktree"
    capabilities = Capability.FS_WRITE | Capability.EXEC
    description = (
        "Restore the original working directory and optionally remove the worktree. "
        "Only works when inside a worktree created by EnterWorktree."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "cleanup": {
                "type": "boolean",
                "description": (
                    "Remove the worktree after exiting. Defaults to true."
                ),
                "default": True,
            },
        },
        "required": [],
    }

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        if not context.metadata.get(_META_IN_WORKTREE):
            return ToolResult(
                output="Not currently inside a worktree.",
                is_error=True,
            )

        wt_path = context.metadata.get(_META_WORKTREE_PATH, "")
        branch = context.metadata.get(_META_WORKTREE_BRANCH, "")
        original_cwd = context.metadata.get(_META_WORKTREE_ORIGINAL_CWD, ".")

        # Restore the original working directory
        context.cwd = original_cwd

        cleanup = input.get("cleanup", True)
        removed = False

        if cleanup and wt_path:
            rc, out, err = await _run_git_async(
                ["worktree", "remove", wt_path, "--force"], original_cwd
            )
            if rc == 0:
                removed = True
            else:
                # Non-fatal: we still exit the worktree, just can't clean up
                pass

        # Clear worktree state
        context.metadata.pop(_META_WORKTREE_PATH, None)
        context.metadata.pop(_META_WORKTREE_BRANCH, None)
        context.metadata.pop(_META_WORKTREE_ORIGINAL_CWD, None)
        context.metadata.pop(_META_IN_WORKTREE, None)

        parts = [f"Exited worktree. Restored cwd to {original_cwd}"]
        if cleanup:
            if removed:
                parts.append(f"Removed worktree at {wt_path}")
            else:
                parts.append(f"Could not remove worktree at {wt_path}")

        return ToolResult(
            output=". ".join(parts),
            metadata={
                "original_cwd": original_cwd,
                "worktree_path": wt_path,
                "branch": branch,
                "cleaned_up": removed,
            },
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
