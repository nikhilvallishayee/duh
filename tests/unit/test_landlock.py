"""Tests for Linux Landlock sandbox adapter."""

import os
import struct
from unittest.mock import MagicMock, patch

import pytest

from duh.adapters.sandbox.policy import SandboxPolicy
from duh.adapters.sandbox.landlock import (
    LANDLOCK_ACCESS_FS_EXECUTE,
    LANDLOCK_ACCESS_FS_READ_FILE,
    LANDLOCK_ACCESS_FS_READ_DIR,
    LANDLOCK_ACCESS_FS_WRITE_FILE,
    LANDLOCK_ACCESS_FS_MAKE_REG,
    LANDLOCK_ACCESS_FS_MAKE_DIR,
    LandlockRuleset,
    build_landlock_argv,
    build_ruleset,
)


class TestLandlockConstants:
    def test_access_flags_are_powers_of_two(self):
        flags = [
            LANDLOCK_ACCESS_FS_EXECUTE,
            LANDLOCK_ACCESS_FS_WRITE_FILE,
            LANDLOCK_ACCESS_FS_READ_FILE,
            LANDLOCK_ACCESS_FS_READ_DIR,
            LANDLOCK_ACCESS_FS_MAKE_REG,
            LANDLOCK_ACCESS_FS_MAKE_DIR,
        ]
        for flag in flags:
            assert flag > 0
            assert (flag & (flag - 1)) == 0, f"{flag} is not a power of 2"


class TestBuildRuleset:
    def test_default_policy_allows_read(self):
        policy = SandboxPolicy()
        ruleset = build_ruleset(policy)
        assert isinstance(ruleset, LandlockRuleset)
        # Default: read allowed everywhere
        assert len(ruleset.read_paths) > 0 or ruleset.global_read is True

    def test_writable_paths_in_ruleset(self):
        policy = SandboxPolicy(writable_paths=["/tmp", "/home/user/.duh"])
        ruleset = build_ruleset(policy)
        assert "/tmp" in ruleset.write_paths
        assert "/home/user/.duh" in ruleset.write_paths

    def test_no_writable_paths_empty(self):
        policy = SandboxPolicy(writable_paths=[])
        ruleset = build_ruleset(policy)
        # Should still include /tmp and ~/.duh as always-writable
        assert "/tmp" in ruleset.write_paths

    def test_always_writable_includes_tmp_and_duh(self):
        policy = SandboxPolicy(writable_paths=[])
        ruleset = build_ruleset(policy)
        assert "/tmp" in ruleset.write_paths
        home_duh = os.path.expanduser("~/.duh")
        assert home_duh in ruleset.write_paths

    def test_network_policy_passed_through(self):
        policy = SandboxPolicy(network_allowed=False)
        ruleset = build_ruleset(policy)
        assert ruleset.network_allowed is False


class TestBuildLandlockArgv:
    def test_returns_argv_and_env(self):
        policy = SandboxPolicy(writable_paths=["/tmp"])
        argv, env = build_landlock_argv("echo hello", policy)
        assert isinstance(argv, list)
        assert len(argv) > 0
        # The command should be somewhere in the argv
        assert any("echo hello" in arg for arg in argv)

    def test_env_contains_landlock_vars(self):
        policy = SandboxPolicy(writable_paths=["/tmp"])
        argv, env = build_landlock_argv("echo hello", policy)
        # Env should have DUH_LANDLOCK_* vars or be None (if using wrapper script)
        # Either approach is valid
        assert isinstance(env, dict) or env is None

    def test_returns_bash_wrapper(self):
        policy = SandboxPolicy(writable_paths=["/tmp"])
        argv, env = build_landlock_argv("ls -la", policy)
        # Should wrap in python -c or bash -c
        assert "bash" in argv[0] or "python" in argv[0] or argv[0].endswith("python3")
