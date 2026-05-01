"""Integration tests for ``duh wave`` daemon control-plane ops — ADR-032 §C.

End-to-end coverage:

    bundle install → daemon start → CLI command(s) talking to daemon →
    parse the printed JSON → daemon stop.

Each test exercises one CLI handler from
:mod:`duh.duhwave.cli.commands` against a real daemon subprocess
rooted under a tmp_path-style scratch dir. Stdout is captured with
``capsys`` and re-parsed as JSON because the handlers print
``json.dumps(resp, indent=2, ...)`` on the happy path.

Unix-socket only — skipped on Windows like the rest of the duhwave
daemon test surface.
"""
from __future__ import annotations

import argparse
import http.client
import json
import shutil
import signal
import socket as _socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import pytest

from duh.duhwave.bundle import BUNDLE_EXT, BundleInstaller, pack_bundle
from duh.duhwave.cli import commands, rpc
from duh.duhwave.ingress.triggers import TriggerLog, TriggerKind

pytestmark = pytest.mark.skipif(
    not sys.platform.startswith(("linux", "darwin")),
    reason="Unix sockets require Linux or macOS",
)


# ── fixture sources ───────────────────────────────────────────────────

_SWARM_TOML = """\
[swarm]
name = "ops-fixture"
version = "0.1.0"
description = "ops integration fixture"
format_version = 1

[[agents]]
id = "alpha"
role = "researcher"
model = "anthropic/claude-haiku-4-5"
tools = ["search"]

[[agents]]
id = "beta"
role = "implementer"
model = "anthropic/claude-sonnet-4-6"
tools = ["bash"]

[[edges]]
from_agent_id = "alpha"
to_agent_id = "beta"
kind = "spawn"

[budget]
max_tokens_per_hour = 100000
max_usd_per_day = 1.0
max_concurrent_tasks = 2
"""

_MANIFEST_TOML = """\
[bundle]
name = "ops-fixture"
version = "0.1.0"
description = "ops integration fixture"
author = "tests <tests@duhwave.local>"
format_version = 1
created_at = 1700000000.0

[signing]
signed = false
"""

_PERMISSIONS_TOML = """\
[filesystem]
read = ["~/repos/*"]

[network]
allow = []

[tools]
require = ["search", "bash"]
"""


# ── helpers ───────────────────────────────────────────────────────────


def _short_tmp() -> Path:
    """Short tmpdir to keep AF_UNIX sun_path under 104 bytes on macOS."""
    return Path(tempfile.mkdtemp(prefix="duh-ops-")).resolve()


