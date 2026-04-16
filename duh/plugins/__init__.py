"""Plugin system -- discover, load, and register plugins.

See ADR-014 for the full rationale.

A plugin is a Python package that provides tools, hooks, or both.

Two discovery mechanisms:

1. **Entry points** (pip-installed plugins):
       pip install duh-plugin-foo
   The plugin declares an entry point in its pyproject.toml:
       [project.entry-points."duh.plugins"]
       foo = "duh_plugin_foo:plugin"
   The entry point must be a PluginSpec instance.

2. **Directory plugins** (``.duh/plugins/`` or ``--plugin-dir``):
   Each subdirectory with a ``plugin.json`` manifest is loaded.
   Tools declared in the manifest become DeferredTool objects
   available via ToolSearch.

   Example ``plugin.json``::

       {
           "name": "my-plugin",
           "version": "1.0.0",
           "description": "Does useful things",
           "tools": [
               {
                   "name": "my_tool",
                   "description": "A useful tool",
                   "input_schema": {
                       "type": "object",
                       "properties": {"arg": {"type": "string"}},
                       "required": ["arg"]
                   }
               }
           ]
       }
"""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
import os
from dataclasses import dataclass, field
from importlib.metadata import entry_points
from pathlib import Path
from typing import Any, Callable

from duh.plugins.manifest import load_manifest, compute_manifest_hash
from duh.plugins.trust_store import TrustStore

logger = logging.getLogger(__name__)


def _default_trust_store_path() -> Path:
    """Default location for the plugin trust store."""
    return Path.home() / ".duh" / "trust.json"


def _entry_point_module_hash(spec: Any, ep: Any) -> str:
    """Compute a stable SHA-256 fingerprint for an entry-point plugin.

    The fingerprint covers the source of the resolved object's module so that
    a tampered or upgraded distribution produces a different hash, triggering
    TOFU re-confirmation.  Falls back to the entry-point's module name + value
    if the source is unavailable (e.g. C extensions, frozen modules).
    """
    payload_parts: list[str] = [
        f"name={spec.name}",
        f"version={spec.version}",
        f"ep_value={getattr(ep, 'value', '')}",
    ]
    try:
        loaded = ep.load()
        module = inspect.getmodule(loaded)
        if module is not None:
            try:
                src = inspect.getsource(module)
                payload_parts.append(f"src_sha256={hashlib.sha256(src.encode()).hexdigest()}")
            except (OSError, TypeError):
                payload_parts.append(f"module={getattr(module, '__name__', '?')}")
    except Exception:  # pragma: no cover - defensive
        pass
    payload = "\n".join(payload_parts).encode()
    return hashlib.sha256(payload).hexdigest()


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

