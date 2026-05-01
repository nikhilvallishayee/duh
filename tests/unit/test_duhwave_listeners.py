"""Tests for the four duhwave ingress listeners — ADR-031 §B.2.

The webhook + manual-seam listeners exercise their real network/UDS
machinery (these are the ones we'd worry about getting wrong). The
filewatch and cron listeners are guarded by ``pytest.importorskip`` —
``watchfiles`` and ``croniter`` are optional today and we don't fake
them. The MCP-push listener is a documented stub; we verify it can be
constructed and started safely.
"""

from __future__ import annotations

import asyncio
import json
import socket
import tempfile
from pathlib import Path
from typing import Any

import pytest

from duh.duhwave.ingress.manual import ManualSeam
from duh.duhwave.ingress.mcp_push import MCPPushListener, MCPSubscription
from duh.duhwave.ingress.triggers import (
    MAX_PAYLOAD_BYTES,
    Trigger,
    TriggerKind,
    TriggerLog,
)
from duh.duhwave.ingress.webhook import WebhookListener


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _free_port() -> int:
    """Reserve and release an ephemeral TCP port on 127.0.0.1."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def _wait_for(predicate, *, timeout: float = 3.0, interval: float = 0.02):
    """Poll ``predicate()`` until truthy or ``timeout`` elapses."""
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(interval)
    return False


@pytest.fixture
def short_tmp(tmp_path):
    """A tmpdir whose absolute path stays under the AF_UNIX 104-char cap.

    pytest's ``tmp_path`` lives under ``/private/var/folders/...`` on
    macOS, which on its own can blow the 104-byte sun_path limit when
    a socket name is appended. We make a parallel tempdir directly
    under ``/tmp`` (TMPDIR is honoured if set short) for UDS use.
    """
    # `tempfile.mkdtemp()` honours TMPDIR; on macOS CI runners that's
    # typically /tmp/, which keeps us well under 104 chars.
    short = Path(tempfile.mkdtemp(prefix="duh-uds-"))
    yield short
    # Best-effort cleanup; tests should already have torn down sockets.
    import shutil
    shutil.rmtree(short, ignore_errors=True)


# ---------------------------------------------------------------------------
# WebhookListener
# ---------------------------------------------------------------------------


class TestWebhookListener:
    async def test_post_json_appended_as_trigger(self, tmp_path: Path):
        aiohttp = pytest.importorskip("aiohttp")
        log = TriggerLog(tmp_path / "triggers.jsonl")
        port = _free_port()
        listener = WebhookListener(log, port=port, host="127.0.0.1")
        await listener.start()
        try:
            async with aiohttp.ClientSession() as session:
                resp = await session.post(
                    f"http://127.0.0.1:{port}/foo",
                    json={"action": "opened", "n": 7},
                )
                assert resp.status == 202
                body = await resp.json()
                assert "correlation_id" in body
        finally:
            await listener.stop()

        triggers = log.replay()
        assert len(triggers) == 1
        t = triggers[0]
        assert t.kind is TriggerKind.WEBHOOK
        assert t.source == "/foo"
        assert t.payload == {"action": "opened", "n": 7}
        assert t.raw_ref is None

    async def test_oversize_body_spills_to_raw_ref(self, tmp_path: Path):
        aiohttp = pytest.importorskip("aiohttp")
        log = TriggerLog(tmp_path / "triggers.jsonl")
        port = _free_port()
        listener = WebhookListener(log, port=port, host="127.0.0.1")
        await listener.start()
        try:
            big = b"x" * (MAX_PAYLOAD_BYTES + 1024)
            async with aiohttp.ClientSession() as session:
                # Plain octet-stream — listener treats it as raw bytes,
                # which is the spill path.
                resp = await session.post(
                    f"http://127.0.0.1:{port}/spill",
                    data=big,
                    headers={"Content-Type": "application/octet-stream"},
                )
                assert resp.status == 202
        finally:
            await listener.stop()

        triggers = log.replay()
        assert len(triggers) == 1
        t = triggers[0]
        assert t.kind is TriggerKind.WEBHOOK
        assert t.source == "/spill"
        # The raw body must have been spilled, not stored inline.
        assert t.raw_ref is not None
        assert Path(t.raw_ref).exists()
        assert "body" not in t.payload
        assert t.payload.get("body_size") == len(big)

    async def test_stop_is_idempotent(self, tmp_path: Path):
        pytest.importorskip("aiohttp")
        log = TriggerLog(tmp_path / "triggers.jsonl")
        port = _free_port()
        listener = WebhookListener(log, port=port, host="127.0.0.1")
        await listener.start()
        await listener.stop()
        # Second stop must not raise.
        await listener.stop()


# ---------------------------------------------------------------------------
# ManualSeam (Unix socket)
# ---------------------------------------------------------------------------


class TestManualSeam:
    async def test_well_formed_line_appends_trigger(self, short_tmp: Path):
        log = TriggerLog(short_tmp / "triggers.jsonl")
        seam = ManualSeam(log, host_dir=short_tmp)
        await seam.start()
        try:
            reader, writer = await asyncio.open_unix_connection(
                str(seam.socket_path)
            )
            line = json.dumps({"source": "t:rapid", "payload": {"k": "v"}}) + "\n"
            writer.write(line.encode("utf-8"))
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

            # Server is fire-and-forget — wait for the append.
            ok = await _wait_for(lambda: len(log.replay()) >= 1)
            assert ok, "trigger never appeared in the log"
        finally:
            await seam.stop()

        triggers = log.replay()
        assert len(triggers) == 1
        t = triggers[0]
        assert t.kind is TriggerKind.MANUAL
        assert t.source == "t:rapid"
        assert t.payload == {"k": "v"}

    async def test_malformed_json_is_dropped_silently(self, short_tmp: Path):
        log = TriggerLog(short_tmp / "triggers.jsonl")
        seam = ManualSeam(log, host_dir=short_tmp)
        await seam.start()
        try:
            reader, writer = await asyncio.open_unix_connection(
                str(seam.socket_path)
            )
            # Malformed line, then a valid one, all on the same connection.
            writer.write(b"not json at all\n")
            writer.write(
                (
                    json.dumps({"source": "t:after-junk", "payload": {}}) + "\n"
                ).encode("utf-8")
            )
            await writer.drain()
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

            ok = await _wait_for(lambda: len(log.replay()) >= 1)
            assert ok
        finally:
            await seam.stop()

        triggers = log.replay()
        # Malformed line dropped; only the valid one made it through.
        assert len(triggers) == 1
        assert triggers[0].source == "t:after-junk"

    async def test_socket_unlinked_on_stop(self, short_tmp: Path):
        log = TriggerLog(short_tmp / "triggers.jsonl")
        seam = ManualSeam(log, host_dir=short_tmp)
        await seam.start()
        sock_path = seam.socket_path
        assert sock_path.exists()
        await seam.stop()
        assert not sock_path.exists()


# ---------------------------------------------------------------------------
# FileWatchListener — gated on `watchfiles`
# ---------------------------------------------------------------------------


class TestFileWatchListener:
    async def test_added_file_emits_trigger(self, tmp_path: Path):
        pytest.importorskip(
            "watchfiles",
            reason="watchfiles not installed — filewatch listener test skipped",
        )
        from duh.duhwave.ingress.filewatch import FileWatchListener

        watch_dir = tmp_path / "watched"
        watch_dir.mkdir()
        log = TriggerLog(tmp_path / "triggers.jsonl")
        # Tight debounce so the test doesn't take long.
        listener = FileWatchListener(log, [watch_dir], debounce_ms=50)
        await listener.start()
        try:
            # Give the watcher a moment to bind before we touch the dir.
            await asyncio.sleep(0.2)
            (watch_dir / "new.txt").write_text("hello")

            ok = await _wait_for(
                lambda: len(log.replay()) >= 1, timeout=5.0
            )
            assert ok, "filewatch never produced a trigger"
        finally:
            await listener.stop()

        triggers = log.replay()
        assert any(t.kind is TriggerKind.FILEWATCH for t in triggers)
        # At least one batch should report an "added" change.
        added_seen = False
        for t in triggers:
            if t.kind is not TriggerKind.FILEWATCH:
                continue
            for change in t.payload.get("changes", []):  # type: ignore[union-attr]
                if change.get("type") == "added":
                    added_seen = True
                    break
        assert added_seen, "no 'added' change in filewatch payloads"


# ---------------------------------------------------------------------------
# CronListener — gated on `croniter`
# ---------------------------------------------------------------------------


class TestCronListener:
    async def test_invalid_cron_expression_raises(self, tmp_path: Path):
        pytest.importorskip(
            "croniter",
            reason="croniter not installed — cron listener test skipped",
        )
        from duh.duhwave.ingress.cron import CronListener

        log = TriggerLog(tmp_path / "triggers.jsonl")
        listener = CronListener(log, [("not a cron expr", "bad")])
        with pytest.raises(ValueError):
            await listener.start()
        # Whether or not start raised, stop must be safe.
        await listener.stop()

    async def test_valid_cron_expression_starts_and_stops(self, tmp_path: Path):
        pytest.importorskip(
            "croniter",
            reason="croniter not installed — cron listener test skipped",
        )
        from duh.duhwave.ingress.cron import CronListener

        log = TriggerLog(tmp_path / "triggers.jsonl")
        # "* * * * *" = every minute. We don't wait for it to fire — we
        # only verify the schedule is set up cleanly and tears down.
        listener = CronListener(log, [("* * * * *", "every-min")])
        await listener.start()
        try:
            # Internals: one schedule per entry, marked running.
            assert listener._running is True  # type: ignore[attr-defined]
            assert len(listener._schedules) == 1  # type: ignore[attr-defined]
        finally:
            await listener.stop()
        assert listener._running is False  # type: ignore[attr-defined]
        assert len(listener._schedules) == 0  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# MCPPushListener — documented stub
# ---------------------------------------------------------------------------


class TestMCPPushListener:
    async def test_construction_with_subscriptions(self, tmp_path: Path):
        log = TriggerLog(tmp_path / "triggers.jsonl")
        subs = [
            MCPSubscription(server_name="github", method="notifications/issues"),
            MCPSubscription(server_name="slack"),  # all notifications
        ]
        listener = MCPPushListener(log, subscriptions=subs)
        # Stub: start succeeds and emits no triggers.
        await listener.start()
        try:
            assert listener._running is True  # type: ignore[attr-defined]
            # Stub never emits.
            assert log.replay() == []
        finally:
            await listener.stop()
        assert listener._running is False  # type: ignore[attr-defined]

    async def test_on_notification_helper_appends_trigger(self, tmp_path: Path):
        # The future MCP integration will call _on_notification when a
        # JSON-RPC notification arrives. Verify the translation here so
        # the contract is locked in even before the wiring exists.
        log = TriggerLog(tmp_path / "triggers.jsonl")
        listener = MCPPushListener(log)
        listener._on_notification(
            "github", "notifications/issues/opened", {"number": 1}
        )
        triggers = log.replay()
        assert len(triggers) == 1
        t = triggers[0]
        assert t.kind is TriggerKind.MCP_PUSH
        assert t.source == "mcp:github:notifications/issues/opened"
        assert t.payload == {"number": 1}
