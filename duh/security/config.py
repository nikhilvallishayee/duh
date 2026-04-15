"""Pydantic SecurityPolicy and dual-config loader.

Precedence (highest first):
  1. CLI flags (applied by caller)
  2. .duh/security.json (project-local)
  3. [tool.duh.security] in pyproject.toml
  4. ~/.config/duh/security.json (user defaults)
  5. Built-in defaults
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from duh.security.finding import Severity


class ScannerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="allow")

    enabled: bool | Literal["auto"] = True
    args: tuple[str, ...] = ()


class RuntimeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    enabled: bool = True
    block_pre_tool_use: bool = True
    rescan_on_dep_change: bool = True
    session_start_audit: bool = True
    session_end_summary: bool = True
    resolver_timeout_s: float = Field(default=5.0, gt=0.0, le=60.0)
    fail_open_on_timeout: bool = True


class CIConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    generate_github_actions: bool = False
    template: Literal["minimal", "standard", "paranoid"] = "standard"


_MODE_PRESETS: dict[str, dict[str, Any]] = {
    "advisory": {
        "fail_on": (),
        "report_on": (Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL),
        "on_scanner_error": "warn",
        "runtime_block_pre_tool_use": False,
    },
    "strict": {
        "fail_on": (Severity.CRITICAL, Severity.HIGH),
        "report_on": (Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL),
        "on_scanner_error": "continue",
        "runtime_block_pre_tool_use": True,
    },
    "paranoid": {
        "fail_on": (Severity.CRITICAL, Severity.HIGH, Severity.MEDIUM),
        "report_on": (Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL),
        "on_scanner_error": "fail",
        "runtime_block_pre_tool_use": True,
    },
}


class SecurityPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    mode: Literal["advisory", "strict", "paranoid"] = "strict"
    fail_on: tuple[Severity, ...] | None = None
    report_on: tuple[Severity, ...] | None = None
    block_on_new_only: bool = True
    on_scanner_error: Literal["continue", "warn", "fail"] | None = None
    max_db_staleness_days: int = Field(default=7, ge=1, le=90)
    allow_network: bool = True
    exceptions_file: Path = Path(".duh/security-exceptions.json")
    cache_file: Path = Path(".duh/security-cache.json")

    trifecta_acknowledged: bool = False

    scanners: dict[str, ScannerConfig] = Field(default_factory=dict)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    ci: CIConfig = Field(default_factory=CIConfig)

    @model_validator(mode="after")
    def _apply_mode_preset(self) -> "SecurityPolicy":
        preset = _MODE_PRESETS[self.mode]
        # Apply preset values where the user did not override.
        changes: dict[str, Any] = {}
        if self.fail_on is None:
            changes["fail_on"] = preset["fail_on"]
        if self.report_on is None:
            changes["report_on"] = preset["report_on"]
        if self.on_scanner_error is None:
            changes["on_scanner_error"] = preset["on_scanner_error"]
        if not changes:
            return self
        # Bypass frozen semantics during post-init normalization.
        for k, v in changes.items():
            object.__setattr__(self, k, v)
        # Reconcile runtime.block_pre_tool_use with preset when it is the default.
        if self.runtime.block_pre_tool_use is True and not preset["runtime_block_pre_tool_use"]:
            object.__setattr__(
                self,
                "runtime",
                self.runtime.model_copy(update={"block_pre_tool_use": False}),
            )
        return self


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_pyproject(path: Path) -> dict[str, Any]:
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return data.get("tool", {}).get("duh", {}).get("security", {})


def _merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load_policy(
    *,
    project_root: Path,
    user_config_dir: Path | None = None,
) -> SecurityPolicy:
    """Load a SecurityPolicy using the full precedence chain."""
    merged: dict[str, Any] = {}

    if user_config_dir is not None:
        user_cfg = user_config_dir / "duh" / "security.json"
        if user_cfg.exists():
            merged = _merge(merged, _read_json(user_cfg))

    pyproject = project_root / "pyproject.toml"
    if pyproject.exists():
        merged = _merge(merged, _read_pyproject(pyproject))

    project_cfg = project_root / ".duh" / "security.json"
    if project_cfg.exists():
        merged = _merge(merged, _read_json(project_cfg))

    return SecurityPolicy.model_validate(merged)
