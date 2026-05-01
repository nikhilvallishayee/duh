"""Token-bucket rate limiter — single-threaded, stdlib only."""
from __future__ import annotations

import time


class TokenBucket:
    """Classic token-bucket rate limiter.

    Not thread-safe by design (see ADR). Wrap in a ``Lock`` if needed.
    """

    __slots__ = ("capacity", "refill_rate", "tokens", "_last_refill_ts")

    def __init__(self, capacity: float, refill_rate: float) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be positive")
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self.tokens = float(capacity)
        self._last_refill_ts = time.monotonic()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_refill_ts
        if elapsed > 0:
            self.tokens = min(self.capacity, self.tokens + elapsed * self.refill_rate)
            self._last_refill_ts = now

    def acquire(self, n: int = 1) -> bool:
        if n <= 0:
            raise ValueError("n must be positive")
        self._refill()
        if self.tokens >= n:
            self.tokens -= n
            return True
        return False
