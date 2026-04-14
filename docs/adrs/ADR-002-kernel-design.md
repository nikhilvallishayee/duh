# ADR-002: Kernel Design

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-07

## Context

The kernel is the smallest set of code that implements the universal agentic cycle: prompt → model → tool → result → iterate. It must have zero external dependencies so it can be tested, understood, and extended without any provider SDK.

## Decision

The kernel consists of exactly 5 files:

| File | Responsibility |
|------|---------------|
| `loop.py` | The async generator — one turn of the agentic cycle |
| `engine.py` | Session lifecycle — message history, turn counting |
| `tool.py` | Tool protocol — what every tool implements |
| `messages.py` | Message data model — the lingua franca |
| `deps.py` | Injectable dependencies — every external call is a seam |

### The Loop (`loop.py`)

An async generator that yields events:
```python
async for event in query(messages=msgs, deps=deps):
    match event["type"]:
        case "text_delta": ...    # streaming text
        case "tool_use": ...      # tool dispatch
        case "tool_result": ...   # tool completed
        case "assistant": ...     # complete response
        case "done": ...          # loop finished
```

### Injectable Dependencies (`deps.py`)

Every external call is injected, never imported:
```python
@dataclass
class Deps:
    call_model: CallModelFn      # how to call the LLM
    run_tool: RunToolFn          # how to execute tools
    approve: ApproveFn           # how to check permissions
    compact: CompactFn           # how to manage context
    uuid: UuidFn                 # deterministic in tests
```

### Tool Protocol (`tool.py`)

Simpler than typical 30-method interfaces:
```python
class Tool(Protocol):
    name: str
    description: str
    input_schema: dict
    async def call(self, input, context) -> ToolResult
    is_read_only: bool
    is_destructive: bool
```

## Consequences

- The kernel can be fully tested with zero network calls
- Adding a provider = writing an adapter, not modifying the kernel
- The kernel is small enough to read in one sitting
- Tests run in 0.05s (no I/O, no imports of heavy libraries)

## Implementation Notes

Core 5-file kernel (~1,016 LOC) on main:
- `duh/kernel/loop.py` — async-generator agentic cycle
- `duh/kernel/engine.py` — session lifecycle, PTL retry, budget enforcement, hook emission
- `duh/kernel/tool.py` — Tool protocol and `ToolContext`
- `duh/kernel/messages.py` — `Message` dataclass and content blocks
- `duh/kernel/deps.py` — `Deps` dependency-injection container

Additional kernel modules added over time (tokens, signals, snapshot, query_guard, redact,
plan_mode, memory, skill, attachments, etc.) sit alongside the 5 core files but do not
touch the agentic loop itself.
