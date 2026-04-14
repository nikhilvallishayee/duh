"""NotebookEditTool — edit cells in Jupyter .ipynb notebooks.

Operations:
  - Modify an existing cell (provide cell_index and new_source)
  - Insert a new cell (cell_index=-1 appends; otherwise inserts before index)
  - Delete a cell (new_source=null/None)

Preserves all notebook metadata, outputs, and kernel info.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from duh.kernel.tool import ToolContext, ToolResult
from duh.security.trifecta import Capability


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_notebook(path: Path) -> dict[str, Any]:
    """Read and parse a .ipynb file. Raises on invalid JSON."""
    text = path.read_text(encoding="utf-8")
    nb = json.loads(text)
    if "cells" not in nb:
        raise ValueError("Not a valid notebook: missing 'cells' key")
    return nb


def _write_notebook(path: Path, nb: dict[str, Any]) -> None:
    """Write notebook back to disk with consistent formatting."""
    text = json.dumps(nb, indent=1, ensure_ascii=False) + "\n"
    path.write_text(text, encoding="utf-8")


def _make_cell(source: str, cell_type: str = "code") -> dict[str, Any]:
    """Create a new notebook cell with minimal required fields."""
    cell: dict[str, Any] = {
        "cell_type": cell_type,
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }
    if cell_type == "code":
        cell["execution_count"] = None
        cell["outputs"] = []
    return cell


def render_notebook(nb: dict[str, Any]) -> str:
    """Render notebook cells in a human-readable format.

    Returns a string like:
        [Cell 0] (code):
        import pandas as pd

        [Cell 1] (markdown):
        # Analysis
    """
    parts: list[str] = []
    for i, cell in enumerate(nb.get("cells", [])):
        ctype = cell.get("cell_type", "unknown")
        source_lines = cell.get("source", [])
        if isinstance(source_lines, list):
            source = "".join(source_lines)
        else:
            source = str(source_lines)
        parts.append(f"[Cell {i}] ({ctype}):")
        parts.append(source)
        # Ensure trailing newline separation between cells
        if not source.endswith("\n"):
            parts.append("")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Tool
# ---------------------------------------------------------------------------

class NotebookEditTool:
    """Edit, insert, or delete cells in Jupyter notebooks."""

    name = "NotebookEdit"
    capabilities = Capability.FS_WRITE | Capability.EXEC
    description = (
        "Edit a cell in a Jupyter .ipynb notebook. "
        "Modify existing cells, insert new cells (cell_index=-1 to append), "
        "or delete cells (new_source=null)."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "notebook_path": {
                "type": "string",
                "description": "Absolute path to the .ipynb file.",
            },
            "cell_index": {
                "type": "integer",
                "description": (
                    "Index of the cell to modify/delete. "
                    "Use -1 to append a new cell at the end."
                ),
            },
            "new_source": {
                "type": ["string", "null"],
                "description": (
                    "New source content for the cell. "
                    "Set to null to delete the cell at cell_index."
                ),
            },
            "cell_type": {
                "type": "string",
                "enum": ["code", "markdown"],
                "description": (
                    "Cell type (only used when inserting a new cell). "
                    "Defaults to 'code'."
                ),
            },
        },
        "required": ["notebook_path", "cell_index"],
    }

    @property
    def is_read_only(self) -> bool:
        return False

    @property
    def is_destructive(self) -> bool:
        return True

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        notebook_path = input.get("notebook_path", "")
        cell_index = input.get("cell_index")
        new_source = input.get("new_source")
        cell_type = input.get("cell_type", "code")

        # --- Validation ---
        if not notebook_path:
            return ToolResult(output="notebook_path is required", is_error=True)
        if cell_index is None:
            return ToolResult(output="cell_index is required", is_error=True)

        path = Path(notebook_path)
        if not path.is_file():
            return ToolResult(
                output=f"File not found: {notebook_path}", is_error=True
            )
        if not notebook_path.endswith(".ipynb"):
            return ToolResult(
                output=f"Not a notebook file (expected .ipynb): {notebook_path}",
                is_error=True,
            )
        if not os.access(path, os.R_OK | os.W_OK):
            return ToolResult(
                output=f"Permission denied: {notebook_path}",
                is_error=True,
            )

        # --- Read notebook ---
        try:
            nb = _read_notebook(path)
        except (json.JSONDecodeError, ValueError) as exc:
            return ToolResult(
                output=f"Error parsing notebook: {exc}", is_error=True
            )

        cells = nb["cells"]
        num_cells = len(cells)

        # --- Dispatch operation ---

        # INSERT: cell_index == -1 (append) or new_source provided for out-of-range
        if cell_index == -1:
            if new_source is None:
                return ToolResult(
                    output="Cannot delete with cell_index=-1 (append mode). "
                    "Provide new_source to insert a cell.",
                    is_error=True,
                )
            new_cell = _make_cell(new_source, cell_type)
            cells.append(new_cell)
            op_desc = f"Appended new {cell_type} cell at index {len(cells) - 1}"

        # DELETE: new_source is None
        elif new_source is None:
            if cell_index < 0 or cell_index >= num_cells:
                return ToolResult(
                    output=f"cell_index {cell_index} out of range "
                    f"(notebook has {num_cells} cells)",
                    is_error=True,
                )
            deleted_type = cells[cell_index].get("cell_type", "unknown")
            del cells[cell_index]
            op_desc = (
                f"Deleted {deleted_type} cell at index {cell_index} "
                f"({num_cells - 1} cells remaining)"
            )

        # MODIFY: update existing cell's source
        else:
            if cell_index < 0 or cell_index >= num_cells:
                return ToolResult(
                    output=f"cell_index {cell_index} out of range "
                    f"(notebook has {num_cells} cells)",
                    is_error=True,
                )
            cell = cells[cell_index]
            cell["source"] = new_source.splitlines(keepends=True)
            # Optionally update cell_type if specified and different
            if cell_type and cell.get("cell_type") != cell_type:
                old_type = cell.get("cell_type", "unknown")
                if input.get("cell_type") is not None:
                    cell["cell_type"] = cell_type
                    # Adjust structure for type change
                    if cell_type == "code" and "outputs" not in cell:
                        cell["outputs"] = []
                        cell["execution_count"] = None
                    elif cell_type == "markdown":
                        cell.pop("outputs", None)
                        cell.pop("execution_count", None)
            op_desc = (
                f"Modified {cell.get('cell_type', 'unknown')} cell at index {cell_index}"
            )

        # --- Write back ---
        try:
            _write_notebook(path, nb)
        except Exception as exc:
            return ToolResult(
                output=f"Error writing notebook: {exc}", is_error=True
            )

        return ToolResult(
            output=op_desc,
            metadata={"cell_count": len(cells), "notebook_path": notebook_path},
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
