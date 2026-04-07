"""Tests for TestImpactTool — test impact analysis.

Covers:
- Helper functions (_module_names_from_path, _find_test_files_by_convention,
  _find_test_files_by_imports)
- Tool protocol conformance (name, schema, read-only, permissions)
- Explicit changed_files input
- Git auto-detection (mocked)
- Edge cases: no files, no Python files, __init__.py, already-test files
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from duh.kernel.tool import Tool, ToolContext, ToolResult
from duh.tools.test_impact import (
    TestImpactTool,
    _find_test_files_by_convention,
    _find_test_files_by_imports,
    _module_names_from_path,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ctx(cwd: str = ".") -> ToolContext:
    return ToolContext(cwd=cwd)


# ===========================================================================
# _module_names_from_path
# ===========================================================================


class TestModuleNamesFromPath:
    def test_simple_module(self):
        names = _module_names_from_path("duh/tools/bash.py")
        assert "bash" in names
        assert "duh.tools.bash" in names
        assert "tools.bash" in names

    def test_single_file(self):
        names = _module_names_from_path("utils.py")
        assert names == ["utils"]

    def test_init_file_returns_empty(self):
        names = _module_names_from_path("duh/__init__.py")
        assert names == []

    def test_non_python_returns_empty(self):
        names = _module_names_from_path("README.md")
        assert names == []

    def test_nested_path(self):
        names = _module_names_from_path("src/utils/helper.py")
        assert "helper" in names
        assert "utils.helper" in names
        assert "src.utils.helper" in names


# ===========================================================================
# _find_test_files_by_convention
# ===========================================================================


class TestFindByConvention:
    def test_finds_matching_test_file(self, tmp_path: Path):
        # Create source and matching test
        (tmp_path / "tests" / "unit").mkdir(parents=True)
        (tmp_path / "tests" / "unit" / "test_foo.py").write_text("# test")

        result = _find_test_files_by_convention("src/foo.py", tmp_path)
        assert len(result) == 1
        assert "tests/unit/test_foo.py" in result[0]

    def test_no_match(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_other.py").write_text("# test")

        result = _find_test_files_by_convention("src/foo.py", tmp_path)
        assert result == []

    def test_skips_already_test_files(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()
        result = _find_test_files_by_convention("tests/test_foo.py", tmp_path)
        assert result == []

    def test_no_tests_dir(self, tmp_path: Path):
        result = _find_test_files_by_convention("src/foo.py", tmp_path)
        assert result == []


# ===========================================================================
# _find_test_files_by_imports
# ===========================================================================


class TestFindByImports:
    def test_finds_import_match(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_bar.py").write_text(
            "from duh.tools.bash import BashTool\n"
        )

        result = _find_test_files_by_imports(["bash", "duh.tools.bash"], tmp_path)
        assert len(result) == 1
        assert "test_bar.py" in result[0]

    def test_no_import_match(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_xyz.py").write_text(
            "import os\nimport sys\n"
        )

        result = _find_test_files_by_imports(["bash"], tmp_path)
        assert result == []

    def test_empty_module_names(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()
        result = _find_test_files_by_imports([], tmp_path)
        assert result == []

    def test_no_tests_dir(self, tmp_path: Path):
        result = _find_test_files_by_imports(["bash"], tmp_path)
        assert result == []

    def test_import_statement_match(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_imp.py").write_text(
            "import duh.tools.grep\n"
        )

        result = _find_test_files_by_imports(["grep", "duh.tools.grep"], tmp_path)
        assert len(result) == 1


# ===========================================================================
# TestImpactTool — protocol conformance
# ===========================================================================


class TestImpactProtocol:
    def test_satisfies_tool_protocol(self):
        tool = TestImpactTool()
        assert isinstance(tool, Tool)

    def test_name(self):
        assert TestImpactTool().name == "TestImpact"

    def test_description_non_empty(self):
        assert TestImpactTool().description

    def test_input_schema_structure(self):
        schema = TestImpactTool().input_schema
        assert schema["type"] == "object"
        assert "changed_files" in schema["properties"]

    def test_is_read_only(self):
        assert TestImpactTool().is_read_only is True

    def test_is_not_destructive(self):
        assert TestImpactTool().is_destructive is False

    async def test_check_permissions(self):
        result = await TestImpactTool().check_permissions({}, ctx())
        assert result["allowed"] is True


# ===========================================================================
# TestImpactTool — explicit changed_files
# ===========================================================================


class TestImpactExplicit:
    async def test_with_matching_convention(self, tmp_path: Path):
        (tmp_path / "tests" / "unit").mkdir(parents=True)
        (tmp_path / "tests" / "unit" / "test_foo.py").write_text("# test")

        tool = TestImpactTool()
        result = await tool.call(
            {"changed_files": ["src/foo.py"]},
            ctx(cwd=str(tmp_path)),
        )
        assert result.is_error is False
        assert "test_foo.py" in result.output
        assert "pytest" in result.output
        assert len(result.metadata["test_files"]) >= 1

    async def test_with_matching_imports(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_bar.py").write_text(
            "from duh.tools.bar import BarTool\n"
        )

        tool = TestImpactTool()
        result = await tool.call(
            {"changed_files": ["duh/tools/bar.py"]},
            ctx(cwd=str(tmp_path)),
        )
        assert result.is_error is False
        assert "test_bar.py" in result.output

    async def test_no_affected_tests(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()

        tool = TestImpactTool()
        result = await tool.call(
            {"changed_files": ["src/orphan.py"]},
            ctx(cwd=str(tmp_path)),
        )
        assert result.is_error is False
        assert "no affected" in result.output.lower()
        assert result.metadata["test_files"] == []

    async def test_changed_test_file_included(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_abc.py").write_text("# test")

        tool = TestImpactTool()
        result = await tool.call(
            {"changed_files": ["tests/test_abc.py"]},
            ctx(cwd=str(tmp_path)),
        )
        assert result.is_error is False
        assert "test_abc.py" in result.output

    async def test_non_python_files_ignored(self, tmp_path: Path):
        tool = TestImpactTool()
        result = await tool.call(
            {"changed_files": ["README.md", "config.yaml"]},
            ctx(cwd=str(tmp_path)),
        )
        assert result.is_error is False
        assert "no python" in result.output.lower()

    async def test_empty_list(self, tmp_path: Path):
        tool = TestImpactTool()
        result = await tool.call(
            {"changed_files": []},
            ctx(cwd=str(tmp_path)),
        )
        assert result.is_error is False
        assert "no changed" in result.output.lower()

    async def test_command_in_metadata(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_foo.py").write_text("# test")

        tool = TestImpactTool()
        result = await tool.call(
            {"changed_files": ["src/foo.py"]},
            ctx(cwd=str(tmp_path)),
        )
        assert result.metadata["command"].startswith("pytest")
        assert "test_foo.py" in result.metadata["command"]


# ===========================================================================
# TestImpactTool — git auto-detection (mocked)
# ===========================================================================


class TestImpactAutoDetect:
    async def test_auto_detects_from_git(self, tmp_path: Path):
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_engine.py").write_text(
            "from duh.kernel.engine import Engine\n"
        )

        tool = TestImpactTool()
        with patch(
            "duh.tools.test_impact._git_changed_files",
            new_callable=AsyncMock,
            return_value=["duh/kernel/engine.py"],
        ):
            result = await tool.call({}, ctx(cwd=str(tmp_path)))

        assert result.is_error is False
        assert result.metadata["auto_detected"] is True
        assert "test_engine.py" in result.output
        assert "Auto-detected" in result.output

    async def test_no_git_changes(self):
        tool = TestImpactTool()
        with patch(
            "duh.tools.test_impact._git_changed_files",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await tool.call({}, ctx())

        assert result.is_error is False
        assert "no changed" in result.output.lower()
        assert result.metadata["auto_detected"] is True
        assert result.metadata["test_files"] == []


# ===========================================================================
# TestImpactTool — registry integration
# ===========================================================================


class TestImpactRegistry:
    def test_registered_in_get_all_tools(self):
        from duh.tools.registry import get_all_tools
        tools = get_all_tools()
        names = [t.name for t in tools]
        assert "TestImpact" in names
