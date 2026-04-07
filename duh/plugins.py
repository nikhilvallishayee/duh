"""Plugin system -- discover, load, and register plugins.

See ADR-014 for the full rationale.

A plugin is a Python package that provides tools, hooks, or both.
Discovery uses Python's standard ``entry_points`` mechanism.

Install a plugin:
    pip install duh-plugin-foo

The plugin declares an entry point in its pyproject.toml:
    [project.entry-points."duh.plugins"]
    foo = "duh_plugin_foo:plugin"

The entry point must be a PluginSpec instance.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Plugin spec -- what a plugin provides
# ---------------------------------------------------------------------------

@dataclass
class PluginSpec:
    """What a plugin provides to D.U.H.

    A plugin provides some combination of tools and hooks.
    Future: commands, agent types, provider adapters.

    Attributes:
        name: Unique plugin name.
        version: Semver version string.
        description: Human-readable description.
        tools: List of Tool instances the plugin provides.
        hooks: List of HookConfig instances the plugin provides.
    """

    name: str
    version: str
    description: str = ""
    tools: list[Any] = field(default_factory=list)
    hooks: list[Any] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Plugin discovery
# ---------------------------------------------------------------------------

def discover_entry_point_plugins() -> list[PluginSpec]:
    """Discover plugins via Python entry_points.

    Scans the ``duh.plugins`` entry point group. Each entry point
    must resolve to a PluginSpec instance.

    Returns:
        List of discovered PluginSpec objects.
    """
    specs: list[PluginSpec] = []
    for ep in entry_points(group="duh.plugins"):
        try:
            obj = ep.load()
            if isinstance(obj, PluginSpec):
                specs.append(obj)
            else:
                logger.warning(
                    "Plugin entry point %r did not return a PluginSpec "
                    "(got %s), skipping.",
                    ep.name,
                    type(obj).__name__,
                )
        except Exception as exc:
            logger.warning(
                "Failed to load plugin entry point %r: %s", ep.name, exc
            )
    return specs


def discover_directory_plugins(plugin_dir: str | Path) -> list[PluginSpec]:
    """Discover plugins from a local directory.

    Each subdirectory with a ``plugin.json`` manifest is loaded.
    The manifest must contain at least ``name`` and ``version``.

    Optionally, the directory can contain:
    - ``tools.py`` with tool classes
    - ``hooks.json`` with hook configurations

    Args:
        plugin_dir: Path to the plugin directory.

    Returns:
        List of discovered PluginSpec objects.
    """
    plugin_dir = Path(plugin_dir)
    if not plugin_dir.is_dir():
        return []

    specs: list[PluginSpec] = []
    for child in sorted(plugin_dir.iterdir()):
        if not child.is_dir():
            continue
        manifest_path = child / "plugin.json"
        if not manifest_path.exists():
            continue
        try:
            manifest = json.loads(manifest_path.read_text())
            name = manifest.get("name", child.name)
            version = manifest.get("version", "0.0.0")
            description = manifest.get("description", "")
            spec = PluginSpec(
                name=name,
                version=version,
                description=description,
            )
            # Future: load tools.py and hooks.json from the directory
            specs.append(spec)
        except Exception as exc:
            logger.warning(
                "Failed to load plugin from %s: %s", child, exc
            )
    return specs


def discover_plugins(
    *,
    extra_dirs: list[str | Path] | None = None,
) -> list[PluginSpec]:
    """Discover all available plugins from all sources.

    Sources (in order):
    1. Python entry_points (pip-installed plugins)
    2. Extra directories (--plugin-dir flag or DUH_PLUGIN_DIR env)

    Args:
        extra_dirs: Additional directories to scan for plugins.

    Returns:
        Combined list of PluginSpec objects, deduplicated by name.
    """
    seen: set[str] = set()
    result: list[PluginSpec] = []

    # Entry points first
    for spec in discover_entry_point_plugins():
        if spec.name not in seen:
            seen.add(spec.name)
            result.append(spec)

    # Directory plugins
    for dir_path in extra_dirs or []:
        for spec in discover_directory_plugins(dir_path):
            if spec.name not in seen:
                seen.add(spec.name)
                result.append(spec)

    return result


# ---------------------------------------------------------------------------
# Plugin registry -- load and manage active plugins
# ---------------------------------------------------------------------------

@dataclass
class PluginRegistry:
    """Manages loaded plugins and their contributions.

    After discovery, plugins are loaded into the registry. Their tools
    are merged into the tool pool, and their hooks are registered.
    """

    _plugins: dict[str, PluginSpec] = field(default_factory=dict)

    @property
    def plugins(self) -> list[PluginSpec]:
        """All loaded plugins."""
        return list(self._plugins.values())

    @property
    def plugin_tools(self) -> list[Any]:
        """All tools from all loaded plugins."""
        tools: list[Any] = []
        for spec in self._plugins.values():
            tools.extend(spec.tools)
        return tools

    @property
    def plugin_hooks(self) -> list[Any]:
        """All hooks from all loaded plugins."""
        hooks: list[Any] = []
        for spec in self._plugins.values():
            hooks.extend(spec.hooks)
        return hooks

    def load(self, spec: PluginSpec) -> None:
        """Load a plugin into the registry.

        Raises ValueError if a plugin with the same name is already loaded.
        """
        if spec.name in self._plugins:
            raise ValueError(
                f"Plugin {spec.name!r} is already loaded."
            )
        self._plugins[spec.name] = spec
        logger.info("Loaded plugin: %s v%s", spec.name, spec.version)

    def unload(self, name: str) -> None:
        """Unload a plugin by name.

        Raises KeyError if the plugin is not loaded.
        """
        if name not in self._plugins:
            raise KeyError(f"Plugin {name!r} is not loaded.")
        del self._plugins[name]
        logger.info("Unloaded plugin: %s", name)

    def get(self, name: str) -> PluginSpec | None:
        """Get a loaded plugin by name, or None."""
        return self._plugins.get(name)

    def load_all(self, specs: list[PluginSpec]) -> list[str]:
        """Load multiple plugins. Returns list of error messages (if any)."""
        errors: list[str] = []
        for spec in specs:
            try:
                self.load(spec)
            except Exception as exc:
                errors.append(f"{spec.name}: {exc}")
        return errors
