"""Role-based tool filter — ADR-031 §A.4.

The kernel reads the active :class:`Role` once at session start and
filters the registered tool list to the role's whitelist *before* the
first turn. Anything outside the whitelist is not registered — the
model never sees a schema for ``Bash`` while in the coordinator role,
so the failure mode is "the model doesn't know that capability
exists" rather than runtime denial.

This module exposes the single integration point :func:`filter_tools_for_role`
that the engine session bootstrap calls.
"""
from __future__ import annotations

from typing import Any

from duh.duhwave.coordinator.role import Role


def filter_tools_for_role(all_tools: list[Any], role: Role) -> list[Any]:
    """Return only the tools whose ``.name`` is in ``role.tool_allowlist``.

    Args:
        all_tools: The kernel's full tool registry (each entry has a
                   ``name`` attribute conforming to the Tool protocol).
        role:      The active role for this session.

    Returns:
        New list, preserving the input order, containing only allowlisted
        tools.

    Notes:
        Tools without a ``name`` attribute are silently skipped — they
        cannot be referenced by the role allowlist anyway, and a
        registry with malformed entries should not crash the filter.
    """
    allowed = set(role.tool_allowlist)
    out: list[Any] = []
    for tool in all_tools:
        name = getattr(tool, "name", None)
        if name is None:
            continue
        if name in allowed:
            out.append(tool)
    return out
