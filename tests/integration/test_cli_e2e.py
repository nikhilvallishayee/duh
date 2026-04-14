"""End-to-end CLI tests — invoke duh via subprocess."""

from __future__ import annotations

import os
import pathlib
import subprocess
import sys

import pytest

PYTHON = sys.executable
PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[2]


def _has_provider() -> bool:
    """Return True if any provider is configured for subprocess tests.

    Until PR #2 lands a stub provider, this only checks env vars; tests
    skip cleanly otherwise so CI doesn't require API credentials.
    """
    return bool(
        os.environ.get("DUH_STUB_PROVIDER") == "1"
        or os.environ.get("ANTHROPIC_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )


def run_duh(*args: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    """Run `python -m duh <args>` and return the result."""
    return subprocess.run(
        [PYTHON, "-m", "duh", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
    )


class TestVersionE2E:
    def test_version_output(self):
        result = run_duh("--version")
        assert result.returncode == 0
        assert "0.3.0" in result.stdout


class TestHelpE2E:
    def test_help_output(self):
        result = run_duh("--help")
        assert result.returncode == 0
        assert "--prompt" in result.stdout
        assert "duh" in result.stdout.lower()

    def test_no_args_enters_repl(self):
        """No args should start REPL (which reads stdin, gets EOF, exits).

        Skips when no provider is configured (the REPL refuses to start
        without one).
        """
        if not _has_provider():
            pytest.skip("no provider configured (set DUH_STUB_PROVIDER or ANTHROPIC_API_KEY)")
        result = run_duh(timeout=5)
        assert result.returncode == 0
        assert "interactive mode" in result.stdout.lower() or "duh>" in result.stdout


class TestDoctorE2E:
    def test_doctor_runs(self):
        result = run_duh("doctor")
        assert "Python version" in result.stdout
        assert "ANTHROPIC_API_KEY" in result.stdout
        assert "Tools available" in result.stdout
