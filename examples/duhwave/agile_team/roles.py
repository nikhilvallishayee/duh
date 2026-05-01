"""Five specialised :class:`Role` instances for the agile-team demo.

Each role encodes one stage of the canonical agile delivery pipeline —
PM, Architect, Engineer, Tester, Reviewer. Roles are pure data: a name,
a system-prompt fragment, a tool allowlist, and a spawn-depth budget.

The coordinator sees them in :data:`BUILTIN_AGILE_ROLES` and dispatches
the corresponding stub :class:`WorkerRunner` per role name.

These are workers (``spawn_depth=0``) — only the coordinator at the top
of the swarm has any spawn budget. Cross-worker handoff happens through
the coordinator's REPL, not directly between workers (ADR-029 §"Worker-
to-worker via the coordinator only").
"""
from __future__ import annotations

from duh.duhwave.coordinator.role import Role


# ---------------------------------------------------------------------------
# System-prompt fragments
# ---------------------------------------------------------------------------
#
# These are short, role-specific addenda that would be concatenated with
# the universal duhwave preamble at session start. The wording deliberately
# encodes the agent's *contract* in one paragraph — the kernel injects
# them verbatim, no templating. A real model run would consume these as
# the top of its system prompt; the stub runners ignore them but the
# topology declares the intent.

_PM_PROMPT = """You are a product manager. Read the user's request and the
target codebase. Your job is to extract crisp acceptance criteria — bullet
points, each independently verifiable. Do not propose a design. Do not
reference implementation details beyond what the user already named. End
with a one-line summary the rest of the team will read first."""

_ARCHITECT_PROMPT = """You are a software architect. Read the refined spec
(handle: refined_spec) and the codebase (handle: codebase). Produce a
short ADR (Architecture Decision Record) covering: API surface, data
model, key tradeoffs, and what you explicitly defer. Do not write code
yet — design before implementation. The Engineer reads this next."""

_ENGINEER_PROMPT = """You are an engineer. Implement the design from
adr_draft against the constraints in refined_spec. Output runnable Python
only — no commentary blocks. Match existing style in codebase. Keep your
implementation minimal; the Reviewer will reject anything that goes
beyond what the ADR specifies."""

_TESTER_PROMPT = """You are a test engineer. Write pytest-shaped tests
for the implementation. Cover the acceptance criteria in refined_spec
and any edge cases the implementation hints at. One test = one assertion
about one behaviour. Do not refactor the implementation — that is the
Reviewer's call."""

_REVIEWER_PROMPT = """You are a senior reviewer. Read the ADR, the
implementation, and the test suite. Comment on security, style, and
performance. End with one of: APPROVE, APPROVE WITH NITS, or REJECT.
Be specific — every concern must cite a line range or function name."""


# ---------------------------------------------------------------------------
# Tool allowlists per role
# ---------------------------------------------------------------------------
#
# The allowlist is the role's complete attack surface. The kernel filters
# the tool registry to exactly these names at session start; the role
# cannot call anything outside the list.
#
# Read-only roles (PM, Architect, Reviewer) get RLM-read tools only. The
# Engineer gets Edit/Write because it produces code. The Tester writes
# test files. None of them get Spawn — workers cannot spawn workers.

_RLM_READ_TOOLS: tuple[str, ...] = ("Peek", "Search", "Slice")
_FS_READ_TOOLS: tuple[str, ...] = ("Read", "Glob", "Grep")
_FS_WRITE_TOOLS: tuple[str, ...] = ("Edit", "Write")


_PM_TOOLS: tuple[str, ...] = _RLM_READ_TOOLS + _FS_READ_TOOLS
_ARCHITECT_TOOLS: tuple[str, ...] = _RLM_READ_TOOLS + _FS_READ_TOOLS
_ENGINEER_TOOLS: tuple[str, ...] = (
    _RLM_READ_TOOLS + _FS_READ_TOOLS + _FS_WRITE_TOOLS + ("Bash",)
)
_TESTER_TOOLS: tuple[str, ...] = (
    _RLM_READ_TOOLS + _FS_READ_TOOLS + _FS_WRITE_TOOLS + ("Bash",)
)
_REVIEWER_TOOLS: tuple[str, ...] = _RLM_READ_TOOLS + _FS_READ_TOOLS


# ---------------------------------------------------------------------------
# The five roles
# ---------------------------------------------------------------------------

PM_ROLE = Role(
    name="pm",
    system_prompt=_PM_PROMPT,
    tool_allowlist=_PM_TOOLS,
    spawn_depth=0,
)

ARCHITECT_ROLE = Role(
    name="architect",
    system_prompt=_ARCHITECT_PROMPT,
    tool_allowlist=_ARCHITECT_TOOLS,
    spawn_depth=0,
)

ENGINEER_ROLE = Role(
    name="engineer",
    system_prompt=_ENGINEER_PROMPT,
    tool_allowlist=_ENGINEER_TOOLS,
    spawn_depth=0,
)

TESTER_ROLE = Role(
    name="tester",
    system_prompt=_TESTER_PROMPT,
    tool_allowlist=_TESTER_TOOLS,
    spawn_depth=0,
)

REVIEWER_ROLE = Role(
    name="reviewer",
    system_prompt=_REVIEWER_PROMPT,
    tool_allowlist=_REVIEWER_TOOLS,
    spawn_depth=0,
)


BUILTIN_AGILE_ROLES: dict[str, Role] = {
    "pm": PM_ROLE,
    "architect": ARCHITECT_ROLE,
    "engineer": ENGINEER_ROLE,
    "tester": TESTER_ROLE,
    "reviewer": REVIEWER_ROLE,
}
"""Role registry, keyed by role name. The coordinator looks up by name
when dispatching to the runner-router; the topology in ``swarm.toml``
mirrors the same names."""


__all__ = [
    "PM_ROLE",
    "ARCHITECT_ROLE",
    "ENGINEER_ROLE",
    "TESTER_ROLE",
    "REVIEWER_ROLE",
    "BUILTIN_AGILE_ROLES",
]
