"""Tests for ADR-073 Wave 2 / Task 8 — live token counter.

Competitors (Claude Code, OpenCode, Codex) update their token / cost
status on every stream delta. D.U.H. historically updated once per
turn, on ``done``. Task 8 adds a cheap ``usage_delta`` event emitted
mid-stream so renderers can refresh their status lines in real time
without paying for a tokenizer call per chunk.

These tests pin:

1. The engine emits ``usage_delta`` events during ``text_delta``
   streaming and still emits the authoritative ``done`` event.
2. Successive ``usage_delta`` events carry monotonically increasing
   output-token estimates.
3. The RichRenderer ``usage_delta`` method writes to stderr only
   when stderr is a TTY.
4. The PlainRenderer has a no-op ``usage_delta`` (contract parity).
"""

from __future__ import annotations

import sys
from typing import Any, AsyncGenerator
from unittest.mock import MagicMock

import pytest

from duh.cli.repl_renderers import HAS_RICH, PlainRenderer, RichRenderer
from duh.kernel.deps import Deps
from duh.kernel.engine import (
    Engine,
    EngineConfig,
    USAGE_DELTA_CHARS_PER_TOKEN,
    USAGE_DELTA_EMIT_INTERVAL_CHARS,
)
from duh.kernel.messages import Message


# ---------------------------------------------------------------------------
# Engine-side tests — usage_delta events surface during streaming.
# ---------------------------------------------------------------------------


def _make_streaming_model(chunks: list[str]):
    """Build a ``call_model`` stub that emits text_delta then assistant."""

    async def _gen(**_kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
        full = "".join(chunks)
        for chunk in chunks:
            yield {"type": "text_delta", "text": chunk}
        yield {
            "type": "assistant",
            "message": Message(
                role="assistant",
                content=[{"type": "text", "text": full}],
                metadata={"stop_reason": "end_turn"},
            ),
        }

    return _gen


class TestEngineUsageDelta:
    async def test_engine_emits_usage_delta_during_streaming(self):
        # Enough characters to cross at least two emit buckets.
        chunks = ["hello " * 20]  # 120 chars → ~3 emit events
        deps = Deps(call_model=_make_streaming_model(chunks))
        engine = Engine(deps=deps, config=EngineConfig(model="claude-opus-4-6"))

        events = [e async for e in engine.run("stream please")]
        usage_events = [e for e in events if e["type"] == "usage_delta"]

        assert len(usage_events) >= 1, (
            f"Expected at least one usage_delta event, got "
            f"{[e['type'] for e in events]}"
        )

    async def test_usage_delta_carries_monotonic_output_estimate(self):
        # A single long chunk produces a single bucket crossing, so use
        # multiple chunks to trigger successive emits.
        chunk_size = USAGE_DELTA_EMIT_INTERVAL_CHARS + 1
        chunks = ["a" * chunk_size] * 3
        deps = Deps(call_model=_make_streaming_model(chunks))
        engine = Engine(deps=deps, config=EngineConfig(model="claude-opus-4-6"))

        events = [e async for e in engine.run("stream")]
        usage_events = [e for e in events if e["type"] == "usage_delta"]

        assert len(usage_events) >= 2
        outs = [e["output_tokens"] for e in usage_events]
        assert outs == sorted(outs), (
            f"usage_delta output estimates must be non-decreasing: {outs}"
        )
        # Each event marks itself as estimated (not authoritative).
        assert all(e.get("estimated") for e in usage_events)

    async def test_done_event_still_emitted_after_usage_deltas(self):
        chunks = ["a" * (USAGE_DELTA_EMIT_INTERVAL_CHARS + 1)]
        deps = Deps(call_model=_make_streaming_model(chunks))
        engine = Engine(deps=deps, config=EngineConfig(model="claude-opus-4-6"))

        events = [e async for e in engine.run("stream")]
        types = [e["type"] for e in events]

        # Both usage_delta and the authoritative done event appear.
        assert "usage_delta" in types
        assert "done" in types
        # done comes after the last usage_delta.
        done_idx = types.index("done")
        last_usage_idx = max(i for i, t in enumerate(types) if t == "usage_delta")
        assert done_idx > last_usage_idx

    async def test_chars_to_token_heuristic_is_divisible_by_four(self):
        """Guard the public constant — renderers may rely on the ratio."""
        assert USAGE_DELTA_CHARS_PER_TOKEN == 4

    async def test_no_usage_delta_when_no_text_delta(self):
        """A tool-only turn (no text_delta) emits no usage_delta."""

        async def _silent_model(**_kwargs: Any) -> AsyncGenerator[
            dict[str, Any], None,
        ]:
            yield {
                "type": "assistant",
                "message": Message(
                    role="assistant",
                    content=[{"type": "text", "text": ""}],
                    metadata={"stop_reason": "end_turn"},
                ),
            }

        deps = Deps(call_model=_silent_model)
        engine = Engine(deps=deps, config=EngineConfig(model="claude-opus-4-6"))
        events = [e async for e in engine.run("no stream")]
        assert not any(e["type"] == "usage_delta" for e in events)


# ---------------------------------------------------------------------------
# Renderer-side tests — RichRenderer writes a live status line.
# ---------------------------------------------------------------------------


rich_only = pytest.mark.skipif(not HAS_RICH, reason="rich not installed")


class TestPlainRendererUsageDelta:
    def test_plain_renderer_usage_delta_is_noop(self, capsys):
        r = PlainRenderer()
        r.usage_delta(input_tokens=123, output_tokens=456)
        out = capsys.readouterr()
        assert out.out == ""
        assert out.err == ""


@rich_only
class TestRichRendererUsageDelta:
    def test_rich_usage_delta_writes_status_line_on_tty(
        self, monkeypatch, capsys,
    ):
        r = RichRenderer()
        monkeypatch.setattr(sys.stderr, "isatty", lambda: True)
        r.usage_delta(input_tokens=1000, output_tokens=42)
        err = capsys.readouterr().err
        # Carriage return + clear-to-EOL so the line overwrites.
        assert "\r" in err
        # Humanized numbers appear in the status line.
        assert "1,000" in err
        assert "42" in err
        # Marked as an estimate for the user.
        assert "(est)" in err

    def test_rich_usage_delta_silent_on_non_tty(self, monkeypatch, capsys):
        r = RichRenderer()
        monkeypatch.setattr(sys.stderr, "isatty", lambda: False)
        r.usage_delta(input_tokens=1000, output_tokens=42)
        err = capsys.readouterr().err
        # Nothing written — we don't want carriage returns in logs.
        assert err == ""

    def test_rich_usage_delta_updates_internal_counters(self, monkeypatch):
        """The renderer's status-bar state should absorb the estimate.

        When the final ``status_bar`` renders between turns, it should
        see the last known token count rather than zero.
        """
        r = RichRenderer()
        r._err_console = MagicMock()
        r._console = MagicMock()
        monkeypatch.setattr(sys.stderr, "isatty", lambda: False)

        r.usage_delta(input_tokens=999, output_tokens=55, cost=0.004)

        assert r._input_tokens == 999
        assert r._output_tokens == 55
        assert r._cost == 0.004

    def test_rich_usage_delta_isatty_error_treated_as_non_tty(
        self, monkeypatch, capsys,
    ):
        r = RichRenderer()

        def _boom() -> bool:
            raise OSError("closed")

        monkeypatch.setattr(sys.stderr, "isatty", _boom)
        r.usage_delta(input_tokens=10, output_tokens=20)
        # Safe fallback: no partial CSI garbage.
        assert capsys.readouterr().err == ""
