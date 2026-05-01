"""Tests for ``duh wave logs --follow`` true streaming.

The wire shape is documented in :func:`duh.duhwave.cli.rpc.stream_call`
and :meth:`duh.duhwave.cli.daemon._Host._stream_logs`. Briefly:

* Client sends one newline-delimited JSON request.
* Server responds with zero or more ``:``-prefixed JSON stream items,
  one per line, each carrying either ``{"line", "offset"}`` (a log
  line) or ``{"heartbeat": <ts>}`` (a keepalive).
* Server terminates with an unprefixed ``{"done": true}\\n`` line.
* Client closes the socket to stop a follow.

These tests stand a stripped-down asyncio server against a real Unix
socket on a tmp ``waves_root``, drive it through the public
:func:`stream_call` client, and assert the framing + behaviours. The
poll + heartbeat cadences are monkey-patched to a few milliseconds so
the suite stays fast.

Run with::

    /Users/nomind/Code/duh/.venv/bin/python3 -m pytest \\
        tests/unit/test_duhwave_logs_follow.py -v
"""
from __future__ import annotations

import asyncio
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from duh.duhwave.cli import daemon as daemon_mod
from duh.duhwave.cli import rpc
from duh.duhwave.cli.host_state import HostState
from duh.duhwave.spec.parser import AgentSpec, BudgetSpec, SwarmSpec

# Unix sockets are POSIX-only.
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith(("linux", "darwin")),
    reason="Unix sockets require Linux or macOS",
)


# ---------------------------------------------------------------------------
# Test fixtures: a small asyncio server that hosts ONE swarm and routes
# through the real ``_handle_client`` / ``_stream_logs`` code paths.
# ---------------------------------------------------------------------------


def _make_spec(name: str = "tiny") -> SwarmSpec:
    """A minimal, valid spec — enough for HostState to operate on."""
    return SwarmSpec(
        name=name,
        version="0.0.0",
        description="",
        format_version=1,
        agents=(AgentSpec(id="a", role="researcher", model="m"),),
        triggers=(),
        edges=(),
        budget=BudgetSpec(),
        secrets=(),
    )


@pytest.fixture
def short_tmp():
    """A tmpdir whose absolute path stays under the 104-char AF_UNIX cap."""
    short = Path(tempfile.mkdtemp(prefix="duh-logsf-"))
    yield short
    shutil.rmtree(short, ignore_errors=True)


class _MiniHost:
    """Lightweight stand-in for ``_Host`` that exercises the real
    ``_handle_client`` + ``_stream_logs`` methods.

    The full :class:`duh.duhwave.cli.daemon._Host` walks an installed-
    bundle index, boots ingress listeners, etc., none of which we
    need for tail-streaming tests. We borrow just enough to satisfy
    the methods under test:

    * ``self.swarms`` — single swarm dict so ``_lookup`` resolves.
    * ``self._stopping`` — asyncio.Event the loop honours on shutdown.

    The class deliberately reuses the unbound methods from ``_Host`` so
    we test the *real* code, not a paraphrase.
    """

    _handle_client = daemon_mod._Host._handle_client  # type: ignore[assignment]
    _stream_logs = daemon_mod._Host._stream_logs  # type: ignore[assignment]
    _dispatch = daemon_mod._Host._dispatch  # type: ignore[assignment]
    _lookup = daemon_mod._Host._lookup  # type: ignore[assignment]
    _op_logs = daemon_mod._Host._op_logs  # type: ignore[assignment]
    _op_ls_tasks = daemon_mod._Host._op_ls_tasks  # type: ignore[assignment]
    _op_inspect = daemon_mod._Host._op_inspect  # type: ignore[assignment]
    _op_pause = daemon_mod._Host._op_pause  # type: ignore[assignment]
    _op_resume = daemon_mod._Host._op_resume  # type: ignore[assignment]

    def __init__(self, waves_root: Path, state: HostState) -> None:
        self.waves_root = waves_root
        self.swarms = {state.spec.name: state}
        self._stopping = asyncio.Event()


