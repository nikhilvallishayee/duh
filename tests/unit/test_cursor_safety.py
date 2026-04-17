"""Tests for ADR-073 Wave 2 / Task 7 — RichRenderer cursor-rewind safety.

``_RichRenderer.flush_response()`` used to emit an unconditional CSI
escape (``\\033[{lines}A\\033[J``) to move the cursor up and overwrite
the streamed text with re-rendered Markdown. In pipes, log files, and
other non-TTY destinations those escape codes are printed literally and
corrupt the output.

These tests pin the safety contract:

1. When stdout is a TTY we still emit the CSI sequence.
2. When stdout is NOT a TTY we emit a ``---`` separator and re-render
   the markdown below (no literal CSI codes anywhere in the output).
3. If ``isatty()`` itself raises (some unusual stream wrappers do), the
   renderer treats the stream as non-TTY — the safe path.
"""

from __future__ import annotations

import io
import sys
from unittest.mock import MagicMock

import pytest

from duh.cli.repl_renderers import HAS_RICH, RichRenderer

pytestmark = pytest.mark.skipif(not HAS_RICH, reason="rich not installed")


# Minimal markdown payload that triggers the re-render branch. The
# heuristic inside ``flush_response`` only re-renders when one of the
# indicators (``##``, fenced code, ``**``, ``* ``, etc.) is present in
# the buffered text.
_MARKDOWN_TEXT = "## Heading\n\nSome **bold** content."


def _make_renderer_with_mock_console() -> RichRenderer:
    """Return a RichRenderer whose Rich consoles are mocked.

    Lets us assert that ``_console.print(Markdown(...))`` is called for
    the re-render without actually writing to the terminal.
    """
    r = RichRenderer()
    r._console = MagicMock()
    r._err_console = MagicMock()
    return r


class TestCursorRewindSafety:
    def test_non_tty_stdout_does_not_emit_csi(self, monkeypatch, capsys):
        """Core regression: no ANSI CSI sequence in non-TTY output."""
        r = _make_renderer_with_mock_console()
        # Force non-TTY mode (pipe / log-file simulation).
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        r._buf = [_MARKDOWN_TEXT]

        r.flush_response()

        out = capsys.readouterr().out
        # The CSI move-up + clear sequence must NOT appear literally.
        assert "\033[" not in out
        # The buffer is drained regardless of TTY mode.
        assert r._buf == []

    def test_non_tty_stdout_emits_separator(self, monkeypatch, capsys):
        """Non-TTY path emits a ``---`` separator before the markdown."""
        r = _make_renderer_with_mock_console()
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        r._buf = [_MARKDOWN_TEXT]

        r.flush_response()

        out = capsys.readouterr().out
        assert "---" in out

    def test_non_tty_stdout_still_renders_markdown(self, monkeypatch):
        """Non-TTY must still call the Rich Markdown re-render."""
        r = _make_renderer_with_mock_console()
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        r._buf = [_MARKDOWN_TEXT]

        r.flush_response()

        # _console.print is called once with the Markdown object in
        # either branch. Verifying it here prevents a future refactor
        # from silently dropping the re-render on non-TTY.
        assert r._console.print.called
        args, _ = r._console.print.call_args
        # The first positional is a rich.markdown.Markdown instance.
        from rich.markdown import Markdown
        assert isinstance(args[0], Markdown)

    def test_tty_stdout_emits_csi_sequence(self, monkeypatch, capsys):
        """The cursor-rewind path is still live on real TTYs."""
        r = _make_renderer_with_mock_console()
        monkeypatch.setattr(sys.stdout, "isatty", lambda: True)
        r._buf = [_MARKDOWN_TEXT]

        r.flush_response()

        out = capsys.readouterr().out
        # The payload has 1 newline → lines == 2. We match the CSI
        # prefix and suffix without hard-coding the line count.
        assert "\033[" in out
        assert "A\033[J" in out

    def test_isatty_raising_is_treated_as_non_tty(self, monkeypatch, capsys):
        """``isatty()`` can raise on stream shims — default to safe path."""
        r = _make_renderer_with_mock_console()

        def _boom() -> bool:
            raise OSError("stream is closed")

        monkeypatch.setattr(sys.stdout, "isatty", _boom)
        r._buf = [_MARKDOWN_TEXT]

        r.flush_response()

        out = capsys.readouterr().out
        assert "\033[" not in out
        assert "---" in out

    def test_empty_buffer_is_a_noop(self, monkeypatch, capsys):
        """No flush side effects when the streamed buffer is empty/blank."""
        r = _make_renderer_with_mock_console()
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        r._buf = ["   \n\n   "]

        r.flush_response()

        out = capsys.readouterr().out
        assert out == ""
        # No re-render attempted for whitespace-only content.
        assert not r._console.print.called

    def test_plain_text_without_markdown_indicators_skips_rerender(
        self, monkeypatch, capsys,
    ):
        """Non-markdown text is NOT re-rendered regardless of TTY state."""
        r = _make_renderer_with_mock_console()
        monkeypatch.setattr(sys.stdout, "isatty", lambda: False)
        r._buf = ["just a plain sentence with no md syntax"]

        r.flush_response()

        out = capsys.readouterr().out
        # No separator printed because we didn't go into the
        # re-render branch at all.
        assert "---" not in out
        assert not r._console.print.called
