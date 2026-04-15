"""Tests for the /context REPL command (context window token breakdown)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from duh.cli.repl import SLASH_COMMANDS, _handle_slash, context_breakdown
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig
from duh.kernel.messages import Message
from duh.kernel.tokens import count_tokens, get_context_limit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeTool:
    """Minimal tool stub with name, description, and input_schema."""

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
# Test: /context is registered in SLASH_COMMANDS
# ---------------------------------------------------------------------------

class TestContextRegistered:
    def test_context_in_slash_commands(self):
        assert "/context" in SLASH_COMMANDS

    def test_context_description_not_empty(self):
        assert len(SLASH_COMMANDS["/context"]) > 0


# ---------------------------------------------------------------------------
# Test: context_breakdown returns correct structure
# ---------------------------------------------------------------------------

class TestContextBreakdownStructure:
    def test_contains_header(self):
        engine = _make_engine()
        output = context_breakdown(engine, "claude-sonnet-4-6")
        assert "Context window:" in output
        assert "1,000,000" in output  # claude-sonnet-4-6 has 1M limit

    def test_contains_all_components(self):
        engine = _make_engine()
        output = context_breakdown(engine, "claude-sonnet-4-6")
        assert "System prompt" in output
        assert "Conversation history" in output
        assert "Tool schemas" in output
        assert "Used" in output
        assert "Available" in output

    def test_contains_percentages(self):
        engine = _make_engine()
        output = context_breakdown(engine, "claude-sonnet-4-6")
        # Should contain percentage signs
        assert "%" in output


# ---------------------------------------------------------------------------
# Test: token counting accuracy
# ---------------------------------------------------------------------------

class TestContextTokenCounting:
    def test_system_prompt_tokens_counted(self):
        prompt = "x" * 400  # 100 tokens
        engine = _make_engine(system_prompt=prompt)
        output = context_breakdown(engine, "claude-sonnet-4-6")
        # System prompt should show 100 tokens
        assert "100" in output

    def test_conversation_history_tokens_counted(self):
        messages = [
            Message(role="user", content="a" * 200),      # 50 tokens
            Message(role="assistant", content="b" * 200),  # 50 tokens
        ]
        engine = _make_engine(system_prompt="", messages=messages)
        output = context_breakdown(engine, "claude-sonnet-4-6")
        # History should show 100 tokens total
        assert "100" in output

    def test_tool_schema_tokens_counted(self):
        tool = _FakeTool(
            name="Read",
            description="Read a file from disk.",
            input_schema={
                "type": "object",
                "properties": {
                    "file_path": {"type": "string", "description": "Path to file."},
                },
                "required": ["file_path"],
            },
        )
        engine = _make_engine(system_prompt="", tools=[tool])
        output = context_breakdown(engine, "claude-sonnet-4-6")
        # Tool schema tokens should be > 0
        # Compute expected: name + desc + JSON schema
        schema_text = "Read Read a file from disk. " + json.dumps(tool.input_schema)
        expected_tokens = count_tokens(schema_text)
        assert expected_tokens > 0
        assert str(expected_tokens) in output

    def test_empty_session_shows_mostly_available(self):
        engine = _make_engine(system_prompt="", tools=[])
        output = context_breakdown(engine, "claude-sonnet-4-6")
        # With no system prompt, no history, no tools, all should be available
        assert "1,000,000" in output  # context limit
        # Used should be 0
        lines = output.split("\n")
        used_line = [l for l in lines if "Used" in l][0]
        assert "0" in used_line

    def test_available_is_limit_minus_used(self):
        prompt = "y" * 800  # 200 tokens
        messages = [Message(role="user", content="z" * 400)]  # 100 tokens
        engine = _make_engine(system_prompt=prompt, messages=messages, tools=[])
        output = context_breakdown(engine, "claude-sonnet-4-6")
        context_limit = get_context_limit("claude-sonnet-4-6")
        used = count_tokens(prompt) + count_tokens("z" * 400)
        available = context_limit - used
        assert f"{available:,}" in output

    def test_different_model_changes_limit(self):
        engine = _make_engine(system_prompt="")
        output_sonnet = context_breakdown(engine, "claude-sonnet-4-6")
        output_gpt = context_breakdown(engine, "gpt-4o")
        assert "1,000,000" in output_sonnet
        assert "128,000" in output_gpt


# ---------------------------------------------------------------------------
# Test: /context slash command handler
# ---------------------------------------------------------------------------

class TestContextSlashCommand:
    def test_context_command_returns_true(self, capsys):
        engine = _make_engine()
        result = _handle_slash("/context", engine, "claude-sonnet-4-6", _make_deps())
        # Should continue (keep_going=True)
        assert result[0] is True

    def test_context_command_prints_output(self, capsys):
        engine = _make_engine(system_prompt="Hello world test prompt")
        _handle_slash("/context", engine, "claude-sonnet-4-6", _make_deps())
        captured = capsys.readouterr()
        assert "Context window:" in captured.out
        assert "System prompt" in captured.out
        assert "Available" in captured.out


# ---------------------------------------------------------------------------
# Test: edge cases
# ---------------------------------------------------------------------------

class TestContextEdgeCases:
    def test_system_prompt_as_list(self):
        """system_prompt can be a list of strings in EngineConfig."""
        engine = _make_engine()
        engine._config.system_prompt = ["Part one.", "Part two."]
        output = context_breakdown(engine, "claude-sonnet-4-6")
        # Should not crash, and should count tokens from joined text
        expected_tokens = count_tokens("Part one. Part two.")
        assert str(expected_tokens) in output

    def test_tool_with_callable_description(self):
        """Some tools have description as a callable (method)."""

        class CallableDescTool:
            name = "MyTool"
            input_schema = {"type": "object", "properties": {}}

            def description(self):
                return "A tool with callable desc."

        engine = _make_engine(system_prompt="", tools=[CallableDescTool()])
        output = context_breakdown(engine, "claude-sonnet-4-6")
        # Should not crash and should include tool tokens
        assert "Tool schemas" in output

    def test_available_never_negative(self):
        """Even if tokens exceed limit, available should be 0 not negative."""
        # Create a huge system prompt that exceeds the context limit
        # gpt-4o-mini has 128K limit; create > 128K tokens worth of text
        huge_prompt = "x" * 600_000  # ~150K tokens
        engine = _make_engine(system_prompt=huge_prompt)
        output = context_breakdown(engine, "gpt-4o-mini")
        # Parse the Available line
        lines = output.split("\n")
        avail_line = [l for l in lines if "Available" in l][0]
        # Available should show 0, not a negative number
        assert "-" not in avail_line.split("Available")[1].split("%")[0]
