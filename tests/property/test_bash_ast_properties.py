"""Property tests for duh.tools.bash_ast — the shell tokenizer.

Tests structural invariants that must hold for *any* input:
- tokenize() never raises on well-formed ASCII input (only ValueError on fanout)
- tokenize() segments can be re-assembled to cover the original tokens
- Balanced quotes are respected (operators inside quotes are not split)
- No catastrophic backtracking (every input under 1000 chars finishes in <1s)
- strip_comments preserves non-comment lines
- strip_wrappers is idempotent after one pass
"""

from __future__ import annotations

import time

from hypothesis import given, settings, assume, strategies as st

from duh.tools.bash_ast import (
    MAX_SUBCOMMANDS,
    Segment,
    SegmentType,
    strip_comments,
    strip_wrappers,
    tokenize,
    _mask_quotes,
)

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# ASCII text covering letters, digits, punctuation, symbols, whitespace.
# Excludes control chars that are used internally (\x00, \x01).
_ascii_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S", "Z"),
    ),
    max_size=500,
)

# Shell-like commands: printable ASCII only, avoids null bytes.
_shell_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    max_size=500,
)

# Short shell fragments for targeted tests.
_short_shell = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    max_size=100,
)

# Operators that the tokenizer splits on.
_operators = st.sampled_from(["&&", "||", "|", ";"])


# ---------------------------------------------------------------------------
# Property: tokenize() never raises on arbitrary ASCII (except ValueError)
# ---------------------------------------------------------------------------

@given(cmd=_ascii_text)
@settings(max_examples=500)
def test_tokenize_never_crashes_on_arbitrary_input(cmd: str) -> None:
    """tokenize() must not raise any exception other than ValueError
    (for the fanout cap). Any other exception is a parser bug."""
    try:
        result = tokenize(cmd)
        assert isinstance(result, list)
        for seg in result:
            assert isinstance(seg, Segment)
            assert isinstance(seg.text, str)
            assert seg.seg_type in (SegmentType.SIMPLE, SegmentType.SUBSHELL)
    except ValueError as exc:
        # Only the fanout cap ValueError is acceptable
        assert "fanout" in str(exc).lower() or "cap" in str(exc).lower()


# ---------------------------------------------------------------------------
# Property: tokenize() output segments cover original non-comment content
# ---------------------------------------------------------------------------

@given(cmd=_shell_text)
@settings(max_examples=500)
def test_segment_text_chars_come_from_input(cmd: str) -> None:
    """Every character in the segment text (after removing masking artifacts)
    must be a character that existed in the original command. This verifies
    the tokenizer does not fabricate content out of thin air.

    Note: the quote masker replaces quoted strings and escape sequences with
    null bytes, so we only check character membership, not substring position.
    """
    try:
        segments = tokenize(cmd)
    except ValueError:
        return  # fanout cap — not a bug

    input_chars = set(cmd)
    for seg in segments:
        # Remove masking artifacts before checking
        clean_text = seg.text.replace("\x00", "").replace("\x01", "")
        for ch in clean_text:
            assert ch in input_chars, (
                f"Character {ch!r} in segment {seg.text!r} "
                f"not found in original input {cmd!r}"
            )


# ---------------------------------------------------------------------------
# Property: balanced single quotes prevent operator splitting
# ---------------------------------------------------------------------------

@given(
    before=_short_shell,
    inside=st.sampled_from(["&&", "||", "|", ";"]),
    after=_short_shell,
)
@settings(max_examples=500)
def test_operators_inside_single_quotes_not_split(
    before: str, inside: str, after: str,
) -> None:
    """An operator inside single quotes must NOT create a split point.
    The entire `echo before'op'after` should remain in a single segment,
    not be split into multiple segments by the quoted operator."""
    # Avoid inputs that contain characters that interfere with quoting/splitting
    assume("'" not in before and "'" not in after)
    assume("\\" not in before and "\\" not in after)
    assume(all(op not in before and op not in after for op in ["&&", "||", "|", ";"]))
    assume("#" not in before and "#" not in after)
    assume("$(" not in before and "$(" not in after)
    assume("`" not in before and "`" not in after)
    assume('"' not in before and '"' not in after)
    cmd = f"echo {before}'{inside}'{after}"
    try:
        segments = tokenize(cmd)
    except ValueError:
        return

    # The key invariant: the quoted operator must NOT cause a split.
    # With no other operators in before/after, we should get exactly 1
    # SIMPLE segment (the echo command containing the quoted operator).
    simple_segments = [s for s in segments if s.seg_type == SegmentType.SIMPLE]
    assert len(simple_segments) == 1, (
        f"Quoted operator {inside!r} caused a split into {len(simple_segments)} "
        f"segments: {[s.text for s in simple_segments]}"
    )


# ---------------------------------------------------------------------------
# Property: balanced double quotes prevent operator splitting
# ---------------------------------------------------------------------------

@given(
    before=_short_shell,
    inside=st.sampled_from(["&&", "||", "|", ";"]),
    after=_short_shell,
)
@settings(max_examples=500)
def test_operators_inside_double_quotes_not_split(
    before: str, inside: str, after: str,
) -> None:
    """An operator inside double quotes must NOT create a split point.
    The entire `echo before"op"after` should remain in a single segment."""
    assume('"' not in before and '"' not in after)
    assume("\\" not in before and "\\" not in after)
    assume(all(op not in before and op not in after for op in ["&&", "||", "|", ";"]))
    assume("#" not in before and "#" not in after)
    assume("$(" not in before and "$(" not in after)
    assume("`" not in before and "`" not in after)
    cmd = f'echo {before}"{inside}"{after}'
    try:
        segments = tokenize(cmd)
    except ValueError:
        return

    # The key invariant: the quoted operator must NOT cause a split.
    simple_segments = [s for s in segments if s.seg_type == SegmentType.SIMPLE]
    assert len(simple_segments) == 1, (
        f"Quoted operator {inside!r} in double quotes caused a split into "
        f"{len(simple_segments)} segments: {[s.text for s in simple_segments]}"
    )


