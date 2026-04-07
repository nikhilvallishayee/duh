# ADR-014: Plugin Architecture

**Status**: Accepted  
**Date**: 2026-04-06

## Context

Claude Code has a substantial plugin system spanning ~45 files and ~5000+ LOC in `src/utils/plugins/`. It handles marketplace-based discovery (official and third-party), git-based installation, NPM packages, version management, auto-update, blocklists, dependency resolution, zip caching, symlink management, and even an anti-impersonation system for marketplace names.

Key pieces:
- `pluginLoader.ts` -- discovers and loads plugins from marketplaces and local directories. Handles manifest validation, hooks loading, duplicate detection, enable/disable state
- `schemas.ts` -- Zod schemas for plugin manifests, marketplace entries, anti-impersonation patterns
- `pluginDirectories.ts` -- manages plugin installation paths
- `loadPluginAgents.ts`, `loadPluginCommands.ts`, `loadPluginHooks.ts` -- load specific plugin components
- `marketplaceManager.ts` -- marketplace registry, fetching, caching
- `pluginBlocklist.ts` -- security blocklist for known-bad plugins
- `pluginVersioning.ts` -- semver-based version management

Plugin directory structure in Claude Code:
```
my-plugin/
  plugin.json          # Manifest with metadata
  commands/            # Custom slash commands (.md files)
  agents/              # Custom agent definitions (.md files)
  hooks/               # Hook configurations (hooks.json)
```

### What D.U.H. simplifies

Claude Code's plugin system is enterprise-grade: marketplace federation, git transports, NPM resolution, SSRF guards, homograph attack prevention, policy-based source filtering. D.U.H. needs none of this at v0.1.

What matters: a plugin is a package that provides tools, hooks, commands, or provider adapters. Discovery uses Python's standard `entry_points` mechanism. No marketplace. No git cloning. No zip caching.

### What D.U.H. keeps

| Claude Code feature | D.U.H. | Rationale |
|---------------------|--------|-----------|
| Plugin provides tools | Yes | Core use case |
| Plugin provides hooks | Yes | Lifecycle extensibility |
| Plugin provides commands | Future | Slash commands not implemented yet |
| Plugin provides agents | Future | Agent definitions via plugin |
| Plugin manifest | Yes (simplified) | `plugin.json` with name, version, description |
| Marketplace discovery | No | Python entry_points is sufficient |
| Git-based install | No | `pip install` handles distribution |
| Version management | No | pip handles versions |
| Auto-update | No | Complexity not justified |
| Blocklist/security | No | Trust pip ecosystem for now |
| Plugin-only policy | No | Enterprise feature, not needed |

## Decision

### 1. Plugin = a Python package with entry points

A D.U.H. plugin is a regular Python package that declares entry points in its `pyproject.toml`:

```toml
[project.entry-points."duh.plugins"]
my-plugin = "my_plugin:plugin"
```

The entry point must be a `PluginSpec` object:

```python
@dataclass
class PluginSpec:
    name: str
    version: str
    description: str = ""
    tools: list[Any] = field(default_factory=list)
    hooks: list[HookConfig] = field(default_factory=list)
```

### 2. Plugin discovery via entry_points

```python
from importlib.metadata import entry_points

def discover_plugins() -> list[PluginSpec]:
    specs = []
    for ep in entry_points(group="duh.plugins"):
        plugin = ep.load()
        if isinstance(plugin, PluginSpec):
            specs.append(plugin)
    return specs
```

This is Python's standard mechanism. It works with `pip install`, editable installs (`pip install -e .`), and virtual environments. No custom discovery code needed.

### 3. Directory-based plugins (for development)

For local development, plugins can also be loaded from a directory:

```
~/.config/duh/plugins/my-plugin/
  plugin.json       # {"name": "my-plugin", "version": "0.1.0"}
  tools.py          # Tool classes
  hooks.json        # Hook configurations
```

The `--plugin-dir` CLI flag or `DUH_PLUGIN_DIR` env var adds a directory to the plugin search path. This is the same pattern Claude Code uses for session-only plugins.

### 4. Plugin lifecycle

```
discover() -> load() -> register() -> [use] -> unload()
```

- **discover**: Find plugins via entry_points + directory scanning
- **load**: Import the module, validate the PluginSpec
- **register**: Merge tools into the tool pool, register hooks
- **use**: Normal operation (tools and hooks are active)
- **unload**: Remove tools and hooks (on shutdown or explicit unload)

### 5. Plugin tools are first-class

Plugin tools implement the same `Tool` protocol as core tools. They get the same schema validation, approval flow, and error handling. The kernel does not know the difference.

```python
# A plugin tool
class MyCustomTool:
    name = "MyCustom"
    description = "Does something custom"
    input_schema = {"type": "object", "properties": {...}}

    async def run(self, input: dict, context: dict) -> str:
        return "result"
```

### 6. Plugin hooks merge with config hooks

Hooks from plugins are registered alongside hooks from the config file. Plugin hooks run after config hooks for the same event (lower priority).

## Architecture

```
CLI startup
  |
  discover_plugins()
  |  - entry_points(group="duh.plugins")
  |  - scan plugin directories
  |
  load each PluginSpec
  |  - validate name, version
  |  - import tool classes
  |  - parse hook configs
  |
  register
  |  - merge tools into tool pool
  |  - register hooks in HookRegistry
  |
  Engine runs with merged tool pool + hooks
```

## Consequences

- Installing a plugin = `pip install duh-plugin-foo`, zero config
- Plugin tools are indistinguishable from core tools at the kernel level
- Plugin hooks integrate seamlessly with the hook system
- No marketplace infrastructure to build or maintain
- Python's package ecosystem handles versioning, dependencies, distribution
- Directory-based plugins enable rapid local development
- Future: plugin can provide slash commands, agent types, provider adapters
