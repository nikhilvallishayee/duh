"""Exhaustive tests for duh.kernel.untrusted — TaintSource + UntrustedStr."""

from __future__ import annotations

import pytest

from duh.kernel.untrusted import (
    TaintSource,
    UNTAINTED_SOURCES,
    UntrustedStr,
    TaintLossError,
    merge_source,
)


# ===========================================================================
# 7.1.1 — TaintSource enum + UNTAINTED_SOURCES + merge_source
# ===========================================================================

def test_taint_source_values() -> None:
    assert TaintSource.USER_INPUT.value == "user_input"
    assert TaintSource.MODEL_OUTPUT.value == "model_output"
    assert TaintSource.TOOL_OUTPUT.value == "tool_output"
    assert TaintSource.FILE_CONTENT.value == "file_content"
    assert TaintSource.MCP_OUTPUT.value == "mcp_output"
    assert TaintSource.NETWORK.value == "network"
    assert TaintSource.SYSTEM.value == "system"


def test_untainted_sources_contents() -> None:
    assert TaintSource.USER_INPUT in UNTAINTED_SOURCES
    assert TaintSource.SYSTEM in UNTAINTED_SOURCES
    assert TaintSource.MODEL_OUTPUT not in UNTAINTED_SOURCES
    assert TaintSource.TOOL_OUTPUT not in UNTAINTED_SOURCES
    assert TaintSource.FILE_CONTENT not in UNTAINTED_SOURCES
    assert TaintSource.MCP_OUTPUT not in UNTAINTED_SOURCES
    assert TaintSource.NETWORK not in UNTAINTED_SOURCES


def test_merge_source_both_untainted_prefers_first() -> None:
    class _S(str):
        _source = TaintSource.SYSTEM
    class _U(str):
        _source = TaintSource.USER_INPUT
    a, b = _S("x"), _U("y")
    assert merge_source(a, b) == TaintSource.SYSTEM


def test_merge_source_tainted_wins_over_untainted() -> None:
    class _S(str):
        _source = TaintSource.SYSTEM
    class _M(str):
        _source = TaintSource.MODEL_OUTPUT
    assert merge_source(_S("x"), _M("y")) == TaintSource.MODEL_OUTPUT
    assert merge_source(_M("y"), _S("x")) == TaintSource.MODEL_OUTPUT


def test_merge_source_both_tainted_first_wins() -> None:
    class _M(str):
        _source = TaintSource.MODEL_OUTPUT
    class _F(str):
        _source = TaintSource.FILE_CONTENT
    assert merge_source(_M("a"), _F("b")) == TaintSource.MODEL_OUTPUT


def test_merge_source_plain_str_defaults_to_system() -> None:
    assert merge_source("a", "b") == TaintSource.SYSTEM


# ===========================================================================
# 7.1.2 — Bare UntrustedStr subclass
# ===========================================================================

def test_untrusted_str_constructs_from_str() -> None:
    s = UntrustedStr("hello", TaintSource.MODEL_OUTPUT)
    assert str(s) == "hello"
    assert s.source == TaintSource.MODEL_OUTPUT


def test_untrusted_str_default_source_is_model_output() -> None:
    s = UntrustedStr("hello")
    assert s.source == TaintSource.MODEL_OUTPUT


def test_untrusted_str_is_str_subclass() -> None:
    s = UntrustedStr("hello", TaintSource.USER_INPUT)
    assert isinstance(s, str)
    assert isinstance(s, UntrustedStr)


def test_untrusted_str_every_taint_source_round_trips() -> None:
    for src in TaintSource:
        s = UntrustedStr("x", src)
        assert s.source is src


# ===========================================================================
# 7.1.3 — is_tainted()
# ===========================================================================

def test_is_tainted_per_source() -> None:
    assert UntrustedStr("x", TaintSource.USER_INPUT).is_tainted() is False
    assert UntrustedStr("x", TaintSource.SYSTEM).is_tainted() is False
    assert UntrustedStr("x", TaintSource.MODEL_OUTPUT).is_tainted() is True
    assert UntrustedStr("x", TaintSource.TOOL_OUTPUT).is_tainted() is True
    assert UntrustedStr("x", TaintSource.FILE_CONTENT).is_tainted() is True
    assert UntrustedStr("x", TaintSource.MCP_OUTPUT).is_tainted() is True
    assert UntrustedStr("x", TaintSource.NETWORK).is_tainted() is True


# ===========================================================================
# 7.1.4 — __add__ / __radd__
# ===========================================================================

def test_add_preserves_source_left() -> None:
    a = UntrustedStr("hello ", TaintSource.MODEL_OUTPUT)
    result = a + "world"
    assert isinstance(result, UntrustedStr)
    assert str(result) == "hello world"
    assert result.source == TaintSource.MODEL_OUTPUT


