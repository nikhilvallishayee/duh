#!/usr/bin/env python3
"""02 — Persistent state across daemon crash.

OpenClaw's headline property is "always-on": you can SIGKILL the
gateway and bring it back, and the inbox has not lost work. The
duhwave realisation is the append-only ``triggers.jsonl`` file under
``<waves_root>/`` plus :meth:`TriggerLog.replay`.

This script:

    1. Packs the parity-claw bundle.
    2. Installs it into a tempdir-rooted ``waves/`` root (the user's
       real ``~/.duh/`` is never touched).
    3. Starts the daemon as a subprocess and waits for its socket.
    4. Appends THREE manual-seam triggers directly to the host's
       ``triggers.jsonl`` (the same path the listener would write to).
    5. SIGKILL's the daemon — not SIGTERM. This simulates a crash:
       no shutdown handler runs.
    6. Restarts the daemon.
    7. Calls ``TriggerLog.replay()`` and asserts the same three
       triggers come back, in order.
    8. Stops cleanly and uninstalls.

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/parity_claw/02_persistent_state.py
"""
from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.duhwave.bundle import (  # noqa: E402
    BUNDLE_EXT,
    BundleInstaller,
    pack_bundle,
)
from duh.duhwave.cli.rpc import (  # noqa: E402
    HostRPCError,
    call as rpc_call,
    host_pid_path,
    host_socket_path,
    is_daemon_running,
)
from duh.duhwave.ingress import Trigger, TriggerKind, TriggerLog  # noqa: E402

SPEC_DIR = Path(__file__).parent.resolve()


def section(title: str) -> None:
    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)


def step(msg: str) -> None:
    print(f"  -> {msg}")


def ok(msg: str) -> None:
    print(f"  \u2713 {msg}")


def fail(msg: str) -> None:
    print(f"  x {msg}")


@dataclass(slots=True)
class _DaemonHandle:
    proc: subprocess.Popen[bytes]
    waves_root: Path

    def kill(self) -> int:
        """SIGKILL — simulates a crash. No cleanup code runs."""
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGKILL)
            return self.proc.wait(timeout=2.0)
        return self.proc.returncode or 0

    def stop(self, *, timeout: float = 5.0) -> int:
        """Graceful SIGTERM — for clean shutdown at the end."""
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                return self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                return self.proc.wait(timeout=timeout)
        return self.proc.returncode or 0


def _spawn_daemon(waves_root: Path) -> _DaemonHandle:
    """Spawn ``python -m duh.duhwave.cli.daemon <waves_root>`` in the background."""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(_REPO) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "duh.duhwave.cli.daemon", str(waves_root)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return _DaemonHandle(proc=proc, waves_root=waves_root)


def _wait_for_socket(
    waves_root: Path,
    *,
    timeout: float = 5.0,
    proc: subprocess.Popen[bytes] | None = None,
    expect_pid: int | None = None,
) -> None:
    """Block until the host binds its Unix socket + writes its pidfile.

    If ``expect_pid`` is given, also verify the pidfile contains that PID
    (otherwise after a SIGKILL we'd see the dead daemon's stale files
    and falsely think the new daemon is up).
    """
    deadline = time.monotonic() + timeout
    sock = host_socket_path(waves_root)
    pid_path = host_pid_path(waves_root)
    while time.monotonic() < deadline:
        if sock.exists() and pid_path.exists():
            if expect_pid is None:
                return
            try:
                actual = int(pid_path.read_text().strip())
            except (ValueError, OSError):
                actual = -1
            if actual == expect_pid:
                return
        if proc is not None and proc.poll() is not None:
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            raise RuntimeError(
                f"daemon exited prematurely (rc={proc.returncode}): {stderr}"
            )
        time.sleep(0.05)
    raise TimeoutError(f"daemon did not bind socket within {timeout}s")


