"""Deterministic stub engine for snapshot boot scripts.

The real :class:`duh.kernel.engine.Engine` owns a provider connection, a
session store, a hook registry and many other moving parts.  For visual
snapshots we don't want *any* of that machinery — we want a frozen
screen that captures one specific UI state.

:class:`StubEngine` provides the minimum public surface ``DuhApp``
touches:

* ``run(prompt)`` — an async generator that yields a caller-supplied
  sequence of engine events (``text_delta``, ``tool_use``,
  ``tool_result``, ``assistant``, ``done``, ...).  Tests that need to
  capture a *mid-streaming* state pass a sentinel event that the boot
  script uses to call ``app.exit()`` at the right moment.
* ``_messages``, ``_session_id``, ``_session_store``, ``_config`` and
  ``_deps`` — attributes ``DuhApp`` reads directly.  Each is either an
  empty sentinel or a minimal stand-in that satisfies the shape
  ``DuhApp`` expects.
* ``total_input_tokens`` / ``total_output_tokens`` properties for the
  status-bar math.

Nothing in this module touches the network, the filesystem, or the real
event loop's timers in a way that affects the rendered screen.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable


@dataclass
class _StubConfig:
    """Stand-in for :class:`duh.kernel.engine.EngineConfig`."""

    model: str = "stub-model"
    system_prompt: str = ""
    max_turns: int = 10
    max_cost: float | None = None


@dataclass
class _StubMessage:
    """Stand-in for :class:`duh.kernel.messages.Message`.

    ``DuhApp`` only reads ``role``, ``content`` and ``metadata`` off the
    message objects it inspects, so a plain dataclass suffices.
    """

    role: str
    content: Any
    metadata: dict[str, Any] = field(default_factory=dict)


class _NullSessionStore:
    """Session-store stub that promises no sessions and no persistence."""

    async def list_sessions(self) -> list[dict[str, Any]]:
        return []

    async def save(self, *_args: Any, **_kwargs: Any) -> None:
        return None

    async def load(self, *_args: Any, **_kwargs: Any) -> list[Any]:
        return []


class StubEngine:
    """Deterministic engine replacement for snapshot tests.

    Parameters
    ----------
    events:
        The sequence of events ``run()`` yields on its first call.  Each
        event is a plain ``dict`` with a ``type`` key matching the
        vocabulary described in :mod:`duh.kernel.engine`.
    model:
        Display name for the model; surfaces in the status bar.
    messages:
        Initial message history (defaults to empty).  ``DuhApp`` uses
        this to populate resumed-session snapshots.
    """

    def __init__(
        self,
        events: list[dict[str, Any]] | None = None,
        *,
        model: str = "stub-model",
        messages: list[_StubMessage] | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
    ) -> None:
        self._events = list(events or [])
        self._messages: list[_StubMessage] = list(messages or [])
        self._session_id = "snapshot-00000000"
        self._config = _StubConfig(model=model)
        self._session_store = _NullSessionStore()
        self._deps = None
        self._total_input_tokens = input_tokens
        self._total_output_tokens = output_tokens

    # ----- properties DuhApp reads -----

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def total_input_tokens(self) -> int:
        return self._total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._total_output_tokens

    # ----- the event stream -----

    async def run(self, _prompt: str) -> AsyncIterator[dict[str, Any]]:
        """Yield the canned event sequence and stop.

        Declared via an inner async generator so the method itself
        returns an async iterator on first call (matches the real
        Engine's behaviour).
        """

        async def _gen() -> AsyncIterator[dict[str, Any]]:
            for event in self._events:
                # A small await lets the Textual worker loop interleave
                # so streaming widgets actually update on screen.
                await asyncio.sleep(0)
                yield event

        return _gen()


def build_resumed_messages(n: int) -> list[dict[str, Any]]:
    """Produce ``n`` canned message dicts for the resumed-session snapshot.

    Alternates user / assistant.  Content is a short deterministic
    string so every run produces the same rendered widget.
    """
    out: list[dict[str, Any]] = []
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        out.append({
            "role": role,
            "content": f"Sample {role} message #{i + 1}.",
            "metadata": {},
        })
    return out


def exit_after(app: Any, delay: float = 0.25) -> None:
    """Schedule ``app.exit()`` after *delay* seconds.

    Used by boot scripts that need to let the app render one or more
    ticks before the snapshot is taken.  The delay is intentionally
    short — long enough for Textual to paint the first frame, short
    enough to keep snapshot-generation fast.
    """
    loop = asyncio.get_event_loop()
    loop.call_later(delay, lambda: app.exit(0))
