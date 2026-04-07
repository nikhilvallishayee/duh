# D.U.H. Architecture Comparison — Honest Assessment

## Project Stats

| | D.U.H. | open_tengu | tengu-legacy (Claude Code) | Codex (OpenAI) | OpenCode |
|---|---|---|---|---|---|
| Language | Python | Python | TypeScript | Rust | Go |
| Source LOC | 6,615 | 418,026 | ~512,000 | ~603,000 | ~42,000 |
| Files | 51 | 2,135 | ~3,000+ | ~1,401 | 140 |
| Tests | 954 passing | 407 passing (3,003 collected) | Internal | Unknown | Unknown |
| Commits | 41 | ~25 | Proprietary | Proprietary | Archived |
| Status | **Evolving** | Alpha (60% ported) | Production | Production | Archived |

---

## Feature Matrix — What's Really There

### Legend
- **Y** = Fully implemented and tested
- **P** = Partial (works but incomplete or lightly tested)
- **S** = Scaffolded (code exists, not functional)
- **N** = Not implemented

| Feature | D.U.H. | open_tengu | tengu-legacy | Codex | OpenCode |
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
| Bash (subprocess) | **Y** | **Y** | **Y** | **Y** | **Y** |
| Glob (file search) | **Y** | **Y** | **Y** | **Y** | **Y** |
| Grep (content search) | **Y** | **Y** | **Y** | **Y** | **Y** |
| WebSearch | N | P | **Y** | **Y** | N |
| WebFetch | N | P | **Y** | N | N |
| Agent (subagent spawn) | **S** (broken) | P | **Y** | **Y** | N |
| Skill (invoke skills) | **P** | P | **Y** | **Y** | N |
| ToolSearch (deferred tools) | **P** | P | **Y** | N | N |
| Notebook/REPL tools | N | P | **Y** | **Y** | N |
| LSP integration | N | N | **Y** | **Y** | **Y** |
| | | | | | |
| **Providers** | | | | | |
| Anthropic (Claude) | **Y** | **Y** | **Y** | N | **Y** |
| OpenAI (GPT-4o, o1) | **Y** | N | N | **Y** | **Y** |
| Ollama (local models) | **Y** | N | N | **Y** | N |
| Google Gemini | N | N | N | N | **Y** |
| AWS Bedrock | N | N | **Y** | N | **Y** |
| Azure OpenAI | N | N | N | N | **Y** |
| litellm (100+ models) | N | N | N | N | N |
| Provider auto-detection | **Y** | N | N | N | **Y** |
| | | | | | |
| **MCP (Model Context Protocol)** | | | | | |
| MCP client | **S** (config parses, connect scaffolded) | P | **Y** | **Y** | N |
| MCP tool discovery | **S** | P | **Y** | **Y** | N |
| MCP tool execution | **S** | P | **Y** | **Y** | N |
| MCP server management | N | P | **Y** | **Y** | N |
| SDK MCP servers | N | N | **Y** | N | N |
| | | | | | |
| **Permissions & Safety** | | | | | |
| Auto-approve mode | **Y** | **Y** | **Y** | **Y** | **Y** |
| Interactive approval | **Y** | **Y** | **Y** | **Y** | **Y** |
| Rule-based approval | **Y** | **Y** | **Y** | **Y** | P |
| Bash command filtering | P (basic) | **Y** (368 patterns) | **Y** | **Y** | P |
| File path validation | N | **Y** | **Y** | **Y** | P |
| Sandbox execution | N | N | P | **Y** (landlock) | P |
| | | | | | |
| **Hook/Event System** | | | | | |
| PreToolUse / PostToolUse | **Y** | **Y** | **Y** | **Y** | N |
| SessionStart / SessionEnd | **Y** | **Y** | **Y** | **Y** | N |
| Shell command hooks | **Y** | **Y** | **Y** | **Y** | N |
| Function hooks | **Y** | **Y** | **Y** | **Y** | N |
| HTTP/webhook hooks | N | P | **Y** | N | N |
| Hook timeout & isolation | **Y** | **Y** | **Y** | P | N |
| Total event types | 6 | 29 | 29+ | Unknown | 0 |
| | | | | | |
| **Session & Context** | | | | | |
| Session persistence (disk) | **S** (file store exists, not wired) | P | **Y** | **Y** | **Y** (SQLite) |
| Session resume (--continue) | **P** (flag exists, logic present) | P | **Y** | **Y** | **Y** |
| Context compaction | **S** (compactor stub, fake summarize) | P | **Y** (16 modules) | **Y** | **Y** (auto at 95%) |
| Token counting | N | P | **Y** | **Y** | **Y** |
| Conversation history | **Y** (in-memory) | P | **Y** | **Y** | **Y** |
| | | | | | |
| **CLI** | | | | | |
| Print mode (-p) | **Y** | **Y** | **Y** | **Y** | N (REPL only) |
| Interactive REPL | **Y** (readline, 7 slash commands) | P | **Y** (Ink TUI) | **Y** (Bubble Tea) | **Y** (Bubble Tea) |
| /help, /model, /status | **Y** | P | **Y** (114 commands) | **Y** | P |
| /clear, /compact | **Y** (/compact limited) | P | **Y** | **Y** | **Y** |
| Doctor diagnostics | **Y** | P | N | N | N |
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
| Flat .md layout | **Y** | N | P | N | N |
| Frontmatter parsing | **Y** (all fields) | P | **Y** | **Y** | N |
| Skill invocation via tool | **P** | P | **Y** | **Y** | N |
| | | | | | |
| **Plugins** | | | | | |
| Plugin discovery | **P** (entry_points work) | P | **Y** | **Y** | N |
| Plugin tool loading | **S** (future) | S | **Y** | **Y** | N |
| Plugin signing/security | N | N | **Y** | P | N |
| | | | | | |
| **Multi-Agent** | | | | | |
| Agent types (coder, researcher, etc.) | **Y** (4 types, run_agent works) | P | **Y** (60+ types) | **Y** | N |
| Subagent spawning | **S** (AgentTool broken) | P | **Y** | **Y** | N |
| Parallel agent execution | N | N | **Y** | **Y** | N |
| Agent coordination | N | N | **Y** (coordinator) | **Y** | N |
| | | | | | |
| **Config** | | | | | |
| User config (~/.config/) | **Y** | P | **Y** | **Y** | **Y** |
| Project config (.duh/) | **Y** | P | **Y** (.claude/) | **Y** | **Y** |
| Environment variables | **Y** | P | **Y** | **Y** | **Y** |
| CLI flag override | **Y** | **Y** | **Y** | **Y** | **Y** |
| Instruction files (DUH.md) | **Y** | P | **Y** (CLAUDE.md) | **Y** (AGENTS.md) | **Y** (.opencode.json) |
| | | | | | |
| **TUI / Rendering** | | | | | |
| Full terminal UI | N | P (Rich components) | **Y** (Ink/React) | **Y** (Bubble Tea) | **Y** (Bubble Tea) |
| Syntax highlighting | N | P | **Y** | **Y** | **Y** |
| Progress indicators | P (tool use stderr) | P | **Y** | **Y** | **Y** |
| Markdown rendering | N | P | **Y** | **Y** | **Y** |
| Vim keybindings | N | N | **Y** | P | P |
| | | | | | |
| **Other** | | | | | |
| Voice input/output | N | N | **Y** | N | N |
| Cost tracking | N (stubbed) | N | **Y** | **Y** | N |
| Telemetry (opt-in) | N | P | **Y** | **Y** | N |
| Auto-update | N | N | **Y** | **Y** | N |
| File change tracking | N | N | **Y** | **Y** | **Y** |

