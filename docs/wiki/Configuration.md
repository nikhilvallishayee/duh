# Configuration Reference

D.U.H. is configured through a layered system. Settings from multiple sources are merged with a strict precedence order, so higher-priority sources override lower ones.

**Global precedence (highest wins):**

| Priority | Source | Scope |
|----------|--------|-------|
| 4 (highest) | CLI flags | Invocation |
| 3 | Environment variables (`DUH_*`) | Shell session |
| 2 | Project config (`.duh/settings.json`) | Repository |
| 1 (lowest) | User config (`~/.config/duh/settings.json`) | Machine-wide |

---

## 1. CLI Flags

All flags are passed directly on the command line. They have the highest precedence and override every other source.

### Core Flags

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--version` | — | — | Print the version and exit. |
| `-p`, `--prompt` | string | — | Run in print mode: execute a single prompt and exit. No interactive REPL. |
| `--model` | string | auto | Model to use. Auto-detected from provider when omitted. |
| `--fallback-model` | string | — | Fallback model if the primary is overloaded or unavailable. |
| `--provider` | choice | auto | LLM provider. Choices: `anthropic`, `litellm`, `ollama`, `openai`. Auto-detected from API keys or local Ollama when omitted. |
| `--max-turns` | int | `100` | Maximum agentic turns before the session stops. |
| `--max-cost` | float | — | Maximum cost in USD for the session. The session halts when reached. |
| `--max-tokens` | int | — | Maximum response tokens per turn. |
| `--max-thinking-tokens` | int | — | Budget for extended thinking tokens (models that support it). |
| `--temperature` | float | — | Sampling temperature (0.0 -- 1.0). |

### Output & Input

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--output-format` | choice | `text` | Output format. Choices: `text`, `json`, `stream-json`. |
| `--output-style` | choice | `default` | Output verbosity. Choices: `default`, `concise`, `verbose`. See [Output Styles](#7-output-styles). |
| `--input-format` | choice | `text` | Input format. Choices: `text`, `stream-json`. |

### Permissions & Approval

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--approval-mode` | choice | — | Approval tier. Choices: `suggest`, `auto-edit`, `full-auto`. See [Security Settings](#8-security-settings). |
| `--permission-mode` | choice | — | SDK-compatible permission mode. Choices: `default`, `acceptEdits`, `plan`, `bypassPermissions`, `dontAsk`, `auto`. `bypassPermissions` and `dontAsk` auto-approve everything. |
| `--dangerously-skip-permissions` | flag | false | Auto-approve all tool calls. Equivalent to `--approval-mode full-auto`. |
| `--i-understand-the-lethal-trifecta` | flag | false | Acknowledge the exfiltration risk when READ_PRIVATE + READ_UNTRUSTED + NETWORK_EGRESS are all active simultaneously. |

### System Prompt

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--system-prompt` | string | — | Override the entire system prompt with the given text. |
| `--system-prompt-file` | string | — | Load the system prompt from a file path. |

### Tool Control

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--tool-choice` | string | `auto` | Control tool use: `auto` (default), `none` (text only), `any` (force tool), or a specific tool name. |
| `--allowedTools` | string | — | Comma-separated list of allowed tools. Only these tools will be available. |
| `--disallowedTools` | string | — | Comma-separated list of disallowed tools. These tools will be removed from the tool set. |

### Context & MCP

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--add-dir` | string (repeatable) | — | Additional directories to include in context. Can be specified multiple times. |
| `--mcp-config` | string | — | MCP server config as a JSON string or path to a JSON file. See [MCP Configuration](#6-mcp-configuration). |

### Session Management

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-c`, `--continue` | flag | false | Continue the most recent session. |
| `--resume` | string | — | Resume a specific session by its ID. |
| `--session-id` | string | — | Use a specific session ID (instead of auto-generating one). |
| `--fork-session` | flag | false | Fork from the resumed session into a new session (use with `--continue` or `--resume`). |
| `--summarize` | flag | false | Summarize older messages on resume to reduce context size (use with `--continue` or `--resume`). |

### Debug & Logging

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `-d`, `--debug` | flag | false | Enable debug output (full event tracing to stderr). |
| `--verbose` | flag | false | Enable verbose output (used by SDK mode). |
| `--brief` | flag | false | Enable brief mode: shorter, more concise responses. |
| `--log-json` | flag | false | Enable structured JSON logging to `~/.config/duh/logs/duh.jsonl`. |

### Modes

| Flag | Type | Default | Description |
|------|------|---------|-------------|
| `--tui` | flag | false | Launch the full Textual TUI instead of the readline REPL. |
| `--coordinator` | flag | false | Run in coordinator mode -- delegates all tasks to subagents. |

### Subcommands

| Subcommand | Description |
|------------|-------------|
| `doctor` | Run diagnostics and health checks. |
| `constitution` | Print the full system prompt for human review. Accepts `--agent-type` (`general`, `coder`, `researcher`, `planner`, `reviewer`). |
| `security` | Vulnerability monitoring. |
| `audit` | Show recent audit log entries. Accepts `-n`/`--limit` (default 20) and `--json`. |
| `review` | Review a pull request. Requires `--pr <number>`, optional `--repo <owner/repo>`. |
| `bridge start` | Start the remote WebSocket bridge server. Accepts `--host`, `--port` (default 9120), `--token`. |

---

## 2. Environment Variables

Environment variables sit at priority 3, above config files but below CLI flags.

### Core Variables

| Variable | Maps to | Description |
|----------|---------|-------------|
| `DUH_MODEL` | `model` | Model to use. |
| `DUH_PROVIDER` | `provider` | LLM provider (`anthropic`, `litellm`, `ollama`, `openai`). |
| `DUH_MAX_TURNS` | `max_turns` | Maximum agentic turns. |
| `DUH_MAX_COST` | `max_cost` | Maximum session cost in USD. |
| `DUH_SYSTEM_PROMPT` | `system_prompt` | Override the system prompt. |

### API Keys

| Variable | Description |
|----------|-------------|
| `ANTHROPIC_API_KEY` | API key for the Anthropic provider. Required for Anthropic models. Can also be saved via `/connect anthropic`. |
| `OPENAI_API_KEY` | API key for the OpenAI provider. Required for OpenAI models. Can also be saved via `/connect openai`. |

### Logging & Debug

| Variable | Values | Description |
|----------|--------|-------------|
| `DUH_LOG_JSON` | `1` | Enable structured JSON logging (same as `--log-json`). |
| `DUH_TAINT_DEBUG` | `1` | Print every string operation that preserves/merges taint tags (untrusted content tracking). |
| `DUH_TAINT_STRICT` | `1` | Raise `TaintLossError` on any silent taint tag drop (development/testing). |
| `DUH_OPENAI_CHATGPT_DEBUG` | `1` | Enable SSE debug logging for the OpenAI ChatGPT adapter. |

### Testing & Development

| Variable | Values | Description |
|----------|--------|-------------|
| `DUH_STUB_PROVIDER` | `1` | Activate the stub provider for testing (canned responses, no API calls). |
| `DUH_STUB_RESPONSE` | string | Custom response text when the stub provider is active. |
| `DUH_PLUGIN_DIR` | path | Extra directory to scan for plugins. |

### Standard

| Variable | Description |
|----------|-------------|
| `XDG_CONFIG_HOME` | Override the base config directory (default: `~/.config`). When set, user config lives at `$XDG_CONFIG_HOME/duh/` instead of `~/.config/duh/`. |

---

## 3. Settings Files

Settings files are JSON objects merged into the configuration at load time. Two locations are checked.

### User Config

**Path:** `~/.config/duh/settings.json` (or `$XDG_CONFIG_HOME/duh/settings.json`)

Machine-wide defaults. Lowest file-based priority.

### Project Config

**Path:** `.duh/settings.json` (nearest ancestor directory)

Per-repository settings. D.U.H. walks up from the current working directory looking for the nearest `.duh/settings.json`.

### Settings File Format

```json
{
  "model": "claude-sonnet-4-20250514",
  "provider": "anthropic",
  "max_turns": 50,
  "max_cost": 5.00,
  "system_prompt": "",
  "approval_mode": "suggest",
  "permissions": {
    "allow_bash": true,
    "allow_web_fetch": false
  },
  "hooks": {
    "pre_edit": "echo 'editing...'",
    "post_edit": "make lint"
  },
  "mcpServers": {
    "my-server": {
      "command": "npx",
      "args": ["-y", "@my-org/mcp-server"],
      "env": { "TOKEN": "..." }
    }
  }
}
```

### Recognized Keys

| Key | Type | Description |
|-----|------|-------------|
| `model` | string | Default model. |
| `provider` | string | Default provider. |
| `max_turns` | int | Maximum agentic turns. |
| `max_cost` | float | Maximum session cost in USD. |
| `system_prompt` | string | Override system prompt. |
| `approval_mode` | string | Default approval mode (`suggest`, `auto-edit`, `full-auto`). |
| `permissions` | object | Permission overrides (merged across sources). |
| `hooks` | object | Lifecycle hooks (merged across sources). |
| `mcpServers` | object | MCP server definitions. See [MCP Configuration](#6-mcp-configuration). |

---

## 4. Instruction Files

Instruction files are markdown documents injected into the system prompt. They tell the model about project conventions, rules, and constraints. They are **not** merged into the Config object -- they appear as additional system prompt content.

### Load Order (lowest priority first)

Content appearing later in the merged prompt receives more model attention. The load order is:

1. `~/.config/duh/DUH.md` -- user-global instructions
2. Per-directory (from git root down to cwd):
   - `DUH.md` in the directory root
   - `.duh/DUH.md`
   - `CLAUDE.md` (cross-tool compatibility)
3. Per-directory rule files:
   - `.duh/rules/*.md` (sorted alphabetically)
   - `.claude/rules/*.md` (cross-tool compatibility, sorted alphabetically)
4. `AGENTS.md` per directory (open standard for agent delegation)

When a project is inside a git repository, D.U.H. walks from the git root down to the current working directory, loading files at each level. Files in deeper directories have higher effective priority.

### File Locations Summary

| File | Location | Purpose |
|------|----------|---------|
| `DUH.md` | Project root or any directory | Project instructions and conventions. |
| `.duh/DUH.md` | `.duh/` in any directory | Same purpose, nested under `.duh/`. |
| `CLAUDE.md` | Project root or any directory | Cross-tool compatible instructions (works with other AI coding tools too). |
| `.duh/rules/*.md` | `.duh/rules/` in any directory | Modular rule files, loaded alphabetically. |
| `.claude/rules/*.md` | `.claude/rules/` in any directory | Cross-tool compatible rule files. |
| `AGENTS.md` | Project root or any directory | Agent delegation instructions (open standard). |
| `~/.config/duh/DUH.md` | User config directory | User-global instructions applied to all projects. |

### `@include` Syntax

Instruction files support an `@path` directive for including content from other files.

**Syntax:**

```markdown
@./relative/path.md
@~/home-relative/path.md
@/absolute/path.md
@path\ with\ spaces.md
```

**Rules:**

- Included files are inserted **before** the file that references them (depth-first).
- Maximum nesting depth: 5 levels.
- Circular references are detected and skipped.
- Only text file extensions are included: `.md`, `.txt`, `.text`, `.yaml`, `.yml`, `.toml`, `.json`, `.cfg`, `.ini`, `.py`, `.ts`, `.js`, `.sh`.
- `@path` references inside fenced code blocks (`` ``` ``) are ignored.
- Fragment identifiers (`@path#section`) are stripped -- the entire file is included.

**Example:**

```markdown
# Project Rules

@./coding-standards.md
@./security-policy.md

These are our high-level project guidelines.
```

This loads `coding-standards.md` and `security-policy.md` before the file containing the references.

---

## 5. Skills

Skills are markdown files with YAML frontmatter that define reusable prompt templates. They are invoked via `/skill-name` in the REPL or TUI.

### Skill Directories (precedence order, last wins by name)

| Priority | Location | Scope |
|----------|----------|-------|
| 1 (lowest) | `~/.claude/skills/` | User-global (cross-tool compat) |
| 2 | `~/.config/duh/skills/` | User-global |
| 3 | `.claude/skills/` (project) | Project-local (cross-tool compat) |
| 4 (highest) | `.duh/skills/` (project) | Project-local |

When two skills share the same name, the higher-priority location wins.

### Supported Layouts

**Flat file:**
```
.duh/skills/my-skill.md
```

**Directory with SKILL.md:**
```
.duh/skills/my-skill/SKILL.md
```

Subdirectories without a `SKILL.md` at their root are treated as namespace directories and recursed into:
```
.duh/skills/category/my-skill/SKILL.md
```

### SKILL.md Format

```markdown
---
name: deploy-check
description: Verify deployment readiness
when-to-use: Before deploying to production
argument-hint: environment name (e.g., staging, production)
model: claude-sonnet-4-20250514
context: inline
agent: coder
effort: high
user-invocable: true
allowed-tools:
  - Bash
  - Read
  - Grep
paths:
  - "*.dockerfile"
  - "deploy/**"
---

# Deploy Readiness Check

Review the deployment configuration for $ARGUMENTS.

1. Check that all environment variables are set
2. Verify database migrations are up to date
3. Confirm health check endpoints respond
```

### Frontmatter Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `name` | string | filename stem | Unique skill identifier. Used in `/name` invocation. Falls back to parent directory name (for `SKILL.md`) or filename stem. |
| `description` | string | first H1 | Short description for skill discovery. Required (falls back to first `# Heading` in body). |
| `when-to-use` | string | — | Guidance for the model on when to automatically invoke this skill. |
| `argument-hint` | string | — | Hint about what arguments the skill accepts. |
| `model` | string | inherit | Preferred model for this skill. `inherit` or empty means use the parent session model. |
| `context` | string | `inline` | Execution context: `inline` (run in current session) or `fork` (run in a subagent). |
| `agent` | string | — | Agent type when `context` is `fork`. |
| `effort` | string | — | Thinking effort level for the model. |
| `user-invocable` | bool | `true` | Whether users can invoke this skill via `/name`. Set to `false` for model-only skills. |
| `allowed-tools` | list | — | Tools the skill is allowed to use (informational, listed as YAML list items). |
| `paths` | list | — | Glob patterns for file path triggers. The skill may be auto-suggested when matching files are edited. |

### Argument Substitution

The body of a skill is a prompt template. The literal string `$ARGUMENTS` is replaced with whatever the user passes after the skill name:

```
/deploy-check staging
```

This replaces `$ARGUMENTS` with `staging` in the skill's body.

---

## 6. MCP Configuration

MCP (Model Context Protocol) servers extend the tool set with external capabilities. They are configured in the `mcpServers` section of any settings file.

### Configuration Location

MCP servers can be defined in:
- `~/.config/duh/settings.json` (user-global)
- `.duh/settings.json` (project-local)
- `--mcp-config` CLI flag (JSON string or file path)

### Format

```json
{
  "mcpServers": {
    "server-name": {
      "command": "npx",
      "args": ["-y", "@org/mcp-server"],
      "env": { "API_TOKEN": "..." },
      "transport": "stdio",
      "url": "",
      "headers": {}
    }
  }
}
```

### Server Config Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `command` | string | — | Executable to spawn (for `stdio` transport). |
| `args` | list | `[]` | Arguments passed to the command. |
| `env` | object | — | Environment variables set for the server process. |
| `transport` | string | `stdio` | Transport protocol. Choices: `stdio`, `sse`, `http`, `ws`. |
| `url` | string | — | Server URL (required for `sse`, `http`, and `ws` transports). |
| `headers` | object | `{}` | HTTP headers sent with remote transport connections. |

### Transport Types

| Transport | Use Case | Required Fields |
|-----------|----------|-----------------|
| `stdio` | Local process communication (default). The server is spawned as a child process. | `command`, `args` |
| `sse` | Server-Sent Events over HTTP. For remote servers with streaming. | `url` |
| `http` | Plain HTTP JSON-RPC. For remote servers without streaming. | `url` |
| `ws` | WebSocket. For bidirectional remote communication. | `url` |

### Examples

**Local stdio server:**
```json
{
  "mcpServers": {
    "filesystem": {
      "command": "npx",
      "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"]
    }
  }
}
```

**Remote SSE server:**
```json
{
  "mcpServers": {
    "remote-tools": {
      "transport": "sse",
      "url": "https://mcp.example.com/events",
      "headers": { "Authorization": "Bearer sk-..." }
    }
  }
}
```

**WebSocket server:**
```json
{
  "mcpServers": {
    "realtime": {
      "transport": "ws",
      "url": "wss://mcp.example.com/ws",
      "headers": { "Authorization": "Bearer sk-..." }
    }
  }
}
```

---

## 7. Output Styles

Output styles control how much detail is shown during a session. The style can be set at launch or changed mid-session.

### Style Definitions

| Style | Description |
|-------|-------------|
| `default` | Full markdown rendering with tool panels. Standard interactive experience. |
| `concise` | Minimal output. Tool calls show status only (no expanded output). |
| `verbose` | Full tool output is shown. Thinking tokens are visible. Debug-friendly. |

### Setting the Style

**At launch:**
```bash
duh --output-style concise
```

**During a session (REPL/TUI):**
```
/style concise
/style verbose
/style default
/style          # prints the current style
```

### Behavior Differences

| Aspect | default | concise | verbose |
|--------|---------|---------|---------|
| Tool panels | Shown | Hidden (status only) | Shown (expanded) |
| Thinking tokens | Hidden | Hidden | Visible |
| Markdown rendering | Full | Minimal | Full |

---

## 8. Security Settings

Security configuration lives in dedicated files, separate from the general settings.

### Security Config Files

**Precedence (highest first):**

| Priority | Source |
|----------|--------|
| 1 (highest) | CLI flags |
| 2 | `.duh/security.json` (project-local) |
| 3 | `[tool.duh.security]` in `pyproject.toml` |
| 4 (lowest) | `~/.config/duh/security.json` (user defaults) |

### `.duh/security.json` Format

```json
{
  "version": 1,
  "mode": "strict",
  "trifecta_acknowledged": false,
  "block_on_new_only": true,
  "max_db_staleness_days": 7,
  "allow_network": true,
  "exceptions_file": ".duh/security-exceptions.json",
  "cache_file": ".duh/security-cache.json",
  "scanners": {},
  "runtime": {
    "enabled": true,
    "block_pre_tool_use": true,
    "rescan_on_dep_change": true,
    "session_start_audit": true,
    "session_end_summary": true,
    "resolver_timeout_s": 5.0,
    "fail_open_on_timeout": true
  },
  "ci": {
    "generate_github_actions": false,
    "template": "standard"
  }
}
```

### Security Policy Fields

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `version` | int | `1` | Schema version. Must be `1`. |
| `mode` | string | `strict` | Security posture. Choices: `advisory`, `strict`, `paranoid`. |
| `trifecta_acknowledged` | bool | `false` | Acknowledge the lethal trifecta risk (READ_PRIVATE + READ_UNTRUSTED + NETWORK_EGRESS). |
| `block_on_new_only` | bool | `true` | Only block on newly discovered vulnerabilities, not pre-existing ones. |
| `max_db_staleness_days` | int | `7` | Maximum age (in days) for the vulnerability database before requiring a refresh. |
| `allow_network` | bool | `true` | Allow network access for vulnerability database updates. |
| `exceptions_file` | path | `.duh/security-exceptions.json` | Path to the security exceptions file. |
| `cache_file` | path | `.duh/security-cache.json` | Path to the security scan cache. |
| `on_scanner_error` | string | per mode | How to handle scanner errors: `continue`, `warn`, or `fail`. |

### Security Modes

| Mode | Fails on | Reports on | Scanner errors | Blocks pre-tool |
|------|----------|------------|----------------|-----------------|
| `advisory` | nothing | LOW, MEDIUM, HIGH, CRITICAL | warn | no |
| `strict` | CRITICAL, HIGH | MEDIUM, HIGH, CRITICAL | continue | yes |
| `paranoid` | CRITICAL, HIGH, MEDIUM | LOW, MEDIUM, HIGH, CRITICAL | fail | yes |

### Runtime Config

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `runtime.enabled` | bool | `true` | Enable runtime security checks. |
| `runtime.block_pre_tool_use` | bool | `true` | Block tool calls pending security review. |
| `runtime.rescan_on_dep_change` | bool | `true` | Re-scan when dependencies change. |
| `runtime.session_start_audit` | bool | `true` | Run audit on session start. |
| `runtime.session_end_summary` | bool | `true` | Print security summary on session end. |
| `runtime.resolver_timeout_s` | float | `5.0` | Timeout (seconds) for vulnerability resolution (1.0 -- 60.0). |
| `runtime.fail_open_on_timeout` | bool | `true` | Allow operation if resolver times out. |

### pyproject.toml

Security settings can also be placed in `pyproject.toml`:

```toml
[tool.duh.security]
mode = "strict"
trifecta_acknowledged = false
max_db_staleness_days = 7
```

### Approval Modes

The `--approval-mode` flag controls which tool calls require human confirmation.

| Mode | Reads | Writes | Commands |
|------|-------|--------|----------|
| `suggest` | auto-approved | needs approval | needs approval |
| `auto-edit` | auto-approved | auto-approved | needs approval |
| `full-auto` | auto-approved | auto-approved | auto-approved |

**Tool classification:**

| Category | Tools |
|----------|-------|
| Read | Read, Glob, Grep, ToolSearch, WebSearch, MemoryRecall, Skill |
| Write | Write, Edit, MultiEdit, NotebookEdit, worktree tools, MemoryStore |
| Command | Bash, WebFetch, Task, HTTP, Database, Docker, GitHub |

Dangerous git commands (`git push --force`, `git reset --hard`, `git clean -f`, `git branch -D`) are blocked across all approval modes, including `full-auto`.

Using `auto-edit` or `full-auto` outside a git repository triggers a safety warning.

### Lethal Trifecta

The "lethal trifecta" (Simon Willison's exfiltration pattern) is the simultaneous presence of:

1. **READ_PRIVATE** -- reading private/local data (Read, Grep, Database, etc.)
2. **READ_UNTRUSTED** -- ingesting untrusted content (WebFetch, WebSearch, MCP)
3. **NETWORK_EGRESS** -- sending data over the network (WebFetch, Bash, HTTP)

When all three are active, D.U.H. refuses to start unless acknowledged via:
- CLI flag: `--i-understand-the-lethal-trifecta`
- Config: `"trifecta_acknowledged": true` in `.duh/security.json`

---

## 9. Session Configuration

Sessions persist conversation history to disk, enabling continuation across invocations.

### Session Flags

| Flag | Description |
|------|-------------|
| `-c` / `--continue` | Resume the most recent session. |
| `--resume <session-id>` | Resume a specific session by its full or partial ID. |
| `--session-id <id>` | Use a specific session ID instead of auto-generating a UUID. |
| `--fork-session` | Fork the resumed session into a new session (preserves the original). Use with `--continue` or `--resume`. |
| `--summarize` | Summarize older messages on resume to reduce context window usage. Use with `--continue` or `--resume`. |

### Session Storage

Sessions are stored as project-scoped JSON files. Each session is identified by a UUID. The `--continue` flag loads the most recently saved session for the current project directory.

### Typical Workflows

**Continue where you left off:**
```bash
duh --continue
```

**Resume a specific session:**
```bash
duh --resume abc12345
```

**Resume and summarize (reduce context):**
```bash
duh --continue --summarize
```

**Branch from an existing session:**
```bash
duh --continue --fork-session
```

**Start with a fixed session ID (for scripting):**
```bash
duh --session-id my-pipeline-session -p "Run the deploy checks"
```

### Session Info in REPL

Use the `/session` command during an interactive session to view the current session ID and state.

---

## File Tree Summary

```
~/.config/duh/
  settings.json         # User-global settings (priority 1)
  DUH.md                # User-global instructions
  security.json         # User-global security defaults
  skills/               # User-global skills
  logs/
    duh.jsonl            # Structured JSON logs (when enabled)

.duh/                     # Project-local (per repo)
  settings.json           # Project settings (priority 2)
  security.json           # Project security policy
  security-exceptions.json
  security-cache.json
  DUH.md                  # Project instructions
  rules/
    *.md                  # Modular rule files
  skills/                 # Project-local skills
    my-skill.md           # Flat layout
    my-skill/SKILL.md     # Directory layout

DUH.md                    # Project instructions (root-level alternative)
CLAUDE.md                 # Cross-tool compatible instructions
AGENTS.md                 # Agent delegation instructions (open standard)
pyproject.toml            # [tool.duh.security] section

~/.claude/skills/         # Cross-tool compatible user-global skills
.claude/skills/           # Cross-tool compatible project-local skills
.claude/rules/*.md        # Cross-tool compatible rule files
```
