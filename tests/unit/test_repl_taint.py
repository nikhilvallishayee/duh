"""Every user input value handed to the REPL message queue is wrapped as
UntrustedStr with TaintSource.USER_INPUT."""

from __future__ import annotations

from duh.cli.repl import _wrap_user_input
from duh.kernel.untrusted import TaintSource, UntrustedStr


def test_wrap_user_input_returns_untrusted() -> None:
    result = _wrap_user_input("hello")
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.USER_INPUT


def test_wrap_user_input_idempotent_preserves_existing_tag() -> None:
    pre = UntrustedStr("hi", TaintSource.USER_INPUT)
    result = _wrap_user_input(pre)
    assert result.source == TaintSource.USER_INPUT
