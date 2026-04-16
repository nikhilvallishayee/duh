"""ReadTool — read a file from disk, return contents with line numbers.

For .ipynb (Jupyter notebook) files, renders cells in a human-readable format
instead of raw JSON.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from duh.kernel.tool import MAX_TOOL_OUTPUT, ToolContext, ToolResult
from duh.kernel.untrusted import TaintSource, UntrustedStr
from duh.security.trifecta import Capability


def _wrap_file_content(text: str) -> UntrustedStr:
    """Tag file-system content as FILE_CONTENT."""
    if isinstance(text, UntrustedStr):
        return text
    return UntrustedStr(text, TaintSource.FILE_CONTENT)


# Maximum file size for reading (50 MB). Files larger than this should
# be read with offset/limit. Prevents OOM on binary blobs.
MAX_FILE_READ_BYTES = 50 * 1024 * 1024  # 50 MB


class ReadTool:
    """Read a file and return its contents with line numbers."""

    name = "Read"

    def __init__(
        self,
        *,
        path_policy: "PathPolicy | None" = None,
    ) -> None:
        from duh.security.path_policy import PathPolicy  # noqa: F811
        self._path_policy: PathPolicy | None = path_policy
    capabilities = Capability.READ_PRIVATE
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
        if not path.is_absolute():
            path = Path(context.cwd) / path

        # Filesystem boundary check
        if self._path_policy is not None:
            allowed, reason = self._path_policy.check(str(path))
            if not allowed:
                return ToolResult(output=reason, is_error=True)

        if not path.exists():
            return ToolResult(
                output=f"File not found: {file_path}", is_error=True
            )
        if not path.is_file():
            return ToolResult(
                output=f"Not a file: {file_path}", is_error=True
            )
        if not os.access(path, os.R_OK):
            return ToolResult(
                output=f"Permission denied: cannot read {file_path}",
                is_error=True,
            )

        # --- File size cap ---
        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = 0
        if file_size > MAX_FILE_READ_BYTES:
            return ToolResult(
                output=(
                    f"File too large ({file_size:,} bytes, limit {MAX_FILE_READ_BYTES:,})."
                    " Use offset and limit to read sections."
                ),
                is_error=True,
            )

        # --- Jupyter notebook rendering ---
        if path.suffix == ".ipynb":
            return await self._read_notebook(path, offset, limit)

        try:
            text = path.read_text(encoding="utf-8")
        except Exception as exc:
            return ToolResult(output=f"Error reading file: {exc}", is_error=True)

        # Large-file guard: if no offset/limit and raw text exceeds limit,
        # truncate early and suggest using offset/limit.
        file_size = len(text.encode("utf-8"))
        no_slice = offset == 0 and limit is None
        if no_slice and file_size > MAX_TOOL_OUTPUT:
            lines = text.splitlines(keepends=True)
            # Gather lines until we approach the limit
            collected: list[str] = []
            acc = 0
            for i, line in enumerate(lines):
                entry = f"{i + 1}\t{line}"
                acc += len(entry)
                if acc > MAX_TOOL_OUTPUT:
                    break
                collected.append(entry)
            truncated_output = (
                "".join(collected)
                + f"\n\n... File is large ({file_size:,} bytes)."
                " Use offset and limit parameters to read specific sections."
            )
            return ToolResult(
                output=truncated_output,
                metadata={
                    "line_count": len(collected),
                    "offset": 0,
                    "truncated": True,
                    "original_size": file_size,
                },
            )

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

    async def _read_notebook(
        self, path: Path, offset: int, limit: int | None
    ) -> ToolResult:
        """Render a .ipynb notebook in human-readable cell format."""
        try:
            from duh.tools.notebook_edit import render_notebook, _read_notebook
            nb = _read_notebook(path)
        except Exception as exc:
            return ToolResult(
                output=f"Error reading notebook: {exc}", is_error=True
            )

        rendered = render_notebook(nb)
        lines = rendered.splitlines(keepends=True)

        # Apply offset/limit
        if offset > 0:
            lines = lines[offset:]
        if limit is not None:
            lines = lines[:limit]

        start = offset + 1
        numbered = "".join(
            f"{start + i}\t{line}" for i, line in enumerate(lines)
        )
        if not numbered:
            if not nb.get("cells"):
                return ToolResult(output="(empty notebook — no cells)")
            return ToolResult(output="(no lines in requested range)")

        cell_count = len(nb.get("cells", []))
        return ToolResult(
            output=numbered,
            metadata={"cell_count": cell_count, "offset": offset, "line_count": len(lines)},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
