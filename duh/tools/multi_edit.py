"""MultiEditTool — apply multiple edits to one or more files in a single call."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult
from duh.tools.edit import _make_diff
from duh.security.trifecta import Capability


class MultiEditTool:
    """Apply multiple edits to one or more files in a single call.

    Each edit performs an exact string replacement (same logic as EditTool).
    Edits are applied sequentially. If one edit fails, the error is recorded
    but remaining edits continue.
    """

    name = "MultiEdit"
    capabilities = Capability.FS_WRITE
    description = (
        "Apply multiple edits to one or more files in a single call. "
        "Each edit replaces an exact occurrence of old_string with new_string. "
        "More efficient than multiple single Edit calls."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "edits": {
                "type": "array",
                "items": {
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
                    },
                    "required": ["file_path", "old_string", "new_string"],
                },
                "description": "List of edits to apply sequentially.",
            },
        },
        "required": ["edits"],
    }

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return True

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        edits = input.get("edits")
        if edits is None:
            return ToolResult(output="edits is required", is_error=True)
        if not isinstance(edits, list):
            return ToolResult(output="edits must be a list", is_error=True)
        if len(edits) == 0:
            return ToolResult(output="edits list is empty — nothing to do", is_error=True)

        # Upfront permission check: validate all files BEFORE applying any edits
        perm_issues: list[str] = []
        for i, edit in enumerate(edits, start=1):
            fp = edit.get("file_path", "")
            if not fp:
                continue  # will be caught by the per-edit validation below
            p = Path(fp)
            if p.is_file() and not os.access(p, os.R_OK | os.W_OK):
                perm_issues.append(
                    f"edit {i}: permission denied: cannot edit {fp} (need read+write)"
                )
        if perm_issues:
            detail = "; ".join(perm_issues)
            return ToolResult(
                output=f"Permission check failed before applying edits: {detail}",
                is_error=True,
            )

        total = len(edits)
        succeeded = 0
        failures: list[str] = []
        diffs: list[str] = []

        for i, edit in enumerate(edits, start=1):
            file_path = edit.get("file_path", "")
            old_string = edit.get("old_string", "")
            new_string = edit.get("new_string", "")

            if not file_path:
                failures.append(f"edit {i}: file_path is required")
                continue
            if not old_string:
                failures.append(f"edit {i}: old_string is required")
                continue

            path = Path(file_path)
            if not path.is_absolute():
                path = Path(context.cwd) / path
            if not path.is_file():
                failures.append(f"edit {i}: file not found: {file_path}")
                continue

            try:
                content = path.read_text(encoding="utf-8")
            except Exception as exc:
                failures.append(f"edit {i}: error reading {file_path}: {exc}")
                continue

            count = content.count(old_string)
            if count == 0:
                failures.append(f"edit {i}: old_string not found in {file_path}")
                continue

            if count > 1:
                failures.append(
                    f"edit {i}: old_string found {count} times in {file_path}. "
                    "Provide more context to make it unique."
                )
                continue

            new_content = content.replace(old_string, new_string, 1)

            try:
                path.write_text(new_content, encoding="utf-8")
            except Exception as exc:
                failures.append(f"edit {i}: error writing {file_path}: {exc}")
                continue

            succeeded += 1

            # Collect per-edit diff
            diff = _make_diff(content, new_content, file_path)
            if diff:
                diffs.append(diff)

        # Build summary
        if succeeded == total:
            summary = f"Applied {succeeded}/{total} edits successfully."
            is_error = False
        elif succeeded > 0:
            fail_detail = "; ".join(failures)
            summary = f"Applied {succeeded}/{total} edits. Failed: {fail_detail}"
            is_error = False
        else:
            fail_detail = "; ".join(failures)
            summary = f"Applied 0/{total} edits. All failed: {fail_detail}"
            is_error = True

        if diffs:
            summary = f"{summary}\n{''.join(diffs)}"

        return ToolResult(
            output=summary,
            is_error=is_error,
            metadata={"succeeded": succeeded, "failed": total - succeeded, "total": total},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
