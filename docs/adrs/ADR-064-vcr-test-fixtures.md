# ADR-064: VCR Test Fixtures — Deterministic API Recording

**Status:** Proposed — 2026-04-16
**Date:** 2026-04-16
**Related:** ADR-002 (kernel design), ADR-003 (ports and adapters)

## Context

The VCR pattern (record/replay API responses) is widely used in agent CLI testing. This enables:
- Deterministic tests that don't hit the real API
- Fast test execution (no network latency)
- Fixture-based regression testing

D.U.H. currently uses:
- `StubProvider` for unit tests (returns hardcoded "stub-ok")
- Mocked `call_model` in test helpers
- No fixture recording/replay

The stub provider is too simple for integration tests. Mocks are fragile. VCR-style fixtures would enable realistic, deterministic, fast tests.

## Decision

### VCR Architecture

```python
class VCR:
    """Record and replay API interactions."""
    
    def __init__(self, fixture_path: Path, mode: "record" | "replay" | "passthrough"):
        ...
    
    async def call_model(self, **kwargs) -> AsyncGenerator:
        if self.mode == "replay":
            yield from self._replay(kwargs)
        elif self.mode == "record":
            async for event in real_call_model(**kwargs):
                self._record(event)
                yield event
        else:
            async for event in real_call_model(**kwargs):
                yield event
```

### Fixture Format

JSONL files in `tests/fixtures/`:
```jsonl
{"request": {"messages": [...], "model": "...", "tools": [...]}, "response_events": [...]}
```

### Usage

```python
@pytest.fixture
def vcr():
    return VCR(fixture_path=Path("tests/fixtures/tool_use.jsonl"), mode="replay")

async def test_tool_use_flow(vcr):
    deps = Deps(call_model=vcr.call_model, run_tool=real_executor.run)
    engine = Engine(deps=deps)
    async for event in engine.run("read test.txt"):
        ...
```

### Recording Mode

```bash
DUH_VCR=record duh -p "read test.txt"  # records fixture
DUH_VCR=replay pytest tests/           # replays fixtures
```

## Consequences

### Positive
- Deterministic integration tests
- No API key needed for test runs
- Fast (no network latency)
- Fixtures serve as documentation of expected API behavior

### Negative
- Fixtures become stale when API behavior changes
- Recording requires real API calls (one-time)
- Fixture files can be large (tool results)
- Must hash requests carefully for matching
