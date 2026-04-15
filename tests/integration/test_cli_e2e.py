"""End-to-end CLI tests — invoke duh via subprocess."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

PYTHON = sys.executable
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


def run_duh(
    *args: str,
    timeout: int = 15,
    stub_provider: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Run `python -m duh <args>` and return the result.

    By default the stub provider is enabled so subprocess tests don't need
    real API credentials. Pass ``stub_provider=False`` to opt out.
    """
    env = os.environ.copy()
    if stub_provider:
        env["DUH_STUB_PROVIDER"] = "1"
    return subprocess.run(
        [PYTHON, "-m", "duh", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
        env=env,
    )


class TestVersionE2E:
    def test_version_output(self):
        result = run_duh("--version")
        assert result.returncode == 0
        assert "0.4.2" in result.stdout


class TestHelpE2E:
    def test_help_output(self):
        result = run_duh("--help")
        assert result.returncode == 0
        assert "--prompt" in result.stdout
        assert "duh" in result.stdout.lower()

    def test_no_args_enters_repl(self):
        """No args should start REPL (which reads stdin, gets EOF, exits)."""
        result = run_duh(timeout=5)
        assert result.returncode == 0
        assert "interactive mode" in result.stdout.lower() or "duh>" in result.stdout


class TestDoctorE2E:
    def test_doctor_runs(self):
        result = run_duh("doctor")
        assert "Python version" in result.stdout
        assert "ANTHROPIC_API_KEY" in result.stdout
        assert "Tools available" in result.stdout
