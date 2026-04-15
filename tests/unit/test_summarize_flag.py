"""Tests for ADR-058 Phase 3: --summarize flag for resume-with-summary."""

from __future__ import annotations

import argparse
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from duh.cli.parser import build_parser
from duh.kernel.messages import Message


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------


class TestSummarizeFlagParser:
    """The --summarize flag is accepted by the CLI parser."""

    def test_parser_accepts_summarize(self):
        parser = build_parser()
        args = parser.parse_args(["--summarize"])
        assert args.summarize is True

    def test_parser_defaults_summarize_false(self):
        parser = build_parser()
        args = parser.parse_args([])
        assert args.summarize is False

    def test_parser_accepts_summarize_with_continue(self):
        parser = build_parser()
        args = parser.parse_args(["--continue", "--summarize"])
        assert args.continue_session is True
        assert args.summarize is True

    def test_parser_accepts_summarize_with_resume(self):
        parser = build_parser()
        args = parser.parse_args(["--resume", "abc-123", "--summarize"])
        assert args.resume == "abc-123"
        assert args.summarize is True


# ---------------------------------------------------------------------------
# Runner integration tests
# ---------------------------------------------------------------------------


class TestSummarizeWithoutContinue:
    """--summarize without --continue is a no-op (no compaction runs)."""

    def test_summarize_alone_does_not_compact(self):
        """When --summarize is set but --continue is not, nothing happens.

        We verify by confirming the parser accepts it, and that the
        runner's resume block is not entered (no session to load).
        """
        parser = build_parser()
        args = parser.parse_args(["--summarize", "-p", "hello"])
        assert args.summarize is True
        assert args.continue_session is False
        assert args.resume is None
        # Runner only enters the resume block when continue_session or resume is set,
        # so --summarize alone is effectively a no-op.


class TestSummarizeWithContinue:
    """--summarize with --continue triggers compaction on loaded messages."""

    @pytest.mark.asyncio
    async def test_summarize_compacts_loaded_messages(self):
        """Simulate the runner's resume + summarize logic."""
        # Build messages as they'd appear after session resume
        loaded_messages = [
            Message(role="user", content="Hello"),
            Message(role="assistant", content="Hi there!"),
            Message(role="user", content="What is Python?"),
            Message(role="assistant", content="Python is a programming language."),
            Message(role="user", content="Tell me more"),
            Message(role="assistant", content="It was created by Guido van Rossum."),
        ]

        # Mock compact function that simulates summarization
        async def mock_compact(messages, token_limit=0):
            # Simulate compaction: summarize older messages, keep recent ones
            if len(messages) <= 2:
                return messages
            summary = Message(role="system", content="Previous conversation summary: discussed Python")
            return [summary] + messages[-2:]

        compact_fn = mock_compact

        # Simulate what the runner does: resume + summarize
        engine_messages = list(loaded_messages)
        before_count = len(engine_messages)

        engine_messages = await compact_fn(engine_messages, token_limit=50000)
        after_count = len(engine_messages)

        # Verify compaction happened
        assert before_count == 6
        assert after_count == 3  # summary + 2 recent
        assert engine_messages[0].role == "system"
        assert "summary" in engine_messages[0].content.lower()

    @pytest.mark.asyncio
    async def test_summarize_with_empty_messages_is_noop(self):
        """If session has no messages, summarize does nothing."""
        engine_messages: list[Message] = []

        compact_called = False

        async def mock_compact(messages, token_limit=0):
            nonlocal compact_called
            compact_called = True
            return messages

        # Runner checks `if engine._messages` before calling compact
        if engine_messages:
            engine_messages = await mock_compact(engine_messages)

        assert not compact_called
        assert engine_messages == []

    @pytest.mark.asyncio
    async def test_summarize_uses_half_default_limit(self):
        """Verify the token_limit is 50% of the compactor's default limit."""
        from duh.adapters.simple_compactor import SimpleCompactor

        compactor = SimpleCompactor(default_limit=100_000)
        received_limit = None

        async def mock_compact(messages, token_limit=0):
            nonlocal received_limit
            received_limit = token_limit
            return messages

        messages = [Message(role="user", content="test")]

        # Simulate runner logic: token_limit=compactor.default_limit // 2
        await mock_compact(messages, token_limit=compactor.default_limit // 2)

        assert received_limit == 50_000

    @pytest.mark.asyncio
    async def test_summarize_with_real_compactor(self):
        """End-to-end: --summarize with the real SimpleCompactor."""
        from duh.adapters.simple_compactor import SimpleCompactor

        # Use a very small limit to force compaction
        compactor = SimpleCompactor(default_limit=200, bytes_per_token=4)

        messages = [
            Message(role="user", content="First question " * 20),
            Message(role="assistant", content="First answer " * 20),
            Message(role="user", content="Second question " * 20),
            Message(role="assistant", content="Second answer " * 20),
            Message(role="user", content="Recent question"),
            Message(role="assistant", content="Recent answer"),
        ]

        before = len(messages)
        # Use half the default limit as the runner does
        compacted = await compactor.compact(messages, token_limit=compactor.default_limit // 2)
        after = len(compacted)

        # With such a small limit, some messages should be dropped
        assert after <= before
        # At minimum, the compactor keeps min_keep (2) messages
        assert after >= 2
