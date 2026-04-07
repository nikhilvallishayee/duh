"""Tests for plugin system (ADR-014)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from duh.plugins import (
    PluginSpec,
    PluginRegistry,
    _parse_manifest_tools,
    discover_directory_plugins,
    discover_entry_point_plugins,
    discover_plugins,
)
from duh.tools.tool_search import DeferredTool


# ---------------------------------------------------------------------------
# PluginSpec
# ---------------------------------------------------------------------------


class TestPluginSpec:
    def test_basic_creation(self):
        spec = PluginSpec(name="test", version="1.0.0")
        assert spec.name == "test"
        assert spec.version == "1.0.0"
        assert spec.description == ""
        assert spec.tools == []
        assert spec.hooks == []

    def test_with_tools_and_hooks(self):
        tools = [MagicMock(name="MyTool")]
        hooks = [MagicMock()]
        spec = PluginSpec(
            name="test",
            version="1.0.0",
            description="A test plugin",
            tools=tools,
            hooks=hooks,
        )
        assert spec.tools == tools
        assert spec.hooks == hooks


# ---------------------------------------------------------------------------
# PluginRegistry
# ---------------------------------------------------------------------------


class TestPluginRegistry:
    def test_load_plugin(self):
        reg = PluginRegistry()
        spec = PluginSpec(name="test", version="1.0.0")
        reg.load(spec)
        assert reg.get("test") is spec

    def test_load_duplicate_raises(self):
        reg = PluginRegistry()
        spec = PluginSpec(name="test", version="1.0.0")
        reg.load(spec)
        with pytest.raises(ValueError, match="already loaded"):
            reg.load(spec)

    def test_unload_plugin(self):
        reg = PluginRegistry()
        spec = PluginSpec(name="test", version="1.0.0")
        reg.load(spec)
        reg.unload("test")
        assert reg.get("test") is None

    def test_unload_missing_raises(self):
        reg = PluginRegistry()
        with pytest.raises(KeyError, match="not loaded"):
            reg.unload("nonexistent")

    def test_plugins_property(self):
        reg = PluginRegistry()
        reg.load(PluginSpec(name="a", version="1.0"))
        reg.load(PluginSpec(name="b", version="2.0"))
        assert len(reg.plugins) == 2

    def test_plugin_tools_aggregated(self):
        reg = PluginRegistry()
        t1, t2 = MagicMock(), MagicMock()
        reg.load(PluginSpec(name="a", version="1.0", tools=[t1]))
        reg.load(PluginSpec(name="b", version="2.0", tools=[t2]))
        assert reg.plugin_tools == [t1, t2]

    def test_plugin_hooks_aggregated(self):
        reg = PluginRegistry()
        h1, h2 = MagicMock(), MagicMock()
        reg.load(PluginSpec(name="a", version="1.0", hooks=[h1]))
        reg.load(PluginSpec(name="b", version="2.0", hooks=[h2]))
        assert reg.plugin_hooks == [h1, h2]

    def test_load_all_returns_errors(self):
        reg = PluginRegistry()
        spec1 = PluginSpec(name="a", version="1.0")
        spec2 = PluginSpec(name="a", version="2.0")  # duplicate
        errors = reg.load_all([spec1, spec2])
        assert len(errors) == 1
        assert "a" in errors[0]

    def test_load_all_success(self):
        reg = PluginRegistry()
        errors = reg.load_all([
            PluginSpec(name="a", version="1.0"),
            PluginSpec(name="b", version="2.0"),
        ])
        assert errors == []
        assert len(reg.plugins) == 2

    def test_get_returns_none_for_missing(self):
        reg = PluginRegistry()
        assert reg.get("nonexistent") is None


# ---------------------------------------------------------------------------
# Directory discovery
# ---------------------------------------------------------------------------


class TestDiscoverDirectoryPlugins:
    def test_empty_dir(self, tmp_path: Path):
        assert discover_directory_plugins(tmp_path) == []

    def test_nonexistent_dir(self):
        assert discover_directory_plugins("/nonexistent/path") == []

    def test_discovers_plugin_with_manifest(self, tmp_path: Path):
        plugin_dir = tmp_path / "my-plugin"
        plugin_dir.mkdir()
        manifest = {"name": "my-plugin", "version": "1.2.3", "description": "Test"}
        (plugin_dir / "plugin.json").write_text(json.dumps(manifest))

        specs = discover_directory_plugins(tmp_path)
        assert len(specs) == 1
        assert specs[0].name == "my-plugin"
        assert specs[0].version == "1.2.3"
        assert specs[0].description == "Test"

    def test_skips_dir_without_manifest(self, tmp_path: Path):
        (tmp_path / "no-manifest").mkdir()
        assert discover_directory_plugins(tmp_path) == []

    def test_skips_files(self, tmp_path: Path):
        (tmp_path / "not-a-dir.txt").write_text("hello")
        assert discover_directory_plugins(tmp_path) == []

    def test_handles_invalid_json(self, tmp_path: Path):
        plugin_dir = tmp_path / "bad-plugin"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text("not json")
        specs = discover_directory_plugins(tmp_path)
        assert specs == []

    def test_uses_dirname_as_fallback_name(self, tmp_path: Path):
        plugin_dir = tmp_path / "fallback-name"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps({"version": "0.1.0"}))
        specs = discover_directory_plugins(tmp_path)
        assert len(specs) == 1
        assert specs[0].name == "fallback-name"


# ---------------------------------------------------------------------------
# Entry point discovery
# ---------------------------------------------------------------------------


class TestDiscoverEntryPointPlugins:
    def test_no_entry_points(self):
        with patch("duh.plugins.entry_points", return_value=[]):
            specs = discover_entry_point_plugins()
        assert specs == []

    def test_valid_entry_point(self):
        spec = PluginSpec(name="ep-plugin", version="1.0")
        ep = MagicMock()
        ep.name = "ep-plugin"
        ep.load.return_value = spec
        with patch("duh.plugins.entry_points", return_value=[ep]):
            specs = discover_entry_point_plugins()
        assert len(specs) == 1
        assert specs[0].name == "ep-plugin"

    def test_skips_non_pluginspec(self):
        ep = MagicMock()
        ep.name = "bad"
        ep.load.return_value = "not a PluginSpec"
        with patch("duh.plugins.entry_points", return_value=[ep]):
            specs = discover_entry_point_plugins()
        assert specs == []

    def test_handles_load_error(self):
        ep = MagicMock()
        ep.name = "broken"
        ep.load.side_effect = ImportError("missing dep")
        with patch("duh.plugins.entry_points", return_value=[ep]):
            specs = discover_entry_point_plugins()
        assert specs == []


# ---------------------------------------------------------------------------
# Combined discovery
# ---------------------------------------------------------------------------


class TestDiscoverPlugins:
    def test_deduplicates_by_name(self, tmp_path: Path):
        # Entry point plugin
        ep_spec = PluginSpec(name="dupe", version="1.0")
        ep = MagicMock()
        ep.name = "dupe"
        ep.load.return_value = ep_spec

        # Directory plugin with same name
        plugin_dir = tmp_path / "dupe"
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(
            json.dumps({"name": "dupe", "version": "2.0"})
        )

        with patch("duh.plugins.entry_points", return_value=[ep]):
            specs = discover_plugins(extra_dirs=[tmp_path])

        assert len(specs) == 1
        # Entry point takes precedence (discovered first)
        assert specs[0].version == "1.0"

    def test_no_plugins(self):
        with patch("duh.plugins.entry_points", return_value=[]):
            specs = discover_plugins()
        assert specs == []


# ---------------------------------------------------------------------------
# Manifest tool parsing
# ---------------------------------------------------------------------------


class TestParseManifestTools:
    def test_parses_tools_from_manifest(self):
        manifest = {
            "name": "test-plugin",
            "tools": [
                {
                    "name": "my_tool",
                    "description": "Does a thing",
                    "input_schema": {
                        "type": "object",
                        "properties": {"arg": {"type": "string"}},
                        "required": ["arg"],
                    },
                }
            ],
        }
        tools = _parse_manifest_tools(manifest, "test-plugin")
        assert len(tools) == 1
        assert isinstance(tools[0], DeferredTool)
        assert tools[0].name == "my_tool"
        assert tools[0].description == "Does a thing"
        assert tools[0].input_schema["properties"]["arg"]["type"] == "string"
        assert tools[0].source == "plugin:test-plugin"

    def test_multiple_tools(self):
        manifest = {
            "tools": [
                {"name": "tool_a", "description": "A"},
                {"name": "tool_b", "description": "B"},
            ]
        }
        tools = _parse_manifest_tools(manifest, "multi")
        assert len(tools) == 2
        assert tools[0].name == "tool_a"
        assert tools[1].name == "tool_b"

    def test_no_tools_key(self):
        tools = _parse_manifest_tools({"name": "bare"}, "bare")
        assert tools == []

    def test_tools_not_a_list(self):
        tools = _parse_manifest_tools({"tools": "not-a-list"}, "bad")
        assert tools == []

    def test_skips_non_dict_entry(self):
        tools = _parse_manifest_tools({"tools": ["not-a-dict"]}, "bad")
        assert tools == []

    def test_skips_entry_without_name(self):
        tools = _parse_manifest_tools(
            {"tools": [{"description": "no name"}]}, "bad"
        )
        assert tools == []

    def test_defaults_schema_when_missing(self):
        tools = _parse_manifest_tools(
            {"tools": [{"name": "minimal"}]}, "p"
        )
        assert len(tools) == 1
        assert tools[0].input_schema == {"type": "object", "properties": {}}
        assert tools[0].description == ""

    def test_defaults_description_when_missing(self):
        tools = _parse_manifest_tools(
            {"tools": [{"name": "nodesc", "input_schema": {"type": "object"}}]},
            "p",
        )
        assert tools[0].description == ""


# ---------------------------------------------------------------------------
# Directory discovery with tools
# ---------------------------------------------------------------------------


class TestDiscoverDirectoryPluginsWithTools:
    def _make_plugin(self, tmp_path: Path, name: str, manifest: dict) -> Path:
        """Helper to create a plugin directory with a manifest."""
        plugin_dir = tmp_path / name
        plugin_dir.mkdir()
        (plugin_dir / "plugin.json").write_text(json.dumps(manifest))
        return plugin_dir

    def test_loads_tools_from_manifest(self, tmp_path: Path):
        self._make_plugin(tmp_path, "with-tools", {
            "name": "with-tools",
            "version": "1.0.0",
            "description": "Has tools",
            "tools": [
                {
                    "name": "fetch_data",
                    "description": "Fetches data from API",
                    "input_schema": {
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                    },
                },
                {
                    "name": "transform",
                    "description": "Transforms data",
                },
            ],
        })

        specs = discover_directory_plugins(tmp_path)
        assert len(specs) == 1
        spec = specs[0]
        assert spec.name == "with-tools"
        assert len(spec.tools) == 2

        tool0 = spec.tools[0]
        assert isinstance(tool0, DeferredTool)
        assert tool0.name == "fetch_data"
        assert tool0.description == "Fetches data from API"
        assert tool0.source == "plugin:with-tools"
        assert "url" in tool0.input_schema["properties"]

        tool1 = spec.tools[1]
        assert tool1.name == "transform"
        assert tool1.input_schema == {"type": "object", "properties": {}}

    def test_no_tools_in_manifest(self, tmp_path: Path):
        self._make_plugin(tmp_path, "no-tools", {
            "name": "no-tools",
            "version": "0.1.0",
        })
        specs = discover_directory_plugins(tmp_path)
        assert len(specs) == 1
        assert specs[0].tools == []

    def test_registry_aggregates_plugin_tools(self, tmp_path: Path):
        """End-to-end: discover + load into registry, then check plugin_tools."""
        self._make_plugin(tmp_path, "alpha", {
            "name": "alpha",
            "version": "1.0",
            "tools": [{"name": "alpha_tool", "description": "Alpha"}],
        })
        self._make_plugin(tmp_path, "beta", {
            "name": "beta",
            "version": "2.0",
            "tools": [
                {"name": "beta_one", "description": "B1"},
                {"name": "beta_two", "description": "B2"},
            ],
        })

        specs = discover_directory_plugins(tmp_path)
        reg = PluginRegistry()
        reg.load_all(specs)

        all_tools = reg.plugin_tools
        assert len(all_tools) == 3
        names = {t.name for t in all_tools}
        assert names == {"alpha_tool", "beta_one", "beta_two"}

    def test_discover_plugins_includes_dir_tools(self, tmp_path: Path):
        """discover_plugins() with extra_dirs picks up manifest tools."""
        self._make_plugin(tmp_path, "dir-plugin", {
            "name": "dir-plugin",
            "version": "0.5.0",
            "tools": [{"name": "dp_tool", "description": "From dir"}],
        })
        with patch("duh.plugins.entry_points", return_value=[]):
            specs = discover_plugins(extra_dirs=[tmp_path])
        assert len(specs) == 1
        assert len(specs[0].tools) == 1
        assert specs[0].tools[0].name == "dp_tool"

    def test_bad_tools_dont_break_plugin(self, tmp_path: Path):
        """Malformed tool entries are skipped; valid ones still load."""
        self._make_plugin(tmp_path, "mixed", {
            "name": "mixed",
            "version": "1.0",
            "tools": [
                "not-a-dict",
                {"description": "no name"},
                {"name": "good_tool", "description": "Works"},
            ],
        })
        specs = discover_directory_plugins(tmp_path)
        assert len(specs) == 1
        assert len(specs[0].tools) == 1
        assert specs[0].tools[0].name == "good_tool"
