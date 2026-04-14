# ADR-048: TodoWrite and AskUserQuestion Tools

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-11
**Depends on**: ADR-004 (Tool Protocol)

## Context

The tool registry provides file operations (Read, Write, Edit, Glob, Grep), shell execution (Bash), and search (WebFetch, WebSearch). Two common agentic patterns are missing:

1. **Structured task tracking** — the model can mention tasks in prose, but has no way to create a machine-readable checklist that persists across turns. Claude Code's `TodoWrite` tool gives the model a structured way to track work items with status (pending, in_progress, done). The existing `/tasks` slash command in the REPL is backed by a `TaskTool`, but that is for user-facing task management. The model needs its own structured checklist tool.

2. **User clarification** — when the model encounters ambiguity, it must either guess or explain the ambiguity in its response and wait for the next turn. An `AskUserQuestion` tool lets the model explicitly pause and ask, with the user's answer returned as the tool result in the same turn. This is faster than round-tripping through a full turn.

## Decision

### TodoWrite

A stateful tool that maintains an in-memory dict of `TodoItem` objects:

```python
class TodoItem:
    id: str          # unique identifier
    text: str        # description
    status: str      # pending | in_progress | done | blocked | cancelled
```

The model sends a list of todos to create or update. The tool returns a formatted summary. The state lives on the `TodoWriteTool` instance and resets with each session. A `summary()` method is exposed for the `/tasks` slash command.

### AskUserQuestion

A tool that accepts a `question` string and calls an injectable `ask_fn` callback. The callback is wired to `input()` in the REPL (terminal prompting) and to an error response in non-interactive mode (SDK runner). The tool is `is_read_only=True` since it does not modify any state.

### Registration

Both tools are added to `duh/tools/registry.py` via the same try/except pattern used by all other tools:

```python
try:
    from duh.tools.todo_tool import TodoWriteTool
    tools.append(TodoWriteTool())
except ImportError:
    pass
```

## Consequences

### Positive
- The model gains structured task tracking that survives across turns
- User clarification happens within a single agentic turn instead of requiring a round-trip
- Both tools follow the existing `Tool` protocol — no framework changes
- `/tasks` slash command can display the model's own checklist

### Negative
- TodoWrite state is in-memory and resets on session restart (no persistence)
- AskUserQuestion requires a callback — non-interactive contexts must handle the error gracefully

### Risks
- The model could abuse AskUserQuestion to ask excessive questions. Mitigated by the approval gate (the user sees each tool call) and by prompt engineering in the system prompt.

## Implementation Notes

- `duh/tools/todo_tool.py` — `TodoWriteTool` with in-memory `TodoItem` state and a
  `summary()` helper used by the REPL's `/tasks` slash command.
- `duh/tools/ask_user_tool.py` — `AskUserQuestionTool` with an injectable `ask_fn`
  callback.
- Both registered in `duh/tools/registry.py` with the standard try/except-import
  pattern.
