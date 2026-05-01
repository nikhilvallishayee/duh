#!/usr/bin/env python3
"""04 — Full bundle: pack, install, daemon, manual trigger, uninstall.

Walks the entire ADR-032 control plane in one runnable script:

    1. Build a swarm.toml + manifest.toml + permissions.toml in a
       tmp_path source tree.
    2. ``pack_bundle`` → produce a deterministic ``.duhwave`` archive.
    3. ``BundleInstaller(tmp_root).install`` → extract into a
       tempdir-rooted ``~/.duh/waves/<name>/<version>/``.
    4. Spawn the daemon (``python -m duh.duhwave.cli.daemon``) as a
       background subprocess.
    5. Wait for the host RPC socket; ping it; assert pong.
    6. Send a manual trigger via a :class:`ManualSeam` wired to the
       same trigger log the daemon would use.
    7. Verify the trigger landed by replaying ``triggers.jsonl``.
    8. SIGTERM the daemon; uninstall the bundle.

This script never touches the user's actual ``~/.duh/`` directory.

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/04_topology_bundle.py

Self-contained. No model calls. No external network.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
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
from duh.duhwave.ingress.manual import ManualSeam  # noqa: E402
from duh.duhwave.ingress.triggers import TriggerLog  # noqa: E402


# ---- inline bundle source ------------------------------------------------

_SWARM_TOML = """\
[swarm]
name = "topology-demo"
version = "0.1.0"
description = "topology demo: full bundle install + daemon + trigger"
format_version = 1

[[agents]]
id = "watcher"
role = "researcher"
model = "anthropic/claude-haiku-4-5"
tools = ["search"]

[[triggers]]
kind = "manual"
source = "demo:fire"
target_agent_id = "watcher"

