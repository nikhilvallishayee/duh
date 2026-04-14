"""Tests for renderer adapters (ADR-011)."""

from __future__ import annotations

import json
from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from duh.adapters.renderers import BareRenderer, JsonRenderer, RichRenderer, select_renderer
from duh.ports.renderer import Renderer


# ---------------------------------------------------------------------------
# BareRenderer
# ---------------------------------------------------------------------------


class TestBareRenderer:
    def test_satisfies_renderer_protocol(self):
        r = BareRenderer()
        # All required methods exist
        assert hasattr(r, "render_text_delta")
        assert hasattr(r, "render_tool_use")
        assert hasattr(r, "render_tool_result")
        assert hasattr(r, "render_thinking")
        assert hasattr(r, "render_error")
        assert hasattr(r, "render_permission_request")
        assert hasattr(r, "finish")
        assert hasattr(r, "handle")

    def test_text_delta_writes_to_stdout(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.render_text_delta("hello")
        assert buf.getvalue() == "hello"

    def test_text_delta_sets_had_output(self):
        r = BareRenderer()
        assert r._had_output is False
        with patch("sys.stdout", StringIO()):
            r.render_text_delta("x")
        assert r._had_output is True

    def test_tool_use_writes_to_stderr(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_tool_use("Bash", {"command": "ls"})
        output = buf.getvalue()
        assert "Bash" in output
        assert "command" in output

    def test_tool_result_error_writes_to_stderr(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_tool_result("file not found", is_error=True)
        assert "file not found" in buf.getvalue()

    def test_tool_result_success_silent_without_debug(self):
        r = BareRenderer(debug=False)
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_tool_result("ok", is_error=False)
        assert buf.getvalue() == ""

    def test_tool_result_success_shown_with_debug(self):
        r = BareRenderer(debug=True)
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_tool_result("ok", is_error=False)
        assert "ok" in buf.getvalue()

    def test_thinking_shown_with_debug(self):
        r = BareRenderer(debug=True)
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_thinking("hmm let me think")
        assert "hmm let me think" in buf.getvalue()

    def test_thinking_hidden_without_debug(self):
        r = BareRenderer(debug=False)
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_thinking("hmm let me think")
        assert buf.getvalue() == ""

    def test_error_writes_to_stderr(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_error("something broke")
        assert "something broke" in buf.getvalue()

    def test_finish_prints_newline_if_had_output(self):
        r = BareRenderer()
        r._had_output = True
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.finish()
        assert buf.getvalue() == "\n"

    def test_finish_no_newline_if_no_output(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.finish()
        assert buf.getvalue() == ""

    def test_handle_dispatches_text_delta(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.handle({"type": "text_delta", "text": "hi"})
        assert buf.getvalue() == "hi"

    def test_handle_dispatches_tool_use(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.handle({"type": "tool_use", "name": "Read", "input": {"path": "/tmp"}})
        assert "Read" in buf.getvalue()

    def test_handle_dispatches_error(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.handle({"type": "error", "error": "boom"})
        assert "boom" in buf.getvalue()

    def test_handle_ignores_unknown_events(self):
        r = BareRenderer()
        # Should not raise
        r.handle({"type": "session", "session_id": "abc"})

    def test_permission_request_writes_to_stderr(self):
        r = BareRenderer()
        buf = StringIO()
        with patch("sys.stderr", buf):
            r.render_permission_request("Bash", {"command": "rm -rf /"})
        output = buf.getvalue()
        assert "Bash" in output
        assert "Permission" in output


# ---------------------------------------------------------------------------
# JsonRenderer
# ---------------------------------------------------------------------------


class TestJsonRenderer:
    def test_collects_events(self):
        r = JsonRenderer()
        r.render_text_delta("hello")
        r.render_tool_use("Read", {"path": "/tmp"})
        r.render_error("oops")
        assert len(r._events) == 3

    def test_finish_writes_json_to_stdout(self):
        r = JsonRenderer()
        r.render_text_delta("hi")
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.finish()
        data = json.loads(buf.getvalue())
        assert isinstance(data, list)
        assert len(data) == 1
        assert data[0]["type"] == "text_delta"
        assert data[0]["text"] == "hi"

    def test_handle_dispatches_all_event_types(self):
        r = JsonRenderer()
        r.handle({"type": "text_delta", "text": "x"})
        r.handle({"type": "tool_use", "name": "Y", "input": {}})
        r.handle({"type": "tool_result", "output": "z", "is_error": False})
        r.handle({"type": "thinking_delta", "text": "t"})
        r.handle({"type": "error", "error": "e"})
        assert len(r._events) == 5

    def test_tool_result_recorded(self):
        r = JsonRenderer()
        r.render_tool_result("output text", is_error=True)
        assert r._events[0]["is_error"] is True
        assert r._events[0]["output"] == "output text"

    def test_permission_request_recorded(self):
        r = JsonRenderer()
        r.render_permission_request("Bash", {"cmd": "ls"})
        assert r._events[0]["type"] == "permission_request"
        assert r._events[0]["tool"] == "Bash"


# ---------------------------------------------------------------------------
# select_renderer
# ---------------------------------------------------------------------------


class TestSelectRenderer:
    def test_json_format_returns_json_renderer(self):
        r = select_renderer(output_format="json")
        assert isinstance(r, JsonRenderer)

    def test_text_format_returns_bare_renderer(self):
        r = select_renderer(output_format="text")
        assert isinstance(r, BareRenderer)

    def test_default_returns_bare_renderer(self):
        r = select_renderer()
        assert isinstance(r, BareRenderer)

    def test_debug_passed_to_bare(self):
        r = select_renderer(output_format="text", debug=True)
        assert isinstance(r, BareRenderer)
        assert r.debug is True

    def test_returns_rich_renderer_when_rich_available_and_tty(self):
        """select_renderer returns RichRenderer when rich is importable and stdout is a TTY."""
        import rich as _rich_module  # noqa: F401 — ensures rich is importable

        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = True
            r = select_renderer(output_format="text")
        assert isinstance(r, RichRenderer)

    def test_falls_back_to_bare_when_not_tty(self):
        """When stdout is not a TTY, BareRenderer is returned even if rich is available."""
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.isatty.return_value = False
            r = select_renderer(output_format="text")
        assert isinstance(r, BareRenderer)

    def test_falls_back_to_bare_when_rich_not_importable(self):
        """When rich cannot be imported, BareRenderer is used."""
        import builtins
        real_import = builtins.__import__

        def _block_rich(name, *args, **kwargs):
            if name == "rich":
                raise ImportError("no rich")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=_block_rich):
            with patch("sys.stdout") as mock_stdout:
                mock_stdout.isatty.return_value = True
                r = select_renderer(output_format="text")
        assert isinstance(r, BareRenderer)


# ---------------------------------------------------------------------------
# RichRenderer (Tier 1)
# ---------------------------------------------------------------------------


class TestRichRenderer:
    """Tests for the Tier 1 RichRenderer (ADR-011).

    All methods are tested via a mock console so we don't need a real TTY.
    """

    def _make_renderer(self, debug: bool = False) -> RichRenderer:
        """Build a RichRenderer with patched consoles."""
        r = RichRenderer(debug=debug)
        r._console = MagicMock()
        r._err_console = MagicMock()
        return r

    # -- protocol conformance ------------------------------------------
    def test_has_all_protocol_methods(self):
        r = self._make_renderer()
        for method in (
            "render_text_delta",
            "render_tool_use",
            "render_tool_result",
            "render_thinking",
            "render_error",
            "render_permission_request",
            "finish",
            "handle",
        ):
            assert hasattr(r, method), f"Missing method: {method}"

    # -- text_delta ----------------------------------------------------
    def test_render_text_delta_writes_to_stdout(self):
        r = self._make_renderer()
        buf = StringIO()
        with patch("sys.stdout", buf):
            r.render_text_delta("hello")
        assert buf.getvalue() == "hello"

    def test_render_text_delta_accumulates_buffer(self):
        r = self._make_renderer()
        with patch("sys.stdout", StringIO()):
            r.render_text_delta("foo")
            r.render_text_delta("bar")
        assert r._buf == ["foo", "bar"]

    def test_render_text_delta_sets_had_output(self):
        r = self._make_renderer()
        assert r._had_output is False
        with patch("sys.stdout", StringIO()):
            r.render_text_delta("x")
        assert r._had_output is True

    # -- markdown flush ------------------------------------------------
    def test_flush_markdown_uses_rich_markdown_for_md_content(self):
        r = self._make_renderer()
        r._buf = ["## Header\n", "```python\nprint('hi')\n```"]
        with patch("sys.stdout", StringIO()):
            r._flush_markdown()
        r._console.print.assert_called_once()
        # The argument should be a RichMarkdown instance
        from rich.markdown import Markdown as RichMarkdown
        call_arg = r._console.print.call_args[0][0]
        assert isinstance(call_arg, RichMarkdown)

    def test_flush_markdown_skips_plain_text(self):
        r = self._make_renderer()
        r._buf = ["just plain text without any markdown"]
        with patch("sys.stdout", StringIO()):
            r._flush_markdown()
        r._console.print.assert_not_called()

    def test_flush_markdown_clears_buffer(self):
        r = self._make_renderer()
        r._buf = ["some text"]
        with patch("sys.stdout", StringIO()):
            r._flush_markdown()
        assert r._buf == []

    # -- tool_use (spinner) --------------------------------------------
    def test_render_tool_use_sets_active_tool(self):
        r = self._make_renderer()
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.render_tool_use("Bash", {"command": "ls"})
        assert r._active_tool == "Bash"

    def test_render_tool_use_writes_spinner_to_stderr(self):
        r = self._make_renderer()
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.render_tool_use("Bash", {"command": "ls"})
        # Spinner line contains tool name
        assert "Bash" in stderr_buf.getvalue()

    def test_render_tool_use_prints_styled_header(self):
        r = self._make_renderer()
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.render_tool_use("Read", {"path": "/tmp"})
        r._err_console.print.assert_called_once()

    # -- tool_result (panels) ------------------------------------------
    def test_render_tool_result_error_shows_panel(self):
        r = self._make_renderer()
        r._active_tool = "Bash"
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.render_tool_result("error output", is_error=True)
        r._err_console.print.assert_called_once()
        from rich.panel import Panel
        arg = r._err_console.print.call_args[0][0]
        assert isinstance(arg, Panel)

    def test_render_tool_result_error_clears_spinner(self):
        r = self._make_renderer()
        r._active_tool = "Bash"
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.render_tool_result("oops", is_error=True)
        assert r._active_tool is None
        # ANSI erase line sequence should have been written
        assert "\r" in stderr_buf.getvalue()

    def test_render_tool_result_success_shows_panel(self):
        r = self._make_renderer()
        r._active_tool = "Read"
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.render_tool_result("file contents here", is_error=False)
        from rich.panel import Panel
        # At least one Panel call
        assert any(
            isinstance(call[0][0], Panel)
            for call in r._err_console.print.call_args_list
        )

    def test_render_tool_result_success_clears_active_tool(self):
        r = self._make_renderer()
        r._active_tool = "Read"
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.render_tool_result("ok", is_error=False)
        assert r._active_tool is None

    def test_render_tool_result_debug_shows_full_output(self):
        r = self._make_renderer(debug=True)
        r._active_tool = "Read"
        stderr_buf = StringIO()
        long_output = "first line\n" + "x" * 200
        with patch("sys.stderr", stderr_buf):
            r.render_tool_result(long_output, is_error=False)
        # Should have called print twice: summary + full output
        assert r._err_console.print.call_count >= 2

    # -- thinking ------------------------------------------------------
    def test_render_thinking_hidden_without_debug(self):
        r = self._make_renderer(debug=False)
        r.render_thinking("deep thought")
        r._err_console.print.assert_not_called()

    def test_render_thinking_shown_with_debug(self):
        r = self._make_renderer(debug=True)
        r.render_thinking("deep thought")
        r._err_console.print.assert_called_once()

    # -- error ---------------------------------------------------------
    def test_render_error_shows_panel(self):
        r = self._make_renderer()
        r.render_error("something broke")
        from rich.panel import Panel
        arg = r._err_console.print.call_args[0][0]
        assert isinstance(arg, Panel)

    def test_render_error_clears_active_tool(self):
        r = self._make_renderer()
        r._active_tool = "Bash"
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.render_error("boom")
        assert r._active_tool is None

    # -- permission_request --------------------------------------------
    def test_render_permission_request_shows_panel(self):
        r = self._make_renderer()
        r.render_permission_request("Bash", {"command": "rm -rf /"})
        from rich.panel import Panel
        arg = r._err_console.print.call_args[0][0]
        assert isinstance(arg, Panel)

    # -- finish --------------------------------------------------------
    def test_finish_flushes_markdown(self):
        r = self._make_renderer()
        r._buf = ["## Title\n", "some text"]
        r._had_output = True
        stdout_buf = StringIO()
        with patch("sys.stdout", stdout_buf):
            r.finish()
        # _flush_markdown should have been called: console.print should be called
        # (for the markdown content)
        r._console.print.assert_called_once()

    def test_finish_writes_trailing_newline_if_had_output(self):
        r = self._make_renderer()
        r._had_output = True
        r._buf = []
        stdout_buf = StringIO()
        with patch("sys.stdout", stdout_buf):
            r.finish()
        assert "\n" in stdout_buf.getvalue()

    def test_finish_clears_spinner(self):
        r = self._make_renderer()
        r._active_tool = "Bash"
        r._had_output = False
        r._buf = []
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.finish()
        assert r._active_tool is None
        assert "\r" in stderr_buf.getvalue()

    # -- handle dispatch -----------------------------------------------
    def test_handle_dispatches_text_delta(self):
        r = self._make_renderer()
        stdout_buf = StringIO()
        with patch("sys.stdout", stdout_buf):
            r.handle({"type": "text_delta", "text": "hi"})
        assert stdout_buf.getvalue() == "hi"

    def test_handle_dispatches_tool_use(self):
        r = self._make_renderer()
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.handle({"type": "tool_use", "name": "Read", "input": {"path": "/x"}})
        assert r._active_tool == "Read"

    def test_handle_dispatches_tool_result(self):
        r = self._make_renderer()
        r._active_tool = "Read"
        stderr_buf = StringIO()
        with patch("sys.stderr", stderr_buf):
            r.handle({"type": "tool_result", "output": "data", "is_error": False})
        assert r._active_tool is None

    def test_handle_dispatches_error(self):
        r = self._make_renderer()
        r.handle({"type": "error", "error": "boom"})
        from rich.panel import Panel
        arg = r._err_console.print.call_args[0][0]
        assert isinstance(arg, Panel)

    def test_handle_ignores_unknown_events(self):
        r = self._make_renderer()
        # Should not raise
        r.handle({"type": "session", "session_id": "abc"})