def test_add_preserves_source_right_with_merge() -> None:
    a = UntrustedStr("hello ", TaintSource.USER_INPUT)
    b = UntrustedStr("world", TaintSource.MODEL_OUTPUT)
    result = a + b
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.MODEL_OUTPUT


def test_radd_preserves_source() -> None:
    b = UntrustedStr("world", TaintSource.TOOL_OUTPUT)
    result = "hello " + b
    assert isinstance(result, UntrustedStr)
    assert str(result) == "hello world"
    assert result.source == TaintSource.TOOL_OUTPUT


# ===========================================================================
# 7.1.5 — __mod__
# ===========================================================================

def test_mod_format_preserves_source() -> None:
    tmpl = UntrustedStr("hello %s", TaintSource.MODEL_OUTPUT)
    result = tmpl % "world"
    assert isinstance(result, UntrustedStr)
    assert str(result) == "hello world"
    assert result.source == TaintSource.MODEL_OUTPUT


def test_mod_format_merges_with_tainted_arg() -> None:
    tmpl = UntrustedStr("x=%s", TaintSource.SYSTEM)
    arg = UntrustedStr("evil", TaintSource.MODEL_OUTPUT)
    result = tmpl % arg
    assert result.source == TaintSource.MODEL_OUTPUT


# ===========================================================================
# 7.1.6 — __mul__ / __rmul__
# ===========================================================================

def test_mul_preserves_source() -> None:
    a = UntrustedStr("ab", TaintSource.FILE_CONTENT)
    result = a * 3
    assert isinstance(result, UntrustedStr)
    assert str(result) == "ababab"
    assert result.source == TaintSource.FILE_CONTENT


def test_rmul_preserves_source() -> None:
    a = UntrustedStr("ab", TaintSource.FILE_CONTENT)
    result = 2 * a
    assert isinstance(result, UntrustedStr)
    assert str(result) == "abab"
    assert result.source == TaintSource.FILE_CONTENT


# ===========================================================================
# 7.1.7 — format / format_map
# ===========================================================================

def test_format_preserves_source() -> None:
    tmpl = UntrustedStr("hi {}", TaintSource.MODEL_OUTPUT)
    result = tmpl.format("bob")
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.MODEL_OUTPUT


def test_format_merges_tainted_arg() -> None:
    tmpl = UntrustedStr("hi {}", TaintSource.SYSTEM)
    arg = UntrustedStr("evil", TaintSource.MODEL_OUTPUT)
    result = tmpl.format(arg)
    assert result.source == TaintSource.MODEL_OUTPUT


def test_format_map_preserves_source() -> None:
    tmpl = UntrustedStr("{x}", TaintSource.TOOL_OUTPUT)
    result = tmpl.format_map({"x": "hi"})
    assert isinstance(result, UntrustedStr)
    assert result.source == TaintSource.TOOL_OUTPUT


# ===========================================================================
# 7.1.8 — join
# ===========================================================================

def test_join_preserves_self_source() -> None:
    sep = UntrustedStr(", ", TaintSource.SYSTEM)
    result = sep.join(["a", "b", "c"])
    assert isinstance(result, UntrustedStr)
    assert str(result) == "a, b, c"


def test_join_merges_with_tainted_parts() -> None:
    sep = UntrustedStr(", ", TaintSource.SYSTEM)
    parts = [UntrustedStr("evil", TaintSource.MODEL_OUTPUT), "clean"]
    result = sep.join(parts)
    assert result.source == TaintSource.MODEL_OUTPUT


# ===========================================================================
# 7.1.9 — replace
# ===========================================================================

def test_replace_preserves_source() -> None:
    s = UntrustedStr("hello", TaintSource.MODEL_OUTPUT)
    result = s.replace("l", "L")
    assert isinstance(result, UntrustedStr)
    assert str(result) == "heLLo"
    assert result.source == TaintSource.MODEL_OUTPUT


def test_replace_merges_tainted_new() -> None:
    s = UntrustedStr("x", TaintSource.SYSTEM)
    evil = UntrustedStr("y", TaintSource.FILE_CONTENT)
    result = s.replace("x", evil)
    assert result.source == TaintSource.FILE_CONTENT


# ===========================================================================
# 7.1.10 — strip family
# ===========================================================================

def test_strip_family_preserves_source() -> None:
    for src in (TaintSource.MODEL_OUTPUT, TaintSource.USER_INPUT):
        s = UntrustedStr("  hi  ", src)
        for fn in (s.strip, s.lstrip, s.rstrip):
            result = fn()
            assert isinstance(result, UntrustedStr), f"{fn} lost type"
            assert result.source == src, f"{fn} lost source"
    assert UntrustedStr("xxhix", TaintSource.FILE_CONTENT).strip("x") == "hi"


