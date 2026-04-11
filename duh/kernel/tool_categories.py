"""Tool classification constants -- shared across approval and snapshot modules.

Three tiers of tools based on their side-effect profile:

    READ_TOOLS:    Read-only, never mutate state.
    WRITE_TOOLS:   Mutate files but don't execute arbitrary commands.
    COMMAND_TOOLS:  Execute arbitrary commands or network requests.

MUTATING_TOOLS is the union of WRITE_TOOLS and COMMAND_TOOLS, provided
as a convenience for snapshot-mode blocking.
"""

from __future__ import annotations

READ_TOOLS: frozenset[str] = frozenset({
    "Read", "Glob", "Grep", "ToolSearch", "WebSearch",
    "MemoryRecall", "Skill",
})

WRITE_TOOLS: frozenset[str] = frozenset({
    "Write", "Edit", "MultiEdit", "NotebookEdit",
    "EnterWorktree", "ExitWorktree", "MemoryStore",
})

COMMAND_TOOLS: frozenset[str] = frozenset({
    "Bash", "WebFetch", "Task", "HTTP", "Database", "Docker",
    "GitHub",
})

# All tools that mutate state (writes + commands).
# Used by snapshot mode to block everything except reads.
MUTATING_TOOLS: frozenset[str] = WRITE_TOOLS | COMMAND_TOOLS
