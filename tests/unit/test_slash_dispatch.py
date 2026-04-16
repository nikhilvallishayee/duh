"""Tests for the SlashDispatcher — CQ-1 refactoring of _handle_slash.

Validates:
1. Each command dispatches to the correct handler method
2. Unknown commands return an error message
3. The dispatch table is extensible (adding a new entry works)
4. SlashContext bundles the right state
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from duh.cli.slash_commands import SlashContext, SlashDispatcher
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_engine() -> Engine:
    """Minimal engine stub for tests."""
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
    """Build a SlashContext with sensible defaults, allowing overrides."""
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


# ===========================================================================
# Test 1: Each known command dispatches to the correct handler
# ===========================================================================


class TestDispatchRouting:
    """Verify that each registered command routes to its handler method."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "/help",
            "/cost",
            "/status",
            "/clear",
            "/exit",
            "/compact-stats",
        ],
    )
    def test_known_simple_commands_dispatch(self, cmd: str, capsys: Any) -> None:
        """Simple commands that need no special args should dispatch cleanly."""
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch(cmd, "")
        # All except /exit should return keep=True
        if cmd == "/exit":
            assert keep is False
        else:
            assert keep is True

    def test_help_prints_commands(self, capsys: Any) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/help", "")
        assert keep is True
        assert model == "test-model"
        captured = capsys.readouterr()
        assert "/help" in captured.out
        assert "/exit" in captured.out

    def test_cost_prints_summary(self, capsys: Any) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/cost", "")
        assert keep is True
        captured = capsys.readouterr()
        assert captured.out.strip()  # should produce some output

    def test_status_prints_session(self, capsys: Any) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/status", "")
        assert keep is True
        captured = capsys.readouterr()
        assert "Session:" in captured.out
        assert "Turns:" in captured.out
        assert "Model:" in captured.out

    def test_clear_empties_messages(self, capsys: Any) -> None:
        engine = _make_engine()
        from duh.kernel.messages import Message
        engine._messages.append(Message(role="user", content="hello"))
        assert len(engine.messages) == 1
        ctx = _make_ctx(engine=engine)
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/clear", "")
        assert keep is True
        assert len(engine.messages) == 0
        captured = capsys.readouterr()
        assert "Conversation cleared" in captured.out

    def test_exit_returns_false(self) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/exit", "")
        assert keep is False
        assert model == "test-model"

    def test_search_no_arg_shows_usage(self, capsys: Any) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/search", "")
        assert keep is True
        captured = capsys.readouterr()
        assert "Usage:" in captured.out

    def test_tasks_no_manager(self, capsys: Any) -> None:
        ctx = _make_ctx(task_manager=None)
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/tasks", "")
        assert keep is True
        captured = capsys.readouterr()
        assert "No tasks" in captured.out

    def test_tasks_with_manager(self, capsys: Any) -> None:
        mgr = SimpleNamespace(summary=lambda: "2 tasks: 1 done, 1 pending")
        ctx = _make_ctx(task_manager=mgr)
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/tasks", "")
        assert keep is True
        captured = capsys.readouterr()
        assert "2 tasks" in captured.out

    def test_undo_no_executor(self, capsys: Any) -> None:
        ctx = _make_ctx(executor=None)
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/undo", "")
        assert keep is True
        captured = capsys.readouterr()
        assert "No executor" in captured.out

    def test_changes_no_executor(self, capsys: Any) -> None:
        ctx = _make_ctx(executor=None)
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/changes", "")
        assert keep is True
        captured = capsys.readouterr()
        assert "No file tracker" in captured.out

    def test_compact_with_compactor(self) -> None:
        deps = _make_deps(compact=MagicMock())
        ctx = _make_ctx(deps=deps)
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/compact", "")
        assert keep is True
        assert model == "\x00compact\x00"

    def test_compact_without_compactor(self, capsys: Any) -> None:
        deps = _make_deps(compact=None)
        ctx = _make_ctx(deps=deps)
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/compact", "")
        assert keep is True
        assert model == "test-model"
        captured = capsys.readouterr()
        assert "No compactor" in captured.out

    def test_snapshot_returns_sentinel(self) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/snapshot", "apply")
        assert keep is True
        assert model == "\x00snapshot\x00apply"

    def test_plan_no_plan_mode(self, capsys: Any) -> None:
        ctx = _make_ctx(plan_mode=None)
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/plan", "show")
        assert keep is True
        captured = capsys.readouterr()
        assert "Plan mode not available" in captured.out

    def test_attach_no_arg(self, capsys: Any) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/attach", "")
        assert keep is True
        captured = capsys.readouterr()
        assert "Usage:" in captured.out

    def test_brief_toggle_on(self, capsys: Any) -> None:
        engine = _make_engine()
        ctx = _make_ctx(engine=engine)
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/brief", "on")
        assert keep is True
        captured = capsys.readouterr()
        assert "Brief mode: ON" in captured.out

    def test_compact_stats(self, capsys: Any) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/compact-stats", "")
        assert keep is True
        # Just verify it doesn't crash and produces output
        captured = capsys.readouterr()
        assert captured.out.strip()


# ===========================================================================
# Test 2: Unknown commands return an error message
# ===========================================================================


