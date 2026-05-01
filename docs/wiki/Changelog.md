# Changelog

## v0.9.0 (2026-05-01) — duhwave persistent agentic-swarm extension

Five Accepted ADRs (028–032), all implemented in `duh/duhwave/`, 343 duhwave-specific tests passing on top of the existing v0.8.0 suite. The headline addition is a substrate that lives **above one CLI invocation**: a host daemon, an event-ingress layer, a persistent Task primitive, recursive cross-agent variable handles, and a topology-as-data DSL. The kernel and adapters are unchanged; duhwave imports kernel primitives, never the other way around.

### duhwave (new)

- **RLM context engine** (`duh/duhwave/rlm/`, [ADR-028](../adrs/ADR-028-rlm-context-engine.md)) — bytes by reference, not by summary. Bulk inputs bind to named handles in a sandboxed `python3 -I` REPL subprocess (memory-capped via `RLIMIT_AS`, no network, no shell, curated stdlib only); the agent operates via `Peek` / `Search` / `Slice` / `Recurse` / `Synthesize`. Cites Zhang, Kraska, Khattab — *Recursive Language Models* (arXiv:2512.24601, January 2026).
- **Recursive cross-agent variable handles** (`duh/duhwave/coordinator/{spawn,view,tool_filter}.py`, [ADR-029](../adrs/ADR-029-recursive-cross-agent-links.md)) — coordinator owns the RLMRepl; workers get read-only `RLMHandleView`s scoped by an explicit `expose=[...]` list; worker output binds back as a new handle in the coordinator's namespace (the RecursiveLink mechanism). Cites Yang, Zou, Pan et al. — *Recursive Multi-Agent Systems* (arXiv:2604.25917, April 2026).
- **Persistent Task lifecycle + three execution surfaces** (`duh/duhwave/task/{registry,executors,remote,remote_server}.py`, [ADR-030](../adrs/ADR-030-persistent-task-lifecycle.md)) — Tasks are records on disk with a 5-state forward-only state machine. `InProcessExecutor` (asyncio.Task), `SubprocessExecutor` (`python3 -I` child, survives parent crashes), `RemoteExecutor` (HTTP+bearer to `RemoteTaskServer`) share the same lifecycle and orphan-recovery semantics.
- **Coordinator-as-prompt-role + event ingress** (`duh/duhwave/coordinator/role.py`, `duh/duhwave/ingress/`, `duh/duhwave/cli/dispatcher.py`, [ADR-031](../adrs/ADR-031-coordinator-prompt-role-event-ingress.md)) — Role = system prompt + tool allowlist + spawn_depth; tool-filtering enforces the synthesis-mandate by absence. Five ingress listeners — `WebhookListener` (aiohttp + HMAC verify via `X-Duh-Signature`), `FileWatchListener` (watchfiles), `CronListener` (croniter), `MCPPushListener` (stub pending MCP notification API), `ManualSeam` (Unix socket).
- **Topology DSL + signed bundles + 10-subcommand control plane** (`duh/duhwave/{spec,bundle,cli}/`, [ADR-032](../adrs/ADR-032-swarm-topology-bundles-control-plane.md)) — declare your whole swarm in one TOML file (agents, models, tools, triggers, edges, budget); pack into a deterministic Ed25519-signable `.duhwave` archive (sorted entries, fixed mtime); manage via `duh wave start / stop / ls / inspect / pause / resume / logs / install / uninstall / web` over a Unix-socket RPC to a `HostState` daemon.
- **Real-OpenAI agile-team benchmark** (`benchmarks/duhwave-agile/RESULT.md`, `examples/duhwave/agile_team/`) — 5-stage PM → Architect → Engineer → Tester → Reviewer pipeline. **5/5 stages, 35.5 s wall, $0.0015 per run on gpt-4o-mini** (3,934 prompt + 1,553 completion tokens). gpt-4o lane: 5/5 stages, 29.3 s, $0.0308 — ~20× the cost for ~9% more output. Pytest pass rate on produced code: 3/5 (mini), 5/6 (gpt-4o); both surfaced real cross-agent coordination defects.

### Runnable demos (new)

