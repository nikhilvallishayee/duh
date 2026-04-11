# ADR-044: Emit Hook Events Across Engine, Loop, and REPL

**Status**: Proposed  
**Date**: 2026-04-11  
**Depends on**: ADR-013 (Hook System), ADR-036 (Extended Hook Events)

## Context

ADR-036 added 22 new `HookEvent` enum members to `hooks.py`, but the corresponding `execute_hooks()` calls were never placed in the codebase. The events exist as dead enum values — no code path fires them. This means user-configured hooks for `PERMISSION_REQUEST`, `PRE_COMPACT`, `USER_PROMPT_SUBMIT`, `STATUS_LINE`, `CWD_CHANGED`, and `POST_TOOL_USE_FAILURE` silently do nothing.

The hook dispatch infrastructure from ADR-013 is fully functional: `execute_hooks()` handles both command and function hooks with error isolation. The only missing piece is the emit calls at the right lifecycle points.

## Decision

### Threading the Registry

Add an optional `hook_registry` field to `Deps` so the registry is accessible in `loop.py` and `engine.py` without import coupling:

```python
# In Deps
hook_registry: Any = None  # HookRegistry | None
```

The REPL creates the registry and sets it on deps before creating the engine. This follows the existing dependency injection pattern — the kernel never imports configuration directly.

### Emit Points

| Event | Location | Trigger |
|-------|----------|---------|
| `PERMISSION_REQUEST` | `loop.py` — before `deps.approve()` | Every tool call that reaches the approval gate |
| `PERMISSION_DENIED` | `loop.py` — when approval returns `allowed=False` | Blocked tool calls |
| `POST_TOOL_USE_FAILURE` | `loop.py` — in the `except` of tool execution | Tool runtime errors |
| `PRE_COMPACT` | `engine.py` — before `deps.compact()` | Auto-compaction threshold reached |
| `POST_COMPACT` | `engine.py` — after `deps.compact()` | Compaction completed |
| `USER_PROMPT_SUBMIT` | `repl.py` — before `engine.run()` | User submits a prompt |
| `STATUS_LINE` | `repl.py` — alongside `renderer.status_bar()` | Each turn start |
| `CWD_CHANGED` | `repl.py` — when cwd changes | Directory change detected |

All emit calls are guarded with `if deps.hook_registry:` so they are no-ops when no hooks are configured (which is the common case).

## Consequences

### Positive
- All 22 new hook events become functional — users can now subscribe to permission, compaction, and prompt events
- Hook infrastructure requires zero changes — only emit calls are added
- Performance cost is nil when no hooks are registered (early `if` check)

### Negative
- Adds 8 new `await execute_hooks()` calls across 3 files — more code in the hot path
- Hook payloads become implicit API contracts that must be maintained

### Risks
- Slow shell hooks could delay the query loop. Mitigated by the existing per-hook timeout (default 30s) and error isolation.
