"""GlobTool — find files matching a glob pattern."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult
from duh.kernel.untrusted import TaintSource, UntrustedStr
from duh.security.trifecta import Capability


def _wrap_file_content(text: str) -> UntrustedStr:
    """Tag file-system content as FILE_CONTENT."""
    if isinstance(text, UntrustedStr):
        return text
    return UntrustedStr(text, TaintSource.FILE_CONTENT)


class GlobTool:
    """Find files matching a glob pattern."""

    name = "Glob"
    capabilities = Capability.READ_PRIVATE
    description = "Find files by glob pattern. Returns matching file paths."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern to match (e.g. '**/*.py').",
            },
            "path": {
                "type": "string",
                "description": "Directory to search in. Defaults to cwd.",
            },
        },
        "required": ["pattern"],
    }

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        return False

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        pattern = input.get("pattern", "")
        search_path = input.get("path", "") or context.cwd or "."

        if not pattern:
            return ToolResult(output="pattern is required", is_error=True)

        root = Path(search_path)
        if not root.is_dir():
            return ToolResult(
                output=f"Directory not found: {search_path}", is_error=True
            )

        try:
            matches = sorted(str(p) for p in root.glob(pattern) if p.is_file())
        except Exception as exc:
            return ToolResult(output=f"Glob error: {exc}", is_error=True)

        if not matches:
            return ToolResult(output="No files matched the pattern.")

        return ToolResult(
            output="\n".join(matches),
            metadata={"count": len(matches)},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
