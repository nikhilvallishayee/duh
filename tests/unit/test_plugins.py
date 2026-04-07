"""Tests for plugin system (ADR-014)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from duh.plugins import (
    PluginSpec,
    PluginRegistry,
    discover_directory_plugins,
    discover_entry_point_plugins,
    discover_plugins,
)


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
