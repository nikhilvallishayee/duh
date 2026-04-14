"""File-reading tools must tag output as FILE_CONTENT."""

from __future__ import annotations

from duh.tools.read import _wrap_file_content
from duh.tools.grep import _wrap_file_content as grep_wrap
from duh.tools.glob_tool import _wrap_file_content as glob_wrap
from duh.kernel.untrusted import TaintSource, UntrustedStr


def test_read_wraps_file_content() -> None:
    result = _wrap_file_content("line 1\nline 2\n")
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.FILE_CONTENT


def test_grep_wraps_file_content() -> None:
    result = grep_wrap("matched line")
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.FILE_CONTENT


def test_glob_wraps_file_content() -> None:
    result = glob_wrap("path/to/file.py")
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.FILE_CONTENT


def test_wrap_idempotent() -> None:
    pre = UntrustedStr("already tagged", TaintSource.FILE_CONTENT)
    assert _wrap_file_content(pre).source == TaintSource.FILE_CONTENT
