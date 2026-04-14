"""Smoke test for the duh.security package skeleton."""

from __future__ import annotations

import importlib


def test_security_package_imports() -> None:
    mod = importlib.import_module("duh.security")
    assert mod.__name__ == "duh.security"


def test_security_package_exposes_version_marker() -> None:
    mod = importlib.import_module("duh.security")
    assert hasattr(mod, "__version__")
    assert isinstance(mod.__version__, str)
    assert mod.__version__ != ""
