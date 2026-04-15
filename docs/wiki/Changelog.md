# Changelog

## v0.5.0 (2026-04-14)

The first public alpha release of D.U.H., delivering a complete provider-agnostic AI coding agent with production-grade security, multi-agent support, and full tool parity.

### Core

- **Agentic kernel** -- clean <5K LOC kernel with ports-and-adapters architecture; zero external dependencies in the core loop
- **5 provider adapters** -- Anthropic Claude, OpenAI (API key + ChatGPT Codex OAuth), Ollama (local), LiteLLM (100+ providers), deterministic stub for tests
- **Claude Agent SDK compatibility** -- full NDJSON protocol support; D.U.H. can serve as a drop-in replacement for the `claude` binary
- **Session persistence** -- JSONL sessions with atomic writes, `--continue` / `--resume`, auto-save every turn

### Tools (25+)

- **File tools**: Read, Write, Edit, MultiEdit, Glob, Grep, NotebookEdit
- **Execution**: Bash (with AST-based security analysis, heredoc support), Docker, Database (read-only SQL), HTTP
- **Search**: WebFetch, WebSearch, ToolSearch
- **Agent tools**: Task (subagent spawning), Agent (typed child engines), EnterWorktree / ExitWorktree (git worktree isolation), SwarmTool (parallel coordination)
- **Development**: GitHub (PR workflows), LSP (go-to-def, find-refs), TestImpact (coverage-aware test selection), Skill
- **Memory**: MemoryStore, MemoryRecall (cross-session persistent memory)
- **User interaction**: TodoWrite, AskUserQuestion

### Interactive REPL

- 20+ slash commands: `/help`, `/model`, `/connect`, `/models`, `/cost`, `/status`, `/context`, `/changes`, `/git`, `/tasks`, `/brief`, `/search`, `/template`, `/plan`, `/pr`, `/undo`, `/jobs`, `/health`, `/clear`, `/compact`, `/snapshot`, `/exit`
- Tab completion for commands and file paths
- Rich TUI with streaming output
- `/plan` mode for design-first two-phase execution
- `/snapshot` for ghost filesystem snapshots (apply or discard)
- `/pr` integration with `gh` CLI (list, view, diff, checks)

### Context Management

- 4-tier compaction: token estimation, auto-compact at 80% context window, smart deduplication of redundant file reads, model-summarized compaction with fallback to tail-window truncation
- `/context` command for context window health dashboard
- `/compact` for manual compaction

### Multi-Agent

- AgentTool spawns child engines with typed system prompts (general, coder, researcher, planner)
- Worktree isolation for safe parallel file edits
- Background job queue with `/jobs` command
- SwarmTool for coordinated multi-agent workflows

### Security (3 layers)

- **Layer 1 -- Vulnerability monitoring**: 13 scanners across 3 tiers (Minimal, Extended, Paranoid); SARIF output; delta mode with baselines; exception management; `duh security scan`, `duh security init`, `duh security doctor`; 5 D.U.H.-specific scanners for project-file RCE, MCP tool poisoning, sandbox bypass, command injection, and OAuth hardening
- **Layer 2 -- Runtime hardening**: UntrustedStr taint propagation across 6 origins; HMAC-bound confirmation tokens; lethal trifecta detection (read-private + read-untrusted + network-egress); MCP Unicode normalization (GlassWorm defense); per-hook filesystem namespacing; PEP 578 audit hook bridge; signed plugin manifests with TOFU trust store
- **Layer 3 -- Platform sandboxing**: macOS Seatbelt profiles, Linux Landlock syscall filtering, network policy layer; `--approval-mode suggest|auto-edit|full-auto`

### Hooks

- 29 lifecycle events: PreToolUse, PostToolUse, PostToolUseFailure, SessionStart, SessionEnd, UserPromptSubmit, PermissionRequest, PreCompact, PostCompact, FileChanged, SubagentStart, Elicitation, and more
- Glob matchers for filtering by tool name
- Blocking semantics: hooks can refuse tool calls or rewrite inputs
- Shell-command and Python-callable handlers

### Configuration

- 4-layer precedence: user settings, project settings, environment variables, CLI flags
- DUH.md / CLAUDE.md / AGENTS.md instruction file loading
- `.duh/rules/*.md` rule directories
- MCP server configuration with 4 transports (stdio, SSE, HTTP, WebSocket)

### CLI

- Print mode: `duh -p "..."` with streaming to stdout
- JSON output: `--output-format json`
- Provider auto-detection from environment
- Doctor subcommand: `duh doctor` for diagnostics and health checks
- Cost control: `--max-cost`, `--max-turns`, `/cost` command
- Debug mode: `--debug` for full event tracing
- CI-friendly: `--dangerously-skip-permissions`, semantic exit codes

### Testing

- 4000+ tests, 100% line coverage
- 330+ security-specific tests including CVE replay fixtures
- Property-based tests via Hypothesis (provider differential fuzzer)
- Performance regression gates
- Full test suite runs in ~28 seconds with `DUH_STUB_PROVIDER=1`

### Documentation

- 54 Architecture Decision Records (ADRs)
- Website with getting started guide, comparisons, and security documentation
- This wiki

### License

Apache 2.0
