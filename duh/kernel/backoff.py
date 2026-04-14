"""Exponential backoff for retryable API errors.

Wraps async generator functions so that transient errors (rate limits,
overloaded servers, connection timeouts) are retried with exponential
backoff + jitter, while non-retryable errors (auth, bad request) are
raised immediately.

Usage:
    async for event in with_backoff(lambda: provider.stream(...)):
        handle(event)
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, AsyncGenerator, Callable, Awaitable

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS_CODES = {429, 503, 529}
_NON_RETRYABLE_STATUS_CODES = {400, 401, 403, 404}

_RETRYABLE_SUBSTRINGS = [
    "rate_limit",
    "rate limit",
    "overloaded",
    "too many requests",
    "connection timeout",
    "connect timeout",
    "connection reset",
    "connection refused",
    "server disconnected",
    "internal server error",
]

_NON_RETRYABLE_SUBSTRINGS = [
    "authentication",
    "unauthorized",
    "invalid api key",
    "invalid_api_key",
    "invalid request",
    "invalid_request",
    "permission denied",
    "not found",
]


def _get_status_code(exc: BaseException) -> int | None:
    """Extract an HTTP status code from common SDK exception types."""
    # anthropic.APIStatusError, openai.APIStatusError both have .status_code
    code = getattr(exc, "status_code", None)
    if isinstance(code, int):
        return code
    # httpx.HTTPStatusError
    response = getattr(exc, "response", None)
    if response is not None:
        sc = getattr(response, "status_code", None)
        if isinstance(sc, int):
            return sc
    return None


def is_retryable(exc: BaseException) -> bool:
    """Return True if *exc* represents a transient, retryable error."""
    # Check status code first — most reliable signal
    code = _get_status_code(exc)
    if code is not None:
        if code in _NON_RETRYABLE_STATUS_CODES:
            return False
        if code in _RETRYABLE_STATUS_CODES:
            return True

    error_lower = str(exc).lower()

    # Non-retryable substrings take precedence when no status code
    for substr in _NON_RETRYABLE_SUBSTRINGS:
        if substr in error_lower:
            return False

    # Retryable substrings
    for substr in _RETRYABLE_SUBSTRINGS:
        if substr in error_lower:
            return True

    # Connection / timeout errors are generally retryable
    if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
        return True

    # asyncio.TimeoutError (subclass of TimeoutError on Python 3.11+)
    if isinstance(exc, asyncio.TimeoutError):  # pragma: no cover - caught by TimeoutError above
        return True

    # Default: not retryable (fail fast on unknown errors)
    return False


# ---------------------------------------------------------------------------
# Backoff wrapper
# ---------------------------------------------------------------------------

async def with_backoff(
    fn: Callable[[], AsyncGenerator[dict[str, Any], None]],
    *,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> AsyncGenerator[dict[str, Any], None]:
    """Retry an async-generator–returning callable with exponential backoff.

    Parameters
    ----------
    fn:
        A zero-argument callable that returns an ``AsyncGenerator[dict, None]``.
        Typically a lambda wrapping the provider's ``stream()`` call.
    max_retries:
        Maximum number of retry attempts (not counting the initial call).
    base_delay:
        Base delay in seconds for the first retry.
    max_delay:
        Maximum delay cap in seconds.

    Yields the same events as the underlying generator. On the final failed
    attempt the exception propagates (so callers can catch and yield an error
    event as usual).
    """
    last_exc: BaseException | None = None

    for attempt in range(max_retries + 1):
        try:
            async for event in fn():
                yield event
            return  # success — generator completed
        except Exception as exc:
            last_exc = exc

            if not is_retryable(exc):
                raise  # non-retryable — bail immediately

            if attempt >= max_retries:
                raise  # exhausted retries — let caller handle

            delay = _compute_delay(attempt, base_delay, max_delay)
            logger.warning(
                "Retryable error (attempt %d/%d), retrying in %.1fs: %s",
                attempt + 1,
                max_retries + 1,
                delay,
                exc,
            )
            await asyncio.sleep(delay)


def _compute_delay(attempt: int, base_delay: float, max_delay: float) -> float:
    """Exponential backoff with full jitter.

    delay = min(max_delay, base_delay * 2^attempt) * random(0.5, 1.0)
    """
    exp_delay = base_delay * (2 ** attempt)
    capped = min(exp_delay, max_delay)
    jittered = capped * random.uniform(0.5, 1.0)
    return jittered
