"""Tests for NotebookEditTool and ReadTool .ipynb rendering."""

import json

import pytest

from duh.kernel.tool import Tool, ToolContext, ToolResult
from duh.tools.notebook_edit import NotebookEditTool, render_notebook
from duh.tools.read import ReadTool


def ctx() -> ToolContext:
    return ToolContext()


# ---------------------------------------------------------------------------
# Helpers — minimal valid .ipynb JSON
# ---------------------------------------------------------------------------

def _make_nb(cells: list[dict] | None = None) -> dict:
    """Build a minimal valid notebook dict."""
    return {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {
                "display_name": "Python 3",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.11.0"},
        },
        "cells": cells or [],
    }


def _code_cell(source: str, execution_count: int | None = None) -> dict:
    return {
        "cell_type": "code",
        "metadata": {},
        "source": source.splitlines(keepends=True),
        "execution_count": execution_count,
        "outputs": [],
    }


def _md_cell(source: str) -> dict:
    return {
        "cell_type": "markdown",
        "metadata": {},
        "source": source.splitlines(keepends=True),
    }


def _write_nb(path, nb: dict) -> None:
    path.write_text(json.dumps(nb, indent=1), encoding="utf-8")


def _read_nb(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ===================================================================
# NotebookEditTool — Protocol
# ===================================================================


class TestNotebookEditProtocol:
    """NotebookEditTool must satisfy the Tool protocol."""

    def test_satisfies_protocol(self):
        assert isinstance(NotebookEditTool(), Tool)

    def test_has_name(self):
        assert NotebookEditTool().name == "NotebookEdit"

    def test_has_input_schema(self):
        schema = NotebookEditTool().input_schema
        assert schema["type"] == "object"
        assert "notebook_path" in schema["properties"]
        assert "cell_index" in schema["properties"]

    def test_is_not_read_only(self):
        assert NotebookEditTool().is_read_only is False

    def test_is_destructive(self):
        assert NotebookEditTool().is_destructive is True


# ===================================================================
# NotebookEditTool — Modify existing cell
# ===================================================================


class TestNotebookEditModify:
    tool = NotebookEditTool()

    async def test_modify_code_cell(self, tmp_path):
        nb = _make_nb([_code_cell("x = 1"), _code_cell("y = 2")])
        f = tmp_path / "test.ipynb"
        _write_nb(f, nb)

        result = await self.tool.call(
            {"notebook_path": str(f), "cell_index": 0, "new_source": "x = 42"},
            ctx(),
        )
        assert result.is_error is False
        assert "Modified" in result.output
        assert "index 0" in result.output

        updated = _read_nb(f)
        assert updated["cells"][0]["source"] == ["x = 42"]
        # Cell 1 unchanged
        assert updated["cells"][1]["source"] == ["y = 2"]

    async def test_modify_preserves_metadata(self, tmp_path):
        """Kernel info, nbformat, and cell metadata must survive edits."""
        nb = _make_nb([_code_cell("old", execution_count=5)])
        nb["cells"][0]["metadata"] = {"tags": ["important"]}
        f = tmp_path / "test.ipynb"
        _write_nb(f, nb)

        await self.tool.call(
            {"notebook_path": str(f), "cell_index": 0, "new_source": "new"},
            ctx(),
        )

        updated = _read_nb(f)
        assert updated["metadata"]["kernelspec"]["name"] == "python3"
        assert updated["nbformat"] == 4
        assert updated["cells"][0]["metadata"] == {"tags": ["important"]}
        assert updated["cells"][0]["execution_count"] == 5

    async def test_modify_markdown_cell(self, tmp_path):
        nb = _make_nb([_md_cell("# Old Title")])
        f = tmp_path / "test.ipynb"
        _write_nb(f, nb)

        result = await self.tool.call(
            {"notebook_path": str(f), "cell_index": 0, "new_source": "# New Title"},
            ctx(),
        )
        assert result.is_error is False
        updated = _read_nb(f)
        assert updated["cells"][0]["source"] == ["# New Title"]
        assert updated["cells"][0]["cell_type"] == "markdown"

    async def test_modify_multiline_source(self, tmp_path):
        nb = _make_nb([_code_cell("a = 1")])
        f = tmp_path / "test.ipynb"
        _write_nb(f, nb)

        new_src = "import os\nimport sys\n\nprint('hello')\n"
        result = await self.tool.call(
            {"notebook_path": str(f), "cell_index": 0, "new_source": new_src},
            ctx(),
        )
        assert result.is_error is False
        updated = _read_nb(f)
        assert "".join(updated["cells"][0]["source"]) == new_src


# ===================================================================
# NotebookEditTool — Insert new cell
# ===================================================================


class TestNotebookEditInsert:
    tool = NotebookEditTool()

    async def test_append_code_cell(self, tmp_path):
        nb = _make_nb([_code_cell("first")])
        f = tmp_path / "test.ipynb"
        _write_nb(f, nb)

        result = await self.tool.call(
            {"notebook_path": str(f), "cell_index": -1, "new_source": "second"},
            ctx(),
        )
        assert result.is_error is False
        assert "Appended" in result.output

        updated = _read_nb(f)
        assert len(updated["cells"]) == 2
        assert "".join(updated["cells"][1]["source"]) == "second"
        assert updated["cells"][1]["cell_type"] == "code"
        assert updated["cells"][1]["outputs"] == []

    async def test_append_markdown_cell(self, tmp_path):
        nb = _make_nb([_code_cell("first")])
        f = tmp_path / "test.ipynb"
        _write_nb(f, nb)

        result = await self.tool.call(
            {
                "notebook_path": str(f),
                "cell_index": -1,
                "new_source": "# Heading",
                "cell_type": "markdown",
            },
            ctx(),
        )
        assert result.is_error is False
        updated = _read_nb(f)
        assert len(updated["cells"]) == 2
        assert updated["cells"][1]["cell_type"] == "markdown"
        assert "outputs" not in updated["cells"][1]

    async def test_append_to_empty_notebook(self, tmp_path):
        nb = _make_nb([])
        f = tmp_path / "test.ipynb"
        _write_nb(f, nb)

        result = await self.tool.call(
            {"notebook_path": str(f), "cell_index": -1, "new_source": "print('hi')"},
            ctx(),
        )
        assert result.is_error is False
        updated = _read_nb(f)
        assert len(updated["cells"]) == 1


# ===================================================================
# NotebookEditTool — Delete cell
# ===================================================================


class TestNotebookEditDelete:
    tool = NotebookEditTool()

    async def test_delete_cell(self, tmp_path):
        nb = _make_nb([_code_cell("keep"), _code_cell("remove"), _code_cell("keep2")])
        f = tmp_path / "test.ipynb"
        _write_nb(f, nb)

        result = await self.tool.call(
            {"notebook_path": str(f), "cell_index": 1, "new_source": None},
            ctx(),
        )
        assert result.is_error is False
        assert "Deleted" in result.output
        assert "2 cells remaining" in result.output

        updated = _read_nb(f)
        assert len(updated["cells"]) == 2
        assert "".join(updated["cells"][0]["source"]) == "keep"
        assert "".join(updated["cells"][1]["source"]) == "keep2"

    async def test_delete_last_cell(self, tmp_path):
        nb = _make_nb([_code_cell("only")])
        f = tmp_path / "test.ipynb"
        _write_nb(f, nb)

        result = await self.tool.call(
            {"notebook_path": str(f), "cell_index": 0, "new_source": None},
            ctx(),
        )
        assert result.is_error is False
        updated = _read_nb(f)
        assert len(updated["cells"]) == 0

    async def test_delete_with_append_index_fails(self, tmp_path):
        """cell_index=-1 with new_source=None is an error (can't delete append)."""
        nb = _make_nb([_code_cell("x")])
        f = tmp_path / "test.ipynb"
        _write_nb(f, nb)

        result = await self.tool.call(
            {"notebook_path": str(f), "cell_index": -1, "new_source": None},
            ctx(),
        )
        assert result.is_error is True
        assert "append" in result.output.lower()


# ===================================================================
# NotebookEditTool — Error handling
# ===================================================================


class TestNotebookEditErrors:
    tool = NotebookEditTool()

    async def test_file_not_found(self, tmp_path):
        result = await self.tool.call(
            {"notebook_path": str(tmp_path / "missing.ipynb"), "cell_index": 0, "new_source": "x"},
            ctx(),
        )
        assert result.is_error is True
        assert "not found" in result.output.lower()

    async def test_not_ipynb_extension(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text("{}")
        result = await self.tool.call(
            {"notebook_path": str(f), "cell_index": 0, "new_source": "x"},
            ctx(),
        )
        assert result.is_error is True
        assert ".ipynb" in result.output

    async def test_invalid_json(self, tmp_path):
        f = tmp_path / "bad.ipynb"
        f.write_text("not json at all")
        result = await self.tool.call(
            {"notebook_path": str(f), "cell_index": 0, "new_source": "x"},
            ctx(),
        )
        assert result.is_error is True
        assert "parsing" in result.output.lower() or "error" in result.output.lower()

    async def test_missing_cells_key(self, tmp_path):
        f = tmp_path / "nocells.ipynb"
        f.write_text(json.dumps({"nbformat": 4}))
        result = await self.tool.call(
            {"notebook_path": str(f), "cell_index": 0, "new_source": "x"},
            ctx(),
        )
        assert result.is_error is True

    async def test_cell_index_out_of_range(self, tmp_path):
        nb = _make_nb([_code_cell("only")])
        f = tmp_path / "test.ipynb"
        _write_nb(f, nb)

        result = await self.tool.call(
            {"notebook_path": str(f), "cell_index": 5, "new_source": "x"},
            ctx(),
        )
        assert result.is_error is True
        assert "out of range" in result.output

    async def test_delete_out_of_range(self, tmp_path):
        nb = _make_nb([_code_cell("x")])
        f = tmp_path / "test.ipynb"
        _write_nb(f, nb)

        result = await self.tool.call(
            {"notebook_path": str(f), "cell_index": 3, "new_source": None},
            ctx(),
        )
        assert result.is_error is True
        assert "out of range" in result.output

    async def test_missing_notebook_path(self):
        result = await self.tool.call(
            {"notebook_path": "", "cell_index": 0, "new_source": "x"},
            ctx(),
        )
        assert result.is_error is True
        assert "required" in result.output.lower()


# ===================================================================
# ReadTool — .ipynb rendering
# ===================================================================


class TestReadToolNotebook:
    tool = ReadTool()

    async def test_read_notebook_renders_cells(self, tmp_path):
        nb = _make_nb([
            _code_cell("import pandas as pd"),
            _md_cell("# Analysis"),
            _code_cell("df = pd.read_csv('data.csv')"),
        ])
        f = tmp_path / "analysis.ipynb"
        _write_nb(f, nb)

        result = await self.tool.call({"file_path": str(f)}, ctx())
        assert result.is_error is False
        assert "[Cell 0] (code):" in result.output
        assert "import pandas as pd" in result.output
        assert "[Cell 1] (markdown):" in result.output
        assert "# Analysis" in result.output
        assert "[Cell 2] (code):" in result.output
        assert result.metadata["cell_count"] == 3

    async def test_read_empty_notebook(self, tmp_path):
        nb = _make_nb([])
        f = tmp_path / "empty.ipynb"
        _write_nb(f, nb)

        result = await self.tool.call({"file_path": str(f)}, ctx())
        assert result.is_error is False
        assert "empty notebook" in result.output.lower()

    async def test_read_notebook_with_offset(self, tmp_path):
        nb = _make_nb([
            _code_cell("cell0"),
            _code_cell("cell1"),
            _code_cell("cell2"),
        ])
        f = tmp_path / "test.ipynb"
        _write_nb(f, nb)

        result = await self.tool.call({"file_path": str(f), "offset": 2}, ctx())
        assert result.is_error is False
        # Lines start numbering from offset+1
        assert result.output.startswith("3\t")

    async def test_read_notebook_with_limit(self, tmp_path):
        nb = _make_nb([
            _code_cell("line1\nline2\nline3"),
            _code_cell("line4"),
        ])
        f = tmp_path / "test.ipynb"
        _write_nb(f, nb)

        result = await self.tool.call({"file_path": str(f), "limit": 2}, ctx())
        assert result.is_error is False
        assert result.metadata["line_count"] == 2

    async def test_read_regular_file_unchanged(self, tmp_path):
        """Non-.ipynb files still render with line numbers as before."""
        f = tmp_path / "code.py"
        f.write_text("x = 1\ny = 2\n")

        result = await self.tool.call({"file_path": str(f)}, ctx())
        assert result.is_error is False
        assert "1\tx = 1\n" in result.output
        assert "2\ty = 2\n" in result.output


# ===================================================================
# render_notebook — unit tests for the helper
# ===================================================================


class TestRenderNotebook:

    def test_basic_rendering(self):
        nb = _make_nb([_code_cell("x = 1"), _md_cell("# Title")])
        rendered = render_notebook(nb)
        assert "[Cell 0] (code):" in rendered
        assert "x = 1" in rendered
        assert "[Cell 1] (markdown):" in rendered
        assert "# Title" in rendered

    def test_empty_cells(self):
        nb = _make_nb([])
        rendered = render_notebook(nb)
        assert rendered == ""

    def test_multiline_cell(self):
        nb = _make_nb([_code_cell("a = 1\nb = 2\nc = 3\n")])
        rendered = render_notebook(nb)
        assert "a = 1" in rendered
        assert "b = 2" in rendered
        assert "c = 3" in rendered


# ===================================================================
# Registry — NotebookEditTool is registered
# ===================================================================


class TestNotebookEditRegistry:

    def test_in_registry(self):
        from duh.tools.registry import get_all_tools
        tools = get_all_tools()
        names = [t.name for t in tools]
        assert "NotebookEdit" in names

    def test_in_all_tools(self):
        from duh.tools import ALL_TOOLS
        names = [t.name for t in ALL_TOOLS]
        assert "NotebookEdit" in names
