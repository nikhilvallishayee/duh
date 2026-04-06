# ADR-002: Kernel Design

**Status**: Accepted  
**Date**: 2026-04-07

## Context

The kernel is the smallest set of code that implements the universal agentic cycle: prompt → model → tool → result → iterate. It must have zero external dependencies so it can be tested, understood, and extended without any provider SDK.

## Decision

The kernel consists of exactly 5 files:

| File | Responsibility | LOC Target |
|------|---------------|------------|
| `loop.py` | The async generator — one turn of the agentic cycle | <200 |
| `engine.py` | Session lifecycle — message history, turn counting | <100 |
| `tool.py` | Tool protocol — what every tool implements | <80 |
| `messages.py` | Message data model — the lingua franca | <120 |
| `deps.py` | Injectable dependencies — every external call is a seam | <50 |

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

Simpler than Claude Code's 30-method interface:
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
- The kernel is small enough to read in one sitting (~600 LOC)
- Tests run in 0.05s (no I/O, no imports of heavy libraries)
