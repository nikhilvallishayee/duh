"""ReadTool — read a file from disk, return contents with line numbers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult


class ReadTool:
    """Read a file and return its contents with line numbers."""

    name = "Read"
    description = "Read a file from disk. Returns contents prefixed with line numbers."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "Absolute path to the file to read.",
            },
            "offset": {
                "type": "integer",
                "description": "Line number to start reading from (0-based). Defaults to 0.",
                "minimum": 0,
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of lines to return. Omit to read entire file.",
                "minimum": 1,
            },
        },
        "required": ["file_path"],
    }

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        file_path = input.get("file_path", "")
        offset = input.get("offset", 0)
        limit = input.get("limit")

        if not file_path:
            return ToolResult(output="file_path is required", is_error=True)

        path = Path(file_path)
        if not path.is_file():
            return ToolResult(
                output=f"File not found: {file_path}", is_error=True
            )

        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(output=f"Error reading file: {exc}", is_error=True)

        lines = text.splitlines(keepends=True)

        # Apply offset
        if offset > 0:
            lines = lines[offset:]

        # Apply limit
        if limit is not None:
            lines = lines[:limit]

        # Format with 1-based line numbers (offset shifts the starting number)
        start = offset + 1
        numbered = "".join(
            f"{start + i}\t{line}" for i, line in enumerate(lines)
        )

        # If the file is empty or slice is empty, say so
        if not numbered:
            if text == "":
                return ToolResult(output="(empty file)")
            return ToolResult(output="(no lines in requested range)")

        return ToolResult(
            output=numbered,
            metadata={"line_count": len(lines), "offset": offset},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
