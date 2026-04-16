"""GrepTool — search file contents with regex."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult
from duh.kernel.untrusted import TaintSource, UntrustedStr
from duh.security.trifecta import Capability

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_DEFAULT_MAX_RESULTS = 500
_BINARY_CHECK_SIZE = 8192  # first 8 KB


def _wrap_file_content(text: str) -> UntrustedStr:
    """Tag file-system content as FILE_CONTENT."""
    if isinstance(text, UntrustedStr):
        return text
    return UntrustedStr(text, TaintSource.FILE_CONTENT)


def _is_binary(path: Path) -> bool:
    """Return True if *path* looks like a binary file.

    Reads the first 8 KB and checks for null bytes — a simple heuristic
    that catches ELF, Mach-O, class files, images, etc.
    """
    try:
        with open(path, "rb") as fh:
            chunk = fh.read(_BINARY_CHECK_SIZE)
        return b"\x00" in chunk
    except Exception:
        return False


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
            "max_results": {
                "type": "integer",
                "description": "Maximum number of matching lines to return. Default: 500.",
                "default": _DEFAULT_MAX_RESULTS,
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
        max_results: int = input.get("max_results", _DEFAULT_MAX_RESULTS)

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
        total_matches = 0
        truncated = False

        for fpath in files:
            # Skip binary files
            if _is_binary(fpath):
                continue

            try:
                fh = open(fpath, encoding="utf-8", errors="replace")  # noqa: SIM115
            except Exception:
                continue

            try:
                for lineno, line in enumerate(fh, start=1):
                    line = line.rstrip("\n\r")
                    if regex.search(line):
                        total_matches += 1
                        if len(results) < max_results:
                            results.append(f"{fpath}:{lineno}:{line}")
                        else:
                            truncated = True
                            break
            finally:
                fh.close()

            if truncated:
                # Count remaining matches in current file for the note,
                # but don't store them — just break out of the file loop.
                break

        if not results and total_matches == 0:
            return ToolResult(output="No matches found.")

        output = "\n".join(results)
        if truncated:
            output += f"\n\n... results truncated (showing {max_results} of {max_results}+ matches)"

        return ToolResult(
            output=output,
            metadata={"match_count": len(results), "truncated": truncated},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
