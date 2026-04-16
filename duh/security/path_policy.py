"""Filesystem boundary enforcement (ADR-072 P0).

Tools should only access files within the project directory
(and allowed system paths like ``/tmp``).

Wire into ReadTool, WriteTool, EditTool as an *optional* parameter
so existing call-sites are unaffected.
"""

from __future__ import annotations

from pathlib import Path


class PathPolicy:
    """Enforce filesystem boundaries for tool operations.

    Tools should only access files within the project directory
    (and allowed system paths like ``/tmp``).
    """

    def __init__(
        self,
        project_root: str,
        allowed_paths: list[str] | None = None,
    ) -> None:
        self._root: Path = Path(project_root).resolve()
        if allowed_paths is None:
            allowed_paths = ["/tmp"]
        self._allowed: list[Path] = [Path(p).resolve() for p in allowed_paths]

    @property
    def project_root(self) -> Path:
        """The resolved project root directory."""
        return self._root

    def check(self, path: str) -> tuple[bool, str]:
        """Return ``(allowed, reason)``.

        A path is allowed if it resolves to a location inside the
        project root **or** inside any of the extra allowed paths.
        """
        resolved = Path(path).resolve()

        if resolved == self._root or _is_relative_to(resolved, self._root):
            return True, ""

        for allowed in self._allowed:
            if resolved == allowed or _is_relative_to(resolved, allowed):
                return True, ""

        return (
            False,
            f"Path {path} is outside project boundary ({self._root})",
        )


def _is_relative_to(child: Path, parent: Path) -> bool:
    """Back-port of Path.is_relative_to (3.9+) for safety."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False