def _wait_for_socket_gone(waves_root: Path, *, timeout: float = 3.0) -> None:
    """After SIGKILL the host can't clean up its socket — but `is_daemon_running`
    returns False because the pidfile points to a dead PID. Wait for that signal.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not is_daemon_running(waves_root):
            return
        time.sleep(0.05)
    # Not fatal — proceed anyway. The next start will unlink the stale socket.


def main() -> int:
    section("02 - persistence across SIGKILL")
    print("  pack -> install -> start -> append 3 triggers -> SIGKILL")
    print("  -> restart -> replay -> verify 3/3 survived -> uninstall")

    # macOS AF_UNIX has a ~104-byte path cap. Root waves_root via mkdtemp
    # so the host socket path stays short.
    waves_root = Path(tempfile.mkdtemp(prefix="dwv-claw-")).resolve()
    install_root = Path(tempfile.mkdtemp(prefix="dwv-install-")).resolve()
    out_dir = Path(tempfile.mkdtemp(prefix="dwv-out-")).resolve()

    rc = 1
    handle: _DaemonHandle | None = None
    try:
        # ---- pack + install ---------------------------------------
        section("1. Pack + install bundle")
        bundle = out_dir / f"parity-claw-0.1.0{BUNDLE_EXT}"
        pack_bundle(SPEC_DIR, bundle)
        ok(f"packed: {bundle.name} ({bundle.stat().st_size:,} bytes)")
        installer = BundleInstaller(root=install_root, confirm=lambda _: True)
        result = installer.install(bundle, public_key_path=None)
        ok(f"installed: {result.name} v{result.version} trust={result.trust_level}")

        # ---- start daemon -----------------------------------------
        section("2. Start daemon")
        handle = _spawn_daemon(waves_root)
        _wait_for_socket(waves_root, timeout=5.0, proc=handle.proc)
        ok(f"daemon up: PID {host_pid_path(waves_root).read_text().strip()}")
        pong = rpc_call(waves_root, {"op": "ping"})
        ok(f"ping -> {pong}")

        # ---- append 3 triggers ------------------------------------
        section("3. Append three manual triggers to triggers.jsonl")
        triggers_path = waves_root / "triggers.jsonl"
        log = TriggerLog(triggers_path)
        sources = ("manual:nudge-A", "manual:nudge-B", "manual:nudge-C")
        for src in sources:
            tr = Trigger(
                kind=TriggerKind.MANUAL,
                source=src,
                payload={"label": src, "ts": time.time()},
            )
            log.append(tr)
            step(f"appended {tr.kind.value} source={tr.source} cid={tr.correlation_id[:8]}")
        ok(f"triggers.jsonl size = {triggers_path.stat().st_size} bytes")
        before = TriggerLog(triggers_path).replay()
        ok(f"pre-crash replay -> {len(before)} triggers")
        if len(before) != 3:
            fail(f"expected 3 triggers pre-crash, got {len(before)}")
            return 1

        # ---- SIGKILL ----------------------------------------------
        section("4. SIGKILL the daemon (simulate crash)")
        crashed_pid = host_pid_path(waves_root).read_text().strip()
        rc_kill = handle.kill()
        ok(f"daemon PID {crashed_pid} killed (rc={rc_kill})")
        handle = None
        _wait_for_socket_gone(waves_root, timeout=2.0)
        if is_daemon_running(waves_root):
            fail("daemon still appears running after SIGKILL")
            return 1
        ok("is_daemon_running -> False")

        # ---- restart ---------------------------------------------
        section("5. Restart daemon")
        # The crashed daemon left its socket file + pidfile behind. The
        # new daemon unlinks the socket and overwrites the pidfile during
        # startup (see _Host.run in daemon.py). Pass `expect_pid` so we
        # don't falsely return on the dead daemon's stale files.
        handle = _spawn_daemon(waves_root)
        _wait_for_socket(
            waves_root,
            timeout=5.0,
            proc=handle.proc,
            expect_pid=handle.proc.pid,
        )
        ok(f"daemon up again: PID {host_pid_path(waves_root).read_text().strip()}")
        pong = rpc_call(waves_root, {"op": "ping"})
        ok(f"ping -> {pong}")

        # ---- replay verifies persistence --------------------------
        section("6. Replay TriggerLog post-restart")
        after = TriggerLog(triggers_path).replay()
        ok(f"replay returned {len(after)} trigger(s)")
        for tr in after:
            print(f"    {tr.kind.value:<8}  source={tr.source:<18}  "
                  f"cid={tr.correlation_id[:8]}")
        if len(after) != len(before):
            fail(f"expected {len(before)} replayed, got {len(after)}")
            return 1
        # Verify identity preserved (correlation_id + source).
        before_ids = [(t.source, t.correlation_id) for t in before]
        after_ids = [(t.source, t.correlation_id) for t in after]
        if before_ids != after_ids:
            fail(f"replay shape mismatch:\n   before={before_ids}\n   after ={after_ids}")
            return 1
        ok("all 3 triggers survived crash, in original order, with original cids")

        rc = 0

        # ---- clean shutdown ---------------------------------------
        section("7. Clean shutdown + uninstall")
        rc_stop = handle.stop(timeout=5.0)
        handle = None
        ok(f"daemon stopped cleanly (rc={rc_stop})")
        try:
            rpc_call(waves_root, {"op": "ping"}, timeout=0.3)
            fail("RPC after shutdown succeeded - daemon still alive?")
            rc = 1
        except HostRPCError:
            ok("RPC after shutdown raises HostRPCError as expected")
        if installer.uninstall("parity-claw"):
            ok("bundle uninstalled cleanly")

        section("Result")
        ok("persistent across crash")
        return rc
    finally:
        if handle is not None and handle.proc.poll() is None:
            handle.proc.kill()
            try:
                handle.proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                pass
        for d in (waves_root, install_root, out_dir):
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
