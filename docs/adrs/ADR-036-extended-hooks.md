# ADR-036: Extended Hook Events

**Status:** Accepted — implemented 2026-04-15
**Date**: 2026-04-08

## Context

D.U.H. currently supports 6 hook events: `pre_tool_use`, `post_tool_use`, `pre_query`, `post_query`, `session_start`, and `session_end`. The reference TS harness supports 29 events covering permissions, compaction, notifications, IDE integration, and lifecycle management.

Key missing events include:
- **Permission hooks** — no way to customize approval logic via hooks
- **Compaction hooks** — no notification when context is compacted
- **Stop hooks** — no way to intercept model stop reasons
- **Notification hooks** — no way to trigger external alerts on events
- **IDE hooks** — no integration points for editor extensions

The existing hook dispatch mechanism (ADR-013) is extensible by design. Adding events requires no new executor types — just new event names and trigger points.

## Decision

Add 23 new hook events to the existing dispatch system:

### Permission Events
| Event | Trigger | Payload |
|-------|---------|---------|
| `pre_permission_check` | Before approval gate runs | tool name, input |
| `post_permission_check` | After approval decision | tool name, decision, reason |
| `permission_denied` | When a tool call is blocked | tool name, input, reason |

### Context Events
| Event | Trigger | Payload |
|-------|---------|---------|
| `pre_compaction` | Before context compaction | message count, context size |
| `post_compaction` | After context compaction | removed count, new size |
| `context_warning` | Context at 80% of limit | current size, limit |

### Model Events
| Event | Trigger | Payload |
|-------|---------|---------|
| `model_stop` | Model returns stop reason | stop reason, usage |
| `model_error` | Provider returns error | error type, message |
| `model_retry` | Before retrying a failed call | attempt number, error |
| `model_switch` | Provider/model changed mid-session | old model, new model |

### Tool Events (extended)
| Event | Trigger | Payload |
|-------|---------|---------|
| `tool_error` | Tool execution fails | tool name, error |
| `tool_timeout` | Tool exceeds time limit | tool name, elapsed |
| `tool_output_truncated` | Output was truncated | tool name, original size |

### Session Events (extended)
| Event | Trigger | Payload |
|-------|---------|---------|
| `session_restore` | Session loaded from disk | session id, message count |
| `session_compact` | Session compacted on save | old size, new size |

### Notification Events
| Event | Trigger | Payload |
|-------|---------|---------|
| `task_complete` | Long-running task finishes | duration, result summary |
| `error_threshold` | N consecutive errors | error count, last error |
| `idle_timeout` | No activity for N minutes | idle duration |

### IDE Integration Events
| Event | Trigger | Payload |
|-------|---------|---------|
| `file_edit_start` | Before a file is edited | file path, tool name |
| `file_edit_end` | After a file edit completes | file path, success |
| `diagnostics_request` | Model requests diagnostics | file path, range |
| `workspace_change` | Working directory changes | old path, new path |
| `focus_change` | Active file changes in IDE | file path |

### Dispatch

All new events use the same `HookDispatcher.emit(event, payload)` mechanism from ADR-013. Hook executors (shell, Python, HTTP) work unchanged. Events are fire-and-forget by default; `pre_*` events support blocking mode for veto logic.

## Consequences

### Positive
- Enables rich plugin ecosystems (IDE extensions, notification bots, analytics)
- Permission hooks enable custom approval logic without modifying core
- Compaction hooks enable logging/alerting on context management
- No new infrastructure — same dispatch, same executors

### Negative
- 29 total events is a larger surface area to document and maintain
- Blocking pre-hooks can slow down the loop if hook executors are slow

### Risks
- Hook payload shape becomes an implicit API contract — breaking changes to payloads affect plugins. Mitigated by versioning payloads.

## Implementation Notes

- `duh/hooks.py` — `HookEvent` enum now carries the core six (ADR-013) plus 22 extended
  events (PERMISSION_REQUEST, PRE_COMPACT, POST_COMPACT, USER_PROMPT_SUBMIT, STATUS_LINE,
  CWD_CHANGED, POST_TOOL_USE_FAILURE, SUBAGENT_START/STOP, TASK_CREATED/COMPLETED,
  ELICITATION, ELICITATION_RESULT, FILE_CHANGED, FILE_SUGGESTION, WORKTREE_CREATE/REMOVE,
  CONFIG_CHANGE, INSTRUCTIONS_LOADED, SETUP, TEAMMATE_IDLE, etc.).
- Emit sites were added by ADR-044 in `duh/kernel/loop.py`, `duh/kernel/engine.py`, and
  `duh/cli/repl.py`.

Related: ADR-013 (hook system), ADR-044 (emission), ADR-045 (blocking semantics).
