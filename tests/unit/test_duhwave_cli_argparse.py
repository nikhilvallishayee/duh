"""Tests for ``duh.duhwave.cli.entrypoint`` argument parsing.

These exercise *only* the parser surface — handlers are tested in
``test_duhwave_cli_commands.py``. The point here is to lock in the
ten subcommands and the ``--waves-root`` flow without spinning up
any daemon or filesystem state.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from duh.duhwave.cli import entrypoint


# Every subcommand and the minimal argv that makes it parseable.
_SUBCOMMANDS: list[tuple[str, list[str]]] = [
    ("start", ["start"]),
    ("start_named", ["start", "myswarm"]),
    ("start_foreground", ["start", "--foreground"]),
    ("stop", ["stop"]),
    ("ls", ["ls"]),
    ("ls_json", ["ls", "--json"]),
    ("inspect", ["inspect", "swarm-1"]),
    ("pause", ["pause", "swarm-1"]),
    ("resume", ["resume", "swarm-1"]),
    ("logs", ["logs", "swarm-1"]),
    ("logs_follow", ["logs", "swarm-1", "--follow", "--lines", "50"]),
    ("install", ["install", "/tmp/whatever.duhwave"]),
    ("install_force", ["install", "/tmp/x.duhwave", "--force"]),
    ("install_pubkey", ["install", "/tmp/x.duhwave", "--public-key", "/tmp/k.pub"]),
    ("uninstall", ["uninstall", "demo"]),
    ("web", ["web"]),
    ("web_port", ["web", "--port", "9999"]),
]


# ---------------------------------------------------------------------------
# Subcommand parsing surface
# ---------------------------------------------------------------------------


class TestSubcommandParsing:
    @pytest.mark.parametrize("label,argv", _SUBCOMMANDS, ids=[s[0] for s in _SUBCOMMANDS])
    def test_subcommand_parses_cleanly(self, label: str, argv: list[str]):
        parser = entrypoint._build_parser()
        ns = parser.parse_args(argv)
        # Every successful parse populates `cmd`.
        assert hasattr(ns, "cmd")
        assert ns.cmd in {
            "start",
            "stop",
            "ls",
            "inspect",
            "pause",
            "resume",
            "logs",
            "install",
            "uninstall",
            "web",
        }

    def test_unknown_subcommand_exits_2(self):
        parser = entrypoint._build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["bogus-cmd"])
        # argparse uses exit code 2 for usage errors.
        assert exc.value.code == 2

    def test_no_subcommand_exits_2(self):
        parser = entrypoint._build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args([])
        assert exc.value.code == 2


# ---------------------------------------------------------------------------
# --help on each subcommand
# ---------------------------------------------------------------------------


class TestHelp:
    @pytest.mark.parametrize(
        "subcmd",
        [
            "start",
            "stop",
            "ls",
            "inspect",
            "pause",
            "resume",
            "logs",
            "install",
            "uninstall",
            "web",
        ],
    )
    def test_subcommand_help_exits_zero(self, subcmd: str):
        parser = entrypoint._build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args([subcmd, "--help"])
        assert exc.value.code == 0

    def test_top_level_help_exits_zero(self):
        parser = entrypoint._build_parser()
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(["--help"])
        assert exc.value.code == 0


# ---------------------------------------------------------------------------
# --waves-root flow-through
# ---------------------------------------------------------------------------


class TestWavesRoot:
    def test_default_waves_root_is_home_path(self):
        parser = entrypoint._build_parser()
        ns = parser.parse_args(["ls"])
        assert isinstance(ns.waves_root, Path)
        # Default is ~/.duh/waves; we don't assume the exact home but
        # we do require the standard tail.
        assert ns.waves_root.parts[-2:] == (".duh", "waves")

    def test_explicit_waves_root_flows_through(self, tmp_path: Path):
        parser = entrypoint._build_parser()
        ns = parser.parse_args(
            ["--waves-root", str(tmp_path / "alt"), "ls"]
        )
        assert ns.waves_root == tmp_path / "alt"


# ---------------------------------------------------------------------------
# Field plumbing — verifies handlers will see the values argparse parsed
# ---------------------------------------------------------------------------


class TestFieldPlumbing:
    def test_install_args_have_expected_fields(self, tmp_path: Path):
        parser = entrypoint._build_parser()
        ns = parser.parse_args(
            [
                "install",
                str(tmp_path / "demo.duhwave"),
                "--force",
                "--public-key",
                str(tmp_path / "k.pub"),
            ]
        )
        assert ns.cmd == "install"
        assert ns.path == tmp_path / "demo.duhwave"
        assert ns.force is True
        assert ns.public_key == tmp_path / "k.pub"

    def test_logs_args_have_expected_fields(self):
        parser = entrypoint._build_parser()
        ns = parser.parse_args(["logs", "wave-x", "-f", "-n", "42"])
        assert ns.cmd == "logs"
        assert ns.swarm_id == "wave-x"
        assert ns.follow is True
        assert ns.lines == 42
