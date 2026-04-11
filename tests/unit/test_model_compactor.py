"""Tests for duh.adapters.model_compactor -- model-call compaction."""

from __future__ import annotations

import pytest

from duh.kernel.messages import Message


class _FakeModelProvider:
    """Fake model that returns a canned summary."""

    def __init__(self, summary: str = "Summary of the conversation."):
        self._summary = summary
        self.calls: list[dict] = []

    async def stream(self, **kwargs):
        self.calls.append(kwargs)
        yield {"type": "text_delta", "text": self._summary}
        yield {
            "type": "assistant",
            "message": Message(role="assistant", content=self._summary),
        }
        yield {"type": "done", "stop_reason": "end_turn"}


class TestModelCompactor:
    @pytest.mark.asyncio
    async def test_compact_below_limit_returns_unchanged(self):
        from duh.adapters.model_compactor import ModelCompactor

        provider = _FakeModelProvider()
        compactor = ModelCompactor(call_model=provider.stream)
        messages = [
            Message(role="user", content="hello"),
            Message(role="assistant", content="hi"),
        ]
        result = await compactor.compact(messages, token_limit=100_000)
        assert len(result) == 2  # no compaction needed
        assert provider.calls == []  # model not called

    @pytest.mark.asyncio
    async def test_compact_above_limit_calls_model(self):
        from duh.adapters.model_compactor import ModelCompactor

        provider = _FakeModelProvider(summary="Conversation about bugs.")
        compactor = ModelCompactor(call_model=provider.stream, bytes_per_token=1)
        # Create enough messages to exceed a tiny limit
        messages = [
            Message(role="user", content="A" * 500),
            Message(role="assistant", content="B" * 500),
            Message(role="user", content="fix the latest bug"),
        ]
        result = await compactor.compact(messages, token_limit=100)
        # Should have compacted: system summary + kept recent messages
        assert len(result) < len(messages) or any(
            "summary" in (m.content.lower() if isinstance(m.content, str) else "")
            for m in result
            if isinstance(m, Message)
        )
        assert len(provider.calls) > 0

    @pytest.mark.asyncio
    async def test_compact_preserves_recent_messages(self):
        from duh.adapters.model_compactor import ModelCompactor

        provider = _FakeModelProvider(summary="Earlier context.")
        compactor = ModelCompactor(
            call_model=provider.stream,
            bytes_per_token=1,
            min_keep=1,
        )
        messages = [
            Message(role="user", content="A" * 500),
            Message(role="assistant", content="B" * 500),
            Message(role="user", content="latest message"),
        ]
        result = await compactor.compact(messages, token_limit=100)
        # The latest message should be preserved
        assert any(
            isinstance(m, Message)
            and "latest" in (m.content if isinstance(m.content, str) else "")
            for m in result
        )

    @pytest.mark.asyncio
    async def test_compact_fallback_on_model_failure(self):
        """When model call fails, fall back to simple truncation."""
        from duh.adapters.model_compactor import ModelCompactor

        async def failing_model(**kwargs):
            raise RuntimeError("API error")
            yield  # make it a generator  # noqa: E501

        compactor = ModelCompactor(call_model=failing_model, bytes_per_token=1)
        messages = [
            Message(role="user", content="A" * 500),
            Message(role="assistant", content="B" * 500),
            Message(role="user", content="latest"),
        ]
        # Should not raise -- falls back to simple compaction
        result = await compactor.compact(messages, token_limit=100)
        assert isinstance(result, list)

    def test_estimate_tokens(self):
        from duh.adapters.model_compactor import ModelCompactor

        compactor = ModelCompactor(call_model=None)
        messages = [Message(role="user", content="hello world")]
        tokens = compactor.estimate_tokens(messages)
        assert tokens > 0

    @pytest.mark.asyncio
    async def test_compact_empty_messages(self):
        from duh.adapters.model_compactor import ModelCompactor

        provider = _FakeModelProvider()
        compactor = ModelCompactor(call_model=provider.stream)
        result = await compactor.compact([], token_limit=100_000)
        assert result == []

    @pytest.mark.asyncio
    async def test_compact_system_messages_preserved(self):
        from duh.adapters.model_compactor import ModelCompactor

        provider = _FakeModelProvider(summary="Context summary.")
        compactor = ModelCompactor(
            call_model=provider.stream, bytes_per_token=1, min_keep=1,
        )
        messages = [
            Message(role="system", content="You are helpful."),
            Message(role="user", content="A" * 500),
            Message(role="assistant", content="B" * 500),
            Message(role="user", content="latest"),
        ]
        result = await compactor.compact(messages, token_limit=100)
        # System message should be first
        assert result[0].role == "system"
        assert result[0].content == "You are helpful."

    @pytest.mark.asyncio
    async def test_compact_no_model_falls_back_to_simple(self):
        """When call_model is None, falls back to SimpleCompactor."""
        from duh.adapters.model_compactor import ModelCompactor

        compactor = ModelCompactor(call_model=None, bytes_per_token=1, min_keep=1)
        messages = [
            Message(role="user", content="A" * 500),
            Message(role="assistant", content="B" * 500),
            Message(role="user", content="latest"),
        ]
        result = await compactor.compact(messages, token_limit=100)
        assert isinstance(result, list)
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_compact_only_system_messages(self):
        from duh.adapters.model_compactor import ModelCompactor

        provider = _FakeModelProvider()
        compactor = ModelCompactor(call_model=provider.stream, bytes_per_token=1)
        messages = [
            Message(role="system", content="sys1"),
            Message(role="system", content="sys2"),
        ]
        result = await compactor.compact(messages, token_limit=10)
        # Only system messages, no conversation to drop
        assert len(result) == 2
        assert all(m.role == "system" for m in result)

    @pytest.mark.asyncio
    async def test_model_summary_included_in_result(self):
        """Verify the model-generated summary text appears in the output."""
        from duh.adapters.model_compactor import ModelCompactor

        provider = _FakeModelProvider(summary="Custom model summary text.")
        compactor = ModelCompactor(
            call_model=provider.stream, bytes_per_token=1, min_keep=1,
        )
        messages = [
            Message(role="user", content="X" * 500),
            Message(role="assistant", content="Y" * 500),
            Message(role="user", content="recent"),
        ]
        result = await compactor.compact(messages, token_limit=100)
        # Find the summary message
        summary_msgs = [
            m for m in result
            if isinstance(m, Message)
            and isinstance(m.content, str)
            and "Custom model summary text" in m.content
        ]
        assert len(summary_msgs) == 1
        assert summary_msgs[0].role == "system"
