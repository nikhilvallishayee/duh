# ADR-039: Ghost Snapshot Mode

**Status**: Proposed  
**Date**: 2026-04-08

## Context

Users frequently want to explore "what if" scenarios: "What would happen if I refactored this module?" or "Show me what the test would look like." Currently, any exploration the model does mutates the real session state — files get written, history grows, and there's no way to undo the exploration cleanly.

Git stash/branch workflows are too heavyweight for quick exploration. The user wants to say "try this idea" and then either keep the result or discard it with zero residue.

## Decision

Add a Ghost Snapshot mode that forks engine state for speculative execution:

### Forking

When the user enters ghost mode (via `/ghost` command or API flag), the engine:

1. **Snapshots** the current conversation history (deep copy)
2. **Forks** a virtual filesystem overlay (changes tracked in memory, not written to disk)
3. **Switches** the tool executor to read-only-plus-overlay mode

```python
@dataclass
class GhostSnapshot:
    id: str
    parent_messages: list[Message]  # Frozen copy of conversation at fork point
    fs_overlay: dict[str, str]      # path → content for virtual writes
    created_at: float
    label: str                      # User-provided description

class GhostExecutor(ToolExecutor):
    """Executor that intercepts writes into an in-memory overlay."""

    def __init__(self, real_executor: ToolExecutor, overlay: dict[str, str]):
        self.real = real_executor
        self.overlay = overlay

    async def execute_write(self, path: str, content: str) -> ToolResult:
        self.overlay[path] = content
        return ToolResult(output=f"[ghost] Would write {len(content)} bytes to {path}")

    async def execute_read(self, path: str) -> ToolResult:
        if path in self.overlay:
            return ToolResult(output=self.overlay[path])
        return await self.real.execute_read(path)
```

### Execution

In ghost mode, the model runs normally — it can read real files and "write" to the overlay. Tool results reflect what would happen. The conversation continues with ghost-mode messages clearly marked.

### Resolution

When the user is done exploring, two options:

- **Discard** (`/ghost discard`): Drop the overlay and ghost messages. Session returns to the fork point as if nothing happened.
- **Merge** (`/ghost merge`): Apply the overlay writes to the real filesystem and append ghost messages to the real conversation history.

### Limits

- Maximum 1 active ghost snapshot at a time (no nested ghosts)
- Overlay capped at 20 files / 10MB total (prevents runaway exploration)
- Ghost mode auto-expires after 50 turns with a warning at 40

## Consequences

### Positive
- Zero-cost exploration — users can experiment freely without fear
- Clean discard means no residue in session state or filesystem
- Merge path means good explorations become real work instantly
- Model behavior is identical in ghost mode — no special prompting needed

### Negative
- In-memory overlay limits ghost mode to moderate-size explorations
- Bash commands in ghost mode are complex — network calls and subprocess side effects can't be intercepted

### Risks
- Bash tool in ghost mode could execute real side effects (network calls, process spawning) — mitigated by running bash in dry-run mode or blocking it entirely in ghost mode
- Users may forget they're in ghost mode — mitigated by persistent UI indicator and turn-count warning
