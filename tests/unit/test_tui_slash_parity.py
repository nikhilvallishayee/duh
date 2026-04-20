"""Tests for ADR-073 Wave 1 TUI slash-command parity.

The TUI no longer reimplements slash commands inline — it delegates to
:class:`duh.cli.slash_commands.SlashDispatcher` via ``async_dispatch``.  Only
``/style``, ``/mode``, ``/session`` (a TUI-only info panel) and the
``/quit``/``/q`` aliases stay local to ``DuhApp``.

These tests verify:

1. ``SlashDispatcher.async_dispatch`` returns ``(output, new_model)`` and
   routes to the right handlers (sync path via stdout capture, async path
   for ``/sessions``).
2. The sync ``dispatch`` method still works identically (REPL untouched).
3. The TUI's ``_handle_slash`` delegates to ``async_dispatch`` for shared
   commands and keeps ``/style`` / ``/mode`` / ``/session`` local.
4. Unknown commands render an error into the message log.
5. ``/compact``, ``/plan``, ``/snapshot`` sentinel returns are handled.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli.slash_commands import SlashContext, SlashDispatcher
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig

textual = pytest.importorskip("textual", reason="textual not installed")

from textual.widgets import Static  # noqa: E402

from duh.ui.app import DuhApp  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_engine() -> Engine:
    cfg = EngineConfig(
        model="test-model",
        system_prompt="You are a test assistant.",
        tools=[],
    )
    return Engine(cfg)


def _make_deps(**overrides: Any) -> Deps:
    return Deps(
        call_model=overrides.get("call_model", MagicMock()),
        run_tool=overrides.get("run_tool", MagicMock()),
        approve=overrides.get("approve", MagicMock()),
        compact=overrides.get("compact", None),
    )


def _make_ctx(**overrides: Any) -> SlashContext:
    return SlashContext(
        engine=overrides.get("engine", _make_engine()),
        model=overrides.get("model", "test-model"),
        deps=overrides.get("deps", _make_deps()),
        executor=overrides.get("executor", None),
        task_manager=overrides.get("task_manager", None),
        template_state=overrides.get("template_state", {}),
        plan_mode=overrides.get("plan_mode", None),
        mcp_executor=overrides.get("mcp_executor", None),
        provider_name=overrides.get("provider_name", ""),
    )


def _fake_tui_engine() -> MagicMock:
    """Build a mock engine suitable for DuhApp construction + slash dispatch."""

    async def _run(_prompt: str):
        if False:
            yield {}  # pragma: no cover — never invoked

    engine = MagicMock()
    engine.run = _run
    engine.total_input_tokens = 0
    engine.total_output_tokens = 0
    engine.session_id = "test-session"
    engine._messages = []
    engine._session_store = None
    engine._deps = _make_deps()

    # Real EngineConfig so attribute access (system_prompt etc.) works.
    engine._config = EngineConfig(
        model="test-model",
        system_prompt="You are a test assistant.",
        tools=[],
    )
    engine.cost_summary = lambda model: f"cost for {model}: $0.0000"
    engine.turn_count = 0

    # context_breakdown depends on these two.  Use simple ints so the REPL
    # helper doesn't crash.
    class _FakeCompactStats:
        def summary(self):
            return "no compaction"

    engine.compact_stats = _FakeCompactStats()

    class _FakeCacheTracker:
        def summary(self):
            return "cache: 0 hits"

    engine.cache_tracker = _FakeCacheTracker()
    return engine


# ===========================================================================
# 1. SlashDispatcher.async_dispatch contract
# ===========================================================================


@pytest.mark.asyncio
class TestAsyncDispatchContract:
    """async_dispatch returns (output, new_model) and captures handler output."""

    async def test_async_dispatch_help_returns_output(self) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        output, model = await dispatcher.async_dispatch("/help", "")
        assert model == "test-model"
        assert "/help" in output
        assert "/exit" in output

    async def test_async_dispatch_cost_returns_summary(self) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        output, model = await dispatcher.async_dispatch("/cost", "")
        assert model == "test-model"
        assert output.strip()  # some output was captured

    async def test_async_dispatch_unknown_command(self) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        output, model = await dispatcher.async_dispatch("/bogus", "")
        assert model == "test-model"
        assert "Unknown command: /bogus" in output

    async def test_async_dispatch_compact_sentinel(self) -> None:
        deps = _make_deps(compact=MagicMock())
        ctx = _make_ctx(deps=deps)
        dispatcher = SlashDispatcher(ctx)
        output, model = await dispatcher.async_dispatch("/compact", "")
        assert model == "\x00compact\x00"

    async def test_async_dispatch_snapshot_sentinel(self) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        output, model = await dispatcher.async_dispatch("/snapshot", "apply")
        assert model == "\x00snapshot\x00apply"

    async def test_async_dispatch_sessions_uses_async_variant(self) -> None:
        """The async variant must await store.list_sessions() directly.

        The sync handler calls ``asyncio.run``, which raises ``RuntimeError``
        inside a running loop.  The async variant must work regardless.
        """
        fake_store = SimpleNamespace()
        fake_store.list_sessions = AsyncMock(return_value=[])
        engine = _make_engine()
        engine._session_store = fake_store
        ctx = _make_ctx(engine=engine)
        dispatcher = SlashDispatcher(ctx)
        output, model = await dispatcher.async_dispatch("/sessions", "")
        assert model == "test-model"
        assert "No sessions" in output
        fake_store.list_sessions.assert_awaited_once()


# ===========================================================================
# 2. Sync dispatch remains identical (REPL-side backward compatibility)
# ===========================================================================


class TestSyncDispatchUnchanged:
    """The sync path used by the REPL must keep working."""

    def test_sync_dispatch_help_still_works(self, capsys: Any) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/help", "")
        assert keep is True
        assert model == "test-model"
        captured = capsys.readouterr()
        assert "/help" in captured.out

    def test_sync_dispatch_exit_returns_false(self) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/exit", "")
        assert keep is False


# ===========================================================================
# 3. TUI _handle_slash delegates to the shared dispatcher
# ===========================================================================


@pytest.mark.asyncio
class TestTuiHandleSlashDelegation:
    """TUI calls SlashDispatcher.async_dispatch for shared commands."""

    async def test_handle_slash_calls_async_dispatch(self) -> None:
        """A known shared command routes through async_dispatch."""
        engine = _fake_tui_engine()
        app = DuhApp(engine=engine, model="test-model", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            with patch.object(
                SlashDispatcher,
                "async_dispatch",
                new=AsyncMock(return_value=("  shared handler output\n", "test-model")),
            ) as mock_dispatch:
                handled = await app._handle_slash("/help")
                assert handled is True
                mock_dispatch.assert_awaited_once()
                # Inspect the call args: name=/help, arg=""
                call = mock_dispatch.call_args
                # async_dispatch is called as method — args = (name, arg)
                assert call.args[0] == "/help"
                assert call.args[1] == ""

    async def test_handle_slash_renders_output_in_log(self) -> None:
        """Output captured from the dispatcher is mounted into the message log."""
        engine = _fake_tui_engine()
        app = DuhApp(engine=engine, model="test-model", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            with patch.object(
                SlashDispatcher,
                "async_dispatch",
                new=AsyncMock(return_value=("  hello from the dispatcher\n", "test-model")),
            ):
                log = app.query_one("#message-log")
                before = len(list(log.children))
                await app._handle_slash("/health")
                await pilot.pause()
                after = len(list(log.children))
                assert after > before
                # Verify the last added child contains the dispatcher output.
                last = list(log.children)[-1]
                # Static renders its text through render() -> Content.
                rendered = str(last.render()) if hasattr(last, "render") else ""
                assert "hello from the dispatcher" in rendered

    async def test_unknown_command_surfaces_error_in_log(self) -> None:
        """Unknown commands get an 'Unknown command' message in the log."""
        engine = _fake_tui_engine()
        app = DuhApp(engine=engine, model="test-model", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            log = app.query_one("#message-log")
            before = len(list(log.children))
            await app._handle_slash("/definitely-not-a-command")
            await pilot.pause()
            after = len(list(log.children))
            assert after > before
            last = list(log.children)[-1]
            rendered = str(last.render()) if hasattr(last, "render") else ""
            assert "Unknown command" in rendered


# ===========================================================================
# 4. /style and /mode remain TUI-local
# ===========================================================================


@pytest.mark.asyncio
class TestTuiLocalCommands:
    """/style and /mode must not go through SlashDispatcher.async_dispatch."""

    async def test_style_does_not_call_dispatcher(self) -> None:
        engine = _fake_tui_engine()
        app = DuhApp(engine=engine, model="test-model", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            with patch.object(
                SlashDispatcher,
                "async_dispatch",
                new=AsyncMock(return_value=("should not be called", "")),
            ) as mock_dispatch:
                handled = await app._handle_slash("/style concise")
                assert handled is True
                mock_dispatch.assert_not_awaited()
                # Style flipped
                from duh.ui.styles import OutputStyle
                assert app._output_style == OutputStyle.CONCISE

    async def test_mode_does_not_call_dispatcher(self) -> None:
        engine = _fake_tui_engine()
        app = DuhApp(engine=engine, model="test-model", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            with patch.object(
                SlashDispatcher,
                "async_dispatch",
                new=AsyncMock(return_value=("should not be called", "")),
            ) as mock_dispatch:
                handled = await app._handle_slash("/mode normal")
                assert handled is True
                mock_dispatch.assert_not_awaited()

    async def test_session_info_is_local(self) -> None:
        """/session is a TUI-only info panel — shared dispatcher not used."""
        engine = _fake_tui_engine()
        app = DuhApp(engine=engine, model="test-model", session_id="abc123")
        async with app.run_test(size=(120, 40)) as pilot:
            with patch.object(
                SlashDispatcher,
                "async_dispatch",
                new=AsyncMock(return_value=("should not be called", "")),
            ) as mock_dispatch:
                handled = await app._handle_slash("/session")
                assert handled is True
                mock_dispatch.assert_not_awaited()


# ===========================================================================
# 5. /clear wipes widgets + engine messages (TUI-only extra behavior)
# ===========================================================================


@pytest.mark.asyncio
class TestClearBehaviour:
    async def test_clear_removes_children_and_messages(self) -> None:
        engine = _fake_tui_engine()
        # Populate a fake message so we can check it's cleared.
        from duh.kernel.messages import Message
        engine._messages = [Message(role="user", content="hi")]
        app = DuhApp(engine=engine, model="test-model", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            await app._handle_slash("/clear")
            await pilot.pause()
            # engine messages wiped
            assert engine._messages == []


# ===========================================================================
# 6. /plan and /snapshot sentinel handling
# ===========================================================================


@pytest.mark.asyncio
class TestSentinelHandling:
    async def test_plan_sentinel_starts_plan_flow(self) -> None:
        """``\\x00plan\\x00`` sentinel triggers the plan-mode flow —
        logs ``Planning: <desc>`` and delegates to ``_run_plan_flow``."""
        engine = _fake_tui_engine()
        app = DuhApp(engine=engine, model="test-model", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            with patch.object(
                SlashDispatcher,
                "async_dispatch",
                new=AsyncMock(return_value=("", "\x00plan\x00refactor the tui")),
            ), patch.object(
                DuhApp, "_run_plan_flow", new=AsyncMock(),
            ) as mock_flow:
                await app._handle_slash("/plan refactor the tui")
                await pilot.pause()
                mock_flow.assert_awaited_once_with("refactor the tui")

    async def test_snapshot_sentinel_starts_snapshot_flow(self) -> None:
        """``\\x00snapshot\\x00`` sentinel triggers the snapshot flow."""
        engine = _fake_tui_engine()
        app = DuhApp(engine=engine, model="test-model", session_id="abc")
        async with app.run_test(size=(120, 40)) as pilot:
            with patch.object(
                SlashDispatcher,
                "async_dispatch",
                new=AsyncMock(return_value=("", "\x00snapshot\x00apply")),
            ), patch.object(
                DuhApp, "_run_snapshot_flow", new=AsyncMock(),
            ) as mock_flow:
                await app._handle_slash("/snapshot apply")
                await pilot.pause()
                mock_flow.assert_awaited_once_with("apply")


# ===========================================================================
# 7. SLASH_COMMANDS dict still exposes all commands (parity invariant)
# ===========================================================================


class TestParityInvariant:
    """Every command listed in SLASH_COMMANDS has a SlashDispatcher handler."""

    def test_all_commands_have_handlers(self) -> None:
        from duh.cli.repl import SLASH_COMMANDS

        for cmd in SLASH_COMMANDS:
            assert cmd in SlashDispatcher._HANDLERS, (
                f"{cmd} is documented in SLASH_COMMANDS but missing "
                "from SlashDispatcher._HANDLERS"
            )

    def test_async_handlers_table_populated(self) -> None:
        """Commands that need async dispatch (e.g. /sessions) are registered."""
        assert "/sessions" in SlashDispatcher._ASYNC_HANDLERS
