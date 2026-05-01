# Refined Spec — Token-Bucket Rate Limiter

User request: Add a token-bucket rate limiter to utils.py.

## Acceptance criteria
- A `TokenBucket` class with `capacity` and `refill_rate` (tokens/sec).
- `acquire(n: int = 1) -> bool` returns True iff `n` tokens are available.
- Tokens accrue continuously between calls (no fixed-window jitter).
- Thread-safety not required for v1; document the constraint.
- Zero new external dependencies — stdlib only.

Summary: ship a minimal, dependency-free TokenBucket; defer concurrency.
