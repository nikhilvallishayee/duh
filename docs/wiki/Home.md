# D.U.H. — Duh is a Universal Harness

**One harness. Any model. Your machine.**

D.U.H. is an open-source, provider-agnostic AI coding agent. It connects any LLM provider to your codebase through a single, clean interface — no vendor lock-in, no 500K-line codebases, no proprietary extensions. It speaks the Claude Agent SDK NDJSON protocol, so it can serve as a drop-in replacement wherever the `claude` binary is expected.

## New: three head-to-head benchmarks vs first-party CLIs

D.U.H. v0.8.0 was benchmarked against Claude Code, Codex CLI, and
Gemini CLI on three tasks of increasing reading-intensity — feature
(B1 /35), rate limiter (B2 /50), docs over D.U.H. source (B3 /45) —
scored by three heterogeneous LLM judges plus automated ground-truth
harnesses. Same-model deltas (D.U.H. − first-party):

| Model      | B1 Δ  | B2 Δ | B3 Δ |
|------------|------:|-----:|-----:|
| Opus 4.7   | −0.3  | −0.3 | +31.4 *|
| GPT-5.4    | −0.6  |  0.0 | **+6.6** |
| Gemini 3.1 | +2.0  |  0.0 | +0.3   |

*claude-code-opus B3 stream-timed-out at 43 min; duh-opus finished in 16.

B1 + B2 → parity at matched models. B3 → D.U.H. wins at GPT-5.4, with
a judge-independent consistency harness confirming (100% vs 52% of
cited symbols resolve against source). Full methodology + raw
artifacts reproducible via `./run_all.sh`. See
**[Benchmarks](Benchmarks)** for scoreboards, rubrics, and caveats.

## What's new in v0.8.0

- **Native Gemini + Groq adapters** — LiteLLM is now an opt-in fallback ([ADR-075](../adrs/ADR-075-drop-litellm-native-adapters.md)). Supply-chain hardened (no LiteLLM in default install path after the March 2026 compromise); native SDKs unlock Anthropic `cache_control`, Gemini `thinking_budget` + explicit caches, and Groq rate-limit headers.
- **Agent tier system** — the `Agent` / `Swarm` tools now take generic `"small"` / `"medium"` / `"large"` / `"inherit"` tiers instead of Anthropic-specific `haiku` / `sonnet` / `opus`. Resolved per-provider at invocation time, so a Gemini-parent never 404s asking for `"haiku"`. See the [Multi-Agent guide](Multi-Agent) for the per-provider resolution table.
- **WebSearch without API keys** — the `WebSearch` tool now falls back to DuckDuckGo (Instant Answer → HTML scrape) when no paid key is set. Priority chain: Serper → Tavily → Brave → DDG IA → DDG HTML. Tune with `DUH_WEBSEARCH_TIMEOUT`.
- **TUI parity sprint** (3 waves, [ADR-073](../adrs/ADR-073-tui-parity-sprint.md)): command palette (`Ctrl+K`), themes (`Ctrl+T`, dark / light / high-contrast), animated spinners, line virtualization, frame-rate cap, and a streaming-visibility fix so in-flight deltas render promptly in slow-terminal setups.
- **Three-tier TUI E2E testing** ([ADR-074](../adrs/ADR-074-tui-e2e-testing.md)): Rich `CaptureConsole` snapshot → PTY + pyte byte-level → tmux full-terminal. CI installs tmux and the pty/pyte deps; `pytest.importorskip` guards keep local runs friction-free.
- **6200+ tests, 100% line coverage** (up from 5665 in v0.7.0). LiteLLM tests auto-skip when the SDK isn't installed.

## Feature Highlights

- **Provider-agnostic (native SDKs)** — Anthropic Claude, OpenAI (API + ChatGPT Codex), Gemini, Groq, Ollama (local), deterministic stub for tests; LiteLLM as opt-in fallback for long-tail providers
- **27 built-in tools** — Read, Write, Edit, MultiEdit, Bash, Glob, Grep, WebFetch, WebSearch, Task, Agent, Swarm, GitHub, Docker, Database, HTTP, LSP, and more
- **MCP support** — stdio, SSE, HTTP, and WebSocket transports for connecting external tool servers
- **Multi-agent** — `Agent` spawns child engines; `Swarm` coordinates parallel work; worktree isolation for safe concurrent edits; generic tier system (small/medium/large/inherit) resolved per-provider
- **4-tier context management** — automatic compaction, smart deduplication, model-summarized compaction, configurable thresholds
- **29 lifecycle hooks** — PreToolUse, PostToolUse, SessionStart, FileChanged, and more, with blocking semantics and input rewriting
- **3-layer security** — vulnerability monitoring (13 scanners), runtime hardening (taint propagation, confirmation tokens), platform sandboxing (macOS Seatbelt, Linux Landlock)
- **Session persistence** — JSONL sessions with `--continue` and `--resume` for picking up where you left off
- **Cost control** — `--max-cost` budget enforcement, `/cost` command, per-provider token pricing, cost-delta warnings on `/model` switch
- **CLAUDE.md / DUH.md / AGENTS.md** — reads all standard instruction file formats
- **Rich TUI** — interactive REPL with command palette (`Ctrl+K`), themes (`Ctrl+T`), 24+ slash commands, tab completion, and streaming output
- **CI-friendly** — `-p "..."` print mode, `--dangerously-skip-permissions`, semantic exit codes, JSON output

## Quick Install

```bash
pip install duh-cli
```

Or with extras:

```bash
pip install 'duh-cli[all]'       # everything: Rich TUI, WebSocket bridge, PDF, security scanners, LiteLLM
pip install 'duh-cli[litellm]'   # + LiteLLM fallback for long-tail providers (ADR-075)
pip install 'duh-cli[security]'  # + vulnerability monitoring tools
```

Core install already ships native adapters for Anthropic, OpenAI, Gemini, Groq, and Ollama. See [Provider Setup](Provider-Setup) for per-provider env vars.

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
