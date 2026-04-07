# ADR-018: Progressive Tool Disclosure

**Status**: Accepted  
**Date**: 2026-04-06

## Context

When an AI coding agent has many tools available (core tools, MCP tools, plugin tools, skills), listing every tool's full JSON Schema in the system prompt consumes significant context window space. Most tools are rarely used in any given conversation.

Production harnesses solve this with "deferred tools" -- tools whose names are visible to the model but whose full schemas are loaded on demand.

### What D.U.H. keeps

| Typical feature | D.U.H. | Rationale |
|-----------------|--------|-----------|
| Deferred tool listing | Yes | Names only in system prompt |
| On-demand schema loading | Yes | ToolSearch tool |
| Keyword search across tools | Yes | Find tools by description |
| Exact tool selection | Yes | `select:ToolName` syntax |
| Core tools always loaded | Yes | Read, Write, Edit, Bash, Glob, Grep |
| MCP tools deferred | Yes | Loaded on demand |
| Plugin tools deferred | Yes | Loaded on demand |

### What D.U.H. simplifies

| Typical feature | D.U.H. | Rationale |
|-----------------|--------|-----------|
| Fuzzy matching | No | Keyword search is sufficient |
| Ranking algorithms | No | Simple substring match |
| Tool categories | No | Flat list is fine at this scale |
| Auto-unlock heuristics | No | Model decides when to search |

## Decision

### 1. Two classes of tools

**Eager tools** (always fully loaded):
- Core tools: Read, Write, Edit, Bash, Glob, Grep
- Skill tool, ToolSearch tool

**Deferred tools** (name + description only):
- MCP server tools
- Plugin tools (optionally)
- Any tool marked as deferred

### 2. System prompt injection for deferred tools

Deferred tools appear in a `<deferred-tools>` section:

```xml
<deferred-tools>
The following tools are available but their schemas are not yet loaded.
Use the ToolSearch tool to load a tool's full schema before calling it.

- mcp__filesystem__read_file: Read a file from the filesystem
- mcp__filesystem__write_file: Write content to a file
- mcp__github__create_pr: Create a pull request
</deferred-tools>
```

### 3. ToolSearchTool

A tool implementing the Tool protocol:

```python
class ToolSearchTool:
    name = "ToolSearch"
    input_schema = {
        "properties": {
            "query": {"type": "string"},
            "select": {"type": "string"},
            "max_results": {"type": "integer", "default": 5}
        }
    }
```

**Two modes:**

1. **Search mode** (`query`): Keyword search across tool names and descriptions. Returns matching tool names with descriptions.

2. **Select mode** (`select`): Comma-separated tool names. Returns full JSON Schema definitions for the named tools, making them callable.

### 4. Deferred tool registry

```python
@dataclass
class DeferredTool:
    name: str
    description: str
    input_schema: dict[str, Any]  # Full schema, held back from prompt
    source: str = ""              # "mcp", "plugin", etc.
```

The ToolSearchTool holds a registry of deferred tools and can return their full schemas on request.

### 5. Context savings

With 20 MCP tools averaging 500 tokens each in schema:
- **Without progressive disclosure**: ~10,000 tokens in system prompt
- **With progressive disclosure**: ~400 tokens (names + descriptions only)
- **Savings**: ~96% reduction in tool-related prompt tokens

## Architecture

```
CLI startup
  |
  Classify tools
  |  - Core tools -> eager (full schema in prompt)
  |  - MCP tools -> deferred (name only in prompt)
  |  - Plugin tools -> deferred (name only in prompt)
  |
  Build ToolSearchTool(deferred_tools=deferred_list)
  |
  Inject <deferred-tools> section into system prompt
  |
  Engine runs
  |  - Model sees tool name in deferred list
  |  - Model calls ToolSearch(select="tool_name")
  |  - ToolSearch returns full schema
  |  - Model can now call the tool with correct parameters
```

## Consequences

- Initial system prompt stays small regardless of how many tools are installed
- Model discovers tools naturally through ToolSearch
- Core tools are always immediately available (no extra round-trip)
- MCP and plugin tools get loaded on demand
- No new dependencies required
- The pattern scales to hundreds of tools without prompt bloat
- Future: tool categories, usage-based preloading, tool recommendations
