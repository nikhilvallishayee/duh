"""WriteTool — write content to a file, creating parent dirs as needed."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult


class WriteTool:
    """Write content to a file. Creates parent directories if they don't exist."""

    name = "Write"
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

        if not file_path:
            return ToolResult(output="file_path is required", is_error=True)

        path = Path(file_path)

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        except Exception as exc:
            return ToolResult(output=f"Error writing file: {exc}", is_error=True)

        return ToolResult(
            output=f"Wrote {len(content)} bytes to {file_path}",
            metadata={"bytes_written": len(content)},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
