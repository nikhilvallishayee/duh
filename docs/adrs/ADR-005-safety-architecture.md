# ADR-005: Safety Architecture

**Status**: Accepted  
**Date**: 2026-04-07

## Context

Claude Code has a sophisticated 3-tier permission system. Aider has none. The universal harness needs defense-in-depth that's simple to understand and extend.

## Decision

Three layers of safety, each independent:

### Layer 1: Schema Filtering (prevents at the prompt level)

Tools the model shouldn't use are excluded from the schema entirely. A read-only agent never sees Write/Edit/Bash in its tool list. Prevention > detection.

```python
def filter_tools_for_mode(tools: list[Tool], mode: str) -> list[Tool]:
    if mode == "plan":
        return [t for t in tools if t.is_read_only]
    return tools
```

### Layer 2: Approval Gate (checks before execution)

The `ApprovalGate` port is called before every tool execution. Implementations decide:

```python
class ApprovalGate(Protocol):
    async def check(self, tool_name: str, input: dict) -> dict:
        """Returns {"allowed": True} or {"allowed": False, "reason": "..."}"""
```

Built-in gates:
- `AutoApprover` — allows everything (for `--dangerously-skip-permissions`)
- `InteractiveApprover` — asks the user y/n in the terminal
- `RuleBasedApprover` — deny rules from config (path restrictions, command blocklists)

### Layer 3: Tool-Level Validation (checks within execution)

Each tool's `check_permissions` method validates its specific input:

```python
class BashTool:
    async def check_permissions(self, input, context):
        cmd = input.get("command", "")
        if is_dangerous_command(cmd):
            return {"allowed": False, "reason": f"Dangerous: {cmd}"}
        return {"allowed": True}
```

### Permission Modes

| Mode | Schema | Approval | Description |
|------|--------|----------|-------------|
| `default` | All tools | Interactive | Ask before write/destructive tools |
| `plan` | Read-only tools only | Auto | Can only read, never write |
| `auto` | All tools | Auto for safe, ask for destructive | Trust read tools, ask for writes |
| `bypass` | All tools | Auto | Skip all checks (sandboxed envs only) |

## Consequences

- Safety is layered — no single point of failure
- Each layer is independently testable
- New approval strategies = new ApprovalGate adapter
- Plan mode physically cannot write (schema filtering)
