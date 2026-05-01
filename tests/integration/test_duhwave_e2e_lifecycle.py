"""End-to-end lifecycle integration test for the duhwave host stack.

Walks the full path from a freshly-generated keypair through bundle
pack/sign, install verification, daemon boot in a subprocess, RPC
round-trips over the host socket, manual-seam trigger ingestion,
clean SIGTERM shutdown, and finally bundle uninstall.

The daemon is started as a child process rooted under ``tmp_path``,
so this test never touches the user's actual ``~/.duh/`` directory.
A ``ManualSeam`` is attached to the same ``triggers.jsonl`` file the
daemon's ``TriggerLog`` writes to, demonstrating the at-least-once
trigger persistence guarantee from ADR-031 §B.5.

Sequenced as one test driven by a session-scoped state fixture so
each step's invariants can be asserted in isolation while sharing
setup. Run with::

    /Users/nomind/Code/duh/.venv/bin/python3 -m pytest \\
        tests/integration/test_duhwave_e2e_lifecycle.py -v
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
from typing import Iterator

import pytest

from duh.duhwave.bundle import (
    BUNDLE_EXT,
    BundleInstaller,
    pack_bundle,
    sign_bundle,
)
from duh.duhwave.bundle.signing import generate_keypair
from duh.duhwave.cli.rpc import (
    HostRPCError,
    call as rpc_call,
    host_pid_path,
    host_socket_path,
    is_daemon_running,
)
from duh.duhwave.ingress.manual import ManualSeam
from duh.duhwave.ingress.triggers import TriggerLog


# ---- bundle fixture sources ---------------------------------------------

_SWARM_TOML = """\
[swarm]
name = "e2e-lifecycle"
version = "0.1.0"
description = "duhwave e2e lifecycle test fixture"
format_version = 1

[[agents]]
id = "researcher"
role = "researcher"
model = "anthropic/claude-haiku-4-5"
tools = ["bash", "search"]

[budget]
max_tokens_per_hour = 100000
max_usd_per_day = 1.0
max_concurrent_tasks = 1
"""

_MANIFEST_TOML = """\
[bundle]
name = "e2e-lifecycle"
version = "0.1.0"
description = "duhwave e2e lifecycle test fixture"
author = "tests <tests@example.com>"
format_version = 1
created_at = 1700000000.0

[signing]
signed = false
"""

_PERMISSIONS_TOML = """\
[filesystem]
read = ["~/repos/*"]
write = ["./tmp/*"]

[network]
allow = ["api.example.com"]

[tools]
require = ["bash", "search"]
"""


def _write_bundle_source(spec_dir: Path) -> None:
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "swarm.toml").write_text(_SWARM_TOML)
    (spec_dir / "manifest.toml").write_text(_MANIFEST_TOML)
    (spec_dir / "permissions.toml").write_text(_PERMISSIONS_TOML)
    (spec_dir / "README.md").write_text("# e2e-lifecycle\n")


# ---- daemon helpers -----------------------------------------------------


@dataclass(slots=True)
class _DaemonHandle:
    """Tracks an in-process spawned daemon child."""

    proc: subprocess.Popen[bytes]
    waves_root: Path

    def stop_sigterm(self, *, timeout: float = 5.0) -> int:
        """Send SIGTERM, wait for clean exit; return rc."""
        if self.proc.poll() is None:
            self.proc.send_signal(signal.SIGTERM)
            try:
                return self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                return self.proc.wait(timeout=timeout)
        return self.proc.returncode or 0


def _wait_for_socket(
    waves_root: Path,
    *,
    timeout: float = 5.0,
    proc: subprocess.Popen[bytes] | None = None,
) -> None:
    """Poll until the daemon's UNIX socket is bound and PID is recorded."""
    sock = host_socket_path(waves_root)
    pid = host_pid_path(waves_root)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if sock.exists() and pid.exists():
            return
        # Surface a daemon early-exit explicitly rather than ticking
        # the polling loop until timeout.
        if proc is not None and proc.poll() is not None:
            stdout = proc.stdout.read().decode("utf-8", errors="replace") if proc.stdout else ""
            stderr = proc.stderr.read().decode("utf-8", errors="replace") if proc.stderr else ""
            raise RuntimeError(
                f"daemon exited prematurely with rc={proc.returncode}\n"
                f"stdout: {stdout}\nstderr: {stderr}"
            )
        time.sleep(0.05)
    raise TimeoutError(
        f"daemon did not bind socket within {timeout}s "
        f"(sock={sock.exists()} pid={pid.exists()})"
    )


