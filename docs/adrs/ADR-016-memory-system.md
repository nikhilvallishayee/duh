# ADR-016: Memory System

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-06

## Context

Production AI coding agents maintain per-project memory that persists across conversations. This memory stores user preferences, project-specific patterns, feedback, and reference material. Without memory, every conversation starts from zero -- the agent cannot learn from past interactions or remember project conventions.

### How memory works in a typical harness

Memory is stored as markdown files in a per-project directory under the user's config home. A central index file (MEMORY.md) contains one-line pointers to topic-specific files. The index is loaded into the system prompt every conversation, giving the agent awareness of what it knows without loading every detail.

Memory files have frontmatter with metadata (name, type, description) and markdown content. There are typically four memory types:

1. **User** -- personal preferences and working style
2. **Feedback** -- corrections and "do this, not that" directives
3. **Project** -- project-specific patterns and decisions
4. **Reference** -- facts, API signatures, architectural notes

The index file has a line cap (e.g., 200 lines) to prevent unbounded system prompt growth. Topic files have no hard cap but are expected to stay concise.

### What D.U.H. simplifies

| Typical feature | D.U.H. | Rationale |
|-----------------|--------|-----------|
| Per-project memory directory | Yes | Core feature |
| MEMORY.md index with pointers | Yes | Essential for prompt injection |
| 200-line index cap | Yes | Prevents unbounded prompt growth |
| 4 memory types (user/feedback/project/reference) | Yes | Clear categorisation |
| Frontmatter on topic files | Yes | Machine-readable metadata |
| Auto-extraction via background agent | Future | Requires multi-agent support |
| Semantic search / embeddings | No | Over-engineering for v0.1 |
| Memory compaction / summarisation | Future | Useful but not essential |

## Decision

### 1. Storage location

Memory lives at `~/.config/duh/projects/<sanitized-cwd>/memory/`.

The cwd is sanitized by replacing `/` with `-` and stripping the leading `-`. For example:

- `/Users/alice/Code/my-project` becomes `Users-alice-Code-my-project`
- `/home/bob/work` becomes `home-bob-work`

This gives each project its own memory namespace without collisions.

### 2. MEMORY.md index

The index file `MEMORY.md` contains one-line pointers to topic files:

```markdown
# Auto Memory - my-project

- [Project Setup](project_setup.md) -- Initial scaffold, dependencies, CI config
- [Code Style](feedback_code_style.md) -- Prefer f-strings, 88-char lines, no type: ignore
- [API Patterns](reference_api_patterns.md) -- REST conventions used in this project
```

Each line follows the format: `- [Title](filename.md) -- One-line description`

The index is capped at 200 lines. When a write would exceed this, the oldest entries are truncated from the top (after the header line).

### 3. Memory types

```python
MEMORY_TYPES = {
    "user":      "Personal preferences and working style",
    "feedback":  "Corrections and 'do this, not that' directives",
    "project":   "Project-specific patterns and decisions",
    "reference": "Facts, API signatures, architectural notes",
}
```

Topic filenames are prefixed with their type: `user_preferences.md`, `feedback_no_fallbacks.md`, `project_setup.md`, `reference_api.md`.

### 4. Topic file format

```markdown
---
name: Code Style Preferences
description: User corrections about code formatting
type: feedback
---

- Always use f-strings over .format()
- Line length: 88 characters (Black default)
- No `type: ignore` comments -- fix the types instead
```

### 5. Memory port

```python
@runtime_checkable
class MemoryStore(Protocol):
    def get_memory_dir(self) -> Path: ...
    def read_index(self) -> str: ...
    def write_index(self, content: str) -> None: ...
    def read_file(self, name: str) -> str: ...
    def write_file(self, name: str, content: str) -> None: ...
    def list_files(self) -> list[MemoryHeader]: ...
    def delete_file(self, name: str) -> None: ...
```

All operations are synchronous -- memory files are small and local.

### 6. System prompt injection

At startup, `build_memory_prompt(store)` reads the MEMORY.md index and wraps it in a system prompt section:

```
<memory>
# Auto Memory - my-project

- [Project Setup](project_setup.md) -- Initial scaffold and decisions
- [Code Style](feedback_code_style.md) -- Formatting preferences
</memory>
```

If no MEMORY.md exists, the section is omitted entirely.

## Architecture

```
CLI startup
  |
  FileMemoryStore(cwd=os.getcwd())
  |  -> ~/.config/duh/projects/<sanitized-cwd>/memory/
  |
  build_memory_prompt(store)
  |  -> reads MEMORY.md
  |  -> wraps in <memory> tags
  |
  system_prompt_parts.append(memory_prompt)
  |
  Engine(system_prompt="\n\n".join(system_prompt_parts))
```

## Consequences

- Every project gets persistent memory that survives across conversations
- The 200-line index cap keeps system prompt growth bounded
- File-based storage means memory is inspectable, editable, and git-friendly
- The port/adapter split allows swapping storage backends later (e.g., SQLite)
- Memory types provide structure without over-constraining content
- Future: a background agent can auto-extract memory entries from conversations

## Implementation Notes

- `duh/ports/memory.py` — `MemoryStore` protocol and `MemoryHeader`.
- `duh/adapters/memory_store.py` — `FileMemoryStore` implementation.
- `duh/kernel/memory.py` — `build_memory_prompt()` and helpers.
- `duh/tools/memory_tool.py` — user-facing Memory tool (recall/store).
