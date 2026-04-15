"""VCR adapter — record and replay API interactions for deterministic testing.

The VCR pattern lets you capture real provider responses to JSONL fixture files,
then replay them in tests without hitting the network.  Three modes:

- **replay**: Read events from a fixture file, yield them as-is.
- **record**: Call the real provider, write every event to a fixture file,
  and yield them to the caller.
- **passthrough**: Delegate to the real provider with no recording.

Fixture format is JSONL — one JSON object per line, each representing a single
stream event dict (the same dicts that provider ``stream()`` methods yield).

Usage::

    vcr = VCR(fixture_path=Path("tests/fixtures/simple_text.jsonl"), mode="replay")
    deps = Deps(call_model=vcr.stream)
    engine = Engine(deps=deps, tools=[])
    async for event in engine.run("hello"):
        ...
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncGenerator, Callable


class VCR:
    """Record and replay API interactions for deterministic testing."""

    VALID_MODES = ("record", "replay", "passthrough")

    def __init__(
        self,
        fixture_path: Path,
        mode: str = "replay",
        real_call_model: Callable[..., AsyncGenerator[Any, None]] | None = None,
    ) -> None:
        if mode not in self.VALID_MODES:
            raise ValueError(
                f"Invalid VCR mode {mode!r}; expected one of {self.VALID_MODES}"
            )
        self.fixture_path = Path(fixture_path)
        self.mode = mode
        self._real_call_model = real_call_model

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def stream(self, **kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        """Drop-in replacement for a provider's ``stream()`` function."""
        if self.mode == "replay":
            async for event in self._replay():
                yield event
        elif self.mode == "record":
            async for event in self._record(**kwargs):
                yield event
        else:
            # passthrough
            async for event in self._passthrough(**kwargs):
                yield event

    def wrap(self, call_model: Callable[..., AsyncGenerator[Any, None]]) -> Callable[..., AsyncGenerator[Any, None]]:
        """Wrap an existing *call_model* for recording or replay.

        Returns a callable with the same signature as *call_model*.
        In replay mode the wrapped function ignores the real provider and
        yields from the fixture.  In record mode it delegates to *call_model*
        while capturing events.  In passthrough mode it simply delegates.
        """
        wrapped = VCR(
            fixture_path=self.fixture_path,
            mode=self.mode,
            real_call_model=call_model,
        )
        return wrapped.stream

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _replay(self) -> AsyncGenerator[dict[str, Any], None]:
        """Yield events from the fixture file."""
        if not self.fixture_path.exists():
            raise FileNotFoundError(
                f"VCR fixture not found: {self.fixture_path}. "
                f"Record a fixture first with mode='record'."
            )
        with self.fixture_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                yield json.loads(line)

    async def _record(self, **kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        """Call the real provider, record events, and yield them."""
        if self._real_call_model is None:
            raise RuntimeError(
                "VCR in record mode requires a real_call_model. "
                "Pass it via the constructor or use wrap()."
            )
        self.fixture_path.parent.mkdir(parents=True, exist_ok=True)
        with self.fixture_path.open("w", encoding="utf-8") as fh:
            async for event in self._real_call_model(**kwargs):
                fh.write(json.dumps(event, default=str) + "\n")
                yield event

    async def _passthrough(self, **kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        """Delegate to the real provider without recording."""
        if self._real_call_model is None:
            raise RuntimeError(
                "VCR in passthrough mode requires a real_call_model. "
                "Pass it via the constructor or use wrap()."
            )
        async for event in self._real_call_model(**kwargs):
            yield event
