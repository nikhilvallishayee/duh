"""Command palette modal for the D.U.H. Textual TUI (ADR-073 Wave 3 task 9).

Provides a ``Ctrl+K``-invokable overlay that fuzzy-searches over every
slash command exposed by the TUI (both the shared REPL commands and the
TUI-local ``/style`` / ``/mode`` / ``/session`` / ``/quit`` / ``/theme``).

Selecting an entry dismisses the modal and returns the command *name*
(including the leading slash) to the caller.  The caller is expected to
insert ``"/<name> "`` into the prompt ``TextArea`` so the user can type
any remaining arguments and hit Enter to run.

Rationale
---------
Codex surfaces discoverability via ``?``; OpenCode via ``Ctrl+K``.  D.U.H.
follows OpenCode's binding because ``?`` collides with some readline
configurations and with shell-interpreted argv.  Users who don't know the
exact syntax of a slash command can browse the palette, pick one, and
then fill in arguments inline — no more "did I spell /compact-stats
right?" moments.

Fuzzy matching
--------------
``rapidfuzz`` is not a dependency.  A dependency-free substring match is
used instead:

* exact prefix match (best)
* leading-char subsequence match ("cs" -> "/compact-stats") — scored by
  distance between chars
* fallback to plain ``in`` substring check

The scoring function is deterministic and stable across Python releases
so the palette order is predictable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Input, OptionList, Static
from textual.widgets.option_list import Option


# ---------------------------------------------------------------------------
# Catalog — the list of commands shown in the palette
# ---------------------------------------------------------------------------


# TUI-local commands that the REPL does not know about.  Paired with a short
# description that mirrors the style of SLASH_COMMANDS entries.
TUI_LOCAL_COMMANDS: dict[str, str] = {
    "/style": "TUI output style (/style default|concise|verbose)",
    "/mode": "TUI mode (/mode normal|coordinator)",
    "/session": "Show TUI session info panel",
    "/theme": "Switch TUI theme (/theme, /theme <name>)",
    "/quit": "Exit the TUI",
    "/q": "Exit the TUI (alias for /quit)",
}


def build_command_catalog() -> list[tuple[str, str]]:
    """Return ``[(name, description), ...]`` for every command in the palette.

    Merges :data:`duh.cli.repl.SLASH_COMMANDS` (the canonical source) with
    :data:`TUI_LOCAL_COMMANDS`.  TUI-local commands come last so the
    catalog matches user intuition ("shared commands first").
    """
    from duh.cli.repl import SLASH_COMMANDS

    catalog: list[tuple[str, str]] = []
    seen: set[str] = set()
    for name, desc in SLASH_COMMANDS.items():
        if name in seen:
            continue
        catalog.append((name, desc))
        seen.add(name)
    for name, desc in TUI_LOCAL_COMMANDS.items():
        if name in seen:
            continue
        catalog.append((name, desc))
        seen.add(name)
    return catalog


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _score(query: str, name: str, description: str) -> int:
    """Return a match score for *query* against a command entry.

    Higher is better.  ``0`` means "no match".  The scoring is intentionally
    cheap and pure-Python so it works without ``rapidfuzz`` installed.

    Rules (lowercase-insensitive):

    * exact name match            → 1_000_000
    * name startswith query       → 100_000 − len(name)
    * name contains query         → 10_000 − name.index(query)
    * description contains query  → 1_000 − description.index(query)
    * every char of *query* appears in order in *name*
      (subsequence match)         → 100 − gap_penalty
    * otherwise                   → 0
    """
    if not query:
        # Empty query matches everything; preserve original order by returning 1.
        return 1

    q = query.lower().lstrip("/")
    n = name.lower().lstrip("/")
    d = description.lower()

    if q == n:
        return 1_000_000
    if n.startswith(q):
        return 100_000 - len(n)
    if q in n:
        return 10_000 - n.index(q)
    if q in d:
        return 1_000 - d.index(q)

    # Subsequence match: every char of q appears in n in order.
    idx = 0
    last_pos = -1
    gap_penalty = 0
    for ch in q:
        found = n.find(ch, idx)
        if found == -1:
            return 0
        if last_pos >= 0:
            gap_penalty += found - last_pos - 1
        last_pos = found
        idx = found + 1
    return max(1, 100 - gap_penalty)


def filter_commands(
    query: str,
    catalog: Iterable[tuple[str, str]] | None = None,
) -> list[tuple[str, str]]:
    """Return *catalog* entries sorted by descending match score.

    Entries with score ``0`` are omitted.  When *query* is empty, the full
    catalog is returned in its original order (the ``1`` score from
    :func:`_score` is equal for all entries, so ``sorted`` is stable).
    """
    items = list(catalog) if catalog is not None else build_command_catalog()
    scored = [(name, desc, _score(query, name, desc)) for name, desc in items]
    scored = [entry for entry in scored if entry[2] > 0]
    scored.sort(key=lambda t: (-t[2], t[0]))
    return [(name, desc) for name, desc, _ in scored]


# ---------------------------------------------------------------------------
# Modal screen
# ---------------------------------------------------------------------------


@dataclass
class _Selection:
    """Return value of :class:`CommandPalette` when the user picks an entry."""

    name: str

    def __str__(self) -> str:  # pragma: no cover — convenience only
        return self.name


class CommandPalette(ModalScreen[str | None]):
    """Fuzzy-searchable slash command picker.

    Returns the selected command name (e.g. ``"/help"``) via
    :meth:`ModalScreen.dismiss`, or ``None`` if the user cancels with Esc.
    """

    DEFAULT_CSS = """
    CommandPalette {
        align: center middle;
        background: $background 60%;
    }

    #palette-dialog {
        width: 80;
        max-width: 95%;
        height: auto;
        max-height: 24;
        background: $surface;
        border: tall $primary;
        padding: 0 1;
    }

    #palette-title {
        color: $primary;
        text-style: bold;
        padding: 0 1;
    }

    #palette-input {
        background: $background;
        border: solid $primary-darken-2;
        margin: 1 0;
    }

    #palette-input:focus {
        border: solid $primary;
    }

    #palette-list {
        height: auto;
        max-height: 16;
        background: $surface;
        border: none;
    }

    #palette-hint {
        color: $text-muted;
        padding: 0 1;
        text-align: center;
    }

    #palette-empty {
        color: $text-muted;
        padding: 1 2;
        text-style: italic;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_palette", "Cancel", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
        Binding("enter", "select", "Select", show=False),
    ]

    def __init__(
        self,
        catalog: list[tuple[str, str]] | None = None,
    ) -> None:
        super().__init__()
        self._catalog: list[tuple[str, str]] = (
            catalog if catalog is not None else build_command_catalog()
        )
        # Populated in compose() and mutated by the Input.Changed handler.
        self._filtered: list[tuple[str, str]] = list(self._catalog)

    # ------------------------------------------------------------------
    # Composition
    # ------------------------------------------------------------------

    def compose(self) -> ComposeResult:
        with Vertical(id="palette-dialog"):
            yield Static("Command Palette", id="palette-title")
            yield Input(
                placeholder="Filter commands…",
                id="palette-input",
            )
            yield OptionList(
                *self._build_options(self._filtered),
                id="palette-list",
            )
            yield Static(
                "[dim]↑/↓ navigate   Enter select   Esc cancel[/]",
                id="palette-hint",
            )

    def on_mount(self) -> None:
        """Focus the Input so the user can start typing immediately."""
        self.query_one("#palette-input", Input).focus()

    # ------------------------------------------------------------------
    # Option list construction
    # ------------------------------------------------------------------

    @staticmethod
    def _build_options(entries: list[tuple[str, str]]) -> list[Option]:
        """Render *entries* as OptionList rows.

        Each row has a stable ``id`` equal to the command name (without the
        leading slash stripped) so callers can map a selection back to the
        catalog entry without maintaining a separate index.
        """
        if not entries:
            return [Option("[dim]no matching commands[/]", id="__empty__", disabled=True)]
        options: list[Option] = []
        for name, desc in entries:
            label = f"[bold]{name}[/]  [dim]{desc}[/]"
            options.append(Option(label, id=name))
        return options

    def _refresh_list(self, query: str) -> None:
        """Re-filter the catalog and replace the OptionList contents."""
        self._filtered = filter_commands(query, self._catalog)
        option_list = self.query_one("#palette-list", OptionList)
        option_list.clear_options()
        option_list.add_options(self._build_options(self._filtered))
        # Highlight the first row so Enter picks it straight away.
        if self._filtered:
            option_list.highlighted = 0

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "palette-input":
            return
        self._refresh_list(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter from the Input: select whatever's currently highlighted."""
        if event.input.id != "palette-input":
            return
        self.action_select()

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        """Mouse click (or Enter on focused OptionList) picks an entry."""
        option_id = event.option.id
        if option_id and option_id != "__empty__":
            self.dismiss(option_id)

    # ------------------------------------------------------------------
    # Actions (keybindings)
    # ------------------------------------------------------------------

    def action_cursor_down(self) -> None:
        option_list = self.query_one("#palette-list", OptionList)
        option_list.action_cursor_down()

    def action_cursor_up(self) -> None:
        option_list = self.query_one("#palette-list", OptionList)
        option_list.action_cursor_up()

    def action_select(self) -> None:
        """Dismiss with the currently highlighted command, if any."""
        if not self._filtered:
            return
        option_list = self.query_one("#palette-list", OptionList)
        highlighted = option_list.highlighted
        if highlighted is None or highlighted < 0:
            highlighted = 0
        if highlighted >= len(self._filtered):
            return
        name, _ = self._filtered[highlighted]
        self.dismiss(name)

    def action_dismiss_palette(self) -> None:
        self.dismiss(None)
