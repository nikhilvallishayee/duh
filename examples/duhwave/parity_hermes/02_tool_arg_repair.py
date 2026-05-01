#!/usr/bin/env python3
"""02 — Tool-arg repair middleware (Hermes pattern → ADR-028 realisation).

Hermes Agent's ``_repair_tool_call_arguments`` is the single biggest
quality lift for local / fine-tuned models — they emit JSON that's
*almost* valid (trailing commas, Python ``True``/``False``/``None``,
smart quotes, prose wrappers, raw newlines inside strings) and
strict ``json.loads()`` rejects all of it.

duhwave ships the same repair pipeline at
:func:`duh.adapters.tool_repair.repair_tool_arguments`, wired into
the OpenAI-shape adapter automatically. Strict JSON fast-paths first;
only broken inputs hit the repair codepath.

This script round-trips six malformed inputs and prints the recovered
dict for each. Exit 0 only if all six recover correctly.

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/parity_hermes/02_tool_arg_repair.py
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.adapters.tool_repair import repair_tool_arguments  # noqa: E402


# ---- pretty output -------------------------------------------------------


def section(title: str) -> None:
    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)


def case_header(n: int, label: str) -> None:
    print()
    print(f"  ── case {n}: {label} ──")


def show(label: str, value: object) -> None:
    print(f"    {label:<10} {value!r}")


def ok(msg: str) -> None:
    print(f"  ✓ {msg}")


def fail(msg: str) -> None:
    print(f"  ✗ {msg}")


# ---- the demo ------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class RepairCase:
    label: str
    raw: str
    expected: dict[str, Any]


CASES: list[RepairCase] = [
    RepairCase(
        label="trailing commas",
        raw='{"path": "main.py", "verbose": true,}',
        expected={"path": "main.py", "verbose": True},
    ),
    RepairCase(
        label="Python literals (True/False/None)",
        raw='{"recursive": True, "follow_symlinks": False, "limit": None}',
        expected={"recursive": True, "follow_symlinks": False, "limit": None},
    ),
    RepairCase(
        label="smart quotes (\u201c \u201d)",
        raw='{\u201cpath\u201d: \u201cmain.py\u201d, \u201cmode\u201d: \u201cread\u201d}',
        expected={"path": "main.py", "mode": "read"},
    ),
    RepairCase(
        label="prose wrapper around JSON body",
        raw='Sure, here is the call: {"path": "x.py", "verbose": true}. Done.',
        expected={"path": "x.py", "verbose": True},
    ),
    RepairCase(
        label="raw control chars inside string values",
        raw='{"body": "line one\nline two\nline three", "ok": true}',
        expected={"body": "line one\nline two\nline three", "ok": True},
    ),
    RepairCase(
        label="combined breakage (prose + smart quotes + Py lit + trailing comma)",
        raw=(
            "Here you go:\n"
            "{\u201cpath\u201d: \u201cmain.py\u201d, "
            "\u201cverbose\u201d: True, \u201climit\u201d: None,}"
        ),
        expected={"path": "main.py", "verbose": True, "limit": None},
    ),
]


def main() -> int:
    section("02 — Tool-arg repair (Hermes → ADR-028)")
    print()
    print("  Open-weights and fine-tuned models routinely emit *almost-valid*")
    print("  JSON. duhwave's repair pipeline runs in order: prose-strip →")
    print("  smart-quotes → Python-literals → control-chars → trailing-commas")
    print("  → strict json.loads. Each step is idempotent; strict JSON")
    print("  fast-paths first.")

    section("Round-trips")
    successes = 0
    for n, case in enumerate(CASES, start=1):
        case_header(n, case.label)
        show("input:", case.raw)
        repaired = repair_tool_arguments(case.raw)
        show("output:", repaired)
        if repaired == case.expected:
            print(f"    ✓ matches expected {case.expected!r}")
            successes += 1
        else:
            print(f"    ✗ expected {case.expected!r}; got {repaired!r}")

    section("Summary")
    if successes != len(CASES):
        fail(f"tool-arg repair: {successes}/{len(CASES)} cases recovered")
        return 1
    ok(f"tool-arg repair: {successes}/{len(CASES)} cases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
