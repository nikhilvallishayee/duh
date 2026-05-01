"""Tests for TokenBucket."""
from __future__ import annotations

import time

import pytest

from implementation import TokenBucket


def test_acquire_within_capacity() -> None:
    bucket = TokenBucket(capacity=5, refill_rate=1)
    assert bucket.acquire(1) is True
    assert bucket.acquire(4) is True
    assert bucket.acquire(1) is False


def test_refill_over_time() -> None:
    bucket = TokenBucket(capacity=2, refill_rate=10)
    assert bucket.acquire(2) is True
    assert bucket.acquire(1) is False
    time.sleep(0.2)  # 0.2s * 10/sec = 2 tokens, clamped to capacity
    assert bucket.acquire(2) is True


def test_invalid_construction() -> None:
    with pytest.raises(ValueError):
        TokenBucket(capacity=0, refill_rate=1)
    with pytest.raises(ValueError):
        TokenBucket(capacity=1, refill_rate=0)
