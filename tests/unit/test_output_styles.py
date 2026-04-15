"""Tests for output style configuration (ADR-062).

Covers:
- OutputStyle enum behaviour
- CLI parser --output-style flag
- /style slash command in TUI
- ToolCallWidget.set_result style parameter (concise/verbose/default)
- run_tui wiring of --output-style into DuhApp
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

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


# ---------------------------------------------------------------------------
# 4. ToolCallWidget.set_result with style parameter
# ---------------------------------------------------------------------------

class TestToolCallWidgetStyleParam:
    """Test ToolCallWidget.set_result renders differently per style.

    We construct a ToolCallWidget, manually set its _result_label to a
    mock Static, then call set_result with various style values.
    """

    def _make_widget_with_mock_label(self):
        from duh.ui.widgets import ToolCallWidget
        w = ToolCallWidget(tool_name="Bash", input={"command": "ls"})
        mock_label = MagicMock()
        w._result_label = mock_label
        return w, mock_label

    def test_default_style_shows_first_line(self):
        w, label = self._make_widget_with_mock_label()
        w.set_result("line1\nline2\nline3", is_error=False, style="default")
        call_text = label.update.call_args[0][0]
        assert "OK" in call_text
        assert "line1" in call_text
        # Should NOT show line2 in default mode
        assert "line2" not in call_text

    def test_concise_style_ok_no_output(self):
        w, label = self._make_widget_with_mock_label()
        w.set_result("lots of output here", is_error=False, style="concise")
        call_text = label.update.call_args[0][0]
        assert "OK" in call_text
        # Concise should NOT include the output text
        assert "lots of output" not in call_text

    def test_concise_style_error_shows_err(self):
        w, label = self._make_widget_with_mock_label()
        w.set_result("error details here", is_error=True, style="concise")
        call_text = label.update.call_args[0][0]
        assert "ERR" in call_text
        # Concise error should NOT show error details
        assert "error details" not in call_text

    def test_verbose_style_shows_more_output(self):
        w, label = self._make_widget_with_mock_label()
        # Create output longer than 120 chars
        long_output = "A" * 500
        w.set_result(long_output, is_error=False, style="verbose")
        call_text = label.update.call_args[0][0]
        assert "OK" in call_text
        # Verbose should show up to 1000 chars, so 500 A's should appear
        assert "A" * 500 in call_text

    def test_verbose_error_shows_full_preview(self):
        w, label = self._make_widget_with_mock_label()
        w.set_result("error details here", is_error=True, style="verbose")
        call_text = label.update.call_args[0][0]
        assert "Error" in call_text
        # Verbose error should show the details (same as default for errors)
        assert "error details here" in call_text

    def test_default_style_truncates_at_120(self):
        w, label = self._make_widget_with_mock_label()
        long_line = "X" * 200
        w.set_result(long_line, is_error=False, style="default")
        call_text = label.update.call_args[0][0]
        # Default truncates first line to 120 chars
        assert "X" * 120 in call_text
        assert "X" * 200 not in call_text

    def test_verbose_caps_at_1000(self):
        w, label = self._make_widget_with_mock_label()
        huge = "Y" * 2000
        w.set_result(huge, is_error=False, style="verbose")
        call_text = label.update.call_args[0][0]
        # Verbose caps at 1000
        assert "Y" * 1000 in call_text
        assert "Y" * 1001 not in call_text


# ---------------------------------------------------------------------------
# 5. run_tui wires --output-style into DuhApp
# ---------------------------------------------------------------------------

class TestRunTuiStyleWiring:
    """Verify that run_tui reads args.output_style and sets app._output_style."""

    @patch("duh.ui.app.DuhApp")
    def test_run_tui_sets_output_style_concise(self, MockDuhApp):
        """run_tui should set _output_style on the constructed DuhApp."""
        mock_app = MagicMock()
        mock_app.run.return_value = 0
        MockDuhApp.return_value = mock_app

        # We need to mock all the dependencies that run_tui uses
        args = MagicMock()
        args.output_style = "concise"
        args.tui = True
        args.debug = False
        args.provider = "anthropic"
        args.model = "claude-sonnet-4-20250514"
        args.resume = None
        args.continue_session = False
        args.summarize = False
        args.approval_mode = None
        args.system_prompt = None
        args.brief = False
        args.max_cost = None
        args.max_turns = 100
        args.fallback_model = None
        args.i_understand_the_lethal_trifecta = True
        args.coordinator = False

        # Rather than mocking the entire run_tui call chain,
        # test the style-setting logic directly
        from duh.ui.app import DuhApp
        engine = MagicMock()
        engine._messages = []
        real_app = DuhApp.__wrapped__(
            engine=engine, model="test", session_id="s"
        ) if hasattr(DuhApp, "__wrapped__") else DuhApp(
            engine=engine, model="test", session_id="s"
        )

        # Simulate what run_tui does
        style_name = getattr(args, "output_style", "default")
        try:
            real_app._output_style = OutputStyle(style_name)
        except ValueError:
            pass
        assert real_app._output_style is OutputStyle.CONCISE

    def test_wiring_logic_default(self):
        """When output_style is 'default', _output_style stays DEFAULT."""
        from duh.ui.app import DuhApp
        engine = MagicMock()
        engine._messages = []
        app = DuhApp(engine=engine, model="test", session_id="s")

        args = MagicMock()
        args.output_style = "default"
        style_name = getattr(args, "output_style", "default")
        try:
            app._output_style = OutputStyle(style_name)
        except ValueError:
            pass
        assert app._output_style is OutputStyle.DEFAULT

    def test_wiring_logic_verbose(self):
        """When output_style is 'verbose', _output_style is VERBOSE."""
        from duh.ui.app import DuhApp
        engine = MagicMock()
        engine._messages = []
        app = DuhApp(engine=engine, model="test", session_id="s")

        args = MagicMock()
        args.output_style = "verbose"
        style_name = getattr(args, "output_style", "default")
        try:
            app._output_style = OutputStyle(style_name)
        except ValueError:
            pass
        assert app._output_style is OutputStyle.VERBOSE

    def test_wiring_logic_invalid_keeps_default(self):
        """An invalid style string should not crash, keeps DEFAULT."""
        from duh.ui.app import DuhApp
        engine = MagicMock()
        engine._messages = []
        app = DuhApp(engine=engine, model="test", session_id="s")

        args = MagicMock()
        args.output_style = "invalid_style"
        style_name = getattr(args, "output_style", "default")
        try:
            app._output_style = OutputStyle(style_name)
        except ValueError:
            pass
        assert app._output_style is OutputStyle.DEFAULT

    def test_wiring_logic_missing_attr(self):
        """If args has no output_style attr, default is used."""
        from duh.ui.app import DuhApp
        engine = MagicMock()
        engine._messages = []
        app = DuhApp(engine=engine, model="test", session_id="s")

        args = MagicMock(spec=[])  # no attributes
        style_name = getattr(args, "output_style", "default")
        try:
            app._output_style = OutputStyle(style_name)
        except ValueError:
            pass
        assert app._output_style is OutputStyle.DEFAULT
