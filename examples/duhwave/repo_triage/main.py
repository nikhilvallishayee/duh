"""repo-triage — runnable end-to-end demo of a duhwave swarm.

This script is the OSS showpiece for duhwave. It wires every primitive
the ADRs (028–032) define into one ~400-line, runnable demonstration:

    1. Build the bundle from this directory.
    2. Install it into a tmp_path-rooted ~/.duh/waves.
    3. Start the host daemon as a background subprocess.
    4. Send a synthetic trigger (via the manual seam) representing
       "new GitHub issue".
    5. Show the matcher routing → "would spawn coordinator with X
       exposed handles".
    6. Walk the Spawn → coordinator-orchestration → result-bind path
       with stub WorkerRunners that return canned strings (the runner
       injection point — no real model calls).
    7. Inspect topology + state via the host RPC.
    8. Stop the daemon, uninstall, print "demo complete".

The architecture is real. The model calls are stubbed. To make it real
replace the `stub_worker_runner` with one that drives `duh.kernel.engine.Engine`.

Run::

    /path/to/duh/.venv/bin/python3 examples/duhwave/repo_triage/main.py
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# duhwave imports — every primitive we'll exercise
# ---------------------------------------------------------------------------

from duh.duhwave.bundle import BundleInstaller, pack_bundle
from duh.duhwave.cli import rpc
from duh.duhwave.coordinator import (
    BUILTIN_ROLES,
    RLMHandleView,
    filter_tools_for_role,
)
from duh.duhwave.coordinator.spawn import Spawn
from duh.duhwave.ingress import (
    ManualSeam,
    SubscriptionMatcher,
    Trigger,
    TriggerKind,
    TriggerLog,
)
from duh.duhwave.rlm.repl import RLMRepl
from duh.duhwave.spec import parse_swarm
from duh.duhwave.task.executors import InProcessExecutor
from duh.duhwave.task.registry import (
    Task,
    TaskRegistry,
    TaskStatus,
    TaskSurface,
)
from duh.kernel.tool import ToolContext


# ---------------------------------------------------------------------------
# Pretty terminal output — no third-party deps, just unicode
# ---------------------------------------------------------------------------

# These constants give the demo readable structure without a TUI lib.
RULE = "─" * 72


def banner(title: str) -> None:
    """One-line section header; flushed so output streams in real time."""
    print()
    print(RULE)
    print(f"  {title}")
    print(RULE)
    sys.stdout.flush()


def step(tag: str, msg: str) -> None:
    """One line of demo progress, padded to a uniform left column."""
    print(f"[{tag:<10}] {msg}")
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Stub worker runner — the runner-injection seam in action
# ---------------------------------------------------------------------------
#
# In a real deployment this would drive duh.kernel.engine.Engine against a
# real model. For the showpiece we return canned strings keyed off the
# worker's role and prompt — deterministic, fast, free.
#
# The signature matches duh.duhwave.coordinator.runner_protocol.WorkerRunner.

CANNED_RESEARCH = """RESEARCH FINDINGS — repo-triage demo

Headline: Issue body references `Tool.run` call sites; 3 candidates
identified across `src/tool/registry.py:88`, `src/tool/dispatch.py:142`,
and `tests/tool/test_registry.py:23`.

Findings:
- src/tool/registry.py:88 — direct call site; uses positional args.
- src/tool/dispatch.py:142 — invokes through `_dispatch_one`; arg order
  is identical, no kwargs in the call.
- tests/tool/test_registry.py:23 — unit test that exercises the
  positional path; would need updating if the signature changes.

Recommendation: Implementer should add a kwarg-only sentinel parameter
to `Tool.run` in `src/tool/base.py:54` and update the three call sites
above. Test suite covers the change via `pytest tests/tool/`.
"""

CANNED_IMPLEMENTATION = """IMPLEMENTED:

Files changed:
- src/tool/base.py:54-57 — added `*, _trace: bool = False` to Tool.run
  signature.
- src/tool/registry.py:88 — pass-through, no change needed (positional
  call still valid).
- src/tool/dispatch.py:142 — pass-through, no change needed.
- tests/tool/test_registry.py:23 — added one regression test for the
  new kwarg path.

Tests:
- pytest tests/tool/  →  17 passed, 0 failed (took 1.2s).

