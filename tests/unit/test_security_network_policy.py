"""Tests for duh.security.network_policy — centralized network egress control (ADR-072)."""

from __future__ import annotations

import pytest

from duh.security.network_policy import NetworkPolicy


class TestNetworkPolicyDefaults:
    """No allow/deny lists => everything passes."""

    def test_allows_any_url_by_default(self):
        policy = NetworkPolicy()
        ok, reason = policy.check("https://example.com/api")
        assert ok is True
        assert reason == ""

    def test_allows_http(self):
        ok, _ = NetworkPolicy().check("http://localhost:8080/health")
        assert ok is True

    def test_empty_url_allowed(self):
        """Edge case: empty URL has no hostname, but no lists to check."""
        ok, _ = NetworkPolicy().check("")
        assert ok is True


class TestDenyList:
    def test_denied_host_blocked(self):
        policy = NetworkPolicy(denied_hosts=["evil.com"])
        ok, reason = policy.check("https://evil.com/payload")
        assert ok is False
        assert "evil.com" in reason
        assert "denied" in reason.lower()

    def test_non_denied_host_allowed(self):
        policy = NetworkPolicy(denied_hosts=["evil.com"])
        ok, _ = policy.check("https://good.com/data")
        assert ok is True

    def test_case_insensitive(self):
        policy = NetworkPolicy(denied_hosts=["Evil.COM"])
        ok, _ = policy.check("https://evil.com/x")
        assert ok is False

    def test_multiple_denied_hosts(self):
        policy = NetworkPolicy(denied_hosts=["a.com", "b.org"])
        ok_a, _ = policy.check("https://a.com")
        ok_b, _ = policy.check("https://b.org")
        ok_c, _ = policy.check("https://c.net")
        assert ok_a is False
        assert ok_b is False
        assert ok_c is True


class TestAllowList:
    def test_allowed_host_passes(self):
        policy = NetworkPolicy(allowed_hosts=["api.example.com"])
        ok, _ = policy.check("https://api.example.com/v1")
        assert ok is True

    def test_non_allowed_host_blocked(self):
        policy = NetworkPolicy(allowed_hosts=["api.example.com"])
        ok, reason = policy.check("https://other.com/v1")
        assert ok is False
        assert "not in the allowed list" in reason

    def test_case_insensitive(self):
        policy = NetworkPolicy(allowed_hosts=["API.Example.COM"])
        ok, _ = policy.check("https://api.example.com/v1")
        assert ok is True


class TestCombinedLists:
    def test_deny_overrides_allow(self):
        """If a host is in both lists, deny wins."""
        policy = NetworkPolicy(
            allowed_hosts=["evil.com", "good.com"],
            denied_hosts=["evil.com"],
        )
        ok, _ = policy.check("https://evil.com")
        assert ok is False

    def test_allowed_not_denied(self):
        policy = NetworkPolicy(
            allowed_hosts=["good.com"],
            denied_hosts=["evil.com"],
        )
        ok, _ = policy.check("https://good.com/api")
        assert ok is True


class TestEdgeCases:
    def test_url_with_port(self):
        policy = NetworkPolicy(allowed_hosts=["localhost"])
        ok, _ = policy.check("http://localhost:3000/health")
        assert ok is True

    def test_url_with_path_and_query(self):
        policy = NetworkPolicy(denied_hosts=["bad.com"])
        ok, _ = policy.check("https://bad.com/path?q=1&x=2#frag")
        assert ok is False

    def test_no_scheme(self):
        """No scheme => urlparse puts everything in path, hostname is None => empty."""
        policy = NetworkPolicy(denied_hosts=["example.com"])
        ok, _ = policy.check("example.com/foo")
        # hostname is empty string, not "example.com", so deny list doesn't match
        assert ok is True
