"""Regression tests for ToolCallWidget preview of Swarm / Agent multi-task output.

Bug fixed on branch ``fix/swarm-tool-result-display``:
    The collapsed preview of a Swarm call showing 4 sub-tasks rendered only
    the first line — ``--- Task 1/4 [researcher] ---`` — which misled the
    user into thinking only one sub-agent had run.  The fix parses the
    per-task block headers and shows a task-count summary like
    ``4/4 tasks OK`` instead.  Edit/Write diff rendering and single-tool
    previews (Bash, Read, …) must remain unchanged.

Two tiers of test:
    * Pure-function tests of the parser helper (fast, regex-focused).
    * Widget-level tests that use a ``MagicMock`` for ``_result_label`` so we
      can assert on the exact string passed to ``Static.update()`` without
      booting the full Textual runtime (matches the pattern already used by
      ``test_output_styles.py``).
    * A couple of ``DuhApp.run_test`` integration tests for the Collapsible
      auto-expand behaviour, which requires a real mounted Collapsible.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("textual", reason="textual not installed")

from duh.ui.widgets import (  # noqa: E402
    ToolCallWidget,
    _summarise_multi_agent_output,
)
from duh.ui.app import DuhApp  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures / constants
# ---------------------------------------------------------------------------


def _fake_engine() -> MagicMock:
    """Minimal engine mock good enough to instantiate ``DuhApp``."""

    async def _run(_prompt: str):  # pragma: no cover — not exercised here
        return
        yield  # make this an async generator

    engine = MagicMock()
    engine.run = _run
    engine.total_input_tokens = 0
    engine.total_output_tokens = 0
    engine.session_id = "test-session"
    return engine


def _make_widget(tool_name: str = "Swarm") -> tuple[ToolCallWidget, MagicMock]:
    """Return a ToolCallWidget with a MagicMock result label attached.

    This bypasses the Textual lifecycle — set_result() writes to a mock so
    we can inspect the exact text passed to Static.update().  Used by
    ``test_output_styles.py`` for the same reason.
    """
    w = ToolCallWidget(tool_name=tool_name, input={})
    mock_label = MagicMock()
    w._result_label = mock_label
    return w, mock_label


def _last_update(label: MagicMock) -> str:
    """Return the string passed to the most recent ``label.update(...)`` call."""
    assert label.update.called, "Static.update() was never called"
    return label.update.call_args[0][0]


SWARM_4_ALL_OK = (
    "--- Task 1/4 [researcher] ---\n"
    "Prompt: research auth patterns\n"
    "Status: OK\n"
    "Result: found JWT patterns\n"
    "\n"
    "--- Task 2/4 [coder] ---\n"
    "Prompt: implement auth\n"
    "Status: OK\n"
    "Result: added login endpoint\n"
    "\n"
    "--- Task 3/4 [tester] ---\n"
    "Prompt: write auth tests\n"
    "Status: OK\n"
    "Result: 12 tests passing\n"
    "\n"
    "--- Task 4/4 [reviewer] ---\n"
    "Prompt: review changes\n"
    "Status: OK\n"
    "Result: LGTM\n"
)

SWARM_4_MIXED = (
    "--- Task 1/4 [researcher] ---\n"
    "Status: OK\n"
    "Result: research done\n"
    "\n"
    "--- Task 2/4 [coder] ---\n"
    "Status: OK\n"
    "Result: code written\n"
    "\n"
    "--- Task 3/4 [tester] ---\n"
    "Status: ERROR\n"
    "Result: tests failed to run\n"
    "\n"
    "--- Task 4/4 [reviewer] ---\n"
    "Status: OK\n"
    "Result: LGTM\n"
)

AGENT_SINGLE = (
    "--- Task 1/1 [researcher] ---\n"
    "Prompt: find patterns\n"
    "Status: OK\n"
    "Result: 3 patterns found\n"
)


# ---------------------------------------------------------------------------
# 1. Pure-function tests — _summarise_multi_agent_output
# ---------------------------------------------------------------------------


class TestSummariseHelper:
    """Direct coverage of the parser helper — cheap and regex-focused."""

    def test_empty_input_returns_none(self):
        assert _summarise_multi_agent_output("") is None
        assert _summarise_multi_agent_output("\n\n") is None

    def test_no_header_returns_none(self):
        """Bash output and similar must fall through to the first-line path."""
        assert _summarise_multi_agent_output("bin etc tmp usr") is None
        assert _summarise_multi_agent_output("no markers here at all") is None

    def test_malformed_header_without_slash_returns_none(self):
        """Be defensive: only real Task N/M [type] blocks count."""
        assert _summarise_multi_agent_output("--- Task [x] ---\nStatus: OK") is None
        assert _summarise_multi_agent_output("--- Task 1 [x] ---\nStatus: OK") is None

    def test_single_task_uses_singular_noun(self):
        assert _summarise_multi_agent_output(AGENT_SINGLE) == "1/1 task OK"

    def test_all_ok_counts_correctly(self):
        assert _summarise_multi_agent_output(SWARM_4_ALL_OK) == "4/4 tasks OK"

    def test_mixed_ok_and_error(self):
        assert _summarise_multi_agent_output(SWARM_4_MIXED) == "3/4 tasks OK, 1 errors"

    def test_header_without_status_lines_reports_zero_ok(self):
        txt = (
            "--- Task 1/2 [a] ---\nno status\n\n"
            "--- Task 2/2 [b] ---\nstill nothing\n"
        )
        assert _summarise_multi_agent_output(txt) == "0/2 tasks OK"

    def test_header_with_whitespace_variations(self):
        txt = (
            "---  Task 1 / 3  [ researcher ]  ---\nStatus: OK\n"
            "--- Task 2/3 [coder] ---\nStatus: OK\n"
            "--- Task 3/3 [tester] ---\nStatus: OK\n"
        )
        assert _summarise_multi_agent_output(txt) == "3/3 tasks OK"


# ---------------------------------------------------------------------------
# 2. Widget-level tests — mocked result label
# ---------------------------------------------------------------------------


class TestSwarmPreviewMocked:
    """Assert on the exact text written to Static.update() by set_result().

    These tests do NOT mount the widget — they just wire a MagicMock to
    ``_result_label`` (pattern used elsewhere in the suite).  The Collapsible
    auto-expand logic is covered separately by the integration tests below,
    because it requires a real mounted Collapsible instance.
    """

    def test_swarm_all_ok_shows_task_count_summary(self):
        """4/4 Swarm sub-tasks render as '4/4 tasks OK' in the preview."""
        w, label = _make_widget(tool_name="Swarm")
        w.set_result(SWARM_4_ALL_OK, is_error=False, elapsed_ms=44400)
        text = _last_update(label)
        assert "4/4 tasks OK" in text
        # The old bug: the raw header leaked into the preview.
        assert "--- Task 1/4" not in text

    def test_swarm_mixed_shows_error_count(self):
        """Mixed OK/ERROR surfaces the failure count next to the tally."""
        w, label = _make_widget(tool_name="Swarm")
        w.set_result(SWARM_4_MIXED, is_error=False, elapsed_ms=12000)
        text = _last_update(label)
        assert "3/4 tasks OK" in text
        assert "1 errors" in text

    def test_agent_single_task_uses_singular(self):
        """Single-task Agent output reads naturally as '1/1 task OK'."""
        w, label = _make_widget(tool_name="Agent")
        w.set_result(AGENT_SINGLE, is_error=False, elapsed_ms=3200)
        text = _last_update(label)
        assert "1/1 task OK" in text

    def test_bash_tool_preview_unchanged(self):
        """Non-multi-agent tools still use the historical first-line summary."""
        w, label = _make_widget(tool_name="Bash")
        w.set_result("bin\netc\ntmp\nusr", is_error=False, elapsed_ms=50)
        text = _last_update(label)
        # First line preserved, trailing lines not included.
        assert "bin" in text
        assert "etc" not in text
        # Must not accidentally apply the task-summary path.
        assert "tasks OK" not in text

    def test_bash_output_with_coincidental_task_marker_ignored(self):
        """If a non-multi-agent tool *happens* to print the marker, we still
        take the first-line path — the summary path is gated on tool name."""
        w, label = _make_widget(tool_name="Bash")
        # Pathological: a Bash script that echoes the task marker.
        payload = "--- Task 1/2 [fake] ---\nStatus: OK\n--- Task 2/2 [fake] ---\nStatus: OK"
        w.set_result(payload, is_error=False, elapsed_ms=10)
        text = _last_update(label)
        assert "tasks OK" not in text
        assert "--- Task 1/2" in text

    def test_elapsed_time_preserved_in_multi_agent_preview(self):
        """Elapsed suffix (e.g. ``(44.4s)``) stays visible alongside the tally."""
        w, label = _make_widget(tool_name="Swarm")
        w.set_result(SWARM_4_ALL_OK, is_error=False, elapsed_ms=44400)
        text = _last_update(label)
        assert "(44.4s)" in text
        assert "4/4 tasks OK" in text

    def test_swarm_empty_output_falls_back_to_default_preview(self):
        """Empty Swarm output must not crash and must fall through."""
        w, label = _make_widget(tool_name="Swarm")
        w.set_result("", is_error=False, elapsed_ms=10)
        text = _last_update(label)
        # No summary (helper returned None for empty input).
        assert "tasks OK" not in text
        # Fall-through path still emits an OK marker.
        assert "OK" in text

    def test_swarm_malformed_output_falls_back_to_first_line(self):
        """Output that *looks* multi-agent but lacks the Task N/M header
        falls back to first-line preview so the user still sees something."""
        w, label = _make_widget(tool_name="Swarm")
        payload = "--- some header ---\nsecond line\nthird line"
        w.set_result(payload, is_error=False, elapsed_ms=10)
        text = _last_update(label)
        assert "tasks OK" not in text
        assert "--- some header ---" in text
        # Later lines stripped by the first-line policy.
        assert "second line" not in text


# ---------------------------------------------------------------------------
# 3. Integration tests — Collapsible auto-expand (requires real mount)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestCollapsibleAutoExpand:
    async def test_swarm_collapsible_auto_expands(self):
        """Collapsible is open after set_result() on Swarm — users see sub-tasks."""
        app = DuhApp(engine=_fake_engine())
        async with app.run_test(size=(120, 40)) as pilot:
            w = ToolCallWidget(tool_name="Swarm", input={})
            await app.query_one("#message-log").mount(w)
            await pilot.pause()
            # Pre-emptively collapse to prove set_result flips it back open.
            assert w._collapsible is not None
            w._collapsible.collapsed = True
            w.set_result(SWARM_4_ALL_OK, is_error=False, elapsed_ms=1000)
            await pilot.pause()
            assert w._collapsible.collapsed is False

    async def test_bash_collapsible_not_forced_open(self):
        """Non-multi-agent tools: set_result does NOT touch the collapsed flag.

        Users may have collapsed a Bash output manually; we respect that.
        """
        app = DuhApp(engine=_fake_engine())
        async with app.run_test(size=(120, 40)) as pilot:
            w = ToolCallWidget(tool_name="Bash", input={"command": "ls"})
            await app.query_one("#message-log").mount(w)
            await pilot.pause()
            assert w._collapsible is not None
            w._collapsible.collapsed = True  # user collapsed manually
            w.set_result("bin etc", is_error=False, elapsed_ms=10)
            await pilot.pause()
            # Bash → no auto-expand.
            assert w._collapsible.collapsed is True
