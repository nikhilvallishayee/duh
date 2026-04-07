"""End-to-end CLI tests — invoke duh via subprocess."""

from __future__ import annotations

import subprocess
import sys

import pytest

PYTHON = sys.executable


def run_duh(*args: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    """Run `python -m duh <args>` and return the result."""
    return subprocess.run(
        [PYTHON, "-m", "duh", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd="/Users/nomind/Code/duh",
    )


class TestVersionE2E:
    def test_version_output(self):
        result = run_duh("--version")
        assert result.returncode == 0
        assert "0.1.0" in result.stdout


class TestHelpE2E:
    def test_help_output(self):
        result = run_duh("--help")
        assert result.returncode == 0
        assert "--prompt" in result.stdout
        assert "duh" in result.stdout.lower()

    def test_no_args_shows_help(self):
        result = run_duh()
        assert result.returncode == 0
        assert "--prompt" in result.stdout


class TestDoctorE2E:
    def test_doctor_runs(self):
        result = run_duh("doctor")
        assert "Python version" in result.stdout
        assert "ANTHROPIC_API_KEY" in result.stdout
        assert "Tools available" in result.stdout
