# Task (identical prompt given to every agent)

You are working inside a fresh, empty Python package scaffold. Do all
work inside the current working directory. Do not `cd` to any other
path, and do not edit files anywhere else.

## What to build

A **distributed rate limiter** as a standalone Python package.

### Required implementation

A package `ratelimit/` containing:

1. **Two algorithm implementations** behind a shared `RateLimiter`
   abstract base class:
   - `TokenBucketLimiter` — classic token bucket with configurable
     capacity and refill rate.
   - `SlidingWindowLimiter` — sliding-window log or sliding-window
     counter (you choose, defend the choice in the ADR).

2. **A distributed backend abstraction** (`Backend` protocol) with two
   implementations:
   - `InMemoryBackend` — for single-process use and tests.
   - `RedisBackend` — using either Redis + Lua scripts (atomic) or
     Redis transactions with `WATCH`/`MULTI`/`EXEC`. Must handle
     lost-update races correctly.

3. **An `enforce(key, cost=1)` API** on each limiter that returns a
   `Decision(allowed: bool, retry_after_ms: int, remaining: int,
   reset_ts: int)`.

4. **A decorator wrapper** `@rate_limit(limiter, key_fn)` for
   function-level enforcement.

5. **An ASGI middleware** `RateLimitMiddleware` compatible with
   FastAPI / Starlette that emits standard rate-limit response headers
   (`X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset`,
   `Retry-After`).

## Required deliverables

1. **ADR** (`docs/adr/ADR-001-rate-limiter-design.md`) written before
   any implementation. Must cover:
   - Problem statement and scope.
   - Token bucket vs sliding window — when each wins.
   - Choice between sliding-window-log and sliding-window-counter;
     tradeoffs named.
   - Redis concurrency model chosen (Lua script vs `WATCH`/`MULTI`/`EXEC`)
     and why.
   - Clock-skew policy for distributed deployments.
   - At least 2 rejected alternatives with reasons.

2. **Implementation** across `ratelimit/` — abstract base, 2
   algorithms, 2 backends, decorator, middleware. No TODOs, no
   placeholders, no `raise NotImplementedError` in the public surface.

3. **Test suite** (`tests/`) with:
   - Unit tests per class (happy path + boundary conditions).
   - Concurrency tests using `threading` or `asyncio` that drive ≥100
     concurrent requests and assert no over-grant.
   - A `fakeredis`-based integration test for `RedisBackend`.
   - A fuzz/property test using `hypothesis` asserting invariants
     (never grants more than `capacity` tokens in any window of length
     `period`).

4. **Design document** (`docs/design.md`) — 600–1000 words,
   diagram-level (ASCII or mermaid), covering request flow through
   middleware → limiter → backend, failure modes, and degradation
   strategy when Redis is unreachable.

5. **README** with install, usage examples for each algorithm,
   decorator, middleware, and operational caveats.

## Working-tree protocol

- Do **not** `git commit`. Leave every change in the working tree.
- Do **not** `git push`.
- Do create new files freely; do modify existing files freely.
- If you run tests, that is fine. Do not revert changes based on test
  failures; leave the state you produced.

## Scope boundary

You are evaluated on:

- Whether the ADR exists before the first code change and covers the
  required topics honestly.
- Whether the implementation is complete and wired (no stubs in the
  public surface).
- Whether tests actually exercise concurrency (not just sequential
  calls).
- Whether the Redis backend correctly handles lost-update races.
- Whether a hidden adversarial test suite (clock skew, chaos drops,
  per-key isolation, capacity=1 edge, decorator re-entry) passes
  against your code.
- Documentation quality.

A short trailing summary of what you did, written to stdout, is
helpful but not graded.
