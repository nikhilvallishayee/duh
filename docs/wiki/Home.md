# D.U.H. — Duh is a Universal Harness

**One harness. Any model. Your machine.**

D.U.H. is an open-source, provider-agnostic AI coding agent. It connects any LLM provider to your codebase through a single, clean interface — no vendor lock-in, no 500K-line codebases, no proprietary extensions. It speaks the Claude Agent SDK NDJSON protocol, so it can serve as a drop-in replacement wherever the `claude` binary is expected.

## Feature Highlights

- **Provider-agnostic** — Anthropic Claude, OpenAI (API + ChatGPT Codex), Ollama (local), LiteLLM (any provider), deterministic stub for tests
- **25+ built-in tools** — Read, Write, Edit, MultiEdit, Bash, Glob, Grep, WebFetch, WebSearch, Task, Agent, GitHub, Docker, Database, HTTP, LSP, and more
- **MCP support** — stdio, SSE, HTTP, and WebSocket transports for connecting external tool servers
- **Multi-agent** — AgentTool spawns child engines; SwarmTool coordinates parallel work; worktree isolation for safe concurrent edits
- **4-tier context management** — automatic compaction, smart deduplication, model-summarized compaction, configurable thresholds
- **29 lifecycle hooks** — PreToolUse, PostToolUse, SessionStart, FileChanged, and more, with blocking semantics and input rewriting
- **3-layer security** — vulnerability monitoring (13 scanners), runtime hardening (taint propagation, confirmation tokens), platform sandboxing (macOS Seatbelt, Linux Landlock)
- **Session persistence** — JSONL sessions with `--continue` and `--resume` for picking up where you left off
- **Cost control** — `--max-cost` budget enforcement, `/cost` command, per-provider token pricing
- **CLAUDE.md / DUH.md / AGENTS.md** — reads all standard instruction file formats
- **Rich TUI** — interactive REPL with 20+ slash commands, tab completion, and streaming output
- **CI-friendly** — `-p "..."` print mode, `--dangerously-skip-permissions`, semantic exit codes, JSON output

## Quick Install

```bash
pip install duh-cli
```

Or with extras:

```bash
pip install 'duh-cli[all]'       # everything: OpenAI, Rich TUI, WebSocket bridge, PDF, security scanners
pip install 'duh-cli[openai]'    # + OpenAI provider
pip install 'duh-cli[security]'  # + vulnerability monitoring tools
```

## Quick Usage

```bash
# One-shot prompt (print mode)
duh -p "fix the bug in auth.py"

# Interactive REPL
duh

# Resume last session
duh --continue

# Force a specific provider and model
duh --provider anthropic --model claude-sonnet-4-6 -p "hello"

# Diagnostics
duh doctor

# Vulnerability scan
duh security scan
```

## Wiki Contents

- **[Home](Home)** — You are here
- **[Getting Started](Getting-Started)** — Installation, first run, configuration basics
- **[Architecture](Architecture)** — Kernel, ports & adapters, source layout
- **[Configuration](Configuration)** — Settings precedence, DUH.md, environment variables
- **[Tools Reference](Tools)** — All 25+ built-in tools
- **[Multi-Agent Guide](Multi-Agent)** — AgentTool, SwarmTool, worktree isolation
- **[Context Management](Context-Management)** — Compaction, token estimation, auto-compact
- **[Security](Security)** — Vulnerability monitoring, runtime hardening, sandboxing
- **[Provider Setup](Provider-Setup)** — Anthropic, OpenAI, Ollama, LiteLLM configuration
- **[FAQ](FAQ)** — Frequently asked questions
- **[Changelog](Changelog)** — Release notes

## Links

- **Repository**: [github.com/nikhilvallishayee/duh](https://github.com/nikhilvallishayee/duh)
- **PyPI**: [pypi.org/project/duh-cli](https://pypi.org/project/duh-cli/)
- **License**: Apache 2.0
