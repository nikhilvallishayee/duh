"""WriteTool — write content to a file, creating parent dirs as needed."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from duh.kernel.git_context import _run_git_async
from duh.kernel.tool import ToolContext, ToolResult
from duh.security.trifecta import Capability

# Maximum content size for writing (50 MB).
MAX_FILE_WRITE_BYTES = 50 * 1024 * 1024  # 50 MB


class WriteTool:
    """Write content to a file. Creates parent directories if they don't exist."""

    name = "Write"

    def __init__(
        self,
        *,
        path_policy: "PathPolicy | None" = None,
    ) -> None:
        from duh.security.path_policy import PathPolicy  # noqa: F811
        self._path_policy: PathPolicy | None = path_policy
    capabilities = Capability.FS_WRITE
    description = "Write content to a file. Creates parent directories as needed."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to write.",
            },
            "content": {
                "type": "string",
                "description": "The content to write to the file.",
            },
        },
        "required": ["file_path", "content"],
    }

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return True

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        file_path = input.get("file_path", "")
        content = input.get("content", "")

        if len(content.encode("utf-8", errors="replace")) > MAX_FILE_WRITE_BYTES:
            return ToolResult(
                output=(
                    f"Content too large ({len(content):,} chars, limit ~{MAX_FILE_WRITE_BYTES // 1024 // 1024}MB)."
                    " Split into smaller writes."
                ),
                is_error=True,
            )

        if not file_path:
            return ToolResult(output="file_path is required", is_error=True)

        path = Path(file_path)
        if not path.is_absolute():
            path = Path(context.cwd) / path

        # Resolve symlinks BEFORE any boundary or existence checks (SEC-CRITICAL-1).
        # Without this, a symlink like project/link -> /etc/shadow bypasses policy.
        path = path.resolve()

        # Filesystem boundary check
        if self._path_policy is not None:
            allowed, reason = self._path_policy.check(str(path))
            if not allowed:
                return ToolResult(output=reason, is_error=True)

        # Permission checks before attempting write
        parent = path.parent
        if parent.exists() and not os.access(parent, os.W_OK):
            return ToolResult(
                output=f"Permission denied: cannot write to {file_path}",
                is_error=True,
            )
        if path.exists() and not os.access(path, os.W_OK):
            return ToolResult(
                output=f"Permission denied: cannot write to {file_path}",
                is_error=True,
            )

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except Exception as exc:
            return ToolResult(output=f"Error writing file: {exc}", is_error=True)

        # Check git dirty state for the file's directory (async, non-blocking)
        git_dirty = bool(await _run_git_async(["status", "--short"], str(path.parent)))

        return ToolResult(
            output=f"Wrote {len(content)} bytes to {file_path}",
            metadata={"bytes_written": len(content), "git_dirty": git_dirty},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
