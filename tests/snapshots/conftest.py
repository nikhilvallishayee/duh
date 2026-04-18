"""Shared fixtures for the Tier A snapshot suite (ADR-074).

Currently this file only exposes constants that the tests import; the
real work lives in :mod:`tests.snapshots.scripts`, which ``snap_compare``
(provided by the ``pytest-textual-snapshot`` plugin) loads as individual
Textual apps.
"""

from __future__ import annotations

from pathlib import Path


# Absolute path to the scripts directory; individual tests join a file
# name onto it and hand the resulting path to ``snap_compare``.
SCRIPTS_DIR: Path = Path(__file__).parent / "scripts"

# Every snapshot uses the same terminal dimensions so baselines remain
# comparable across developer machines.
SNAPSHOT_TERMINAL_SIZE: tuple[int, int] = (120, 40)
