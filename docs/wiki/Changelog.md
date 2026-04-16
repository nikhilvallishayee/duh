# Changelog

## v0.7.0 (2026-04-17) — QE hardening release

Comprehensive response to the external QE swarm audit ([#8](https://github.com/nikhilvallishayee/duh/issues/8)). Six merged PRs closed every finding across all seven reports — code quality, security, performance, quality experience, test suite, SFDIPOT, and the executive summary. No functional regressions; 347 net new tests.

### Security

- **SEC-CRITICAL**: `Read`/`Write`/`Edit` now call `path.resolve()` before boundary checks, preventing symlink-based path traversal (CWE-59, CWE-22)
- **SEC-HIGH**: `WebFetchTool` SSRF protection — rejects private, loopback, link-local, reserved, multicast, and cloud-metadata hostnames; fail-closed on unparseable IPs or DNS failure
- **SEC-HIGH**: `PathPolicy` wired into all file tools (`Read`, `Write`, `Edit`, `MultiEdit`) — `check_permissions()` consults boundary instead of returning `True`
- **SEC-HIGH**: OAuth `wait_state` moved from class-level to factory-returned handler class (concurrent flows no longer share state); JWT structural validation; ephemeral port replaces hardcoded 1455
- **SEC-MEDIUM**: Trust store `chmod 0o600`; entry-point plugin TOFU verification with opt-in trust flag; Seatbelt explicit read allow-list replaces `(allow file-read*)`; `skip_permissions` audit logging; AST parse failure logs `WARNING` instead of silent fallback; dependency version upper bounds
- **SEC-LOW/INFO**: `eval` regex tightened to command position only; `UntrustedStr.__format__()` preserves taint through f-string interpolation; MCP output taint-wrapped in `run()` via `TaintSource.MCP_OUTPUT`

### Performance

- **PERF-CRITICAL**: Engine token estimation replaced with per-message cache + running total — O(N×M) → O(1) amortized, ~100× faster at 200 messages
- **PERF-HIGH**: `subprocess.run` replaced with `asyncio.create_subprocess_exec` across `Write`, `Edit`, `FileTracker`; `diff_summary()` batches all paths into a single `git diff --stat`; `GrepTool` bounded by `max_results=500`, binary-file detection, line-by-line reading
- **PERF-MEDIUM**: `JobQueue` evicts oldest completed jobs (cap 50); `CacheTracker` O(1) running totals, bounded history; `memory_store` append-only fast path for new keys; lazy tool loading via `LazyTool` proxy; parallel read-only tool execution via `asyncio.gather`; security scanners run in parallel (max 4)
- **PERF-LOW**: `recall_facts` uses on-the-fly casefold (no temp string concatenation); redundant message-list copies removed; OpenAI dedup uses `set` (O(1)); MCP disconnect uses per-server tool index; `UndoStack` byte budget (8 MiB cap) with spill-to-disk

### Code quality

- **CQ-1**: `_handle_slash()` decomposed from 580 LOC / CC 52 into `SlashDispatcher` with 27 handlers (each <30 LOC, CC <8), `SlashContext` dataclass replaces 9-parameter cluster
- **CQ-2**: `Engine.run()` decomposed from 393 LOC / CC 35 into 58-line orchestrator + 14 focused helpers (each <80 LOC, CC ≤12); shared `_process_query_events()` eliminates 80% duplication between primary and fallback paths
- **CQ-4**: Extracted `SessionBuilder` from `runner.py` and `repl.py` — 16 phase methods (Rule of 7); 601 lines of duplication removed
- **CQ-26**: Renderers extracted to `repl_renderers.py`; `repl.py` dropped from 1754 → 864 LOC (−51%); `runner.py` 639 → 332 LOC (−48%)
- **CQ-P4**: `OpenAIChatGPTProvider.stream()` decomposed from 230 LOC / CC 22 into `stream` (CC 7) + `_pump_sse_events` (CC 8) + `_dispatch_sse_event` (CC 8) + per-event handlers dispatched via `_EXACT_SSE_HANDLERS` table

### Quality Experience

- **QX**: `duh security scan` default output is now a severity-sorted ANSI table with summary line; `--format sarif` preserved for CI; `--fail-on` valid values documented
- **QX**: Interactive `duh security init` wizard — scanner selection, `--fail-on` severity, allowed paths; writes `.duh/security.json`
- **QX**: No-provider error now suggests `duh doctor` with per-provider env var hints
- **QX**: `/model` switch warns on ≥10× cost delta (e.g. haiku → opus = 60×)
- **QX**: OAuth errors include HTTP status and `duh doctor` remediation hint
- **QX**: OAuth token expiry shown in `/health` ("ChatGPT OAuth: ✓ token valid for 2h 15m")
- **QX**: `duh security scan` shows `Scanning [3/13] bash_ast…` progress on stderr TTY (silent in CI)
- **QX**: New `/errors` slash command — shows last N errors from the current session (100-entry bounded buffer)
- **QX fix**: `/compact` no longer crashes on Python 3.12+ (removed `run_until_complete` inside running event loop; uses sentinel + await pattern)

### Testing

- `pytest-timeout=30` default, `--strict-markers`, CI-portable E2E smoke tests (removed hardcoded `/Users/nomind/...` paths)
- 36 new property-based tests: bash AST tokenizer invariants, redaction regex robustness + idempotence, message serialization round-trip
- 8-test taint-through-full-loop integration test
- 60 coverage-chasing tests with weak assertions removed (after triage: 588 kept, 77 moved to module-named files, 59 deleted)
- New test coverage for: taint serialization through FileStore (with fix to persist taint metadata), disk-full (ENOSPC) scenarios, concurrent job queue access, confirmation token boundary (exactly 300s)

### Stats

- **5665 tests passing** (up from 5318), **0 failing**, **100% line coverage**
- **+347 net new tests**, 60 coverage-chasing tests removed
- **6 PRs merged**, all CI-green before merge
- Hotspot files: `repl.py` −51%, `runner.py` −48%, `_handle_slash()` CC −85%, `Engine.run()` CC −66%, `stream()` CC −68%

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
