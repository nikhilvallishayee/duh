"""Full coverage for duh.tools.registry — ImportError fallback branches."""

from unittest.mock import patch

from duh.tools.registry import get_all_tools


class TestGetAllToolsImportErrors:
    def test_all_tools_available(self):
        """Normal case — all tools import successfully."""
        tools = get_all_tools()
        names = [t.name for t in tools]
        assert "Read" in names
        assert "Write" in names
        assert "Edit" in names
        assert "Bash" in names
        assert "Glob" in names
        assert "Grep" in names
        assert len(tools) == 6

    def test_read_import_fails(self):
        with patch.dict("sys.modules", {"duh.tools.read": None}):
            tools = get_all_tools()
            names = [t.name for t in tools]
            assert "Read" not in names
            assert len(tools) == 5

    def test_write_import_fails(self):
        with patch.dict("sys.modules", {"duh.tools.write": None}):
            tools = get_all_tools()
            names = [t.name for t in tools]
            assert "Write" not in names
            assert len(tools) == 5

    def test_edit_import_fails(self):
        with patch.dict("sys.modules", {"duh.tools.edit": None}):
            tools = get_all_tools()
            names = [t.name for t in tools]
            assert "Edit" not in names
            assert len(tools) == 5

    def test_bash_import_fails(self):
        with patch.dict("sys.modules", {"duh.tools.bash": None}):
            tools = get_all_tools()
            names = [t.name for t in tools]
            assert "Bash" not in names
            assert len(tools) == 5

    def test_glob_import_fails(self):
        with patch.dict("sys.modules", {"duh.tools.glob_tool": None}):
            tools = get_all_tools()
            names = [t.name for t in tools]
            assert "Glob" not in names
            assert len(tools) == 5

    def test_grep_import_fails(self):
        with patch.dict("sys.modules", {"duh.tools.grep": None}):
            tools = get_all_tools()
            names = [t.name for t in tools]
            assert "Grep" not in names
            assert len(tools) == 5

    def test_all_imports_fail(self):
        with patch.dict("sys.modules", {
            "duh.tools.read": None,
            "duh.tools.write": None,
            "duh.tools.edit": None,
            "duh.tools.bash": None,
            "duh.tools.glob_tool": None,
            "duh.tools.grep": None,
        }):
            tools = get_all_tools()
            assert tools == []
