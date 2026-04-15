"""Tests for coordinator mode persistence (ADR-063)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from duh.kernel.coordinator import COORDINATOR_SYSTEM_PROMPT
from duh.kernel.messages import Message


# ---------------------------------------------------------------------------
# Mode saved in session metadata
# ---------------------------------------------------------------------------


class TestModeSavedInMetadata:
    """When /mode coordinator is used, the mode is stored in message metadata."""

    @pytest.mark.asyncio
    async def test_mode_saved_on_coordinator_switch(self):
        from duh.ui.app import DuhApp

        engine = MagicMock()
        engine._config = MagicMock()
        engine._config.system_prompt = "base prompt"
        engine.session_id = "test"
        # Simulate messages already in the engine
        msg = Message(role="user", content="hello")
        engine._messages = [msg]

        app = DuhApp(engine=engine, model="m", session_id="s")

        async def noop(w):
            pass
        app._add_widget = noop

        await app._handle_slash("/mode coordinator")
        assert app._coordinator_mode is True
        assert engine._messages[0].metadata.get("coordinator_mode") is True

    @pytest.mark.asyncio
    async def test_mode_cleared_on_normal_switch(self):
        from duh.ui.app import DuhApp

        engine = MagicMock()
        engine._config = MagicMock()
        engine._config.system_prompt = "base prompt"
        engine.session_id = "test"
        msg = Message(role="user", content="hello", metadata={"coordinator_mode": True})
        engine._messages = [msg]

        app = DuhApp(engine=engine, model="m", session_id="s")
        app._coordinator_mode = True

        async def noop(w):
            pass
        app._add_widget = noop

        # Switch to coordinator first to prepend the prompt
        engine._config.system_prompt = COORDINATOR_SYSTEM_PROMPT + "\n\n" + "base prompt"

        await app._handle_slash("/mode normal")
        assert app._coordinator_mode is False
        assert engine._messages[0].metadata.get("coordinator_mode") is False

    @pytest.mark.asyncio
    async def test_mode_not_saved_when_no_messages(self):
        """When no messages exist yet, the switch still works (no crash)."""
        from duh.ui.app import DuhApp

        engine = MagicMock()
        engine._config = MagicMock()
        engine._config.system_prompt = "base prompt"
        engine.session_id = "test"
        engine._messages = []

        app = DuhApp(engine=engine, model="m", session_id="s")

        async def noop(w):
            pass
        app._add_widget = noop

        # Should not raise even with empty messages
        await app._handle_slash("/mode coordinator")
        assert app._coordinator_mode is True


# ---------------------------------------------------------------------------
# Mode restored on resume
# ---------------------------------------------------------------------------


class TestModeRestoredOnResume:
    """When a session is resumed, coordinator_mode in metadata restores the mode."""

    def test_coordinator_mode_detected_in_metadata(self):
        """Messages with coordinator_mode=True in first message metadata
        should trigger coordinator mode restoration."""
        msgs = [
            Message(role="user", content="hello", metadata={"coordinator_mode": True}),
            Message(role="assistant", content="world"),
        ]
        # Check the first message's metadata
        assert msgs[0].metadata.get("coordinator_mode") is True

    def test_no_coordinator_mode_in_metadata(self):
        """Messages without coordinator_mode should not trigger restoration."""
        msgs = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="world"),
        ]
        assert msgs[0].metadata.get("coordinator_mode") is None

    def test_coordinator_prompt_prepended_on_restore(self):
        """Simulates what run_tui does: prepend coordinator prompt when metadata says so."""
        original_prompt = "You are a helpful assistant."
        msgs = [
            Message(role="user", content="hello", metadata={"coordinator_mode": True}),
        ]

        # Simulate the restoration logic from run_tui
        system_prompt = original_prompt
        if msgs and msgs[0].metadata.get("coordinator_mode"):
            if not system_prompt.startswith(COORDINATOR_SYSTEM_PROMPT):
                system_prompt = COORDINATOR_SYSTEM_PROMPT + "\n\n" + system_prompt

        assert system_prompt.startswith(COORDINATOR_SYSTEM_PROMPT)
        assert original_prompt in system_prompt

    def test_no_double_prepend_on_restore(self):
        """If coordinator prompt is already there, don't add it again."""
        original_prompt = COORDINATOR_SYSTEM_PROMPT + "\n\nYou are a helpful assistant."
        msgs = [
            Message(role="user", content="hello", metadata={"coordinator_mode": True}),
        ]

        system_prompt = original_prompt
        if msgs and msgs[0].metadata.get("coordinator_mode"):
            if not system_prompt.startswith(COORDINATOR_SYSTEM_PROMPT):
                system_prompt = COORDINATOR_SYSTEM_PROMPT + "\n\n" + system_prompt

        # Should be identical — no double prepend
        assert system_prompt == original_prompt
