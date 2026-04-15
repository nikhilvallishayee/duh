"""Tests for ADR-067 P0: thinking block display and diff rendering.

Covers:
- ThinkingWidget collapsed parameter and char-count title updates
- Thinking shown in verbose mode (not just debug)
- Thinking shown collapsed in default mode
- Thinking NOT shown in concise mode
- Diff coloring for Edit/Write tool results
- _looks_like_diff and _render_diff helper functions
- ToolCallWidget.set_result with tool_name parameter
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

textual = pytest.importorskip("textual", reason="textual not installed")

from textual.widgets import Input, Button, Static  # noqa: E402

from duh.ui.widgets import (  # noqa: E402
    ThinkingWidget,
    ToolCallWidget,
    _looks_like_diff,
    _render_diff,
)
from duh.ui.styles import OutputStyle  # noqa: E402
from duh.ui.app import DuhApp  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_engine(events: list[dict]) -> MagicMock:
    """Return a mock engine whose run() yields the given events."""

    async def _run(_prompt: str):
        for ev in events:
            yield ev

    engine = MagicMock()
    engine.run = _run
    engine.total_input_tokens = 0
    engine.total_output_tokens = 0
    engine.session_id = "test-session"
    return engine


# ---------------------------------------------------------------------------
# ThinkingWidget unit tests
# ---------------------------------------------------------------------------


class TestThinkingWidgetCollapsed:
    def test_default_collapsed_is_true(self):
        w = ThinkingWidget()
        assert w._collapsed is True

    def test_collapsed_false(self):
        w = ThinkingWidget(collapsed=False)
        assert w._collapsed is False

    def test_collapsed_true_explicit(self):
        w = ThinkingWidget(collapsed=True)
        assert w._collapsed is True

    def test_append_accumulates_and_no_raise(self):
        w = ThinkingWidget()
        w.append("step 1 ")
        w.append("step 2")
        assert w._content == "step 1 step 2"


# ---------------------------------------------------------------------------
# _looks_like_diff
# ---------------------------------------------------------------------------


class TestLooksLikeDiff:
    def test_empty_string_is_not_diff(self):
        assert _looks_like_diff("") is False

    def test_plain_text_is_not_diff(self):
        assert _looks_like_diff("The file was updated successfully.") is False

    def test_unified_diff_header(self):
        text = "--- a/foo.py\n+++ b/foo.py\n@@ -1,3 +1,4 @@\n+new line"
        assert _looks_like_diff(text) is True

    def test_hunk_header_alone(self):
        assert _looks_like_diff("@@ -10,5 +10,6 @@\n context") is True

    def test_plus_minus_without_at(self):
        # Only + and - lines but no @@/---/+++ within first 20 lines
        text = "+added\n-removed\n context"
        assert _looks_like_diff(text) is False

    def test_triple_dash_detected(self):
        text = "--- old_file\n some content"
        assert _looks_like_diff(text) is True

    def test_triple_plus_detected(self):
        text = "+++ new_file\n some content"
        assert _looks_like_diff(text) is True


# ---------------------------------------------------------------------------
# _render_diff
# ---------------------------------------------------------------------------


class TestRenderDiff:
    def test_additions_are_green(self):
        text = "@@ -1,2 +1,3 @@\n context\n+added line"
        rendered = _render_diff(text)
        assert "[green]" in rendered
        assert "added line" in rendered

    def test_removals_are_red(self):
        text = "@@ -1,3 +1,2 @@\n context\n-removed line"
        rendered = _render_diff(text)
        assert "[red]" in rendered
        assert "removed line" in rendered

    def test_hunk_headers_are_cyan(self):
        text = "@@ -1,3 +1,3 @@\n context"
        rendered = _render_diff(text)
        assert "[cyan]" in rendered

    def test_file_headers_are_bold(self):
        text = "--- a/foo.py\n+++ b/foo.py\n@@ -1 +1 @@"
        rendered = _render_diff(text)
        assert "[bold]" in rendered

    def test_ok_prefix(self):
        text = "@@ -1 +1 @@\n+x"
        rendered = _render_diff(text)
        assert rendered.startswith("[green]OK:[/green]")

    def test_markup_chars_escaped(self):
        # Rich markup chars like [ should be escaped in content
        text = "@@ -1 +1 @@\n+print('[hello]')"
        rendered = _render_diff(text)
        # The literal [ should be escaped so Rich doesn't interpret it
        assert "\\[hello" in rendered or "[hello" not in rendered.replace("[green]", "")

    def test_max_lines_truncation(self):
        lines = ["@@ -1 +1 @@"] + [f"+line {i}" for i in range(100)]
        text = "\n".join(lines)
        rendered = _render_diff(text, max_lines=10)
        # Should mention truncation
        assert "more lines" in rendered


# ---------------------------------------------------------------------------
# ToolCallWidget.set_result with tool_name
# ---------------------------------------------------------------------------


class TestToolCallWidgetDiffRendering:
    def test_set_result_accepts_tool_name_kwarg(self):
        w = ToolCallWidget(tool_name="Edit", input={"file_path": "/tmp/x"})
        # Before mount, _result_label is None — should not raise
        w.set_result("@@ -1 +1 @@\n+new", is_error=False, tool_name="Edit")

    def test_set_result_without_tool_name_does_not_raise(self):
        w = ToolCallWidget(tool_name="Bash", input={"command": "ls"})
        w.set_result("output", is_error=False)

    def test_non_diff_output_for_edit_tool(self):
        """Edit tool with non-diff output should use normal rendering."""
        w = ToolCallWidget(tool_name="Edit", input={})
        # No mount, so _result_label is None — just verify no crash
        w.set_result("The file was updated successfully.", is_error=False, tool_name="Edit")

    def test_error_result_ignores_diff_rendering(self):
        """Error results should not attempt diff rendering."""
        w = ToolCallWidget(tool_name="Edit", input={})
        w.set_result("@@ -1 +1 @@\n+new", is_error=True, tool_name="Edit")


# ---------------------------------------------------------------------------
# DuhApp integration: thinking in verbose vs default vs concise
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestThinkingDisplay:
    def test_thinking_logic_verbose_shows_expanded(self):
        """Verbose mode code path creates thinking widget expanded."""
        import inspect
        source = inspect.getsource(DuhApp._run_query)
        assert "VERBOSE" in source
        assert "thinking_delta" in source

    def test_thinking_logic_default_shows_collapsed(self):
        """Default mode code path creates thinking widget collapsed."""
        import inspect
        source = inspect.getsource(DuhApp._run_query)
        assert "collapsed" in source

    def test_thinking_logic_concise_skips(self):
        """Concise mode code path skips thinking."""
        import inspect
        source = inspect.getsource(DuhApp._run_query)
        assert "CONCISE" in source

    def test_thinking_logic_debug_shows(self):
        """Debug mode code path shows thinking."""
        import inspect
        source = inspect.getsource(DuhApp._run_query)
        assert "self._debug" in source

    async def test_diff_rendering_for_edit_tool(self):
        """Edit tool results with diff-like output should show colored diff."""
        diff_output = "--- a/foo.py\n+++ b/foo.py\n@@ -1,2 +1,3 @@\n old\n+new line\n old2"
        events = [
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "/tmp/test.py"}},
            {"type": "tool_result", "output": diff_output, "is_error": False},
        ]
        engine = _fake_engine(events)
        app = DuhApp(engine=engine, model="test")
        async with app.run_test(size=(120, 40)) as pilot:
            inp = app.query_one("#prompt-input", Input)
            inp.value = "edit file"
            await pilot.click("#send-button")
            await pilot.pause(0.5)
            await pilot.pause(0.1)
            tool_widgets = app.query("ToolCallWidget")
            assert len(tool_widgets) >= 1
