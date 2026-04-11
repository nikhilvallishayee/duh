"""Tests for connection pre-warming at REPL startup."""

from __future__ import annotations

import asyncio
import time
import pytest


class _FakeProvider:
    """Provider that tracks whether it was called."""

    def __init__(self, latency: float = 0.0):
        self.called = False
        self.call_count = 0
        self._latency = latency

    async def stream(self, **kwargs):
        self.called = True
        self.call_count += 1
        if self._latency:
            await asyncio.sleep(self._latency)
        yield {"type": "text_delta", "text": ""}
        yield {"type": "done", "stop_reason": "end_turn"}


class TestPrewarm:
    async def test_prewarm_fires_lightweight_call(self):
        from duh.cli.prewarm import prewarm_connection
        provider = _FakeProvider()
        task = asyncio.create_task(prewarm_connection(provider.stream))
        await task
        assert provider.called is True

    async def test_prewarm_does_not_block_startup(self):
        from duh.cli.prewarm import prewarm_connection
        provider = _FakeProvider(latency=0.5)
        start = time.monotonic()
        task = asyncio.create_task(prewarm_connection(provider.stream))
        elapsed = time.monotonic() - start
        # Creating the task should be near-instant
        assert elapsed < 0.1
        # Let it complete
        await task
        assert provider.called

    async def test_prewarm_failure_is_silent(self):
        async def failing_provider(**kwargs):
            raise RuntimeError("connection refused")
            yield  # noqa: E501  # pragma: no cover

        from duh.cli.prewarm import prewarm_connection
        # Should not raise
        await prewarm_connection(failing_provider)

    async def test_prewarm_caches_result(self):
        from duh.cli.prewarm import prewarm_connection, PrewarmResult
        provider = _FakeProvider()
        result = await prewarm_connection(provider.stream)
        assert isinstance(result, PrewarmResult)
        assert result.success is True

    async def test_prewarm_records_latency(self):
        from duh.cli.prewarm import prewarm_connection
        provider = _FakeProvider(latency=0.05)
        result = await prewarm_connection(provider.stream)
        assert result.latency_ms >= 0

    async def test_prewarm_failure_returns_result_with_error(self):
        async def failing_provider(**kwargs):
            raise ConnectionError("no route to host")
            yield  # pragma: no cover

        from duh.cli.prewarm import prewarm_connection, PrewarmResult
        result = await prewarm_connection(failing_provider)
        assert isinstance(result, PrewarmResult)
        assert result.success is False
        assert "no route to host" in result.error
        assert result.latency_ms >= 0
