"""Taint tagging for CLI prompt flag and SDK user messages."""

from __future__ import annotations

from duh.cli.runner import wrap_prompt_flag
from duh.cli.sdk_runner import wrap_stream_user_message
from duh.kernel.untrusted import TaintSource, UntrustedStr


def test_prompt_flag_tagged_user_input() -> None:
    result = wrap_prompt_flag("hello")
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.USER_INPUT


def test_stream_user_message_tagged_user_input() -> None:
    result = wrap_stream_user_message({"role": "user", "content": "hi"})
    # Content string tagged
    assert isinstance(result["content"], UntrustedStr)
    assert result["content"].source == TaintSource.USER_INPUT