---

## Architectural Comparison

| Dimension | D.U.H. | open_tengu | tengu-legacy | Codex | OpenCode |
|---|---|---|---|---|---|
| **Architecture** | Hexagonal (ports & adapters) | Direct port of TS | Monolith + React hooks | Workspace (78 Rust crates) | Go packages |
| **Kernel isolation** | Strict (kernel never imports providers) | Partial | Entangled with React | Strict (trait boundaries) | Partial |
| **Dependency injection** | Explicit (Deps dataclass) | Implicit | React context/hooks | Rust traits | Go interfaces |
| **Test strategy** | 954 unit + integration | 407 passing (mixed) | Internal | Unknown | Unknown |
| **Error handling** | Events + metadata flags | Exceptions | Events + React error boundaries | Rust Result types | Go errors |

---

## What D.U.H. Does Well (Honest Strengths)

1. **Clean kernel** — 5 files, zero provider imports. The agentic loop is genuinely provider-agnostic.
2. **3 working providers** — Anthropic, OpenAI, Ollama all stream correctly. No other Python tool has all 3.
3. **SDK drop-in** — Verified end-to-end with Claude Agent SDK. No other open tool does this.
4. **Skill format parity** — Loads .claude/skills/ natively. Skills built for Claude Code work as-is.
5. **Test discipline** — 954 tests in 6.6K LOC is ~1:7 test ratio. Higher than most OSS tools.
6. **Hooks actually execute** — Shell commands, JSON on stdin, error isolation. Not a stub.
7. **Architecture decisions documented** — 19 ADRs. Every design choice is written down.

