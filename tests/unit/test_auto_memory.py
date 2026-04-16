"""Tests for auto-memory extraction (ADR-069 P1).

Covers:
- _messages_to_text truncation
- _parse_extraction with valid/invalid/empty inputs
- extract_memories end-to-end with mock call_model
- Config flag gating
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import pytest

from duh.kernel.auto_memory import (
    AUTO_MEMORY_PROMPT,
    _messages_to_text,
    _parse_extraction,
    extract_memories,
)
from duh.kernel.messages import Message


# ---------------------------------------------------------------------------
# _messages_to_text
# ---------------------------------------------------------------------------


class TestMessagesToText:
    def test_empty_messages(self):
        assert _messages_to_text([]) == ""

    def test_single_message(self):
        msgs = [Message(role="user", content="hello world")]
        text = _messages_to_text(msgs)
        assert "[user] hello world" in text

    def test_multiple_messages(self):
        msgs = [
            Message(role="user", content="first"),
            Message(role="assistant", content="second"),
            Message(role="user", content="third"),
        ]
        text = _messages_to_text(msgs)
        assert "[user] first" in text
        assert "[assistant] second" in text
        assert "[user] third" in text

    def test_truncation_at_max_chars(self):
        # Create messages that exceed max_chars
        msgs = [
            Message(role="user", content="x" * 5000),
            Message(role="assistant", content="y" * 5000),
            Message(role="user", content="z" * 5000),
        ]
        text = _messages_to_text(msgs, max_chars=8000)
        # Should be truncated -- total would be ~15k + role tags
        assert len(text) <= 8200  # some margin for role tags

    def test_recent_messages_preferred(self):
        # With a tight limit, only the most recent messages survive
        msgs = [
            Message(role="user", content="OLD " * 2000),
            Message(role="assistant", content="NEW response"),
        ]
        text = _messages_to_text(msgs, max_chars=200)
        assert "NEW response" in text


# ---------------------------------------------------------------------------
# _parse_extraction
# ---------------------------------------------------------------------------


class TestParseExtraction:
    def test_valid_json_array(self):
        raw = json.dumps([
            {"key": "test-runner", "value": "Uses pytest"},
            {"key": "db", "value": "PostgreSQL 16"},
        ])
        result = _parse_extraction(raw)
        assert len(result) == 2
        assert result[0]["key"] == "test-runner"
        assert result[1]["value"] == "PostgreSQL 16"

    def test_empty_array(self):
        assert _parse_extraction("[]") == []

    def test_empty_string(self):
        assert _parse_extraction("") == []

    def test_whitespace_only(self):
        assert _parse_extraction("   \n  ") == []

    def test_markdown_code_fence(self):
        raw = '```json\n[{"key": "k", "value": "v"}]\n```'
        result = _parse_extraction(raw)
        assert len(result) == 1
        assert result[0]["key"] == "k"

    def test_non_json_returns_empty(self):
        assert _parse_extraction("This is not JSON at all") == []

    def test_non_list_returns_empty(self):
        assert _parse_extraction('{"key": "k", "value": "v"}') == []

    def test_caps_at_three(self):
        raw = json.dumps([
            {"key": f"k{i}", "value": f"v{i}"} for i in range(5)
        ])
        result = _parse_extraction(raw)
        assert len(result) == 3

    def test_missing_key_skipped(self):
        raw = json.dumps([
            {"value": "no key here"},
            {"key": "good", "value": "has key"},
        ])
        result = _parse_extraction(raw)
        assert len(result) == 1
        assert result[0]["key"] == "good"

    def test_strips_whitespace(self):
        raw = json.dumps([{"key": "  spaced  ", "value": "  val  "}])
        result = _parse_extraction(raw)
        assert result[0]["key"] == "spaced"
        assert result[0]["value"] == "val"


# ---------------------------------------------------------------------------
# extract_memories
# ---------------------------------------------------------------------------


class TestExtractMemories:
    @pytest.mark.asyncio
    async def test_empty_messages_returns_empty(self):
        call_model = AsyncMock()
        result = await extract_memories([], call_model)
        assert result == []
        call_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_short_conversation_returns_empty(self):
        """Conversations under 100 chars are too short to extract from."""
        msgs = [Message(role="user", content="hi")]
        call_model = AsyncMock()
        result = await extract_memories(msgs, call_model)
        assert result == []

    @pytest.mark.asyncio
    async def test_successful_extraction(self):
        msgs = [
            Message(role="user", content="We always use ruff for linting in this project. " * 10),
            Message(role="assistant", content="Got it, I'll use ruff. " * 10),
        ]

        facts_json = json.dumps([
            {"key": "linter", "value": "Project uses ruff for linting"},
        ])

        # Create an async generator that yields an assistant message
        async def mock_call_model(**kwargs):
            yield {
                "type": "assistant",
                "message": Message(role="assistant", content=facts_json),
            }

        result = await extract_memories(msgs, mock_call_model)
        assert len(result) == 1
        assert result[0]["key"] == "linter"
        assert "ruff" in result[0]["value"]

    @pytest.mark.asyncio
    async def test_model_returns_empty_array(self):
        msgs = [
            Message(role="user", content="Just a simple question " * 20),
            Message(role="assistant", content="Here is the answer " * 20),
        ]

        async def mock_call_model(**kwargs):
            yield {
                "type": "assistant",
                "message": Message(role="assistant", content="[]"),
            }

        result = await extract_memories(msgs, mock_call_model)
        assert result == []

    @pytest.mark.asyncio
    async def test_model_error_returns_empty(self):
        msgs = [
            Message(role="user", content="Important conversation " * 20),
        ]

        async def mock_call_model(**kwargs):
            raise RuntimeError("Model unavailable")
            # Make it an async generator
            yield  # pragma: no cover

        result = await extract_memories(msgs, mock_call_model)
        assert result == []

    @pytest.mark.asyncio
    async def test_text_delta_events_collected(self):
        """Ensure text_delta events are also captured."""
        msgs = [
            Message(role="user", content="We use PostgreSQL 16 " * 20),
        ]

        async def mock_call_model(**kwargs):
            yield {"type": "text_delta", "text": '[{"key": "db",'}
            yield {"type": "text_delta", "text": ' "value": "PostgreSQL 16"}]'}

        result = await extract_memories(msgs, mock_call_model)
        assert len(result) == 1
        assert result[0]["key"] == "db"

    @pytest.mark.asyncio
    async def test_model_kwarg_passed(self):
        """When model is specified, it's passed to call_model."""
        msgs = [
            Message(role="user", content="Some long conversation " * 20),
        ]
        received_kwargs: dict = {}

        async def mock_call_model(**kwargs):
            received_kwargs.update(kwargs)
            yield {
                "type": "assistant",
                "message": Message(role="assistant", content="[]"),
            }

        await extract_memories(msgs, mock_call_model, model="haiku")
        assert received_kwargs.get("model") == "haiku"