@pytest.fixture
def server_loop():
    """Boot a background asyncio loop for the duration of one test.

    Yields the loop so fixtures + tests can submit coroutines via
    :func:`asyncio.run_coroutine_threadsafe`. Stops cleanly on
    teardown.
    """
    loop = asyncio.new_event_loop()

    def _run():
        asyncio.set_event_loop(loop)
        loop.run_forever()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    try:
        yield loop
    finally:
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2.0)
        loop.close()


@pytest.fixture
def follow_server(short_tmp: Path, server_loop: asyncio.AbstractEventLoop):
    """Spin up an in-process streaming server bound to a real socket.

    Yields ``(waves_root, state, sock_path, host)``. Tests append to
    ``state.event_log_path`` to drive the streaming loop, then call
    :func:`rpc.stream_call` against ``waves_root`` from the main
    thread.
    """
    waves_root = short_tmp / "waves"
    waves_root.mkdir()
    install_dir = waves_root / "tiny" / "0.0.0"
    state = HostState(install_dir=install_dir, spec=_make_spec("tiny"))

    host = _MiniHost(waves_root, state)
    sock_path = rpc.host_socket_path(waves_root)
    sock_path.unlink(missing_ok=True)

    server = asyncio.run_coroutine_threadsafe(
        asyncio.start_unix_server(host._handle_client, path=str(sock_path)),
        server_loop,
    ).result(timeout=5.0)

    # Mark the daemon as "running" so ``rpc.is_daemon_running`` etc.
    # route through.
    rpc.host_pid_path(waves_root).write_text(str(os.getpid()))

    try:
        yield waves_root, state, sock_path, host
    finally:
        async def _shutdown() -> None:
            server.close()
            await server.wait_closed()
            host._stopping.set()

        try:
            asyncio.run_coroutine_threadsafe(_shutdown(), server_loop).result(
                timeout=2.0
            )
        except Exception:
            pass
        rpc.host_pid_path(waves_root).unlink(missing_ok=True)
        sock_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# 1. Snapshot mode (follow=false) is unchanged — proves backwards compat.
# ---------------------------------------------------------------------------


def test_snapshot_mode_unchanged(follow_server) -> None:
    """``follow=False`` keeps the unary-RPC shape of the legacy path.

    The handler still goes through ``_op_logs``, returns the snapshot
    dict, and uses the one-line response framing :func:`rpc.call`
    expects. No ``:``-prefixed lines, no ``{"done": true}``.
    """
    waves_root, state, _sock, _host = follow_server

    # Seed two log lines.
    state.append_event("seed.one", "first")
    state.append_event("seed.two", "second")

    resp = rpc.call(
        waves_root,
        {"op": "logs", "swarm_id": "tiny", "follow": False, "lines": 50},
    )
    assert resp["ok"] is True
    assert resp["swarm"] == "tiny"
    assert resp["follow_supported"] is False  # legacy field — backward-compat
    # Both seeded lines are present.
    assert any("seed.one" in line for line in resp["lines"])
    assert any("seed.two" in line for line in resp["lines"])


# ---------------------------------------------------------------------------
# 2. Follow mode emits a line that lands in the file mid-stream.
# ---------------------------------------------------------------------------


