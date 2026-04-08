"""Tests for sandbox policy abstraction."""

import sys
from unittest.mock import patch

import pytest

from duh.adapters.sandbox.policy import (
    SandboxCommand,
    SandboxPolicy,
    SandboxType,
    detect_sandbox_type,
)


class TestSandboxType:
    def test_none_exists(self):
        assert SandboxType.NONE.value == "none"

    def test_seatbelt_exists(self):
        assert SandboxType.MACOS_SEATBELT.value == "macos_seatbelt"

    def test_landlock_exists(self):
        assert SandboxType.LINUX_LANDLOCK.value == "linux_landlock"


class TestSandboxPolicy:
    def test_defaults(self):
        policy = SandboxPolicy()
        assert policy.writable_paths == []
        assert policy.readable_paths == []
        assert policy.network_allowed is True
        assert policy.env_vars == {}

    def test_custom_paths(self):
        policy = SandboxPolicy(
            writable_paths=["/tmp", "/home/user/.duh"],
            readable_paths=["/usr", "/etc"],
            network_allowed=False,
        )
        assert "/tmp" in policy.writable_paths
        assert policy.network_allowed is False

    def test_env_vars(self):
        policy = SandboxPolicy(env_vars={"HOME": "/home/user"})
        assert policy.env_vars["HOME"] == "/home/user"

    def test_is_dataclass(self):
        from dataclasses import fields
        f = fields(SandboxPolicy)
        names = {field.name for field in f}
        assert "writable_paths" in names
        assert "readable_paths" in names
        assert "network_allowed" in names
        assert "env_vars" in names


class TestDetectSandboxType:
    @patch("sys.platform", "darwin")
    @patch("shutil.which", return_value="/usr/bin/sandbox-exec")
    def test_darwin_with_sandbox_exec(self, mock_which):
        result = detect_sandbox_type()
        assert result == SandboxType.MACOS_SEATBELT

    @patch("sys.platform", "darwin")
    @patch("shutil.which", return_value=None)
    def test_darwin_without_sandbox_exec(self, mock_which):
        result = detect_sandbox_type()
        assert result == SandboxType.NONE

    @patch("sys.platform", "linux")
    @patch("duh.adapters.sandbox.policy._landlock_available", return_value=True)
    def test_linux_with_landlock(self, mock_ll):
        result = detect_sandbox_type()
        assert result == SandboxType.LINUX_LANDLOCK

    @patch("sys.platform", "linux")
    @patch("duh.adapters.sandbox.policy._landlock_available", return_value=False)
    def test_linux_without_landlock(self, mock_ll):
        result = detect_sandbox_type()
        assert result == SandboxType.NONE

    @patch("sys.platform", "win32")
    def test_windows_returns_none(self):
        result = detect_sandbox_type()
        assert result == SandboxType.NONE


class TestSandboxCommand:
    def test_none_type_returns_original(self):
        policy = SandboxPolicy()
        result = SandboxCommand.build(
            command="echo hello",
            policy=policy,
            sandbox_type=SandboxType.NONE,
        )
        assert result.command == "echo hello"
        assert result.profile_path is None
        assert result.argv == ["bash", "-c", "echo hello"]

    def test_build_returns_sandbox_command(self):
        policy = SandboxPolicy(writable_paths=["/tmp"])
        result = SandboxCommand.build(
            command="echo hello",
            policy=policy,
            sandbox_type=SandboxType.MACOS_SEATBELT,
        )
        assert result.command == "echo hello"
        assert result.profile_path is not None
        assert "sandbox-exec" in result.argv[0]

    def test_build_landlock_returns_wrapper(self):
        policy = SandboxPolicy(writable_paths=["/tmp"])
        result = SandboxCommand.build(
            command="echo hello",
            policy=policy,
            sandbox_type=SandboxType.LINUX_LANDLOCK,
        )
        # Landlock wraps via a helper script or env setup
        assert result.command == "echo hello"
        # The argv should contain the landlock wrapper
        assert len(result.argv) > 0
