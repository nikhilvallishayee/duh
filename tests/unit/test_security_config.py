"""Tests for SecurityPolicy, dual-config loader, precedence, mode presets."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from duh.security.config import (
    CIConfig,
    RuntimeConfig,
    ScannerConfig,
    SecurityPolicy,
    load_policy,
)
from duh.security.finding import Severity


def test_default_policy_is_strict_mode() -> None:
    p = SecurityPolicy()
    assert p.mode == "strict"
    assert Severity.CRITICAL in p.fail_on
    assert Severity.HIGH in p.fail_on
    assert Severity.MEDIUM not in p.fail_on


def test_advisory_mode_preset_never_blocks() -> None:
    p = SecurityPolicy(mode="advisory")
    assert p.fail_on == ()
    assert p.runtime.block_pre_tool_use is False


def test_paranoid_mode_includes_medium() -> None:
    p = SecurityPolicy(mode="paranoid")
    assert Severity.MEDIUM in p.fail_on
    assert p.on_scanner_error == "fail"


def test_policy_is_frozen() -> None:
    p = SecurityPolicy()
    with pytest.raises(Exception):
        p.mode = "paranoid"  # type: ignore[misc]


def test_extra_keys_forbidden() -> None:
    with pytest.raises(Exception):
        SecurityPolicy(unknown_field="x")  # type: ignore[call-arg]


def test_scanner_config_defaults() -> None:
    sc = ScannerConfig()
    assert sc.enabled in (True, False, "auto")
    assert sc.args == ()


def test_load_policy_from_json(tmp_path: Path) -> None:
    cfg_dir = tmp_path / ".duh"
    cfg_dir.mkdir()
    (cfg_dir / "security.json").write_text(json.dumps({
        "version": 1,
        "mode": "paranoid",
        "allow_network": False,
        "scanners": {"ruff-sec": {"enabled": True}},
    }))
    p = load_policy(project_root=tmp_path)
    assert p.mode == "paranoid"
    assert p.allow_network is False
    assert p.scanners["ruff-sec"].enabled is True


def test_load_policy_from_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.duh.security]\n"
        "mode = \"advisory\"\n"
        "allow_network = true\n"
    )
    p = load_policy(project_root=tmp_path)
    assert p.mode == "advisory"
    assert p.allow_network is True


def test_json_overrides_pyproject(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[tool.duh.security]\nmode = \"advisory\"\n"
    )
    cfg_dir = tmp_path / ".duh"
    cfg_dir.mkdir()
    (cfg_dir / "security.json").write_text(json.dumps({"version": 1, "mode": "paranoid"}))
    p = load_policy(project_root=tmp_path)
    assert p.mode == "paranoid"


def test_load_policy_missing_files_returns_default(tmp_path: Path) -> None:
    p = load_policy(project_root=tmp_path)
    assert p.mode == "strict"


def test_runtime_config_defaults() -> None:
    rc = RuntimeConfig()
    assert rc.enabled is True
    assert rc.resolver_timeout_s == pytest.approx(5.0)
    assert rc.fail_open_on_timeout is True


def test_ci_config_defaults() -> None:
    c = CIConfig()
    assert c.generate_github_actions is False
    assert c.template == "standard"
