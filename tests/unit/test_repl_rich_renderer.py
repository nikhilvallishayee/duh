"""Tests for the REPL-level _RichRenderer and _PlainRenderer (ADR-011 Tier 1).

These tests verify the Tier 1 features implemented in duh/cli/repl.py:
- Markdown rendering with Rich (flush_response)
- Progress spinners during tool execution (tool_use / tool_result)
- Collapsible tool result panels (always-shown success panel)
- Enhanced status bar with token counts and cost (status_bar + update_stats)
- update_stats no-op on _PlainRenderer
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

# Import the internal renderer classes.  They are not public API but are the
# units under test for this ADR.
from duh.cli.repl import _PlainRenderer, _RichRenderer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_rich(debug: bool = False) -> _RichRenderer:
    """Return a _RichRenderer with mock consoles so no real TTY is needed."""
    r = _RichRenderer(debug=debug)
    r._console = MagicMock()
    r._err_console = MagicMock()
    return r


# ---------------------------------------------------------------------------
# _PlainRenderer — update_stats no-op
# ---------------------------------------------------------------------------


class TestPlainRendererUpdateStats:
    def test_update_stats_is_a_no_op(self):
        """_PlainRenderer.update_stats must exist and not raise."""
        r = _PlainRenderer()
        # Should not raise, should not do anything observable.
        r.update_stats(input_tokens=100, output_tokens=50, cost=0.001)

    def test_status_bar_remains_no_op(self):
        r = _PlainRenderer()
        stdout_buf = StringIO()
        stderr_buf = StringIO()
        with patch("sys.stdout", stdout_buf), patch("sys.stderr", stderr_buf):
            r.status_bar("claude-sonnet-4-6", 3)
        assert stdout_buf.getvalue() == ""
        assert stderr_buf.getvalue() == ""


# ---------------------------------------------------------------------------
# _RichRenderer — initialization
# ---------------------------------------------------------------------------


class TestRichRendererInit:
    def test_initial_token_counts_are_zero(self):
        r = _make_rich()
        assert r._input_tokens == 0
        assert r._output_tokens == 0
        assert r._cost == 0.0

    def test_active_tool_starts_as_none(self):
        r = _make_rich()
        assert r._active_tool is None

    def test_buf_starts_empty(self):
        r = _make_rich()
        assert r._buf == []


# ---------------------------------------------------------------------------
# _RichRenderer — text streaming and markdown flush
# ---------------------------------------------------------------------------


class TestRichRendererTextAndMarkdown:
    def test_text_delta_streams_to_stdout(self):
        r = _make_rich()
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.text_delta("hello ")
            r.text_delta("world")
        assert buf.getvalue() == "hello world"

    def test_text_delta_accumulates_buffer(self):
        r = _make_rich()
        with patch("sys.stdout", StringIO()):
            r.text_delta("foo")
            r.text_delta("bar")
        assert r._buf == ["foo", "bar"]

    def test_flush_response_renders_markdown(self):
        r = _make_rich()
        r._buf = ["## Header\n\nSome text\n"]
        with patch("sys.stdout", StringIO()):
            r.flush_response()
        r._console.print.assert_called_once()

    def test_flush_response_renders_fenced_code(self):
        r = _make_rich()
        r._buf = ["```python\nprint('hi')\n```\n"]
        with patch("sys.stdout", StringIO()):
            r.flush_response()
        r._console.print.assert_called_once()
        # The arg should be a RichMarkdown instance (contains syntax-highlighted code)
        from rich.markdown import Markdown as RichMarkdown
        arg = r._console.print.call_args[0][0]
        assert isinstance(arg, RichMarkdown)

    def test_flush_response_skips_plain_text(self):
        r = _make_rich()
        r._buf = ["just plain text with no markdown constructs"]
        with patch("sys.stdout", StringIO()):
            r.flush_response()
        r._console.print.assert_not_called()

    def test_flush_response_clears_buffer(self):
        r = _make_rich()
        r._buf = ["**bold**"]
        with patch("sys.stdout", StringIO()):
            r.flush_response()
        assert r._buf == []

    def test_flush_response_is_no_op_on_empty(self):
        r = _make_rich()
        r._buf = []
        with patch("sys.stdout", StringIO()):
            r.flush_response()
        r._console.print.assert_not_called()


# ---------------------------------------------------------------------------
# _RichRenderer — tool_use spinner
# ---------------------------------------------------------------------------


class TestRichRendererSpinner:
    def test_tool_use_sets_active_tool(self):
        r = _make_rich()
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.tool_use("Bash", {"command": "ls"})
        assert r._active_tool == "Bash"

    def test_tool_use_writes_spinner_to_stderr(self):
        r = _make_rich()
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.tool_use("Bash", {"command": "ls"})
        assert "Bash" in stderr_buf.getvalue()

    def test_tool_use_prints_styled_header(self):
        r = _make_rich()
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.tool_use("Read", {"path": "/tmp"})
        r._err_console.print.assert_called_once()

    def test_tool_result_clears_spinner(self):
        r = _make_rich()
        r._active_tool = "Bash"
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.tool_result("output", is_error=False)
        assert r._active_tool is None
        # ANSI clear-line sequence
        assert "\r" in stderr_buf.getvalue()

    def test_turn_end_clears_leftover_spinner(self):
        r = _make_rich()
        r._active_tool = "Bash"
        stderr_buf = StringIO()
        stdout_buf = StringIO()
        with patch("sys.stderr", stderr_buf), patch("sys.stdout", stdout_buf):
            r.turn_end()
        assert r._active_tool is None
        assert "\r" in stderr_buf.getvalue()

    def test_error_clears_spinner(self):
        r = _make_rich()
        r._active_tool = "Bash"
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.error("something broke")
        assert r._active_tool is None

    def test_error_no_spinner_to_clear(self):
        """error() should not write ANSI if no tool is active."""
        r = _make_rich()
        r._active_tool = None
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.error("boom")
        assert "\r" not in stderr_buf.getvalue()


# ---------------------------------------------------------------------------
# _RichRenderer — tool_result panels
# ---------------------------------------------------------------------------


class TestRichRendererToolResultPanels:
    def test_error_result_shows_panel_with_red_border(self):
        r = _make_rich()
        r._active_tool = "Bash"
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.tool_result("command not found", is_error=True)
        from rich.panel import Panel
        arg = r._err_console.print.call_args[0][0]
        assert isinstance(arg, Panel)
        assert arg.border_style == "tool.err"

    def test_success_result_shows_compact_panel(self):
        r = _make_rich()
        r._active_tool = "Read"
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.tool_result("file contents", is_error=False)
        from rich.panel import Panel
        assert any(
            isinstance(call[0][0], Panel)
            for call in r._err_console.print.call_args_list
        )

    def test_success_panel_shows_first_line_summary(self):
        r = _make_rich()
        r._active_tool = "Read"
        stderr_buf = StringIO()
        output = "first line of output\nsecond line\nthird line"
        with patch("sys.stderr", stderr_buf):
            r.tool_result(output, is_error=False)
        # The panel text should contain the first line summary
        from rich.text import Text
        call_args = r._err_console.print.call_args_list
        # Find any Text arg containing the first line
        found = False
        for call in call_args:
            for arg in call[0]:
                if isinstance(arg, Text) and "first line" in str(arg):
                    found = True
            # Also check panel contents
            from rich.panel import Panel
            for arg in call[0]:
                if isinstance(arg, Panel):
                    found = True  # summary panel was shown
        assert found

    def test_success_empty_output_shows_placeholder(self):
        r = _make_rich()
        r._active_tool = "Read"
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.tool_result("", is_error=False)
        # Should still show a panel (not crash on empty)
        r._err_console.print.assert_called()

    def test_verbose_style_shows_more_output_in_single_panel(self):
        """ADR-073 Wave 2: output volume is driven by OutputStyle, not the
        --debug flag.  Under VERBOSE the success panel shows the full (capped
        at 2000 chars) output in a single Panel — no separate "full output"
        Panel is emitted any more."""
        from duh.ui.styles import OutputStyle

        r = _make_rich(debug=True)
        r.output_style = OutputStyle.VERBOSE
        r._active_tool = "Read"
        stderr_buf = StringIO()
        long_output = "first line\n" + "x" * 300
        with patch("sys.stderr", stderr_buf):
            r.tool_result(long_output, is_error=False)
        # Exactly one Panel — the shared OutputTruncationPolicy replaced the
        # legacy debug-only "full output" panel.
        assert r._err_console.print.call_count == 1
        panel = r._err_console.print.call_args[0][0]
        body = panel.renderable
        text = body if isinstance(body, str) else body.plain
        # Verbose budget (2000 chars) easily fits our 311-char payload.
        assert "first line" in text
        assert "x" * 300 in text

    def test_default_style_shows_only_summary_panel(self):
        """DEFAULT is the single-Panel summary path, unchanged by debug."""
        r = _make_rich(debug=False)
        r._active_tool = "Read"
        stderr_buf = StringIO()
        long_output = "first line\n" + "x" * 300
        with patch("sys.stderr", stderr_buf):
            r.tool_result(long_output, is_error=False)
        assert r._err_console.print.call_count == 1


# ---------------------------------------------------------------------------
# _RichRenderer — update_stats and enhanced status bar
# ---------------------------------------------------------------------------


class TestRichRendererStatusBar:
    def test_update_stats_stores_values(self):
        r = _make_rich()
        r.update_stats(input_tokens=1000, output_tokens=500, cost=0.0123)
        assert r._input_tokens == 1000
        assert r._output_tokens == 500
        assert r._cost == pytest.approx(0.0123)

    def test_status_bar_shows_model_and_turn(self):
        r = _make_rich()
        r.status_bar("claude-sonnet-4-6", 3)
        r._err_console.print.assert_called_once()
        from rich.text import Text
        arg = r._err_console.print.call_args[0][0]
        assert isinstance(arg, Text)
        text_str = str(arg)
        assert "claude-sonnet-4-6" in text_str
        assert "3" in text_str

    def test_status_bar_without_tokens_omits_token_info(self):
        r = _make_rich()
        r.status_bar("gpt-4o", 1)
        from rich.text import Text
        arg = r._err_console.print.call_args[0][0]
        text_str = str(arg)
        # No token info when counts are zero
        assert "in=" not in text_str
        assert "out=" not in text_str

    def test_status_bar_with_tokens_shows_counts(self):
        r = _make_rich()
        r.update_stats(input_tokens=2000, output_tokens=800, cost=0.0)
        r.status_bar("claude-sonnet-4-6", 2)
        from rich.text import Text
        arg = r._err_console.print.call_args[0][0]
        text_str = str(arg)
        assert "in=" in text_str
        assert "out=" in text_str
        assert "2,000" in text_str
        assert "800" in text_str

    def test_status_bar_with_cost_shows_dollar_amount(self):
        r = _make_rich()
        r.update_stats(input_tokens=100, output_tokens=50, cost=0.0025)
        r.status_bar("claude-sonnet-4-6", 1)
        from rich.text import Text
        arg = r._err_console.print.call_args[0][0]
        text_str = str(arg)
        assert "$" in text_str

    def test_status_bar_zero_cost_omits_dollar(self):
        r = _make_rich()
        r.update_stats(input_tokens=100, output_tokens=50, cost=0.0)
        r.status_bar("claude-sonnet-4-6", 1)
        from rich.text import Text
        arg = r._err_console.print.call_args[0][0]
        text_str = str(arg)
        assert "$" not in text_str


# ---------------------------------------------------------------------------
# _RichRenderer — banner and error
# ---------------------------------------------------------------------------


class TestRichRendererBannerAndError:
    def test_banner_shows_panel_with_model(self):
        r = _make_rich()
        r.banner("claude-sonnet-4-6")
        from rich.panel import Panel
        arg = r._console.print.call_args_list[0][0][0]
        assert isinstance(arg, Panel)

    def test_error_shows_panel(self):
        r = _make_rich()
        r.error("something went wrong")
        from rich.panel import Panel
        arg = r._err_console.print.call_args[0][0]
        assert isinstance(arg, Panel)
