"""duhwave — D.U.H.'s persistent agentic-swarm extension.

Composes five ADRs into one runtime:

- ADR-028 :mod:`duh.duhwave.rlm`         — RLM context engine
- ADR-029 :mod:`duh.duhwave.coordinator` — recursive cross-agent links
- ADR-030 :mod:`duh.duhwave.task`        — persistent Task lifecycle
- ADR-031 :mod:`duh.duhwave.ingress`     — event ingress
- ADR-032 :mod:`duh.duhwave.spec`        — topology DSL + bundles
- ADR-032 :mod:`duh.duhwave.cli`         — control plane

A duhwave host is a long-running process that hosts agents past a
single CLI invocation. Triggers (webhooks / file watches / cron / MCP
push) spawn :class:`Task` records; coordinators delegate to workers
via the RLM substrate, sharing variable handles instead of prose.

The package is import-clean — importing :mod:`duh.duhwave` does not
start a host or open any sockets. Use :func:`duh.duhwave.cli.main` or
``duh wave start`` to run.
"""
from __future__ import annotations

__all__ = [
    "rlm",
    "task",
    "coordinator",
    "ingress",
    "spec",
    "bundle",
    "cli",
]
