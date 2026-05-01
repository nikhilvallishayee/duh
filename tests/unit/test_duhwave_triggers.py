"""Tests for ``duh.duhwave.ingress.triggers`` — Trigger record + JSONL log.

These cover the data-shape invariants only: serialisation round-trips,
the 64 KB inline-payload guard, and the at-least-once log replay
semantics described in ADR-031 §B.5. Listener integration lives in
``test_duhwave_listeners.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from duh.duhwave.ingress.triggers import (
    MAX_PAYLOAD_BYTES,
    Trigger,
    TriggerKind,
    TriggerLog,
    TriggerOversizeError,
    validate,
)


# ---------------------------------------------------------------------------
# Trigger.to_dict / from_dict
# ---------------------------------------------------------------------------


class TestTriggerSerialisation:
    def test_round_trip_preserves_all_fields(self):
        t = Trigger(
            kind=TriggerKind.WEBHOOK,
            source="github:nikhil/repo",
            payload={"action": "opened", "number": 42},
            raw_ref=None,
        )
        d = t.to_dict()
        # Kind must serialise as its string value, not the enum.
        assert d["kind"] == "webhook"
        assert isinstance(d["kind"], str)

        restored = Trigger.from_dict(d)
        assert restored.kind is TriggerKind.WEBHOOK
        assert restored.source == t.source
        assert restored.payload == t.payload
        assert restored.raw_ref == t.raw_ref
        assert restored.received_at == t.received_at
        # correlation_id stability is the load-bearing property — without
        # it, log replay would assign new IDs to every event.
        assert restored.correlation_id == t.correlation_id

    def test_correlation_id_stable_across_round_trip(self):
        t = Trigger(kind=TriggerKind.MANUAL, source="t:cid")
        cid_before = t.correlation_id
        json_str = json.dumps(t.to_dict())
        restored = Trigger.from_dict(json.loads(json_str))
        assert restored.correlation_id == cid_before

    def test_default_correlation_id_is_unique(self):
        a = Trigger(kind=TriggerKind.MANUAL, source="t:a")
        b = Trigger(kind=TriggerKind.MANUAL, source="t:b")
        assert a.correlation_id != b.correlation_id

    def test_from_dict_accepts_minimal_input(self):
        # received_at + correlation_id default if omitted.
        t = Trigger.from_dict({"kind": "manual", "source": "t:m"})
        assert t.kind is TriggerKind.MANUAL
        assert t.source == "t:m"
        assert t.payload == {}
        assert t.raw_ref is None
        assert isinstance(t.correlation_id, str) and len(t.correlation_id) > 0


# ---------------------------------------------------------------------------
# validate() / oversize guard
# ---------------------------------------------------------------------------


class TestValidate:
    def test_passes_for_small_payload(self):
        t = Trigger(kind=TriggerKind.MANUAL, source="t:ok", payload={"x": 1})
        # Should not raise.
        validate(t)

    def test_raises_when_oversize_and_no_raw_ref(self):
        # Build a payload whose JSON encoding exceeds the cap.
        big = "a" * (MAX_PAYLOAD_BYTES + 100)
        t = Trigger(
            kind=TriggerKind.WEBHOOK,
            source="t:big",
            payload={"body": big},
            raw_ref=None,
        )
        with pytest.raises(TriggerOversizeError):
            validate(t)

    def test_passes_when_oversize_but_raw_ref_set(self):
        big = "a" * (MAX_PAYLOAD_BYTES + 100)
        t = Trigger(
            kind=TriggerKind.WEBHOOK,
            source="t:big",
            payload={"body": big},
            raw_ref="/tmp/spilled.bin",
        )
        # raw_ref present → spec accepts the oversize inline payload.
        validate(t)


# ---------------------------------------------------------------------------
# TriggerLog
# ---------------------------------------------------------------------------


class TestTriggerLog:
    def test_append_then_replay_round_trip(self, tmp_path: Path):
        log_path = tmp_path / "triggers.jsonl"
        log = TriggerLog(log_path)

        triggers = [
            Trigger(kind=TriggerKind.MANUAL, source="t:a", payload={"i": 0}),
            Trigger(kind=TriggerKind.WEBHOOK, source="/hook/1", payload={"i": 1}),
            Trigger(kind=TriggerKind.CRON, source="every-min", payload={"i": 2}),
        ]
        for t in triggers:
            log.append(t)

        replayed = log.replay()
        assert len(replayed) == 3
        # Order is preserved (append-only on-disk log).
        for original, got in zip(triggers, replayed):
            assert got.kind is original.kind
            assert got.source == original.source
            assert got.payload == original.payload
            assert got.correlation_id == original.correlation_id

    def test_replay_missing_file_returns_empty(self, tmp_path: Path):
        log = TriggerLog(tmp_path / "never-written.jsonl")
        assert log.replay() == []

    def test_replay_skips_corrupt_lines(self, tmp_path: Path):
        log_path = tmp_path / "triggers.jsonl"
        log = TriggerLog(log_path)

        log.append(Trigger(kind=TriggerKind.MANUAL, source="t:before"))
        # Write a corrupt line directly.
        with log_path.open("a", encoding="utf-8") as f:
            f.write("this is not json\n")
            f.write("{\"kind\": \"unknown_kind\", \"source\": \"x\"}\n")
            f.write("\n")  # blank line — also tolerated
        log.append(Trigger(kind=TriggerKind.MANUAL, source="t:after"))

        replayed = log.replay()
        # The two valid entries survive; the corrupt + unknown-kind ones drop.
        sources = [t.source for t in replayed]
        assert sources == ["t:before", "t:after"]

    def test_append_creates_parent_dir(self, tmp_path: Path):
        nested = tmp_path / "a" / "b" / "c" / "triggers.jsonl"
        log = TriggerLog(nested)
        log.append(Trigger(kind=TriggerKind.MANUAL, source="t:nest"))
        assert nested.exists()
        assert nested.parent.is_dir()
