# ADR-013: Hook System

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-06

## Context

Production harnesses have powerful hook systems that let users run shell commands, HTTP webhooks, prompt-based hooks, and agent-based hooks at 20+ lifecycle events. This enables linting before tool calls, logging after tool calls, custom notifications, and session setup scripts.

### The copy-paste problem

A common anti-pattern is having 20+ individual dispatch functions (one per event) that each follow the same pattern: gather hooks, build JSON input, iterate, execute, aggregate results. This creates hundreds of lines of near-identical code.

D.U.H. replaces this with one function: `execute_hooks(event, data)`. An event descriptor table defines the event names and their input shapes. The dispatch logic is written once.

### What D.U.H. keeps

| Typical feature | D.U.H. | Rationale |
|---------------------|--------|-----------|
| Shell command hooks | Yes | Core use case |
| Function callback hooks | Yes | Essential for in-process validation |
| HTTP webhook hooks | Future | Lower priority for v0.1 |
| Agent/prompt hooks | No | Over-engineered for most users |
| 20+ event types | 6 core events | Start with what matters |
| Matcher patterns | Yes (simple) | Match on tool name etc. |
| Async background hooks | No | Complexity not justified yet |
| Per-source priority (user/project/local) | No | Single config source for now |

## Decision

### 1. Six core hook events

```python
class HookEvent(str, Enum):
    PRE_TOOL_USE = "PreToolUse"
    POST_TOOL_USE = "PostToolUse"
    SESSION_START = "SessionStart"
    SESSION_END = "SessionEnd"
    NOTIFICATION = "Notification"
    STOP = "Stop"
```

These cover the essential lifecycle points. More events (SubagentStart, PreCompact, etc.) can be added later by extending the enum --- no dispatch code changes needed.

### 2. Two hook types: command and function

```python
class HookType(str, Enum):
    COMMAND = "command"    # Shell command (subprocess)
    FUNCTION = "function"  # Python callable (in-process)
```

HTTP hooks are future work. Agent hooks (spawn a sub-model to evaluate) are deliberately excluded --- they are over-engineered for most use cases and can be built on top of function hooks if needed.

### 3. Data-driven dispatch

One function handles all events:

```python
async def execute_hooks(
    registry: HookRegistry,
    event: HookEvent,
    data: dict[str, Any],
    *,
    matcher_field: str | None = None,
    matcher_value: str | None = None,
    timeout: float = 30.0,
) -> list[HookResult]:
```

The caller passes the event and the data dict. The registry looks up all hooks registered for that event, optionally filters by matcher, and executes them. No per-event boilerplate.

### 4. Hook config format

```json
{
    "hooks": {
        "PreToolUse": [
            {
                "matcher": "Bash",
                "hooks": [
                    {
                        "type": "command",
                        "command": "echo 'About to run bash tool'"
                    }
                ]
            }
        ],
        "SessionStart": [
            {
                "matcher": "",
                "hooks": [
                    {
                        "type": "command",
                        "command": "echo 'Session started'"
                    }
                ]
            }
        ]
    }
}
```

This uses a standard config shape compatible with other harnesses, so users can reuse their hook definitions.

### 5. Hook execution semantics

- **Shell hooks**: Spawned as subprocesses. Input JSON is passed via stdin. Exit code 0 = success, non-zero = error. Stdout/stderr captured.
- **Function hooks**: Called with `(event, data)` args. Return a `HookResult`. Exceptions are caught and reported.
- **Timeout**: Default 30s per hook, configurable per-hook.
- **Error isolation**: One hook failing does not prevent other hooks from running.
- **Ordering**: Hooks fire in registration order. All hooks for an event run (no short-circuit on first error).

### 6. HookResult

```python
@dataclass
class HookResult:
    hook_name: str
    success: bool
    output: str = ""
    error: str = ""
    exit_code: int | None = None
```

## Architecture

```
Kernel (engine.py)
  |
  execute_hooks(registry, PRE_TOOL_USE, {"tool_name": "Bash", "input": {...}})
  |
  HookRegistry
  |  - looks up hooks for PRE_TOOL_USE
  |  - filters by matcher (tool_name == "Bash")
  |  - executes each hook
  |
  CommandHookExecutor          FunctionHookExecutor
  |                            |
  subprocess (shell cmd)       Python callable
```

## Consequences

- Adding a new event = add one enum value, zero dispatch code
- Adding a new hook type = add one executor function
- The entire hook system is compact -- one dispatch function, not 20+ per-event functions
- No per-event boilerplate functions
- Config format is compatible with other harnesses (subset of the standard shape)
- Function hooks enable in-process validation without subprocess overhead

## Implementation Notes

- `duh/hooks.py` — `HookEvent` enum (28+ events including ADR-036 extensions),
  `HookRegistry`, `execute_hooks()`, `execute_hooks_with_blocking()` (ADR-045),
  `HookResponse`, shell and function executors.
- Emit points: engine (`PRE_COMPACT`/`POST_COMPACT`), loop
  (`PERMISSION_REQUEST`/`PERMISSION_DENIED`/`POST_TOOL_USE_FAILURE`), REPL
  (`USER_PROMPT_SUBMIT`/`STATUS_LINE`/`SESSION_START`/`SESSION_END`). See ADR-044.
- Blocking semantics and glob matchers: ADR-045.