- `examples/duhwave/01_rlm_demo.py` — RLM substrate single-agent.
- `examples/duhwave/02_swarm_demo.py` — cross-agent handle-passing.
- `examples/duhwave/03_event_driven.py` — webhook → trigger → matcher.
- `examples/duhwave/04_topology_bundle.py` — pack → install → daemon → manual seam.
- `examples/duhwave/repo_triage/main.py` — full multi-agent showpiece (~400 LOC, stub workers).
- `examples/duhwave/parity_hermes/run_all.py` — Hermes feature-parity matrix (5 patterns).
- `examples/duhwave/parity_claw/run_all.py` — always-on multi-channel parity (4 channels).
- `examples/duhwave/agile_team/main.py` — 5-agent agile-team headless run (stub or real OpenAI).
- `examples/duhwave/telegram_assistant/main.py` — mock Telegram bus + real OpenAI (3 flows: inbound webhook, scheduled cron, on-demand manual).
- `examples/duhwave/real_e2e/main.py` — daemon-driven webhook → real OpenAI agent → outbox.

### Cookbook (new)

- `docs/cookbook/build-your-own-swarm.md` — companion to `build-your-own-agent.md`; walks the six duhwave primitives bottom-up, with the `repo_triage/` showpiece as the runnable target.

### Documentation (new)

- New ADR set: ADR-028, ADR-029, ADR-030, ADR-031, ADR-032 — all Accepted (implemented).
- New wiki pages: [Duhwave](Duhwave), [Examples](Examples).
- New wiki sections: `Architecture.md` §5b "duhwave layer (ADRs 028–032)"; `Multi-Agent.md` "duhwave swarms".

### Stats

- **343 duhwave tests passing** (319 unit + 24 integration), 0 failing — on top of the v0.8.0 suite, **6377 tests total** across the project (`pytest tests/unit tests/integration -q`).
- **5 new ADRs**, all Accepted (implemented).
- **10 new runnable examples** under `examples/duhwave/`, plus the existing single-agent cookbook example at `examples/hermes_style/agent.py`.
- **Kernel: zero changes.** Adapters: zero changes. duhwave is purely additive.

---

## v0.8.0 (2026-04-19) — Native providers, tiered agents, TUI parity

