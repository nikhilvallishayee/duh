"""Handle protocol — names + metadata for variables in the REPL.

A :class:`Handle` is the agent's reference to a piece of bulk content.
The agent never sees the bytes inline — it sees the handle's metadata
(name, type, size) and operates via :class:`~duh.duhwave.rlm.tools` on
the underlying value.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


HandleKind = Literal["str", "bytes", "list", "dict", "json"]


@dataclass(frozen=True, slots=True)
class Handle:
    """A reference to a value bound in a REPL.

    Immutable. The store holds the actual value; this is metadata only.
    """

    name: str
    kind: HandleKind
    total_chars: int = 0
    total_lines: int = 0
    total_bytes: int = 0
    sha256: str = ""
    bound_at: float = 0.0
    bound_by: str = "user"  # "user" | "tool:<name>" | "child:<task_id>"

    def summary(self) -> str:
        """One-line description for the model's system block."""
        size = f"{self.total_chars:,} chars"
        if self.total_lines:
            size += f", {self.total_lines:,} lines"
        return f"  {self.name}  ({self.kind}, {size})  bound_by={self.bound_by}"


class HandleStore:
    """In-memory metadata index for handles bound in a REPL.

    Values live in the REPL subprocess; this stores only the
    :class:`Handle` records the parent uses to reason about them.
    """

    def __init__(self) -> None:
        self._handles: dict[str, Handle] = {}

    def bind(self, handle: Handle) -> None:
        """Insert a new handle. Raises ``ValueError`` on name clash."""
        if handle.name in self._handles:
            raise ValueError(f"handle already bound: {handle.name}")
        self._handles[handle.name] = handle

    def rebind(self, handle: Handle) -> None:
        """Replace an existing handle (used by tools that mutate)."""
        self._handles[handle.name] = handle

    def get(self, name: str) -> Handle | None:
        """Return the handle metadata for ``name``, or ``None``."""
        return self._handles.get(name)

    def list(self) -> list[Handle]:
        """All bound handles, in insertion order."""
        return list(self._handles.values())

    def remove(self, name: str) -> None:
        """Drop the named handle if present. Idempotent."""
        self._handles.pop(name, None)

    def system_block(self) -> str:
        """Render the handle list as a system-prompt fragment."""
        if not self._handles:
            return "No handles bound. Use load_directory / load_file to bind one."
        lines = ["You have a Python REPL with these variables loaded:"]
        for h in self._handles.values():
            lines.append(h.summary())
        lines.append("")
        lines.append(
            "Use Peek / Search / Slice / Recurse / Synthesize tools to "
            "interact. The full content is addressable; nothing has been "
            "summarised."
        )
        return "\n".join(lines)
