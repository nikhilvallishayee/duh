"""Tests for the TUI permission modal and TUIApprover (ADR-066 P1).

Covers:
- PermissionModal composition and button-press dispatch
- TUIApprover cache hits (allow/deny) bypass the modal
- TUIApprover records decisions into SessionPermissionCache
- TUIApprover returns correct allow/deny dicts
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Skip entire module when textual is not installed
textual = pytest.importorskip("textual", reason="textual not installed")

from duh.kernel.permission_cache import SessionPermissionCache  # noqa: E402
from duh.ui.permission_modal import PermissionModal  # noqa: E402
from duh.ui.tui_approver import TUIApprover  # noqa: E402


# ---------------------------------------------------------------------------
# PermissionModal
# ---------------------------------------------------------------------------


class TestPermissionModal:
    def test_modal_stores_tool_name(self):
        modal = PermissionModal("Bash", {"command": "ls"})
        assert modal._tool_name == "Bash"

    def test_modal_truncates_long_input(self):
        long_input = {"data": "x" * 500}
        modal = PermissionModal("Write", long_input)
        assert len(modal._input_preview) <= 200

    def test_modal_short_input_not_truncated(self):
        short_input = {"path": "/tmp/foo"}
        modal = PermissionModal("Read", short_input)
        assert "path" in modal._input_preview

    def test_modal_is_modal_screen(self):
        from textual.screen import ModalScreen

        modal = PermissionModal("Edit", {})
        assert isinstance(modal, ModalScreen)


# ---------------------------------------------------------------------------
# TUIApprover — cache behavior (no modal needed)
# ---------------------------------------------------------------------------


class TestTUIApproverCache:
    @pytest.mark.asyncio
    async def test_cache_allow_returns_immediately(self):
        """When the cache says 'allow', the modal is never shown."""
        cache = SessionPermissionCache()
        cache.record("Bash", "a")  # always allow

        app = MagicMock()
        approver = TUIApprover(app=app, permission_cache=cache)
        result = await approver.check("Bash", {"command": "ls"})

        assert result == {"allowed": True}
        app.push_screen_wait.assert_not_called()

    @pytest.mark.asyncio
    async def test_cache_deny_returns_immediately(self):
        """When the cache says 'deny', the modal is never shown."""
        cache = SessionPermissionCache()
        cache.record("Bash", "N")  # never allow

        app = MagicMock()
        approver = TUIApprover(app=app, permission_cache=cache)
        result = await approver.check("Bash", {"command": "rm -rf /"})

        assert result["allowed"] is False
        assert "cached" in result["reason"]
        app.push_screen_wait.assert_not_called()

    @pytest.mark.asyncio
    async def test_creates_default_cache_when_none_given(self):
        """When no cache is provided, TUIApprover creates its own."""
        app = MagicMock()
        app.push_screen_wait = AsyncMock(return_value="y")
        approver = TUIApprover(app=app, permission_cache=None)

        assert approver._cache is not None
        assert isinstance(approver._cache, SessionPermissionCache)


# ---------------------------------------------------------------------------
# TUIApprover — modal interaction (mocked push_screen_wait)
# ---------------------------------------------------------------------------


class TestTUIApproverModal:
    @pytest.mark.asyncio
    async def test_yes_allows_tool(self):
        app = MagicMock()
        app.push_screen_wait = AsyncMock(return_value="y")
        approver = TUIApprover(app=app)

        result = await approver.check("Bash", {"command": "echo hi"})
        assert result == {"allowed": True}

    @pytest.mark.asyncio
    async def test_always_allows_tool(self):
        app = MagicMock()
        app.push_screen_wait = AsyncMock(return_value="a")
        approver = TUIApprover(app=app)

        result = await approver.check("Bash", {"command": "echo hi"})
        assert result == {"allowed": True}

    @pytest.mark.asyncio
    async def test_no_denies_tool(self):
        app = MagicMock()
        app.push_screen_wait = AsyncMock(return_value="n")
        approver = TUIApprover(app=app)

        result = await approver.check("Bash", {"command": "echo hi"})
        assert result["allowed"] is False
        assert "denied" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_never_denies_tool(self):
        app = MagicMock()
        app.push_screen_wait = AsyncMock(return_value="N")
        approver = TUIApprover(app=app)

        result = await approver.check("Bash", {"command": "echo hi"})
        assert result["allowed"] is False

    @pytest.mark.asyncio
    async def test_always_caches_for_future_calls(self):
        """After 'always', subsequent calls should not show the modal."""
        app = MagicMock()
        app.push_screen_wait = AsyncMock(return_value="a")
        approver = TUIApprover(app=app)

        # First call: modal is shown
        result1 = await approver.check("Read", {"path": "/tmp/x"})
        assert result1["allowed"] is True
        assert app.push_screen_wait.call_count == 1

        # Second call: cache hit, modal NOT shown
        result2 = await approver.check("Read", {"path": "/tmp/y"})
        assert result2["allowed"] is True
        assert app.push_screen_wait.call_count == 1  # still 1

    @pytest.mark.asyncio
    async def test_never_caches_for_future_calls(self):
        """After 'never', subsequent calls should not show the modal."""
        app = MagicMock()
        app.push_screen_wait = AsyncMock(return_value="N")
        approver = TUIApprover(app=app)

        # First call: modal is shown
        result1 = await approver.check("Bash", {"command": "rm -rf /"})
        assert result1["allowed"] is False
        assert app.push_screen_wait.call_count == 1

        # Second call: cache hit, modal NOT shown
        result2 = await approver.check("Bash", {"command": "ls"})
        assert result2["allowed"] is False
        assert app.push_screen_wait.call_count == 1  # still 1

    @pytest.mark.asyncio
    async def test_yes_does_not_cache(self):
        """A 'yes' is one-time — next call should show the modal again."""
        app = MagicMock()
        app.push_screen_wait = AsyncMock(return_value="y")
        approver = TUIApprover(app=app)

        await approver.check("Edit", {"path": "/tmp/a"})
        await approver.check("Edit", {"path": "/tmp/b"})

        # Modal shown twice (y is one-time, not cached)
        assert app.push_screen_wait.call_count == 2

    @pytest.mark.asyncio
    async def test_no_does_not_cache(self):
        """A 'no' is one-time — next call should show the modal again."""
        app = MagicMock()
        app.push_screen_wait = AsyncMock(return_value="n")
        approver = TUIApprover(app=app)

        await approver.check("Write", {"path": "/tmp/a"})
        await approver.check("Write", {"path": "/tmp/b"})

        # Modal shown twice (n is one-time, not cached)
        assert app.push_screen_wait.call_count == 2

    @pytest.mark.asyncio
    async def test_different_tools_cached_independently(self):
        """'always' on one tool should not affect another tool."""
        app = MagicMock()
        app.push_screen_wait = AsyncMock(return_value="a")
        approver = TUIApprover(app=app)

        await approver.check("Bash", {"command": "ls"})
        await approver.check("Write", {"path": "/tmp/x"})

        # Both showed the modal (different tool names)
        assert app.push_screen_wait.call_count == 2

    @pytest.mark.asyncio
    async def test_push_screen_wait_receives_permission_modal(self):
        """Verify the modal passed to push_screen_wait is a PermissionModal."""
        app = MagicMock()
        app.push_screen_wait = AsyncMock(return_value="y")
        approver = TUIApprover(app=app)

        await approver.check("Bash", {"command": "ls"})

        args, kwargs = app.push_screen_wait.call_args
        modal = args[0]
        assert isinstance(modal, PermissionModal)
        assert modal._tool_name == "Bash"


# ---------------------------------------------------------------------------
# PermissionModal — Textual run_test (button presses via callback)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestPermissionModalRunTest:
    async def test_yes_button_dismisses_with_y(self):
        from textual.app import App, ComposeResult

        class TestApp(App):
            result: str = ""

            def compose(self) -> ComposeResult:
                yield textual.widgets.Static("base")

            def on_mount(self) -> None:
                def _on_dismiss(value: str) -> None:
                    self.result = value
                    self.exit()

                self.push_screen(
                    PermissionModal("Bash", {"command": "ls"}),
                    callback=_on_dismiss,
                )

        app = TestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            await pilot.click("#yes")
            await pilot.pause(0.1)

        assert app.result == "y"

    async def test_always_button_dismisses_with_a(self):
        from textual.app import App, ComposeResult

        class TestApp(App):
            result: str = ""

            def compose(self) -> ComposeResult:
                yield textual.widgets.Static("base")

            def on_mount(self) -> None:
                def _on_dismiss(value: str) -> None:
                    self.result = value
                    self.exit()

                self.push_screen(
                    PermissionModal("Read", {"path": "/tmp"}),
                    callback=_on_dismiss,
                )

        app = TestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            await pilot.click("#always")
            await pilot.pause(0.1)

        assert app.result == "a"

    async def test_no_button_dismisses_with_n(self):
        from textual.app import App, ComposeResult

        class TestApp(App):
            result: str = ""

            def compose(self) -> ComposeResult:
                yield textual.widgets.Static("base")

            def on_mount(self) -> None:
                def _on_dismiss(value: str) -> None:
                    self.result = value
                    self.exit()

                self.push_screen(
                    PermissionModal("Write", {}),
                    callback=_on_dismiss,
                )

        app = TestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            await pilot.click("#no")
            await pilot.pause(0.1)

        assert app.result == "n"

    async def test_never_button_dismisses_with_N(self):
        from textual.app import App, ComposeResult

        class TestApp(App):
            result: str = ""

            def compose(self) -> ComposeResult:
                yield textual.widgets.Static("base")

            def on_mount(self) -> None:
                def _on_dismiss(value: str) -> None:
                    self.result = value
                    self.exit()

                self.push_screen(
                    PermissionModal("Bash", {"command": "rm -rf /"}),
                    callback=_on_dismiss,
                )

        app = TestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            await pilot.click("#never")
            await pilot.pause(0.1)

        assert app.result == "N"

    async def test_modal_shows_tool_name(self):
        from textual.app import App, ComposeResult

        class TestApp(App):
            def compose(self) -> ComposeResult:
                yield textual.widgets.Static("base")

            def on_mount(self) -> None:
                self.push_screen(PermissionModal("MyTool", {"key": "val"}))

        app = TestApp()
        async with app.run_test(size=(80, 24)) as pilot:
            await pilot.pause(0.3)
            # Query the active screen (the modal), not the app's default screen
            screen = app.screen
            tool_label = screen.query_one("#permission-tool")
            assert "MyTool" in str(tool_label.render())
            app.exit()