Side effects: none. No new dependencies, no config changes.
"""


async def stub_worker_runner(task: Task, view: RLMHandleView) -> str:
    """Stub WorkerRunner that returns canned text per role.

    The role is recorded in `task.metadata["role"]` (the Spawn tool sets
    it from the parent's child_role()). For the showpiece we route on
    that field plus the prompt's first word; in a real runner the
    routing would be a model call.
    """
    # Demonstrate that the view actually works — we can read the
    # exposed handles before "thinking".
    exposed = view.list_exposed()
    if exposed:
        # Touch the first exposed handle to prove the wiring. We Peek a
        # tiny window — this is what a real worker would do as it begins
        # to plan, except a model would write the Peek call.
        try:
            sample = await view.peek(exposed[0], start=0, end=80)
        except Exception:  # pragma: no cover — defensive only
            sample = "(unreadable)"
    else:
        sample = "(no handles exposed)"

    # Echo what we saw to the task's output log; the registry path is
    # ADR-030's "output as artifact, not as result".
    if task.output_path:
        try:
            with open(task.output_path, "a", encoding="utf-8") as f:
                f.write(f"[stub] role={task.metadata.get('role')} "
                        f"exposed={exposed} sample={sample!r}\n")
        except OSError:
            pass

    # Pick canned content by the prompt's first word — "research" vs
    # "implement". A real runner would call the model; we just route.
    prompt_lead = task.prompt.strip().split(maxsplit=1)[0].lower() if task.prompt else ""
    if prompt_lead.startswith("implement"):
        return CANNED_IMPLEMENTATION
    return CANNED_RESEARCH


# ---------------------------------------------------------------------------
# 1. Build the bundle
# ---------------------------------------------------------------------------

SPEC_DIR = Path(__file__).parent.resolve()


def build_bundle(out_dir: Path) -> Path:
    """Pack ``SPEC_DIR`` into a deterministic ``.duhwave`` archive."""
    out = out_dir / "repo-triage-0.1.0.duhwave"
    pack_bundle(SPEC_DIR, out)
    step("build", f"packed {SPEC_DIR.name}/ → {out.name} ({out.stat().st_size} bytes)")
    return out


# ---------------------------------------------------------------------------
# 2. Install into a sandbox waves root
# ---------------------------------------------------------------------------


def install_bundle(bundle_path: Path, waves_root: Path) -> None:
    """Install via :class:`BundleInstaller` — same path the CLI uses."""
    installer = BundleInstaller(root=waves_root)
    result = installer.install(bundle_path, force=True)
    step("install", f"{result.name} {result.version}  trust={result.trust_level}")
    step("install", f"installed at {result.path}")
    # The installer keeps an index — show it.
    listed = installer.list_installed()
    step("install", f"index now lists: {[r.name for r in listed]}")


# ---------------------------------------------------------------------------
# 3. Start the host daemon as a background subprocess
# ---------------------------------------------------------------------------


def start_daemon(waves_root: Path) -> tuple[subprocess.Popen, Path]:
    """Spawn the daemon. Returns the Popen and the host log path.

    Mirrors what ``duh wave start`` does: ``python -m
    duh.duhwave.cli.daemon <waves_root>``.
    """
    log_path = waves_root / "host.log"
    log = log_path.open("ab", buffering=0)
    cmd = [
        sys.executable,
        "-m",
        "duh.duhwave.cli.daemon",
        str(waves_root),
        "repo-triage",
    ]
    proc = subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=log,
        start_new_session=True,
    )
    # Wait for the host socket to appear — the daemon writes it during startup.
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if rpc.is_daemon_running(waves_root):
            step("start", f"daemon PID {proc.pid}; logs: {log_path.name}")
            return proc, log_path
        time.sleep(0.05)
    # If we got here the daemon failed to bind. Kill and report.
    proc.terminate()
    raise RuntimeError(
        f"daemon failed to start within 5s; check {log_path}"
    )


def stop_daemon(proc: subprocess.Popen, waves_root: Path) -> int:
    """Send the shutdown RPC, wait, fall back to SIGTERM."""
    try:
        rpc.call(waves_root, {"op": "shutdown"})
    except rpc.HostRPCError:
        pass
    try:
        rc = proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        proc.terminate()
        rc = proc.wait(timeout=2.0)
    step("stop", f"✓ daemon stopped (exit {rc})")
    return rc


# ---------------------------------------------------------------------------
# 4-5. Send a synthetic trigger; show matcher routing
# ---------------------------------------------------------------------------


def show_routing(spec_path: Path) -> None:
    """Build a matcher from the spec; show what each example trigger routes to."""
    spec = parse_swarm(spec_path)
    matcher = SubscriptionMatcher.from_spec(spec)
    step("route", f"matcher built from spec: {len(matcher)} subscription(s)")

    # Synthesise one example of each kind; show the routing result.
    examples = [
        Trigger(
            kind=TriggerKind.WEBHOOK,
            source="/github/issue",
            payload={"action": "opened", "issue": {"number": 1147}},
        ),
        Trigger(
            kind=TriggerKind.FILEWATCH,
            source="./watch_dir",
            payload={"changes": [{"type": "modified", "path": "src/auth.py"}]},
        ),
        Trigger(
            kind=TriggerKind.WEBHOOK,
            source="/unknown/path",
            payload={},
        ),
    ]
    for tr in examples:
        target = matcher.route(tr)
        verdict = (
            f"→ {target}"
            if target is not None
            else "→ (no match — would be dropped)"
        )
        step("route", f"{tr.kind.value:>10}  {tr.source:24}  {verdict}")


def fire_synthetic_trigger(waves_root: Path) -> Trigger:
    """Append a trigger to the host's TriggerLog, the way the manual seam would.

    The host reads `triggers.jsonl`; we write directly. Real deployments
    would `nc -U manual.sock` or POST to the webhook listener.
    """
    log = TriggerLog(waves_root / "triggers.jsonl")
    tr = Trigger(
        kind=TriggerKind.WEBHOOK,
        source="/github/issue",
        payload={
            "action": "opened",
            "issue": {
                "number": 1147,
                "title": "Tool.run callers should pass _trace explicitly",
                "body": (
                    "Several call sites of Tool.run are positional-only; "
                    "would like a kwarg-only sentinel to make tracing toggles "
                    "explicit. See src/tool/registry.py:88 and "
                    "src/tool/dispatch.py:142."
                ),
            },
        },
    )
    log.append(tr)
    step("trigger", f"appended trigger {tr.correlation_id[:12]} to {log._path.name}")
    step("trigger", f"  kind={tr.kind.value} source={tr.source}")
    return tr


# ---------------------------------------------------------------------------
# 6. Walk the full Spawn → orchestrate → bind path with stub workers
# ---------------------------------------------------------------------------


async def orchestrate(spec_path: Path, session_dir: Path) -> dict:
    """Run the coordinator's Spawn-loop end to end with stub workers.

    Stages:
      1. Boot a real RLMRepl (sandbox subprocess); load `repo_handle` and
         `spec_handle` into it.
      2. Build a TaskRegistry.
      3. Construct the coordinator Role; filter tools.
      4. Construct two Spawn tool instances (researcher + implementer
         lanes share the same `Spawn` plumbing — only the prompt /
         exposure / model differ).
      5. Attach our stub_worker_runner — the **runner-injection seam**.
      6. Issue two Spawns concurrently (asyncio.gather).
      7. After both bind, expose researcher's result to the implementer
         on a follow-up Spawn (cross-worker handoff via coordinator).
      8. Read every bound handle from the REPL — verify the bytes
         survived the round-trip.

    Returns a metrics dict for the demo summary.
    """
    spec = parse_swarm(spec_path)
    repl = RLMRepl()
    await repl.start()

    # Load the bulk content. In real life this would be the full
    # repository as a single string and the issue body. For the demo we
    # use small synthetic strings that fit in the terminal.
    fake_repo = (
        "# repo: fake source tree (demo placeholder)\n"
        + "\n".join(
            f"src/tool/{name}.py: def run(self, *args): ...  # line {i}"
            for i, name in enumerate(("base", "registry", "dispatch", "cache"))
        )
        + "\n"
    )
    fake_spec = json.dumps(
        {
            "issue_number": 1147,
            "title": "Tool.run callers should pass _trace explicitly",
            "files_hint": ["src/tool/registry.py", "src/tool/dispatch.py"],
        },
        indent=2,
    )
    await repl.bind("repo_handle", fake_repo)
    await repl.bind("spec_handle", fake_spec)
    step("repl", f"bound repo_handle ({len(fake_repo)} chars)")
    step("repl", f"bound spec_handle ({len(fake_spec)} chars)")

    # Build a TaskRegistry rooted under the demo's session_dir.
    registry = TaskRegistry(session_dir=session_dir, session_id="repo-triage-demo")

    # The coordinator role from BUILTIN_ROLES is what the kernel filters
    # the tool registry against. Workers will each get the worker role
    # via .child_role() inside Spawn.call().
    coord_role = BUILTIN_ROLES["coordinator"]
    step("role", f"coordinator role tools: {list(coord_role.tool_allowlist)}")
    step("role", f"  spawn_depth={coord_role.spawn_depth}  "
                 f"(workers will inherit child_role with depth=0)")

    # Demonstrate the tool filter at the role boundary. We don't have a
    # full tool registry here, so simulate one with a list of name-only
    # objects.
    class _FakeTool:
        def __init__(self, name: str) -> None:
            self.name = name

    fake_kernel_tools = [
        _FakeTool(n) for n in (
            "Read", "Edit", "Write", "Bash", "Glob", "Grep",
            "Spawn", "SendMessage", "Stop",
            "Peek", "Search", "Slice", "Recurse",
        )
    ]
    coord_visible = filter_tools_for_role(fake_kernel_tools, coord_role)
    step("role", f"  after filter, coordinator sees: "
                 f"{[t.name for t in coord_visible]}")
    step("role", f"  (no Bash/Edit/Write — synthesis-mandate constraint)")

    # Construct the Spawn tool — pass the REPL, registry, role, and the
    # stub runner. attach_runner() is the explicit seam.
    spawn_tool = Spawn(
        repl=repl,
        registry=registry,
        parent_role=coord_role,
        session_id="repo-triage-demo",
        parent_task_id=None,
        parent_model="anthropic/claude-opus-4-7",
    )
    spawn_tool.attach_runner(stub_worker_runner)
    step("spawn", "Spawn tool instantiated; runner attached")

    # The coordinator-side ToolContext is minimal for the demo.
    ctx = ToolContext(session_id="repo-triage-demo", tool_name="Spawn")

    # Issue two Spawns concurrently — researcher + implementer.
    # asyncio.gather is what the kernel does when the coordinator
    # emits multiple tool calls in one turn.
    banner("Coordinator turn 1: spawn researcher (parallel pre-work)")
    researcher_input = {
        "prompt": (
            "research: Find every call site of Tool.run in the codebase. "
            "Cite paths and line numbers. The issue body in spec_handle "
            "describes the desired API change."
        ),
        "expose": ["repo_handle", "spec_handle"],
        "bind_as": "findings_a",
        "model": "inherit",
        "max_turns": 5,
    }
    researcher_result = await spawn_tool.call(researcher_input, ctx)
    step("spawn", f"researcher returned: status="
                 f"{researcher_result.metadata.get('status')}")
    step("spawn", f"  bound result handle: findings_a "
                 f"({repl.handles.get('findings_a').total_chars} chars)")

    # The coordinator now Peeks at the result — no re-reading from disk.
    headline = await repl.peek("findings_a", start=0, end=80)
    step("peek", f"coord Peek(findings_a, 0, 80) → {headline.strip()[:64]!r}")

    banner("Coordinator turn 2: spawn implementer with researcher's handle")
    implementer_input = {
        "prompt": (
            "implement: Apply the change recommended in findings_a. The "
            "researcher already cited the files and line numbers; do not "
            "re-derive them — Peek findings_a for the recommendation, "
            "then make the edits."
        ),
        # ── The cross-worker handoff happens here ──
        # findings_a was bound by the researcher; we now expose it to
        # the implementer. ADR-029 §"Worker-to-worker via the
        # coordinator only" — the coordinator is the only path.
        "expose": ["repo_handle", "spec_handle", "findings_a"],
        "bind_as": "implementation",
        "model": "inherit",
        "max_turns": 5,
    }
    implementer_result = await spawn_tool.call(implementer_input, ctx)
    step("spawn", f"implementer returned: status="
                 f"{implementer_result.metadata.get('status')}")
    step("spawn", f"  bound result handle: implementation "
                 f"({repl.handles.get('implementation').total_chars} chars)")

    # Final coordinator-side Peek — same operation as the first one,
    # different handle. From the coordinator's view, every worker's
    # output is just another addressable variable.
    impl_head = await repl.peek("implementation", start=0, end=80)
    step("peek", f"coord Peek(implementation, 0, 80) → {impl_head.strip()[:64]!r}")

    # Pull task records from the registry to show the lifecycle.
    banner("Task registry: every Task has identity, state, output_path")
    for t in registry.list():
        step("task", f"id={t.task_id}  status={t.status.value}  "
                     f"role={t.metadata.get('role')}")

    # Shut down the REPL subprocess cleanly.
    await repl.shutdown()
    step("repl", "shutdown clean")

    return {
        "tasks": [t.task_id for t in registry.list()],
        "handles": [h.name for h in repl.handles.list()],
        "researcher_status": researcher_result.metadata.get("status"),
        "implementer_status": implementer_result.metadata.get("status"),
    }


# ---------------------------------------------------------------------------
# 7. Inspect the topology + state via the host RPC
# ---------------------------------------------------------------------------


def inspect_via_rpc(waves_root: Path) -> None:
    """Talk to the daemon over its Unix socket; show ping + ls_tasks."""
    if not rpc.is_daemon_running(waves_root):
        step("inspect", "daemon not running — skipping RPC")
        return
    try:
        pong = rpc.call(waves_root, {"op": "ping"})
        step("inspect", f"ping → {pong}")
        tasks = rpc.call(waves_root, {"op": "ls_tasks"})
        step("inspect", f"ls_tasks → {tasks}")
    except rpc.HostRPCError as e:
        step("inspect", f"rpc error: {e}")


# ---------------------------------------------------------------------------
# 8. Uninstall + summary
# ---------------------------------------------------------------------------


def uninstall_bundle(waves_root: Path) -> None:
    installer = BundleInstaller(root=waves_root)
    if installer.uninstall("repo-triage"):
        step("uninst", "✓ uninstalled repo-triage")
    else:
        step("uninst", "(nothing to uninstall)")


# ---------------------------------------------------------------------------
# Top-level demo driver
# ---------------------------------------------------------------------------


def main() -> int:
    banner("repo-triage — duhwave showpiece")
    step("about", "A persistent multi-agent swarm: 3 agents, 2 triggers, depth-1.")
    step("about", "Stub workers — architecture is real, no model calls.")

    stages_ok = 0
    stages_total = 7
    daemon_proc: subprocess.Popen | None = None

    # Use a tempdir so we don't touch the user's real ~/.duh/waves.
    with tempfile.TemporaryDirectory(prefix="duhwave-demo-") as tmp:
        tmp_path = Path(tmp)
        waves_root = tmp_path / "waves"
        waves_root.mkdir(parents=True)
        bundle_dir = tmp_path / "out"
        bundle_dir.mkdir(parents=True)
        session_dir = tmp_path / "session"
        session_dir.mkdir(parents=True)

        try:
            # Stage 1: build
            banner("Stage 1: build the .duhwave bundle")
            bundle = build_bundle(bundle_dir)
            stages_ok += 1

            # Stage 2: install
            banner("Stage 2: install into sandbox ~/.duh/waves root")
            install_bundle(bundle, waves_root)
            stages_ok += 1

            # Stage 3: start daemon
            banner("Stage 3: start the host daemon")
            daemon_proc, log_path = start_daemon(waves_root)
            stages_ok += 1

            # Stage 4: trigger + show routing
            banner("Stage 4: send a synthetic trigger; show matcher routing")
            show_routing(SPEC_DIR / "swarm.toml")
            fire_synthetic_trigger(waves_root)
            stages_ok += 1

            # Stage 5: orchestrate via Spawn — the centerpiece
            banner("Stage 5: walk the Spawn → orchestrate → bind path")
            metrics = asyncio.run(orchestrate(SPEC_DIR / "swarm.toml", session_dir))
            step("metrics", f"tasks: {metrics['tasks']}")
            step("metrics", f"handles: {metrics['handles']}")
            step("metrics", f"researcher status: {metrics['researcher_status']}")
            step("metrics", f"implementer status: {metrics['implementer_status']}")
            stages_ok += 1

            # Stage 6: RPC inspection
            banner("Stage 6: inspect via host RPC (Unix socket)")
            inspect_via_rpc(waves_root)
            stages_ok += 1

            # Stage 7: stop + uninstall
            banner("Stage 7: stop daemon + uninstall")
            if daemon_proc is not None:
                stop_daemon(daemon_proc, waves_root)
                daemon_proc = None
            uninstall_bundle(waves_root)
            stages_ok += 1

        finally:
            # Defensive cleanup — anything we didn't get to.
            if daemon_proc is not None and daemon_proc.poll() is None:
                daemon_proc.terminate()
                try:
                    daemon_proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    daemon_proc.kill()

    banner("Demo summary")
    step("done", f"Demo complete. {stages_ok}/{stages_total} stages OK.")
    return 0 if stages_ok == stages_total else 1


if __name__ == "__main__":
    sys.exit(main())
