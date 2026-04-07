"""Tests for --brief mode and /brief REPL toggle."""

from __future__ import annotations

import argparse
from unittest.mock import AsyncMock, MagicMock

import pytest

from duh.cli.parser import build_parser
from duh.cli.runner import BRIEF_INSTRUCTION, SYSTEM_PROMPT
from duh.cli.repl import SLASH_COMMANDS, _handle_slash
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(system_prompt: str = "test prompt") -> Engine:
    deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
    config = EngineConfig(model="test-model", system_prompt=system_prompt)
    return Engine(deps=deps, config=config)


def _make_deps() -> Deps:
    return Deps(call_model=AsyncMock(), run_tool=AsyncMock())


# ---------------------------------------------------------------------------
# 1. Parser: --brief flag
# ---------------------------------------------------------------------------

class TestBriefParserFlag:
    def test_brief_default_false(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.brief is False

    def test_brief_flag_sets_true(self):
        parser = build_parser()
        args = parser.parse_args(["--brief"])
        assert args.brief is True

    def test_brief_with_prompt(self):
        parser = build_parser()
        args = parser.parse_args(["--brief", "-p", "hello"])
        assert args.brief is True
        assert args.prompt == "hello"


# ---------------------------------------------------------------------------
# 2. BRIEF_INSTRUCTION constant
# ---------------------------------------------------------------------------

class TestBriefInstruction:
    def test_instruction_contains_concise(self):
        assert "concise" in BRIEF_INSTRUCTION.lower()

    def test_instruction_contains_3_sentences(self):
        assert "3 sentences" in BRIEF_INSTRUCTION


# ---------------------------------------------------------------------------
# 3. /brief REPL slash command — listed in SLASH_COMMANDS
# ---------------------------------------------------------------------------

class TestBriefInSlashCommands:
    def test_brief_in_slash_commands(self):
        assert "/brief" in SLASH_COMMANDS

    def test_brief_help_mentions_toggle(self):
        desc = SLASH_COMMANDS["/brief"]
        assert "brief" in desc.lower()


# ---------------------------------------------------------------------------
# 4. /brief toggle — on/off/bare
# ---------------------------------------------------------------------------

class TestBriefToggle:
    def test_brief_on(self, capsys):
        engine = _make_engine("base prompt")
        keep, model = _handle_slash("/brief on", engine, "m", _make_deps())
        assert keep is True
        assert BRIEF_INSTRUCTION in engine._config.system_prompt
        captured = capsys.readouterr()
        assert "ON" in captured.out

    def test_brief_off(self, capsys):
        prompt_with_brief = "base prompt\n\n" + BRIEF_INSTRUCTION
        engine = _make_engine(prompt_with_brief)
        keep, model = _handle_slash("/brief off", engine, "m", _make_deps())
        assert keep is True
        assert BRIEF_INSTRUCTION not in engine._config.system_prompt
        captured = capsys.readouterr()
        assert "OFF" in captured.out

    def test_brief_bare_toggles_on(self, capsys):
        engine = _make_engine("base prompt")
        _handle_slash("/brief", engine, "m", _make_deps())
        assert BRIEF_INSTRUCTION in engine._config.system_prompt
        captured = capsys.readouterr()
        assert "ON" in captured.out

    def test_brief_bare_toggles_off(self, capsys):
        prompt_with_brief = "base prompt\n\n" + BRIEF_INSTRUCTION
        engine = _make_engine(prompt_with_brief)
        _handle_slash("/brief", engine, "m", _make_deps())
        assert BRIEF_INSTRUCTION not in engine._config.system_prompt
        captured = capsys.readouterr()
        assert "OFF" in captured.out

    def test_brief_on_when_already_on(self, capsys):
        prompt_with_brief = "base prompt\n\n" + BRIEF_INSTRUCTION
        engine = _make_engine(prompt_with_brief)
        _handle_slash("/brief on", engine, "m", _make_deps())
        captured = capsys.readouterr()
        assert "no change" in captured.out

    def test_brief_off_when_already_off(self, capsys):
        engine = _make_engine("base prompt")
        _handle_slash("/brief off", engine, "m", _make_deps())
        captured = capsys.readouterr()
        assert "no change" in captured.out


# ---------------------------------------------------------------------------
# 5. System prompt wiring: --brief appends instruction
# ---------------------------------------------------------------------------

class TestBriefSystemPromptWiring:
    def test_runner_brief_appends_instruction(self):
        """When args.brief is True, BRIEF_INSTRUCTION should be appended."""
        # Simulate the system_prompt_parts logic from runner.py
        args = argparse.Namespace(
            system_prompt=None,
            brief=True,
        )
        system_prompt_parts = [args.system_prompt or SYSTEM_PROMPT]
        if getattr(args, "brief", False):
            system_prompt_parts.append(BRIEF_INSTRUCTION)
        result = "\n\n".join(system_prompt_parts)
        assert BRIEF_INSTRUCTION in result

    def test_runner_no_brief_no_instruction(self):
        """When args.brief is False, BRIEF_INSTRUCTION should NOT appear."""
        args = argparse.Namespace(
            system_prompt=None,
            brief=False,
        )
        system_prompt_parts = [args.system_prompt or SYSTEM_PROMPT]
        if getattr(args, "brief", False):
            system_prompt_parts.append(BRIEF_INSTRUCTION)
        result = "\n\n".join(system_prompt_parts)
        assert BRIEF_INSTRUCTION not in result


# ---------------------------------------------------------------------------
# 6. Round-trip: toggle on then off restores original prompt
# ---------------------------------------------------------------------------

class TestBriefRoundTrip:
    def test_toggle_on_off_restores_prompt(self):
        original = "my system prompt"
        engine = _make_engine(original)
        # Toggle ON
        _handle_slash("/brief on", engine, "m", _make_deps())
        assert BRIEF_INSTRUCTION in engine._config.system_prompt
        # Toggle OFF
        _handle_slash("/brief off", engine, "m", _make_deps())
        assert engine._config.system_prompt == original
        assert BRIEF_INSTRUCTION not in engine._config.system_prompt
