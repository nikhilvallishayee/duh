# ADR-069: Memory and Cross-Session Learning — Competitive Gaps

**Status:** Proposed — 2026-04-15
**Date:** 2026-04-15
**Related:** ADR-016 (memory system), ADR-056 (auto-compact), ADR-058 (resume modes), ADR-065 (competitive positioning)

## Context

Every major AI coding agent CLI has evolved memory and learning capabilities beyond simple conversation persistence. Memory — the ability to retain, recall, and act on knowledge across sessions — is becoming a core differentiator. Users expect their agent to remember project conventions, personal preferences, past mistakes, and architectural decisions without being told twice.

D.U.H.'s current memory system (ADR-016) provides a solid foundation: per-project MEMORY.md index, topic files with frontmatter, MemoryStore/MemoryRecall tools, and JSONL-backed persistent facts. But a competitive analysis reveals six gaps where the field has moved ahead.

## Competitive Landscape

### Claude Code

Claude Code has three memory tiers:

1. **Auto-memory (MEMORY.md)** — The model automatically extracts important facts from conversations and writes them to `MEMORY.md` and topic files without the user explicitly calling a tool. When the user says "always use pytest, never unittest," the model writes that to memory unprompted. The MEMORY.md index is injected into every system prompt.

2. **Session memory (compaction summaries)** — When context is compacted, a structured summary of key decisions, current progress, and user preferences is preserved. This summary survives context window limits, so the model retains the essence of a 200-turn conversation even after aggressive compaction. The summary is reloaded on `--continue`.

3. **Instruction files (CLAUDE.md hierarchy)** — A hierarchy of instruction files from user-global (`~/.claude/CLAUDE.md`) through project-root to per-directory. Supports `@path` include directives for modular instructions. Rules in `.claude/rules/` provide per-topic overrides.

### GitHub Copilot CLI

1. **Workspace memory** — Copilot maintains a project-level memory of coding patterns, preferred libraries, and conventions. This is tied to the GitHub account and workspace, not a local file.

2. **Session state persistence** — Sessions persist across CLI restarts. Background compaction runs asynchronously, so the user is never blocked by memory management.

3. **Instruction files** — `.github/copilot-instructions.md` at the repository root provides project-level instructions.

### Codex CLI

1. **Conversation handoff summaries** — When a conversation hits context limits, Codex produces structured summaries with explicit dimensions: current progress, key decisions, constraints, user preferences, TODOs, and critical data. This multi-dimensional format is richer than free-form summaries.

2. **Persistent instructions** — `codex.md` and `AGENTS.md` files provide project-level and per-directory instructions.

3. **Hosted compaction** — For supported models, compaction runs server-side. For others, a local model handles it.

### Gemini CLI

1. **GEMINI.md instructions** — Project-level instruction file, analogous to CLAUDE.md.

2. **`/memory` commands** — Explicit slash commands for memory management: `/memory show` displays all stored instructions and facts concatenated, `/memory add` stores new facts. Memory visualization is first-class — the user can see exactly what the model knows.

3. **Session persistence** — SQLite-backed sessions with automatic project-aware switching. Changing directories auto-switches the active session context.

### OpenCode

1. **SQLite session storage** — All sessions stored in SQLite, making them queryable and efficiently searchable across projects. More structured than flat JSONL files.

2. **Project-specific config** — `opencode.json` per project, analogous to instruction files but JSON-structured rather than markdown.

3. **Dual-agent architecture** — Separate Build and Plan agents maintain their own context, effectively giving the system two parallel memory streams for strategy vs. execution.

## D.U.H.'s Current State (ADR-016)

