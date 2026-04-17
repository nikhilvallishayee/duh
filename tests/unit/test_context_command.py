"""Tests for /context in both TUI and REPL: listed in help, contains expected fields."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from duh.cli.repl import SLASH_COMMANDS, _handle_slash, context_breakdown
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeTool:
    """Minimal tool stub."""

    def __init__(self, name: str, description: str, input_schema: dict):
        self.name = name
        self.description = description
        self.input_schema = input_schema


def _make_engine(
    model: str = "claude-sonnet-4-6",
    system_prompt: str = "You are a helpful assistant.",
    tools: list | None = None,
    messages: list[Message] | None = None,
) -> Engine:
    deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
    config = EngineConfig(
        model=model,
        system_prompt=system_prompt,
        tools=tools or [],
    )
    engine = Engine(deps=deps, config=config)
    if messages:
        engine._messages.extend(messages)
    return engine


def _make_deps() -> Deps:
    return Deps(call_model=AsyncMock(), run_tool=AsyncMock())


# ---------------------------------------------------------------------------
# Test: /context listed in help (REPL)
# ---------------------------------------------------------------------------

class TestContextInHelp:
    """Verify /context appears in the REPL SLASH_COMMANDS registry."""

    def test_context_in_slash_commands(self):
        assert "/context" in SLASH_COMMANDS

    def test_context_description_mentions_token(self):
        desc = SLASH_COMMANDS["/context"]
        assert "token" in desc.lower() or "context" in desc.lower()


# ---------------------------------------------------------------------------
# Test: /context listed in TUI help output
# ---------------------------------------------------------------------------

class TestContextInTuiHelp:
    """Verify /context appears in the TUI /help output (behavior test)."""

    @pytest.mark.asyncio
    async def test_tui_help_mentions_context(self):
        """/help output from the TUI must list /context (covered by the shared
        SlashDispatcher help)."""
        from duh.cli.slash_commands import SlashDispatcher, SlashContext
        from unittest.mock import MagicMock

        ctx = SlashContext(engine=MagicMock(), model="test", deps=None,
                           executor=None, task_manager=None, template_state={},
                           plan_mode=None, mcp_executor=None, provider_name="stub")
        dispatcher = SlashDispatcher(ctx)
        output, _ = await dispatcher.async_dispatch("/help", "")
        assert "/context" in output


# ---------------------------------------------------------------------------
# Test: context breakdown output contains expected fields
# ---------------------------------------------------------------------------

class TestContextBreakdownFields:
    """Verify context_breakdown output has all required components."""

    def test_contains_context_window_header(self):
        engine = _make_engine()
        output = context_breakdown(engine, "claude-sonnet-4-6")
        assert "Context window:" in output

    def test_contains_system_prompt_row(self):
        engine = _make_engine()
        output = context_breakdown(engine, "claude-sonnet-4-6")
        assert "System prompt" in output

    def test_contains_conversation_history_row(self):
        engine = _make_engine()
        output = context_breakdown(engine, "claude-sonnet-4-6")
        assert "Conversation history" in output

    def test_contains_tool_schemas_row(self):
        engine = _make_engine()
        output = context_breakdown(engine, "claude-sonnet-4-6")
        assert "Tool schemas" in output

    def test_contains_used_row(self):
        engine = _make_engine()
        output = context_breakdown(engine, "claude-sonnet-4-6")
        assert "Used" in output

    def test_contains_available_row(self):
        engine = _make_engine()
        output = context_breakdown(engine, "claude-sonnet-4-6")
        assert "Available" in output

    def test_contains_percentages(self):
        engine = _make_engine()
        output = context_breakdown(engine, "claude-sonnet-4-6")
        assert "%" in output

    def test_contains_model_name(self):
        engine = _make_engine()
        output = context_breakdown(engine, "claude-sonnet-4-6")
        assert "claude-sonnet-4-6" in output


# ---------------------------------------------------------------------------
# Test: REPL /context handler prints output
# ---------------------------------------------------------------------------

class TestContextReplHandler:
    def test_repl_context_continues_loop(self, capsys):
        engine = _make_engine()
        keep_going, _ = _handle_slash("/context", engine, "claude-sonnet-4-6", _make_deps())
        assert keep_going is True

    def test_repl_context_prints_breakdown(self, capsys):
        engine = _make_engine(system_prompt="Hello world")
        _handle_slash("/context", engine, "claude-sonnet-4-6", _make_deps())
        captured = capsys.readouterr()
        assert "Context window:" in captured.out
        assert "System prompt" in captured.out
        assert "Available" in captured.out


# ---------------------------------------------------------------------------
# Test: breakdown with conversation history
# ---------------------------------------------------------------------------

class TestContextWithHistory:
    def test_history_tokens_reflected(self):
        messages = [
            Message(role="user", content="a" * 200),
            Message(role="assistant", content="b" * 200),
        ]
        engine = _make_engine(system_prompt="", messages=messages)
        output = context_breakdown(engine, "claude-sonnet-4-6")
        # Each 200-char message ~ 50 tokens at 4 chars/token, total ~100
        assert "100" in output

    def test_tool_tokens_reflected(self):
        tool = _FakeTool(
            name="Bash",
            description="Run a shell command.",
            input_schema={"type": "object", "properties": {"cmd": {"type": "string"}}},
        )
        engine = _make_engine(system_prompt="", tools=[tool])
        output = context_breakdown(engine, "claude-sonnet-4-6")
        # Tool schemas row should be non-zero
        lines = output.split("\n")
        tool_line = [l for l in lines if "Tool schemas" in l][0]
        # Extract the token count -- should not be "0"
        assert "0 " not in tool_line.split("Tool schemas")[1].lstrip()
