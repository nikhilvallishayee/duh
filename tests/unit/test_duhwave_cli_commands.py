"""Tests for ``duh.duhwave.cli.commands`` handlers.

We isolate the daemon: no real socket, no real subprocess. ``ls`` and
``install``/``uninstall`` only touch on-disk state, so they run as-is.
Daemon-required handlers (``inspect``, ``pause``, ``resume``,
``logs``, ``web``) are exercised against a *missing* daemon to verify
the failure mode is clean.
"""

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import pytest

from duh.duhwave.bundle import BUNDLE_EXT, pack_bundle
from duh.duhwave.cli import commands


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_args(**kwargs) -> argparse.Namespace:
    return argparse.Namespace(**kwargs)


def _build_bundle(tmp_path: Path, *, name: str = "demo", version: str = "0.1.0") -> Path:
    src = tmp_path / f"src-{name}"
    src.mkdir()
    (src / "manifest.toml").write_text(
        f"""
[bundle]
name = "{name}"
version = "{version}"
description = "demo"
author = ""
format_version = 1
created_at = 1700000000.0

[signing]
signed = false
""".strip()
        + "\n"
    )
    (src / "swarm.toml").write_text(
        f"""
[swarm]
name = "{name}"
version = "{version}"
description = ""
format_version = 1

[[agents]]
id = "solo"
role = "researcher"
model = "sonnet"
""".strip()
        + "\n"
    )
    (src / "permissions.toml").write_text(
        """
[filesystem]
read = ["/repos/**"]

network = []
tools = []
""".strip()
        + "\n"
    )
    return pack_bundle(src, tmp_path / f"{name}-{version}{BUNDLE_EXT}")


# ---------------------------------------------------------------------------
# cmd_ls
# ---------------------------------------------------------------------------


class TestCmdLs:
    def test_no_installed_no_daemon(self, tmp_path: Path, capsys):
        waves_root = tmp_path / "waves"
        waves_root.mkdir()
        args = _make_args(waves_root=waves_root, json=False)
        rc = commands.cmd_ls(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "daemon: stopped" in out
        assert "no swarms installed" in out

    def test_with_one_installed_no_daemon(self, tmp_path: Path, capsys):
        bundle = _build_bundle(tmp_path)
        waves_root = tmp_path / "waves"
        from duh.duhwave.bundle import BundleInstaller

        BundleInstaller(root=waves_root).install(bundle, force=True)

        args = _make_args(waves_root=waves_root, json=False)
        rc = commands.cmd_ls(args)
        assert rc == 0
        out = capsys.readouterr().out
        assert "daemon: stopped" in out
        # Bundle name appears in the install list.
        assert "demo" in out
        assert "0.1.0" in out

    def test_json_output(self, tmp_path: Path, capsys):
        waves_root = tmp_path / "waves"
        waves_root.mkdir()
        args = _make_args(waves_root=waves_root, json=True)
        rc = commands.cmd_ls(args)
        assert rc == 0
        out = capsys.readouterr().out
        # Valid JSON with the expected top-level shape.
        import json

        obj = json.loads(out)
        assert obj["daemon_running"] is False
        assert obj["installed"] == []
        assert obj["tasks"] == []


# ---------------------------------------------------------------------------
# cmd_install / cmd_uninstall
# ---------------------------------------------------------------------------


class TestCmdInstallUninstall:
    def test_install_missing_path_returns_2(self, tmp_path: Path, capsys):
        args = _make_args(
            path=tmp_path / "ghost.duhwave",
            public_key=None,
            force=False,
            waves_root=tmp_path / "waves",
        )
        (tmp_path / "waves").mkdir()
        rc = commands.cmd_install(args)
        assert rc == 2
        err = capsys.readouterr().err
        assert "bundle not found" in err

    def test_install_then_uninstall_round_trip(self, tmp_path: Path, capsys):
        bundle = _build_bundle(tmp_path)
        waves_root = tmp_path / "waves"
        waves_root.mkdir()

        rc = commands.cmd_install(
            _make_args(
                path=bundle,
                public_key=None,
                force=True,
                waves_root=waves_root,
            )
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "installed: demo" in out

        rc = commands.cmd_uninstall(
            _make_args(name="demo", waves_root=waves_root)
        )
        assert rc == 0
        out = capsys.readouterr().out
        assert "uninstalled: demo" in out

    def test_uninstall_unknown_returns_1(self, tmp_path: Path, capsys):
        waves_root = tmp_path / "waves"
        waves_root.mkdir()
        rc = commands.cmd_uninstall(
            _make_args(name="ghost", waves_root=waves_root)
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "not installed" in err


# ---------------------------------------------------------------------------
# Daemon-required commands without a daemon
# ---------------------------------------------------------------------------


class TestNoDaemon:
    @pytest.fixture
    def waves_root(self, tmp_path: Path) -> Path:
        d = tmp_path / "waves"
        d.mkdir()
        return d

    def test_inspect_no_daemon(self, waves_root: Path, capsys):
        rc = commands.cmd_inspect(
            _make_args(waves_root=waves_root, swarm_id="x")
        )
        assert rc == 1
        err = capsys.readouterr().err
        assert "daemon not running" in err

    def test_pause_no_daemon(self, waves_root: Path, capsys):
        rc = commands.cmd_pause(
            _make_args(waves_root=waves_root, swarm_id="x")
        )
        assert rc == 1
        assert "daemon not running" in capsys.readouterr().err

    def test_resume_no_daemon(self, waves_root: Path, capsys):
        rc = commands.cmd_resume(
            _make_args(waves_root=waves_root, swarm_id="x")
        )
        assert rc == 1
        assert "daemon not running" in capsys.readouterr().err

    def test_logs_no_daemon(self, waves_root: Path, capsys):
        rc = commands.cmd_logs(
            _make_args(
                waves_root=waves_root,
                swarm_id="x",
                follow=False,
                lines=10,
            )
        )
        assert rc == 1
        assert "daemon not running" in capsys.readouterr().err

    def test_web_no_daemon(self, waves_root: Path, capsys):
        rc = commands.cmd_web(
            _make_args(waves_root=waves_root, port=8729)
        )
        assert rc == 1
        assert "daemon not running" in capsys.readouterr().err
