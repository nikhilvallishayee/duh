# ADR-004: Tool Protocol

**Status:** Accepted — implemented 2026-04-14
**Date**: 2026-04-07

## Context

Existing harnesses range from ~30-method tool interfaces to no formal interface at all. Some use MCP as the tool protocol. We need the sweet spot: rich enough for production safety, simple enough for easy authoring.

## Decision

A tool implements 4 required fields and 2 optional properties:

```python
class Tool(Protocol):
    # Required
    name: str                           # "Read", "Bash", "Edit"
    description: str                    # For the LLM prompt
    input_schema: dict[str, Any]        # JSON Schema
    async def call(self, input, context) -> ToolResult

    # Optional (have defaults)
    is_read_only: bool      # True = safe for concurrent execution
    is_destructive: bool    # True = needs explicit approval
    async def check_permissions(self, input, context) -> dict
```

### Comparison with typical 30-method interfaces

| Typical interface (30 methods) | D.U.H. (4+3) | Where it went |
|-------------------------------|---------------|---------------|
| name | name | Same |
| description | description | Same |
| inputSchema | input_schema | Same |
| call | call | Same |
| isReadOnly | is_read_only | Same |
| checkPermissions | check_permissions | Same |
| prompt | description (or adapter) | Merged — description IS the prompt |
| validateInput | JSON Schema validation in executor | Moved to executor |
| render/renderResult | UI adapter | Moved to UI layer |
| getToolUseSummary | UI adapter | Moved to UI layer |
| getActivityDescription | UI adapter | Moved to UI layer |
| extractSearchText | Tool search adapter | Moved to search layer |
| mapToolResultToBlockParam | Message adapter | Moved to message layer |
| isResultTruncated | Context manager | Moved to context layer |
| backfillObservableInput | Telemetry adapter | Moved to telemetry |
| 15+ more | Not needed | Eliminated |

### ToolResult

```python
@dataclass
class ToolResult:
    output: str | list[Any] = ""
    is_error: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)
```

### ToolContext

```python
@dataclass  
class ToolContext:
    cwd: str = "."
    tool_use_id: str = ""
    abort_signal: Any = None
    permissions: Any = None
    session_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
```

## Consequences

- Writing a new tool = one class with 4 fields
- Safety classification is declarative (is_read_only, is_destructive)
- UI rendering, telemetry, search are NOT the tool's responsibility
- MCP tools can be wrapped as Tool implementations trivially

## Implementation Notes

- Tool protocol: `duh/kernel/tool.py` (`Tool`, `ToolResult`, `ToolContext`, `TOOL_TIMEOUTS`)
- Tool registry: `duh/tools/registry.py`
- Concrete tools in `duh/tools/`: Read, Write, Edit, MultiEdit, Bash, Glob, Grep, WebFetch,
  WebSearch, Agent, Skill, ToolSearch, Task, Todo, AskUser (ADR-048), Memory, GitHub,
  Docker, HTTP, Database, LSP, Notebook, Worktree, TestImpact.
