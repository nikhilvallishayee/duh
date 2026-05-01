#!/usr/bin/env python3
"""04 — Per-swarm install isolation.

OpenClaw runs many user-defined "skills" against one gateway, each
with its own state. The duhwave realisation lives at the
:class:`BundleInstaller` layer: each installed bundle gets its own
``<waves_root>/<name>/<version>/`` subtree, listed in
``<waves_root>/index.json``. Uninstalling one leaves the others
intact.

This script:

    1. Builds two independent bundles (claw-a, claw-b) from inline
       sources.
    2. Installs both into the same waves root.
    3. Verifies each has its own ``<root>/<name>/<version>/`` directory.
    4. Verifies ``index.json`` lists both bundles.
    5. Verifies neither bundle's directory contains a path inside the
       other's directory (no overlap).
    6. Uninstalls ``claw-a``.
    7. Verifies ``claw-b``'s tree is still intact and its index entry
       remains.

Run::

    /Users/nomind/Code/duh/.venv/bin/python3 examples/duhwave/parity_claw/04_per_channel_isolation.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from duh.duhwave.bundle import (  # noqa: E402
    BUNDLE_EXT,
    BundleInstaller,
    pack_bundle,
)


def section(title: str) -> None:
    print()
    print("=" * 64)
    print(f"  {title}")
    print("=" * 64)


def step(msg: str) -> None:
    print(f"  -> {msg}")


def ok(msg: str) -> None:
    print(f"  \u2713 {msg}")


def fail(msg: str) -> None:
    print(f"  x {msg}")


def _swarm_toml(name: str, agent_id: str) -> str:
    return f"""\
[swarm]
name = "{name}"
version = "0.1.0"
description = "isolation demo: {name}"
format_version = 1

[[agents]]
id = "{agent_id}"
role = "coordinator"
model = "anthropic/claude-haiku-4-5"
tools = ["Spawn", "Stop", "Peek", "Search", "Slice"]

[[triggers]]
kind = "manual"
source = "manual:{name}"
target_agent_id = "{agent_id}"

[budget]
max_concurrent_tasks = 1
max_tokens_per_hour = 50000
max_usd_per_day = 0.50
"""


def _manifest_toml(name: str) -> str:
    return f"""\
[bundle]
name = "{name}"
version = "0.1.0"
description = "isolation demo: {name}"
author = "duhwave examples"
format_version = 1
created_at = 1745625600.0

[signing]
signed = false
"""


_PERMISSIONS_TOML = """\
[filesystem]
read = []
write = []

[network]
allow = []

[tools]
require = ["Spawn", "Stop", "Peek", "Search", "Slice"]
"""


def _build_source(parent: Path, name: str, agent_id: str) -> Path:
    """Materialise a source tree for one minimal bundle."""
    src = parent / f"src-{name}"
    src.mkdir(parents=True, exist_ok=True)
    (src / "swarm.toml").write_text(_swarm_toml(name, agent_id))
    (src / "manifest.toml").write_text(_manifest_toml(name))
    (src / "permissions.toml").write_text(_PERMISSIONS_TOML)
    return src


def main() -> int:
    section("04 - per-swarm isolation under one waves root")

    waves_root = Path(tempfile.mkdtemp(prefix="dwv-claw-iso-")).resolve()
    work = Path(tempfile.mkdtemp(prefix="dwv-iso-work-")).resolve()
    rc = 1
    try:
        installer = BundleInstaller(root=waves_root, confirm=lambda _: True)

        # ---- build + install both --------------------------------
        section("1. Pack + install two bundles into one waves root")
        src_a = _build_source(work, "claw-a", "agent_a")
        src_b = _build_source(work, "claw-b", "agent_b")
        bundle_a = work / f"claw-a{BUNDLE_EXT}"
        bundle_b = work / f"claw-b{BUNDLE_EXT}"
        pack_bundle(src_a, bundle_a)
        pack_bundle(src_b, bundle_b)
        result_a = installer.install(bundle_a)
        result_b = installer.install(bundle_b)
        ok(f"installed: {result_a.name} -> {Path(result_a.path).relative_to(waves_root)}")
        ok(f"installed: {result_b.name} -> {Path(result_b.path).relative_to(waves_root)}")

        # ---- verify per-swarm directories exist -----------------
        section("2. Verify <waves_root>/<name>/<version>/ subtrees")
        path_a = waves_root / "claw-a" / "0.1.0"
        path_b = waves_root / "claw-b" / "0.1.0"
        if not path_a.is_dir():
            fail(f"missing claw-a tree at {path_a}")
            return 1
        if not path_b.is_dir():
            fail(f"missing claw-b tree at {path_b}")
            return 1
        ok(f"claw-a tree: {sorted(p.name for p in path_a.iterdir())}")
        ok(f"claw-b tree: {sorted(p.name for p in path_b.iterdir())}")

        # ---- verify the trees do not overlap --------------------
        section("3. Verify the two trees are siblings (no overlap)")
        if path_a in path_b.parents or path_b in path_a.parents:
            fail("one bundle's tree contains the other - state would leak")
            return 1
        if path_a == path_b:
            fail("paths collided")
            return 1
        ok(f"siblings under same root: {waves_root.name}/")

        # ---- verify index lists both ----------------------------
        section("4. Verify index.json lists both bundles")
        index_path = waves_root / "index.json"
        if not index_path.is_file():
            fail(f"index.json missing at {index_path}")
            return 1
        index = json.loads(index_path.read_text())
        if "claw-a" not in index or "claw-b" not in index:
            fail(f"index incomplete: keys={list(index.keys())}")
            return 1
        ok(f"index.json keys: {sorted(index.keys())}")
        listed = installer.list_installed()
        ok(f"installer.list_installed() = {[r.name for r in listed]}")
        if {r.name for r in listed} != {"claw-a", "claw-b"}:
            fail(f"unexpected list_installed shape: {[r.name for r in listed]}")
            return 1

        # ---- uninstall claw-a -----------------------------------
        section("5. Uninstall claw-a; verify claw-b survives intact")
        a_files_before = sorted(p.relative_to(path_b).as_posix()
                                for p in path_b.rglob("*") if p.is_file())
        removed = installer.uninstall("claw-a")
        if not removed:
            fail("uninstall(claw-a) returned False")
            return 1
        ok("claw-a removed")
        if (waves_root / "claw-a").exists():
            fail("claw-a tree still present after uninstall")
            return 1
        if not path_b.is_dir():
            fail("claw-b tree disappeared along with claw-a")
            return 1
        a_files_after = sorted(p.relative_to(path_b).as_posix()
                               for p in path_b.rglob("*") if p.is_file())
        if a_files_before != a_files_after:
            fail(f"claw-b file set changed!\n   before={a_files_before}"
                 f"\n   after ={a_files_after}")
            return 1
        ok(f"claw-b file set unchanged: {a_files_after}")

        index_after = json.loads((waves_root / "index.json").read_text())
        if "claw-a" in index_after or "claw-b" not in index_after:
            fail(f"index post-uninstall wrong: {list(index_after.keys())}")
            return 1
        ok(f"index.json now lists: {sorted(index_after.keys())}")

        section("Result")
        ok("per-swarm isolation")
        rc = 0
        return rc
    finally:
        # Defensive cleanup of any leftover bundles.
        try:
            BundleInstaller(root=waves_root, confirm=lambda _: True).uninstall("claw-b")
        except Exception:
            pass
        for d in (waves_root, work):
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
