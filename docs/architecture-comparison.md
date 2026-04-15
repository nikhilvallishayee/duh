# D.U.H. Architecture Comparison — Honest Assessment

## Project Stats

| | D.U.H. | open_tengu | Claude Code (Claude Code) | Codex (OpenAI) | OpenCode |
|---|---|---|---|---|---|
| Language | Python | Python | TypeScript | Rust | Go |
| Source LOC | 24,327 | 418,026 | ~512,000 | ~603,000 | ~42,000 |
| Test LOC | 55,438 | ~12,000 | Internal | Unknown | Unknown |
| Files | ~200 | 2,135 | ~3,000+ | ~1,401 | 140 |
| Tests | 4,160 passing | 407 passing (3,003 collected) | Internal | Unknown | Unknown |
| ADRs | 54 | ~25 | Proprietary | Proprietary | 0 |
| Status | **Production alpha** | Alpha (60% ported) | Production | Production | Archived |

---

## Feature Matrix — What's Really There

### Legend
- **Y** = Fully implemented and tested
- **P** = Partial (works but incomplete or lightly tested)
- **S** = Scaffolded (code exists, not functional)
- **N** = Not implemented

| Feature | D.U.H. | open_tengu | Claude Code | Codex | OpenCode |
|---|---|---|---|---|---|
| **Core Loop** | | | | | |
| Multi-turn agentic loop | **Y** | **Y** | **Y** | **Y** | **Y** |
| Streaming text output | **Y** | **Y** | **Y** | **Y** | **Y** |
| Thinking/reasoning blocks | **Y** | **Y** | **Y** | N | N |
| Max turns enforcement | **Y** | **Y** | **Y** | **Y** | **Y** |
| Error recovery in loop | **Y** | P | **Y** | **Y** | P |
| | | | | | |
| **Tools** | | | | | |
| Read | **Y** | **Y** | **Y** | **Y** | **Y** |
| Write | **Y** | **Y** | **Y** | **Y** | **Y** |
| Edit (exact string match) | **Y** | **Y** | **Y** | **Y** | **Y** |
| MultiEdit | **Y** | **Y** | **Y** | N | N |
| Bash (subprocess) | **Y** | **Y** | **Y** | **Y** | **Y** |
| Glob (file search) | **Y** | **Y** | **Y** | **Y** | **Y** |
| Grep (content search) | **Y** | **Y** | **Y** | **Y** | **Y** |
| WebSearch | **Y** (Serper + Tavily) | P | **Y** | **Y** | N |
| WebFetch | **Y** (httpx, taint-tagged) | P | **Y** | N | N |
| Agent (subagent spawn) | **Y** (4 types + model select) | P | **Y** | **Y** | N |
| Skill (invoke skills) | **Y** | P | **Y** | **Y** | N |
| ToolSearch (deferred tools) | **Y** | P | **Y** | N | N |
| NotebookEdit | **Y** | P | **Y** | **Y** | N |
| LSP integration | **Y** | N | **Y** | **Y** | **Y** |
| Docker | **Y** | N | N | **Y** | N |
| Database | **Y** | N | N | N | N |
| HTTP | **Y** | N | N | N | N |
| GitHub (PR/issues) | **Y** | N | **Y** | **Y** | N |
| TestImpact | **Y** | N | N | N | N |
| TodoWrite | **Y** | P | **Y** | N | N |
| AskUserQuestion | **Y** | P | **Y** | **Y** | N |
| EnterWorktree/ExitWorktree | **Y** | N | **Y** | N | N |
| MemoryStore/MemoryRecall | **Y** | N | **Y** | N | N |
| **Tool count** | **26** | ~15 | 25+ | ~15 | ~10 |
| | | | | | |
| **Providers** | | | | | |
| Anthropic (Claude) | **Y** | **Y** | **Y** | N | **Y** |
| OpenAI (API key) | **Y** | N | N | **Y** | **Y** |
| OpenAI ChatGPT/Codex (OAuth) | **Y** (PKCE, ADR-051/052) | N | N | **Y** | N |
| Ollama (local models) | **Y** | N | N | **Y** | N |
| Stub (deterministic testing) | **Y** | N | N | N | N |
| Google Gemini | N | N | N | N | **Y** |
| AWS Bedrock | N | N | **Y** | N | **Y** |
| Azure OpenAI | N | N | N | N | **Y** |
| Provider auto-detection | **Y** | N | N | N | **Y** |
| **Provider count** | **5** (Claude, OpenAI API, ChatGPT/Codex, Ollama, Stub) | 1 | 1 | 2 | 5 |
| | | | | | |
| **MCP (Model Context Protocol)** | | | | | |
| MCP client (stdio/SSE/HTTP/WS) | **Y** (4 transports, ADR-040) | P | **Y** | **Y** | N |
| MCP tool discovery | **Y** | P | **Y** | **Y** | N |
| MCP tool execution | **Y** | P | **Y** | **Y** | N |
| MCP Unicode normalization | **Y** (GlassWorm defense) | N | N | N | N |
| MCP subprocess sandboxing | **Y** (Seatbelt/Landlock) | N | N | N | N |
| MCP hash-pinning (MCPoison) | **Y** (duh-mcp-pin scanner) | N | N | N | N |
| | | | | | |
| **Permissions & Safety** | | | | | |
| Auto-approve mode | **Y** | **Y** | **Y** | **Y** | **Y** |
| Interactive approval | **Y** | **Y** | **Y** | **Y** | **Y** |
| Rule-based approval | **Y** | **Y** | **Y** | **Y** | P |
| Bash command filtering | **Y** (AST-based, 61+ patterns) | **Y** (368 patterns) | **Y** | **Y** | P |
| File path validation | **Y** | **Y** | **Y** | **Y** | P |
| Sandbox execution | **Y** (Seatbelt + Landlock) | N | P | **Y** (landlock) | P |
| Taint propagation (UntrustedStr) | **Y** (ADR-054) | N | N | N | N |
| Confirmation token gating | **Y** (HMAC-bound, ADR-054) | N | N | N | N |
| Lethal trifecta check | **Y** (ADR-054) | N | N | N | N |
| | | | | | |
| **Security Scanning (ADR-053)** | | | | | |
| Vulnerability scanner framework | **Y** (13 scanners, 3 tiers) | N | N | N | N |
| Python SAST (ruff S-rules) | **Y** | N | N | N | N |
| Dependency audit (pip-audit) | **Y** | N | N | N | N |
| Secret scanning | **Y** (detect-secrets) | N | N | N | N |
| SBOM generation (CycloneDX) | **Y** | N | N | N | N |
| CVE replay fixtures | **Y** (4 CVEs) | N | N | N | N |
| SARIF output | **Y** | N | N | N | N |
| Runtime policy resolver | **Y** (tool-call gating) | N | N | N | N |
| Exception management | **Y** (alias, scope, expiry) | N | N | N | N |
| CI template generation | **Y** (3 tiers) | N | N | N | N |
| | | | | | |
| **Hook/Event System** | | | | | |
| PreToolUse / PostToolUse | **Y** | **Y** | **Y** | **Y** | N |
| SessionStart / SessionEnd | **Y** | **Y** | **Y** | **Y** | N |
| Shell command hooks | **Y** | **Y** | **Y** | **Y** | N |
| Function hooks | **Y** | **Y** | **Y** | **Y** | N |
| Hook blocking (input rewrite) | **Y** (ADR-045) | **Y** | **Y** | P | N |
| Per-hook FS namespacing | **Y** (ADR-054) | N | N | N | N |
| PEP 578 audit hook bridge | **Y** (ADR-054) | N | N | N | N |
| Total event types | 29 | 29 | 29+ | Unknown | 0 |
| | | | | | |
| **Session & Context** | | | | | |
| Session persistence (disk) | **Y** (FileStore + auth store) | P | **Y** | **Y** | **Y** (SQLite) |
| Session resume (--continue) | **Y** | P | **Y** | **Y** | **Y** |
| Context compaction (truncation) | **Y** (SimpleCompactor) | P | **Y** | **Y** | **Y** |
| Context compaction (LLM summary) | **Y** (ModelCompactor) | N | **Y** (16 modules) | **Y** | N |
| Token estimation | **P** (chars/4 heuristic) | P | **Y** | **Y** | **Y** |
| Conversation history | **Y** | P | **Y** | **Y** | **Y** |
| | | | | | |
| **CLI** | | | | | |
| Print mode (-p) | **Y** | **Y** | **Y** | **Y** | N (REPL only) |
| Interactive REPL | **Y** (Rich, 21 slash commands) | P | **Y** (Ink TUI) | **Y** (Bubble Tea) | **Y** (Bubble Tea) |
| /help, /model, /status, /cost | **Y** | P | **Y** (114 commands) | **Y** | P |
| /plan, /undo, /snapshot | **Y** | N | **Y** | N | N |
| /connect (OAuth flow) | **Y** | N | N | N | N |
| Doctor diagnostics | **Y** (duh doctor + duh security doctor) | P | N | N | N |
| Security CLI (scan/init/exception) | **Y** | N | N | N | N |
| JSON output | **Y** | P | **Y** | **Y** | N |
| Debug/verbose mode | **Y** | P | **Y** | **Y** | P |
| Graceful shutdown (SIGTERM) | **Y** | N | **Y** | **Y** | P |
| | | | | | |
| **SDK Compatibility** | | | | | |
| stream-json NDJSON protocol | **Y** | **Y** (62 tests) | **Y** (native) | N | N |
| Control request/response | **Y** (initialize) | **Y** | **Y** (full protocol) | N | N |
| Claude Agent SDK drop-in | **Y** (verified e2e) | P | **Y** (IS the SDK target) | N | N |
| | | | | | |
| **Skills** | | | | | |
| .claude/skills/ loading | **Y** | P | **Y** | **Y** | N |
| .duh/skills/ loading | **Y** | N | N | N | N |
| Directory layout (SKILL.md) | **Y** | N | **Y** | **Y** | N |
| Frontmatter parsing | **Y** (all fields) | P | **Y** | **Y** | N |
| Skill invocation via tool | **Y** | P | **Y** | **Y** | N |
| | | | | | |
| **Plugins** | | | | | |
| Plugin discovery (entry_points) | **Y** | P | **Y** | **Y** | N |
| Plugin tool loading | **Y** | S | **Y** | **Y** | N |
| Plugin signed manifests | **Y** (TOFU + sigstore-ready) | N | **Y** | P | N |
| Plugin trust store + revocation | **Y** | N | N | N | N |
| | | | | | |
| **Multi-Agent** | | | | | |
| Agent types (coder, researcher, etc.) | **Y** (4 types + model select) | P | **Y** (60+ types) | **Y** | N |
| Subagent spawning | **Y** (AgentTool, working) | P | **Y** | **Y** | N |
| Parallel agent execution | P | N | **Y** | **Y** | N |
| Agent coordination | P | N | **Y** (coordinator) | **Y** | N |
| | | | | | |
| **Config** | | | | | |
| User config (~/.config/) | **Y** | P | **Y** | **Y** | **Y** |
| Project config (.duh/) | **Y** | P | **Y** (.claude/) | **Y** | **Y** |
| Environment variables | **Y** | P | **Y** | **Y** | **Y** |
| CLI flag override | **Y** | **Y** | **Y** | **Y** | **Y** |
| Instruction files (DUH.md) | **Y** | P | **Y** (CLAUDE.md) | **Y** (AGENTS.md) | **Y** (.opencode.json) |
| | | | | | |
| **TUI / Rendering** | | | | | |
| Rich terminal rendering | **Y** (Rich-based) | P | **Y** (Ink/React) | **Y** (Bubble Tea) | **Y** (Bubble Tea) |
| Syntax highlighting | P | P | **Y** | **Y** | **Y** |
| Progress indicators | **Y** | P | **Y** | **Y** | **Y** |
| Markdown rendering | P | P | **Y** | **Y** | **Y** |
| Vim keybindings | N | N | **Y** | P | P |
| | | | | | |
| **Other** | | | | | |
| Voice input/output | N | N | **Y** | N | N |
| Cost tracking | P (--max-cost, budget controls) | N | **Y** | **Y** | N |
| Telemetry (opt-in) | N | P | **Y** | **Y** | N |
| Auto-update | N | N | **Y** | **Y** | N |
| File change tracking | P (git_context, snapshot) | N | **Y** | **Y** | **Y** |
| Property-based testing (hypothesis) | **Y** (taint + provider fuzzer) | N | N | N | N |
| Provider differential fuzzer | **Y** | N | N | N | N |

