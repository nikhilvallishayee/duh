# ADR-045: Hook Blocking Semantics

**Status**: Proposed  
**Date**: 2026-04-11  
**Depends on**: ADR-013 (Hook System), ADR-044 (Hook Event Emission)

## Context

The current hook system is fire-and-forget: hooks execute, their output is logged, but nothing in the system reads their return values to make decisions. The reference TS harness allows `PreToolUse` hooks to return a JSON response that can block a tool call before it executes. This is essential for policy enforcement — an organization might want to block `rm -rf` via a hook rather than hardcoding it into the bash security classifier.

Additionally, hook matchers currently only support exact string matching (`matcher == "Bash"`). The TS reference supports glob patterns like `Bash(git *)` to match tool calls by name and argument pattern.

## Decision

### HookResponse Dataclass

Introduce a `HookResponse` parsed from hook stdout JSON:

```python
@dataclass
class HookResponse:
    decision: str = "continue"    # "continue" | "block" | "allow"
    suppress_output: bool = False  # suppress tool output from model context
    message: str = ""              # explanation for block/allow
```

If hook stdout is not valid JSON or does not contain a `decision` field, the response defaults to `"continue"` (no change to behavior). This makes blocking opt-in: existing hooks that print text to stdout still work as before.

### Blocking Execution

A new function `execute_hooks_with_blocking()` runs all matching hooks and aggregates decisions:
- If **any** hook returns `decision="block"`, the aggregate result is `"block"`.
- If all hooks return `"continue"` or `"allow"`, the aggregate matches.
- Block takes precedence over allow (deny-wins policy).

### Glob Matchers

Replace exact-match filtering with `fnmatch.fnmatch()` glob matching:
- `""` (empty) matches everything (unchanged behavior)
- `"Bash"` matches exactly (unchanged)
- `"Bash(git *)"` matches `"Bash(git push)"` but not `"Bash(rm -rf /)"``
- `"*"` matches everything

### Environment Variables

Shell command hooks receive three env vars:
- `TOOL_NAME` — the tool being called (e.g., `"Bash"`)
- `TOOL_INPUT` — JSON-encoded tool input
- `SESSION_ID` — current session identifier

This lets hook scripts make decisions without parsing stdin JSON.

## Consequences

### Positive
- Enables policy-as-hooks: organizations can enforce tool restrictions without forking the codebase
- Glob matchers allow fine-grained hook targeting
- Backward compatible: existing hooks continue to work (no JSON = continue)

### Negative
- Blocking hooks add latency to every tool call with matching hooks
- The deny-wins policy means a misconfigured hook can block all tool calls

### Risks
- A slow blocking hook could stall the agent loop. Mitigated by the existing per-hook timeout (default 30s).
- Malicious hooks could block all execution. Mitigated by hooks being user-configured (you only block yourself).
