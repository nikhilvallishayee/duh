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
    max_turns: int = 10
    system_prompt: str = ""
    permissions: dict[str, Any] = field(default_factory=dict)
    hooks: dict[str, Any] = field(default_factory=dict)
    mcp_servers: dict[str, Any] = field(default_factory=dict)


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
    if "system_prompt" in data and data["system_prompt"]:
        config.system_prompt = str(data["system_prompt"])
    if "permissions" in data and isinstance(data["permissions"], dict):
        config.permissions.update(data["permissions"])
    if "hooks" in data and isinstance(data["hooks"], dict):
        config.hooks.update(data["hooks"])
    if "mcpServers" in data and isinstance(data["mcpServers"], dict):
        config.mcp_servers.update(data["mcpServers"])


# ---------------------------------------------------------------------------
# Environment variable mapping
# ---------------------------------------------------------------------------

_ENV_MAP: dict[str, str] = {
    "DUH_MODEL": "model",
    "DUH_PROVIDER": "provider",
    "DUH_MAX_TURNS": "max_turns",
    "DUH_SYSTEM_PROMPT": "system_prompt",
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


def load_instructions(cwd: str = ".") -> list[str]:
    """Load all DUH.md and AGENTS.md instruction files.

    Files are loaded in precedence order (lowest first, highest last).
    The model pays more attention to content appearing later.

    Order:
        1. ~/.config/duh/DUH.md (user-global)
        2. DUH.md / .duh/DUH.md per directory (git root to cwd)
        3. .duh/rules/*.md per directory
        4. AGENTS.md per directory (open standard)

    Args:
        cwd: Current working directory.

    Returns:
        List of instruction strings, ordered by precedence.
    """
    instructions: list[str] = []

    # User-global instructions
    user_duh = config_dir() / "DUH.md"
    if user_duh.exists():
        try:
            instructions.append(user_duh.read_text(encoding="utf-8"))
        except Exception as exc:
            logger.warning("Failed to read %s: %s", user_duh, exc)

    # Project instructions (root to cwd)
    for dir_path in _dirs_root_to_cwd(cwd):
        # DUH.md in directory root
        for name in ["DUH.md", str(Path(".duh") / "DUH.md")]:
            md_path = dir_path / name
            if md_path.exists():
                try:
                    instructions.append(md_path.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.warning("Failed to read %s: %s", md_path, exc)

        # .duh/rules/*.md
        rules_dir = dir_path / ".duh" / "rules"
        if rules_dir.is_dir():
            for md_file in sorted(rules_dir.glob("*.md")):
                try:
                    instructions.append(md_file.read_text(encoding="utf-8"))
                except Exception as exc:
                    logger.warning("Failed to read %s: %s", md_file, exc)

        # AGENTS.md (open standard)
        agents_md = dir_path / "AGENTS.md"
        if agents_md.exists():
            try:
                instructions.append(agents_md.read_text(encoding="utf-8"))
            except Exception as exc:
                logger.warning("Failed to read %s: %s", agents_md, exc)

    return instructions
