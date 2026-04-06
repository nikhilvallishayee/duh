"""SessionStore port — how D.U.H. persists conversations."""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class SessionStore(Protocol):
    """Abstract interface for session persistence."""

    async def save(self, session_id: str, messages: list[Any]) -> None:
        """Persist messages for a session."""
        ...

    async def load(self, session_id: str) -> list[Any] | None:
        """Load messages for a session. Returns None if not found."""
        ...

    async def list_sessions(self) -> list[dict[str, Any]]:
        """List available sessions with metadata."""
        ...

    async def delete(self, session_id: str) -> bool:
        """Delete a session. Returns True if it existed."""
        ...
