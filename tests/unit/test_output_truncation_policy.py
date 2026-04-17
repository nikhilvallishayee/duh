"""Tests for :class:`OutputTruncationPolicy` (ADR-073 Wave 2).

The policy is the single source of truth for how much tool output each
renderer shows for a given :class:`OutputStyle`.  It must:

- Return the documented (max_chars, max_lines) matrix for every
  ``style × is_error`` combination.
- Truncate respecting both caps — the stricter wins.
- Produce identical output in :class:`RichRenderer` and
  :class:`ToolCallWidget` when given the same ``(style, is_error, text)``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from duh.ui.styles import OutputStyle, OutputTruncationPolicy


# ---------------------------------------------------------------------------
# 1. Policy matrix per style × is_error
# ---------------------------------------------------------------------------


class TestPolicyMatrix:
    """The concrete thresholds documented on :meth:`for_style`."""

    def test_concise_success_is_zero_zero(self):
        p = OutputTruncationPolicy.for_style(OutputStyle.CONCISE, is_error=False)
        assert p.max_chars == 0
        assert p.max_lines == 0

    def test_concise_error_is_200_by_5(self):
        p = OutputTruncationPolicy.for_style(OutputStyle.CONCISE, is_error=True)
        assert p.max_chars == 200
        assert p.max_lines == 5

    def test_default_success_is_120_by_3(self):
        p = OutputTruncationPolicy.for_style(OutputStyle.DEFAULT, is_error=False)
        assert p.max_chars == 120
        assert p.max_lines == 3

    def test_default_error_is_300_by_10(self):
        p = OutputTruncationPolicy.for_style(OutputStyle.DEFAULT, is_error=True)
        assert p.max_chars == 300
        assert p.max_lines == 10

    def test_verbose_success_is_2000_by_60(self):
        p = OutputTruncationPolicy.for_style(OutputStyle.VERBOSE, is_error=False)
        assert p.max_chars == 2000
        assert p.max_lines == 60

    def test_verbose_error_is_2000_by_30(self):
        p = OutputTruncationPolicy.for_style(OutputStyle.VERBOSE, is_error=True)
        assert p.max_chars == 2000
        assert p.max_lines == 30

    def test_policy_is_frozen_dataclass(self):
        """Frozen dataclasses are hashable — guards against accidental mutation."""
        p = OutputTruncationPolicy.for_style(OutputStyle.DEFAULT)
        with pytest.raises(Exception):
            p.max_chars = 999  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 2. apply() — truncation semantics
# ---------------------------------------------------------------------------


class TestApply:
    def test_apply_returns_false_when_text_fits(self):
        p = OutputTruncationPolicy(max_chars=100, max_lines=10)
        out, trunc = p.apply("hello world")
        assert out == "hello world"
        assert trunc is False

    def test_apply_truncates_when_chars_exceed(self):
        p = OutputTruncationPolicy(max_chars=5, max_lines=10)
        out, trunc = p.apply("hello world")
        assert out == "hello"
        assert trunc is True

    def test_apply_truncates_when_lines_exceed(self):
        """Line cap wins even if the char cap would allow the full text."""
        p = OutputTruncationPolicy(max_chars=10_000, max_lines=2)
        out, trunc = p.apply("a\nb\nc\nd\ne")
        assert out == "a\nb"
        assert trunc is True

    def test_apply_honours_whichever_limit_hits_first(self):
        """A text that exceeds both caps is cut by the stricter one."""
        p = OutputTruncationPolicy(max_chars=3, max_lines=10)
        # 100 lines of 'XX' each; line cap wouldn't kick in but char cap does.
        out, trunc = p.apply("XX\n" * 100)
        assert len(out) == 3
        assert trunc is True

    def test_apply_returns_empty_when_max_chars_zero(self):
        """A zero policy suppresses all output but flags truncation on non-empty input."""
        p = OutputTruncationPolicy(max_chars=0, max_lines=0)
        out, trunc = p.apply("anything")
        assert out == ""
        assert trunc is True

    def test_apply_on_empty_input_never_truncated(self):
        p = OutputTruncationPolicy(max_chars=0, max_lines=0)
        out, trunc = p.apply("")
        assert out == ""
        assert trunc is False

    def test_apply_on_none_is_safe(self):
        """Robustness: apply(None) returns empty without raising."""
        p = OutputTruncationPolicy(max_chars=100, max_lines=10)
        out, trunc = p.apply(None)  # type: ignore[arg-type]
        assert out == ""
        assert trunc is False

    def test_apply_line_cap_preserves_char_cap(self):
        """Both caps applied: 5 lines of 1000 chars, line cap=2, char cap=50."""
        p = OutputTruncationPolicy(max_chars=50, max_lines=2)
        text = ("X" * 1000 + "\n") * 5
        out, trunc = p.apply(text)
        # Line cap first → 2 lines of 1000 X's joined by "\n" (2001 chars).
        # Char cap then trims to 50.
        assert len(out) == 50
        assert trunc is True


# ---------------------------------------------------------------------------
# 3. RichRenderer uses the policy
# ---------------------------------------------------------------------------


# Reuse the helper from test_repl_renderers.
from duh.cli.repl_renderers import HAS_RICH, RichRenderer  # noqa: E402


pytestmark_rich = pytest.mark.skipif(not HAS_RICH, reason="rich not installed")


def _rich_renderer(
    style: OutputStyle = OutputStyle.DEFAULT, debug: bool = False
) -> RichRenderer:
    r = RichRenderer(debug=debug, output_style=style)
    r._console = MagicMock()
    r._err_console = MagicMock()
    return r


def _panel_body_text(mock_console: MagicMock) -> str:
    """Extract the text content of the last Panel printed on ``mock_console``."""
    panel = mock_console.print.call_args[0][0]
    body = panel.renderable
    # Rich Text has .plain; plain strings are themselves.
    return getattr(body, "plain", body) if not isinstance(body, str) else body


@pytestmark_rich
class TestRichRendererUsesPolicy:
    def test_default_style_truncates_success_to_first_line_120_chars(self):
        r = _rich_renderer(OutputStyle.DEFAULT)
        r._active_tool = "Bash"
        r.tool_result("X" * 500 + "\nextra line", is_error=False)
        text = _panel_body_text(r._err_console)
        # First line is 500 X's; DEFAULT policy caps at 120.
        assert "X" * 120 in text
        assert "X" * 121 not in text

    def test_default_style_truncates_error_to_300_chars(self):
        r = _rich_renderer(OutputStyle.DEFAULT)
        r.tool_result("Z" * 1000, is_error=True)
        panel = r._err_console.print.call_args[0][0]
        body = panel.renderable
        text = body if isinstance(body, str) else body.plain
        assert "Z" * 300 in text
        assert "Z" * 301 not in text

    def test_verbose_style_allows_2000_chars_success(self):
        r = _rich_renderer(OutputStyle.VERBOSE)
        r.tool_result("A" * 5000, is_error=False)
        panel = r._err_console.print.call_args[0][0]
        body = panel.renderable
        text = body if isinstance(body, str) else body.plain
        assert "A" * 2000 in text
        assert "A" * 2001 not in text

    def test_concise_style_success_shows_status_only(self):
        r = _rich_renderer(OutputStyle.CONCISE)
        r.tool_result("some long output here", is_error=False)
        panel = r._err_console.print.call_args[0][0]
        body = panel.renderable
        text = body if isinstance(body, str) else body.plain
        # Concise success suppresses preview entirely — only the "(ok)" marker.
        assert "some long output" not in text


# ---------------------------------------------------------------------------
# 4. ToolCallWidget uses the policy
# ---------------------------------------------------------------------------


textual = pytest.importorskip("textual", reason="textual not installed")

from duh.ui.widgets import ToolCallWidget  # noqa: E402


def _widget_with_mock_label() -> tuple[ToolCallWidget, MagicMock]:
    w = ToolCallWidget(tool_name="Bash", input={"command": "ls"})
    label = MagicMock()
    w._result_label = label
    return w, label


class TestToolCallWidgetUsesPolicy:
    def test_default_style_caps_success_at_120(self):
        w, label = _widget_with_mock_label()
        w.set_result("X" * 500, is_error=False, style="default")
        text = label.update.call_args[0][0]
        assert "X" * 120 in text
        assert "X" * 121 not in text

    def test_default_style_caps_error_at_300(self):
        w, label = _widget_with_mock_label()
        w.set_result("Z" * 1000, is_error=True, style="default")
        text = label.update.call_args[0][0]
        assert "Z" * 300 in text
        assert "Z" * 301 not in text

    def test_verbose_style_caps_success_at_2000(self):
        w, label = _widget_with_mock_label()
        w.set_result("A" * 5000, is_error=False, style="verbose")
        text = label.update.call_args[0][0]
        assert "A" * 2000 in text
        assert "A" * 2001 not in text

    def test_concise_style_success_hides_output(self):
        w, label = _widget_with_mock_label()
        w.set_result("some long success output", is_error=False, style="concise")
        text = label.update.call_args[0][0]
        assert "some long success output" not in text
        assert "OK" in text


# ---------------------------------------------------------------------------
# 5. REPL and TUI produce the same truncated content for the same style
# ---------------------------------------------------------------------------


@pytestmark_rich
class TestRendererParity:
    """For a given ``(style, is_error)``, the REPL and TUI must show the same
    number of characters (modulo markup) so switching modes is lossless.
    """

    def test_default_style_same_char_count_success(self):
        payload = "hello world\n" + "B" * 500
        # REPL
        r = _rich_renderer(OutputStyle.DEFAULT)
        r.tool_result(payload, is_error=False)
        panel = r._err_console.print.call_args[0][0]
        body = panel.renderable
        rich_text = body if isinstance(body, str) else body.plain
        # TUI
        w, label = _widget_with_mock_label()
        w.set_result(payload, is_error=False, style="default")
        tui_text = label.update.call_args[0][0]

        # Both should render just the first line (up to 120 chars).
        # "hello world" is 11 chars, well under the cap.
        assert "hello world" in rich_text
        assert "hello world" in tui_text
        # Neither should leak content from the second line.
        assert "B" not in rich_text
        assert "B" not in tui_text

    def test_verbose_style_same_cap_success(self):
        payload = "V" * 5000
        # REPL
        r = _rich_renderer(OutputStyle.VERBOSE)
        r.tool_result(payload, is_error=False)
        panel = r._err_console.print.call_args[0][0]
        body = panel.renderable
        rich_text = body if isinstance(body, str) else body.plain
        # TUI
        w, label = _widget_with_mock_label()
        w.set_result(payload, is_error=False, style="verbose")
        tui_text = label.update.call_args[0][0]

        # Both cap at 2000 V's.
        assert "V" * 2000 in rich_text
        assert "V" * 2001 not in rich_text
        assert "V" * 2000 in tui_text
        assert "V" * 2001 not in tui_text

    def test_default_style_same_error_cap(self):
        payload = "E" * 1000
        # REPL
        r = _rich_renderer(OutputStyle.DEFAULT)
        r.tool_result(payload, is_error=True)
        panel = r._err_console.print.call_args[0][0]
        body = panel.renderable
        rich_text = body if isinstance(body, str) else body.plain
        # TUI
        w, label = _widget_with_mock_label()
        w.set_result(payload, is_error=True, style="default")
        tui_text = label.update.call_args[0][0]

        assert "E" * 300 in rich_text
        assert "E" * 301 not in rich_text
        assert "E" * 300 in tui_text
        assert "E" * 301 not in tui_text
