# ADR-065: Competitive Positioning — D.U.H. vs The Field

**Status:** Active — 2026-04-16
**Date:** 2026-04-16

## D.U.H. is Not a Port

D.U.H. is a **first-principles universal harness**. It benchmarks against every major AI coding agent CLI and takes the best ideas from each.

## Competitive Landscape (April 2026)

| Feature | Claude Code | Copilot CLI | Codex CLI | Gemini CLI | OpenCode | **D.U.H.** |
|---------|------------|-------------|-----------|------------|----------|-------------|
| **Provider** | Anthropic only | GitHub/OpenAI | OpenAI + any | Google only | Any (Ollama, etc.) | **Any** |
| **Context mgmt** | 4-tier compaction | Background auto | Hosted `/compact` endpoint | Auto per-project | Configurable auto | **Best of all** |
| **Session persist** | JSONL + transcript | State files | Opaque | SQLite-like | SQLite | **JSONL per-project** |
| **Resume** | Full load, auto-compact | `--continue` | Long-running | Dir-aware auto-switch | Terminal resume | **Full + --summarize** |
| **@include** | @path in CLAUDE.md | N/A | N/A | GEMINI.md (no includes) | opencode.json | **@path (full parity)** |
| **Multi-agent** | AgentTool + coordinator | N/A | N/A | N/A | Build/Plan dual agent | **AgentTool + SwarmTool (planned)** |
| **Cache optimization** | cache_control + edits | Proprietary | Proprietary | Proprietary | N/A | **Planned (ADR-061)** |
| **Tool search** | Deferred + discovery | N/A | N/A | N/A | N/A | **Deferred (ADR-018)** |
| **Approval modes** | Tiered | Auto | Sandboxed | Auto | Auto | **Tiered (ADR-038)** |
| **Open source** | No (binary) | No (binary) | Yes (partial) | Yes | Yes | **Yes (full)** |

## What D.U.H. Takes From Each

### From Claude Code
- Multi-tier compaction (micro → snip → summary → reactive)
- @include directive in instruction files
- Per-project session scoping
- Tool search with deferred loading
- Tiered approval system

### From Copilot CLI
- **Background compaction without blocking** — user keeps typing while compaction runs async
- **Disk-based tool output storage** — large tool results written to disk instead of kept in memory transcript

### From Codex CLI
- **Structured handoff summaries** — compaction preserves: current progress, key decisions, constraints, user preferences, TODOs, critical data. More structured than free-form summary approaches.
- **Hybrid compaction** — hosted endpoint for supported models, local LLM for others

### From Gemini CLI
- **Directory-aware session switching** — cd to a different project, session auto-switches. No explicit `--continue` needed.
- **Memory visualization** — `/memory show` displays concatenated instructions. D.U.H. should have `/context` showing what the model sees.

### From OpenCode
- **Dual-agent role separation** — Build agent (code changes) vs Plan agent (strategy). Maps to D.U.H.'s `coder` vs `planner` agent types.
- **SQLite sessions** — more queryable than JSONL. Consider for future.
- **Provider-agnostic from day one** — same as D.U.H.

## D.U.H. Unique Advantages

1. **Truly universal** — any provider, any model, any tool protocol (native + MCP)
2. **Open source with full kernel** — not a binary blob, not partial open source
3. **First-principles architecture** — ports-and-adapters, not framework lock-in
4. **Instruction file compatibility** — reads CLAUDE.md, .claude/rules, .claude/skills
5. **Multi-agent with recursion control** — AgentTool with depth limits, parent tool inheritance
6. **Security-first** — trifecta check, taint tracking, confirmation gates, sandbox policies

## ADR Priorities (Reframed)

These are prioritized by competitive advantage:

| Priority | ADR | Feature | Why |
|----------|-----|---------|-----|
| **P0** | 060 | Snip compaction | Free context savings, all agents have some form of this |
| **P0** | 061 | Prompt cache | 80% cost reduction, Copilot/Codex have proprietary equivalents |
| **P1** | 059 | Context collapse | Background compaction (Copilot pattern), non-blocking |
| **P1** | 063 | Coordinator + SwarmTool | OpenCode's dual-agent inspired, industry coordinator pattern |
| **P2** | 062 | Output styles | Developer experience, all agents offer some verbosity control |
| **P2** | 064 | VCR fixtures | Testing infrastructure, enables CI without API keys |
| **P3** | — | Dir-aware sessions | Gemini's auto-switch pattern, nice UX |
| **P3** | — | Structured handoff | Codex's multi-dimensional summary format |
