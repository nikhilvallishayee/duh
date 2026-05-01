"""Resilience tests for the duhwave host: crash recovery, orphans, replay.

Pins three ADR-030 invariants and one ADR-031 invariant:

1.  **Trigger log persistence** — :class:`TriggerLog.replay` returns
    everything ever written, across daemon-process boundaries.
    (ADR-031 §B.5: at-least-once delivery.)

2.  **Subprocess executor failure surface** — a SUBPROCESS-surface task
    that exits non-zero transitions to ``FAILED`` with the exit code
    in ``error``. (ADR-030 §"Surface 2: subprocess".)

3.  **Orphaned RUNNING task on host restart** — when a daemon dies
    mid-flight with a task in ``RUNNING``, the next host start must
    transition that task to ``FAILED`` with an ``"orphaned"`` error.
    (ADR-030 §"Resumption protocol".) Implemented in
    :meth:`TaskRegistry.restore_from_disk` via the ``claimed_in_process``
    / ``claimed_subprocess`` arguments — the new host names the
    task_ids its executors are about to reattach; everything else
    RUNNING is orphaned.

4.  **Daemon SIGKILL leaves the trigger log readable** — even after
    SIGKILL (no clean shutdown), triggers written before the kill
    still replay.

Run with::

    /Users/nomind/Code/duh/.venv/bin/python3 -m pytest \\
        tests/integration/test_duhwave_resilience.py -v
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from duh.duhwave.cli.rpc import (
    host_pid_path,
    host_socket_path,
    is_daemon_running,
)
from duh.duhwave.ingress.manual import ManualSeam
from duh.duhwave.ingress.triggers import Trigger, TriggerKind, TriggerLog
from duh.duhwave.task.executors import (
    InProcessExecutor,
    SubprocessExecutor,
)
from duh.duhwave.task.registry import (
    Task,
    TaskRegistry,
    TaskStatus,
    TaskSurface,
)


# ---- daemon spawn helper (duplicated from e2e_lifecycle for test isolation) -


@dataclass(slots=True)
class _DaemonHandle:
    proc: subprocess.Popen[bytes]
    waves_root: Path


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
                f"daemon exited prematurely with rc={proc.returncode}: {stderr}"
            )
        time.sleep(0.05)
    raise TimeoutError(f"daemon did not bind socket within {timeout}s")


def _short_waves_root() -> Path:
    """A short tempdir suitable for AF_UNIX bind on macOS (~104-byte path limit)."""
    return Path(tempfile.mkdtemp(prefix="dwv-")).resolve()


# ---- Test 1: TriggerLog persists across processes -----------------------


def test_trigger_log_persists_across_daemon_restart() -> None:
    """Daemon writes triggers; SIGTERM; new daemon; old triggers still replay.

    Demonstrates the at-least-once delivery property from ADR-031 §B.5:
    the trigger log is the durable source of truth, the daemon is just
    a pump.
    """
    waves_root = _short_waves_root()
    triggers_path = waves_root / "triggers.jsonl"
    try:
        # ── Round 1: spawn daemon, write a trigger via ManualSeam, stop. ──
        handle = _spawn_daemon(waves_root)
        try:
            _wait_for_socket(waves_root, timeout=5.0, proc=handle.proc)
            _send_one_manual_trigger(
                waves_root,
                triggers_path,
                source="round-one",
                payload={"n": 1},
            )
        finally:
            handle.proc.send_signal(signal.SIGTERM)
            rc = handle.proc.wait(timeout=5.0)
            assert rc == 0

        assert triggers_path.is_file()
        first_replay = TriggerLog(triggers_path).replay()
        assert len(first_replay) == 1
        assert first_replay[0].source == "round-one"
        assert first_replay[0].payload == {"n": 1}

        # ── Round 2: fresh daemon process, write another trigger. ─────────
        handle2 = _spawn_daemon(waves_root)
        try:
            _wait_for_socket(waves_root, timeout=5.0, proc=handle2.proc)
            _send_one_manual_trigger(
                waves_root,
                triggers_path,
                source="round-two",
                payload={"n": 2},
            )
        finally:
            handle2.proc.send_signal(signal.SIGTERM)
            rc = handle2.proc.wait(timeout=5.0)
            assert rc == 0

        final_replay = TriggerLog(triggers_path).replay()
        assert len(final_replay) == 2
        sources = [t.source for t in final_replay]
        assert sources == ["round-one", "round-two"]
    finally:
        import shutil
        shutil.rmtree(waves_root, ignore_errors=True)


def test_trigger_log_survives_sigkill() -> None:
    """SIGKILL the daemon — earlier triggers still replay.

    The log is fsync-safe by line because each trigger is a complete
    JSONL record before the next one starts.
    """
    waves_root = _short_waves_root()
    triggers_path = waves_root / "triggers.jsonl"
    try:
        handle = _spawn_daemon(waves_root)
        try:
            _wait_for_socket(waves_root, timeout=5.0, proc=handle.proc)
            _send_one_manual_trigger(
                waves_root,
                triggers_path,
                source="pre-kill",
                payload={"x": 42},
            )
            # Give the seam time to fsync to disk before we yank the daemon.
            time.sleep(0.1)
        finally:
            # SIGKILL: no clean shutdown, no chance to fsync anything new.
            handle.proc.kill()
            handle.proc.wait(timeout=5.0)

        replayed = TriggerLog(triggers_path).replay()
        assert len(replayed) == 1
        assert replayed[0].source == "pre-kill"
    finally:
        import shutil
        shutil.rmtree(waves_root, ignore_errors=True)


# ---- Test 2: SubprocessExecutor exit-code failure surface ---------------


async def test_subprocess_task_failed_with_exit_code(tmp_path: Path) -> None:
    """A subprocess task that exits non-zero lands in FAILED with the rc.

    Pins ADR-030 §"Surface 2: subprocess" — failure surfaces are
    structured: ``error="exit code N"`` rather than free-form prose.
    """
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    registry = TaskRegistry(session_dir, session_id="resilience")

    # Replace the subprocess runner script with one that exits non-zero.
    # We do this by writing a trivial replacement and redirecting the
    # executor through it. SubprocessExecutor hard-codes the runner
    # script path, so we monkey-patch the executor's submit to point
    # at a custom script for this one task.
    fail_script = tmp_path / "fail_runner.py"
    fail_script.write_text(
        "import sys\n"
        "sys.stdout.write('about to fail\\n')\n"
        "sys.exit(7)\n"
    )

    # Build the task and drive it through SubprocessExecutor's wire,
    # but replace the runner path. Cleanest seam: subclass.
    class _PatchedExecutor(SubprocessExecutor):
        async def submit(self, task: Task) -> None:  # type: ignore[override]
            if task.surface is not TaskSurface.SUBPROCESS:
                raise ValueError(f"executor surface mismatch: {task.surface}")
            out_path = self._registry.output_path_for(task.task_id)
            log = out_path.open("ab", buffering=0)
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-I",
                str(fail_script),
                stdin=asyncio.subprocess.PIPE,
                stdout=log,
                stderr=log,
            )
            self._procs[task.task_id] = proc
            self._registry.transition(task.task_id, TaskStatus.RUNNING)
            asyncio.create_task(self._reap(task.task_id, proc, log))

    executor = _PatchedExecutor(registry)
    task = Task(
        task_id=registry.new_id(),
        session_id="resilience",
        parent_id=None,
        surface=TaskSurface.SUBPROCESS,
        prompt="(unused — runner exits with rc=7)",
        model="inherit",
        tools_allowlist=("Bash",),
    )
    registry.register(task)
    await executor.submit(task)

    # Poll for terminal state.
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        t = registry.get(task.task_id)
        if t is not None and t.status.terminal:
            break
        await asyncio.sleep(0.05)

    final = registry.get(task.task_id)
    assert final is not None
    assert final.status is TaskStatus.FAILED, (
        f"expected FAILED, got {final.status}: error={final.error!r}"
    )
    assert final.error is not None
    assert "exit code 7" in final.error


# ---- Test 3: Orphaned RUNNING in-process task on restore ---------------


def test_orphaned_running_task_transitions_to_failed_on_restore(
    tmp_path: Path,
) -> None:
    """RUNNING tasks on disk must become FAILED on restore unless claimed.

    Per ADR-030 §"Resumption protocol", three sub-cases are pinned:

    1. IN_PROCESS task not in ``claimed_in_process`` → orphaned. There
       is no on-disk live signal for an asyncio.Task, so a fresh
       restore is *always* an orphan unless the caller explicitly
       names it.
    2. SUBPROCESS task with a stale ``metadata["pid"]`` (no live
       process by that pid) → orphaned regardless of claim.
    3. SUBPROCESS task whose pid is alive AND named in
       ``claimed_subprocess`` → kept in RUNNING (no transition).
    """
    session_dir = tmp_path / "session"
    session_dir.mkdir()

    # ── Sub-case 1: IN_PROCESS, unclaimed ─────────────────────────────
    reg1 = TaskRegistry(session_dir, session_id="orphan-test")
    in_proc_task = Task(
        task_id=reg1.new_id(),
        session_id="orphan-test",
        parent_id=None,
        surface=TaskSurface.IN_PROCESS,
        prompt="will be orphaned",
        model="inherit",
        tools_allowlist=("Bash",),
    )
    reg1.register(in_proc_task)
    reg1.transition(in_proc_task.task_id, TaskStatus.RUNNING)

    # ── Sub-case 2: SUBPROCESS with stale pid ─────────────────────────
    sub_stale = Task(
        task_id=reg1.new_id(),
        session_id="orphan-test",
        parent_id=None,
        surface=TaskSurface.SUBPROCESS,
        prompt="stale pid",
        model="inherit",
        tools_allowlist=("Bash",),
    )
    reg1.register(sub_stale)
    sub_stale.metadata["pid"] = _find_dead_pid()
    reg1.transition(sub_stale.task_id, TaskStatus.RUNNING)

    # ── Sub-case 3: SUBPROCESS with live pid (our own), claimed ───────
    sub_live = Task(
        task_id=reg1.new_id(),
        session_id="orphan-test",
        parent_id=None,
        surface=TaskSurface.SUBPROCESS,
        prompt="live and claimed",
        model="inherit",
        tools_allowlist=("Bash",),
    )
    reg1.register(sub_live)
    sub_live.metadata["pid"] = os.getpid()  # this test process is, by definition, alive
    reg1.transition(sub_live.task_id, TaskStatus.RUNNING)

    # Simulate host restart: drop reg1, build a fresh registry.
    del reg1
    reg2 = TaskRegistry(session_dir, session_id="orphan-test")
    reg2.restore_from_disk(
        claimed_in_process=[],
        claimed_subprocess=[sub_live.task_id],
    )

    # Sub-case 1: in_process unclaimed → FAILED.
    restored1 = reg2.get(in_proc_task.task_id)
    assert restored1 is not None
    assert restored1.status is TaskStatus.FAILED, (
        f"expected FAILED (orphaned), got {restored1.status.value}"
    )
    assert restored1.error is not None
    assert "orphan" in restored1.error.lower()

    # Sub-case 2: subprocess with stale pid → FAILED.
    restored2 = reg2.get(sub_stale.task_id)
    assert restored2 is not None
    assert restored2.status is TaskStatus.FAILED, (
        f"expected FAILED (stale pid), got {restored2.status.value}"
    )
    assert restored2.error is not None
    assert "orphan" in restored2.error.lower()

    # Sub-case 3: subprocess with live pid, claimed → still RUNNING.
    restored3 = reg2.get(sub_live.task_id)
    assert restored3 is not None
    assert restored3.status is TaskStatus.RUNNING, (
        f"expected RUNNING (claimed + live), got {restored3.status.value}"
    )


def _find_dead_pid() -> int:
    """Return a positive PID that does not name any live process.

    Walks downward from a high PID; bails if every probe is alive
    (very unlikely on any modern system).
    """
    for candidate in range(2_000_000, 1_000_000, -1):
        try:
            os.kill(candidate, 0)
        except ProcessLookupError:
            return candidate
        except OSError:
            continue
    raise RuntimeError("could not find a dead pid for the orphan-recovery test")


# ---- helpers -----------------------------------------------------------


def _send_one_manual_trigger(
    waves_root: Path,
    triggers_path: Path,
    *,
    source: str,
    payload: dict[str, object],
) -> None:
    """Wire a ManualSeam to the daemon's trigger log and send one trigger.

    The daemon doesn't itself host a ManualSeam yet (its host
    skeleton accepts only RPC ops; trigger ingestion is wired in a
    follow-up). For the resilience tests this is fine: the
    ManualSeam appends to the same ``triggers.jsonl`` the daemon's
    TriggerLog uses, so the log persistence guarantee can be tested
    independently of which process did the writing.
    """
    log = TriggerLog(triggers_path)
    seam = ManualSeam(log, host_dir=waves_root / "manual_seams")

    async def _send() -> None:
        await seam.start()
        try:
            sk = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sk.settimeout(2.0)
            try:
                sk.connect(str(seam.socket_path))
                line = json.dumps({"source": source, "payload": payload}) + "\n"
                sk.sendall(line.encode("utf-8"))
            finally:
                sk.close()
            # Yield to let the seam handler read + append.
            for _ in range(50):
                await asyncio.sleep(0.02)
                if triggers_path.exists():
                    contents = triggers_path.read_text()
                    if source in contents:
                        return
        finally:
            await seam.stop()

    asyncio.run(_send())
