"""Subcommand handlers for ``duh wave``.

Each ``cmd_<name>`` function takes the argparse Namespace and returns
an exit code. Handlers split into three groups:

- **On-disk state only** (no daemon required): ``ls``, ``install``,
  ``uninstall``. These call into :mod:`duh.duhwave.bundle` directly.

- **Daemon-required**: ``start`` (spawns the daemon), ``stop`` (signals
  it), ``inspect``/``pause``/``resume``/``logs``/``web`` (RPC over the
  host socket). The host RPC client lives in :mod:`duh.duhwave.cli.rpc`.

- **Daemon entry**: the ``start`` handler optionally re-execs itself in
  ``--foreground`` mode by importing :mod:`duh.duhwave.cli.daemon`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from duh.duhwave.bundle import (
    BundleInstaller,
    BundleSignatureError,
)
from duh.duhwave.cli import rpc


# ---- on-disk handlers ------------------------------------------------


def cmd_ls(args: argparse.Namespace) -> int:
    waves_root: Path = args.waves_root
    installer = BundleInstaller(root=waves_root)
    installed = installer.list_installed()
    daemon_running = rpc.is_daemon_running(waves_root)
    running_tasks: list[dict[str, object]] = []
    if daemon_running:
        try:
            running_tasks = rpc.call(waves_root, {"op": "ls_tasks"}).get("tasks", [])
        except rpc.HostRPCError:
            running_tasks = []
    if args.json:
        out = {
            "daemon_running": daemon_running,
            "installed": [
                {"name": r.name, "version": r.version, "trust_level": r.trust_level}
                for r in installed
            ],
            "tasks": running_tasks,
        }
        print(json.dumps(out, indent=2, sort_keys=True))
        return 0
    print(f"daemon: {'running' if daemon_running else 'stopped'}")
    if installed:
        print()
        print("installed swarms:")
        for r in installed:
            print(f"  {r.name:24} {r.version:10} trust={r.trust_level}")
    else:
        print("no swarms installed")
    if daemon_running and running_tasks:
        print()
        print("running tasks:")
        for t in running_tasks:
            print(f"  {t.get('task_id','?'):28} {t.get('status','?'):10} {t.get('prompt','')[:40]}")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    bundle_path: Path = args.path
    if not bundle_path.exists():
        print(f"error: bundle not found: {bundle_path}", file=sys.stderr)
        return 2
    installer = BundleInstaller(root=args.waves_root)
    try:
        result = installer.install(
            bundle_path,
            public_key_path=args.public_key,
            force=args.force,
        )
    except BundleSignatureError as e:
        print(f"signature check failed: {e}", file=sys.stderr)
        return 3
    except ValueError as e:
        print(f"install failed: {e}", file=sys.stderr)
        return 4
    print(f"installed: {result.name} {result.version}  trust={result.trust_level}")
    if result.permissions_changed:
        print("note: permissions changed vs. prior install; review before starting.")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    installer = BundleInstaller(root=args.waves_root)
    if not installer.uninstall(args.name):
        print(f"not installed: {args.name}", file=sys.stderr)
        return 1
    print(f"uninstalled: {args.name}")
    return 0


# ---- daemon-control handlers ----------------------------------------


def cmd_start(args: argparse.Namespace) -> int:
    if rpc.is_daemon_running(args.waves_root):
        print("daemon already running")
        return 0
    if args.foreground:
        from duh.duhwave.cli import daemon
        return daemon.run_foreground(args.waves_root, swarm_name=args.name)
    # Background: spawn a detached process running this same module
    # in --foreground mode. Stdout/stderr → host log.
    import subprocess
    log_path = args.waves_root / "host.log"
    log = log_path.open("ab", buffering=0)
    cmd = [sys.executable, "-m", "duh.duhwave.cli.daemon", str(args.waves_root)]
    if args.name:
        cmd.append(args.name)
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    # Don't wait — it's daemonised. Persist the PID.
    pid_path = args.waves_root / "host.pid"
    pid_path.write_text(str(proc.pid))
    print(f"started duhwave host (pid {proc.pid}); logs: {log_path}")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    pid_path = args.waves_root / "host.pid"
    if not pid_path.exists():
        print("daemon not running")
        return 0
    try:
        pid = int(pid_path.read_text().strip())
    except ValueError:
        print(f"corrupt host.pid; removing", file=sys.stderr)
        pid_path.unlink(missing_ok=True)
        return 1
    import os
    import signal
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        print(f"process {pid} already gone")
        pid_path.unlink(missing_ok=True)
        return 0
    pid_path.unlink(missing_ok=True)
    print(f"sent SIGTERM to pid {pid}")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    return _rpc_print(args.waves_root, {"op": "inspect", "swarm_id": args.swarm_id})


def cmd_pause(args: argparse.Namespace) -> int:
    return _rpc_print(args.waves_root, {"op": "pause", "swarm_id": args.swarm_id})


def cmd_resume(args: argparse.Namespace) -> int:
    return _rpc_print(args.waves_root, {"op": "resume", "swarm_id": args.swarm_id})


def cmd_logs(args: argparse.Namespace) -> int:
    """Print or tail a swarm's event log.

    Without ``--follow`` this is a single RPC round trip: the daemon
    returns a snapshot dict and we pretty-print it as JSON, matching
    every other ``duh wave`` control-plane command.

    With ``--follow`` we switch to the streaming wire shape: each log
    line lands in real time and is printed verbatim, plus periodic
    heartbeat markers so a totally quiet log still shows the
    connection is healthy. Ctrl-C disconnects cleanly.
    """
    if not args.follow:
        return _rpc_print(
            args.waves_root,
            {
                "op": "logs",
                "swarm_id": args.swarm_id,
                "follow": False,
                "lines": args.lines,
            },
        )

    if not rpc.is_daemon_running(args.waves_root):
        print(
            "daemon not running; start it first with `duh wave start`",
            file=sys.stderr,
        )
        return 1

    def _print_frame(frame: dict[str, object]) -> None:
        # Stream items are either log lines or heartbeats. Log lines
        # print verbatim so this command behaves like ``tail -f``;
        # heartbeats print to stderr so they don't pollute log
        # capture but still confirm liveness.
        if "line" in frame:
            print(frame["line"], flush=True)
        elif "heartbeat" in frame:
            print(f"# heartbeat {frame['heartbeat']}", file=sys.stderr, flush=True)

    payload = {
        "op": "logs",
        "swarm_id": args.swarm_id,
        "follow": True,
        "lines": args.lines,
    }
    try:
        rpc.stream_call(args.waves_root, payload, _print_frame)
    except KeyboardInterrupt:
        # Clean disconnect — the rpc helper closes the socket on its
        # way out of the finally block.
        return 0
    except rpc.HostRPCError as e:
        print(f"rpc error: {e}", file=sys.stderr)
        return 2
    return 0


def cmd_web(args: argparse.Namespace) -> int:
    return _rpc_print(args.waves_root, {"op": "web", "port": args.port})


# ---- helpers ---------------------------------------------------------


def _rpc_print(waves_root: Path, payload: dict[str, object]) -> int:
    if not rpc.is_daemon_running(waves_root):
        print("daemon not running; start it first with `duh wave start`", file=sys.stderr)
        return 1
    try:
        resp = rpc.call(waves_root, payload)
    except rpc.HostRPCError as e:
        print(f"rpc error: {e}", file=sys.stderr)
        return 2
    if "error" in resp:
        print(f"error: {resp['error']}", file=sys.stderr)
        return 3
    print(json.dumps(resp, indent=2, sort_keys=True))
    return 0