def discover_entry_point_plugins(
    *,
    trust_store: TrustStore | None = None,
    trust_entry_points: bool | None = None,
    confirm_tofu: Callable | None = None,
) -> list[PluginSpec]:
    """Discover plugins via Python entry_points (with TOFU verification).

    Scans the ``duh.plugins`` entry point group. Each entry point must
    resolve to a :class:`PluginSpec` instance. By default, every entry-point
    plugin is run through the same TOFU trust store as directory plugins so
    a tampered or unexpected distribution cannot silently inject tools/hooks
    into the harness (SEC-MEDIUM-6).

    Trust resolution:

    * On first encounter, the plugin's module-source hash is recorded and the
      plugin is loaded only if ``confirm_tofu`` returns True (or
      ``trust_entry_points`` is explicitly enabled).
    * If the hash matches the stored entry, the plugin is loaded.
    * If the hash differs ("signature_mismatch") or has been revoked, the
      plugin is skipped with a warning.

    Args:
        trust_store: Trust store to consult. Defaults to ``~/.duh/trust.json``.
        trust_entry_points: When ``True``, accept first-use entry-point
            plugins without prompting (equivalent to passing
            ``confirm_tofu=lambda _: True``). When ``False``, refuse all
            untrusted entry-point plugins. When ``None`` (default), respect
            the ``DUH_TRUST_ENTRYPOINT_PLUGINS`` env var (any truthy value
            allows; otherwise refuse and warn).
        confirm_tofu: Optional interactive callback invoked on first use.

    Returns:
        List of verified PluginSpec objects.
    """
    if trust_store is None:
        trust_store = TrustStore(store_path=_default_trust_store_path())
    if trust_entry_points is None:
        env_flag = os.environ.get("DUH_TRUST_ENTRYPOINT_PLUGINS", "").strip().lower()
        trust_entry_points = env_flag in ("1", "true", "yes", "on")

    specs: list[PluginSpec] = []
    for ep in entry_points(group="duh.plugins"):
        try:
            obj = ep.load()
        except Exception as exc:
            logger.warning(
                "Failed to load plugin entry point %r: %s", ep.name, exc
            )
            continue

        if not isinstance(obj, PluginSpec):
            logger.warning(
                "Plugin entry point %r did not return a PluginSpec "
                "(got %s), skipping.",
                ep.name,
                type(obj).__name__,
            )
            continue

        sig_hash = _entry_point_module_hash(obj, ep)
        result = trust_store.verify(obj.name, sig_hash)

        if result.status == "trusted":
            specs.append(obj)
            continue
        if result.status == "first_use":
            accepted = False
            if confirm_tofu is not None:
                try:
                    accepted = bool(confirm_tofu(obj))
                except Exception as exc:  # pragma: no cover - defensive
                    logger.warning(
                        "TOFU prompt for entry-point plugin %r raised: %s",
                        obj.name, exc,
                    )
                    accepted = False
            elif trust_entry_points:
                accepted = True
            if accepted:
                trust_store.add(obj.name, sig_hash)
                specs.append(obj)
            else:
                logger.warning(
                    "Skipping entry-point plugin %r: untrusted (first use). "
                    "Re-run with --trust-entrypoint-plugins or set "
                    "DUH_TRUST_ENTRYPOINT_PLUGINS=1 to allow.",
                    obj.name,
                )
            continue
        if result.status == "revoked":
            logger.warning(
                "Skipping entry-point plugin %r: revoked (%s).",
                obj.name, result.reason,
            )
            continue
        if result.status == "signature_mismatch":
            logger.warning(
                "Skipping entry-point plugin %r: module hash changed since "
                "first trust (saved=%s, current=%s) — possible tampering or "
                "upgrade. Remove the entry from the trust store to re-confirm.",
                obj.name, result.known, result.provided,
            )
            continue
        logger.warning(
            "Skipping entry-point plugin %r: unknown trust status %r.",
            obj.name, result.status,
        )
    return specs


def _parse_manifest_tools(
    manifest: dict[str, Any], plugin_name: str
) -> list[Any]:
    """Parse tool definitions from a plugin manifest into DeferredTool objects.

    Each tool entry must have at least ``name`` and ``description``.
    ``input_schema`` is optional (defaults to empty object schema).

    Args:
        manifest: Parsed plugin.json dict.
        plugin_name: Name of the owning plugin (used as source prefix).

    Returns:
        List of DeferredTool instances.
    """
    from duh.tools.tool_search import DeferredTool

    raw_tools = manifest.get("tools", [])
    if not isinstance(raw_tools, list):
        logger.warning(
            "Plugin %r: 'tools' must be a list, got %s. Skipping tools.",
            plugin_name,
            type(raw_tools).__name__,
        )
        return []

    result: list[DeferredTool] = []
    for i, entry in enumerate(raw_tools):
        if not isinstance(entry, dict):
            logger.warning(
                "Plugin %r: tools[%d] is not a dict, skipping.", plugin_name, i
            )
            continue
        tool_name = entry.get("name")
        if not tool_name:
            logger.warning(
                "Plugin %r: tools[%d] missing 'name', skipping.", plugin_name, i
            )
            continue
        result.append(
            DeferredTool(
                name=tool_name,
                description=entry.get("description", ""),
                input_schema=entry.get("input_schema", {"type": "object", "properties": {}}),
                source=f"plugin:{plugin_name}",
            )
        )
    return result