---

## Architectural Comparison

| Dimension | D.U.H. | open_tengu | Claude Code | Codex | OpenCode |
|---|---|---|---|---|---|
| **Architecture** | Hexagonal (ports & adapters) | Direct port of TS | Monolith + React hooks | Workspace (78 Rust crates) | Go packages |
| **Kernel isolation** | Strict (kernel never imports providers) | Partial | Entangled with React | Strict (trait boundaries) | Partial |
| **Dependency injection** | Explicit (Deps dataclass) | Implicit | React context/hooks | Rust traits | Go interfaces |
| **Test strategy** | 4,160 unit + integration + property + benchmark | 407 passing (mixed) | Internal | Unknown | Unknown |
| **Test:code ratio** | 2.3:1 (55K test / 24K source) | ~0.03:1 | Unknown | Unknown | Unknown |
| **Error handling** | Events + metadata flags | Exceptions | Events + React error boundaries | Rust Result types | Go errors |
| **Security depth** | 3-layer (scanning + taint + sandbox) | Basic (patterns only) | Pattern-based | Sandbox-focused | Minimal |

---

## What D.U.H. Does Well (Honest Strengths)

1. **Clean kernel** — zero provider imports. The agentic loop is genuinely provider-agnostic.
2. **5 working providers** — Anthropic, OpenAI API, ChatGPT/Codex (OAuth), Ollama, Stub. No other Python tool has all 5.
3. **SDK drop-in** — Verified end-to-end with Claude Agent SDK. No other open tool does this.
4. **Deepest security story in OSS** — 3-layer pluggable security: 13 vulnerability scanners, taint-propagating UntrustedStr, confirmation tokens, lethal trifecta check, per-hook FS namespacing, PEP 578 audit hooks, signed plugin manifests, MCP Unicode normalization, provider differential fuzzer. Covers every published 2024-2026 agent CVE.
5. **Skill format parity** — Loads .claude/skills/ natively. Skills built for Claude Code work as-is.
6. **Test discipline** — 4,160 tests in 24K LOC. 2.3:1 test:code ratio. Property-based + benchmark tests.
7. **54 ADRs** — Every design choice is documented.
8. **26 built-in tools** — More than any competing OSS agent.
9. **Real web tools** — WebSearch (Serper + Tavily) and WebFetch with taint tagging.
10. **Real compaction** — Both truncation (SimpleCompactor) and LLM summarization (ModelCompactor).

