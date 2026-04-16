"""Tests for the extracted REPL renderer module (issue #26).

These tests verify that :class:`PlainRenderer` and :class:`RichRenderer` from
:mod:`duh.cli.repl_renderers` handle the streaming event shapes used by the
REPL (``text_delta``, ``thinking_delta``, ``tool_use``, ``tool_result``,
``error``) and that :mod:`duh.cli.repl` still re-exports them under their
legacy underscore-prefixed names.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest

from duh.cli.repl_renderers import (
    HAS_RICH,
    PROMPT,
    PlainRenderer,
    RichRenderer,
)


# ---------------------------------------------------------------------------
# PlainRenderer
# ---------------------------------------------------------------------------


class TestPlainRenderer:
    def test_text_delta_writes_plain_text_to_stdout(self, capsys):
        r = PlainRenderer()
        r.text_delta("hello world")
        out = capsys.readouterr().out
        assert out == "hello world"
        # No ANSI styling applied to streamed text.
        assert "\033[" not in out

    def test_tool_use_and_tool_result_write_to_stderr(self, capsys):
        r = PlainRenderer(debug=True)
        r.tool_use("Bash", {"cmd": "ls"})
        r.tool_result("output-line", is_error=False)
        err = capsys.readouterr().err
        assert "> Bash" in err
        assert "cmd='ls'" in err
        assert "< output-line" in err

    def test_tool_result_error_prefix(self, capsys):
        r = PlainRenderer()
        r.tool_result("boom", is_error=True)
        err = capsys.readouterr().err
        assert "! boom" in err

    def test_thinking_delta_only_when_debug(self, capsys):
        silent = PlainRenderer(debug=False)
        silent.thinking_delta("ponder")
        assert capsys.readouterr().err == ""

        loud = PlainRenderer(debug=True)
        loud.thinking_delta("ponder")
        assert "ponder" in capsys.readouterr().err

    def test_error_writes_error_label_to_stderr(self, capsys):
        r = PlainRenderer()
        r.error("something went wrong")
        err = capsys.readouterr().err
        assert "Error: something went wrong" in err

    def test_flush_response_clears_buffer(self):
        r = PlainRenderer()
        r.text_delta("chunk-a")
        r.text_delta("chunk-b")
        assert r._buf == ["chunk-a", "chunk-b"]
        r.flush_response()
        assert r._buf == []

    def test_prompt_and_banner_no_op_helpers(self, capsys):
        assert PlainRenderer.prompt() == PROMPT
        r = PlainRenderer()
        r.banner("claude-opus")
        r.status_bar("claude-opus", 1)  # must not raise / no-op
        r.update_stats(input_tokens=10, output_tokens=5, cost=0.01)  # no-op
        r.turn_end()
        out = capsys.readouterr().out
        assert "D.U.H. interactive mode (claude-opus)" in out


# ---------------------------------------------------------------------------
# RichRenderer (skip when `rich` is not installed)
# ---------------------------------------------------------------------------


pytestmark_rich = pytest.mark.skipif(not HAS_RICH, reason="rich not installed")


def _make_rich(debug: bool = False) -> RichRenderer:
    """Build a RichRenderer with mocked Consoles so we can assert calls."""
    r = RichRenderer(debug=debug)
    r._console = MagicMock()
    r._err_console = MagicMock()
    return r


@pytestmark_rich
class TestRichRenderer:
    def test_tool_use_prints_rich_header_and_spawns_spinner(self, capsys):
        r = _make_rich()
        r.tool_use("Read", {"path": "/tmp/f"})
        # Rich header goes through the mocked console.
        assert r._err_console.print.call_count == 1
        # Spinner line goes through raw stderr.
        err = capsys.readouterr().err
        assert "running Read" in err
        assert r._active_tool == "Read"

    def test_tool_result_success_renders_ok_panel(self):
        r = _make_rich()
        r._active_tool = "Read"
        r.tool_result("file contents", is_error=False)
        # Should have printed a Panel via Rich.
        assert r._err_console.print.called
        # Panel import is in the module; spot-check the title keyword.
        args, _ = r._err_console.print.call_args
        panel = args[0]
        assert "tool ok" in str(panel.title)
        assert r._active_tool is None

    def test_tool_result_error_renders_err_panel(self):
        r = _make_rich()
        r.tool_result("kaboom", is_error=True)
        args, _ = r._err_console.print.call_args
        panel = args[0]
        assert "tool error" in str(panel.title)

    def test_thinking_delta_only_when_debug(self):
        silent = _make_rich(debug=False)
        silent.thinking_delta("pondering")
        assert silent._err_console.print.call_count == 0

        loud = _make_rich(debug=True)
        loud.thinking_delta("pondering")
        assert loud._err_console.print.call_count == 1

    def test_error_clears_spinner_and_prints_panel(self, capsys):
        r = _make_rich()
        r._active_tool = "Bash"
        r.error("boom")
        # Spinner clear sequence on stderr.
        err = capsys.readouterr().err
        assert "\r\033[K" in err
        # Rich panel rendered via err console.
        args, _ = r._err_console.print.call_args
        assert "Error" in str(args[0].title)
        assert r._active_tool is None

    def test_update_stats_and_status_bar_include_tokens_and_cost(self):
        r = _make_rich()
        r.update_stats(input_tokens=1234, output_tokens=56, cost=0.0125)
        r.status_bar("gpt-4", 3)
        args, _ = r._err_console.print.call_args
        rendered = str(args[0])
        assert "gpt-4" in rendered
        assert "turn 3" in rendered
        assert "in=1,234" in rendered
        assert "out=56" in rendered
        assert "$0.0125" in rendered

    def test_text_delta_buffers_for_later_flush(self, capsys):
        r = _make_rich()
        r.text_delta("plain chunk ")
        r.text_delta("more text")
        # Raw streamed output (goes to real stdout).
        assert capsys.readouterr().out == "plain chunk more text"
        assert r._buf == ["plain chunk ", "more text"]
        # Without markdown indicators, flush_response just clears the buffer.
        r.flush_response()
        assert r._buf == []
        assert not r._console.print.called


# ---------------------------------------------------------------------------
# Backward-compatible re-exports from duh.cli.repl
# ---------------------------------------------------------------------------


class TestReplModuleReExports:
    def test_repl_module_exposes_legacy_names(self):
        from duh.cli import repl

        assert repl._PlainRenderer is PlainRenderer
        assert repl._RichRenderer is RichRenderer
        assert repl._HAS_RICH == HAS_RICH
        assert repl.PROMPT == PROMPT

    def test_make_renderer_returns_plain_when_rich_disabled(self):
        from duh.cli import repl

        with patch("duh.cli.repl._HAS_RICH", False):
            r = repl._make_renderer(debug=True)
        assert isinstance(r, PlainRenderer)
        assert r.debug is True
