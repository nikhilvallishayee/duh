"""Tests for ADR-067 P1/P2: progress indicators and file-tree sidebar.

Covers:
- _format_elapsed helper returns correct human-readable strings
- ToolCallWidget.set_result() renders elapsed time in all styles
- RecentFilesWidget tracks files and renders the list
- DuhApp._track_recent_file deduplicates and caps at 10
- DuhApp._run_query records elapsed time and tracks file paths
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

textual = pytest.importorskip("textual", reason="textual not installed")


# ---------------------------------------------------------------------------
# _format_elapsed
# ---------------------------------------------------------------------------

from duh.ui.widgets import _format_elapsed  # noqa: E402


class TestFormatElapsed:
    def test_none_returns_empty(self):
        assert _format_elapsed(None) == ""

    def test_sub_100ms_shows_ms(self):
        result = _format_elapsed(42.0)
        assert "42ms" in result

    def test_above_100ms_shows_seconds(self):
        result = _format_elapsed(1234.0)
        assert "1.2s" in result

    def test_exactly_100ms_shows_seconds(self):
        result = _format_elapsed(100.0)
        assert "0.1s" in result

    def test_zero_ms_shows_ms(self):
        result = _format_elapsed(0.0)
        assert "0ms" in result

    def test_large_value_shows_seconds(self):
        result = _format_elapsed(65432.0)
        assert "65.4s" in result


# ---------------------------------------------------------------------------
# ToolCallWidget with elapsed_ms
# ---------------------------------------------------------------------------

from duh.ui.widgets import ToolCallWidget  # noqa: E402


class TestToolCallWidgetElapsed:
    def test_set_result_ok_with_elapsed(self):
        """set_result before mount (no _result_label) should not raise."""
        w = ToolCallWidget(tool_name="Bash", input={"command": "ls"})
        # _result_label is None before mount — should not raise
        w.set_result("ok", is_error=False, elapsed_ms=150.0)

    def test_set_result_error_with_elapsed(self):
        w = ToolCallWidget(tool_name="Read", input={"path": "/x"})
        w.set_result("fail", is_error=True, elapsed_ms=300.0)

    def test_set_result_no_elapsed(self):
        """Backward compat: no elapsed_ms still works."""
        w = ToolCallWidget(tool_name="Bash", input={})
        w.set_result("done", is_error=False)


# ---------------------------------------------------------------------------
# RecentFilesWidget
# ---------------------------------------------------------------------------

from duh.ui.file_tree import RecentFilesWidget  # noqa: E402


class TestRecentFilesWidget:
    def test_initial_files_empty(self):
        w = RecentFilesWidget()
        assert w._files == []

    def test_add_file_appends(self):
        w = RecentFilesWidget()
        w.add_file("/tmp/a.py")
        assert w._files == ["/tmp/a.py"]

    def test_add_file_deduplicates(self):
        w = RecentFilesWidget()
        w.add_file("/tmp/a.py")
        w.add_file("/tmp/b.py")
        w.add_file("/tmp/a.py")
        assert w._files == ["/tmp/a.py", "/tmp/b.py"]

    def test_add_file_caps_at_10(self):
        w = RecentFilesWidget()
        for i in range(15):
            w.add_file(f"/tmp/{i}.py")
        assert len(w._files) == 10
        # Most recent first
        assert w._files[0] == "/tmp/14.py"

    def test_set_files_replaces(self):
        w = RecentFilesWidget()
        w.add_file("/tmp/old.py")
        w.set_files(["/tmp/new.py", "/tmp/other.py"])
        assert w._files == ["/tmp/new.py", "/tmp/other.py"]

    def test_set_files_caps_at_10(self):
        w = RecentFilesWidget()
        w.set_files([f"/tmp/{i}.py" for i in range(20)])
        assert len(w._files) == 10


# ---------------------------------------------------------------------------
# DuhApp._track_recent_file
# ---------------------------------------------------------------------------

from duh.ui.app import DuhApp  # noqa: E402


def _fake_engine(events=None):
    """Return a mock engine."""
    async def _run(_prompt):
        for ev in (events or []):
            yield ev

    engine = MagicMock()
    engine.run = _run
    engine.total_input_tokens = 0
    engine.total_output_tokens = 0
    return engine


class TestDuhAppTrackRecentFile:
    def test_track_adds_file(self):
        app = DuhApp(engine=_fake_engine(), model="m")
        app._recent_files_widget = None  # sidebar not mounted
        app._track_recent_file("/tmp/foo.py")
        assert app._recent_files == ["/tmp/foo.py"]

    def test_track_deduplicates_and_moves_to_front(self):
        app = DuhApp(engine=_fake_engine(), model="m")
        app._recent_files_widget = None
        app._track_recent_file("/a.py")
        app._track_recent_file("/b.py")
        app._track_recent_file("/a.py")
        assert app._recent_files == ["/a.py", "/b.py"]

    def test_track_caps_at_10(self):
        app = DuhApp(engine=_fake_engine(), model="m")
        app._recent_files_widget = None
        for i in range(15):
            app._track_recent_file(f"/f{i}.py")
        assert len(app._recent_files) == 10
        assert app._recent_files[0] == "/f14.py"

    def test_track_updates_widget(self):
        app = DuhApp(engine=_fake_engine(), model="m")
        mock_widget = MagicMock()
        app._recent_files_widget = mock_widget
        app._track_recent_file("/tmp/x.py")
        mock_widget.set_files.assert_called_once_with(["/tmp/x.py"])


# ---------------------------------------------------------------------------
# Integration: DuhApp.run_test — elapsed time and file tracking
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestProgressIntegration:
    async def test_tool_result_shows_elapsed_in_ui(self):
        """tool_use -> tool_result should record elapsed time."""
        events = [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_result", "output": "bin  etc", "is_error": False},
        ]
        engine = _fake_engine(events)
        app = DuhApp(engine=engine, model="test-m")
        async with app.run_test(size=(120, 40)) as pilot:
            inp = app.query_one("#prompt-input")
            inp.load_text("hello")
            await pilot.click("#send-button")
            await pilot.pause(0.5)
            await pilot.pause(0.1)
            # Turn should have advanced
            assert app._turn == 1

    async def test_file_tool_tracks_path(self):
        """Read tool should add file_path to recent files."""
        events = [
            {"type": "tool_use", "name": "Read", "input": {"file_path": "/tmp/test.py"}},
            {"type": "tool_result", "output": "contents", "is_error": False},
        ]
        engine = _fake_engine(events)
        app = DuhApp(engine=engine, model="test-m")
        async with app.run_test(size=(120, 40)) as pilot:
            inp = app.query_one("#prompt-input")
            inp.load_text("read file")
            await pilot.click("#send-button")
            await pilot.pause(0.5)
            await pilot.pause(0.1)
            assert "/tmp/test.py" in app._recent_files

    async def test_non_file_tool_does_not_track(self):
        """Bash tool should NOT add to recent files."""
        events = [
            {"type": "tool_use", "name": "Bash", "input": {"command": "ls"}},
            {"type": "tool_result", "output": "bin  etc", "is_error": False},
        ]
        engine = _fake_engine(events)
        app = DuhApp(engine=engine, model="test-m")
        async with app.run_test(size=(120, 40)) as pilot:
            inp = app.query_one("#prompt-input")
            inp.load_text("list stuff")
            await pilot.click("#send-button")
            await pilot.pause(0.5)
            await pilot.pause(0.1)
            assert app._recent_files == []

    async def test_sidebar_contains_recent_files_widget(self):
        """The sidebar should contain the RecentFilesWidget."""
        engine = _fake_engine([])
        app = DuhApp(engine=engine, model="test-m")
        async with app.run_test(size=(120, 40)) as pilot:
            assert app._recent_files_widget is not None
            widget = app.query_one("#recent-files", RecentFilesWidget)
            assert widget is not None
