"""WebFetch must tag response bodies as NETWORK."""

from __future__ import annotations

from duh.tools.web_fetch import _wrap_network_body
from duh.kernel.untrusted import TaintSource, UntrustedStr


def test_wrap_network_body() -> None:
    result = _wrap_network_body("<html>hello</html>")
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.NETWORK


def test_wrap_network_body_idempotent() -> None:
    pre = UntrustedStr("already tagged", TaintSource.NETWORK)
    result = _wrap_network_body(pre)
    assert result.source == TaintSource.NETWORK
