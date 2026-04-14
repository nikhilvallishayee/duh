"""PEP 578 audit hook bridge — telemetry, not enforcement (ADR-054, 7.5).

This is telemetry, not enforcement. PEP 578 audit hooks observe events but
cannot prevent them. For enforcement, D.U.H. uses OS-level sandboxing
(Seatbelt on macOS, Landlock on Linux). Audit events feed the D.U.H. hook
bus so user-defined SIEM rules can match, alert, and log."""

from __future__ import annotations

import sys
from typing import Any

__all__ = ["WATCHED_EVENTS", "install"]

WATCHED_EVENTS: frozenset[str] = frozenset({
    "open",
    "socket.connect",
    "socket.gethostbyname",
    "subprocess.Popen",
    "os.exec",
    "os.posix_spawn",
    "compile",
    "exec",
    "ctypes.dlopen",
    "ctypes.cdata",
    "import",
    "pickle.find_class",
    "marshal.loads",
    "urllib.Request",
    "ssl.wrap_socket",
})

_SENSITIVE_IMPORTS: frozenset[str] = frozenset({
    "pickle", "marshal", "code", "dis", "compile",
})

_registry: Any = None
_installed: bool = False


def install(registry: Any) -> None:
    """Install the audit hook. Safe to call once per process."""
    global _registry, _installed
    if _installed:
        return
    _registry = registry
    sys.addaudithook(_audit_handler)
    _installed = True


def _audit_handler(event: str, args: tuple) -> None:
    """Audit hook callback — early-return on unwatched events."""
    if event not in WATCHED_EVENTS:
        return None
    if event == "import":
        name = args[0] if args else ""
        if name not in _SENSITIVE_IMPORTS:
            return None
    try:
        if _registry is not None:
            _registry.fire_audit(event, _sanitize(args))
    except Exception:
        pass  # audit hooks must never raise
    return None


def _sanitize(args: tuple) -> tuple:
    """Sanitize audit args — truncate long strings, redact paths."""
    sanitized = []
    for arg in args:
        if isinstance(arg, str) and len(arg) > 256:
            sanitized.append(arg[:256] + "...[truncated]")
        else:
            sanitized.append(arg)
    return tuple(sanitized)
