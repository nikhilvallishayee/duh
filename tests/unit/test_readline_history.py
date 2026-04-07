"""Tests for REPL readline history persistence and tab completion."""

from __future__ import annotations

import os
import readline
import tempfile
from unittest.mock import patch

import pytest

from duh.cli.repl import (
    HISTORY_FILE,
    MAX_HISTORY,
    SLASH_COMMANDS,
    _SlashCompleter,
    _load_history,
    _save_history,
    _setup_completion,
)


# ---------------------------------------------------------------------------
# History persistence
# ---------------------------------------------------------------------------


class TestLoadHistory:
    def test_load_creates_directory(self, tmp_path):
        """_load_history creates ~/.config/duh if it doesn't exist."""
        hist_dir = str(tmp_path / "config" / "duh")
        hist_file = os.path.join(hist_dir, "repl_history")
        with patch("duh.cli.repl.HISTORY_DIR", hist_dir), \
             patch("duh.cli.repl.HISTORY_FILE", hist_file):
            _load_history()
        assert os.path.isdir(hist_dir)

    def test_load_no_file_no_error(self, tmp_path):
        """_load_history silently handles missing history file."""
        hist_dir = str(tmp_path / "config" / "duh")
        hist_file = os.path.join(hist_dir, "repl_history")
        with patch("duh.cli.repl.HISTORY_DIR", hist_dir), \
             patch("duh.cli.repl.HISTORY_FILE", hist_file):
            # Should not raise
            _load_history()

    def test_load_reads_existing_history(self, tmp_path):
        """_load_history reads entries from an existing history file."""
        hist_dir = str(tmp_path / "config" / "duh")
        hist_file = os.path.join(hist_dir, "repl_history")
        os.makedirs(hist_dir)

        # Clear readline state, write a known history, then load it
        readline.clear_history()
        readline.add_history("previous command")
        readline.write_history_file(hist_file)
        readline.clear_history()

        assert readline.get_current_history_length() == 0
        with patch("duh.cli.repl.HISTORY_DIR", hist_dir), \
             patch("duh.cli.repl.HISTORY_FILE", hist_file):
            _load_history()
        assert readline.get_current_history_length() >= 1
        assert readline.get_history_item(1) == "previous command"

        # Cleanup readline state
        readline.clear_history()


class TestSaveHistory:
    def test_save_creates_directory(self, tmp_path):
        """_save_history creates ~/.config/duh if it doesn't exist."""
        hist_dir = str(tmp_path / "config" / "duh")
        hist_file = os.path.join(hist_dir, "repl_history")
        readline.clear_history()
        with patch("duh.cli.repl.HISTORY_DIR", hist_dir), \
             patch("duh.cli.repl.HISTORY_FILE", hist_file):
            _save_history()
        assert os.path.isdir(hist_dir)
        assert os.path.isfile(hist_file)
        readline.clear_history()

    def test_save_writes_history_file(self, tmp_path):
        """_save_history persists current readline history to disk."""
        hist_dir = str(tmp_path / "config" / "duh")
        hist_file = os.path.join(hist_dir, "repl_history")
        os.makedirs(hist_dir)

        readline.clear_history()
        readline.add_history("hello world")
        readline.add_history("/model gpt-4o")

        with patch("duh.cli.repl.HISTORY_DIR", hist_dir), \
             patch("duh.cli.repl.HISTORY_FILE", hist_file):
            _save_history()

        assert os.path.isfile(hist_file)
        # Read it back
        readline.clear_history()
        readline.read_history_file(hist_file)
        assert readline.get_current_history_length() == 2
        assert readline.get_history_item(1) == "hello world"
        assert readline.get_history_item(2) == "/model gpt-4o"

        readline.clear_history()

    def test_save_respects_max_history(self, tmp_path):
        """_save_history truncates to MAX_HISTORY entries."""
        hist_dir = str(tmp_path / "config" / "duh")
        hist_file = os.path.join(hist_dir, "repl_history")
        os.makedirs(hist_dir)

        readline.clear_history()
        # Add more than MAX_HISTORY entries
        for i in range(MAX_HISTORY + 50):
            readline.add_history(f"line {i}")

        with patch("duh.cli.repl.HISTORY_DIR", hist_dir), \
             patch("duh.cli.repl.HISTORY_FILE", hist_file):
            _save_history()

        # Read back and check count
        readline.clear_history()
        readline.read_history_file(hist_file)
        assert readline.get_current_history_length() <= MAX_HISTORY

        readline.clear_history()


# ---------------------------------------------------------------------------
# Tab completion
# ---------------------------------------------------------------------------


class TestSlashCompleter:
    def test_complete_slash_prefix(self):
        """Typing '/' matches all commands."""
        completer = _SlashCompleter(list(SLASH_COMMANDS.keys()))
        result = completer.complete("/", 0)
        assert result is not None
        assert result.startswith("/")

    def test_complete_specific_prefix(self):
        """Typing '/co' matches /cost, /compact, (and any other /co* commands)."""
        completer = _SlashCompleter(list(SLASH_COMMANDS.keys()))
        matches = []
        state = 0
        while True:
            m = completer.complete("/co", state)
            if m is None:
                break
            matches.append(m)
            state += 1
        assert "/cost" in matches
        assert "/compact" in matches
        # /help should NOT be in matches
        assert "/help" not in matches

    def test_complete_exact_match(self):
        """Typing '/exit' returns '/exit' at state 0, None at state 1."""
        completer = _SlashCompleter(list(SLASH_COMMANDS.keys()))
        assert completer.complete("/exit", 0) == "/exit"
        assert completer.complete("/exit", 1) is None

    def test_complete_no_slash_returns_none(self):
        """Text without '/' prefix returns no completions."""
        completer = _SlashCompleter(list(SLASH_COMMANDS.keys()))
        assert completer.complete("hello", 0) is None

    def test_complete_nonexistent_prefix(self):
        """Prefix that matches nothing returns None."""
        completer = _SlashCompleter(list(SLASH_COMMANDS.keys()))
        assert completer.complete("/zzz", 0) is None


class TestSetupCompletion:
    def test_setup_sets_completer(self):
        """_setup_completion installs a completer function on readline."""
        old_completer = readline.get_completer()
        try:
            _setup_completion()
            completer = readline.get_completer()
            assert completer is not None
            # Verify it can complete slash commands
            result = completer("/he", 0)
            assert result == "/help"
        finally:
            readline.set_completer(old_completer)
