"""Tests for SSRF protection in WebFetchTool (SEC-HIGH-4)."""

from __future__ import annotations

import socket
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from duh.kernel.tool import ToolContext, ToolResult
from duh.tools.web_fetch import (
    WebFetchTool,
    _is_private_ip,
    _validate_url_ssrf,
)


def ctx() -> ToolContext:
    return ToolContext(cwd=".")


# ---------------------------------------------------------------------------
# _is_private_ip — unit tests
# ---------------------------------------------------------------------------


class TestIsPrivateIp:
    """Direct tests for the _is_private_ip helper."""

    # --- Should be private ---

    def test_loopback_127_0_0_1(self):
        assert _is_private_ip("127.0.0.1") is True

    def test_loopback_127_x(self):
        assert _is_private_ip("127.0.0.2") is True
        assert _is_private_ip("127.255.255.255") is True

    def test_ipv6_loopback(self):
        assert _is_private_ip("::1") is True

    def test_private_10_x(self):
        assert _is_private_ip("10.0.0.1") is True
        assert _is_private_ip("10.255.255.255") is True

    def test_private_172_16_x(self):
        assert _is_private_ip("172.16.0.1") is True
        assert _is_private_ip("172.31.255.255") is True

    def test_private_192_168_x(self):
        assert _is_private_ip("192.168.1.1") is True
        assert _is_private_ip("192.168.0.0") is True

    def test_link_local_169_254(self):
        """AWS metadata endpoint lives at 169.254.169.254."""
        assert _is_private_ip("169.254.169.254") is True
        assert _is_private_ip("169.254.0.1") is True

    def test_unspecified_0_0_0_0(self):
        assert _is_private_ip("0.0.0.0") is True

    def test_unspecified_ipv6(self):
        assert _is_private_ip("::") is True

    def test_ipv6_link_local(self):
        assert _is_private_ip("fe80::1") is True

    def test_multicast(self):
        assert _is_private_ip("224.0.0.1") is True

    def test_unparseable_treated_as_private(self):
        """Garbage input should be blocked (fail-closed)."""
        assert _is_private_ip("not-an-ip") is True

    # --- Should NOT be private ---

    def test_public_ip_8_8_8_8(self):
        assert _is_private_ip("8.8.8.8") is False

    def test_public_ip_1_1_1_1(self):
        assert _is_private_ip("1.1.1.1") is False

    def test_public_ip_93_184_216_34(self):
        assert _is_private_ip("93.184.216.34") is False

    def test_public_ipv6(self):
        assert _is_private_ip("2607:f8b0:4004:800::200e") is False


# ---------------------------------------------------------------------------
# _validate_url_ssrf — unit tests (DNS resolution mocked)
# ---------------------------------------------------------------------------


def _fake_addrinfo(ip: str, family: int = socket.AF_INET):
    """Build a minimal getaddrinfo-style result for a single IP."""
    if family == socket.AF_INET:
        return [(family, socket.SOCK_STREAM, 0, "", (ip, 0))]
    return [(family, socket.SOCK_STREAM, 0, "", (ip, 0, 0, 0))]


