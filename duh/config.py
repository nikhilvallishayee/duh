"""Configuration loading -- settings and instruction files.

See ADR-015 for the full rationale.

Settings precedence (highest wins):
    flags > env > project config > user config

Instruction files (DUH.md, AGENTS.md) are loaded separately and
injected into the system prompt.

Usage:
    config = load_config(cli_args={"model": "opus"}, cwd=".")
    instructions = load_instructions(cwd=".")
    system_prompt = base_prompt + "\\n".join(instructions)
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config data class
# ---------------------------------------------------------------------------

@dataclass
class Config:
    """Merged configuration from all sources.

    Each field has a default. Higher-priority sources override lower.
    """

    model: str = ""
    provider: str = ""
    max_turns: int = 100
    max_cost: float | None = None
    system_prompt: str = ""
    approval_mode: str = ""
    permissions: dict[str, Any] = field(default_factory=dict)
    hooks: dict[str, Any] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)
    trifecta_acknowledged: bool = False
    auto_memory: bool = False
    # ADR-073 Wave 1 task 3: TUI permission-modal auto-deny timeout.
    # None disables the timeout (modal waits forever). Default 60s.
    approval_timeout_seconds: float | None = 60.0


# ---------------------------------------------------------------------------
# Config directory
# ---------------------------------------------------------------------------

def config_dir() -> Path:
    """Return the user config directory (~/.config/duh)."""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "duh"
    return Path.home() / ".config" / "duh"


# ---------------------------------------------------------------------------
# JSON loading
# ---------------------------------------------------------------------------

def _load_json(path: Path) -> dict[str, Any]:
    """Load a JSON file, returning empty dict on any error."""
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
        if isinstance(data, dict):
            return data
        logger.warning("Config file %s is not a JSON object, ignoring.", path)
        return {}
    except FileNotFoundError:
        return {}
    except Exception as exc:
        logger.warning("Failed to load config from %s: %s", path, exc)
        return {}


# ---------------------------------------------------------------------------
# Merging
# ---------------------------------------------------------------------------

def _merge_into(config: Config, data: dict[str, Any]) -> None:
    """Merge a dict of settings into a Config, overriding non-empty values."""
    if "model" in data and data["model"]:
        config.model = str(data["model"])
    if "provider" in data and data["provider"]:
        config.provider = str(data["provider"])
    if "max_turns" in data and data["max_turns"]:
        try:
            config.max_turns = int(data["max_turns"])
        except (ValueError, TypeError):
            pass
    if "max_cost" in data and data["max_cost"] is not None:
        try:
            config.max_cost = float(data["max_cost"])
        except (ValueError, TypeError):
            pass
    if "system_prompt" in data and data["system_prompt"]:
        config.system_prompt = str(data["system_prompt"])
    if "approval_mode" in data and data["approval_mode"]:
        config.approval_mode = str(data["approval_mode"])
    if "permissions" in data and isinstance(data["permissions"], dict):
        config.permissions.update(data["permissions"])
    if "hooks" in data and isinstance(data["hooks"], dict):
        config.hooks.update(data["hooks"])
    if "mcpServers" in data and isinstance(data["mcpServers"], dict):
        config.mcp_servers.update(data["mcpServers"])
    if "auto_memory" in data:
        config.auto_memory = bool(data["auto_memory"])
    if "approval_timeout_seconds" in data:
        val = data["approval_timeout_seconds"]
        # Explicit None (JSON null) or string "none"/"null"/"" disables the timeout.
        if val is None:
            config.approval_timeout_seconds = None
        elif isinstance(val, str) and val.strip().lower() in ("", "none", "null", "off", "disabled"):
            config.approval_timeout_seconds = None
        else:
            try:
                parsed = float(val)
                config.approval_timeout_seconds = parsed if parsed > 0 else None
            except (ValueError, TypeError):
                pass


# ---------------------------------------------------------------------------
# Environment variable mapping
# ---------------------------------------------------------------------------

_ENV_MAP: dict[str, str] = {
    "DUH_MODEL": "model",
    "DUH_PROVIDER": "provider",
    "DUH_MAX_TURNS": "max_turns",
    "DUH_MAX_COST": "max_cost",
    "DUH_SYSTEM_PROMPT": "system_prompt",
    "DUH_AUTO_MEMORY": "auto_memory",
    "DUH_APPROVAL_TIMEOUT_SECONDS": "approval_timeout_seconds",
}


def _apply_env(config: Config) -> None:
    """Apply DUH_* environment variables to config."""
    for env_var, field_name in _ENV_MAP.items():
        value = os.environ.get(env_var)
        if value is not None:
            _merge_into(config, {field_name: value})


# ---------------------------------------------------------------------------
# Project config discovery
# ---------------------------------------------------------------------------

def _find_project_config(cwd: str) -> Path | None:
    """Find the nearest .duh/settings.json walking up from cwd."""
    current = Path(cwd).resolve()
    for _ in range(100):  # safety limit
        candidate = current / ".duh" / "settings.json"
        if candidate.exists():
            return candidate
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


# ---------------------------------------------------------------------------
# Git root discovery
# ---------------------------------------------------------------------------

def _find_git_root(cwd: str) -> Path | None:
    """Find the git root by walking up from cwd looking for .git."""
    current = Path(cwd).resolve()
    for _ in range(100):
        if (current / ".git").exists():
            return current
        parent = current.parent
        if parent == current:
            break
        current = parent
    return None


# ---------------------------------------------------------------------------
# Main load function
# ---------------------------------------------------------------------------

def load_config(
    *,
    cli_args: dict[str, Any] | None = None,
    cwd: str = ".",
) -> Config:
    """Load config from all sources, merging by precedence.

    Priority (highest wins):
        4. CLI flags (cli_args)
        3. Environment variables (DUH_*)
        2. Project config (.duh/settings.json)
        1. User config (~/.config/duh/settings.json)

    Args:
        cli_args: CLI flag overrides (e.g., {"model": "opus"}).
        cwd: Current working directory for project config discovery.

    Returns:
        Merged Config object.
    """
    config = Config()

    # Layer 1: user config
    user_path = config_dir() / "settings.json"
    user_data = _load_json(user_path)
    if user_data:
        _merge_into(config, user_data)
        logger.debug("Loaded user config from %s", user_path)

    # Layer 2: project config
    project_path = _find_project_config(cwd)
    if project_path:
        project_data = _load_json(project_path)
        if project_data:
            _merge_into(config, project_data)
            logger.debug("Loaded project config from %s", project_path)

        # Load .duh/security.json (sibling of settings.json) for security keys
        security_path = project_path.parent / "security.json"
        if security_path.exists():
            security_data = _load_json(security_path)
            if security_data.get("trifecta_acknowledged") is True:
                config.trifecta_acknowledged = True
                logger.debug("trifecta_acknowledged=True from %s", security_path)

    # Layer 3: environment variables
    _apply_env(config)

    # Layer 4: CLI flags
    if cli_args:
        _merge_into(config, cli_args)

    return config


# ---------------------------------------------------------------------------
# Instruction file loading (DUH.md, AGENTS.md)
# ---------------------------------------------------------------------------

def _dirs_root_to_cwd(cwd: str) -> list[Path]:
    """Return directories from git root down to cwd (inclusive).

    If no git root is found, returns just [cwd].
    """
    cwd_path = Path(cwd).resolve()
    git_root = _find_git_root(cwd)

    if git_root is None:
        return [cwd_path]

    # Build path from git root to cwd
    dirs: list[Path] = []
    current = cwd_path
    while True:
        dirs.append(current)
        if current == git_root:
            break
        parent = current.parent
        if parent == current:
            break
        current = parent

    dirs.reverse()  # root first, cwd last
    return dirs


_MAX_INCLUDE_DEPTH = 5
_TEXT_EXTENSIONS = {".md", ".txt", ".text", ".yaml", ".yml", ".toml", ".json", ".cfg", ".ini", ".py", ".ts", ".js", ".sh"}
_INCLUDE_RE = re.compile(r"(?:^|\s)@((?:[^\s\\]|\\ )+)")

# Code-fence state machine: skip @paths inside ``` blocks
_FENCE_RE = re.compile(r"^(`{3,}|~{3,})")


def _expand_includes(
    content: str,
    base_dir: Path,
    processed: set[str] | None = None,
    depth: int = 0,
) -> list[str]:
    """Expand @path references in instruction file content.

    Implements the @include directive pattern:
    - @path, @./relative, @~/home, @/absolute
    - Skips code blocks (``` fenced)
    - Max depth 5, circular reference protection
    - Text file extensions only

    Returns list of [included_content..., original_content].
    """
    if depth >= _MAX_INCLUDE_DEPTH:
        return [content]
    if processed is None:
        processed = set()

    results: list[str] = []
    include_paths: list[Path] = []

    # Extract @paths from non-code-block lines
    in_fence = False
    for line in content.split("\n"):
        fence_match = _FENCE_RE.match(line.strip())
        if fence_match:
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in _INCLUDE_RE.finditer(line):
            raw_path = match.group(1)
            # Strip trailing punctuation and fragment identifiers
            if "#" in raw_path:
                raw_path = raw_path[:raw_path.index("#")]
            if not raw_path:
                continue
            # Unescape spaces
            raw_path = raw_path.replace("\\ ", " ")
            # Resolve path
            if raw_path.startswith("~/"):
                resolved = Path(raw_path).expanduser()
            elif raw_path.startswith("/"):
                resolved = Path(raw_path)
            else:
                # Relative (including ./ prefix)
                resolved = base_dir / raw_path
            resolved = resolved.resolve()
            # Only text files
            if resolved.suffix.lower() not in _TEXT_EXTENSIONS:
                continue
            include_paths.append(resolved)

    # Process includes (depth-first, included before parent)
    for inc_path in include_paths:
        norm = str(inc_path)
        if norm in processed:
            continue
        processed.add(norm)
        if not inc_path.exists() or not inc_path.is_file():
            continue
        try:
            inc_content = inc_path.read_text(encoding="utf-8")
        except Exception:
            continue
        # Recurse — included files can have their own @includes
        expanded = _expand_includes(inc_content, inc_path.parent, processed, depth + 1)
        results.extend(expanded)

    # Original content last (includes appear before the including file)
    results.append(content)
    return results


def _load_file_with_includes(path: Path, processed: set[str]) -> list[str]:
    """Load a file and expand its @include directives."""
    norm = str(path.resolve())
    if norm in processed:
        return []
    processed.add(norm)
    try:
        content = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return []
    return _expand_includes(content, path.parent, processed)


def load_instructions(cwd: str = ".") -> list[str]:
    """Load all DUH.md, CLAUDE.md, and AGENTS.md instruction files.

    Files are loaded in precedence order (lowest first, highest last).
    The model pays more attention to content appearing later.

    @path references in instruction files are expanded:
    - @./relative/path, @~/home/path, @/absolute/path
    - Included files appear before the file that references them
    - Max 5 levels of nesting, circular references prevented
    - Only text file extensions (.md, .txt, .py, .ts, etc.)

    Order:
        1. ~/.config/duh/DUH.md (user-global)
        2. DUH.md / .duh/DUH.md / CLAUDE.md per directory (git root to cwd)
        3. .duh/rules/*.md and .claude/rules/*.md per directory
        4. AGENTS.md per directory (open standard)

    Args:
        cwd: Current working directory.

    Returns:
        List of instruction strings, ordered by precedence.
    """
    instructions: list[str] = []
    processed: set[str] = set()

    # User-global instructions
    user_duh = config_dir() / "DUH.md"
    if user_duh.exists():
        instructions.extend(_load_file_with_includes(user_duh, processed))

    # Project instructions (root to cwd)
    for dir_path in _dirs_root_to_cwd(cwd):
        # DUH.md / CLAUDE.md in directory root (cross-tool compatibility)
        for name in ["DUH.md", str(Path(".duh") / "DUH.md"), "CLAUDE.md"]:
            md_path = dir_path / name
            if md_path.exists():
                instructions.extend(_load_file_with_includes(md_path, processed))

        # .duh/rules/*.md and .claude/rules/*.md (cross-tool compatibility)
        for rules_name in [Path(".duh") / "rules", Path(".claude") / "rules"]:
            rules_dir = dir_path / rules_name
            if rules_dir.is_dir():
                for md_file in sorted(rules_dir.glob("*.md")):
                    instructions.extend(_load_file_with_includes(md_file, processed))

        # AGENTS.md (open standard)
        agents_md = dir_path / "AGENTS.md"
        if agents_md.exists():
            instructions.extend(_load_file_with_includes(agents_md, processed))

    return instructions
