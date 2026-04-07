"""Tests for duh.kernel.backoff — exponential backoff with error classification.

Covers:
- is_retryable error classification (status codes, substrings, exception types)
- _compute_delay exponential calculation
- with_backoff retry logic (success, retry-then-success, max retries, non-retryable)
- Logging of retry attempts
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncGenerator
from unittest.mock import patch

import pytest

from duh.kernel.backoff import (
    _compute_delay,
    is_retryable,
    with_backoff,
)


# ===================================================================
# is_retryable — error classification
# ===================================================================

class TestIsRetryable:
    """Test error classification logic."""

    def test_429_is_retryable(self):
        exc = _make_status_error(429, "Too Many Requests")
        assert is_retryable(exc) is True

    def test_503_is_retryable(self):
        exc = _make_status_error(503, "Service Unavailable")
        assert is_retryable(exc) is True

    def test_529_is_retryable(self):
        exc = _make_status_error(529, "Overloaded")
        assert is_retryable(exc) is True

    def test_400_not_retryable(self):
        exc = _make_status_error(400, "Bad Request")
        assert is_retryable(exc) is False

    def test_401_not_retryable(self):
        exc = _make_status_error(401, "Unauthorized")
        assert is_retryable(exc) is False

    def test_404_not_retryable(self):
        exc = _make_status_error(404, "Not Found")
        assert is_retryable(exc) is False

    def test_403_not_retryable(self):
        exc = _make_status_error(403, "Forbidden")
        assert is_retryable(exc) is False

    def test_rate_limit_substring(self):
        exc = Exception("rate_limit exceeded, please slow down")
        assert is_retryable(exc) is True

    def test_overloaded_substring(self):
        exc = Exception("API is overloaded, try again later")
        assert is_retryable(exc) is True

    def test_connection_timeout_substring(self):
        exc = Exception("connection timeout after 30s")
        assert is_retryable(exc) is True

    def test_authentication_not_retryable(self):
        exc = Exception("authentication failed")
        assert is_retryable(exc) is False

    def test_invalid_api_key_not_retryable(self):
        exc = Exception("invalid_api_key: key is expired")
        assert is_retryable(exc) is False

    def test_invalid_request_not_retryable(self):
        exc = Exception("invalid_request_error: messages is required")
        assert is_retryable(exc) is False

    def test_not_found_not_retryable(self):
        exc = Exception("model not found")
        assert is_retryable(exc) is False

    def test_connection_error_retryable(self):
        exc = ConnectionError("Connection refused")
        assert is_retryable(exc) is True

    def test_timeout_error_retryable(self):
        exc = TimeoutError("timed out")
        assert is_retryable(exc) is True

    def test_asyncio_timeout_retryable(self):
        exc = asyncio.TimeoutError()
        assert is_retryable(exc) is True

    def test_os_error_retryable(self):
        exc = OSError("Network is unreachable")
        assert is_retryable(exc) is True

    def test_unknown_error_not_retryable(self):
        exc = ValueError("something unexpected")
        assert is_retryable(exc) is False

    def test_status_code_takes_precedence_over_substring(self):
        """A 400 with 'rate_limit' in the message should not be retried."""
        exc = _make_status_error(400, "rate_limit error in bad request")
        assert is_retryable(exc) is False

    def test_retryable_status_overrides_non_retryable_substring(self):
        """A 429 with 'authentication' in the message should still be retried."""
        exc = _make_status_error(429, "authentication rate limit exceeded")
        assert is_retryable(exc) is True

    def test_httpx_response_status_code(self):
        """Test extraction from httpx-style response.status_code."""
        exc = Exception("error")
        exc.response = type("Response", (), {"status_code": 503})()
        assert is_retryable(exc) is True


# ===================================================================
# _compute_delay — exponential backoff calculation
# ===================================================================

class TestComputeDelay:
    """Test delay calculation with exponential backoff."""

    @patch("duh.kernel.backoff.random.uniform", return_value=1.0)
    def test_first_attempt_base_delay(self, mock_uniform):
        delay = _compute_delay(attempt=0, base_delay=1.0, max_delay=30.0)
        assert delay == 1.0  # 1.0 * 2^0 * 1.0

    @patch("duh.kernel.backoff.random.uniform", return_value=1.0)
    def test_second_attempt_doubles(self, mock_uniform):
        delay = _compute_delay(attempt=1, base_delay=1.0, max_delay=30.0)
        assert delay == 2.0  # 1.0 * 2^1 * 1.0

    @patch("duh.kernel.backoff.random.uniform", return_value=1.0)
    def test_third_attempt_quadruples(self, mock_uniform):
        delay = _compute_delay(attempt=2, base_delay=1.0, max_delay=30.0)
        assert delay == 4.0  # 1.0 * 2^2 * 1.0

    @patch("duh.kernel.backoff.random.uniform", return_value=1.0)
    def test_max_delay_cap(self, mock_uniform):
        delay = _compute_delay(attempt=10, base_delay=1.0, max_delay=30.0)
        assert delay == 30.0  # capped at max_delay

    @patch("duh.kernel.backoff.random.uniform", return_value=0.5)
    def test_jitter_halves_delay(self, mock_uniform):
        delay = _compute_delay(attempt=0, base_delay=2.0, max_delay=30.0)
        assert delay == 1.0  # 2.0 * 2^0 * 0.5

    def test_delay_is_within_expected_range(self):
        """Without mocking, delay should be in [0.5*base, base] for attempt 0."""
        delay = _compute_delay(attempt=0, base_delay=4.0, max_delay=30.0)
        assert 2.0 <= delay <= 4.0

    @patch("duh.kernel.backoff.random.uniform", return_value=1.0)
    def test_custom_base_delay(self, mock_uniform):
        delay = _compute_delay(attempt=0, base_delay=5.0, max_delay=60.0)
        assert delay == 5.0


# ===================================================================
# with_backoff — retry logic
# ===================================================================

class TestWithBackoff:
    """Test the async generator retry wrapper."""

    async def test_success_no_retry(self):
        """Generator succeeds on first attempt — no retries."""
        call_count = 0

        async def gen() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal call_count
            call_count += 1
            yield {"type": "text_delta", "text": "hello"}

        events = [e async for e in with_backoff(gen, max_retries=3, base_delay=0.01)]
        assert call_count == 1
        assert len(events) == 1
        assert events[0]["text"] == "hello"

    async def test_retry_on_retryable_error(self):
        """Retryable error triggers retry, then succeeds."""
        call_count = 0

        async def gen() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_status_error(429, "Rate limited")
            yield {"type": "text_delta", "text": "success"}

        events = [e async for e in with_backoff(gen, max_retries=3, base_delay=0.01)]
        assert call_count == 2
        assert events[0]["text"] == "success"

    async def test_no_retry_on_non_retryable_error(self):
        """Non-retryable error is raised immediately without retry."""
        call_count = 0

        async def gen() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal call_count
            call_count += 1
            raise _make_status_error(401, "Unauthorized")
            yield  # make it a generator  # noqa: E501

        with pytest.raises(Exception, match="Unauthorized"):
            async for _ in with_backoff(gen, max_retries=3, base_delay=0.01):
                pass
        assert call_count == 1

    async def test_max_retries_exceeded(self):
        """After max_retries attempts, exception is raised."""
        call_count = 0

        async def gen() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal call_count
            call_count += 1
            raise _make_status_error(503, "Service Unavailable")
            yield  # noqa: E501

        with pytest.raises(Exception, match="Service Unavailable"):
            async for _ in with_backoff(gen, max_retries=2, base_delay=0.01):
                pass
        assert call_count == 3  # initial + 2 retries

    async def test_success_after_multiple_retries(self):
        """Succeeds on the third attempt (after 2 failures)."""
        call_count = 0

        async def gen() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise _make_status_error(429, "Rate limited")
            yield {"type": "text_delta", "text": "finally"}

        events = [e async for e in with_backoff(gen, max_retries=3, base_delay=0.01)]
        assert call_count == 3
        assert events[0]["text"] == "finally"

    async def test_yields_all_events_on_success(self):
        """All events from a successful stream are yielded."""
        async def gen() -> AsyncGenerator[dict[str, Any], None]:
            yield {"type": "text_delta", "text": "a"}
            yield {"type": "text_delta", "text": "b"}
            yield {"type": "text_delta", "text": "c"}

        events = [e async for e in with_backoff(gen, max_retries=1, base_delay=0.01)]
        assert len(events) == 3
        assert [e["text"] for e in events] == ["a", "b", "c"]

    async def test_mid_stream_retryable_error_retries(self):
        """Error mid-stream triggers retry; second attempt succeeds fully."""
        call_count = 0

        async def gen() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal call_count
            call_count += 1
            yield {"type": "text_delta", "text": "partial"}
            if call_count == 1:
                raise _make_status_error(503, "mid-stream disconnect")
            yield {"type": "text_delta", "text": "complete"}

        events = [e async for e in with_backoff(gen, max_retries=2, base_delay=0.01)]
        assert call_count == 2
        # First attempt's "partial" was yielded, then retry yields "partial" + "complete"
        texts = [e["text"] for e in events]
        assert texts == ["partial", "partial", "complete"]

    async def test_connection_error_retries(self):
        """ConnectionError is retried."""
        call_count = 0

        async def gen() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("Connection refused")
            yield {"type": "ok"}

        events = [e async for e in with_backoff(gen, max_retries=1, base_delay=0.01)]
        assert call_count == 2
        assert events[0]["type"] == "ok"

    async def test_zero_max_retries(self):
        """max_retries=0 means no retries at all."""
        call_count = 0

        async def gen() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal call_count
            call_count += 1
            raise _make_status_error(429, "Rate limited")
            yield  # noqa: E501

        with pytest.raises(Exception, match="Rate limited"):
            async for _ in with_backoff(gen, max_retries=0, base_delay=0.01):
                pass
        assert call_count == 1

    async def test_logging_on_retry(self):
        """Each retry attempt is logged."""
        call_count = 0

        async def gen() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise _make_status_error(429, "Rate limited")
            yield {"type": "ok"}

        with patch("duh.kernel.backoff.logger") as mock_logger:
            events = [e async for e in with_backoff(gen, max_retries=3, base_delay=0.01)]

        assert mock_logger.warning.call_count == 2
        # Verify log message contains attempt info
        first_call_args = mock_logger.warning.call_args_list[0][0]
        assert "1/" in first_call_args[0] % first_call_args[1:]

    async def test_sleep_is_called_between_retries(self):
        """asyncio.sleep is called with computed delay."""
        call_count = 0
        sleep_args: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_args.append(delay)

        async def gen() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise _make_status_error(429, "Rate limited")
            yield {"type": "ok"}

        with patch("duh.kernel.backoff.asyncio.sleep", side_effect=fake_sleep):
            events = [e async for e in with_backoff(gen, max_retries=1, base_delay=0.5)]

        assert len(sleep_args) == 1
        assert 0.25 <= sleep_args[0] <= 0.5  # base * jitter(0.5, 1.0)

    async def test_custom_max_delay_caps_backoff(self):
        """max_delay caps the sleep duration even for high attempt counts."""
        call_count = 0

        async def gen() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal call_count
            call_count += 1
            if call_count <= 5:
                raise _make_status_error(503, "Unavailable")
            yield {"type": "ok"}

        delays: list[float] = []
        original_sleep = asyncio.sleep

        async def capture_sleep(d: float) -> None:
            delays.append(d)

        with patch("duh.kernel.backoff.asyncio.sleep", side_effect=capture_sleep):
            events = [e async for e in with_backoff(
                gen, max_retries=5, base_delay=1.0, max_delay=2.0,
            )]

        # All delays should be <= max_delay
        for d in delays:
            assert d <= 2.0

    async def test_empty_generator_success(self):
        """An empty generator (yields nothing) still counts as success."""
        call_count = 0

        async def gen() -> AsyncGenerator[dict[str, Any], None]:
            nonlocal call_count
            call_count += 1
            return
            yield  # noqa: E501

        events = [e async for e in with_backoff(gen, max_retries=1, base_delay=0.01)]
        assert call_count == 1
        assert events == []


# ===================================================================
# Helpers
# ===================================================================

def _make_status_error(status_code: int, message: str) -> Exception:
    """Create an exception with a status_code attribute, mimicking SDK errors."""
    exc = Exception(message)
    exc.status_code = status_code  # type: ignore[attr-defined]
    return exc
