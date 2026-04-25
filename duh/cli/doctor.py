"""Doctor diagnostics for D.U.H. CLI."""

from __future__ import annotations

import os
import shutil
import sys

from duh.tools.registry import get_all_tools


def _format_latency(ms: int) -> str:
    """Format latency for display."""
    if ms < 1000:
        return f"{ms}ms"
    return f"{ms / 1000:.1f}s"


def run_doctor() -> int:
    checks: list[tuple[str, bool, str]] = []

    py_version = sys.version.split()[0]
    py_ok = sys.version_info >= (3, 12)
    checks.append(("Python version", py_ok,
                    f"{py_version} {'(>= 3.12)' if py_ok else '(need >= 3.12)'}"))

    # Provider API keys (presence check) — env-var names come from the
    # single source of truth in ``duh.providers.registry.PROVIDER_ENV_VARS``.
    from duh.providers.registry import PROVIDER_ENV_VARS, get_api_key
    anthropic_env = PROVIDER_ENV_VARS["anthropic"][0]
    anthropic_key = get_api_key("anthropic")
    checks.append((anthropic_env, bool(anthropic_key),
                    "set" if anthropic_key else "not set"))

    openai_env = PROVIDER_ENV_VARS["openai"][0]
    openai_key = get_api_key("openai")
    checks.append((openai_env, True,
                    "set" if openai_key else "not set (optional)"))

    # --- Provider connectivity (actual health checks) ---
    from duh.kernel.health_check import HealthChecker
    checker = HealthChecker(timeout=5.0)

    if anthropic_key:
        result = checker.check_provider("anthropic")
        latency = _format_latency(result["latency_ms"])
        if result["healthy"]:
            checks.append(("Anthropic connectivity", True, f"reachable ({latency})"))
        else:
            checks.append(("Anthropic connectivity", False,
                            f"unreachable ({result['error']}, {latency})"))

    if openai_key:
        result = checker.check_provider("openai")
        latency = _format_latency(result["latency_ms"])
        if result["healthy"]:
            checks.append(("OpenAI connectivity", True, f"reachable ({latency})"))
        else:
            checks.append(("OpenAI connectivity", False,
                            f"unreachable ({result['error']}, {latency})"))

    # Ollama (connectivity replaces old presence check)
    ollama_ok = False
    result = checker.check_provider("ollama")
    latency = _format_latency(result["latency_ms"])
    if result["healthy"]:
        ollama_ok = True
        # Try to get model names from Ollama
        model_info = ""
        try:
            import httpx
            r = httpx.get("http://localhost:11434/api/tags", timeout=2)
            if r.status_code == 200:
                models = r.json().get("models", [])
                model_names = [m.get("name", "?") for m in models[:5]]
                model_info = f", models: {', '.join(model_names)}" if model_names else ""
        except Exception:
            pass
        checks.append(("Ollama", True, f"running ({latency}{model_info})"))
    else:
        checks.append(("Ollama", True, f"not running (optional, {latency})"))

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

    # --- MCP server health ---
    mcp_healthy = 0
    mcp_total = 0
    try:
        from duh.config import load_config
        app_config = load_config(cwd=os.getcwd())
        if app_config.mcp_servers:
            mcp_servers = app_config.mcp_servers.get("mcpServers", app_config.mcp_servers)
            mcp_total = len(mcp_servers)
            # We can only check config presence here (no live connections in doctor).
            # Report configured servers.
            server_names = list(mcp_servers.keys())
            checks.append(("MCP servers configured", mcp_total > 0,
                            ", ".join(server_names)))
    except Exception:
        pass  # No config or config error -- skip MCP section

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

    # --- Adapter availability (ADR-075) ---
    sys.stdout.write(_render_adapter_section())

    sys.stdout.write(f"\n{'All checks passed.' if all_ok else 'Some checks failed.'}\n")
    return 0 if all_ok else 1


def _render_adapter_section() -> str:
    """Render the provider-adapter availability table (ADR-027)."""
    from duh.providers.registry import (
        _google_genai_available,
    )

    rows: list[tuple[str, bool, str]] = [
        ("anthropic", True, "native (always)"),
        ("openai", True, "native (always)"),
        ("ollama", True, "native (always)"),
        ("deepseek", True, "native (api.deepseek.com)"),
        ("mistral", True, "native (api.mistral.ai)"),
        ("qwen", True, "native (DashScope)"),
        ("together", True, "native (api.together.xyz)"),
        (
            "gemini",
            _google_genai_available(),
            "native (google-genai installed)"
            if _google_genai_available()
            else "not installed (pip install google-genai)",
        ),
    ]
    lines = ["\nProviders:\n"]
    for name, ok, detail in rows:
        mark = "\u2713" if ok else "\u2717"
        lines.append(f"  {name:<11} {mark}  {detail}\n")
    return "".join(lines)