# ===========================================================================
# 7.1.11 — split family
# ===========================================================================

def test_split_returns_list_of_untrusted_str() -> None:
    s = UntrustedStr("a,b,c", TaintSource.MODEL_OUTPUT)
    parts = s.split(",")
    assert len(parts) == 3
    for p in parts:
        assert isinstance(p, UntrustedStr)
        assert p.source == TaintSource.MODEL_OUTPUT
    assert [str(p) for p in parts] == ["a", "b", "c"]


def test_rsplit_returns_untrusted() -> None:
    s = UntrustedStr("a.b.c", TaintSource.TOOL_OUTPUT)
    parts = s.rsplit(".", 1)
    assert all(isinstance(p, UntrustedStr) for p in parts)
    assert parts[0].source == TaintSource.TOOL_OUTPUT


def test_splitlines_returns_untrusted() -> None:
    s = UntrustedStr("x\ny\n", TaintSource.FILE_CONTENT)
    lines = s.splitlines()
    assert len(lines) == 2
    for line in lines:
        assert isinstance(line, UntrustedStr)
        assert line.source == TaintSource.FILE_CONTENT


# ===========================================================================
# 7.1.12 — Case methods
# ===========================================================================

import pytest as _pytest


@_pytest.mark.parametrize("method,expected", [
    ("lower", "hello"),
    ("upper", "HELLO"),
    ("title", "Hello"),
    ("casefold", "hello"),
    ("capitalize", "Hello"),
    ("swapcase", "HELLO"),
])
def test_case_methods_preserve_source(method, expected) -> None:
    s = UntrustedStr("hello", TaintSource.MODEL_OUTPUT)
    result = getattr(s, method)()
    assert isinstance(result, UntrustedStr)
    assert str(result) == expected
    assert result.source == TaintSource.MODEL_OUTPUT


# ===========================================================================
# 7.1.13 — Padding methods
# ===========================================================================

def test_expandtabs_preserves_source() -> None:
    s = UntrustedStr("a\tb", TaintSource.TOOL_OUTPUT)
    assert isinstance(s.expandtabs(4), UntrustedStr)
    assert s.expandtabs(4).source == TaintSource.TOOL_OUTPUT


def test_justify_preserve_source() -> None:
    s = UntrustedStr("x", TaintSource.FILE_CONTENT)
    for r in (s.center(5), s.ljust(5), s.rjust(5)):
        assert isinstance(r, UntrustedStr)
        assert r.source == TaintSource.FILE_CONTENT
    assert str(s.center(5)) == "  x  "


def test_zfill_preserves_source() -> None:
    s = UntrustedStr("42", TaintSource.MODEL_OUTPUT)
    assert s.zfill(5).source == TaintSource.MODEL_OUTPUT
    assert str(s.zfill(5)) == "00042"


# ===========================================================================
# 7.1.14 — translate / encode / removeprefix / removesuffix
# ===========================================================================

def test_translate_preserves_source() -> None:
    s = UntrustedStr("abc", TaintSource.MCP_OUTPUT)
    table = str.maketrans("a", "A")
    result = s.translate(table)
    assert isinstance(result, UntrustedStr)
    assert str(result) == "Abc"
    assert result.source == TaintSource.MCP_OUTPUT


def test_encode_returns_plain_bytes() -> None:
    s = UntrustedStr("hi", TaintSource.NETWORK)
    result = s.encode("utf-8")
    assert isinstance(result, bytes)
    assert result == b"hi"


def test_removeprefix_preserves_source() -> None:
    s = UntrustedStr("pfx-body", TaintSource.FILE_CONTENT)
    result = s.removeprefix("pfx-")
    assert isinstance(result, UntrustedStr)
    assert str(result) == "body"
    assert result.source == TaintSource.FILE_CONTENT


def test_removesuffix_preserves_source() -> None:
    s = UntrustedStr("body-sfx", TaintSource.FILE_CONTENT)
    result = s.removesuffix("-sfx")
    assert isinstance(result, UntrustedStr)
    assert str(result) == "body"
    assert result.source == TaintSource.FILE_CONTENT


# ===========================================================================
# 7.1.15 — Slicing
# ===========================================================================

def test_slice_preserves_source() -> None:
    s = UntrustedStr("helloworld", TaintSource.MODEL_OUTPUT)
    result = s[5:]
    assert isinstance(result, UntrustedStr)
    assert str(result) == "world"
    assert result.source == TaintSource.MODEL_OUTPUT


def test_index_preserves_source() -> None:
    s = UntrustedStr("abc", TaintSource.FILE_CONTENT)
    result = s[1]
    assert isinstance(result, UntrustedStr)
    assert str(result) == "b"
    assert result.source == TaintSource.FILE_CONTENT


