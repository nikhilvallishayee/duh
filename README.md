# D.U.H. — D.U.H. is a Universal Harness

[![CI](https://github.com/nikhilvallishayee/duh/actions/workflows/ci.yml/badge.svg)](https://github.com/nikhilvallishayee/duh/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests-2309%20passing-brightgreen)]()
[![Coverage](https://img.shields.io/badge/coverage-90%25-green)]()
[![Python](https://img.shields.io/badge/python-3.12%2B-blue)]()
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

> An evolving, open-source alternative to Claude Code, OpenCode, Codex, and Cline.

Provider-agnostic AI coding harness. Use Claude, GPT-4o, or Ollama local models through one interface. Drop-in replacement for Claude Code in any Claude Agent SDK app.

## Quick Start

```bash
pip install duh-cli
export ANTHROPIC_API_KEY=sk-ant-...   # or OPENAI_API_KEY, or just use Ollama
duh -p "fix the bug in auth.py"       # print mode
duh                                    # interactive REPL
```

## Benchmark: D.U.H. vs Claude Code

Same model (Haiku 4.5), same prompt, same API, 3 runs each, isolated directories:

| Metric | Claude Code | D.U.H. |
|---|---|---|
| **Avg time** | 63.2s | **45.7s** (-28%) |
| **Avg tests generated** | 10.5 | **18** (+71%) |
| **Success rate** | 2/3 (67%) | **3/3 (100%)** |
| **Self-correction** | Minimal | **Active** (fixes own test failures) |

Full methodology: [docs/benchmark-results.md](docs/benchmark-results.md)

## Feature Comparison

| Feature | D.U.H. | Claude Code | OpenCode | Codex | Cline |
|---|---|---|---|---|---|
| **Open source** | Apache 2.0 | No | Yes | No | Yes |
| **Providers** | 3 (Claude, GPT, Ollama) | Anthropic only | 75+ | OpenAI only | Multi |
| **SDK drop-in** | Yes | N/A | No | No | No |
| **Skill format parity** | .claude/ + .duh/ | .claude/ | No | No | No |
| **Multi-agent** | 4 types + model selection | 60+ types | No | Yes | No |
| **MCP support** | Adapter (verified) | Full | No | Yes | Extension |
| **Hooks** | 6 events + shell exec | Hooks | No | Yes | No |
| **Safety layers** | 3 (schema + approval + 61 patterns) | 3 | 1 | Sandbox | 1 |
| **Context management** | Auto-compact + dedup | 16 modules | Auto at 95% | Yes | Manual |
| **TUI** | Rich markdown REPL | Ink (React) | Bubble Tea | Bubble Tea | IDE |
| **Session persistence** | Auto-save per turn | Full | SQLite | Yes | IDE |
| **Background jobs** | bg: prefix + /jobs | Full | No | Yes | No |
| **Plan mode** | /plan (design-first) | Yes | No | Yes | No |
| **Notebook editing** | .ipynb cells | Yes | No | No | No |
| **Git worktrees** | EnterWorktree/Exit | Yes | No | No | No |
| **File undo** | /undo stack | Limited | No | No | IDE |
| **Cost control** | --max-cost + budget | Limited | No | No | No |
| **Test impact** | TestImpact tool | No | No | No | No |
| **LSP** | Static analysis | Full LSP | Yes | Yes | IDE |
| **Docker** | DockerTool | No | No | No | No |
| **Database** | SQL query tool | No | No | No | No |
| **GitHub PR** | gh CLI integration | No | No | No | No |
| **HTTP testing** | HTTPTool | No | No | No | No |
| **Tests** | 2309 | Internal | Unknown | Unknown | Unknown |

## What You Can Do

```bash
# Code generation and modification
duh -p "add input validation to the API endpoints" --dangerously-skip-permissions

# Interactive REPL with 17 commands
duh
duh> /help                    # see all commands
duh> /plan add user auth      # design first, then execute
duh> /model claude-opus-4-6   # switch models mid-session
duh> /brief on                # concise mode
duh> /context                 # token usage dashboard
duh> /search "auth"           # search conversation history
duh> /undo                    # revert last file change
duh> /tasks                   # track work items
duh> /git                     # repo status
duh> /pr list                 # GitHub PRs
duh> /health                  # provider connectivity
duh> /cost                    # estimated spend
duh> /jobs                    # background tasks

# Multi-agent with model selection
duh -p "research the API, then implement" --max-turns 20
# Model spawns researcher (haiku) and coder (sonnet) subagents

# SDK compatibility — drop-in for Claude Code
from claude_agent_sdk import ClaudeAgentOptions, query
async for msg in query(
    prompt="fix the bug",
    options=ClaudeAgentOptions(cli_path="/path/to/duh-sdk-shim")
):
    print(msg)

# Background jobs
duh> bg: pytest tests/ -v     # runs tests in background
duh> /jobs                    # check status

# Template-driven prompts
duh> /template code-review    # use a saved prompt pattern

# Docker integration
duh -p "build and test in Docker"

# Database inspection
duh -p "show me the schema and recent users"
```

## Architecture

```
duh/
  kernel/          # Core loop (provider-agnostic, zero external imports)
    loop.py        # prompt → model → tool → result (async generator)
    engine.py      # session wrapper + auto-compaction + budget control
    backoff.py     # exponential retry for transient API errors
    tokens.py      # token estimation + cost calculation
    tasks.py       # task tracking with checkbox display
    plan_mode.py   # design-first two-phase workflows
    undo.py        # file modification rollback
    git_context.py # branch, status, warnings in system prompt
    skill.py       # .claude/ + .duh/ skill loading
    memory.py      # per-project persistent facts

  adapters/        # Provider wrappers (translate to uniform events)
    anthropic.py   # Claude (streaming + backoff)
    openai.py      # GPT-4o, o1 (streaming + backoff)
    ollama.py      # Local models (tool call extraction fallback)
    mcp_executor.py    # MCP server connection + tool execution
    structured_logging.py  # JSONL audit log

  tools/           # 25+ tools
    read, write, edit, multi_edit, bash, glob, grep,
    skill, tool_search, web_fetch, web_search, task,
    notebook_edit, memory (store + recall), test_impact,
    lsp, worktree (enter + exit), github, http, docker,
    database, agent, mcp_tool

  cli/             # CLI interface
    main.py        # Entry point + signal handling
    parser.py      # 20+ flags including --brief, --max-cost, --log-json
    runner.py      # Print mode
    repl.py        # Rich REPL with 17 slash commands
    sdk_runner.py  # Claude Agent SDK NDJSON protocol
    ndjson.py      # Stream-JSON helpers
```

## Providers

| Provider | Status | Models | Auto-detect |
|---|---|---|---|
| **Anthropic** | Working | Sonnet 4.6, Opus 4.6, Haiku 4.5 | ANTHROPIC_API_KEY or --model claude-* |
| **OpenAI** | Working | GPT-4o, o1, any OpenAI-compatible | OPENAI_API_KEY or --model gpt-* |
| **Ollama** | Working | Any local model | Ollama running on localhost |

## Safety

3-layer defense:
1. **Schema validation** — tool inputs checked before execution
2. **Approval gates** — InteractiveApprover prompts for dangerous tools, RuleApprover for policy
3. **Command security** — 61 patterns (26 dangerous + 10 moderate + 18 PS dangerous + 7 PS moderate)

Bash security blocks: `rm -rf /`, fork bombs, `dd`, `mkfs`, pipe-to-shell, `eval`, `sudo`, reverse shells, and more. PowerShell patterns included for Windows.

## Skills

D.U.H. loads skills from Claude Code directories natively:

```
~/.claude/skills/          → Claude Code global skills
~/.config/duh/skills/      → D.U.H. global skills
.claude/skills/            → Claude Code project skills
.duh/skills/               → D.U.H. project skills (highest priority)
```

Both `skill-name/SKILL.md` (directory) and `skill-name.md` (flat) layouts. All Claude Code frontmatter fields supported.

## CLI Reference

```
duh                                          # interactive REPL
duh -p "prompt"                              # print mode
duh -p "prompt" --model gpt-4o              # specific model
duh -p "prompt" --provider openai           # force provider
duh -p "prompt" --brief                     # concise responses
duh -p "prompt" --max-cost 1.00             # budget limit ($1)
duh -p "prompt" --max-turns 20              # iteration limit
duh -p "prompt" --dangerously-skip-permissions
duh -p "prompt" --fallback-model haiku      # auto-switch on overload
duh -p "prompt" --output-format stream-json # NDJSON for SDK
duh -p "prompt" --log-json                  # structured audit log
duh --input-format stream-json              # SDK mode (stdin NDJSON)
duh doctor                                   # diagnostics + connectivity
duh --version
```

## Install from PyPI

```bash
pip install duh-cli                    # core (Anthropic + Ollama)
pip install 'duh-cli[all]'             # includes OpenAI + Rich TUI
pip install 'duh-cli[openai]'          # just OpenAI provider
pip install 'duh-cli[rich]'            # just Rich TUI rendering
```

After install:
```bash
export ANTHROPIC_API_KEY=sk-ant-...    # or OPENAI_API_KEY, or just Ollama
duh -p "hello world"                   # verify it works
duh doctor                             # check everything
```

## For claude-flow / RuFlow Users

D.U.H. works as a drop-in backend for [claude-flow](https://github.com/ruvnet/claude-flow) by @ruvnet.

**Quick setup:**

```bash
# 1. Install D.U.H.
pip install 'duh-cli[all]'

# 2. Create the SDK shim (one-time setup)
mkdir -p ~/.local/bin
cat > ~/.local/bin/duh-sdk-shim << 'SHIM'
#!/bin/bash
exec python3 -m duh "$@"
SHIM
chmod +x ~/.local/bin/duh-sdk-shim

# 3. Configure claude-flow to use D.U.H.
# In your claude-flow.config.json:
```

```json
{
  "swarm": {
    "claudeExecutablePath": "~/.local/bin/duh-sdk-shim"
  }
}
```

```bash
# 4. Alias claude → duh (optional, for full replacement)
alias claude='python3 -m duh'

# 5. Verify
duh doctor
npx @claude-flow/cli agent spawn -t coder --name test
```

**What you get:**
- **Multi-provider**: Switch between Claude, GPT-4o, and local Ollama models without changing claude-flow config
- **Cost control**: `--max-cost 5.00` prevents runaway sessions
- **25+ tools**: Docker, GitHub PRs, HTTP testing, database queries — all available to your agents
- **SDK compatible**: D.U.H. speaks the same NDJSON protocol as Claude Code

**For the Universal Companion API:**
```bash
# Replace Claude Code with D.U.H. in UC API
export DUH_CLI_PATH=~/.local/bin/duh-sdk-shim
export CLAUDE_AGENT_SDK_SKIP_VERSION_CHECK=1
# Start your UC API server — it now uses D.U.H. as the backend
```

## Development

```bash
git clone https://github.com/nikhilvallishayee/duh
cd duh
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q --tb=short                   # 2346 tests, ~23s
pytest --cov=duh --cov-report=term     # 90% coverage
```

## License

Apache 2.0
