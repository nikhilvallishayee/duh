"""Tests for macOS Seatbelt sandbox profile generation."""

import pytest

from duh.adapters.sandbox.policy import SandboxPolicy
from duh.adapters.sandbox.seatbelt import generate_profile


class TestGenerateProfile:
    def test_default_policy_allows_read(self):
        policy = SandboxPolicy()
        profile = generate_profile(policy)
        assert "(version 1)" in profile
        assert "(allow file-read*)" in profile

    def test_default_policy_allows_process(self):
        policy = SandboxPolicy()
        profile = generate_profile(policy)
        assert "(allow process-exec)" in profile
        assert "(allow process-fork)" in profile

    def test_writable_paths_in_profile(self):
        policy = SandboxPolicy(writable_paths=["/tmp/work", "/home/user/.duh"])
        profile = generate_profile(policy)
        assert '(subpath "/tmp/work")' in profile
        assert '(subpath "/home/user/.duh")' in profile
        assert "(allow file-write*" in profile

    def test_no_writable_paths_still_allows_always_writable(self):
        """Even with no explicit writable_paths, always-writable dirs are included."""
        policy = SandboxPolicy(writable_paths=[])
        profile = generate_profile(policy)
        # /tmp is always writable, so file-write* should be present
        assert "(allow file-write*" in profile
        assert '"/tmp"' in profile

    def test_network_allowed(self):
        policy = SandboxPolicy(network_allowed=True)
        profile = generate_profile(policy)
        assert "(allow network*)" in profile

    def test_network_denied(self):
        policy = SandboxPolicy(network_allowed=False)
        profile = generate_profile(policy)
        assert "(deny network*)" in profile

    def test_readable_paths_in_profile(self):
        policy = SandboxPolicy(readable_paths=["/usr/local", "/opt"])
        profile = generate_profile(policy)
        # Readable paths should appear in file-read rules
        assert '"/usr/local"' in profile or '(subpath "/usr/local")' in profile

    def test_profile_is_valid_sexp(self):
        """Basic validation: parens should balance."""
        policy = SandboxPolicy(
            writable_paths=["/tmp"],
            readable_paths=["/usr"],
            network_allowed=False,
        )
        profile = generate_profile(policy)
        open_count = profile.count("(")
        close_count = profile.count(")")
        assert open_count == close_count, (
            f"Unbalanced parens: {open_count} open, {close_count} close"
        )

    def test_temp_dir_always_writable(self):
        """Temp dirs should always be writable for subprocess needs."""
        policy = SandboxPolicy(writable_paths=[])
        profile = generate_profile(policy)
        # /tmp or /private/tmp should be writable (macOS maps /tmp -> /private/tmp)
        assert "/tmp" in profile or "/private/tmp" in profile

    def test_home_duh_always_writable(self):
        """~/.duh should always be writable for duh's own state."""
        policy = SandboxPolicy(writable_paths=[])
        profile = generate_profile(policy)
        assert ".duh" in profile

    def test_profile_denies_by_default(self):
        """The profile should have a default-deny stance."""
        policy = SandboxPolicy()
        profile = generate_profile(policy)
        assert "(deny default)" in profile
