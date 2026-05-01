#!/usr/bin/env python3
"""agile-team — the headline duhwave showpiece.

A single CLI invocation triggers a 5-agent agile-team swarm to deliver a
feature end-to-end:

    PM  →  Architect  →  Engineer  →  Tester  →  Reviewer

The whole pipeline runs in one host process via real duhwave production
code paths — :class:`RLMRepl`, :class:`Role`, :class:`RLMHandleView`,
:class:`Spawn`, :class:`TaskRegistry`, :class:`InProcessExecutor` — but
each worker's "model call" is a deterministic stub. That keeps the
demo:

- byte-reproducible: same input → same output bytes (verify_run.py
  diffs against ``expected_output/``);
- fast: about half a second wall-clock;
- free: no model API calls, no network.

The runner-injection seam is :func:`build_runner_router` in
``runners.py`` — replace it with a router that drives
``duh.kernel.engine.Engine`` against a real model and the same
coordinator orchestration runs against live agents.

Usage::

    python examples/duhwave/agile_team/main.py "Add a token-bucket rate limiter to utils.py"
    python examples/duhwave/agile_team/main.py "<prompt>" --out-dir ./my-run

Exits 0 on success; 1 on any spawn / bind / write failure.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Path bootstrap — let the demo run as a script from anywhere
# ---------------------------------------------------------------------------

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.duhwave.coordinator.role import BUILTIN_ROLES  # noqa: E402
from duh.duhwave.coordinator.spawn import Spawn  # noqa: E402
from duh.duhwave.rlm.repl import RLMRepl  # noqa: E402
from duh.duhwave.task.executors import InProcessExecutor  # noqa: E402
from duh.duhwave.task.registry import TaskRegistry, TaskStatus  # noqa: E402
from duh.kernel.tool import ToolContext  # noqa: E402

# Local demo modules.
from examples.duhwave.agile_team.roles import BUILTIN_AGILE_ROLES  # noqa: E402
from examples.duhwave.agile_team.runners import build_runner_router  # noqa: E402


# ---------------------------------------------------------------------------
# Embedded codebase — stand-in for utils.py
# ---------------------------------------------------------------------------
#
# A small, plausible Python source string (~2 KB) that the agents pretend
# to extend. Workers see this through the ``codebase`` handle.

_CODEBASE = '''\
"""utils.py — common helpers for the demo project."""
from __future__ import annotations

import time


def now_ms() -> int:
    """Return current monotonic time in milliseconds."""
    return int(time.monotonic() * 1000)


def chunked(seq, n):
    """Yield successive n-sized chunks from ``seq``."""
    if n <= 0:
        raise ValueError("n must be positive")
    buf = []
    for item in seq:
        buf.append(item)
        if len(buf) == n:
            yield buf
            buf = []
    if buf:
        yield buf


def clamp(value, lo, hi):
    """Clamp ``value`` to the inclusive range [lo, hi]."""
    if lo > hi:
        raise ValueError("lo must not exceed hi")
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


def retry(fn, attempts: int = 3, delay_s: float = 0.1):
    """Call ``fn`` up to ``attempts`` times; sleep ``delay_s`` between."""
    last_exc: Exception | None = None
    for _ in range(attempts):
        try:
            return fn()
        except Exception as e:  # pragma: no cover - retry path
            last_exc = e
            time.sleep(delay_s)
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("retry: zero attempts")
'''


# ---------------------------------------------------------------------------
# Pretty terminal output
# ---------------------------------------------------------------------------

_RULE_HEAVY = "=" * 64
_RULE_LIGHT = "\u2500" * 64  # ── (U+2500)


def _banner(title: str, *, quiet: bool) -> None:
    if quiet:
        return
    print()
    print(_RULE_HEAVY)
    print(f"  {title}")
    print(_RULE_HEAVY)
    sys.stdout.flush()


def _section(title: str, *, quiet: bool) -> None:
    if quiet:
        return
    print()
    print(_RULE_LIGHT)
    print(f"  {title}")
    print(_RULE_LIGHT)
    sys.stdout.flush()


def _say(msg: str, *, quiet: bool) -> None:
    if not quiet:
        print(msg)
        sys.stdout.flush()


# ---------------------------------------------------------------------------
# Pipeline definition — single source of truth for stages
# ---------------------------------------------------------------------------
#
# Each tuple says: (stage_no, role_name, display_name, expose, bind_as,
# success_caption). The whole orchestrator below is data-driven from
# this list.

_PIPELINE: list[tuple[int, str, str, list[str], str, str]] = [
    (
        1, "pm", "PM (Product Manager)",
        ["spec", "codebase"], "refined_spec",
        "acceptance criteria extracted",
    ),
    (
        2, "architect", "Architect",
        ["spec", "refined_spec", "codebase"], "adr_draft",
        "ADR with API surface + tradeoffs",
    ),
    (
        3, "engineer", "Engineer",
        ["refined_spec", "adr_draft", "codebase"], "implementation",
        "implementation drafted",
    ),
    (
        4, "tester", "Tester",
        ["refined_spec", "implementation"], "test_suite",
        "pytest suite generated",
    ),
    (
        5, "reviewer", "Reviewer",
        ["adr_draft", "implementation", "test_suite"], "review_notes",
        "review with verdict",
    ),
]


# Stages that emit Python source (output filename ends in ``.py``); the
# rest emit Markdown.
_PYTHON_STAGES: frozenset[str] = frozenset({"implementation", "test_suite"})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_verdict(review_text: str) -> str:
    """Pull the verdict line from the reviewer's output.

    Looks for the canonical tokens in priority order; falls back to
    "(no verdict found)" if nothing matches.
    """
    upper = review_text.upper()
    for token in ("APPROVE WITH NITS", "APPROVE", "REJECT"):
        if token in upper:
            return token
    return "(no verdict found)"


# ---------------------------------------------------------------------------
# Output writer — the six artefact files
# ---------------------------------------------------------------------------


async def _peek_full(repl: RLMRepl, name: str) -> str:
    """Peek a handle's full contents — handles up to 1 MB."""
    h = repl.handles.get(name)
    if h is None:
        raise RuntimeError(f"unknown handle: {name}")
    # Peek with a generous end; the REPL clamps to actual length.
    return await repl.peek(name, start=0, end=max(h.total_chars, 1))


