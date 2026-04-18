"""Theme management for the D.U.H. Textual TUI (ADR-073 Wave 3 #10).

Ships five themes out of the box:

====================  ==============================  ========================
Theme name            Display name                    Description
====================  ==============================  ========================
``duh-dark``          D.U.H. Dark (default)           Default dark theme.
``duh-light``         D.U.H. Light                    Light background.
``catppuccin-mocha``  Catppuccin Mocha                Soothing pastel palette.
``tokyo-night``       Tokyo Night                     Modern vibrant dark.
``gruvbox``           Gruvbox                         Retro warm tones.
====================  ==============================  ========================

The *colour palette* is driven by Textual's built-in theme registry
(``textual.theme.BUILTIN_THEMES``).  Each D.U.H. theme above maps to one
of Textual's pre-registered themes — we don't ship colour JSON.

The *widget styling overrides* (borders, accents, banner layout) live in
the ``duh/ui/themes/*.tcss`` files, one per theme.  They're loaded on
disk so power users can tweak them without rebuilding D.U.H.

Preference persistence
----------------------
The active theme is stored at ``~/.config/duh/tui_theme.txt`` (one line,
theme name).  On app startup :meth:`DuhApp.on_mount` reads the file and
applies the theme before mounting widgets so the user never sees a flash
of the wrong colours.

Fallback policy
---------------
Any of the following conditions silently falls back to ``duh-dark``:

* CSS file for the requested theme missing on disk
* Requested theme not registered with Textual
* Preference file corrupt or unreadable

Failing loud would break the TUI for a trivial cosmetic issue; we prefer
"works but looks default" over "crashes on startup".
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

if TYPE_CHECKING:
    from textual.app import App

logger = logging.getLogger("duh.ui.theme_manager")


# ---------------------------------------------------------------------------
# Theme registry — display metadata + mapping to Textual builtin theme names
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThemeEntry:
    """Static metadata describing a selectable theme."""

    name: str          # the name users type (/theme <name>) and we pass to app.theme
    display_name: str  # human-friendly label for the picker UI
    description: str   # one-line description shown next to the name
    css_file: str      # filename (relative to duh/ui/themes/) — used as-is
    textual_theme: str  # name of the registered Textual theme to activate


# Canonical theme catalog.  Order is intentional — the default comes first.
# Every entry's ``textual_theme`` must be a name that Textual registers by
# default (see ``textual.theme.BUILTIN_THEMES``).  We don't ship our own
# Theme objects because Textual's built-ins already cover our palette
# choices.
_THEMES: list[ThemeEntry] = [
    ThemeEntry(
        name="duh-dark",
        display_name="D.U.H. Dark",
        description="Default dark theme (recommended)",
        css_file="duh_dark.tcss",
        textual_theme="textual-dark",
    ),
    ThemeEntry(
        name="duh-light",
        display_name="D.U.H. Light",
        description="Light background, dark text",
        css_file="duh_light.tcss",
        textual_theme="textual-light",
    ),
    ThemeEntry(
        name="catppuccin-mocha",
        display_name="Catppuccin Mocha",
        description="Soothing pastel palette (port from OpenCode)",
        css_file="catppuccin_mocha.tcss",
        textual_theme="catppuccin-mocha",
    ),
    ThemeEntry(
        name="tokyo-night",
        display_name="Tokyo Night",
        description="Modern vibrant dark theme",
        css_file="tokyonight.tcss",
        textual_theme="tokyo-night",
    ),
    ThemeEntry(
        name="gruvbox",
        display_name="Gruvbox",
        description="Retro warm dark theme",
        css_file="gruvbox_dark.tcss",
        textual_theme="gruvbox",
    ),
]

DEFAULT_THEME = "duh-dark"


def _themes_dir() -> Path:
    """Return the directory holding the bundled ``*.tcss`` files."""
    return Path(__file__).parent / "themes"


def _preference_path() -> Path:
    """Return the on-disk path for the persisted theme preference.

    Honours ``XDG_CONFIG_HOME`` when set (matches ``duh.config.config_dir``).
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "duh" / "tui_theme.txt"
    return Path.home() / ".config" / "duh" / "tui_theme.txt"


# ---------------------------------------------------------------------------
# ThemeManager
# ---------------------------------------------------------------------------