class TestUnknownCommand:
    def test_unknown_command_returns_error(self, capsys: Any) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/nonexistent", "")
        assert keep is True
        assert model == "test-model"
        captured = capsys.readouterr()
        assert "Unknown command: /nonexistent" in captured.out

    def test_unknown_command_suggests_help(self, capsys: Any) -> None:
        ctx = _make_ctx()
        dispatcher = SlashDispatcher(ctx)
        keep, model = dispatcher.dispatch("/foobar", "abc")
        captured = capsys.readouterr()
        assert "/help" in captured.out


# ===========================================================================
# Test 3: Dispatch table is extensible
# ===========================================================================


class TestDispatchExtensibility:
    def test_register_new_command(self, capsys: Any) -> None:
        """Adding a new entry to _HANDLERS makes it dispatchable."""
        calls: list[str] = []

        def _custom_handler(self: SlashDispatcher, arg: str) -> tuple[bool, str]:
            calls.append(arg)
            sys.stdout.write(f"  Custom: {arg}\n")
            return True, self.ctx.model

        # Register the custom handler
        original_handlers = SlashDispatcher._HANDLERS.copy()
        SlashDispatcher._HANDLERS["/custom"] = _custom_handler
        try:
            ctx = _make_ctx()
            dispatcher = SlashDispatcher(ctx)
            keep, model = dispatcher.dispatch("/custom", "hello world")
            assert keep is True
            assert model == "test-model"
            assert calls == ["hello world"]
            captured = capsys.readouterr()
            assert "Custom: hello world" in captured.out
        finally:
            # Restore original handlers
            SlashDispatcher._HANDLERS = original_handlers

    def test_override_existing_command(self, capsys: Any) -> None:
        """Overriding an existing handler works without affecting others."""
        def _new_help(self: SlashDispatcher, arg: str) -> tuple[bool, str]:
            sys.stdout.write("  Custom help!\n")
            return True, self.ctx.model

        original_handlers = SlashDispatcher._HANDLERS.copy()
        SlashDispatcher._HANDLERS["/help"] = _new_help
        try:
            ctx = _make_ctx()
            dispatcher = SlashDispatcher(ctx)
            keep, model = dispatcher.dispatch("/help", "")
            captured = capsys.readouterr()
            assert "Custom help!" in captured.out

            # Other commands still work
            keep2, _ = dispatcher.dispatch("/status", "")
            assert keep2 is True
        finally:
            SlashDispatcher._HANDLERS = original_handlers

    def test_all_slash_commands_have_handlers(self) -> None:
        """Every command in SLASH_COMMANDS dict has a corresponding handler."""
        from duh.cli.repl import SLASH_COMMANDS

        for cmd_name in SLASH_COMMANDS:
            assert cmd_name in SlashDispatcher._HANDLERS, (
                f"SLASH_COMMANDS has {cmd_name} but SlashDispatcher._HANDLERS does not"
            )

    def test_handler_count_matches_known_commands(self) -> None:
        """The handler table has entries for all documented commands."""
        from duh.cli.repl import SLASH_COMMANDS

        # Dispatch table should have at least as many entries as SLASH_COMMANDS
        assert len(SlashDispatcher._HANDLERS) >= len(SLASH_COMMANDS)


# ===========================================================================
# Test 4: SlashContext holds the right state
# ===========================================================================


class TestSlashContext:
    def test_context_fields(self) -> None:
        engine = _make_engine()
        deps = _make_deps()
        ctx = SlashContext(
            engine=engine,
            model="claude-sonnet-4-6",
            deps=deps,
            provider_name="anthropic",
        )
        assert ctx.engine is engine
        assert ctx.model == "claude-sonnet-4-6"
        assert ctx.deps is deps
        assert ctx.provider_name == "anthropic"
        assert ctx.executor is None
        assert ctx.task_manager is None
        assert ctx.template_state == {}
        assert ctx.plan_mode is None
        assert ctx.mcp_executor is None

    def test_context_with_all_fields(self) -> None:
        engine = _make_engine()
        deps = _make_deps()
        executor = SimpleNamespace(name="fake_executor")
        task_mgr = SimpleNamespace(summary=lambda: "tasks")
        tmpl_state = {"templates": {}, "active": None}

        ctx = SlashContext(
            engine=engine,
            model="test",
            deps=deps,
            executor=executor,
            task_manager=task_mgr,
            template_state=tmpl_state,
            provider_name="ollama",
        )
        assert ctx.executor is executor
        assert ctx.task_manager is task_mgr
        assert ctx.template_state is tmpl_state
        assert ctx.provider_name == "ollama"


# ===========================================================================
# Test 5: Backward compatibility — _handle_slash still works
# ===========================================================================


class TestBackwardCompatibility:
    """Ensure _handle_slash from repl.py still delegates properly."""

    def test_handle_slash_delegates_to_dispatcher(self, capsys: Any) -> None:
        from duh.cli.repl import _handle_slash

        engine = _make_engine()
        deps = _make_deps()
        keep, model = _handle_slash("/help", engine, "test-model", deps)
        assert keep is True
        assert model == "test-model"
        captured = capsys.readouterr()
        assert "/help" in captured.out

    def test_handle_slash_unknown(self, capsys: Any) -> None:
        from duh.cli.repl import _handle_slash

        engine = _make_engine()
        deps = _make_deps()
        keep, model = _handle_slash("/bogus", engine, "test-model", deps)
        assert keep is True
        captured = capsys.readouterr()
        assert "Unknown command" in captured.out

    def test_handle_slash_exit(self) -> None:
        from duh.cli.repl import _handle_slash

        engine = _make_engine()
        deps = _make_deps()
        keep, model = _handle_slash("/exit", engine, "test-model", deps)
        assert keep is False
