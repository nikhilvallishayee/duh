#!/usr/bin/env python3
"""Regression check for the agile-team showpiece.

Runs ``main.py`` against the canonical prompt and diffs each output file
against the pinned ``expected_output/`` reference. Exits 0 if the bytes
match, 1 if any drift is detected.

Determinism is the demo's contract — if a contributor edits a stub
runner, the role prompts, or the embedded codebase string, this script
catches it before merge. Update ``expected_output/`` *only* when the
drift is intentional.

Usage::

    python examples/duhwave/agile_team/verify_run.py
"""
from __future__ import annotations

import filecmp
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).parent.resolve()
_MAIN = _HERE / "main.py"
_EXPECTED = _HERE / "expected_output"
_PROMPT = "Add a token-bucket rate limiter to utils.py"

# The six artefact files produced by main.py — listed in the order
# main.py writes them, so any drift is reported in a stable order.
_ARTEFACTS: tuple[str, ...] = (
    "refined_spec.md",
    "adr_draft.md",
    "implementation.py",
    "test_suite.py",
    "review_notes.md",
    "SUMMARY.md",
)


def _run_demo(out_dir: Path) -> int:
    """Invoke main.py once with the canonical prompt; return its exit code."""
    cmd = [
        sys.executable,
        str(_MAIN),
        _PROMPT,
        "--out-dir",
        str(out_dir),
        "--quiet",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"main.py failed (exit {proc.returncode}):", file=sys.stderr)
        print(proc.stdout, file=sys.stderr)
        print(proc.stderr, file=sys.stderr)
    return proc.returncode


def _diff_dirs(actual: Path, expected: Path) -> list[str]:
    """Return a list of human-readable drift descriptions; empty if clean."""
    drift: list[str] = []
    if not expected.exists():
        drift.append(
            f"expected_output/ does not exist at {expected} — "
            f"run main.py once and copy out_run/ here to seed it."
        )
        return drift

    expected_names = {p.name for p in expected.iterdir() if p.is_file()}
    actual_names = {p.name for p in actual.iterdir() if p.is_file()}

    missing = expected_names - actual_names
    extra = actual_names - expected_names
    for n in sorted(missing):
        drift.append(f"missing in actual run:    {n}")
    for n in sorted(extra):
        drift.append(f"unexpected in actual run: {n}")

    # Byte-compare every artefact we expect.
    for name in _ARTEFACTS:
        a = actual / name
        e = expected / name
        if not e.exists():
            drift.append(f"{name}: not in expected_output/ (regenerate?)")
            continue
        if not a.exists():
            drift.append(f"{name}: not produced by main.py")
            continue
        if not filecmp.cmp(a, e, shallow=False):
            a_size = a.stat().st_size
            e_size = e.stat().st_size
            drift.append(
                f"{name}: bytes differ "
                f"(actual={a_size} B, expected={e_size} B)"
            )
    return drift


def main() -> int:
    if not _MAIN.exists():
        print(f"main.py not found at {_MAIN}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="agile-verify-") as td:
        out_dir = Path(td) / "out"
        rc = _run_demo(out_dir)
        if rc != 0:
            return rc

        drift = _diff_dirs(out_dir, _EXPECTED)
        if not drift:
            print("verify_run: OK \u2014 all 6 artefacts byte-match expected_output/")
            return 0

        print("verify_run: DRIFT detected", file=sys.stderr)
        for line in drift:
            print(f"  - {line}", file=sys.stderr)
        # Helpful hint: the first time this fails for a contributor it
        # is almost always because the stubs were intentionally changed.
        print(
            "\nIf the drift is intentional, regenerate the reference with:\n"
            f"  python {_MAIN} {_PROMPT!r} --out-dir {_EXPECTED} --quiet",
            file=sys.stderr,
        )
        # Also dump the actual outputs into the script's directory for
        # easy inspection. Use a sibling .actual directory rather than
        # overwriting the canonical one.
        target = _HERE / "out_run"
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(out_dir, target)
        print(f"  (actual outputs preserved at {target} for inspection)", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
