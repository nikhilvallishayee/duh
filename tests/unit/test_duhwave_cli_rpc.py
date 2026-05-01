"""Tests for ``duh.duhwave.cli.rpc`` + ``duh.duhwave.cli.daemon``.

These are the closest things to integration tests in the duhwave
slice: a real subprocess running the daemon, real Unix sockets, real
JSON-over-newline framing. Unix sockets are POSIX-only — the whole
module skips on Windows.
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from duh.duhwave.bundle import BUNDLE_EXT, BundleInstaller, pack_bundle
from duh.duhwave.cli import rpc

# Unix-domain sockets aren't supported on Windows in CPython prior to
# 3.9, and our daemon code uses asyncio.start_unix_server which needs
# them. Skip the entire module on win32 to keep CI green there.
pytestmark = pytest.mark.skipif(
    not sys.platform.startswith(("linux", "darwin")),
    reason="Unix sockets require Linux or macOS",
)


# ---------------------------------------------------------------------------
# is_daemon_running — pure on-disk checks
# ---------------------------------------------------------------------------


class TestIsDaemonRunning:
    def test_no_pid_file_means_not_running(self, tmp_path: Path):
        assert rpc.is_daemon_running(tmp_path) is False

    def test_pid_file_with_dead_pid_means_not_running(self, tmp_path: Path):
        # PID 1 is reserved (init / launchd) so we use a clearly-dead
        # high PID. Find one by spawning + reaping a tiny subprocess.
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        dead_pid = proc.pid

        pid_path = rpc.host_pid_path(tmp_path)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text(str(dead_pid))
        assert rpc.is_daemon_running(tmp_path) is False

    def test_corrupt_pid_file(self, tmp_path: Path):
        pid_path = rpc.host_pid_path(tmp_path)
        pid_path.parent.mkdir(parents=True, exist_ok=True)
        pid_path.write_text("not-a-number")
        assert rpc.is_daemon_running(tmp_path) is False


# ---------------------------------------------------------------------------
# call() — fails clean when no socket
# ---------------------------------------------------------------------------


class TestCallNoSocket:
    def test_raises_host_rpc_error_when_socket_missing(self, tmp_path: Path):
        with pytest.raises(rpc.HostRPCError, match="no host socket"):
            rpc.call(tmp_path, {"op": "ping"})


# ---------------------------------------------------------------------------
# Live daemon — subprocess fixture
# ---------------------------------------------------------------------------


@pytest.fixture
def short_tmp():
    """A tmpdir whose absolute path stays under the 104-char AF_UNIX cap.

    pytest's tmp_path on macOS lives under /private/var/folders/..., which
    can blow the 104-byte sun_path limit once we append `host.sock`.
    """
    short = Path(tempfile.mkdtemp(prefix="duh-rpc-"))
    yield short
    shutil.rmtree(short, ignore_errors=True)


@pytest.fixture
def live_daemon(short_tmp: Path):
    """Spawn the duhwave daemon in a subprocess; tear it down on exit."""
    waves_root = short_tmp / "waves"
    waves_root.mkdir()
    proc = subprocess.Popen(
        [sys.executable, "-m", "duh.duhwave.cli.daemon", str(waves_root)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Poll for readiness: pid file + socket both present and is_daemon_running.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if rpc.is_daemon_running(waves_root):
            break
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=1)
            pytest.fail(
                "daemon exited before becoming ready: "
                f"rc={proc.returncode} stdout={stdout!r} stderr={stderr!r}"
            )
        time.sleep(0.05)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        pytest.fail("daemon never became ready within 5 s")

    try:
        yield waves_root, proc
    finally:
        # Always tear down — first try a clean signal, then SIGKILL.
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass


class TestLiveDaemon:
    def test_ping_round_trip(self, live_daemon):
        waves_root, _proc = live_daemon
        resp = rpc.call(waves_root, {"op": "ping"})
        assert resp.get("ok") is True
        assert resp.get("pong") is True

    def test_unknown_op_returns_error(self, live_daemon):
        waves_root, _proc = live_daemon
        resp = rpc.call(waves_root, {"op": "absolutely-not-a-real-op"})
        assert "error" in resp
        assert "unknown op" in resp["error"]

    def test_ls_tasks_returns_empty_list(self, live_daemon):
        waves_root, _proc = live_daemon
        resp = rpc.call(waves_root, {"op": "ls_tasks"})
        # Empty registry today; the contract is just "returns a list".
        assert resp.get("ok") is True
        assert resp.get("tasks") == []

    def test_inspect_unknown_swarm_returns_error(self, live_daemon):
        waves_root, _proc = live_daemon
        resp = rpc.call(waves_root, {"op": "inspect", "swarm_id": "ghost"})
        assert "error" in resp
        assert "swarm not installed" in resp["error"]

    def test_pause_unknown_swarm_returns_error(self, live_daemon):
        waves_root, _proc = live_daemon
        resp = rpc.call(waves_root, {"op": "pause", "swarm_id": "ghost"})
        assert "error" in resp
        assert "swarm not installed" in resp["error"]

    def test_logs_unknown_swarm_returns_error(self, live_daemon):
        waves_root, _proc = live_daemon
        resp = rpc.call(
            waves_root,
            {"op": "logs", "swarm_id": "ghost", "lines": 10, "follow": False},
        )
        assert "error" in resp
        assert "swarm not installed" in resp["error"]

    def test_shutdown_cleanly_exits_daemon(self, live_daemon):
        waves_root, proc = live_daemon
        resp = rpc.call(waves_root, {"op": "shutdown"})
        assert resp.get("ok") is True
        assert resp.get("shutting_down") is True

        # Daemon should exit on its own within a few seconds.
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.05)
        else:
            pytest.fail("daemon did not exit after shutdown op")

        assert proc.returncode == 0
        # Socket should be unlinked on clean exit.
        assert not rpc.host_socket_path(waves_root).exists()
        assert not rpc.host_pid_path(waves_root).exists()


# ---------------------------------------------------------------------------
# Daemon with a bundle installed — exercise the real control-plane ops
# ---------------------------------------------------------------------------


_TINY_SWARM_TOML = """\
[swarm]
name = "tiny"
version = "0.1.0"
description = "tiny test fixture"
format_version = 1

