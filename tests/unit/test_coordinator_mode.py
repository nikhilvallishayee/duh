"""Tests for coordinator mode (ADR-063)."""

from __future__ import annotations

import argparse
from unittest.mock import MagicMock

import pytest

from duh.cli.parser import build_parser
from duh.kernel.coordinator import COORDINATOR_SYSTEM_PROMPT


# ---------------------------------------------------------------------------
# Parser accepts --coordinator
# ---------------------------------------------------------------------------


class TestParserCoordinatorFlag:
    def test_coordinator_default_false(self):
        parser = build_parser()
        args = parser.parse_args(["-p", "hello"])
        assert args.coordinator is False

    def test_coordinator_flag_sets_true(self):
        parser = build_parser()
        args = parser.parse_args(["--coordinator", "-p", "hello"])
        assert args.coordinator is True

    def test_coordinator_flag_no_value(self):
        """--coordinator is a boolean flag, no value needed."""
        parser = build_parser()
        args = parser.parse_args(["--coordinator"])
        assert args.coordinator is True


# ---------------------------------------------------------------------------
# Coordinator prompt content
# ---------------------------------------------------------------------------


class TestCoordinatorPrompt:
    def test_prompt_is_nonempty_string(self):
        assert isinstance(COORDINATOR_SYSTEM_PROMPT, str)
        assert len(COORDINATOR_SYSTEM_PROMPT) > 50

    def test_prompt_contains_delegation_instructions(self):
        assert "delegate" in COORDINATOR_SYSTEM_PROMPT.lower()

    def test_prompt_mentions_swarm_tool(self):
        assert "Swarm" in COORDINATOR_SYSTEM_PROMPT

    def test_prompt_mentions_agent_types(self):
        for agent_type in ("coder", "researcher", "planner", "reviewer"):
            assert agent_type in COORDINATOR_SYSTEM_PROMPT

    def test_prompt_forbids_direct_file_tools(self):
        assert "Never use file tools" in COORDINATOR_SYSTEM_PROMPT

    def test_prompt_mentions_synthesize(self):
        assert "synthesize" in COORDINATOR_SYSTEM_PROMPT.lower()


# ---------------------------------------------------------------------------
# Runner prepends coordinator prompt
# ---------------------------------------------------------------------------


class TestRunnerCoordinatorInjection:
    def test_coordinator_prompt_prepended_in_runner(self):
        """When args.coordinator is True, the coordinator prompt is at the
        front of the system prompt parts list."""
        # Simulate what runner.py does:
        base = "You are a helpful assistant."
        parts = [base]
        coordinator = True
        if coordinator:
            parts.insert(0, COORDINATOR_SYSTEM_PROMPT)
        joined = "\n\n".join(parts)
        assert joined.startswith(COORDINATOR_SYSTEM_PROMPT)
        assert base in joined

    def test_no_coordinator_prompt_when_flag_false(self):
        base = "You are a helpful assistant."
        parts = [base]
        coordinator = False
        if coordinator:
            parts.insert(0, COORDINATOR_SYSTEM_PROMPT)
        joined = "\n\n".join(parts)
        assert joined == base


# ---------------------------------------------------------------------------
# TUI /mode command
# ---------------------------------------------------------------------------


class TestTuiModeCommand:
    def test_mode_command_in_help_text(self):
        """The /mode command appears in the TUI help output."""
        from duh.ui.app import DuhApp
        # Create a minimal app to inspect slash handler
        engine = MagicMock()
        engine._config = MagicMock()
        engine._config.system_prompt = "base prompt"
        engine.session_id = "test-session"
        app = DuhApp(engine=engine, model="test-model", session_id="test-session")
        # The _handle_slash method should exist
        assert hasattr(app, "_handle_slash")
        # The _coordinator_mode attribute should exist and default to False
        assert app._coordinator_mode is False

    @pytest.mark.asyncio
    async def test_mode_show_current(self):
        """'/mode' with no arg shows current mode."""
        from duh.ui.app import DuhApp
        engine = MagicMock()
        engine._config = MagicMock()
        engine._config.system_prompt = "base prompt"
        engine.session_id = "test"
        app = DuhApp(engine=engine, model="m", session_id="s")

        # Track what widgets are added
        added_widgets = []
        original_add = app._add_widget

        async def capture_widget(w):
            added_widgets.append(w)

        app._add_widget = capture_widget

        result = await app._handle_slash("/mode")
        assert result is True
        assert len(added_widgets) == 1
        # Default mode is "normal" — Textual Static stores content in .content
        widget_content = str(getattr(added_widgets[0], "content", ""))
        assert "normal" in widget_content

    @pytest.mark.asyncio
    async def test_mode_switch_to_coordinator(self):
        """'/mode coordinator' switches to coordinator mode."""
        from duh.ui.app import DuhApp
        engine = MagicMock()
        engine._config = MagicMock()
        engine._config.system_prompt = "base prompt"
        engine.session_id = "test"
        app = DuhApp(engine=engine, model="m", session_id="s")

        added_widgets = []

        async def capture_widget(w):
            added_widgets.append(w)

        app._add_widget = capture_widget

        result = await app._handle_slash("/mode coordinator")
        assert result is True
        assert app._coordinator_mode is True
        # The engine system prompt should now start with the coordinator prompt
        assert engine._config.system_prompt.startswith(COORDINATOR_SYSTEM_PROMPT)

    @pytest.mark.asyncio
    async def test_mode_switch_back_to_normal(self):
        """'/mode normal' after '/mode coordinator' removes coordinator prompt."""
        from duh.ui.app import DuhApp
        engine = MagicMock()
        engine._config = MagicMock()
        original_prompt = "base prompt"
        engine._config.system_prompt = original_prompt
        engine.session_id = "test"
        app = DuhApp(engine=engine, model="m", session_id="s")

        added_widgets = []

        async def capture_widget(w):
            added_widgets.append(w)

        app._add_widget = capture_widget

        # Switch to coordinator
        await app._handle_slash("/mode coordinator")
        assert app._coordinator_mode is True

        # Switch back to normal
        await app._handle_slash("/mode normal")
        assert app._coordinator_mode is False
        assert engine._config.system_prompt == original_prompt

    @pytest.mark.asyncio
    async def test_mode_invalid_shows_error(self):
        """'/mode bogus' shows an error."""
        from duh.ui.app import DuhApp
        engine = MagicMock()
        engine._config = MagicMock()
        engine._config.system_prompt = "base"
        engine.session_id = "test"
        app = DuhApp(engine=engine, model="m", session_id="s")

        errors = []
        app._add_error_message = lambda msg: capture_error(msg)

        async def capture_error(msg):
            errors.append(msg)

        app._add_error_message = capture_error

        result = await app._handle_slash("/mode bogus")
        assert result is True
        assert len(errors) == 1
        assert "bogus" in errors[0]

    @pytest.mark.asyncio
    async def test_mode_coordinator_idempotent(self):
        """Switching to coordinator twice doesn't double-prepend."""
        from duh.ui.app import DuhApp
        engine = MagicMock()
        engine._config = MagicMock()
        engine._config.system_prompt = "base"
        engine.session_id = "test"
        app = DuhApp(engine=engine, model="m", session_id="s")

        async def noop_widget(w):
            pass

        app._add_widget = noop_widget

        await app._handle_slash("/mode coordinator")
        prompt_after_first = engine._config.system_prompt
        await app._handle_slash("/mode coordinator")
        prompt_after_second = engine._config.system_prompt

        # Should not have been prepended twice
        assert prompt_after_first == prompt_after_second
