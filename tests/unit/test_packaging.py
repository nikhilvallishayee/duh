"""Tests for packaging: importability, version, CLI entry point."""

from __future__ import annotations

import importlib
import subprocess
import sys


class TestPackageImport:
    """Verify the package is importable and has expected attributes."""

    def test_import_duh(self):
        import duh
        assert hasattr(duh, "__version__")

    def test_version_is_string(self):
        import duh
        assert isinstance(duh.__version__, str)

    def test_version_is_pep440(self):
        """Version string should be a valid PEP 440 version."""
        import duh
        from packaging.version import Version
        v = Version(duh.__version__)
        assert v.major >= 0

    def test_import_cli_main(self):
        from duh.cli.main import main
        assert callable(main)

    def test_import_cli_package(self):
        from duh.cli import main
        assert callable(main)


class TestCLIEntryPoint:
    """Verify the installed 'duh' console script works."""

    def test_duh_version(self):
        """'duh --version' should print the version and exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "duh", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        import duh
        assert duh.__version__ in result.stdout

    def test_duh_help(self):
        """'duh --help' should print usage and exit 0."""
        result = subprocess.run(
            [sys.executable, "-m", "duh", "--help"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert "duh" in result.stdout.lower()
        assert "--prompt" in result.stdout

    def test_entry_point_registered(self):
        """The duh console_script entry point should be discoverable."""
        from importlib.metadata import distribution
        dist = distribution("duh-cli")
        eps = [ep for ep in dist.entry_points if ep.group == "console_scripts" and ep.name == "duh"]
        assert len(eps) == 1
        assert "duh.cli.main:main" in eps[0].value
