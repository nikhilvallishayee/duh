"""Tier A visual snapshot tests for the D.U.H. TUI (ADR-074).

Each test in this module captures a specific TUI state as an SVG and
compares it against a baseline committed under ``__snapshots__/``.
Regenerate the baselines after an intentional UI change with::

    .venv/bin/python -m pytest tests/snapshots/ --snapshot-update

See :file:`conftest.py` for the shared terminal-size constant and the
resolved scripts directory.
"""

from __future__ import annotations

import pytest

pytest.importorskip(
    "pytest_textual_snapshot", reason="tui-e2e extras not installed"
)

from tests.snapshots.conftest import SCRIPTS_DIR, SNAPSHOT_TERMINAL_SIZE  # noqa: E402


pytestmark = pytest.mark.snapshot


def _script(name: str) -> str:
    """Absolute-path helper so tests read top-to-bottom."""
    return str(SCRIPTS_DIR / name)


# ---------------------------------------------------------------------------
# Phase 1 — 10 screens
# ---------------------------------------------------------------------------


def test_welcome_fresh(snap_compare):
    """Welcome banner, no history, focused prompt."""
    assert snap_compare(
        _script("welcome_fresh.py"),
        terminal_size=SNAPSHOT_TERMINAL_SIZE,
    )


def test_welcome_resumed(snap_compare):
    """Welcome banner with 5 pre-loaded resumed messages."""
    assert snap_compare(
        _script("welcome_resumed.py"),
        terminal_size=SNAPSHOT_TERMINAL_SIZE,
    )


def test_streaming_mid(snap_compare):
    """Assistant message frozen ~50% through streaming a paragraph."""
    assert snap_compare(
        _script("streaming_mid.py"),
        terminal_size=SNAPSHOT_TERMINAL_SIZE,
    )


def test_tool_call_running(snap_compare):
    """Tool call in running state with a frozen spinner frame."""
    assert snap_compare(
        _script("tool_call_running.py"),
        terminal_size=SNAPSHOT_TERMINAL_SIZE,
    )


def test_tool_result_ok_default(snap_compare):
    """Tool result — success, DEFAULT output style."""
    assert snap_compare(
        _script("tool_result_ok_default.py"),
        terminal_size=SNAPSHOT_TERMINAL_SIZE,
    )


def test_tool_result_err_default(snap_compare):
    """Tool result — error, DEFAULT output style."""
    assert snap_compare(
        _script("tool_result_err_default.py"),
        terminal_size=SNAPSHOT_TERMINAL_SIZE,
    )


def test_tool_result_ok_concise(snap_compare):
    """Tool result — success, CONCISE output style."""
    assert snap_compare(
        _script("tool_result_ok_concise.py"),
        terminal_size=SNAPSHOT_TERMINAL_SIZE,
    )


def test_tool_result_ok_verbose(snap_compare):
    """Tool result — success, VERBOSE output style."""
    assert snap_compare(
        _script("tool_result_ok_verbose.py"),
        terminal_size=SNAPSHOT_TERMINAL_SIZE,
    )


def test_permission_modal(snap_compare):
    """Permission modal overlaid on the welcome screen."""
    assert snap_compare(
        _script("permission_modal.py"),
        terminal_size=SNAPSHOT_TERMINAL_SIZE,
    )


def test_command_palette(snap_compare):
    """Command palette opened with filter 'mem'."""
    assert snap_compare(
        _script("command_palette.py"),
        terminal_size=SNAPSHOT_TERMINAL_SIZE,
    )
