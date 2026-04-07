# ADR-015: Configuration

**Status**: Accepted  
**Date**: 2026-04-06

## Context

Production AI coding agents typically have multi-layered configuration systems with project-level instructions, user settings, and environment-specific overrides.

### Settings layers (Claude Code)

Settings are loaded from 5 sources in increasing priority:

1. **User settings** (`~/.claude/settings.json`) -- global preferences
2. **Project settings** (`.claude/settings.json`) -- shared per-repo, checked in
3. **Local settings** (`.claude/settings.local.json`) -- gitignored, per-machine
4. **Flag settings** (`--settings path` CLI flag) -- explicit override
5. **Policy settings** (`/etc/claude-code/managed-settings.json`) -- admin/MDM

Later sources override earlier ones. Settings include permissions, hooks, MCP servers, environment variables, model preferences, and feature flags.

### CLAUDE.md (instruction files)

Separate from JSON settings, `CLAUDE.md` files provide natural-language instructions that are injected into the system prompt. These are loaded from 4 tiers:

1. **Managed** (`/etc/claude-code/CLAUDE.md`) -- admin instructions for all users
2. **User** (`~/.claude/CLAUDE.md`) -- personal global instructions
3. **Project** (`CLAUDE.md`, `.claude/CLAUDE.md`, `.claude/rules/*.md`) -- per-repo instructions, traversed from cwd up to git root
4. **Local** (`CLAUDE.local.md`) -- gitignored personal project instructions

Project instruction files support directory traversal, include directives, frontmatter parsing, and gitignore exclusion patterns.

### What D.U.H. simplifies

The core insight: there are two kinds of configuration.

1. **Structured settings** (JSON) -- permissions, hooks, MCP servers, model defaults
2. **Instruction files** (Markdown) -- natural-language instructions for the system prompt

Both need a precedence chain. Both need to be simple to understand.

D.U.H. renames `CLAUDE.md` to `DUH.md` (same concept, different branding). D.U.H. also supports the open-standard `AGENTS.md` file for cross-tool compatibility.

### What D.U.H. keeps

| Claude Code feature | D.U.H. | Rationale |
|---------------------|--------|-----------|
| Multi-layer settings precedence | Yes (simplified) | Essential for override chains |
| CLAUDE.md instruction files | Yes, as DUH.md | Core feature, renamed |
| AGENTS.md support | Yes | Open standard compatibility |
| `@include` directives | Future | Useful but not essential for v0.1 |
| `.claude/rules/*.md` directory | Yes, as `.duh/rules/*.md` | Useful for splitting instructions |
| Managed/policy settings | No | Enterprise feature, not needed |
| MDM/HKCU settings | No | Windows/macOS enterprise, not needed |
| Remote managed settings | No | Requires server infrastructure |
| Settings validation with Zod | Yes (Pydantic) | Type-safe config |

## Decision

### 1. Settings precedence: flags > env > project > user

Four layers, in increasing priority:

| Priority | Source | Path | Scope |
|----------|--------|------|-------|
| 1 (lowest) | User config | `~/.config/duh/settings.json` | All projects |
| 2 | Project config | `.duh/settings.json` | This repo (checked in) |
| 3 | Environment | `DUH_*` env vars | This session |
| 4 (highest) | CLI flags | `--model`, `--provider`, etc. | This invocation |

No managed/policy layer. No local (gitignored) settings layer for v0.1. If needed later, add `.duh/settings.local.json` between project and env.

### 2. Settings file format

```json
{
    "model": "claude-sonnet-4-6",
    "provider": "anthropic",
    "max_turns": 20,
    "permissions": {
        "allow": [
            {"tool": "Read"},
            {"tool": "Glob"},
            {"tool": "Grep"}
        ],
        "deny": [
            {"tool": "Bash", "command": "rm -rf /"}
        ],
        "default_mode": "ask"
    },
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [{"type": "command", "command": "echo lint"}]
            }
        ]
    },
    "mcpServers": {
        "filesystem": {
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]
        }
    }
}
```

### 3. DUH.md instruction files

D.U.H. loads instruction files from these locations (lowest to highest priority):