class TestValidateUrlSsrf:
    """Tests for _validate_url_ssrf with mocked DNS."""

    def test_blocks_loopback_127(self):
        with patch("duh.tools.web_fetch.socket.getaddrinfo", return_value=_fake_addrinfo("127.0.0.1")):
            with pytest.raises(ValueError, match="SSRF blocked"):
                _validate_url_ssrf("http://evil.com/steal")

    def test_blocks_private_10_x(self):
        with patch("duh.tools.web_fetch.socket.getaddrinfo", return_value=_fake_addrinfo("10.0.0.1")):
            with pytest.raises(ValueError, match="SSRF blocked"):
                _validate_url_ssrf("http://evil.com")

    def test_blocks_private_192_168(self):
        with patch("duh.tools.web_fetch.socket.getaddrinfo", return_value=_fake_addrinfo("192.168.1.1")):
            with pytest.raises(ValueError, match="SSRF blocked"):
                _validate_url_ssrf("http://evil.com")

    def test_blocks_private_172_16(self):
        with patch("duh.tools.web_fetch.socket.getaddrinfo", return_value=_fake_addrinfo("172.16.0.1")):
            with pytest.raises(ValueError, match="SSRF blocked"):
                _validate_url_ssrf("http://evil.com")

    def test_blocks_link_local_169_254(self):
        with patch("duh.tools.web_fetch.socket.getaddrinfo", return_value=_fake_addrinfo("169.254.169.254")):
            with pytest.raises(ValueError, match="SSRF blocked"):
                _validate_url_ssrf("http://evil.com")

    def test_blocks_0_0_0_0(self):
        with patch("duh.tools.web_fetch.socket.getaddrinfo", return_value=_fake_addrinfo("0.0.0.0")):
            with pytest.raises(ValueError, match="SSRF blocked"):
                _validate_url_ssrf("http://evil.com")

    def test_blocks_ipv6_loopback(self):
        with patch(
            "duh.tools.web_fetch.socket.getaddrinfo",
            return_value=_fake_addrinfo("::1", socket.AF_INET6),
        ):
            with pytest.raises(ValueError, match="SSRF blocked"):
                _validate_url_ssrf("http://evil.com")

    def test_allows_public_ip(self):
        with patch("duh.tools.web_fetch.socket.getaddrinfo", return_value=_fake_addrinfo("93.184.216.34")):
            _validate_url_ssrf("https://example.com")  # should not raise

    def test_allows_another_public_ip(self):
        with patch("duh.tools.web_fetch.socket.getaddrinfo", return_value=_fake_addrinfo("8.8.8.8")):
            _validate_url_ssrf("https://dns.google")  # should not raise

    def test_blocks_metadata_google_internal(self):
        """Cloud metadata hostnames are blocked before DNS even happens."""
        with pytest.raises(ValueError, match="cloud metadata"):
            _validate_url_ssrf("http://metadata.google.internal/computeMetadata/v1/")

    def test_blocks_metadata_gcp_internal(self):
        with pytest.raises(ValueError, match="cloud metadata"):
            _validate_url_ssrf("http://metadata.gcp.internal/something")

    def test_blocks_when_any_resolved_ip_is_private(self):
        """If DNS returns a mix of public and private, block."""
        mixed = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
        ]
        with patch("duh.tools.web_fetch.socket.getaddrinfo", return_value=mixed):
            with pytest.raises(ValueError, match="SSRF blocked"):
                _validate_url_ssrf("http://dual-homed.example.com")

    def test_dns_failure_raises_valueerror(self):
        with patch(
            "duh.tools.web_fetch.socket.getaddrinfo",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            with pytest.raises(ValueError, match="DNS resolution failed"):
                _validate_url_ssrf("http://nonexistent.invalid")

    def test_no_hostname_raises(self):
        with pytest.raises(ValueError, match="no hostname"):
            _validate_url_ssrf("http://")


# ---------------------------------------------------------------------------
# WebFetchTool.call() — integration with SSRF guard (mocked HTTP + DNS)
# ---------------------------------------------------------------------------


def _mock_response(
    text: str = "Hello, world!",
    status_code: int = 200,
    content_type: str = "text/plain",
    url: str = "https://example.com",
):
    resp = httpx.Response(
        status_code=status_code,
        headers={"content-type": content_type},
        text=text,
        request=httpx.Request("GET", url),
    )
    return resp


class TestWebFetchSsrfIntegration:
    """End-to-end tests: WebFetchTool.call() -> SSRF guard -> error or success."""

    tool = WebFetchTool()

    async def test_blocks_localhost_url(self):
        with patch(
            "duh.tools.web_fetch.socket.getaddrinfo",
            return_value=_fake_addrinfo("127.0.0.1"),
        ):
            result = await self.tool.call({"url": "http://localhost/admin"}, ctx())
        assert result.is_error is True
        assert "SSRF blocked" in result.output

    async def test_blocks_127_0_0_1_url(self):
        with patch(
            "duh.tools.web_fetch.socket.getaddrinfo",
            return_value=_fake_addrinfo("127.0.0.1"),
        ):
            result = await self.tool.call({"url": "http://127.0.0.1/secret"}, ctx())
        assert result.is_error is True
        assert "SSRF blocked" in result.output

    async def test_blocks_aws_metadata_ip(self):
        with patch(
            "duh.tools.web_fetch.socket.getaddrinfo",
            return_value=_fake_addrinfo("169.254.169.254"),
        ):
            result = await self.tool.call(
                {"url": "http://169.254.169.254/latest/meta-data/"}, ctx()
            )
        assert result.is_error is True
        assert "SSRF blocked" in result.output

    async def test_blocks_internal_10_x(self):
        with patch(
            "duh.tools.web_fetch.socket.getaddrinfo",
            return_value=_fake_addrinfo("10.0.0.1"),
        ):
            result = await self.tool.call({"url": "http://internal.corp/api"}, ctx())
        assert result.is_error is True
        assert "SSRF blocked" in result.output

    async def test_blocks_google_metadata_hostname(self):
        result = await self.tool.call(
            {"url": "http://metadata.google.internal/computeMetadata/v1/"}, ctx()
        )
        assert result.is_error is True
        assert "cloud metadata" in result.output

    async def test_allows_public_url(self):
        """Public IP should pass SSRF check and proceed to HTTP fetch."""
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=_mock_response("OK"))

        with patch(
            "duh.tools.web_fetch.socket.getaddrinfo",
            return_value=_fake_addrinfo("93.184.216.34"),
        ):
            with patch("duh.tools.web_fetch.httpx.AsyncClient", return_value=mock_client):
                result = await self.tool.call({"url": "https://example.com"}, ctx())

        assert result.is_error is False
        assert "OK" in result.output

    async def test_blocks_ipv6_loopback_url(self):
        with patch(
            "duh.tools.web_fetch.socket.getaddrinfo",
            return_value=_fake_addrinfo("::1", socket.AF_INET6),
        ):
            result = await self.tool.call({"url": "http://[::1]/secret"}, ctx())
        assert result.is_error is True
        assert "SSRF blocked" in result.output

    async def test_blocks_0_0_0_0_url(self):
        with patch(
            "duh.tools.web_fetch.socket.getaddrinfo",
            return_value=_fake_addrinfo("0.0.0.0"),
        ):
            result = await self.tool.call({"url": "http://0.0.0.0/"}, ctx())
        assert result.is_error is True
        assert "SSRF blocked" in result.output
