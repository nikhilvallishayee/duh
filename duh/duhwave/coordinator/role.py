"""The coordinator/worker Role data type — ADR-031 §A.

A :class:`Role` is the engine's view of "what kind of agent am I in
this turn?". The kernel uses it to filter the tool registry and to
inject the right system prompt — no special engine code, no
``CoordinatorEngine`` subclass.

Default roles defined in :data:`BUILTIN_ROLES`:

- ``coordinator`` — synthesis-only; no Bash/Edit/Write; spawn_depth=1.
- ``worker``      — standard tool set; spawn_depth=0 (cannot Spawn).

Custom roles are declared in the swarm topology (ADR-032) and loaded
by :func:`Role.from_dict`.
"""
from __future__ import annotations

from dataclasses import dataclass, field


_COORDINATOR_PROMPT = """You are a coordinator. Your job is to:

- Understand the user's goal.
- Direct workers (via the Spawn tool) to research, implement, and verify.
- Synthesise their findings before re-delegating — never write
  "based on your findings" or "based on the research." If you do not
  understand a worker's report well enough to write a precise next
  prompt with file paths, line numbers, and exact changes, you have
  not synthesised it.
- Communicate with the user.

You do not have execution tools (Bash, Edit, Write). You have only
delegation tools (Spawn, SendMessage, Stop) and read-only inspection
(Peek, Search, Slice over your REPL handles).

Workers report back as <task-update> blocks in your next turn — never
poll. Continue an existing worker (SendMessage) when its loaded
context overlaps the next task; spawn a fresh worker when it doesn't.
"""

_WORKER_PROMPT = """You are a worker spawned to perform a focused task.

Use the tools provided. Report a clean result text describing what
you found or what you changed. Do not spawn further workers.
"""


@dataclass(slots=True, frozen=True)
class Role:
    """Configuration of an agent's behaviour for a single Task.

    Fields:
        name:             Role identifier ("coordinator", "worker", or
                          a custom name from the topology).
        system_prompt:    Injected at the top of the agent's system
                          prompt (after the universal duhwave preamble).
        tool_allowlist:   Names of tools visible to this role. The kernel
                          filters the tool registry at session start.
        spawn_depth:      Max remaining Spawn depth. Coordinators=1,
                          workers=0. Decremented on each Spawn.
    """

    name: str
    system_prompt: str
    tool_allowlist: tuple[str, ...]
    spawn_depth: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, object]) -> "Role":
        """Build a Role from a parsed-TOML dict (see ADR-032 ``[[roles]]``)."""
        return cls(
            name=str(d["name"]),
            system_prompt=str(d.get("system_prompt", "")),
            tool_allowlist=tuple(str(x) for x in d.get("tool_allowlist", ())),
            spawn_depth=int(d.get("spawn_depth", 0)),  # type: ignore[arg-type]
        )

    def child_role(self, name: str = "worker") -> "Role":
        """Construct the role for a child Task spawned from this one."""
        if self.spawn_depth <= 0:
            raise ValueError(f"role {self.name!r} has no spawn budget left")
        # By default children inherit the worker preset minus spawn capacity.
        return Role(
            name=name,
            system_prompt=BUILTIN_ROLES["worker"].system_prompt,
            tool_allowlist=BUILTIN_ROLES["worker"].tool_allowlist,
            spawn_depth=0,
        )


_COORDINATOR_TOOLS: tuple[str, ...] = (
    "Spawn",
    "SendMessage",
    "Stop",
    "Peek",
    "Search",
    "Slice",
)

_WORKER_TOOLS: tuple[str, ...] = (
    # Standard execution tools — the names match the existing D.U.H.
    # tool registry (Read, Edit, Write, Bash, Glob, Grep) plus RLM
    # read-only tools.
    "Read",
    "Edit",
    "Write",
    "Bash",
    "Glob",
    "Grep",
    "Peek",
    "Search",
    "Slice",
    "Recurse",
)


BUILTIN_ROLES: dict[str, Role] = {
    "coordinator": Role(
        name="coordinator",
        system_prompt=_COORDINATOR_PROMPT,
        tool_allowlist=_COORDINATOR_TOOLS,
        spawn_depth=1,
    ),
    "worker": Role(
        name="worker",
        system_prompt=_WORKER_PROMPT,
        tool_allowlist=_WORKER_TOOLS,
        spawn_depth=0,
    ),
}
