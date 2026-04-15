"""Tests for VCR (record/replay) adapter."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest

from duh.adapters.vcr import VCR


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures"
SIMPLE_TEXT_FIXTURE = FIXTURE_DIR / "simple_text.jsonl"


async def _fake_provider(**kwargs: Any) -> AsyncGenerator[dict[str, Any], None]:
    """A minimal fake provider that yields three events."""
    yield {"type": "text_delta", "text": "fake response"}
    yield {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "fake response"}],
            "metadata": {"stop_reason": "end_turn", "model": "fake-model"},
        },
    }
    yield {"type": "done", "stop_reason": "end_turn", "turns": 1}


async def _collect(gen: AsyncGenerator) -> list[dict[str, Any]]:
    """Drain an async generator into a list."""
    results = []
    async for item in gen:
        results.append(item)
    return results


# ---------------------------------------------------------------------------
# Replay mode
# ---------------------------------------------------------------------------


class TestReplayMode:
    @pytest.mark.asyncio
    async def test_replay_yields_recorded_events(self):
        vcr = VCR(fixture_path=SIMPLE_TEXT_FIXTURE, mode="replay")
        events = await _collect(vcr.stream())

        assert len(events) == 3
        assert events[0]["type"] == "text_delta"
        assert events[0]["text"] == "Hello, world!"
        assert events[1]["type"] == "assistant"
        assert events[2]["type"] == "done"

    @pytest.mark.asyncio
    async def test_replay_preserves_event_content(self):
        vcr = VCR(fixture_path=SIMPLE_TEXT_FIXTURE, mode="replay")
        events = await _collect(vcr.stream())

        assistant_event = events[1]
        assert assistant_event["message"]["role"] == "assistant"
        assert assistant_event["message"]["content"][0]["text"] == "Hello, world!"

        done_event = events[2]
        assert done_event["stop_reason"] == "end_turn"
        assert done_event["turns"] == 1

    @pytest.mark.asyncio
    async def test_replay_missing_fixture_raises_file_not_found(self):
        vcr = VCR(fixture_path=Path("/nonexistent/fixture.jsonl"), mode="replay")
        with pytest.raises(FileNotFoundError, match="VCR fixture not found"):
            await _collect(vcr.stream())


# ---------------------------------------------------------------------------
# Record mode
# ---------------------------------------------------------------------------


class TestRecordMode:
    @pytest.mark.asyncio
    async def test_record_captures_and_yields_events(self, tmp_path: Path):
        fixture_file = tmp_path / "recorded.jsonl"
        vcr = VCR(
            fixture_path=fixture_file,
            mode="record",
            real_call_model=_fake_provider,
        )

        events = await _collect(vcr.stream(messages=[], model="fake-model"))

        # Events were yielded to the caller
        assert len(events) == 3
        assert events[0]["type"] == "text_delta"
        assert events[1]["type"] == "assistant"
        assert events[2]["type"] == "done"

        # Events were written to the fixture file
        assert fixture_file.exists()
        lines = fixture_file.read_text().strip().split("\n")
        assert len(lines) == 3
        for line in lines:
            parsed = json.loads(line)
            assert "type" in parsed

    @pytest.mark.asyncio
    async def test_record_creates_parent_dirs(self, tmp_path: Path):
        fixture_file = tmp_path / "deep" / "nested" / "dir" / "recorded.jsonl"
        vcr = VCR(
            fixture_path=fixture_file,
            mode="record",
            real_call_model=_fake_provider,
        )

        await _collect(vcr.stream(messages=[]))
        assert fixture_file.exists()

    @pytest.mark.asyncio
    async def test_record_without_provider_raises(self, tmp_path: Path):
        fixture_file = tmp_path / "recorded.jsonl"
        vcr = VCR(fixture_path=fixture_file, mode="record")

        with pytest.raises(RuntimeError, match="real_call_model"):
            await _collect(vcr.stream(messages=[]))

    @pytest.mark.asyncio
    async def test_recorded_fixture_is_replayable(self, tmp_path: Path):
        fixture_file = tmp_path / "roundtrip.jsonl"

        # Record
        recorder = VCR(
            fixture_path=fixture_file,
            mode="record",
            real_call_model=_fake_provider,
        )
        recorded_events = await _collect(recorder.stream(messages=[]))

        # Replay
        player = VCR(fixture_path=fixture_file, mode="replay")
        replayed_events = await _collect(player.stream())

        assert recorded_events == replayed_events


# ---------------------------------------------------------------------------
# Passthrough mode
# ---------------------------------------------------------------------------


class TestPassthroughMode:
    @pytest.mark.asyncio
    async def test_passthrough_forwards_events(self, tmp_path: Path):
        fixture_file = tmp_path / "unused.jsonl"
        vcr = VCR(
            fixture_path=fixture_file,
            mode="passthrough",
            real_call_model=_fake_provider,
        )

        events = await _collect(vcr.stream(messages=[]))
        assert len(events) == 3
        assert events[0]["type"] == "text_delta"

        # No fixture file should be created
        assert not fixture_file.exists()

    @pytest.mark.asyncio
    async def test_passthrough_without_provider_raises(self, tmp_path: Path):
        vcr = VCR(fixture_path=Path("unused.jsonl"), mode="passthrough")
        with pytest.raises(RuntimeError, match="real_call_model"):
            await _collect(vcr.stream(messages=[]))


# ---------------------------------------------------------------------------
# wrap()
# ---------------------------------------------------------------------------


class TestWrap:
    @pytest.mark.asyncio
    async def test_wrap_returns_callable(self):
        vcr = VCR(fixture_path=SIMPLE_TEXT_FIXTURE, mode="replay")
        wrapped = vcr.wrap(_fake_provider)

        assert callable(wrapped)

    @pytest.mark.asyncio
    async def test_wrap_replay_ignores_real_provider(self):
        vcr = VCR(fixture_path=SIMPLE_TEXT_FIXTURE, mode="replay")
        wrapped = vcr.wrap(_fake_provider)

        events = await _collect(wrapped(messages=[]))

        # Should get events from the fixture, not from _fake_provider
        assert events[0]["text"] == "Hello, world!"  # fixture content
        assert events[0]["text"] != "fake response"  # not from fake provider

    @pytest.mark.asyncio
    async def test_wrap_record_captures_from_real_provider(self, tmp_path: Path):
        fixture_file = tmp_path / "wrap_recorded.jsonl"
        vcr = VCR(fixture_path=fixture_file, mode="record")
        wrapped = vcr.wrap(_fake_provider)

        events = await _collect(wrapped(messages=[]))

        assert len(events) == 3
        assert events[0]["text"] == "fake response"
        assert fixture_file.exists()

    @pytest.mark.asyncio
    async def test_wrap_passthrough_delegates(self, tmp_path: Path):
        fixture_file = tmp_path / "unused.jsonl"
        vcr = VCR(fixture_path=fixture_file, mode="passthrough")
        wrapped = vcr.wrap(_fake_provider)

        events = await _collect(wrapped(messages=[]))
        assert len(events) == 3
        assert not fixture_file.exists()

    @pytest.mark.asyncio
    async def test_wrap_has_same_signature_as_call_model(self):
        """wrap() returns a callable that accepts **kwargs like call_model."""
        vcr = VCR(fixture_path=SIMPLE_TEXT_FIXTURE, mode="replay")
        wrapped = vcr.wrap(_fake_provider)

        # Should accept the same keyword args as a provider stream()
        events = await _collect(
            wrapped(
                messages=[{"role": "user", "content": "hi"}],
                model="test-model",
                system_prompt="you are helpful",
                tools=None,
                max_tokens=1024,
            )
        )
        assert len(events) == 3


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class TestValidation:
    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError, match="Invalid VCR mode"):
            VCR(fixture_path=Path("test.jsonl"), mode="invalid")

    def test_valid_modes_accepted(self):
        for mode in ("record", "replay", "passthrough"):
            vcr = VCR(fixture_path=Path("test.jsonl"), mode=mode)
            assert vcr.mode == mode