def test_follow_emits_appended_line(
    follow_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Append a log line during the follow; client receives it."""
    waves_root, state, _sock, _host = follow_server
    monkeypatch.setattr(daemon_mod, "LOGS_FOLLOW_POLL_S", 0.02)
    monkeypatch.setattr(daemon_mod, "LOGS_FOLLOW_HEARTBEAT_S", 60.0)

    # Pre-existing line: should land in the snapshot.
    state.append_event("snap.hello", "snapshot-line")

    received: list[dict[str, Any]] = []
    stop_after_first_appended = threading.Event()

    def on_line(frame: dict[str, Any]) -> None:
        received.append(frame)
        # Once we've seen the *appended* line (not the snapshot one),
        # raise to disconnect cleanly.
        if "line" in frame and "live.world" in frame["line"]:
            stop_after_first_appended.set()
            raise KeyboardInterrupt

    # Append a fresh line from a thread shortly after we connect.
    def appender() -> None:
        time.sleep(0.1)
        state.append_event("live.world", "tail-line")

    threading.Thread(target=appender, daemon=True).start()

    with pytest.raises(KeyboardInterrupt):
        rpc.stream_call(
            waves_root,
            {"op": "logs", "swarm_id": "tiny", "follow": True, "lines": 50},
            on_line,
        )

    assert stop_after_first_appended.is_set()
    # Snapshot line came first, appended line came second (at minimum).
    line_frames = [f for f in received if "line" in f]
    assert any("snap.hello" in f["line"] for f in line_frames)
    assert any("live.world" in f["line"] for f in line_frames)


# ---------------------------------------------------------------------------
# 3. Client KeyboardInterrupt → clean disconnect.
# ---------------------------------------------------------------------------


def test_keyboard_interrupt_disconnects_cleanly(
    follow_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ctrl-C closes the socket; the server's loop notices and unwinds.

    We seed a snapshot line, raise ``KeyboardInterrupt`` from
    ``on_line`` on the first frame, and verify the streaming socket
    file descriptor is closed afterwards.
    """
    waves_root, state, _sock, _host = follow_server
    monkeypatch.setattr(daemon_mod, "LOGS_FOLLOW_POLL_S", 0.02)
    monkeypatch.setattr(daemon_mod, "LOGS_FOLLOW_HEARTBEAT_S", 60.0)
    state.append_event("seed", "x")

    def on_line(frame: dict[str, Any]) -> None:
        # Bail on the very first frame.
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        rpc.stream_call(
            waves_root,
            {"op": "logs", "swarm_id": "tiny", "follow": True, "lines": 5},
            on_line,
        )

    # The socket file descriptor isn't directly accessible from the
    # outside, but a follow-up unary call against the same daemon
    # must still succeed — proves the server didn't wedge on the
    # interrupted connection.
    resp = rpc.call(
        waves_root,
        {"op": "logs", "swarm_id": "tiny", "follow": False, "lines": 5},
    )
    assert resp["ok"] is True


# ---------------------------------------------------------------------------
# 4. Heartbeat lines arrive at the configured cadence.
# ---------------------------------------------------------------------------


def test_heartbeat_arrives_on_quiet_log(
    follow_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No log activity → at least one heartbeat lands within a tight bound."""
    waves_root, _state, _sock, _host = follow_server
    # Tiny heartbeat for testability — 50 ms.
    monkeypatch.setattr(daemon_mod, "LOGS_FOLLOW_POLL_S", 0.01)
    monkeypatch.setattr(daemon_mod, "LOGS_FOLLOW_HEARTBEAT_S", 0.05)

    received: list[dict[str, Any]] = []

    def on_line(frame: dict[str, Any]) -> None:
        received.append(frame)
        if "heartbeat" in frame:
            raise KeyboardInterrupt

    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        rpc.stream_call(
            waves_root,
            {"op": "logs", "swarm_id": "tiny", "follow": True, "lines": 0},
            on_line,
        )

    # First heartbeat should arrive within ~0.5 s comfortably even on
    # CI under load (cadence is 0.05 s).
    elapsed = time.monotonic() - started
    assert elapsed < 2.0
    assert any("heartbeat" in f for f in received)


# ---------------------------------------------------------------------------
# 5. Offset bookkeeping: only NEW lines after the snapshot get streamed
#    with their byte offsets.
# ---------------------------------------------------------------------------


def test_offset_bookkeeping_only_new_lines_streamed_post_snapshot(
    follow_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Snapshot lines share one offset (file size at connect time);
    every subsequent appended line has a strictly larger offset
    matching its position in the file.
    """
    waves_root, state, _sock, _host = follow_server
    monkeypatch.setattr(daemon_mod, "LOGS_FOLLOW_POLL_S", 0.02)
    monkeypatch.setattr(daemon_mod, "LOGS_FOLLOW_HEARTBEAT_S", 60.0)

    # Three snapshot lines pre-connect.
    state.append_event("snap.a", "1")
    state.append_event("snap.b", "2")
    state.append_event("snap.c", "3")
    snapshot_size = state.event_log_path.stat().st_size

    received: list[dict[str, Any]] = []
    expected_after_snapshot = 2  # we'll append two more
    received_appended = threading.Event()

    def on_line(frame: dict[str, Any]) -> None:
        received.append(frame)
        # Stop once we've seen both appended lines.
        appended_count = sum(
            1
            for f in received
            if "line" in f and ("live." in f["line"])
        )
        if appended_count >= expected_after_snapshot:
            received_appended.set()
            raise KeyboardInterrupt

    def appender() -> None:
        time.sleep(0.1)
        state.append_event("live.x", "newer-1")
        state.append_event("live.y", "newer-2")

    threading.Thread(target=appender, daemon=True).start()

    with pytest.raises(KeyboardInterrupt):
        rpc.stream_call(
            waves_root,
            {"op": "logs", "swarm_id": "tiny", "follow": True, "lines": 50},
            on_line,
        )

    assert received_appended.is_set()

    line_frames = [f for f in received if "line" in f]
    snapshot_frames = [f for f in line_frames if f["offset"] == snapshot_size]
    appended_frames = [f for f in line_frames if f["offset"] > snapshot_size]

    # Snapshot frames all share the snapshot offset (everything-up-to-here
    # marker), and there are exactly 3 of them.
    assert len(snapshot_frames) == 3
    # Appended frames have monotonically increasing offsets matching
    # the file's actual size after each line.
    appended_lines = [f for f in appended_frames if "live." in f["line"]]
    assert len(appended_lines) == expected_after_snapshot
    offsets = [f["offset"] for f in appended_lines]
    assert offsets == sorted(offsets)
    # Last appended offset matches the file's final size.
    final_size = state.event_log_path.stat().st_size
    assert appended_lines[-1]["offset"] == final_size


# ---------------------------------------------------------------------------
# 6. ``done`` terminator stops the client cleanly.
# ---------------------------------------------------------------------------


def test_done_terminator_stops_client(
    follow_server, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Server-emitted ``{"done": true}`` ends the client loop without error.

    We push the daemon's ``_stopping`` event from the test thread; the
    streaming loop notices and falls through to its terminator block,
    sending ``{"done": true}`` and closing.
    """
    waves_root, state, _sock, host = follow_server
    monkeypatch.setattr(daemon_mod, "LOGS_FOLLOW_POLL_S", 0.02)
    monkeypatch.setattr(daemon_mod, "LOGS_FOLLOW_HEARTBEAT_S", 60.0)

    state.append_event("seed", "x")
    received: list[dict[str, Any]] = []

    def on_line(frame: dict[str, Any]) -> None:
        received.append(frame)
        # On the first frame, ask the server to stop. The streaming
        # loop should notice ``_stopping`` is set and emit ``done``.
        if len(received) == 1:
            host._stopping.set()

    # No KeyboardInterrupt: the server's terminator should end the loop.
    rpc.stream_call(
        waves_root,
        {"op": "logs", "swarm_id": "tiny", "follow": True, "lines": 5},
        on_line,
    )
    # The fixture re-arms ``_stopping`` on teardown; but we already
    # exited cleanly. ``received`` has at least the seed frame.
    assert any("line" in f and "seed" in f["line"] for f in received)


# ---------------------------------------------------------------------------
# 7. Unknown swarm on the streaming path returns a clean error frame.
# ---------------------------------------------------------------------------


def test_follow_unknown_swarm_returns_error(follow_server) -> None:
    """``follow=True`` against an unknown swarm raises HostRPCError.

    The daemon writes an unprefixed ``{"error": ...}`` line and closes;
    the client's :func:`rpc.stream_call` surfaces that as
    :class:`rpc.HostRPCError` so callers see the same error shape as
    the unary path.
    """
    waves_root, _state, _sock, _host = follow_server

    def on_line(frame: dict[str, Any]) -> None:  # pragma: no cover
        raise AssertionError(f"no stream items expected, got {frame}")

    with pytest.raises(rpc.HostRPCError, match="swarm not installed"):
        rpc.stream_call(
            waves_root,
            {"op": "logs", "swarm_id": "ghost", "follow": True, "lines": 1},
            on_line,
        )
