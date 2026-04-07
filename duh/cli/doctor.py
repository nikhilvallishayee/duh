"""Doctor diagnostics for D.U.H. CLI."""

from __future__ import annotations

import os
import sys

from duh.tools.registry import get_all_tools


def run_doctor() -> int:
    checks: list[tuple[str, bool, str]] = []

    py_version = sys.version.split()[0]
    py_ok = sys.version_info >= (3, 12)
    checks.append(("Python version", py_ok,
                    f"{py_version} {'(>= 3.12)' if py_ok else '(need >= 3.12)'}"))

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    checks.append(("ANTHROPIC_API_KEY", bool(api_key), "set" if api_key else "not set"))

    config_dir = os.path.expanduser("~/.config/duh")
    checks.append(("Config directory", True,
                    f"{config_dir} {'(exists)' if os.path.isdir(config_dir) else '(not created yet)'}"))

    try:
        import anthropic  # noqa: F401
        checks.append(("anthropic SDK", True, "installed"))
    except ImportError:
        checks.append(("anthropic SDK", False, "not installed (pip install anthropic)"))

    tools = get_all_tools()
    checks.append(("Tools available", len(tools) > 0,
                    ", ".join(getattr(t, "name", "?") for t in tools)))

    all_ok = True
    for name, ok, detail in checks:
        status = "ok" if ok else "FAIL"
        if not ok:
            all_ok = False
        sys.stdout.write(f"  [{status:>4}] {name}: {detail}\n")

    sys.stdout.write(f"\n{'All checks passed.' if all_ok else 'Some checks failed.'}\n")
    return 0 if all_ok else 1
