"""Tests for network policy enforcement."""

import pytest

from duh.adapters.sandbox.network import NetworkMode, NetworkPolicy


class TestNetworkMode:
    def test_full_mode(self):
        assert NetworkMode.FULL.value == "full"

    def test_limited_mode(self):
        assert NetworkMode.LIMITED.value == "limited"

    def test_none_mode(self):
        assert NetworkMode.NONE.value == "none"


class TestNetworkPolicy:
    def test_default_is_full(self):
        policy = NetworkPolicy()
        assert policy.mode == NetworkMode.FULL
        assert policy.allowed_hosts == []
        assert policy.denied_hosts == []

    def test_none_mode_denies_all(self):
        policy = NetworkPolicy(mode=NetworkMode.NONE)
        assert policy.is_request_allowed("GET", "https://example.com") is False

    def test_full_mode_allows_all(self):
        policy = NetworkPolicy(mode=NetworkMode.FULL)
        assert policy.is_request_allowed("POST", "https://example.com") is True

    def test_limited_mode_allows_safe_methods(self):
        policy = NetworkPolicy(mode=NetworkMode.LIMITED)
        assert policy.is_request_allowed("GET", "https://example.com") is True
        assert policy.is_request_allowed("HEAD", "https://example.com") is True
        assert policy.is_request_allowed("OPTIONS", "https://example.com") is True

    def test_limited_mode_blocks_mutating_methods(self):
        policy = NetworkPolicy(mode=NetworkMode.LIMITED)
        assert policy.is_request_allowed("POST", "https://example.com") is False
        assert policy.is_request_allowed("PUT", "https://example.com") is False
        assert policy.is_request_allowed("DELETE", "https://example.com") is False
        assert policy.is_request_allowed("PATCH", "https://example.com") is False

    def test_allowed_hosts_filter(self):
        policy = NetworkPolicy(
            mode=NetworkMode.FULL,
            allowed_hosts=["api.example.com", "cdn.example.com"],
        )
        assert policy.is_request_allowed("GET", "https://api.example.com/v1") is True
        assert policy.is_request_allowed("GET", "https://evil.com") is False

    def test_denied_hosts_filter(self):
        policy = NetworkPolicy(
            mode=NetworkMode.FULL,
            denied_hosts=["evil.com", "malware.org"],
        )
        assert policy.is_request_allowed("GET", "https://evil.com/payload") is False
        assert policy.is_request_allowed("GET", "https://example.com") is True

    def test_denied_hosts_override_allowed(self):
        """Deny list wins over allow list."""
        policy = NetworkPolicy(
            mode=NetworkMode.FULL,
            allowed_hosts=["evil.com"],
            denied_hosts=["evil.com"],
        )
        assert policy.is_request_allowed("GET", "https://evil.com") is False

    def test_to_sandbox_network_flag_full(self):
        policy = NetworkPolicy(mode=NetworkMode.FULL)
        assert policy.to_sandbox_flag() is True

    def test_to_sandbox_network_flag_none(self):
        policy = NetworkPolicy(mode=NetworkMode.NONE)
        assert policy.to_sandbox_flag() is False

    def test_to_sandbox_network_flag_limited(self):
        policy = NetworkPolicy(mode=NetworkMode.LIMITED)
        # Limited still needs network access (filtering happens at app level)
        assert policy.to_sandbox_flag() is True

    def test_host_extraction_from_url(self):
        policy = NetworkPolicy(
            mode=NetworkMode.FULL,
            allowed_hosts=["example.com"],
        )
        assert policy.is_request_allowed("GET", "https://example.com:8443/path") is True
        assert policy.is_request_allowed("GET", "http://other.com/path") is False