class ThemeManager:
    """Register, apply, and persist the TUI theme preference.

    The manager is intentionally stateless — all durable state lives on
    the ``App`` instance (``app.theme``) or on disk (the preference file).
    Multiple managers can exist without stepping on each other; they all
    read the same files and the same registered-theme table.
    """

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    def available_themes(self) -> list[tuple[str, str, str]]:
        """Return ``[(name, display_name, description), ...]`` for the picker."""
        return [(t.name, t.display_name, t.description) for t in _THEMES]

    def _lookup(self, name: str) -> ThemeEntry | None:
        for entry in _THEMES:
            if entry.name == name:
                return entry
        return None

    def has_theme(self, name: str) -> bool:
        return self._lookup(name) is not None

    def css_path(self, name: str) -> Path | None:
        """Return the on-disk CSS path for *name*, or ``None`` if unknown.

        Used by tests; the runtime reads the CSS via :meth:`_load_css`.
        """
        entry = self._lookup(name)
        if entry is None:
            return None
        return _themes_dir() / entry.css_file

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def ensure_registered(self, app: "App") -> None:
        """Make every bundled theme available to ``app.theme = <name>``.

        For each :class:`ThemeEntry` we look up the underlying Textual
        theme in ``app.available_themes`` and re-register it under the
        D.U.H. alias.  This means ``app.theme = "duh-dark"`` activates
        the same palette as ``app.theme = "textual-dark"``, but with a
        D.U.H.-branded name.
        """
        for entry in _THEMES:
            if entry.name in app.available_themes:
                continue  # already registered (e.g. on second call)
            base_theme = app.get_theme(entry.textual_theme)
            if base_theme is None:
                # Underlying Textual theme disappeared — fall back to the
                # default dark palette so the registration still succeeds.
                base_theme = app.get_theme("textual-dark")
            if base_theme is None:
                continue  # truly nothing to clone; skip silently
            # Build a clone under the D.U.H. name.  We rebuild the kwargs
            # dict rather than relying on __replace__ because Theme may
            # not be a dataclass in older Textual versions.
            try:
                from textual.theme import Theme as _Theme
                clone = _Theme(
                    name=entry.name,
                    primary=base_theme.primary,
                    secondary=base_theme.secondary,
                    warning=base_theme.warning,
                    error=base_theme.error,
                    success=base_theme.success,
                    accent=base_theme.accent,
                    foreground=base_theme.foreground,
                    background=base_theme.background,
                    surface=base_theme.surface,
                    panel=base_theme.panel,
                    boost=base_theme.boost,
                    dark=base_theme.dark,
                    luminosity_spread=base_theme.luminosity_spread,
                    text_alpha=base_theme.text_alpha,
                    variables=dict(base_theme.variables),
                )
                app.register_theme(clone)
            except Exception as exc:  # noqa: BLE001 — never block startup
                logger.warning(
                    "Failed to register theme %s: %s", entry.name, exc,
                )

    # ------------------------------------------------------------------
    # Apply
    # ------------------------------------------------------------------

    def _load_css(self, entry: ThemeEntry) -> str:
        """Return the contents of *entry*'s CSS file, or ``""`` on failure."""
        path = _themes_dir() / entry.css_file
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("Theme CSS file missing: %s", path)
            return ""
        except OSError as exc:
            logger.warning("Could not read theme CSS %s: %s", path, exc)
            return ""

    def apply_theme(self, app: "App", name: str) -> tuple[bool, str]:
        """Apply theme *name* to *app*.

        Returns ``(ok, message)`` — ``message`` is a human-friendly error
        on failure, or a confirmation on success.

        The caller is responsible for persisting the preference (see
        :meth:`save_preference`); :meth:`apply_theme` never writes to
        disk, so it's safe to call inside :meth:`on_mount`.
        """
        entry = self._lookup(name)
        if entry is None:
            choices = ", ".join(t.name for t in _THEMES)
            return False, (
                f"Unknown theme '{name}'. Available: {choices}"
            )

        # Make sure the theme is registered with Textual before assignment.
        self.ensure_registered(app)
        if entry.name not in app.available_themes:
            return False, (
                f"Theme '{name}' could not be registered with Textual "
                "(base palette unavailable)."
            )

        # Flip the palette.  Textual's validator raises if the theme
        # isn't registered, so we just swallowed that case above.
        try:
            app.theme = entry.name
        except Exception as exc:  # noqa: BLE001
            return False, f"Could not activate theme '{name}': {exc}"

        # Load the widget-style CSS file (optional — failure is not fatal).
        css = self._load_css(entry)
        if css:
            try:
                # ``App.stylesheet`` owns the parsed CSS; adding a source
                # with a stable ``read_from`` location replaces any prior
                # entry with the same location, which is what we want on
                # theme switch.
                css_path = str(_themes_dir() / entry.css_file)
                app.stylesheet.add_source(
                    css,
                    read_from=(css_path, ""),
                    is_default_css=False,
                )
                app.stylesheet.parse()
                app.refresh_css(animate=False)
            except Exception as exc:  # noqa: BLE001 — colours already applied
                logger.warning("Could not load theme CSS for %s: %s", name, exc)

        return True, f"Theme '{name}' applied."

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def save_preference(self, name: str) -> bool:
        """Persist *name* as the preferred theme.

        Returns ``True`` on successful write, ``False`` otherwise.  Failure
        is logged but never raised — a read-only filesystem shouldn't
        break the TUI.
        """
        entry = self._lookup(name)
        if entry is None:
            return False
        path = _preference_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(entry.name + "\n", encoding="utf-8")
            return True
        except OSError as exc:
            logger.warning("Could not save theme preference to %s: %s", path, exc)
            return False

    def load_preference(self) -> str | None:
        """Return the persisted theme name, or ``None`` if not set.

        Corrupt or unreadable preference files are treated as "not set".
        The caller should fall back to :data:`DEFAULT_THEME`.
        """
        path = _preference_path()
        try:
            raw = path.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError):
            return None
        candidate = raw.strip()
        if not candidate:
            return None
        if not self.has_theme(candidate):
            logger.warning(
                "Persisted theme %r is not a known D.U.H. theme; ignoring.",
                candidate,
            )
            return None
        return candidate