async def _write_outputs(
    repl: RLMRepl,
    metrics: dict[str, Any],
    out_dir: Path,
) -> list[tuple[str, int]]:
    """Write the six artefact files; return [(filename, size_bytes), ...]."""
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[tuple[str, int]] = []

    # The five stage handles, mapped to filenames.
    file_map: list[tuple[str, str]] = [
        ("refined_spec", "refined_spec.md"),
        ("adr_draft", "adr_draft.md"),
        ("implementation", "implementation.py"),
        ("test_suite", "test_suite.py"),
        ("review_notes", "review_notes.md"),
    ]

    for handle_name, filename in file_map:
        text = await _peek_full(repl, handle_name)
        path = out_dir / filename
        path.write_text(text, encoding="utf-8")
        written.append((filename, len(text.encode("utf-8"))))

    # SUMMARY.md is synthesised by the coordinator — already in metrics.
    summary_path = out_dir / "SUMMARY.md"
    summary_path.write_text(metrics["summary_text"], encoding="utf-8")
    written.append(("SUMMARY.md", len(metrics["summary_text"].encode("utf-8"))))
    return written


# ---------------------------------------------------------------------------
# Final banner
# ---------------------------------------------------------------------------


def _print_outputs_banner(
    out_dir: Path,
    written: list[tuple[str, int]],
    elapsed_s: float,
    *,
    quiet: bool,
) -> None:
    if quiet:
        # Quiet mode: a single machine-readable line per file.
        for name, size in written:
            print(f"{out_dir / name} {size}")
        return

    _section(f"Outputs written to {out_dir}:", quiet=False)
    total = 0
    for name, size in written:
        total += size
        # Right-align the size column at width 9.
        print(f"  {name:<22}  {size:>7,} B  \u2713")
    print()
    kb = total / 1024.0
    print(f"  Total: {len(written)} files, ~{kb:.1f} KB")
    print(
        f"  Run took {elapsed_s:.2f} seconds with 5 stub workers, "
        f"depth-1 coordinator."
    )
    sys.stdout.flush()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="agile_team",
        description=(
            "Run the duhwave 5-agent agile-team pipeline against a feature "
            "request. Deterministic stub workers; no model API calls."
        ),
    )
    parser.add_argument(
        "prompt",
        help="The user's feature request, e.g. 'Add a token-bucket rate limiter to utils.py'.",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=Path(__file__).parent / "out_run",
        help=(
            "Directory to write the six artefact files into. Created if "
            "absent. Default: ./out_run/ alongside this script."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Suppress the section banners; emit only artefact paths + sizes.",
    )
    parser.add_argument(
        "--use-openai",
        action="store_true",
        help=(
            "Replace the deterministic stub runners with real OpenAI calls "
            "via D.U.H.'s native adapter. Requires OPENAI_API_KEY."
        ),
    )
    parser.add_argument(
        "--openai-model",
        default="gpt-4o-mini",
        help="OpenAI model id when --use-openai is set (default: gpt-4o-mini).",
    )
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    quiet: bool = args.quiet
    user_prompt: str = args.prompt
    out_dir: Path = Path(args.out_dir).resolve()

    _banner("duhwave agile-team headless run", quiet=quiet)
    _say(f"  user request: {user_prompt}", quiet=quiet)

    started = time.monotonic()

    # Run the pipeline against a tmp session_dir so the registry's
    # task-state files don't leak into the user's project tree.
    with tempfile.TemporaryDirectory(prefix="agile-team-") as td:
        session_dir = Path(td)

        # We need the REPL alive both during pipeline execution and
        # during file-writing (peeking handles), so reuse a single
        # RLMRepl instance via the orchestrator's pattern. Re-architect
        # for simplicity: run the pipeline, then write files inside the
        # same try-block.
        repl = RLMRepl()
        await repl.start()
        try:
            await repl.bind("spec", user_prompt)
            await repl.bind("codebase", _CODEBASE)

            session_id = "agile-team"
            registry = TaskRegistry(session_dir=session_dir, session_id=session_id)
            coord_role = BUILTIN_ROLES["coordinator"]

            active_role: dict[str, str] = {"name": ""}
            ledger = None
            if getattr(args, "use_openai", False):
                from examples.duhwave.agile_team.openai_runner import (
                    BenchmarkLedger,
                    build_openai_router,
                )
                ledger = BenchmarkLedger(model=args.openai_model)
                runner = build_openai_router(
                    active_role, model=args.openai_model, ledger=ledger
                )
                _say(
                    f"  ⚠  USING REAL OPENAI MODEL: {args.openai_model}",
                    quiet=quiet,
                )
            else:
                runner = build_runner_router(active_role)

            spawn_tool = Spawn(
                repl=repl,
                registry=registry,
                parent_role=coord_role,
                session_id=session_id,
                parent_task_id=None,
                parent_model="anthropic/claude-opus-4-7",
            )
            spawn_tool.attach_runner(runner)
            ctx = ToolContext(session_id=session_id, tool_name="Spawn")

            metrics: dict[str, Any] = {"stages": [], "task_ids": [], "total_chars": 0}

            for stage_no, role_name, display_name, expose, bind_as, caption in _PIPELINE:
                _section(f"Stage {stage_no}/5  {display_name}", quiet=quiet)
                active_role["name"] = role_name
                spawn_input = {
                    "prompt": (
                        f"You are the {role_name}. The user request is in handle 'spec'. "
                        f"Produce your role's output. Receive: {expose}. Bind as: {bind_as}."
                    ),
                    "expose": expose,
                    "bind_as": bind_as,
                    "model": "inherit",
                    "max_turns": 1,
                }
                result = await spawn_tool.call(spawn_input, ctx)
                if result.is_error:
                    print(
                        f"[stage {stage_no}] FAILED: {result.output}",
                        file=sys.stderr,
                    )
                    return 1
                handle = repl.handles.get(bind_as)
                if handle is None:
                    print(
                        f"[stage {stage_no}] handle {bind_as!r} not bound",
                        file=sys.stderr,
                    )
                    return 1
                task_id = result.metadata.get("task_id", "?")
                metrics["task_ids"].append(task_id)
                metrics["total_chars"] += handle.total_chars
                metrics["stages"].append({
                    "stage": stage_no,
                    "role": role_name,
                    "task_id": task_id,
                    "bind_as": bind_as,
                    "chars": handle.total_chars,
                    "expose": list(expose),
                })
                _say(
                    f"[stage {stage_no}] spawned task {task_id} \u2192 "
                    f"{bind_as} ({handle.total_chars:,} chars)",
                    quiet=quiet,
                )
                _say(f"[stage {stage_no}] \u2713 {caption}", quiet=quiet)

            # Verify every Task ended COMPLETED.
            for t in registry:
                if t.status is not TaskStatus.COMPLETED:
                    print(
                        f"task {t.task_id} ended in {t.status.value}, expected completed",
                        file=sys.stderr,
                    )
                    return 1

            # ---- Coordinator synthesis (SUMMARY.md) -----------------
            synthesis_lines: list[str] = [
                "# Agile-Team Run \u2014 Summary",
                "",
                f"User request: {user_prompt}",
                "",
                "## What each agent contributed",
                "",
            ]
            for record in metrics["stages"]:
                head = await repl.peek(record["bind_as"], start=0, end=200)
                head_clean = head.strip().splitlines()[0] if head.strip() else "(empty)"
                synthesis_lines.append(
                    f"- **{record['role']}** (task `{record['task_id']}`, "
                    f"{record['chars']:,} chars): {head_clean}"
                )
            synthesis_lines.append("")

            review_text = await repl.peek("review_notes", start=0, end=4096)
            verdict = _extract_verdict(review_text)
            synthesis_lines.extend([
                "## Final verdict",
                "",
                verdict,
                "",
                "## Token usage (deterministic stub estimate)",
                "",
            ])
            total_tokens = 0
            for record in metrics["stages"]:
                est_tokens = max(1, record["chars"] // 4)
                total_tokens += est_tokens
                synthesis_lines.append(
                    f"- {record['role']}: ~{est_tokens:,} tokens"
                )
            synthesis_lines.extend([
                "",
                f"**Total: ~{total_tokens:,} tokens**",
                "",
            ])
            metrics["summary_text"] = "\n".join(synthesis_lines)

            # ---- Write the six output files ------------------------
            written = await _write_outputs(repl, metrics, out_dir)
        finally:
            await repl.shutdown()

    elapsed = time.monotonic() - started
    _print_outputs_banner(out_dir, written, elapsed, quiet=quiet)

    # ---- Real-runner ledger (only when --use-openai) -----------
    if ledger is not None and ledger.stages:
        if not quiet:
            _section("OpenAI ledger", quiet=False)
            print(f"  model: {ledger.model}")
            for s in ledger.stages:
                print(
                    f"  [{s.role:9}]  in={s.prompt_tokens:>5}  "
                    f"out={s.completion_tokens:>5}  cached={s.cached_tokens:>5}  "
                    f"{s.duration_s:5.2f}s"
                )
            print(
                f"\n  totals: in={ledger.total_prompt_tokens:,}  "
                f"out={ledger.total_completion_tokens:,}  "
                f"cached={ledger.total_cached_tokens:,}"
            )
            print(f"  estimated cost: ${ledger.estimated_cost_usd():.4f}")
            print(f"  total wall:     {ledger.total_duration_s:.2f}s")

    return 0


def main() -> int:
    args = _parse_args(sys.argv[1:])
    try:
        return asyncio.run(_amain(args))
    except KeyboardInterrupt:
        print("\n[agile_team] interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