| Capability | Status | Notes |
|-----------|--------|-------|
| Per-project memory directory | Implemented | `~/.config/duh/projects/<sanitized-cwd>/memory/` |
| MEMORY.md index (200-line cap) | Implemented | Injected into system prompt |
| 4 memory types (user/feedback/project/reference) | Implemented | Topic files with frontmatter |
| MemoryStore tool (explicit save) | Implemented | Model calls tool to save a fact |
| MemoryRecall tool (keyword search) | Implemented | Substring match on key/value/tags |
| Persistent facts (JSONL, 500 cap) | Implemented | Per-project `facts.jsonl` |
| Instruction files (DUH.md/CLAUDE.md hierarchy) | Implemented | User-global + git-root-to-cwd walk + @include |
| Prompt injection of memory + facts | Implemented | `build_memory_prompt()` in system prompt |

## Gap Analysis

### Gap 1: Auto-Memory Extraction

**What it is:** The model automatically identifies important facts during conversation and saves them to memory without being explicitly asked. When the user says "I prefer ruff over flake8" or "this project uses PostgreSQL 16," the model writes that to memory unprompted.

**Who has it:** Claude Code does this natively — the model decides what is worth remembering and calls its memory write tool autonomously. Gemini CLI's `/memory` commands make manual storage easy, but automatic extraction is a model-side behavior.

**D.U.H.'s gap:** D.U.H. has the MemoryStore tool, but the model only uses it when explicitly prompted or when the system prompt instructs it to. There is no mechanism that triggers memory extraction automatically — it depends entirely on the model choosing to call the tool.

**Proposed fix:** Add a system prompt directive that instructs the model to proactively save important facts:

```
When the user states a preference, convention, or important project fact,
save it using MemoryStore without being asked. Examples:
- "We use pytest" -> MemoryStore(key="test-framework", value="Uses pytest, not unittest")
- "Always use f-strings" -> MemoryStore(key="string-format", value="Prefer f-strings over .format()")
```

Additionally, a post-session hook could scan the conversation for unsaved preferences and prompt the model to extract them. This is lighter than a background agent and works within the single-agent architecture.

**Complexity:** Low. System prompt change + optional post-session extraction pass.

### Gap 2: Session Memory (Compaction Summaries)

**What it is:** When context is compacted, a structured summary of the session's key learnings is preserved and survives across compaction boundaries. This means the model retains knowledge of decisions made 200 turns ago, even though those turns have been removed from context.

**Who has it:** Claude Code preserves session summaries that survive compaction. Codex CLI produces multi-dimensional handoff summaries with explicit fields (progress, decisions, constraints, preferences, TODOs, critical data).

**D.U.H.'s gap:** When `AdaptiveCompactor` or `SimpleCompactor` fires, the summary is a free-form condensation of the dropped messages. There is no structured extraction of "what should the model remember from this segment." Key decisions and user corrections are lost in the summary noise. Furthermore, the summary is not saved to persistent memory — it only lives in the current session's message list.

**Proposed fix:** Two changes:

1. **Structured compaction summary** — When compacting, extract a structured object:
   ```python
   @dataclass
   class CompactionSummary:
       progress: str          # What was accomplished
       decisions: list[str]   # Key architectural/design decisions
       preferences: list[str] # User preferences discovered
       constraints: list[str] # Constraints and gotchas found
       todos: list[str]       # Remaining work items
       critical_data: str     # File paths, variable names, error messages
   ```

2. **Summary-to-memory bridge** — After compaction, automatically persist `decisions` and `preferences` to the MemoryStore as facts. This way, even if the session ends, the learnings survive.

**Complexity:** Medium. Requires changes to the compaction pipeline and a new extraction prompt.

### Gap 3: Memory Search Quality

**What it is:** Semantic search over stored memories using embeddings, so "authentication approach" matches a fact stored under "JWT with refresh tokens" even though the keywords don't overlap.

**Who has it:** Most agent CLIs with server-side infrastructure use embedding-based retrieval. OpenCode's SQLite storage enables structured queries. The field is moving toward hybrid search (keyword + semantic).

**D.U.H.'s gap:** `recall_facts()` does substring matching on key/value/tags. This is fast and simple, but it misses semantic relationships. Searching for "how we handle auth" won't find a fact keyed as "token-refresh-strategy" with value "JWT access + refresh token rotation." As the fact store grows past 50-100 entries, keyword search becomes increasingly inadequate.