[[agents]]
id = "solo"
role = "researcher"
model = "anthropic/claude-haiku-4-5"
tools = ["search"]

[[triggers]]
kind = "manual"
source = "test:fire"
target_agent_id = "solo"

[budget]
max_tokens_per_hour = 100000
max_usd_per_day = 1.0
max_concurrent_tasks = 1
"""

_TINY_MANIFEST_TOML = """\
[bundle]
name = "tiny"
version = "0.1.0"
description = "tiny test fixture"
author = "tests <tests@duhwave.local>"
format_version = 1
created_at = 1700000000.0

[signing]
signed = false
"""

_TINY_PERMISSIONS_TOML = """\
[filesystem]
read = ["~/repos/*"]

[network]
allow = []

[tools]
require = ["search"]
"""


def _install_tiny_bundle(waves_root: Path, scratch: Path) -> None:
    """Pack and install the ``tiny`` bundle into ``waves_root``."""
    src = scratch / "tiny-src"
    src.mkdir()
    (src / "swarm.toml").write_text(_TINY_SWARM_TOML)
    (src / "manifest.toml").write_text(_TINY_MANIFEST_TOML)
    (src / "permissions.toml").write_text(_TINY_PERMISSIONS_TOML)
    bundle = pack_bundle(src, scratch / f"tiny{BUNDLE_EXT}")
    BundleInstaller(root=waves_root, confirm=lambda _d: True).install(
        bundle, public_key_path=None, force=True
    )


@pytest.fixture
def live_daemon_with_bundle(short_tmp: Path):
    """Spawn a daemon with one tiny bundle pre-installed.

    Yields ``(waves_root, proc, swarm_id)``. Bundle install happens
    *before* the daemon starts so the host's startup walk picks it up.
    """
    waves_root = short_tmp / "waves"
    waves_root.mkdir()
    scratch = short_tmp / "scratch"
    scratch.mkdir()
    _install_tiny_bundle(waves_root, scratch)

    proc = subprocess.Popen(
        [sys.executable, "-m", "duh.duhwave.cli.daemon", str(waves_root)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if rpc.is_daemon_running(waves_root):
            break
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=1)
            pytest.fail(
                f"daemon exited before becoming ready: rc={proc.returncode} "
                f"stdout={stdout!r} stderr={stderr!r}"
            )
        time.sleep(0.05)
    else:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
        pytest.fail("daemon never became ready within 5 s")

    try:
        yield waves_root, proc, "tiny"
    finally:
        if proc.poll() is None:
            try:
                proc.send_signal(signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass


class TestLiveDaemonWithBundle:
    """Happy-path coverage of the new control-plane ops."""

    def test_ls_tasks_returns_list(self, live_daemon_with_bundle):
        waves_root, _proc, _sid = live_daemon_with_bundle
        resp = rpc.call(waves_root, {"op": "ls_tasks"})
        assert resp.get("ok") is True
        # Real registry, no spawned tasks yet — list shape, possibly empty.
        assert isinstance(resp.get("tasks"), list)

    def test_inspect_known_swarm(self, live_daemon_with_bundle):
        waves_root, _proc, sid = live_daemon_with_bundle
        resp = rpc.call(waves_root, {"op": "inspect", "swarm_id": sid})
        assert resp.get("ok") is True
        swarm = resp["swarm"]
        assert swarm["name"] == "tiny"
        assert swarm["version"] == "0.1.0"
        # Topology fields are all present and parsed.
        assert any(a["id"] == "solo" for a in swarm["agents"])
        assert any(t["target_agent_id"] == "solo" for t in swarm["triggers"])
        assert isinstance(swarm["edges"], list)
        assert swarm["budget"]["max_concurrent_tasks"] == 1
        # State block reports zero tasks + not-paused on a fresh install.
        state = resp["state"]
        assert state["paused"] is False
        assert state["active_tasks"] == 0
        assert state["completed_tasks"] == 0
        assert state["failed_tasks"] == 0
        assert isinstance(state["trigger_log_size"], int)

    def test_pause_then_inspect_shows_paused(self, live_daemon_with_bundle):
        waves_root, _proc, sid = live_daemon_with_bundle
        resp = rpc.call(waves_root, {"op": "pause", "swarm_id": sid})
        assert resp == {"ok": True, "paused": True, "swarm": "tiny"}
        # Idempotency: a second pause is a no-op success.
        resp2 = rpc.call(waves_root, {"op": "pause", "swarm_id": sid})
        assert resp2.get("ok") is True
        assert resp2.get("paused") is True

        info = rpc.call(waves_root, {"op": "inspect", "swarm_id": sid})
        assert info["state"]["paused"] is True

    def test_resume_then_inspect_shows_not_paused(
        self, live_daemon_with_bundle
    ):
        waves_root, _proc, sid = live_daemon_with_bundle
        rpc.call(waves_root, {"op": "pause", "swarm_id": sid})
        resp = rpc.call(waves_root, {"op": "resume", "swarm_id": sid})
        assert resp == {"ok": True, "paused": False, "swarm": "tiny"}
        # Idempotent.
        resp2 = rpc.call(waves_root, {"op": "resume", "swarm_id": sid})
        assert resp2.get("ok") is True
        assert resp2.get("paused") is False

        info = rpc.call(waves_root, {"op": "inspect", "swarm_id": sid})
        assert info["state"]["paused"] is False

    def test_logs_returns_lines_and_size(self, live_daemon_with_bundle):
        waves_root, _proc, sid = live_daemon_with_bundle
        # Host writes a 'host.start' line on startup; logs should
        # surface it.
        resp = rpc.call(
            waves_root,
            {"op": "logs", "swarm_id": sid, "lines": 50, "follow": False},
        )
        assert resp.get("ok") is True
        assert isinstance(resp["lines"], list)
        assert resp["total_size_bytes"] >= 0
        assert resp["follow_supported"] is False
        # Trigger one more event (pause) and confirm the tail grows.
        rpc.call(waves_root, {"op": "pause", "swarm_id": sid})
        resp2 = rpc.call(
            waves_root,
            {"op": "logs", "swarm_id": sid, "lines": 50, "follow": False},
        )
        assert resp2["total_size_bytes"] > resp["total_size_bytes"]
        assert any("swarm.paused" in line for line in resp2["lines"])

    def test_logs_with_zero_lines_returns_default(
        self, live_daemon_with_bundle
    ):
        """``lines=0`` should still return up to the default tail (200)."""
        waves_root, _proc, sid = live_daemon_with_bundle
        resp = rpc.call(
            waves_root,
            {"op": "logs", "swarm_id": sid, "lines": 0, "follow": False},
        )
        assert resp.get("ok") is True
        # Lines list shape; may include the host.start event.
        assert isinstance(resp["lines"], list)


class TestHostStateUnit:
    """Direct in-process unit tests for HostState — no subprocess.

    Useful for empty-event-log / paths-only assertions where spinning
    up a daemon is overkill.
    """

    def test_empty_event_log_returns_empty_lines(self, tmp_path: Path):
        from duh.duhwave.cli.host_state import HostState
        from duh.duhwave.spec.parser import (
            AgentSpec,
            BudgetSpec,
            SwarmSpec,
        )

        spec = SwarmSpec(
            name="x",
            version="0.0.0",
            description="",
            format_version=1,
            agents=(AgentSpec(id="a", role="researcher", model="m"),),
            triggers=(),
            edges=(),
            budget=BudgetSpec(),
            secrets=(),
        )
        state = HostState(install_dir=tmp_path / "x" / "0.0.0", spec=spec)
        # Fresh: event log was created (touch) but is zero bytes.
        lines, size = state.tail_event_log(50)
        assert lines == []
        assert size == 0

    def test_pause_resume_idempotent(self, tmp_path: Path):
        from duh.duhwave.cli.host_state import HostState
        from duh.duhwave.spec.parser import (
            AgentSpec,
            BudgetSpec,
            SwarmSpec,
        )

        spec = SwarmSpec(
            name="x",
            version="0.0.0",
            description="",
            format_version=1,
            agents=(AgentSpec(id="a", role="researcher", model="m"),),
            triggers=(),
            edges=(),
            budget=BudgetSpec(),
            secrets=(),
        )
        state = HostState(install_dir=tmp_path / "x" / "0.0.0", spec=spec)
        assert state.is_paused() is False
        state.mark_paused()
        state.mark_paused()  # idempotent
        assert state.is_paused() is True
        state.mark_resumed()
        state.mark_resumed()  # idempotent
        assert state.is_paused() is False
