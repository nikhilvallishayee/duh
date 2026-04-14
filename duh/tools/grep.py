"""GrepTool — search file contents with regex."""

from __future__ import annotations

import re
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


class GrepTool:
    """Search file contents using a regular expression."""

    name = "Grep"
    capabilities = Capability.READ_PRIVATE
    description = "Search for a regex pattern in files. Returns matching lines."
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression pattern to search for.",
            },
            "path": {
                "type": "string",
                "description": "File or directory to search in. Defaults to cwd.",
            },
            "glob": {
                "type": "string",
                "description": "Glob filter for files when searching a directory (e.g. '*.py').",
            },
            "case_insensitive": {
                "type": "boolean",
                "description": "If true, search case-insensitively. Default: false.",
                "default": False,
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
        pattern_str = input.get("pattern", "")
        search_path = input.get("path", "") or context.cwd or "."
        file_glob = input.get("glob", "")
        case_insensitive = input.get("case_insensitive", False)

        if not pattern_str:
            return ToolResult(output="pattern is required", is_error=True)

        flags = re.IGNORECASE if case_insensitive else 0
        try:
            regex = re.compile(pattern_str, flags)
        except re.error as exc:
            return ToolResult(output=f"Invalid regex: {exc}", is_error=True)

        root = Path(search_path)

        # Collect files to search
        if root.is_file():
            files = [root]
        elif root.is_dir():
            glob_pattern = file_glob or "**/*"
            try:
                files = sorted(p for p in root.glob(glob_pattern) if p.is_file())
            except Exception as exc:
                return ToolResult(output=f"Glob error: {exc}", is_error=True)
        else:
            return ToolResult(
                output=f"Path not found: {search_path}", is_error=True
            )

        results: list[str] = []
        for fpath in files:
            try:
                text = fpath.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    results.append(f"{fpath}:{lineno}:{line}")

        if not results:
            return ToolResult(output="No matches found.")

        return ToolResult(
            output="\n".join(results),
            metadata={"match_count": len(results)},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