**Proposed fix:** A two-tier search:

1. **Keyword search (current)** — Fast, zero-dependency, handles exact matches.
2. **Semantic search (new)** — Optional embedding-based search using a lightweight local model (e.g., `sentence-transformers/all-MiniLM-L6-v2` via ONNX, or the provider's own embedding API). Falls back to keyword search if no embedding model is configured.

Store embeddings alongside facts in a separate `.npy` or SQLite file. On recall, compute query embedding and rank by cosine similarity.

**Complexity:** Medium-high. Requires an embedding provider port, vector storage, and a dependency decision (local ONNX vs. API call vs. optional).

### Gap 4: Memory Decay and Cleanup

**What it is:** Old, stale, or superseded facts are automatically deprioritized or removed. Without decay, the memory store accumulates noise — facts about deleted files, outdated API patterns, preferences the user has since changed.

**Who has it:** No agent CLI has sophisticated memory decay today. Claude Code's auto-memory overwrites topic files, providing implicit deduplication. OpenCode's structured storage makes manual cleanup possible.

**D.U.H.'s gap:** Facts accumulate forever up to the 500-entry JSONL cap, at which point the oldest are pruned by position. There is no concept of relevance decay, access tracking, or contradiction detection. A fact stored 6 months ago about a dependency that has since been removed sits alongside current facts with equal weight.

**Proposed fix:**

1. **Access tracking** — Record a `last_accessed` timestamp on each fact when it appears in a recall result. Facts not accessed in N days (configurable, default 90) are candidates for cleanup.
2. **Contradiction detection** — When storing a new fact with the same key, flag the old value as superseded (already partially implemented via key deduplication).
3. **Decay scoring** — Rank facts by `recency * access_frequency`. The bottom N% are moved to an archive file, removed from prompt injection but still searchable.
4. **`duh memory gc`** CLI command — Manual garbage collection that shows stale facts and asks for confirmation before pruning.

**Complexity:** Low-medium. Access tracking is straightforward; decay scoring requires a ranking heuristic.

### Gap 5: Cross-Project Memory Sharing

**What it is:** Global learnings about user preferences that apply across all projects — code style, preferred tools, communication style, working hours — stored once and available everywhere.

**Who has it:** Claude Code has user-global `~/.claude/CLAUDE.md` for cross-project instructions. Copilot CLI ties preferences to the GitHub account. Gemini CLI's memory is project-scoped but instructions in `~/.config/gemini/` are global.

**D.U.H.'s gap:** D.U.H. has user-global instructions (`~/.config/duh/DUH.md`) but no user-global memory facts. The MemoryStore and facts.jsonl are strictly per-project. If the user says "I always prefer ruff over flake8" in project A, project B doesn't know this unless the user repeats it or manually copies the DUH.md instruction.

**Proposed fix:**

1. **Global facts namespace** — A `~/.config/duh/memory/global/facts.jsonl` that stores user-wide preferences.
2. **Namespace parameter on MemoryStore** — `MemoryStore(key="linter", value="ruff", namespace="global")` writes to the global store. Default namespace remains project-local.
3. **Prompt injection** — `build_memory_prompt()` loads both project-local and global facts, with project-local taking precedence on key conflicts.
4. **Promotion** — A fact stored in 3+ projects with the same key/value is automatically promoted to global (or suggested for promotion).

**Complexity:** Low. Mostly plumbing — a second facts directory and a namespace parameter on existing methods.

### Gap 6: Memory Visualization

**What it is:** User-facing commands to inspect, search, and manage what the model knows. The ability to see "what does my agent remember about this project?" and prune incorrect entries.

**Who has it:** Gemini CLI has `/memory show` and `/memory search` as first-class slash commands. Claude Code surfaces auto-memory through the MEMORY.md file (human-readable and editable). OpenCode's SQLite storage is queryable.

**D.U.H.'s gap:** D.U.H. has no slash command or CLI command to view stored memories. The MemoryRecall tool is model-facing, not user-facing. A user who wants to see what the agent remembers must manually navigate to `~/.config/duh/memory/<project-hash>/facts.jsonl` and read raw JSON lines. There is no `/memory` command, no `duh memory list` CLI, no way to search or delete from the TUI.

**Proposed fix:**

1. **CLI commands** — `duh memory list`, `duh memory search <query>`, `duh memory show`, `duh memory delete <key>`, `duh memory gc`. These operate on the current project's memory by default, with `--global` for user-wide facts.
2. **TUI slash commands** — `/memory show` displays all facts for the current project. `/memory search <query>` searches. `/memory delete <key>` removes.
3. **`/context` command** — Show everything the model currently sees: instruction files loaded, memory facts injected, session summary, system prompt sections. This is the "what does the model know?" command that Gemini CLI pioneered.

**Complexity:** Low-medium. CLI commands are straightforward. TUI integration requires a slash command handler.

## Priority Matrix

| Gap | Impact | Effort | Priority | Rationale |
|-----|--------|--------|----------|-----------|
| **1. Auto-memory extraction** | High | Low | **P0** | System prompt change, massive UX improvement. Every competitor has this. |
| **6. Memory visualization** | High | Low | **P0** | Users cannot manage what they cannot see. CLI + TUI commands. |
| **2. Session memory (compaction summaries)** | High | Medium | **P1** | Prevents knowledge loss across compaction. Builds on ADR-058/059. |
| **5. Cross-project memory sharing** | Medium | Low | **P1** | Global preferences are the #1 user complaint about per-project memory. |
| **4. Memory decay/cleanup** | Medium | Low | **P2** | Not urgent at small scale, becomes critical past 100+ facts. |
| **3. Memory search quality** | Medium | High | **P3** | Keyword search is adequate for <100 facts. Semantic search adds a dependency. |

## Decision

Address these gaps in three waves:

### Wave 1 (P0 — immediate)

- Add auto-memory system prompt directive instructing the model to proactively store preferences and project facts.
- Implement `duh memory list|search|show|delete|gc` CLI subcommands.
- Implement `/memory show`, `/memory search`, `/memory delete` TUI slash commands.
- Implement `/context` slash command showing all loaded instructions + memory.

### Wave 2 (P1 — after ADR-058/059 compaction work)

- Structured compaction summaries with explicit dimensions (progress, decisions, preferences, constraints, TODOs, critical data).
- Summary-to-memory bridge: persist learnings from compaction to the fact store.
- Global facts namespace with promotion heuristic.

### Wave 3 (P2/P3 — when scale demands it)

- Access tracking and decay scoring for memory cleanup.
- Optional semantic search via embedding provider port.
- `duh memory gc` with interactive confirmation.

## Consequences

- **Auto-memory** makes D.U.H. feel like it learns from every conversation, matching Claude Code's most-loved feature.
- **Memory visualization** gives users confidence and control — they can verify, correct, and prune what the agent knows.
- **Session memory** prevents the "compaction amnesia" problem where the model forgets decisions made earlier in a long session.
- **Cross-project sharing** eliminates the repeated-preference problem that every per-project memory system suffers from.
- **Decay and cleanup** prevent the memory store from becoming a junk drawer over months of use.
- **Semantic search** is deliberately deferred — keyword search on structured key/value facts is adequate at current scale, and adding an embedding dependency is a significant architectural choice.

## Implementation Notes

- Auto-memory directive goes in `duh/kernel/prompts/` as part of the base system prompt.
- CLI memory commands go in `duh/cli/` as a new `memory` subcommand group.
- TUI slash commands extend the existing slash command handler in `duh/ui/`.
- Global facts use the same `FileMemoryStore` with a `namespace` parameter, defaulting to project-local.
- Structured compaction summaries extend `AdaptiveCompactor` in `duh/adapters/compactor.py`.
- The `/context` command reads from the same `build_memory_prompt()` + instruction loader used at startup.