def _build_and_install_bundle(waves_root: Path) -> None:
    src = _short_tmp()
    out = _short_tmp()
    try:
        (src / "swarm.toml").write_text(_SWARM_TOML)
        (src / "manifest.toml").write_text(_MANIFEST_TOML)
        (src / "permissions.toml").write_text(_PERMISSIONS_TOML)
        bundle_path = pack_bundle(src, out / f"ops-fixture{BUNDLE_EXT}")
        BundleInstaller(root=waves_root, confirm=lambda _d: True).install(
            bundle_path, public_key_path=None, force=True
        )
    finally:
        shutil.rmtree(src, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


def _start_daemon(waves_root: Path) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "-m", "duh.duhwave.cli.daemon", str(waves_root)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if rpc.is_daemon_running(waves_root):
            return proc
        if proc.poll() is not None:
            stdout, stderr = proc.communicate(timeout=1)
            pytest.fail(
                f"daemon exited early: rc={proc.returncode} "
                f"stdout={stdout!r} stderr={stderr!r}"
            )
        time.sleep(0.05)
    proc.terminate()
    pytest.fail("daemon did not become ready within 5s")
    return proc  # unreachable, satisfies the type checker


def _stop_daemon(proc: subprocess.Popen) -> None:
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


@pytest.fixture
def live_ops_daemon():
    """Install the ops-fixture bundle then bring up a daemon over it.

    Yields ``(waves_root, swarm_id)``. Cleans up the daemon, the
    waves_root, and any bundle scratch dirs afterwards.
    """
    waves_root = _short_tmp()
    proc: subprocess.Popen | None = None
    try:
        _build_and_install_bundle(waves_root)
        proc = _start_daemon(waves_root)
        yield waves_root, "ops-fixture"
    finally:
        if proc is not None:
            _stop_daemon(proc)
        shutil.rmtree(waves_root, ignore_errors=True)


def _ns(**kw) -> argparse.Namespace:
    return argparse.Namespace(**kw)


# ── tests ─────────────────────────────────────────────────────────────


class TestCmdInspect:
    def test_returns_topology_and_state(self, live_ops_daemon, capsys):
        waves_root, sid = live_ops_daemon
        rc = commands.cmd_inspect(_ns(waves_root=waves_root, swarm_id=sid))
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["ok"] is True
        assert payload["swarm"]["name"] == "ops-fixture"
        # Two agents we declared above.
        ids = {a["id"] for a in payload["swarm"]["agents"]}
        assert ids == {"alpha", "beta"}
        # Edge from alpha → beta is present.
        edges = payload["swarm"]["edges"]
        assert len(edges) == 1
        assert edges[0]["from_agent_id"] == "alpha"
        assert edges[0]["to_agent_id"] == "beta"
        # State block is sane on a fresh install.
        assert payload["state"]["paused"] is False
        assert payload["state"]["active_tasks"] == 0


class TestCmdPauseResume:
    def test_pause_then_resume_round_trip(self, live_ops_daemon, capsys):
        waves_root, sid = live_ops_daemon

        rc = commands.cmd_pause(_ns(waves_root=waves_root, swarm_id=sid))
        assert rc == 0
        pause_out = json.loads(capsys.readouterr().out)
        assert pause_out["paused"] is True

        # Verify via inspect.
        rc = commands.cmd_inspect(_ns(waves_root=waves_root, swarm_id=sid))
        assert rc == 0
        info = json.loads(capsys.readouterr().out)
        assert info["state"]["paused"] is True

        rc = commands.cmd_resume(_ns(waves_root=waves_root, swarm_id=sid))
        assert rc == 0
        resume_out = json.loads(capsys.readouterr().out)
        assert resume_out["paused"] is False

        rc = commands.cmd_inspect(_ns(waves_root=waves_root, swarm_id=sid))
        assert rc == 0
        info2 = json.loads(capsys.readouterr().out)
        assert info2["state"]["paused"] is False


class TestCmdLogs:
    def test_returns_tail_and_total_size(self, live_ops_daemon, capsys):
        waves_root, sid = live_ops_daemon
        # Generate one extra event by pausing.
        commands.cmd_pause(_ns(waves_root=waves_root, swarm_id=sid))
        capsys.readouterr()  # discard pause output

        rc = commands.cmd_logs(
            _ns(
                waves_root=waves_root,
                swarm_id=sid,
                follow=False,
                lines=20,
            )
        )
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["ok"] is True
        assert isinstance(payload["lines"], list)
        assert payload["total_size_bytes"] > 0
        assert payload["follow_supported"] is False
        # The pause we just issued shows up in the tail.
        assert any("swarm.paused" in line for line in payload["lines"])


class TestCmdInspectUnknown:
    def test_unknown_swarm_returns_3(self, live_ops_daemon, capsys):
        waves_root, _sid = live_ops_daemon
        rc = commands.cmd_inspect(
            _ns(waves_root=waves_root, swarm_id="ghost-swarm")
        )
        # _rpc_print returns 3 when the daemon responds with {"error": ...}.
        assert rc == 3
        err = capsys.readouterr().err
        assert "swarm not installed" in err


class TestCmdLsViaDaemon:
    def test_cmd_ls_includes_running_state_block(
        self, live_ops_daemon, capsys
    ):
        """``duh wave ls`` (with daemon up) merges installed + tasks."""
        waves_root, _sid = live_ops_daemon
        rc = commands.cmd_ls(_ns(waves_root=waves_root, json=True))
        assert rc == 0
        out = capsys.readouterr().out
        payload = json.loads(out)
        assert payload["daemon_running"] is True
        # The bundle we installed shows up.
        names = [r["name"] for r in payload["installed"]]
        assert "ops-fixture" in names
        # No running tasks yet — list shape only.
        assert isinstance(payload["tasks"], list)


# ── ADR-031 §B: daemon auto-starts ingress listeners ──────────────────


def _free_port() -> int:
    """Reserve and release an ephemeral TCP port on 127.0.0.1."""
    with _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _build_webhook_swarm_toml(*, name: str, port: int) -> str:
    """A swarm.toml carrying one webhook trigger + an explicit ingress port."""
    return f"""\
[swarm]
name = "{name}"
version = "0.1.0"
description = "webhook listener auto-boot fixture"
format_version = 1

[[agents]]
id = "ingest"
role = "worker"
model = "stub"

[[triggers]]
kind = "webhook"
source = "/hello/*"
target_agent_id = "ingest"

[ingress]
webhook_port = {port}
webhook_host = "127.0.0.1"

[budget]
max_concurrent_tasks = 1
"""


def _install_webhook_bundle(waves_root: Path, *, name: str, port: int) -> None:
    """Pack + install a webhook-triggering bundle into ``waves_root``."""
    src = _short_tmp()
    out = _short_tmp()
    try:
        (src / "swarm.toml").write_text(
            _build_webhook_swarm_toml(name=name, port=port)
        )
        (src / "manifest.toml").write_text(
            _MANIFEST_TOML.replace("ops-fixture", name)
        )
        (src / "permissions.toml").write_text(_PERMISSIONS_TOML)
        bundle_path = pack_bundle(src, out / f"{name}{BUNDLE_EXT}")
        BundleInstaller(root=waves_root, confirm=lambda _d: True).install(
            bundle_path, public_key_path=None, force=True
        )
    finally:
        shutil.rmtree(src, ignore_errors=True)
        shutil.rmtree(out, ignore_errors=True)


def _wait_port_open(host: str, port: int, *, timeout: float = 5.0) -> bool:
    """Poll ``host:port`` until something accepts a TCP connection."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with _socket.create_connection((host, port), timeout=0.25):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def _post_json(host: str, port: int, path: str, body: dict) -> int:
    """One-shot HTTP POST returning the status code."""
    conn = http.client.HTTPConnection(host, port, timeout=3)
    try:
        conn.request(
            "POST",
            path,
            body=json.dumps(body),
            headers={"Content-Type": "application/json"},
        )
        r = conn.getresponse()
        r.read()
        return r.status
    finally:
        conn.close()


class TestDaemonListenerAutoBoot:
    """ADR-031 §B: ``_Host.run`` walks each swarm's triggers and starts
    the appropriate listener. No CLI workaround should be needed.
    """

    def test_webhook_listener_binds_topology_port(self):
        """A webhook trigger in swarm.toml → daemon binds that port."""
        waves_root = _short_tmp()
        proc: subprocess.Popen | None = None
        port = _free_port()
        try:
            _install_webhook_bundle(waves_root, name="auto-webhook", port=port)
            proc = _start_daemon(waves_root)
            # The dispatcher is up the instant the RPC socket is up;
            # listeners boot asynchronously in the same coroutine but
            # may need a tick. Polling the TCP port is the right wait.
            assert _wait_port_open("127.0.0.1", port, timeout=5.0), (
                f"daemon never bound webhook listener to port {port}"
            )
        finally:
            if proc is not None:
                _stop_daemon(proc)
            shutil.rmtree(waves_root, ignore_errors=True)

    def test_post_to_listener_lands_in_trigger_log(self):
        """POST → daemon-managed listener → triggers.jsonl gets one entry."""
        waves_root = _short_tmp()
        proc: subprocess.Popen | None = None
        port = _free_port()
        try:
            _install_webhook_bundle(waves_root, name="auto-webhook2", port=port)
            proc = _start_daemon(waves_root)
            assert _wait_port_open("127.0.0.1", port, timeout=5.0)

            status = _post_json(
                "127.0.0.1",
                port,
                "/hello/world",
                {"action": "ping", "n": 1},
            )
            assert status == 202

            # Allow a moment for the file write to flush.
            log = TriggerLog(waves_root / "triggers.jsonl")
            deadline = time.monotonic() + 3.0
            triggers = []
            while time.monotonic() < deadline:
                triggers = log.replay()
                if triggers:
                    break
                time.sleep(0.05)
            assert len(triggers) == 1, "POST never landed in trigger log"
            t = triggers[0]
            assert t.kind is TriggerKind.WEBHOOK
            assert t.source == "/hello/world"
            assert t.payload == {"action": "ping", "n": 1}
        finally:
            if proc is not None:
                _stop_daemon(proc)
            shutil.rmtree(waves_root, ignore_errors=True)

    def test_sigterm_stops_listener_cleanly(self):
        """SIGTERM → listener releases the TCP port within a few seconds.

        Cleanly, here, means: the next bind on the same port succeeds.
        We can't bind it from this process while the daemon owns it, so
        the test signs off by SIGTERM-ing the daemon and confirming the
        port is reusable on a fresh socket.
        """
        waves_root = _short_tmp()
        proc: subprocess.Popen | None = None
        port = _free_port()
        try:
            _install_webhook_bundle(waves_root, name="auto-webhook3", port=port)
            proc = _start_daemon(waves_root)
            assert _wait_port_open("127.0.0.1", port, timeout=5.0)

            # Issue SIGTERM and wait for the daemon to exit.
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                pytest.fail("daemon did not exit within 5s of SIGTERM")

            # The bind has been released. We wrap in a brief poll so the
            # OS has a chance to reap any TIME_WAIT.
            ok = False
            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    s = _socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)
                    # SO_REUSEADDR matches what aiohttp/asyncio default to.
                    s.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
                    s.bind(("127.0.0.1", port))
                    s.close()
                    ok = True
                    break
                except OSError:
                    time.sleep(0.1)
            assert ok, f"port {port} never released by daemon shutdown"
        finally:
            if proc is not None and proc.poll() is None:
                _stop_daemon(proc)
            shutil.rmtree(waves_root, ignore_errors=True)
