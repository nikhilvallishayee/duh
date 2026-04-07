"""Doctor diagnostics for D.U.H. CLI."""

from __future__ import annotations

import os
import shutil
import sys

from duh.tools.registry import get_all_tools


def run_doctor() -> int:
    checks: list[tuple[str, bool, str]] = []

    py_version = sys.version.split()[0]
    py_ok = sys.version_info >= (3, 12)
    checks.append(("Python version", py_ok,
                    f"{py_version} {'(>= 3.12)' if py_ok else '(need >= 3.12)'}"))

    # Provider API keys
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
    checks.append(("ANTHROPIC_API_KEY", bool(anthropic_key),
                    "set" if anthropic_key else "not set"))

    openai_key = os.environ.get("OPENAI_API_KEY", "")
    checks.append(("OPENAI_API_KEY", True,
                    "set" if openai_key else "not set (optional)"))

    # Ollama
    ollama_ok = False
    try:
        import httpx
        r = httpx.get("http://localhost:11434/api/tags", timeout=2)
        if r.status_code == 200:
            models = r.json().get("models", [])
            model_names = [m.get("name", "?") for m in models[:5]]
            ollama_ok = True
            checks.append(("Ollama", True, f"running ({', '.join(model_names)})"))
        else:
            checks.append(("Ollama", True, "not running (optional)"))
    except Exception:
        checks.append(("Ollama", True, "not running (optional)"))

    # Config
    config_dir = os.path.expanduser("~/.config/duh")
    checks.append(("Config directory", True,
                    f"{config_dir} {'(exists)' if os.path.isdir(config_dir) else '(not created yet)'}"))

    # SDKs
    try:
        import anthropic  # noqa: F401
        checks.append(("anthropic SDK", True, "installed"))
    except ImportError:
        checks.append(("anthropic SDK", False, "not installed (pip install anthropic)"))

    try:
        import openai  # noqa: F401
        checks.append(("openai SDK", True, "installed"))
    except ImportError:
        checks.append(("openai SDK", True, "not installed (optional)"))

    # Tools
    tools = get_all_tools()
    checks.append(("Tools available", len(tools) > 0,
                    ", ".join(getattr(t, "name", "?") for t in tools)))

    # Skills
    from duh.kernel.skill import load_all_skills
    skills = load_all_skills(".")
    checks.append(("Skills loaded", True,
                    f"{len(skills)} skills" if skills else "none (check .duh/skills/ or .claude/skills/)"))

    # Provider summary
    providers = []
    if anthropic_key:
        providers.append("Anthropic")
    if openai_key:
        providers.append("OpenAI")
    if ollama_ok:
        providers.append("Ollama")
    provider_summary = ", ".join(providers) if providers else "none available"
    checks.append(("Providers ready", len(providers) > 0, provider_summary))

    all_ok = True
    for name, ok, detail in checks:
        status = "ok" if ok else "FAIL"
        if not ok:
            all_ok = False
        sys.stdout.write(f"  [{status:>4}] {name}: {detail}\n")

    sys.stdout.write(f"\n{'All checks passed.' if all_ok else 'Some checks failed.'}\n")
    return 0 if all_ok else 1
