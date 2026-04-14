"""Lethal trifecta capability matrix (ADR-054, workstream 7.3).

Refuse to start a session where READ_PRIVATE + READ_UNTRUSTED + NETWORK_EGRESS
are all enabled simultaneously unless the operator explicitly acknowledges.

Simon Willison's observation: read-private-data + read-untrusted-content +
network-egress = the classic exfiltration vector.
"""

from __future__ import annotations

from enum import Flag, auto

__all__ = [
    "Capability",
    "LETHAL_TRIFECTA",
    "LethalTrifectaError",
    "compute_session_capabilities",
    "check_trifecta",
]


class Capability(Flag):
    """Capability flags for tool classification."""

    NONE = 0
    READ_PRIVATE = auto()    # Read, MemoryRecall, Grep on cwd, Database, LSP
    READ_UNTRUSTED = auto()  # WebFetch, WebSearch, MCP_OUTPUT, MCP tools
    NETWORK_EGRESS = auto()  # WebFetch, Bash (unsandboxed), HTTP, Docker
    FS_WRITE = auto()        # Write, Edit, MultiEdit, NotebookEdit
    EXEC = auto()            # Bash, Docker, Skill, Agent, NotebookEdit kernel


LETHAL_TRIFECTA = (
    Capability.READ_PRIVATE | Capability.READ_UNTRUSTED | Capability.NETWORK_EGRESS
)


class LethalTrifectaError(RuntimeError):
    """Raised when all three trifecta capabilities are active without ack."""


def compute_session_capabilities(tools: list) -> Capability:
    """Union all tool capabilities for the current session.

    Args:
        tools: List of tool objects, each with a ``capabilities`` attribute.

    Returns:
        Combined :class:`Capability` flags for the session.
    """
    caps = Capability.NONE
    for tool in tools:
        caps |= tool.capabilities
    return caps


def check_trifecta(caps: Capability, *, acknowledged: bool = False) -> None:
    """Raise :class:`LethalTrifectaError` if all three trifecta caps are active
    and the operator has not acknowledged the risk.

    Args:
        caps: Combined session capabilities from :func:`compute_session_capabilities`.
        acknowledged: Whether the operator has explicitly acknowledged the risk
            (via CLI flag or config key).

    Raises:
        LethalTrifectaError: If the lethal trifecta is present and not acknowledged.
    """
    if (caps & LETHAL_TRIFECTA) == LETHAL_TRIFECTA and not acknowledged:
        raise LethalTrifectaError(
            "This session enables all three of READ_PRIVATE, READ_UNTRUSTED, "
            "NETWORK_EGRESS simultaneously. This combination is the classic "
            "exfiltration trifecta — data read from private sources can be "
            "smuggled out via untrusted content through network egress.\n\n"
            "To proceed, either:\n"
            "  - Disable one of: WebFetch / WebSearch / MCP untrusted servers\n"
            "  - Disable the source of READ_PRIVATE\n"
            "  - Acknowledge with: duh --i-understand-the-lethal-trifecta\n"
            "  - Or set trifecta_acknowledged: true in .duh/security.json"
        )
