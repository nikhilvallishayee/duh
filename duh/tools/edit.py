"""EditTool — replace old_string with new_string in a file."""

from __future__ import annotations

import difflib
import os
from pathlib import Path
from typing import Any

from duh.kernel.git_context import _run_git_async
from duh.kernel.tool import ToolContext, ToolResult
from duh.security.trifecta import Capability


def _make_diff(old: str, new: str, path: str, *, context: int = 3) -> str:
    """Return a unified diff string comparing *old* and *new* content.

    Uses ``difflib.unified_diff`` with ``--- old/<path>`` / ``+++ new/<path>``
    headers and *context* lines of surrounding context (default 3).
    Returns an empty string when the texts are identical.
    """
    diff_lines = difflib.unified_diff(
        old.splitlines(keepends=True),
        new.splitlines(keepends=True),
        fromfile=f"old/{path}",
        tofile=f"new/{path}",
        n=context,
    )
    return "".join(diff_lines)


class EditTool:
    """Perform an exact string replacement in a file."""

    name = "Edit"

    def __init__(
        self,
        *,
        path_policy: "PathPolicy | None" = None,
    ) -> None:
        from duh.security.path_policy import PathPolicy  # noqa: F811
        self._path_policy: PathPolicy | None = path_policy
    capabilities = Capability.FS_WRITE
    description = (
        "Replace an exact occurrence of old_string with new_string in a file. "
        "Fails if old_string is not found or is not unique."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to edit.",
            },
            "old_string": {
                "type": "string",
                "description": "The exact text to find and replace.",
            },
            "new_string": {
                "type": "string",
                "description": "The replacement text.",
            },
            "replace_all": {
                "type": "boolean",
                "description": "If true, replace all occurrences. Default: false.",
                "default": False,
            },
        },
        "required": ["file_path", "old_string", "new_string"],
    }

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return True

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        file_path = input.get("file_path", "")
        old_string = input.get("old_string", "")
        new_string = input.get("new_string", "")
        replace_all = input.get("replace_all", False)

        if not file_path:
            return ToolResult(output="file_path is required", is_error=True)
        if not old_string:
            return ToolResult(output="old_string is required", is_error=True)

        path = Path(file_path)
        if not path.is_absolute():
            path = Path(context.cwd) / path

        # Filesystem boundary check
        if self._path_policy is not None:
            allowed, reason = self._path_policy.check(str(path))
            if not allowed:
                return ToolResult(output=reason, is_error=True)

        if not path.is_file():
            return ToolResult(
                output=f"File not found: {file_path}", is_error=True
            )
        if not os.access(path, os.R_OK | os.W_OK):
            return ToolResult(
                output=f"Permission denied: cannot edit {file_path} (need read+write)",
                is_error=True,
            )

        try:
            content = path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(output=f"Error reading file: {exc}", is_error=True)

        count = content.count(old_string)
        if count == 0:
            return ToolResult(
                output=f"old_string not found in {file_path}", is_error=True
            )

        if not replace_all and count > 1:
            return ToolResult(
                output=(
                    f"old_string found {count} times in {file_path}. "
                    "Provide more context to make it unique, or set replace_all=true."
                ),
                is_error=True,
            )

        if replace_all:
            new_content = content.replace(old_string, new_string)
        else:
            new_content = content.replace(old_string, new_string, 1)

        try:
            path.write_text(new_content, encoding="utf-8")
        except Exception as exc:
            return ToolResult(output=f"Error writing file: {exc}", is_error=True)

        replacements = count if replace_all else 1

        # Check git dirty state for the file's directory (async, non-blocking)
        git_dirty = bool(await _run_git_async(["status", "--short"], str(path.parent)))

        # Build unified diff showing what changed
        diff = _make_diff(content, new_content, file_path)
        msg = f"Replaced {replacements} occurrence(s) in {file_path}"
        if diff:
            msg = f"{msg}\n{diff}"

        return ToolResult(
            output=msg,
            metadata={"replacements": replacements, "git_dirty": git_dirty},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
