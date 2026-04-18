"""Tier C tmux-based integration tests (ADR-074).

These tests run the real ``duh`` binary inside a real tmux pane and exercise
scenarios that neither the unit/Pilot tier nor the PTY + pyte tier can cover:

* Terminal resize mid-session
* Scrollback / scroll history semantics
* Ctrl+C recovery (prompt returns, session still usable)
* Multi-instance isolation (two duh REPLs in distinct sessions)

Every test is marked ``@pytest.mark.tmux`` so the default test run skips
them. CI enables them via ``-m tmux`` on dedicated macOS + Linux jobs; the
conftest skip guard protects Windows runners where tmux isn't available.
"""

from __future__ import annotations

import re
import time

import pytest

from tests.integration.tmux_helpers import (
    capture_pane,
    cleanup,
    send_keys,
    start_duh_in_tmux,
    wait_for_text,
)

pytestmark = [pytest.mark.tmux, pytest.mark.slow]


PROMPT = "duh>"
BOOT_MARKER = "interactive mode"


# ---------------------------------------------------------------------------
# 1. Scroll history survives terminal resize (grow 80x24 -> 120x40)
# ---------------------------------------------------------------------------


def test_scroll_history_survives_resize():
    server, _session, pane, name = start_duh_in_tmux(
        width=80, height=24, session_name="scroll-resize"
    )
    try:
        assert wait_for_text(pane, BOOT_MARKER, timeout=5), (
            "duh never painted its banner at 80x24"
        )

        # Populate scrollback: /help is ~30 lines (exceeds 24-row window).
        # Run it 3 times so we definitely push content off the visible area.
        for _ in range(3):
            send_keys(pane, "/help")
            assert wait_for_text(pane, "/exit", timeout=5), (
                "/help never rendered its last line"
            )

        # Resize up. tmux scrollback survives a grow.
        pane.resize(width=120, height=40)
        time.sleep(0.4)  # let tmux reflow

        # Capture with full history. The earliest /help output should still
        # be present somewhere in scrollback.
        full = capture_pane(pane, start=-1000)
        assert "Show available commands" in full, (
            "Earliest /help output disappeared after resize; scrollback lost. "
            f"Captured {len(full)} chars."
        )
        # And the current prompt is still alive.
        assert wait_for_text(pane, PROMPT, timeout=3)
    finally:
        cleanup(server, name)


# ---------------------------------------------------------------------------
# 2. Long output visible via scrollback
# ---------------------------------------------------------------------------


def test_long_output_visible_via_scrollback():
    server, _session, pane, name = start_duh_in_tmux(
        width=100, height=20, session_name="long-output"
    )
    try:
        assert wait_for_text(pane, BOOT_MARKER, timeout=5)

        send_keys(pane, "/help")
        # /help has a unique first item and a unique last item. Both must be
        # retrievable via scrollback even though only ~20 lines are visible.
        assert wait_for_text(pane, "/exit", timeout=5), "tail of /help missing"

        full = capture_pane(pane, start=-1000)
        first_line_marker = "Show available commands"
        assert first_line_marker in full, (
            "First line of /help unreachable via scrollback"
        )
        # Sanity: also make sure the tail is present (from visible area).
        assert "Exit the REPL" in full
    finally:
        cleanup(server, name)


# ---------------------------------------------------------------------------
# 3. Ctrl+C brings the prompt back and the session is still usable
# ---------------------------------------------------------------------------


def test_ctrl_c_prompt_returns():
    server, _session, pane, name = start_duh_in_tmux(
        width=100, height=30, session_name="ctrlc-recover"
    )
    try:
        assert wait_for_text(pane, BOOT_MARKER, timeout=5)
        assert wait_for_text(pane, PROMPT, timeout=5)

        # Kick a prompt.  The stub provider returns almost immediately, but
        # we still fire Ctrl+C right after as a smoke test: it must either
        # (a) interrupt a still-streaming turn, or (b) be a no-op against
        # an idle prompt.  Either way the REPL must stay alive.
        send_keys(pane, "hello world")
        pane.send_keys("C-c", enter=False, suppress_history=False)
        time.sleep(1.0)

        # Prompt must be back.
        assert wait_for_text(pane, PROMPT, timeout=3), (
            "Prompt did not return after Ctrl+C"
        )

        # Session must still accept a fresh prompt.
        send_keys(pane, "second")
        assert wait_for_text(pane, "stub-ok", timeout=5), (
            "REPL unresponsive after Ctrl+C recovery"
        )
    finally:
        cleanup(server, name)


