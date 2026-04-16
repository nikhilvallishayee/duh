"""Recent-files sidebar widget (ADR-067 P2).

Shows the last N files touched by tool calls (Read, Write, Edit, etc.)
in the TUI sidebar.
"""

from __future__ import annotations

import os

from textual.app import ComposeResult
from textual.widget import Widget
from textual.widgets import Static


class RecentFilesWidget(Widget):
    """Shows recently accessed files in the sidebar."""

    DEFAULT_CSS = """
    RecentFilesWidget {
        height: auto;
        padding: 0 1;
    }
    """

    def __init__(
        self,
        *,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(name=name, id=id, classes=classes)
        self._files: list[str] = []
        self._body: Static | None = None

    # ------------------------------------------------------------------
    # Compose
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Static("[dim]No files yet[/dim]", id="recent-files-body")

    def on_mount(self) -> None:
        self._body = self.query_one("#recent-files-body", Static)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_file(self, path: str) -> None:
        """Add a single file path, deduplicating and capping at 10."""
        if path in self._files:
            self._files.remove(path)
        self._files.insert(0, path)
        self._files = self._files[:10]
        self._refresh()

    def set_files(self, paths: list[str]) -> None:
        """Replace the entire file list (already deduped/capped by caller)."""
        self._files = list(paths)[:10]
        self._refresh()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        if self._body is None:
            return
        if not self._files:
            self._body.update("[dim]No files yet[/dim]")
            return
        lines = ["[bold]Recent Files[/bold]"]
        for fp in self._files:
            short = os.path.basename(fp)
            lines.append(f"  [cyan]{short}[/cyan]")
        self._body.update("\n".join(lines))