def test_stride_preserves_source() -> None:
    s = UntrustedStr("abcdef", TaintSource.TOOL_OUTPUT)
    assert s[::2].source == TaintSource.TOOL_OUTPUT
    assert str(s[::2]) == "ace"


# ===========================================================================
# 7.1.16 — Non-str-returning methods pass through
# ===========================================================================

def test_non_str_methods_pass_through() -> None:
    s = UntrustedStr("hello world", TaintSource.MODEL_OUTPUT)
    # Methods returning int / bool / list[int] / not str
    assert len(s) == 11
    assert s.count("l") == 3
    assert s.startswith("hello") is True
    assert s.endswith("world") is True
    assert s.find("world") == 6
    assert s.rfind("l") == 9
    assert s.index("o") == 4
    assert s.rindex("o") == 7
    assert UntrustedStr("123", TaintSource.FILE_CONTENT).isdigit() is True
    assert UntrustedStr("abc", TaintSource.FILE_CONTENT).isalpha() is True
    assert UntrustedStr("   ", TaintSource.FILE_CONTENT).isspace() is True
    assert UntrustedStr("Hi There", TaintSource.FILE_CONTENT).istitle() is True
    assert UntrustedStr("ABC", TaintSource.FILE_CONTENT).isupper() is True
    assert UntrustedStr("abc", TaintSource.FILE_CONTENT).islower() is True
    assert UntrustedStr("⅒", TaintSource.FILE_CONTENT).isnumeric() is True
    assert UntrustedStr("3", TaintSource.FILE_CONTENT).isdecimal() is True
    assert UntrustedStr("abc1", TaintSource.FILE_CONTENT).isalnum() is True
    assert UntrustedStr("abc", TaintSource.FILE_CONTENT).isidentifier() is True
    assert UntrustedStr("abc", TaintSource.FILE_CONTENT).isprintable() is True
    assert UntrustedStr("abc", TaintSource.FILE_CONTENT).isascii() is True
    assert hash(UntrustedStr("x", TaintSource.MODEL_OUTPUT)) == hash("x")
    assert bool(UntrustedStr("x", TaintSource.MODEL_OUTPUT)) is True
    assert bool(UntrustedStr("", TaintSource.MODEL_OUTPUT)) is False
    assert "lo" in UntrustedStr("hello", TaintSource.MODEL_OUTPUT)
    assert list(iter(UntrustedStr("ab", TaintSource.MODEL_OUTPUT))) == ["a", "b"]


# ===========================================================================
# 7.1.18 — DUH_TAINT_DEBUG + DUH_TAINT_STRICT
# ===========================================================================

def test_taint_strict_raises_on_drop(monkeypatch) -> None:
    from duh.kernel.untrusted import TaintLossError, _record_drop

    monkeypatch.setenv("DUH_TAINT_STRICT", "1")
    with pytest.raises(TaintLossError):
        _record_drop("fake_op", "expected_source")


def test_taint_debug_prints(monkeypatch, capsys) -> None:
    from duh.kernel.untrusted import _record_preserve

    monkeypatch.setenv("DUH_TAINT_DEBUG", "1")
    _record_preserve("my_op", TaintSource.MODEL_OUTPUT)
    out = capsys.readouterr().err
    assert "my_op" in out
    assert "model_output" in out


# ===========================================================================
# 7.1.25 — DUH_TAINT_STRICT=1 full pipeline
# ===========================================================================

def test_strict_mode_full_pipeline(monkeypatch) -> None:
    """With DUH_TAINT_STRICT=1, every UntrustedStr method that returns a new
    string must return an UntrustedStr. Passing plain str through any tagged
    code path must raise TaintLossError."""

    monkeypatch.setenv("DUH_TAINT_STRICT", "1")

    s = UntrustedStr("hello world", TaintSource.MODEL_OUTPUT)

    # All these must succeed (no tag loss):
    assert isinstance(s.upper(), UntrustedStr)
    assert isinstance(s.lower(), UntrustedStr)
    assert isinstance(s.strip(), UntrustedStr)
    assert isinstance(s + " more", UntrustedStr)
    assert isinstance(s.replace("hello", "hi"), UntrustedStr)
    assert isinstance(s[:5], UntrustedStr)
    parts = s.split()
    assert all(isinstance(p, UntrustedStr) for p in parts)
    joined = UntrustedStr(",", TaintSource.SYSTEM).join(parts)
    assert isinstance(joined, UntrustedStr)


def test_strict_mode_record_drop_raises(monkeypatch) -> None:
    from duh.kernel.untrusted import TaintLossError, _record_drop

    monkeypatch.setenv("DUH_TAINT_STRICT", "1")
    with pytest.raises(TaintLossError, match="taint dropped"):
        _record_drop("test_op", TaintSource.MODEL_OUTPUT)
