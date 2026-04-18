"""Tests confirming engine.py and file_size_guard.py use the same
``CHARS_PER_TOKEN`` constant re-exported from ``duh.kernel.tokens``.

This nails down the consolidation done in the drift-risk refactor: there
must be exactly one source of truth for the "1 token ≈ 4 chars" heuristic,
with engine.py and file_size_guard.py pulling from it.
"""

from __future__ import annotations


def test_engine_imports_canonical_chars_per_token() -> None:
    """Engine's USAGE_DELTA_CHARS_PER_TOKEN is the canonical value."""
    from duh.kernel.engine import CHARS_PER_TOKEN, USAGE_DELTA_CHARS_PER_TOKEN
    from duh.kernel.tokens import CHARS_PER_TOKEN as TOKENS_CPT
    # All three references resolve to the same integer object.
    assert CHARS_PER_TOKEN is TOKENS_CPT
    assert USAGE_DELTA_CHARS_PER_TOKEN == TOKENS_CPT


def test_file_size_guard_imports_canonical_chars_per_token() -> None:
    """File-size-guard's CHARS_PER_TOKEN re-exports the canonical value."""
    from duh.kernel.file_size_guard import CHARS_PER_TOKEN as GUARD_CPT
    from duh.kernel.tokens import CHARS_PER_TOKEN as TOKENS_CPT
    assert GUARD_CPT is TOKENS_CPT


def test_all_chars_per_token_identical() -> None:
    """No call-site has drifted: every copy of the constant is the same int."""
    from duh.kernel.engine import USAGE_DELTA_CHARS_PER_TOKEN
    from duh.kernel.file_size_guard import CHARS_PER_TOKEN as GUARD_CPT
    from duh.kernel.tokens import CHARS_PER_TOKEN as TOKENS_CPT
    assert TOKENS_CPT == USAGE_DELTA_CHARS_PER_TOKEN == GUARD_CPT == 4