def _spawn_daemon(waves_root: Path) -> _DaemonHandle:
    """Spawn ``python -m duh.duhwave.cli.daemon <waves_root>``."""
    repo_root = Path(__file__).resolve().parents[2]
    env = os.environ.copy()
    # Ensure the child sees the repo on sys.path even if the venv's
    # site-packages doesn't have duh installed editable.
    env["PYTHONPATH"] = str(repo_root) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.Popen(
        [sys.executable, "-m", "duh.duhwave.cli.daemon", str(waves_root)],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    return _DaemonHandle(proc=proc, waves_root=waves_root)


# ---- shared-state fixture ----------------------------------------------


@dataclass(slots=True)
class _LifecycleState:
    waves_root: Path
    install_root: Path
    bundle_path: Path
    sig_path: Path
    pub_key: Path
    priv_key: Path
    installer: BundleInstaller


@pytest.fixture(scope="function")
def lifecycle_state(tmp_path: Path) -> Iterator[_LifecycleState]:
    """Build a packed + signed bundle and an installer rooted in tmp_path.

    UNIX-domain sockets on macOS have a ~104-byte path limit; pytest's
    ``tmp_path`` lives under ``/private/var/folders/...`` which can
    blow that limit. The ``waves_root`` (which holds the daemon's
    socket) lives under a shorter ``tempfile.mkdtemp`` so the daemon
    can always bind. Other dirs stay under ``tmp_path`` for tidy
    pytest reporting.
    """
    spec_dir = tmp_path / "spec"
    keys_dir = tmp_path / "keys"
    out_dir = tmp_path / "out"
    install_root = tmp_path / "install_root"
    # Short path for the daemon socket — critical on macOS.
    waves_root = Path(tempfile.mkdtemp(prefix="dwv-")).resolve()

    keys_dir.mkdir()
    out_dir.mkdir()

    _write_bundle_source(spec_dir)

    priv, pub = generate_keypair(keys_dir / "lifecycle")
    bundle_path = out_dir / f"e2e-lifecycle{BUNDLE_EXT}"
    pack_bundle(spec_dir, bundle_path)
    sig_path = sign_bundle(bundle_path, priv)

    installer = BundleInstaller(root=install_root, confirm=lambda _diff: True)

    try:
        yield _LifecycleState(
            waves_root=waves_root,
            install_root=install_root,
            bundle_path=bundle_path,
            sig_path=sig_path,
            pub_key=pub,
            priv_key=priv,
            installer=installer,
        )
    finally:
        shutil.rmtree(waves_root, ignore_errors=True)


# ---- the lifecycle test -------------------------------------------------


def test_full_e2e_lifecycle(lifecycle_state: _LifecycleState) -> None:
    """Drive the full bundle → daemon → trigger → shutdown → uninstall path.

    Invariants checked at each step are kept inline as ``assert`` so a
    failure points at the exact transition that broke.
    """
    s = lifecycle_state

    # Step 1-4 are covered by the fixture (keypair, pack, sign).
    assert s.bundle_path.is_file(), "fixture: bundle missing"
    assert s.sig_path.is_file(), "fixture: signature missing"
    assert s.sig_path.stat().st_size == 64, "ed25519 sig must be 64 bytes"

    # Step 5: install with the signing pubkey → trust_level=trusted.
    result = s.installer.install(s.bundle_path, public_key_path=s.pub_key)
    assert result.name == "e2e-lifecycle"
    assert result.version == "0.1.0"
    assert result.trust_level == "trusted", (
        f"expected trusted, got {result.trust_level}"
    )
    assert result.signed is True
    assert Path(result.path).is_dir()
    assert (Path(result.path) / "swarm.toml").is_file()
    assert (s.install_root / "index.json").is_file()

    # Step 6: spawn the daemon as a subprocess.
    handle = _spawn_daemon(s.waves_root)
    try:
        # Step 7: wait for the socket to bind; verify is_daemon_running.
        _wait_for_socket(s.waves_root, timeout=5.0, proc=handle.proc)
        assert is_daemon_running(s.waves_root), (
            "is_daemon_running returned False after socket bound"
        )
        sock = host_socket_path(s.waves_root)
        assert sock.exists()
        # Owner-only perms on the socket (manual.sock convention).
        sock_mode = sock.stat().st_mode & 0o777
        assert sock_mode == 0o600, f"unexpected socket mode: {oct(sock_mode)}"

        # Step 8: RPC ping → pong; ls_tasks → empty list.
        pong = rpc_call(s.waves_root, {"op": "ping"})
        assert pong.get("ok") is True
        assert pong.get("pong") is True
        ls = rpc_call(s.waves_root, {"op": "ls_tasks"})
        assert ls.get("ok") is True
        assert ls.get("tasks") == [], f"expected empty task list, got {ls}"
        # Unknown op should be rejected gracefully (still alive afterward).
        bad = rpc_call(s.waves_root, {"op": "no-such-op"})
        assert "error" in bad

        # Step 9: send a manual trigger via a ManualSeam wired to the
        # same triggers.jsonl the daemon's TriggerLog writes to.
        triggers_path = s.waves_root / "triggers.jsonl"
        log = TriggerLog(triggers_path)
        seam = ManualSeam(log, host_dir=s.waves_root)

        async def _send_one() -> None:
            await seam.start()
            try:
                seam_path = seam.socket_path
                assert seam_path.exists()
                # Connect synchronously, send one JSON line, close.
                sk = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                sk.settimeout(2.0)
                try:
                    sk.connect(str(seam_path))
                    sk.sendall(
                        b'{"source":"test:lifecycle","payload":{"hello":"world"}}\n'
                    )
                finally:
                    sk.close()
                # Give the seam a moment to read + append. The seam
                # processes inside its asyncio task; yielding lets it
                # run to completion before we tear down.
                for _ in range(50):
                    await asyncio.sleep(0.02)
                    if triggers_path.exists() and triggers_path.read_text().strip():
                        break
            finally:
                await seam.stop()

        asyncio.run(_send_one())

        # Step 10: verify the trigger landed in triggers.jsonl.
        assert triggers_path.is_file(), "triggers.jsonl was not created"
        replayed = TriggerLog(triggers_path).replay()
        assert len(replayed) == 1, f"expected 1 trigger, got {len(replayed)}"
        t = replayed[0]
        assert t.kind.value == "manual"
        assert t.source == "test:lifecycle"
        assert t.payload == {"hello": "world"}

    finally:
        # Step 11: SIGTERM the daemon; verify clean shutdown.
        rc = handle.stop_sigterm(timeout=5.0)
        assert rc == 0, f"daemon exited with rc={rc}"
        # Pidfile + socket must both be cleaned up.
        assert not host_pid_path(s.waves_root).exists(), "pidfile not removed"
        assert not host_socket_path(s.waves_root).exists(), "socket not removed"
        assert not is_daemon_running(s.waves_root)
        # Subsequent RPC should fail (daemon is gone).
        with pytest.raises(HostRPCError):
            rpc_call(s.waves_root, {"op": "ping"}, timeout=0.5)

    # Step 12: bundle still listed (uninstall is separate).
    listed = s.installer.list_installed()
    assert len(listed) == 1
    assert listed[0].name == "e2e-lifecycle"

    # Step 13: uninstall → bundle gone.
    removed = s.installer.uninstall("e2e-lifecycle")
    assert removed is True
    assert s.installer.list_installed() == []
    assert not (s.install_root / "e2e-lifecycle").exists()


def test_unsigned_bundle_downgrades_trust(lifecycle_state: _LifecycleState) -> None:
    """An unsigned install (no .sig present) lands as trust_level=unsigned.

    Complements the lifecycle test by exercising the alternate trust
    path through the same installer surface.
    """
    s = lifecycle_state
    # Remove the sig that the fixture produced; install without pubkey.
    s.sig_path.unlink()

    # The installer downgrades unsigned bundles to "ask every time".
    result = s.installer.install(s.bundle_path, public_key_path=None)
    assert result.trust_level == "unsigned"
    assert result.signed is False


def test_signed_no_pubkey_is_untrusted(tmp_path: Path) -> None:
    """A signed bundle installed without a pubkey is recorded as untrusted.

    Distinct fixture so the trust-level cache from the main lifecycle
    fixture doesn't bleed across.
    """
    spec_dir = tmp_path / "spec"
    keys_dir = tmp_path / "keys"
    out_dir = tmp_path / "out"
    install_root = tmp_path / "install_root"
    keys_dir.mkdir()
    out_dir.mkdir()
    _write_bundle_source(spec_dir)
    priv, _pub = generate_keypair(keys_dir / "k")
    bundle = out_dir / f"e2e-lifecycle{BUNDLE_EXT}"
    pack_bundle(spec_dir, bundle)
    sign_bundle(bundle, priv)

    installer = BundleInstaller(root=install_root, confirm=lambda _diff: True)
    result = installer.install(bundle, public_key_path=None)
    assert result.signed is True
    assert result.trust_level == "untrusted"