# ---------------------------------------------------------------------------
# ThemeSelector — Ctrl+T modal picker
# ---------------------------------------------------------------------------


class ThemeSelector(ModalScreen[str | None]):
    """Modal picker for switching themes (ADR-073 Wave 3 #10).

    Renders each theme as a row showing its name + description, with a
    marker next to the currently active theme.  The row label is rendered
    with a short colour preview so users can skim-compare palettes.

    Returns the selected theme name via :meth:`ModalScreen.dismiss`, or
    ``None`` if the user cancels with ``Esc``.
    """

    DEFAULT_CSS = """
    ThemeSelector {
        align: center middle;
        background: $background 60%;
    }

    #theme-dialog {
        width: 72;
        max-width: 95%;
        height: auto;
        max-height: 24;
        background: $surface;
        border: tall $primary;
        padding: 0 1;
    }

    #theme-title {
        color: $primary;
        text-style: bold;
        padding: 0 1;
    }

    #theme-list {
        height: auto;
        max-height: 16;
        background: $surface;
        border: none;
        margin: 1 0;
    }

    #theme-hint {
        color: $text-muted;
        padding: 0 1;
        text-align: center;
    }
    """

    BINDINGS = [
        Binding("escape", "dismiss_selector", "Cancel", show=False),
        Binding("enter", "select", "Apply", show=False),
        Binding("down", "cursor_down", "Down", show=False),
        Binding("up", "cursor_up", "Up", show=False),
    ]

    def __init__(
        self,
        *,
        manager: ThemeManager | None = None,
        current: str | None = None,
    ) -> None:
        super().__init__()
        self._manager = manager or ThemeManager()
        self._current = current
        self._entries: list[tuple[str, str, str]] = self._manager.available_themes()

    def compose(self) -> ComposeResult:
        with Vertical(id="theme-dialog"):
            yield Static("Select Theme", id="theme-title")
            yield OptionList(
                *self._build_options(),
                id="theme-list",
            )
            yield Static(
                "[dim]↑/↓ navigate   Enter apply   Esc cancel[/]",
                id="theme-hint",
            )

    def on_mount(self) -> None:
        option_list = self.query_one("#theme-list", OptionList)
        # Highlight the current theme if we can find it.
        for i, (name, _display, _desc) in enumerate(self._entries):
            if name == self._current:
                option_list.highlighted = i
                break
        option_list.focus()

    # ------------------------------------------------------------------
    # Option rendering
    # ------------------------------------------------------------------

    def _build_options(self) -> list[Option]:
        options: list[Option] = []
        for name, display, desc in self._entries:
            marker = "[green]*[/]" if name == self._current else " "
            swatch = self._swatch_for(name)
            label = f"{marker} {swatch} [bold]{display}[/]  [dim]{desc}[/]"
            options.append(Option(label, id=name))
        return options

    @staticmethod
    def _swatch_for(name: str) -> str:
        """Return a short coloured glyph preview for *name*.

        Keeps the palette inside the picker regardless of the currently
        active theme, so users can eyeball the difference before picking.
        """
        palettes = {
            "duh-dark":          ["#1e1e2e", "#89b4fa", "#a6e3a1"],
            "duh-light":         ["#eff1f5", "#1e66f5", "#40a02b"],
            "catppuccin-mocha":  ["#1e1e2e", "#cba6f7", "#f5c2e7"],
            "tokyo-night":       ["#1a1b26", "#7aa2f7", "#bb9af7"],
            "gruvbox":           ["#282828", "#fabd2f", "#8ec07c"],
        }
        colors = palettes.get(name, ["#333", "#888", "#ccc"])
        return "".join(f"[on {c}]  [/]" for c in colors)

    # ------------------------------------------------------------------
    # Events + actions
    # ------------------------------------------------------------------

    def on_option_list_option_selected(
        self, event: OptionList.OptionSelected,
    ) -> None:
        if event.option.id:
            self.dismiss(event.option.id)

    def action_select(self) -> None:
        option_list = self.query_one("#theme-list", OptionList)
        highlighted = option_list.highlighted
        if highlighted is None or highlighted < 0:
            highlighted = 0
        if highlighted >= len(self._entries):
            return
        name, _, _ = self._entries[highlighted]
        self.dismiss(name)

    def action_cursor_down(self) -> None:
        self.query_one("#theme-list", OptionList).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one("#theme-list", OptionList).action_cursor_up()

    def action_dismiss_selector(self) -> None:
        self.dismiss(None)