def discover_directory_plugins(plugin_dir: str | Path) -> list[PluginSpec]:
    """Discover plugins from a local directory.

    Each subdirectory with a ``plugin.json`` manifest is loaded.
    The manifest must contain at least ``name`` and ``version``.

    Tools declared in the manifest's ``tools`` array are parsed into
    :class:`~duh.tools.tool_search.DeferredTool` objects and attached
    to the resulting :class:`PluginSpec`.

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
            tools = _parse_manifest_tools(manifest, name)
            spec = PluginSpec(
                name=name,
                version=version,
                description=description,
                tools=tools,
            )
            specs.append(spec)
        except Exception as exc:
            logger.warning(
                "Failed to load plugin from %s: %s", child, exc
            )
    return specs


def discover_plugins(
    *,
    extra_dirs: list[str | Path] | None = None,
    trust_store: TrustStore | None = None,
    trust_entry_points: bool | None = None,
    confirm_tofu: Callable | None = None,
) -> list[PluginSpec]:
    """Discover all available plugins from all sources.

    Sources (in order):
    1. Python entry_points (pip-installed plugins) — TOFU-verified.
    2. Extra directories (--plugin-dir flag or DUH_PLUGIN_DIR env).

    Args:
        extra_dirs: Additional directories to scan for plugins.
        trust_store: Trust store for entry-point TOFU verification.
        trust_entry_points: See :func:`discover_entry_point_plugins`.
        confirm_tofu: Optional confirmation callback for first-use plugins.

    Returns:
        Combined list of PluginSpec objects, deduplicated by name.
    """
    seen: set[str] = set()
    result: list[PluginSpec] = []

    # Entry points first
    for spec in discover_entry_point_plugins(
        trust_store=trust_store,
        trust_entry_points=trust_entry_points,
        confirm_tofu=confirm_tofu,
    ):
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


# ---------------------------------------------------------------------------
# Signed manifest + TOFU verification (ADR-054, 7.7)
# ---------------------------------------------------------------------------


class PluginError(RuntimeError):
    """Raised when plugin loading or verification fails."""


def load_verified_plugin(
    manifest_path: Path,
    trust_store: TrustStore,
    *,
    confirm_tofu: Callable | None = None,
) -> Any:
    """Load and verify a plugin manifest against the trust store.

    On first encounter (TOFU), calls ``confirm_tofu(manifest)`` and trusts
    the plugin if it returns True. Raises PluginError if the user refuses,
    the key is revoked, or the signature does not match the stored hash.

    Returns the PluginManifest with a ``_sig_hash`` attribute attached.

    Raises:
        FileNotFoundError: If manifest_path does not exist.
        PluginError: On TOFU rejection, revocation, or signature mismatch.
    """
    raw_data = json.loads(manifest_path.read_text())  # raises FileNotFoundError if missing
    manifest = load_manifest(manifest_path)
    sig_hash = compute_manifest_hash(raw_data)
    # Attach for caller inspection (frozen dataclass, so use object.__setattr__)
    object.__setattr__(manifest, "_sig_hash", sig_hash)

    result = trust_store.verify(manifest.plugin_name, sig_hash)

    if result.status == "trusted":
        return manifest
    elif result.status == "first_use":
        if confirm_tofu and confirm_tofu(manifest):
            trust_store.add(manifest.plugin_name, sig_hash)
            return manifest
        raise PluginError(
            f"User refused TOFU trust for plugin {manifest.plugin_name!r}"
        )
    elif result.status == "revoked":
        raise PluginError(
            f"Plugin {manifest.plugin_name!r} signing key revoked: {result.reason}"
        )
    elif result.status == "signature_mismatch":
        raise PluginError(
            f"Plugin {manifest.plugin_name!r} signature invalid — possible tampering. "
            f"Saved: {result.known}, new: {result.provided}"
        )
    else:
        raise PluginError(
            f"Unknown verification status for {manifest.plugin_name!r}: {result.status}"
        )


def load_plugin_from_dir(
    plugin_dir: Path,
    trust_store: TrustStore | None = None,
    confirm_tofu: Callable | None = None,
) -> Any:
    """Load a plugin from a directory, verifying its manifest.json.

    If no trust_store is provided, uses the default location
    ``~/.duh/trust.json``.
    """
    manifest_path = plugin_dir / "manifest.json"
    if trust_store is None:
        trust_store = TrustStore(store_path=_default_trust_store_path())
    return load_verified_plugin(manifest_path, trust_store, confirm_tofu=confirm_tofu)