1. `~/.config/duh/DUH.md` -- user-global instructions
2. `DUH.md` or `.duh/DUH.md` in each directory from git root to cwd -- project instructions
3. `.duh/rules/*.md` -- additional rule files in the project

All found instruction files are concatenated and injected into the system prompt, with later (higher-priority) files appearing last (models pay more attention to later content).

### 4. AGENTS.md support

D.U.H. also reads `AGENTS.md` files per the open standard. If both `DUH.md` and `AGENTS.md` exist, both are loaded (`DUH.md` first, `AGENTS.md` second). This ensures compatibility with tools that use the `AGENTS.md` convention.

### 5. Environment variable mapping

Every settings key has a corresponding env var with a `DUH_` prefix:

| Setting | Env var | Example |
|---------|---------|---------|
| `model` | `DUH_MODEL` | `DUH_MODEL=claude-opus-4-6` |
| `provider` | `DUH_PROVIDER` | `DUH_PROVIDER=ollama` |
| `max_turns` | `DUH_MAX_TURNS` | `DUH_MAX_TURNS=50` |

### 6. Config loading implementation

```python
@dataclass
class Config:
    model: str = ""
    provider: str = ""
    max_turns: int = 10
    permissions: dict = field(default_factory=dict)
    hooks: dict = field(default_factory=dict)
    mcp_servers: dict = field(default_factory=dict)

def load_config(
    *,
    cli_args: dict | None = None,
    cwd: str = ".",
) -> Config:
    """Load config from all sources, merging by precedence."""
    config = Config()

    # Layer 1: user config
    user_path = Path("~/.config/duh/settings.json").expanduser()
    if user_path.exists():
        merge(config, load_json(user_path))

    # Layer 2: project config
    project_path = find_project_config(cwd)
    if project_path:
        merge(config, load_json(project_path))

    # Layer 3: environment variables
    apply_env_overrides(config)

    # Layer 4: CLI flags
    if cli_args:
        apply_cli_overrides(config, cli_args)

    return config
```

### 7. Instruction file loading

```python
def load_instructions(cwd: str = ".") -> list[str]:
    """Load all DUH.md and AGENTS.md files, ordered by precedence."""
    instructions = []

    # User-global instructions
    user_duh = Path("~/.config/duh/DUH.md").expanduser()
    if user_duh.exists():
        instructions.append(user_duh.read_text())

    # Project instructions (git root to cwd)
    for dir in dirs_from_root_to_cwd(cwd):
        for name in ["DUH.md", ".duh/DUH.md"]:
            path = dir / name
            if path.exists():
                instructions.append(path.read_text())

        # Rules directory
        rules_dir = dir / ".duh" / "rules"
        if rules_dir.is_dir():
            for md in sorted(rules_dir.glob("*.md")):
                instructions.append(md.read_text())

        # AGENTS.md (open standard)
        agents_md = dir / "AGENTS.md"
        if agents_md.exists():
            instructions.append(agents_md.read_text())

    return instructions
```

## Architecture

```
CLI startup
  |
  load_config(cli_args=args, cwd=os.getcwd())
  |  - ~/.config/duh/settings.json  (user)
  |  - .duh/settings.json           (project)
  |  - DUH_* env vars               (env)
  |  - --model, --provider, etc.    (flags)
  |
  load_instructions(cwd=os.getcwd())
  |  - ~/.config/duh/DUH.md         (user)
  |  - DUH.md / .duh/DUH.md         (project, per directory)
  |  - .duh/rules/*.md              (project rules)
  |  - AGENTS.md                    (open standard)
  |
  system_prompt = base_prompt + "\n".join(instructions)
  |
  Engine(config=config, system_prompt=system_prompt)
```

## Consequences

- Configuration is predictable: later sources always win
- `DUH.md` gives users a familiar, natural-language way to configure behavior
- `AGENTS.md` support makes D.U.H. compatible with the broader ecosystem
- Environment variables enable CI/CD and containerized usage
- No admin/policy layer simplifies the code significantly
- Pydantic validation catches config errors early with clear messages
- Future: add `@include` directives, local settings, managed settings when needed
