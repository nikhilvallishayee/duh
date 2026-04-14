"""Property test: arbitrary sequences of str operations preserve taint.

Pick a starting UntrustedStr and a sequence of ops. After running every op,
the result must still be an UntrustedStr whose source is at least as tainted
as the most-tainted input that flowed in."""

from __future__ import annotations

from hypothesis import given, settings, strategies as st

from duh.kernel.untrusted import (
    TaintSource,
    UNTAINTED_SOURCES,
    UntrustedStr,
)


def _tainted(src: TaintSource) -> bool:
    return src not in UNTAINTED_SOURCES


sources = st.sampled_from(list(TaintSource))
safe_str = st.text(alphabet=st.characters(min_codepoint=32, max_codepoint=126), min_size=1, max_size=16)


@given(value=safe_str, src=sources)
@settings(max_examples=100)
def test_starting_source_is_preserved_under_case_ops(value, src) -> None:
    s = UntrustedStr(value, src)
    # Use bound methods (not str.lower(s)) to ensure UntrustedStr overrides fire
    for method_name in ("lower", "upper", "casefold", "title", "capitalize", "swapcase"):
        out = getattr(s, method_name)()
        assert isinstance(out, UntrustedStr)
        assert out.source == src


@given(value=safe_str, src=sources)
@settings(max_examples=100)
def test_slicing_preserves_source(value, src) -> None:
    s = UntrustedStr(value, src)
    assert s[:].source == src
    assert s[1:].source == src
    assert s[::2].source == src


@given(a_val=safe_str, a_src=sources, b_val=safe_str, b_src=sources)
@settings(max_examples=100)
def test_concat_retains_most_tainted(a_val, a_src, b_val, b_src) -> None:
    a = UntrustedStr(a_val, a_src)
    b = UntrustedStr(b_val, b_src)
    result = a + b
    assert isinstance(result, UntrustedStr)
    if _tainted(a_src) or _tainted(b_src):
        assert _tainted(result.source)


@given(parts=st.lists(safe_str, min_size=1, max_size=5), sep_src=sources)
@settings(max_examples=100)
def test_join_preserves_tightest_taint(parts, sep_src) -> None:
    sep = UntrustedStr(",", sep_src)
    result = sep.join(UntrustedStr(p, TaintSource.MODEL_OUTPUT) for p in parts)
    assert isinstance(result, UntrustedStr)
    assert _tainted(result.source)