# ---------------------------------------------------------------------------
# 4. Mid-stream resize must not crash (layout reflows)
# ---------------------------------------------------------------------------


def test_terminal_resize_mid_stream():
    # Large stub response to give resize a chance to land while duh is
    # still painting.  The stub emits text_delta → assistant → done; with
    # a long payload there's enough wire chatter that a resize during it
    # exercises the reflow path.
    long_response = "abcdefghij" * 200  # 2000 chars
    server, _session, pane, name = start_duh_in_tmux(
        width=100,
        height=30,
        session_name="resize-mid-stream",
        env_extra={"DUH_STUB_RESPONSE": long_response},
    )
    try:
        assert wait_for_text(pane, BOOT_MARKER, timeout=5)
        assert wait_for_text(pane, PROMPT, timeout=5)

        send_keys(pane, "go", settle=0.0)  # fire the turn, don't debounce
        # Resize immediately to race with the delta emission.
        pane.resize(width=60, height=20)
        time.sleep(0.3)
        pane.resize(width=100, height=30)

        # No crash = the REPL eventually returns to prompt.  Text from the
        # response must be present somewhere.
        assert wait_for_text(pane, PROMPT, timeout=6), (
            "duh prompt never returned after mid-stream resize"
        )
        full = capture_pane(pane, start=-1000)
        # Fragment of the long response must have made it to the screen.
        assert "abcdefghij" in full, (
            "Stub response content missing after resize"
        )
    finally:
        cleanup(server, name)


# ---------------------------------------------------------------------------
# 5. Two duh instances are fully isolated (distinct session IDs)
# ---------------------------------------------------------------------------


_SESSION_RE = re.compile(r"Session:\s+([0-9a-fA-F]+)")


def _extract_session_id(pane) -> str | None:
    buf = capture_pane(pane, start=-500)
    m = _SESSION_RE.search(buf)
    return m.group(1) if m else None


def test_two_duh_instances_isolated():
    server_a, _sa, pane_a, name_a = start_duh_in_tmux(
        width=100, height=30, session_name="iso-a"
    )
    server_b, _sb, pane_b, name_b = start_duh_in_tmux(
        width=100, height=30, session_name="iso-b"
    )
    try:
        assert wait_for_text(pane_a, BOOT_MARKER, timeout=5)
        assert wait_for_text(pane_b, BOOT_MARKER, timeout=5)

        send_keys(pane_a, "/status")
        send_keys(pane_b, "/status")

        assert wait_for_text(pane_a, "Session:", timeout=5)
        assert wait_for_text(pane_b, "Session:", timeout=5)

        sid_a = _extract_session_id(pane_a)
        sid_b = _extract_session_id(pane_b)
        assert sid_a, "pane A did not expose a session id"
        assert sid_b, "pane B did not expose a session id"
        assert sid_a != sid_b, (
            f"two duh instances shared session id {sid_a!r}; isolation broken"
        )

        # Confirm independent conversation state: send a unique prompt to A.
        send_keys(pane_a, "ping-alpha-only")
        assert wait_for_text(pane_a, "stub-ok", timeout=5)
        # B should not have seen the A-only prompt text rendered in its pane.
        buf_b = capture_pane(pane_b, start=-500)
        assert "ping-alpha-only" not in buf_b, (
            "Instance B received traffic meant for instance A"
        )
    finally:
        cleanup(server_a, name_a)
        cleanup(server_b, name_b)


# ---------------------------------------------------------------------------
# 6. Sanity: /help renders inside tmux
# ---------------------------------------------------------------------------


def test_help_command_works():
    server, _session, pane, name = start_duh_in_tmux(
        width=120, height=40, session_name="help-sanity"
    )
    try:
        assert wait_for_text(pane, BOOT_MARKER, timeout=5)
        send_keys(pane, "/help")
        assert wait_for_text(pane, "Show available commands", timeout=5), (
            "/help output never appeared"
        )
        assert wait_for_text(pane, "Exit the REPL", timeout=5), (
            "/help tail missing"
        )
    finally:
        cleanup(server, name)
