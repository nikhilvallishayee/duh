"""Subprocess runner for SUBPROCESS-surface tasks.

Stub: a real implementation drives :class:`duh.kernel.engine.Engine` here
with a permission proxy that round-trips approval requests to the host
over stdin/stdout JSON. The host wires that into its existing
``approve()`` callable so the user sees one prompt UX regardless of
which surface a task is running on.

Argv: ``<task_id>``.
"""
from __future__ import annotations

import sys


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        sys.stderr.write("usage: _subprocess_runner.py <task_id>\n")
        return 2
    task_id = argv[1]
    sys.stdout.write(f"[duhwave subprocess] task_id={task_id}\n")
    sys.stdout.flush()
    # Real loop lands here (ADR-030 follow-up).
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