## Remaining Gaps (Honest)

### Would improve production readiness
1. **Precise token counting** — Currently chars/4 heuristic. Model-specific tokenizers would improve context management.
2. **Full TUI polish** — Rich-based REPL works but no vim keybindings, limited markdown rendering, no Ink-level reactivity.
3. **Parallel multi-agent execution** — Subagent spawning works; true parallel coordination with shared state is partial.
4. **Cost tracking** — Budget controls exist (--max-cost) but per-turn cost display and /cost reporting are basic.

### Nice to have
5. **Voice input/output** — Not planned currently.
6. **Telemetry (opt-in)** — No analytics; relies on user feedback.
7. **Auto-update** — Manual pip upgrade required.
8. **Additional providers** — Gemini, Bedrock, Azure not yet integrated.

---

## What Would It Take to Reach Parity?

### To match OpenCode (archived, simplest target)
- [x] Multi-provider support (5 providers)
- [x] Agentic loop + streaming
- [x] Core tools (26 tools)
- [x] Session persistence
- [x] Context compaction (truncation + LLM)
- [ ] Bubble Tea / Rich TUI polish (1-2 weeks)

### To match Codex (medium target)
- Everything above, plus:
- [x] ChatGPT/Codex OAuth support (ADR-051/052)
- [x] Execution sandboxing (Seatbelt + Landlock)
- [x] Real MCP integration (4 transports + Unicode + sandbox)
- [x] Plugin system with signing
- [x] WebSearch tool
- [ ] Precise token counting (2-3 days)
- [ ] File change tracking (partial — git_context + snapshot exist)

### To match Claude Code (hardest target)
- Everything above, plus:
- [ ] Full TUI with markdown rendering (2-3 weeks)
- [ ] 60+ agent types with coordination (2-3 weeks — currently 4 types)
- [ ] 114 slash commands (2-3 weeks — currently 21)
- [ ] Voice input/output (1 week)
- [ ] Precise cost tracking and analytics (3-5 days)

### Areas where D.U.H. is AHEAD of Claude Code
- [x] Provider-agnostic (5 providers vs. Anthropic-only)
- [x] Pluggable security scanning (13 scanners, 3 tiers)
- [x] Taint propagation (UntrustedStr — no other agent has this)
- [x] Confirmation token gating on dangerous tools
- [x] Lethal trifecta capability matrix
- [x] MCP hash-pinning + Unicode normalization
- [x] PEP 578 audit hook bridge
- [x] Signed plugin manifests with TOFU trust
- [x] Provider differential fuzzer
- [x] Open-source with 54 documented ADRs

---

*This document is an honest snapshot as of commit d2e1956 (April 15, 2026). Updated as features land.*
