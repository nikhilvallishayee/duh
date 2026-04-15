"""Tests for output style configuration (ADR-062)."""

from __future__ import annotations

import pytest

from duh.ui.styles import OutputStyle
from duh.cli.parser import build_parser


# ---------------------------------------------------------------------------
# 1. OutputStyle enum
# ---------------------------------------------------------------------------

class TestOutputStyleEnum:
    def test_has_three_values(self):
        assert len(OutputStyle) == 3

    def test_default_value(self):
        assert OutputStyle.DEFAULT.value == "default"

    def test_concise_value(self):
        assert OutputStyle.CONCISE.value == "concise"

    def test_verbose_value(self):
        assert OutputStyle.VERBOSE.value == "verbose"

    def test_is_str_enum(self):
        """OutputStyle members are also strings."""
        assert isinstance(OutputStyle.DEFAULT, str)
        assert OutputStyle.CONCISE == "concise"

    def test_construct_from_string(self):
        assert OutputStyle("default") is OutputStyle.DEFAULT
        assert OutputStyle("concise") is OutputStyle.CONCISE
        assert OutputStyle("verbose") is OutputStyle.VERBOSE

    def test_invalid_value_raises(self):
        with pytest.raises(ValueError):
            OutputStyle("unknown")


# ---------------------------------------------------------------------------
# 2. CLI parser --output-style flag
# ---------------------------------------------------------------------------

class TestOutputStyleParserFlag:
    def test_default_is_default(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.output_style == "default"

    def test_concise(self):
        parser = build_parser()
        args = parser.parse_args(["--output-style", "concise"])
        assert args.output_style == "concise"

    def test_verbose(self):
        parser = build_parser()
        args = parser.parse_args(["--output-style", "verbose"])
        assert args.output_style == "verbose"

    def test_explicit_default(self):
        parser = build_parser()
        args = parser.parse_args(["--output-style", "default"])
        assert args.output_style == "default"

    def test_invalid_rejected(self):
        parser = build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["--output-style", "json"])

    def test_combines_with_other_flags(self):
        parser = build_parser()
        args = parser.parse_args(["--output-style", "verbose", "--debug"])
        assert args.output_style == "verbose"
        assert args.debug is True


# ---------------------------------------------------------------------------
# 3. /style slash command in TUI
# ---------------------------------------------------------------------------

class TestStyleSlashCommand:
    """Test /style via DuhApp._handle_slash.

    We instantiate DuhApp with a minimal mock engine and exercise the
    slash-command handler directly (no Textual event loop needed).
    """

    @pytest.fixture()
    def app(self):
        """Build a DuhApp with a stub engine (no real model calls)."""
        from unittest.mock import MagicMock
        from duh.ui.app import DuhApp

        engine = MagicMock()
        engine._messages = []
        duh_app = DuhApp(engine=engine, model="test-model", session_id="abc123")
        return duh_app

    def test_initial_style_is_default(self, app):
        assert app._output_style is OutputStyle.DEFAULT

    @pytest.mark.asyncio
    async def test_style_show_current(self, app):
        """Bare /style should report current style and return True."""
        # Patch _add_widget so we can capture what was shown
        from unittest.mock import AsyncMock
        app._add_widget = AsyncMock()
        handled = await app._handle_slash("/style")
        assert handled is True
        app._add_widget.assert_called_once()
        widget = app._add_widget.call_args[0][0]
        # Static stores content as name-mangled _Static__content
        content = str(getattr(widget, "_Static__content", ""))
        assert "default" in content.lower()

    @pytest.mark.asyncio
    async def test_style_set_concise(self, app):
        from unittest.mock import AsyncMock
        app._add_widget = AsyncMock()
        handled = await app._handle_slash("/style concise")
        assert handled is True
        assert app._output_style is OutputStyle.CONCISE

    @pytest.mark.asyncio
    async def test_style_set_verbose(self, app):
        from unittest.mock import AsyncMock
        app._add_widget = AsyncMock()
        handled = await app._handle_slash("/style verbose")
        assert handled is True
        assert app._output_style is OutputStyle.VERBOSE

    @pytest.mark.asyncio
    async def test_style_set_default(self, app):
        from unittest.mock import AsyncMock
        app._add_widget = AsyncMock()
        # First switch away from default
        await app._handle_slash("/style concise")
        assert app._output_style is OutputStyle.CONCISE
        # Then switch back
        await app._handle_slash("/style default")
        assert app._output_style is OutputStyle.DEFAULT

    @pytest.mark.asyncio
    async def test_style_invalid_shows_error(self, app):
        from unittest.mock import AsyncMock
        app._add_widget = AsyncMock()
        app._add_error_message = AsyncMock()
        handled = await app._handle_slash("/style fancy")
        assert handled is True
        app._add_error_message.assert_called_once()
        error_text = app._add_error_message.call_args[0][0]
        assert "fancy" in error_text
        # Style should not change
        assert app._output_style is OutputStyle.DEFAULT

    @pytest.mark.asyncio
    async def test_style_in_help_output(self, app):
        from unittest.mock import AsyncMock
        app._add_widget = AsyncMock()
        await app._handle_slash("/help")
        widget = app._add_widget.call_args[0][0]
        content = str(getattr(widget, "_Static__content", ""))
        assert "/style" in content