# ---------------------------------------------------------------------------
# AUTO_MEMORY_PROMPT content
# ---------------------------------------------------------------------------


class TestAutoMemoryPrompt:
    def test_prompt_has_instructions(self):
        assert "0-3 key facts" in AUTO_MEMORY_PROMPT
        assert "JSON array" in AUTO_MEMORY_PROMPT

    def test_prompt_has_examples(self):
        assert "short-id" in AUTO_MEMORY_PROMPT


# ---------------------------------------------------------------------------
# Config flag (auto_memory on EngineConfig)
# ---------------------------------------------------------------------------


class TestAutoMemoryConfig:
    def test_engine_config_default_false(self):
        from duh.kernel.engine import EngineConfig
        cfg = EngineConfig()
        assert cfg.auto_memory is False

    def test_engine_config_can_enable(self):
        from duh.kernel.engine import EngineConfig
        cfg = EngineConfig(auto_memory=True)
        assert cfg.auto_memory is True

    def test_duh_config_default_false(self):
        from duh.config import Config
        cfg = Config()
        assert cfg.auto_memory is False

    def test_duh_config_merge(self):
        from duh.config import Config, _merge_into
        cfg = Config()
        _merge_into(cfg, {"auto_memory": True})
        assert cfg.auto_memory is True

    def test_duh_config_merge_falsy(self):
        from duh.config import Config, _merge_into
        cfg = Config()
        _merge_into(cfg, {"auto_memory": False})
        assert cfg.auto_memory is False