## What D.U.H. Doesn't Have (Honest Gaps)

### Critical Gaps (would block production use)
1. **No real context compaction** — The compactor uses a fake summarizer. Long conversations will hit token limits with no recovery.
2. **No real session persistence** — FileStore exists but isn't wired into the main pipeline. Sessions are lost on restart.
3. **AgentTool is broken** — Parameter mismatch means subagent spawning crashes at runtime.
4. **No token counting** — Can't estimate costs or manage context window intelligently.

### Significant Gaps (functional but limited)
5. **MCP is scaffolded, not functional** — Config parses correctly (verified with Playwright). Connection setup has bugs (now fixed). But real tool execution via MCP is not battle-tested.
6. **Plugin loading from disk doesn't work** — Discovery via entry_points works. Loading plugins from .duh/plugins/ directories is "future work."
7. **No TUI** — readline REPL works but there's no Rich/Textual/Ink terminal UI. No syntax highlighting, no markdown rendering, no progress bars.
8. **No file change tracking** — Can't show diffs, can't rollback tool changes.
9. **No cost tracking** — /cost command is a stub.

### Minor Gaps (nice to have)
10. **No WebSearch/WebFetch tools** — 7 of 9 tools work; web tools missing.
11. **No LSP integration** — No code intelligence from language servers.
12. **No voice input/output**
13. **No auto-update mechanism**
14. **No telemetry (even opt-in)**

---

## D.U.H. vs open_tengu — Direct Comparison

| | D.U.H. | open_tengu |
|---|---|---|
| **Philosophy** | Clean-room harness (own design) | Faithful port of Claude Code |
| **LOC** | 6,615 (focused) | 418,026 (comprehensive) |
| **Working features** | Core loop + 3 providers + hooks + skills + SDK | Core loop + permissions + hooks + CLI |
| **Test health** | 954 passing, 0 failures | 407 passing, 595 import failures |
| **Provider support** | 3 (Anthropic, OpenAI, Ollama) | 1 (Anthropic only) |
| **MCP** | Scaffolded | Partially ported |
| **TUI** | readline REPL | Rich components (untested) |
| **Bash security** | Basic | 368 attack patterns |
| **Permissions** | 3 approvers | 91-test 3-tier system |
| **SDK compat** | Verified e2e | 62 protocol tests |
| **Skills** | Full .claude/ parity | Framework only |
| **Battle-tested?** | Core loop yes, periphery no | Core loop yes, most subsystems no |

**Summary**: D.U.H. is narrower but deeper — fewer features, but what's there actually works end-to-end. open_tengu is wider but shallower — more code, but 25% contains stubs and 595 modules fail import validation.

---

## What Would It Take to Reach Parity?

### To match OpenCode (simplest target, archived)
- [x] Multi-provider support
- [x] Agentic loop
- [x] Core tools
- [ ] SQLite session persistence (1-2 days)
- [ ] Context compaction with real summarization (2-3 days)
- [ ] Bubble Tea / Rich TUI (1-2 weeks)

### To match Codex (medium target)
- Everything above, plus:
- [ ] Responses API support (1 week)
- [ ] Execution sandboxing (1 week)
- [ ] Real MCP integration (1 week)
- [ ] Plugin system (3-5 days)
- [ ] File change tracking (2-3 days)
- [ ] WebSearch tool (1-2 days)

### To match Claude Code (hardest target)
- Everything above, plus:
- [ ] Full TUI with markdown rendering (2-3 weeks)
- [ ] 60+ agent types with coordination (2-3 weeks)
- [ ] 114 slash commands (2-3 weeks)
- [ ] Voice input/output (1 week)
- [ ] Remote context/compaction (1 week)
- [ ] Cost tracking and analytics (3-5 days)
- [ ] Plugin signing and marketplace (1-2 weeks)

---

*This document is an honest snapshot as of commit 2d5ca95 (April 7, 2026). Updated as features land.*
