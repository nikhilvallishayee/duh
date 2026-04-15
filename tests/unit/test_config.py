"""Tests for configuration system (ADR-015)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from duh.config import (
    Config,
    config_dir,
    load_config,
    load_instructions,
    _find_git_root,
    _find_project_config,
    _dirs_root_to_cwd,
    _load_json,
    _merge_into,
    _apply_env,
)


# ---------------------------------------------------------------------------
# Config data class
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults(self):
        c = Config()
        assert c.model == ""
        assert c.provider == ""
        assert c.max_turns == 100
        assert c.permissions == {}
        assert c.hooks == {}
        assert c.mcp_servers == {}


# ---------------------------------------------------------------------------
# config_dir
# ---------------------------------------------------------------------------


class TestConfigDir:
    def test_default_is_home_config(self):
        with patch.dict(os.environ, {}, clear=False):
            # Remove XDG_CONFIG_HOME if set
            env = dict(os.environ)
            env.pop("XDG_CONFIG_HOME", None)
            with patch.dict(os.environ, env, clear=True):
                d = config_dir()
                assert d == Path.home() / ".config" / "duh"

    def test_respects_xdg(self):
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": "/tmp/xdg"}):
            d = config_dir()
            assert d == Path("/tmp/xdg/duh")


# ---------------------------------------------------------------------------
# _load_json
# ---------------------------------------------------------------------------


class TestLoadJson:
    def test_loads_valid_json(self, tmp_path: Path):
        f = tmp_path / "test.json"
        f.write_text('{"model": "opus"}')
        data = _load_json(f)
        assert data == {"model": "opus"}

    def test_returns_empty_for_missing_file(self, tmp_path: Path):
        data = _load_json(tmp_path / "nope.json")
        assert data == {}

    def test_returns_empty_for_invalid_json(self, tmp_path: Path):
        f = tmp_path / "bad.json"
        f.write_text("not json")
        data = _load_json(f)
        assert data == {}

    def test_returns_empty_for_non_object(self, tmp_path: Path):
        f = tmp_path / "array.json"
        f.write_text("[1, 2, 3]")
        data = _load_json(f)
        assert data == {}


# ---------------------------------------------------------------------------
# _merge_into
# ---------------------------------------------------------------------------


class TestMergeInto:
    def test_merges_model(self):
        c = Config()
        _merge_into(c, {"model": "opus"})
        assert c.model == "opus"

    def test_merges_provider(self):
        c = Config()
        _merge_into(c, {"provider": "ollama"})
        assert c.provider == "ollama"

    def test_merges_max_turns(self):
        c = Config()
        _merge_into(c, {"max_turns": 50})
        assert c.max_turns == 50

    def test_merges_permissions(self):
        c = Config()
        _merge_into(c, {"permissions": {"allow": [{"tool": "Read"}]}})
        assert "allow" in c.permissions

    def test_merges_hooks(self):
        c = Config()
        _merge_into(c, {"hooks": {"PreToolUse": []}})
        assert "PreToolUse" in c.hooks

    def test_merges_mcp_servers(self):
        c = Config()
        _merge_into(c, {"mcpServers": {"fs": {"command": "npx"}}})
        assert "fs" in c.mcp_servers

    def test_skips_empty_model(self):
        c = Config(model="existing")
        _merge_into(c, {"model": ""})
        assert c.model == "existing"

    def test_invalid_max_turns_ignored(self):
        c = Config(max_turns=10)
        _merge_into(c, {"max_turns": "not_a_number"})
        assert c.max_turns == 10


# ---------------------------------------------------------------------------
# _apply_env
# ---------------------------------------------------------------------------


class TestApplyEnv:
    def test_applies_duh_model(self):
        c = Config()
        with patch.dict(os.environ, {"DUH_MODEL": "haiku"}):
            _apply_env(c)
        assert c.model == "haiku"

    def test_applies_duh_provider(self):
        c = Config()
        with patch.dict(os.environ, {"DUH_PROVIDER": "ollama"}):
            _apply_env(c)
        assert c.provider == "ollama"

    def test_applies_duh_max_turns(self):
        c = Config()
        with patch.dict(os.environ, {"DUH_MAX_TURNS": "99"}):
            _apply_env(c)
        assert c.max_turns == 99

    def test_no_env_no_change(self):
        c = Config(model="original")
        with patch.dict(os.environ, {}, clear=True):
            _apply_env(c)
        assert c.model == "original"


# ---------------------------------------------------------------------------
# _find_project_config
# ---------------------------------------------------------------------------


class TestFindProjectConfig:
    def test_finds_config_in_current_dir(self, tmp_path: Path):
        duh_dir = tmp_path / ".duh"
        duh_dir.mkdir()
        settings = duh_dir / "settings.json"
        settings.write_text("{}")
        result = _find_project_config(str(tmp_path))
        assert result is not None
        assert result.name == "settings.json"

    def test_finds_config_in_parent(self, tmp_path: Path):
        duh_dir = tmp_path / ".duh"
        duh_dir.mkdir()
        (duh_dir / "settings.json").write_text("{}")
        child = tmp_path / "sub" / "dir"
        child.mkdir(parents=True)
        result = _find_project_config(str(child))
        assert result is not None

    def test_returns_none_when_no_config(self, tmp_path: Path):
        result = _find_project_config(str(tmp_path))
        assert result is None


# ---------------------------------------------------------------------------
# _find_git_root
# ---------------------------------------------------------------------------


class TestFindGitRoot:
    def test_finds_git_root(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        child = tmp_path / "src" / "pkg"
        child.mkdir(parents=True)
        root = _find_git_root(str(child))
        assert root == tmp_path

    def test_returns_none_when_no_git(self, tmp_path: Path):
        root = _find_git_root(str(tmp_path))
        assert root is None


# ---------------------------------------------------------------------------
# load_config (integration)
# ---------------------------------------------------------------------------


class TestLoadConfig:
    def test_empty_config_has_defaults(self):
        with patch("duh.config.config_dir", return_value=Path("/nonexistent")):
            c = load_config(cwd="/nonexistent")
        assert c.model == ""
        assert c.max_turns == 100

    def test_cli_args_override_everything(self, tmp_path: Path):
        # Write user config
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "settings.json").write_text('{"model": "from-user"}')

        with patch("duh.config.config_dir", return_value=user_dir):
            c = load_config(
                cli_args={"model": "from-cli"},
                cwd=str(tmp_path),
            )
        assert c.model == "from-cli"

    def test_user_config_loaded(self, tmp_path: Path):
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "settings.json").write_text('{"model": "from-user"}')

        with patch("duh.config.config_dir", return_value=user_dir):
            c = load_config(cwd=str(tmp_path))
        assert c.model == "from-user"

    def test_project_config_overrides_user(self, tmp_path: Path):
        # User config
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "settings.json").write_text('{"model": "user-model"}')

        # Project config
        duh_dir = tmp_path / "project" / ".duh"
        duh_dir.mkdir(parents=True)
        (duh_dir / "settings.json").write_text('{"model": "project-model"}')

        with patch("duh.config.config_dir", return_value=user_dir):
            c = load_config(cwd=str(tmp_path / "project"))
        assert c.model == "project-model"

    def test_env_overrides_project(self, tmp_path: Path):
        duh_dir = tmp_path / ".duh"
        duh_dir.mkdir()
        (duh_dir / "settings.json").write_text('{"model": "project-model"}')

        with patch("duh.config.config_dir", return_value=Path("/nonexistent")):
            with patch.dict(os.environ, {"DUH_MODEL": "env-model"}):
                c = load_config(cwd=str(tmp_path))
        assert c.model == "env-model"


# ---------------------------------------------------------------------------
# load_instructions
# ---------------------------------------------------------------------------


class TestLoadInstructions:
    def test_no_files_returns_empty(self, tmp_path: Path):
        with patch("duh.config.config_dir", return_value=tmp_path / "nope"):
            result = load_instructions(cwd=str(tmp_path))
        assert result == []

    def test_loads_duh_md(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "DUH.md").write_text("# Project instructions")

        with patch("duh.config.config_dir", return_value=tmp_path / "nope"):
            result = load_instructions(cwd=str(tmp_path))
        assert len(result) == 1
        assert "Project instructions" in result[0]

    def test_loads_dot_duh_duh_md(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        duh_dir = tmp_path / ".duh"
        duh_dir.mkdir()
        (duh_dir / "DUH.md").write_text("# Dot-duh instructions")

        with patch("duh.config.config_dir", return_value=tmp_path / "nope"):
            result = load_instructions(cwd=str(tmp_path))
        assert any("Dot-duh instructions" in s for s in result)

    def test_loads_agents_md(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "AGENTS.md").write_text("# Agent instructions")

        with patch("duh.config.config_dir", return_value=tmp_path / "nope"):
            result = load_instructions(cwd=str(tmp_path))
        assert any("Agent instructions" in s for s in result)

    def test_loads_rules_directory(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        rules = tmp_path / ".duh" / "rules"
        rules.mkdir(parents=True)
        (rules / "rule-01.md").write_text("Rule one")
        (rules / "rule-02.md").write_text("Rule two")

        with patch("duh.config.config_dir", return_value=tmp_path / "nope"):
            result = load_instructions(cwd=str(tmp_path))
        texts = "\n".join(result)
        assert "Rule one" in texts
        assert "Rule two" in texts

    def test_user_global_loaded_first(self, tmp_path: Path):
        user_dir = tmp_path / "user"
        user_dir.mkdir()
        (user_dir / "DUH.md").write_text("User global")

        project = tmp_path / "project"
        project.mkdir()
        (project / ".git").mkdir()
        (project / "DUH.md").write_text("Project local")

        with patch("duh.config.config_dir", return_value=user_dir):
            result = load_instructions(cwd=str(project))

        assert result[0] == "User global"  # user first
        assert "Project local" in result[-1]  # project last (highest priority)

    def test_both_duh_md_and_agents_md(self, tmp_path: Path):
        (tmp_path / ".git").mkdir()
        (tmp_path / "DUH.md").write_text("DUH instructions")
        (tmp_path / "AGENTS.md").write_text("AGENTS instructions")

        with patch("duh.config.config_dir", return_value=tmp_path / "nope"):
            result = load_instructions(cwd=str(tmp_path))
        assert len(result) >= 2
        # DUH.md should come before AGENTS.md
        duh_idx = next(i for i, s in enumerate(result) if "DUH instructions" in s)
        agents_idx = next(i for i, s in enumerate(result) if "AGENTS instructions" in s)
        assert duh_idx < agents_idx


# ---------------------------------------------------------------------------
# Task 7.3.7: trifecta_acknowledged in Config + .duh/security.json
# ---------------------------------------------------------------------------


class TestTrifectaAcknowledged:
    def test_config_trifecta_acknowledged_defaults_false(self):
        with patch("duh.config.config_dir", return_value=Path("/nonexistent")):
            cfg = load_config(cwd="/nonexistent")
        assert cfg.trifecta_acknowledged is False

    def test_config_trifecta_acknowledged_from_security_json(self, tmp_path: Path):
        # Create project .duh dir with both settings.json and security.json
        duh_dir = tmp_path / ".duh"
        duh_dir.mkdir(parents=True)
        (duh_dir / "settings.json").write_text("{}")
        (duh_dir / "security.json").write_text(
            json.dumps({"trifecta_acknowledged": True})
        )
        with patch("duh.config.config_dir", return_value=Path("/nonexistent")):
            cfg = load_config(cwd=str(tmp_path))
        assert cfg.trifecta_acknowledged is True

    def test_config_trifecta_stays_false_without_security_json(self, tmp_path: Path):
        duh_dir = tmp_path / ".duh"
        duh_dir.mkdir(parents=True)
        (duh_dir / "settings.json").write_text("{}")
        with patch("duh.config.config_dir", return_value=Path("/nonexistent")):
            cfg = load_config(cwd=str(tmp_path))
        assert cfg.trifecta_acknowledged is False
