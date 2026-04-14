"""D.U.H. security module — continuous vulnerability monitoring.

Three layers share one SecurityPolicy:
  1. CLI batch     (`duh security init | scan | diff | exception ...`)
  2. Scanner plugins (via importlib.metadata entry points)
  3. Runtime hook resolver (PRE/POST_TOOL_USE, SESSION_START/END)

See ADR-053 and docs/superpowers/specs/2026-04-14-vuln-monitoring-design.md.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
