# ADR — Token-Bucket Rate Limiter

## Status
Proposed.

## Context
The user wants rate limiting in `utils.py`. A token bucket is the
canonical algorithm: tokens accrue at a fixed rate up to a capacity;
each operation consumes tokens. It is preferable to a fixed-window
counter because it smooths bursts naturally.

## Decision
Implement `TokenBucket` as a single class with three public attributes
and one method.

### API
```
class TokenBucket:
    capacity: float        # max tokens the bucket holds
    refill_rate: float     # tokens added per second
    tokens: float          # current token count
    def acquire(n: int = 1) -> bool
```

### Data model
The bucket stores `(capacity, refill_rate, tokens, last_refill_ts)`.
On each `acquire` call we lazily compute the refill since
`last_refill_ts`, clamp to `capacity`, and atomically (within a single
thread) decide whether to grant `n` tokens.

## Tradeoffs
- **Simple over correct under contention.** No locking; v1 is
  single-threaded. A future v2 can wrap mutations in a `threading.Lock`.
- **Float clock.** We use `time.monotonic()` for a steady clock; this
  costs ~50ns per call but avoids wall-clock skew.
- **Lazy refill.** Cheaper than a background thread; rate is exact at
  acquisition time, not continuously.

## Deferred
- Concurrency (no `Lock`).
- Per-key buckets (caller wraps a dict).
- Persistence across restarts.