Sixteen merged PRs (#33 → #48) across three themes: closing the external QE swarm audit tail (SEC-LOW/INFO and quality-experience follow-ups), a three-wave TUI parity sprint with matching three-tier E2E test infrastructure, and a provider strategy pivot — LiteLLM is demoted from "default" to "opt-in fallback" in favor of native `google-genai` and `groq` SDK adapters.

### Breaking change

- **LiteLLM moved to `[litellm]` extras.** A plain `pip install duh-cli` no longer pulls LiteLLM. To keep the old behavior: `pip install 'duh-cli[litellm]'` (or `[all]`). Motivation: the March 2026 PyPI compromise of `litellm` 1.82.7 / 1.82.8 plus a string of RCE / auth-bypass CVEs. D.U.H. now pins LiteLLM `>=1.83.8` when the extra is installed. See [ADR-075](../adrs/ADR-075-drop-litellm-native-adapters.md).
- **Agent / Swarm `model` field** no longer accepts `haiku` / `sonnet` / `opus` as a first-class enum. Use the generic tiers `"small"` / `"medium"` / `"large"` / `"inherit"` instead. Literal model names (including `claude-haiku-4-5`, `gemini-2.5-flash`, …) are still accepted for backwards compatibility. See the [Multi-Agent guide](Multi-Agent).

### Providers

- **Native Gemini adapter** (`duh/adapters/gemini.py`, PR #45) — uses `google-genai`. Supports `thinking_budget`, explicit cache objects, and the system-instructions-vs-system-role distinction that LiteLLM flattens. Prefix routing: `gemini/<model>` or `gemini-<model>`.
- **Native Groq adapter** (`duh/adapters/groq.py`, PR #45) — uses the `groq` SDK. Preserves `X-RateLimit-Remaining` / reset headers. Models: `llama-3.3-70b-versatile` (default), `openai/gpt-oss-120b`, `llama-3.1-8b-instant`. Prefix routing: `groq/<model>`.
- **LiteLLM demoted to opt-in** ([ADR-075](../adrs/ADR-075-drop-litellm-native-adapters.md), PR #45) — still available via `--provider litellm` after `pip install 'duh-cli[litellm]'`. One-shot stderr deprecation notice per session.
- **Gemini / Groq / Ollama model_caps accuracy** (PR #44) — context-window + output-token limits now reflect live `/v1/models` probes; unified lookup via `ModelAliases.lookup_capabilities()`.
- **Gemini prefix 404 fix + unified token limit lookup + deferred markdown rendering** (PR #45 follow-up).

### Multi-agent

- **Agent tier system** (PR #47) — `Agent` / `Swarm` now take `"small"` / `"medium"` / `"large"` / `"inherit"` resolved per-provider via `PROVIDER_TIER_MODELS` (see `duh/providers/registry.py`). No more Anthropic-specific enum hardcoding; a Gemini-parent with `"small"` resolves to `gemini-2.5-flash`, a Groq-parent to `llama-3.1-8b-instant`, etc. Default is `"inherit"` (keeps the sub-agent on the parent's current model).
- **Swarm tool result preview fix** (PR #48) — per-task summary shown in the live preview; full result auto-expands when the user hovers. Long results are no longer truncated to the first 40 chars in the TUI.
- **Swarm output capture fix** (PR #46) — drift-risk refactor + proper stdout/stderr routing so parallel agent logs don't interleave into a single line.

### Security

- **File-size guard** (PR #44) — `Read` / `Write` reject files over the configured cap (default 50 MB) before streaming bytes into the model; prevents accidental context-window blowouts and token-cost spikes.
- **Drift-risk consolidation refactor** (PR #46) — consolidated the taint-propagation audit hook surface, reducing duplicate `UntrustedStr` wrapping across the engine loop. No behavioral change; simpler invariant to reason about.
- **QE Analysis tail — LOW / INFO findings** (PRs #33 – #39) — completes the v0.7.0 audit (`/Users/nomind/...` → portable paths in remaining fixtures, `AST` logging polish, dependency upper-bound consistency, `skip_permissions` audit log assertions).

### Performance

- **TUI line virtualization + frame-rate cap** (PR #42) — long transcripts no longer re-render every line on every tick; FPS capped so background re-renders don't starve model streaming.
- **Streaming visibility fix** (PR #46) — incremental deltas now render within 16 ms of receipt on slow terminals; previously a buffering edge case could delay partial output until a full line break arrived.

### TUI (three-wave parity sprint, [ADR-073](../adrs/ADR-073-tui-parity-sprint.md))

- **Wave 1 — slash dispatch parity** (PR #40): approval timeout, multi-line input, consistent slash-command handler dispatch across REPL and runner.
- **Wave 2 — rendering quality** (PR #41): syntax-accurate diff rendering, tool-result pretty-print, code-block language detection.
- **Wave 3 — polish** (PR #42): **command palette** (`Ctrl+K`), **themes** (`Ctrl+T`, dark / light / high-contrast), animated spinner, line virtualization, frame-rate cap.

### Testing

- **Three-tier TUI E2E suite** (PR #43, [ADR-074](../adrs/ADR-074-tui-e2e-testing.md)):
  - **Tier 1** — Rich `CaptureConsole` snapshots (fast, deterministic, runs in every test invocation).
  - **Tier 2** — PTY + pyte byte-level assertions (verifies ANSI escape sequences on a virtual terminal).
  - **Tier 3** — tmux full-terminal harness (catches issues that only surface under a real terminal emulator).
- CI installs tmux and pty/pyte deps; `pytest.importorskip` guards keep local runs friction-free when optional deps are missing.
- `test_build_model_backend_litellm` auto-skips when LiteLLM isn't installed (PR #45 follow-up).
- `audit_handler` benchmark tolerance loosened 1000ns → 2000ns for CI variance (PR #46 follow-up).

### Tools

- **`WebSearch` without API keys** (PR #46) — zero-config DuckDuckGo fallback. Priority chain: Serper → Tavily → Brave → DDG Instant Answer → DDG HTML. `DUH_WEBSEARCH_TIMEOUT` (default 5 s) tunes the per-request timeout. Previously the tool returned a hard error when no paid key was configured.

### Stats

- **6200+ tests passing** (up from 5318 in v0.5.0 and 5665 in v0.7.0), **0 failing**, **100% line coverage**.
- **~+900 net new tests** across the three test tiers (snapshot ~+350, PTY ~+280, tmux ~+270) plus unit tests for the Gemini / Groq adapters and tier-resolution helper.
- **16 PRs merged** (#33 → #48), all CI-green before merge.
- **LOC delta**: roughly flat — TUI and adapter additions balanced by LiteLLM demotion and drift-risk consolidation.

---

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
