"""Tests for the TUI theme system (ADR-073 Wave 3 #10).

Covers:

* ``ThemeManager.available_themes`` returns the five bundled themes.
* ``ThemeManager.apply_theme`` flips the Textual palette and loads the
  CSS file; unknown names return a descriptive error.
* Persistence — ``save_preference`` writes a parseable file,
  ``load_preference`` round-trips the value and tolerates corrupt files.
* ``DuhApp.on_mount`` reads the preference on startup and applies it.
* ``/theme`` slash command dispatches locally (TUI-only, never through
  the shared dispatcher).
* Fallback — a missing CSS file does not break ``apply_theme`` (the
  palette still swaps, just without widget overrides).
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

textual = pytest.importorskip("textual", reason="textual not installed")

from duh.ui.app import DuhApp  # noqa: E402
from duh.ui.theme_manager import (  # noqa: E402
    DEFAULT_THEME,
    ThemeManager,
    ThemeSelector,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_engine() -> MagicMock:
    async def _run(_prompt: str):
        if False:
            yield {}  # pragma: no cover

    engine = MagicMock()
    engine.run = _run
    engine.total_input_tokens = 0
    engine.total_output_tokens = 0
    engine.session_id = "sid-test"
    engine._messages = []
    engine._session_store = None
    return engine


@pytest.fixture
def isolated_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect ``~/.config/duh`` to a tmp dir for preference-file tests.

    The :class:`ThemeManager` honours ``XDG_CONFIG_HOME`` when present;
    setting it here keeps the test hermetic without touching the user's
    real config.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    return tmp_path / "duh"


# ===========================================================================
# 1. Catalog
# ===========================================================================


class TestCatalog:
    def test_available_themes_returns_five(self):
        themes = ThemeManager().available_themes()
        assert len(themes) == 5

    def test_default_theme_present(self):
        names = {n for n, _, _ in ThemeManager().available_themes()}
        assert DEFAULT_THEME in names
        # Required set per the ADR.
        for required in (
            "duh-dark",
            "duh-light",
            "catppuccin-mocha",
            "tokyo-night",
            "gruvbox",
        ):
            assert required in names, f"{required} missing from catalog"

    def test_every_theme_has_a_css_file_on_disk(self):
        """Fallback policy requires a real file for every entry."""
        mgr = ThemeManager()
        for name, _, _ in mgr.available_themes():
            path = mgr.css_path(name)
            assert path is not None
            assert path.exists(), f"missing CSS file for {name}: {path}"

    def test_unknown_theme_has_no_css_path(self):
        assert ThemeManager().css_path("not-a-real-theme") is None


# ===========================================================================
# 2. apply_theme
# ===========================================================================


@pytest.mark.asyncio
class TestApplyTheme:
    async def test_apply_theme_activates_palette(self):
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        mgr = ThemeManager()
        async with app.run_test(size=(120, 40)):
            mgr.ensure_registered(app)
            ok, msg = mgr.apply_theme(app, "catppuccin-mocha")
            assert ok, msg
            assert app.theme == "catppuccin-mocha"

    async def test_apply_unknown_theme_returns_error(self):
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        mgr = ThemeManager()
        async with app.run_test(size=(120, 40)):
            ok, msg = mgr.apply_theme(app, "definitely-not-a-theme")
            assert not ok
            assert "Unknown theme" in msg

    async def test_apply_gruvbox_switches_from_default(self):
        """Switching themes changes ``app.theme`` away from the initial value."""
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        mgr = ThemeManager()
        async with app.run_test(size=(120, 40)):
            mgr.ensure_registered(app)
            initial = app.theme
            ok, _ = mgr.apply_theme(app, "gruvbox")
            assert ok
            assert app.theme == "gruvbox"
            assert app.theme != initial or initial == "gruvbox"

    async def test_apply_with_missing_css_file_still_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ):
        """If a CSS file disappears at runtime, the palette still swaps.

        The spec requires graceful fallback: "Themes must not break
        existing CSS — fall back to current dark theme if theme file
        missing".  We simulate by redirecting :func:`_themes_dir` to an
        empty tmp directory.
        """
        import duh.ui.theme_manager as tm

        monkeypatch.setattr(tm, "_themes_dir", lambda: tmp_path)
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        mgr = ThemeManager()
        async with app.run_test(size=(120, 40)):
            mgr.ensure_registered(app)
            ok, _ = mgr.apply_theme(app, "duh-dark")
            assert ok, "missing CSS must not break apply_theme"
            assert app.theme == "duh-dark"


# ===========================================================================
# 3. Persistence
# ===========================================================================


class TestPersistence:
    def test_save_preference_writes_file(self, isolated_config: Path):
        mgr = ThemeManager()
        assert mgr.save_preference("tokyo-night") is True
        pref_file = isolated_config / "tui_theme.txt"
        assert pref_file.exists()
        assert pref_file.read_text().strip() == "tokyo-night"

    def test_load_preference_roundtrip(self, isolated_config: Path):
        mgr = ThemeManager()
        mgr.save_preference("gruvbox")
        assert mgr.load_preference() == "gruvbox"

    def test_load_preference_returns_none_when_unset(self, isolated_config: Path):
        mgr = ThemeManager()
        assert mgr.load_preference() is None

    def test_save_preference_rejects_unknown_theme(self, isolated_config: Path):
        mgr = ThemeManager()
        assert mgr.save_preference("not-a-theme") is False
        pref_file = isolated_config / "tui_theme.txt"
        assert not pref_file.exists()

    def test_load_preference_ignores_corrupt_file(self, isolated_config: Path):
        """A preference file with an invalid theme name loads as ``None``."""
        pref_dir = isolated_config
        pref_dir.mkdir(parents=True, exist_ok=True)
        (pref_dir / "tui_theme.txt").write_text("not-a-real-theme\n")
        assert ThemeManager().load_preference() is None


# ===========================================================================
# 4. /theme slash command + startup application
# ===========================================================================


@pytest.mark.asyncio
class TestSlashIntegration:
    async def test_theme_slash_lists_themes(self):
        """``/theme`` with no argument lists every theme in the log."""
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            log = app.query_one("#message-log")
            before = len(list(log.children))
            handled = await app._handle_slash("/theme")
            await pilot.pause()
            assert handled is True
            after = len(list(log.children))
            assert after > before

    async def test_theme_slash_switches_and_persists(
        self, isolated_config: Path,
    ):
        """``/theme gruvbox`` switches + writes the preference file."""
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            handled = await app._handle_slash("/theme gruvbox")
            await pilot.pause()
            assert handled is True
            assert app.theme == "gruvbox"
            pref_file = isolated_config / "tui_theme.txt"
            assert pref_file.exists()
            assert pref_file.read_text().strip() == "gruvbox"

    async def test_theme_slash_rejects_unknown(self):
        """Invalid theme names surface an error but do not change ``app.theme``."""
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            initial = app.theme
            handled = await app._handle_slash("/theme bogus-bogus")
            await pilot.pause()
            assert handled is True
            # Unknown name: app.theme unchanged.
            assert app.theme == initial

    async def test_startup_applies_saved_preference(
        self, isolated_config: Path,
    ):
        """on_mount should read the preference and activate that theme."""
        # Persist a preference before launching the app.
        ThemeManager().save_preference("tokyo-night")
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.pause()
            assert app.theme == "tokyo-night"


# ===========================================================================
# 5. ThemeSelector modal
# ===========================================================================


@pytest.mark.asyncio
class TestThemeSelectorModal:
    async def test_selector_opens_with_all_themes(self):
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            mgr = ThemeManager()
            mgr.ensure_registered(app)
            received: list = []

            def _cap(result):
                received.append(result)

            await app.push_screen(ThemeSelector(manager=mgr, current=app.theme), _cap)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ThemeSelector)
            from textual.widgets import OptionList
            option_list = screen.query_one("#theme-list", OptionList)
            assert option_list.option_count == 5

    async def test_selector_cancel_returns_none(self):
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        received: list = []
        async with app.run_test(size=(120, 40)) as pilot:

            def _cap(result):
                received.append(result)

            await app.push_screen(ThemeSelector(), _cap)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, ThemeSelector)
            screen.action_dismiss_selector()
            await pilot.pause()
            assert received == [None]