[budget]
max_tokens_per_hour = 100000
max_usd_per_day = 1.0
max_concurrent_tasks = 1
"""

_MANIFEST_TOML = """\
[bundle]
name = "topology-demo"
version = "0.1.0"
description = "topology bundle demo (no model calls)"
author = "examples <examples@duhwave.local>"
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
require = ["search"]
"""


# ---- pretty output -------------------------------------------------------


def section(title: str) -> None:
    print()
    print("=" * 60)
    print(f"  {title}")
    print("=" * 60)


def step(msg: str) -> None:
    print(f"  → {msg}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


# ---- daemon helpers -----------------------------------------------------


@dataclass(slots=True)
class _DaemonHandle:
    proc: subprocess.Popen[bytes]
    waves_root: Path

    def stop(self, *, timeout: float = 5.0) -> int:
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                return self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                return self.proc.wait(timeout=timeout)
        return self.proc.returncode or 0


def _spawn_daemon(waves_root: Path) -> _DaemonHandle:
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")
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
) -> None:
    deadline = time.monotonic() + timeout
    sock = host_socket_path(waves_root)
    pid = host_pid_path(waves_root)
    while time.monotonic() < deadline:
        if sock.exists() and pid.exists():
            return
        if proc is not None and proc.poll() is not None:
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            raise RuntimeError(
                f"daemon exited prematurely (rc={proc.returncode}): {stderr}"
            )
        time.sleep(0.05)
    raise TimeoutError(f"daemon did not bind socket within {timeout}s")


# ---- the demo -----------------------------------------------------------


async def main() -> int:
    section("Topology bundle demo — ADR-032 control plane")
    print()
    print("  pack → install → daemon → trigger → uninstall")
    print("  All paths under tempdirs. Never touches the user's ~/.duh/.")

    # AF_UNIX has a ~104-byte path limit on macOS. Keep the daemon's
    # socket path short by rooting waves_root in /var/folders/T/dwv-XXXX
    # (via tempfile.mkdtemp) rather than nested under tmp_path.
    waves_root = Path(tempfile.mkdtemp(prefix="dwv-")).resolve()
    install_root = Path(tempfile.mkdtemp(prefix="dwv-install-")).resolve()
    src_dir = Path(tempfile.mkdtemp(prefix="dwv-src-")).resolve()
    out_dir = Path(tempfile.mkdtemp(prefix="dwv-out-")).resolve()

    try:
        # ---- 1. write the source tree ------------------------------
        section("1. Build the bundle source tree")
        (src_dir / "swarm.toml").write_text(_SWARM_TOML)
        (src_dir / "manifest.toml").write_text(_MANIFEST_TOML)
        (src_dir / "permissions.toml").write_text(_PERMISSIONS_TOML)
        (src_dir / "README.md").write_text("# topology-demo\n")
        ok(f"wrote 4 files under {src_dir.name}/")

        # ---- 2. pack ----------------------------------------------
        section("2. Pack into a .duhwave archive")
        bundle_path = out_dir / f"topology-demo{BUNDLE_EXT}"
        pack_bundle(src_dir, bundle_path)
        ok(f"bundle: {bundle_path.name}  ({bundle_path.stat().st_size:,} bytes)")

        # ---- 3. install -------------------------------------------
        section("3. Install into a tempdir-rooted waves/")
        installer = BundleInstaller(root=install_root, confirm=lambda _diff: True)
        # Unsigned bundle — no public key — trust_level should be "unsigned".
        result = installer.install(bundle_path, public_key_path=None)
        ok(
            f"installed: {result.name} {result.version}  "
            f"trust={result.trust_level}  signed={result.signed}"
        )
        if not Path(result.path).is_dir():
            fail(f"install path missing: {result.path}")
            return 1
        listed = installer.list_installed()
        ok(f"BundleInstaller.list_installed() = {[r.name for r in listed]}")

        # ---- 4. spawn the daemon ----------------------------------
        section("4. Spawn the daemon as a background subprocess")
        handle = _spawn_daemon(waves_root)
        try:
            _wait_for_socket(waves_root, timeout=5.0, proc=handle.proc)
            ok(f"daemon bound socket: {host_socket_path(waves_root).name}")
            ok(f"daemon PID: {host_pid_path(waves_root).read_text().strip()}")
            if not is_daemon_running(waves_root):
                fail("is_daemon_running returned False unexpectedly")
                return 1

            # ---- 5. RPC ping -----------------------------------
            section("5. RPC round-trip (ping)")
            pong = rpc_call(waves_root, {"op": "ping"})
            ok(f"ping → {pong}")
            if not pong.get("pong"):
                fail("did not get pong")
                return 1

            ls = rpc_call(waves_root, {"op": "ls_tasks"})
            ok(f"ls_tasks → {ls}")

            # ---- 6. send a manual trigger ----------------------
            section("6. Send a manual trigger via ManualSeam")
            triggers_path = waves_root / "triggers.jsonl"
            log = TriggerLog(triggers_path)
            seam = ManualSeam(log, host_dir=waves_root / "manual")
            await seam.start()
            try:
                step("connect to manual.sock and send one JSON line")
                sk = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sk.settimeout(2.0)
                try:
                    sk.connect(str(seam.socket_path))
                    sk.sendall(
                        json.dumps(
                            {
                                "source": "demo:fire",
                                "payload": {"manual": True, "n": 1},
                            }
                        ).encode("utf-8")
                        + b"\n"
                    )
                finally:
                    sk.close()
                # Yield so the seam handler reads + appends.
                for _ in range(50):
                    await asyncio.sleep(0.02)
                    if triggers_path.exists() and triggers_path.read_text().strip():
                        break
                ok("trigger written")
            finally:
                await seam.stop()

            # ---- 7. verify replay ------------------------------
            section("7. Verify the trigger via TriggerLog.replay")
            replayed = TriggerLog(triggers_path).replay()
            ok(f"replay returned {len(replayed)} trigger(s)")
            for trig in replayed:
                print(
                    f"    {trig.kind.value}  source={trig.source}  "
                    f"payload={trig.payload}"
                )
            if len(replayed) != 1:
                fail(f"expected 1 trigger, got {len(replayed)}")
                return 1
            if replayed[0].source != "demo:fire":
                fail(f"unexpected source: {replayed[0].source}")
                return 1

        finally:
            # ---- 8. stop the daemon ---------------------------
            section("8. SIGTERM the daemon")
            rc = handle.stop(timeout=5.0)
            if rc != 0:
                fail(f"daemon exited with rc={rc}")
                return 1
            ok("daemon exited cleanly")
            if host_pid_path(waves_root).exists():
                fail("pidfile not removed")
                return 1
            if host_socket_path(waves_root).exists():
                fail("socket not removed")
                return 1
            ok("socket + pidfile cleaned up")
            try:
                rpc_call(waves_root, {"op": "ping"}, timeout=0.3)
                fail("RPC succeeded after shutdown — daemon still alive?")
                return 1
            except HostRPCError:
                ok("RPC after shutdown raises HostRPCError as expected")

        # ---- 9. uninstall ------------------------------------
        section("9. Uninstall the bundle")
        removed = installer.uninstall("topology-demo")
        if not removed:
            fail("uninstall returned False")
            return 1
        if installer.list_installed():
            fail("bundle still listed after uninstall")
            return 1
        ok("bundle uninstalled cleanly")

        print()
        print("topology demo OK")
        return 0
    finally:
        # Clean up every tempdir we minted.
        for d in (waves_root, install_root, src_dir, out_dir):
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
