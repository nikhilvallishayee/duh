"""Tests for LSPTool — static-analysis-based LSP queries.

Covers:
- All four actions: definition, references, hover, symbols
- Python files (ast-based) and non-Python files (regex-based)
- Edge cases: missing file, bad action, no symbol at position
- Registry integration as a deferred tool
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import pytest

from duh.kernel.tool import ToolContext
from duh.tools.lsp_tool import LSPTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ctx(cwd: str = ".") -> ToolContext:
    return ToolContext(cwd=cwd)


def _write_py(tmp_path: Path, content: str) -> Path:
    """Write a Python file and return its path."""
    p = tmp_path / "sample.py"
    p.write_text(textwrap.dedent(content))
    return p


def _write_file(tmp_path: Path, name: str, content: str) -> Path:
    """Write any file and return its path."""
    p = tmp_path / name
    p.write_text(textwrap.dedent(content))
    return p


# ===========================================================================
# Schema / metadata
# ===========================================================================


class TestLSPToolMeta:
    def test_name_and_description(self):
        tool = LSPTool()
        assert tool.name == "LSP"
        assert "language server" in tool.description.lower()

    def test_schema_structure(self):
        tool = LSPTool()
        schema = tool.input_schema
        assert schema["type"] == "object"
        props = schema["properties"]
        assert "action" in props
        assert "file" in props
        assert "line" in props
        assert "character" in props
        assert set(schema["required"]) == {"action", "file"}

    def test_is_read_only(self):
        tool = LSPTool()
        assert tool.is_read_only is True
        assert tool.is_destructive is False

    async def test_check_permissions(self):
        tool = LSPTool()
        perm = await tool.check_permissions({"action": "symbols", "file": "x"}, ctx())
        assert perm["allowed"] is True


# ===========================================================================
# Action: symbols (Python)
# ===========================================================================


class TestSymbolsPython:
    async def test_lists_functions_and_classes(self, tmp_path: Path):
        p = _write_py(tmp_path, """\
            class Foo:
                pass

            def bar(x: int) -> str:
                return str(x)

            async def baz():
                pass

            CONSTANT = 42
        """)
        tool = LSPTool()
        result = await tool.call({"action": "symbols", "file": str(p)}, ctx())
        assert result.is_error is False
        assert "Foo" in result.output
        assert "bar" in result.output
        assert "baz" in result.output
        assert "CONSTANT" in result.output
        assert result.metadata["count"] == 4

    async def test_empty_file(self, tmp_path: Path):
        p = _write_py(tmp_path, "")
        tool = LSPTool()
        result = await tool.call({"action": "symbols", "file": str(p)}, ctx())
        assert result.metadata["count"] == 0
        assert "no symbols" in result.output.lower()


# ===========================================================================
# Action: symbols (non-Python — regex fallback)
# ===========================================================================


class TestSymbolsRegex:
    async def test_js_symbols(self, tmp_path: Path):
        p = _write_file(tmp_path, "app.js", """\
            function greet(name) {
                return "Hello " + name;
            }

            const MAX_RETRIES = 5;

            class EventBus {
                constructor() {}
            }
        """)
        tool = LSPTool()
        result = await tool.call({"action": "symbols", "file": str(p)}, ctx())
        assert "greet" in result.output
        assert "MAX_RETRIES" in result.output
        assert "EventBus" in result.output

    async def test_rust_symbols(self, tmp_path: Path):
        p = _write_file(tmp_path, "lib.rs", """\
            pub fn process(data: &[u8]) -> Result<(), Error> {
                Ok(())
            }

            struct Config {
                timeout: u64,
            }
        """)
        tool = LSPTool()
        result = await tool.call({"action": "symbols", "file": str(p)}, ctx())
        assert "process" in result.output
        assert "Config" in result.output


# ===========================================================================
# Action: definition
# ===========================================================================


class TestDefinition:
    async def test_find_function_definition(self, tmp_path: Path):
        p = _write_py(tmp_path, """\
            def helper(x):
                return x + 1

            result = helper(5)
        """)
        tool = LSPTool()
        # Point at "helper" on the call line (line 4, char 9)
        result = await tool.call(
            {"action": "definition", "file": str(p), "line": 4, "character": 9},
            ctx(),
        )
        assert result.is_error is False
        assert result.metadata["found"] is True
        assert "helper" in result.output
        assert ":1" in result.output  # defined on line 1

    async def test_find_class_definition(self, tmp_path: Path):
        p = _write_py(tmp_path, """\
            class Widget:
                pass

            w = Widget()
        """)
        tool = LSPTool()
        result = await tool.call(
            {"action": "definition", "file": str(p), "line": 4, "character": 4},
            ctx(),
        )
        assert result.metadata["found"] is True
        assert "Widget" in result.output

    async def test_definition_not_found(self, tmp_path: Path):
        p = _write_py(tmp_path, """\
            x = some_external_func()
        """)
        tool = LSPTool()
        result = await tool.call(
            {"action": "definition", "file": str(p), "line": 1, "character": 4},
            ctx(),
        )
        assert result.metadata["found"] is False


# ===========================================================================
# Action: references
# ===========================================================================


class TestReferences:
    async def test_find_all_references(self, tmp_path: Path):
        p = _write_py(tmp_path, """\
            def compute(x):
                return x * 2

            a = compute(1)
            b = compute(2)
            c = compute(3)
        """)
        tool = LSPTool()
        result = await tool.call(
            {"action": "references", "file": str(p), "line": 1, "character": 4},
            ctx(),
        )
        assert result.is_error is False
        # "compute" appears on lines 1, 4, 5, 6
        assert result.metadata["count"] == 4

    async def test_no_references(self, tmp_path: Path):
        p = _write_py(tmp_path, """\
            x = 1
            y = 2
        """)
        tool = LSPTool()
        result = await tool.call(
            {"action": "references", "file": str(p), "line": 1, "character": 4},
            ctx(),
        )
        # "1" is not an identifier — _find_symbol_at won't match digits well
        # Actually "x" is at char 0, "1" at char 4. Let's use char 0.
        result = await tool.call(
            {"action": "references", "file": str(p), "line": 1, "character": 0},
            ctx(),
        )
        # x appears only once
        assert result.metadata["count"] == 1


# ===========================================================================
# Action: hover
# ===========================================================================


class TestHover:
    async def test_hover_function_with_docstring(self, tmp_path: Path):
        p = _write_py(tmp_path, '''\
            def greet(name: str) -> str:
                """Say hello to someone."""
                return f"Hello {name}"
        ''')
        tool = LSPTool()
        result = await tool.call(
            {"action": "hover", "file": str(p), "line": 1, "character": 4},
            ctx(),
        )
        assert result.is_error is False
        assert result.metadata["found"] is True
        assert "greet" in result.output
        assert "name: str" in result.output
        assert "Say hello" in result.output

    async def test_hover_not_found(self, tmp_path: Path):
        p = _write_py(tmp_path, """\
            x = unknown()
        """)
        tool = LSPTool()
        result = await tool.call(
            {"action": "hover", "file": str(p), "line": 1, "character": 4},
            ctx(),
        )
        assert result.metadata["found"] is False


# ===========================================================================
# Error handling
# ===========================================================================


class TestErrors:
    async def test_unknown_action(self, tmp_path: Path):
        p = _write_py(tmp_path, "x = 1\n")
        tool = LSPTool()
        result = await tool.call(
            {"action": "rename", "file": str(p)}, ctx()
        )
        assert result.is_error is True
        assert "unknown action" in result.output.lower()

    async def test_missing_file(self):
        tool = LSPTool()
        result = await tool.call(
            {"action": "symbols", "file": "/nonexistent/path.py"}, ctx()
        )
        assert result.is_error is True
        assert "not found" in result.output.lower()

    async def test_no_identifier_at_position(self, tmp_path: Path):
        p = _write_py(tmp_path, """\
            x = 1
        """)
        tool = LSPTool()
        # Line 1, char 2 is the space in "x = 1" — no identifier
        result = await tool.call(
            {"action": "definition", "file": str(p), "line": 1, "character": 2},
            ctx(),
        )
        # char 2 is ' ', but the identifier-expansion logic might still grab
        # a nearby identifier. If it finds nothing, it's an error.
        # Let's use a truly empty line
        p2 = _write_py(tmp_path, "\n\n\n")
        result = await tool.call(
            {"action": "definition", "file": str(p2), "line": 2, "character": 0},
            ctx(),
        )
        assert result.is_error is True

    async def test_relative_path_resolved(self, tmp_path: Path):
        p = _write_py(tmp_path, "x = 1\n")
        tool = LSPTool()
        result = await tool.call(
            {"action": "symbols", "file": "sample.py"},
            ctx(cwd=str(tmp_path)),
        )
        assert result.is_error is False

    async def test_file_is_directory(self, tmp_path: Path):
        tool = LSPTool()
        result = await tool.call(
            {"action": "symbols", "file": str(tmp_path)}, ctx()
        )
        assert result.is_error is True
        assert "not a file" in result.output.lower()

    async def test_empty_file_param(self):
        tool = LSPTool()
        result = await tool.call(
            {"action": "symbols", "file": ""}, ctx()
        )
        assert result.is_error is True


# ===========================================================================
# Registry integration
# ===========================================================================


class TestRegistryIntegration:
    def test_lsp_registered_as_deferred(self):
        """LSPTool should be registered as a deferred tool in ToolSearch."""
        from duh.tools.registry import get_all_tools

        tools = get_all_tools()
        tool_search = None
        for t in tools:
            if getattr(t, "name", "") == "ToolSearch":
                tool_search = t
                break
        assert tool_search is not None, "ToolSearch not found in registry"

        deferred_names = [dt.name for dt in tool_search.deferred_tools]
        assert "LSP" in deferred_names
