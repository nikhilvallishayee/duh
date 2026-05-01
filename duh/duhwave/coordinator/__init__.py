"""Coordinator-as-prompt-role + Spawn — ADR-029, ADR-031.

The coordinator is a *role*, not a runtime construct. The kernel
doesn't know it's running a coordinator vs. a worker — same engine,
different system prompt + tool whitelist + spawn_depth.

Public surface:

- :class:`Role` / :data:`BUILTIN_ROLES` — coordinator/worker role spec
  (system prompt, tool set, depth budget).
- :class:`RLMHandleView` — worker-scoped read-only view over the
  coordinator's REPL handles (ADR-029 selective handle exposure).
- :func:`filter_tools_for_role` — tool registry filter applied at
  session start (ADR-031 §A.4 integration point).
- :class:`Spawn` — the coordinator's delegation tool (binds a worker's
  result back into the coordinator REPL as a new handle). Hosts
  instantiate one per session and wire a worker runner into it via
  :meth:`Spawn.attach_runner`.
"""
from __future__ import annotations

from duh.duhwave.coordinator.role import BUILTIN_ROLES, Role
from duh.duhwave.coordinator.spawn import Spawn
from duh.duhwave.coordinator.tool_filter import filter_tools_for_role
from duh.duhwave.coordinator.view import RLMHandleView

__all__ = [
    "Role",
    "BUILTIN_ROLES",
    "RLMHandleView",
    "Spawn",
    "filter_tools_for_role",
]