# ---------------------------------------------------------------------------
# Property: no catastrophic backtracking (under 1 second for <=1000 chars)
# ---------------------------------------------------------------------------

@given(cmd=st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    max_size=1000,
))
@settings(max_examples=500)
def test_no_catastrophic_backtracking(cmd: str) -> None:
    """tokenize() must finish within 1 second for any input up to 1000 chars.
    Catastrophic backtracking in the regex engine would blow this budget."""
    start = time.monotonic()
    try:
        tokenize(cmd)
    except ValueError:
        pass  # fanout cap is fine
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, (
        f"tokenize() took {elapsed:.2f}s on {len(cmd)}-char input — "
        f"possible catastrophic backtracking. Input prefix: {cmd[:80]!r}"
    )


# ---------------------------------------------------------------------------
# Property: strip_comments preserves all non-comment lines
# ---------------------------------------------------------------------------

@given(lines=st.lists(
    st.text(
        alphabet=st.characters(min_codepoint=32, max_codepoint=126),
        max_size=80,
    ),
    min_size=1,
    max_size=20,
))
@settings(max_examples=500)
def test_strip_comments_preserves_non_comment_lines(lines: list[str]) -> None:
    """Lines not starting with # (after whitespace) must survive strip_comments
    unchanged. Comment lines must be replaced with empty strings."""
    cmd = "\n".join(lines)
    result = strip_comments(cmd)
    result_lines = result.split("\n")

    assert len(result_lines) == len(lines), (
        "strip_comments must not change the number of lines"
    )

    for orig, stripped in zip(lines, result_lines):
        if orig.lstrip().startswith("#"):
            assert stripped == "", (
                f"Comment line should be empty, got {stripped!r}"
            )
        else:
            assert stripped == orig, (
                f"Non-comment line was modified: {orig!r} -> {stripped!r}"
            )


# ---------------------------------------------------------------------------
# Property: strip_wrappers is idempotent
# ---------------------------------------------------------------------------

@given(cmd=_shell_text)
@settings(max_examples=500)
def test_strip_wrappers_idempotent(cmd: str) -> None:
    """Stripping wrappers twice must give the same result as stripping once."""
    once = strip_wrappers(cmd)
    twice = strip_wrappers(once)
    assert once == twice, (
        f"strip_wrappers not idempotent: "
        f"once={once!r}, twice={twice!r}"
    )


# ---------------------------------------------------------------------------
# Property: segment count is bounded by MAX_SUBCOMMANDS
# ---------------------------------------------------------------------------

@given(cmd=_shell_text)
@settings(max_examples=500)
def test_segment_count_bounded(cmd: str) -> None:
    """tokenize() either returns <= MAX_SUBCOMMANDS segments or raises ValueError."""
    try:
        segments = tokenize(cmd)
        assert len(segments) <= MAX_SUBCOMMANDS, (
            f"Got {len(segments)} segments, exceeding cap of {MAX_SUBCOMMANDS}"
        )
    except ValueError:
        pass  # expected for inputs exceeding the cap


# ---------------------------------------------------------------------------
# Property: _mask_quotes output has same length as input
# ---------------------------------------------------------------------------

@given(cmd=_shell_text)
@settings(max_examples=500)
def test_mask_quotes_preserves_length(cmd: str) -> None:
    """_mask_quotes must return a masked string of exactly the same length
    as the original. Length mismatch would corrupt all index-based operations."""
    masked, original = _mask_quotes(cmd)
    assert len(masked) == len(cmd), (
        f"Length mismatch: input={len(cmd)}, masked={len(masked)}"
    )
    assert original == cmd, "_mask_quotes must return the original string unchanged"


# ---------------------------------------------------------------------------
# Property: splitting on N operators produces at most N+1 segments
# ---------------------------------------------------------------------------

@given(
    parts=st.lists(_short_shell, min_size=2, max_size=6),
    op=_operators,
)
@settings(max_examples=500)
def test_operator_splitting_produces_expected_segments(
    parts: list[str], op: str,
) -> None:
    """Joining N non-empty parts with an operator should yield >= N simple
    segments (some may be empty and get filtered, subshells add more)."""
    # Filter out parts that themselves contain operators or quotes
    clean_parts = [
        p for p in parts
        if p.strip()
        and "&&" not in p and "||" not in p and "|" not in p and ";" not in p
        and "'" not in p and '"' not in p
        and "$(" not in p and "`" not in p
        and "#" not in p
    ]
    assume(len(clean_parts) >= 2)

    cmd = f" {op} ".join(clean_parts)
    try:
        segments = tokenize(cmd)
    except ValueError:
        return

    # At minimum, we should get as many segments as non-empty parts
    non_empty_parts = [p for p in clean_parts if p.strip()]
    simple_segments = [s for s in segments if s.seg_type == SegmentType.SIMPLE]
    assert len(simple_segments) >= len(non_empty_parts), (
        f"Expected >= {len(non_empty_parts)} simple segments from "
        f"{len(non_empty_parts)} parts joined by {op!r}, "
        f"got {len(simple_segments)}: {[s.text for s in simple_segments]}"
    )
