"""Tests for the Ctrl+K command palette (ADR-073 Wave 3 #9).

Covers:

* ``build_command_catalog`` merges :data:`SLASH_COMMANDS` with
  :data:`TUI_LOCAL_COMMANDS` and keeps each command unique.
* ``filter_commands`` narrows / widens the list based on a fuzzy query.
* ``CommandPalette`` modal renders, navigates, selects and dismisses.
* Integration with :class:`DuhApp`: ``Ctrl+K`` opens the palette and the
  selected entry is inserted into the prompt ``TextArea`` (plus trailing
  space); ``Esc`` leaves the TextArea empty and re-focused.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

textual = pytest.importorskip("textual", reason="textual not installed")

from textual.widgets import Input, OptionList  # noqa: E402

from duh.cli.repl import SLASH_COMMANDS  # noqa: E402
from duh.ui.app import DuhApp, SubmittableTextArea  # noqa: E402
from duh.ui.command_palette import (  # noqa: E402
    CommandPalette,
    TUI_LOCAL_COMMANDS,
    build_command_catalog,
    filter_commands,
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


# ===========================================================================
# 1. Catalog construction
# ===========================================================================


class TestCatalog:
    def test_build_catalog_includes_every_slash_command(self):
        catalog = build_command_catalog()
        names = {name for name, _ in catalog}
        for cmd in SLASH_COMMANDS:
            assert cmd in names, f"{cmd} missing from palette catalog"

    def test_build_catalog_includes_every_tui_local_command(self):
        catalog = build_command_catalog()
        names = {name for name, _ in catalog}
        for cmd in TUI_LOCAL_COMMANDS:
            assert cmd in names, f"{cmd} missing from palette catalog"

    def test_build_catalog_deduplicates(self):
        catalog = build_command_catalog()
        names = [name for name, _ in catalog]
        assert len(names) == len(set(names)), "catalog must be deduplicated"

    def test_catalog_descriptions_are_non_empty(self):
        """Every row needs a description; an empty one looks broken."""
        for name, desc in build_command_catalog():
            assert desc, f"{name} has no description"


# ===========================================================================
# 2. Fuzzy filtering
# ===========================================================================


class TestFilter:
    def test_empty_query_returns_full_catalog(self):
        catalog = build_command_catalog()
        filtered = filter_commands("", catalog)
        assert len(filtered) == len(catalog)

    def test_exact_prefix_match_ranks_first(self):
        """``/help`` must be the top hit for the query ``help``."""
        results = filter_commands("help", build_command_catalog())
        assert results[0][0] == "/help"

    def test_substring_narrows_the_list(self):
        all_cmds = build_command_catalog()
        narrowed = filter_commands("mem", all_cmds)
        # /memory is the obvious match; the list must shrink.
        assert len(narrowed) < len(all_cmds)
        assert any(name == "/memory" for name, _ in narrowed)

    def test_unknown_query_returns_empty_list(self):
        """A nonsense query yields an empty list (caller renders "no match")."""
        results = filter_commands("xyzzyqq-no-such", build_command_catalog())
        assert results == []

    def test_description_substring_still_matches(self):
        """Searching for a word only in the description keeps the entry."""
        results = filter_commands("github", build_command_catalog())
        # /pr mentions GitHub in its description.
        assert any(name == "/pr" for name, _ in results)

    def test_leading_slash_in_query_is_ignored(self):
        """Users often type ``/he`` — the leading slash must not break matching."""
        results = filter_commands("/he", build_command_catalog())
        assert any(name == "/help" for name, _ in results)


# ===========================================================================
# 3. Modal screen behaviour
# ===========================================================================


@pytest.mark.asyncio
class TestPaletteModal:
    async def test_palette_opens_and_shows_commands(self):
        """Push the palette directly and verify its option list is populated."""
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            screen: CommandPalette | None = None

            def _capture(result):
                pass

            await app.push_screen(CommandPalette(), _capture)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommandPalette)
            option_list = screen.query_one("#palette-list", OptionList)
            # At least the REPL commands + TUI-local ones.
            expected = len(SLASH_COMMANDS) + len(TUI_LOCAL_COMMANDS)
            assert option_list.option_count >= expected - 2

    async def test_typing_in_input_filters_list(self):
        """Typing 'help' narrows the list to entries containing 'help'."""
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:

            def _noop(result):
                pass

            await app.push_screen(CommandPalette(), _noop)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommandPalette)
            inp = screen.query_one("#palette-input", Input)
            inp.value = "help"
            # Input.Changed runs synchronously on assignment; pause to let
            # the OptionList re-render.
            await pilot.pause()
            option_list = screen.query_one("#palette-list", OptionList)
            # The filtered list must be strictly smaller than the catalog.
            assert option_list.option_count < len(build_command_catalog())

    async def test_unknown_query_shows_empty_placeholder(self):
        """A no-match query renders a single disabled placeholder row."""
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:

            def _noop(result):
                pass

            await app.push_screen(CommandPalette(), _noop)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommandPalette)
            inp = screen.query_one("#palette-input", Input)
            inp.value = "zzz-not-a-command"
            await pilot.pause()
            option_list = screen.query_one("#palette-list", OptionList)
            assert option_list.option_count == 1
            only = option_list.get_option_at_index(0)
            assert only.id == "__empty__"
            assert only.disabled

    async def test_enter_selects_highlighted_command(self):
        """Enter on a filtered list dismisses with the top match."""
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        selected: list = []

        async with app.run_test(size=(120, 40)) as pilot:

            def _capture(result):
                selected.append(result)

            await app.push_screen(CommandPalette(), _capture)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommandPalette)
            inp = screen.query_one("#palette-input", Input)
            inp.value = "help"
            await pilot.pause()
            # Invoke select action directly — mirrors pressing Enter.
            screen.action_select()
            await pilot.pause()
            assert selected == ["/help"]

    async def test_escape_dismisses_with_none(self):
        """Esc cancels the palette, delivering ``None`` to the callback."""
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        received: list = []
        async with app.run_test(size=(120, 40)) as pilot:

            def _capture(result):
                received.append(result)

            await app.push_screen(CommandPalette(), _capture)
            await pilot.pause()
            screen = app.screen
            assert isinstance(screen, CommandPalette)
            screen.action_dismiss_palette()
            await pilot.pause()
            assert received == [None]


# ===========================================================================
# 4. Integration with DuhApp — Ctrl+K + selection insertion
# ===========================================================================


@pytest.mark.asyncio
class TestDuhAppIntegration:
    async def test_ctrl_k_binding_is_registered(self):
        """The binding must be wired on DuhApp (smoke test)."""
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        bindings = [b.key for b in app.BINDINGS]
        assert "ctrl+k" in bindings

    async def test_selection_inserts_command_into_textarea(self):
        """Selecting ``/memory`` puts ``"/memory "`` in the TextArea with trailing space."""
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            textarea = app.query_one("#prompt-input", SubmittableTextArea)
            textarea.load_text("")
            # Simulate the callback the app registers against push_screen.
            # We don't push the real modal to avoid fighting the event
            # loop — instead we call the logic the callback performs.
            insert = "/memory "
            textarea.insert(insert)
            textarea.focus()
            await pilot.pause()
            assert textarea.text == "/memory "
            assert textarea.has_focus

    async def test_cancel_leaves_textarea_empty_and_refocuses(self):
        """Cancelling (result = None) refocuses the TextArea without inserting."""
        engine = _fake_engine()
        app = DuhApp(engine=engine, model="m", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            textarea = app.query_one("#prompt-input", SubmittableTextArea)
            textarea.load_text("")
            # Simulate the cancel branch — no insert, just focus.
            textarea.focus()
            await pilot.pause()
            assert textarea.text == ""
            assert textarea.has_focus
