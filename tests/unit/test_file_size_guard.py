"""Unit tests for ``duh.kernel.file_size_guard``.

These tests verify the char-to-token estimate, the 50%-of-context budget,
and the graceful-fallback behaviour of ``check_file_size`` independent of
any tool wiring.
"""

from __future__ import annotations

import os

import pytest

from duh.kernel.file_size_guard import (
    CHARS_PER_TOKEN,
    MAX_FILE_FRACTION,
    FileSizeDecision,
    check_file_size,
)
from duh.kernel.model_caps import get_capabilities


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Model with a known small context window (gpt-4o = 128K).
SMALL_CTX_MODEL = "gpt-4o"
SMALL_CTX = 128_000

# Model with a 200K context window (claude-sonnet-4).
MID_CTX_MODEL = "claude-sonnet-4"
MID_CTX = 200_000

# Model with a 1M context window (claude-opus-4-6).
LARGE_CTX_MODEL = "claude-opus-4-6"
LARGE_CTX = 1_000_000


# ---------------------------------------------------------------------------
# Basic budget math
# ---------------------------------------------------------------------------


def test_small_file_is_allowed(tmp_path):
    """A 1 KB file on a 200K-context model is well within budget."""
    f = tmp_path / "tiny.txt"
    f.write_text("hello\n" * 100)  # ~600 bytes

    decision = check_file_size(str(f), MID_CTX_MODEL)
    assert decision.allowed is True
    assert decision.reason == ""
    assert decision.budget_tokens == int(MID_CTX * MAX_FILE_FRACTION)


def test_400kb_file_on_128k_context_is_refused():
    """400 KB → ~102K tokens, which exceeds 50% of the 128K gpt-4o window
    (64K-token budget)."""
    decision = check_file_size(
        "ignored-path", SMALL_CTX_MODEL, size_bytes=400 * 1024
    )
    assert decision.allowed is False
    assert "exceeds" in decision.reason
    assert "context window" in decision.reason
    assert str(SMALL_CTX_MODEL) in decision.reason
    # 400 KB / 4 chars per token = 102,400 estimated tokens
    assert decision.estimated_tokens == (400 * 1024) // CHARS_PER_TOKEN


def test_50kb_file_on_200k_context_is_allowed():
    """50 KB → ~12,800 tokens; ~13% of 200K is well under the 50% budget."""
    decision = check_file_size(
        "ignored-path", MID_CTX_MODEL, size_bytes=50 * 1024
    )
    assert decision.allowed is True
    assert decision.estimated_tokens == (50 * 1024) // CHARS_PER_TOKEN
    assert decision.budget_tokens == MID_CTX // 2


def test_600kb_file_on_1m_context_is_allowed():
    """600 KB → 150K tokens; well under 500K token budget for 1M-context."""
    decision = check_file_size(
        "ignored-path", LARGE_CTX_MODEL, size_bytes=600 * 1024
    )
    assert decision.allowed is True
    assert decision.estimated_tokens == (600 * 1024) // CHARS_PER_TOKEN
    assert decision.budget_tokens == LARGE_CTX // 2


# ---------------------------------------------------------------------------
# Boundary conditions
# ---------------------------------------------------------------------------


def test_exact_budget_is_allowed():
    """A file estimated at exactly the budget is allowed (strict >)."""
    caps = get_capabilities(MID_CTX_MODEL)
    budget_bytes = int(caps.context_window * MAX_FILE_FRACTION) * CHARS_PER_TOKEN
    decision = check_file_size("p", MID_CTX_MODEL, size_bytes=budget_bytes)
    assert decision.allowed is True


def test_one_token_over_budget_is_refused():
    """Just over the budget trips the refusal."""
    caps = get_capabilities(MID_CTX_MODEL)
    over_bytes = (int(caps.context_window * MAX_FILE_FRACTION) + 1) * CHARS_PER_TOKEN
    decision = check_file_size("p", MID_CTX_MODEL, size_bytes=over_bytes)
    assert decision.allowed is False


# ---------------------------------------------------------------------------
# Custom fraction parameter
# ---------------------------------------------------------------------------


def test_custom_fraction_tightens_budget():
    """A 25% fraction should refuse files that 50% would allow."""
    size = 40 * 1024  # 10K tokens
    # At 50% of 200K = 100K budget — allowed.
    assert check_file_size("p", MID_CTX_MODEL, size_bytes=size).allowed is True
    # At 10% of 200K = 20K budget — still allowed (10K < 20K).
    assert (
        check_file_size("p", MID_CTX_MODEL, size_bytes=size, fraction=0.1).allowed
        is True
    )
    # At 2% of 200K = 4K budget — refused (10K > 4K).
    decision = check_file_size("p", MID_CTX_MODEL, size_bytes=size, fraction=0.02)
    assert decision.allowed is False
    assert "2%" in decision.reason


# ---------------------------------------------------------------------------
# Unknown-model fallback & stat behaviour
# ---------------------------------------------------------------------------


def test_unknown_model_falls_back_to_default_200k_context():
    """Unknown model names get the conservative 200K default."""
    # 40 KB → 10K tokens, ~10% of 200K → allowed
    assert (
        check_file_size("p", "totally-made-up-model-v999", size_bytes=40 * 1024).allowed
        is True
    )
    # 500 KB → 125K tokens > 100K budget → refused
    decision = check_file_size(
        "p", "totally-made-up-model-v999", size_bytes=500 * 1024
    )
    assert decision.allowed is False
    assert decision.budget_tokens == 100_000  # 50% of 200K default


def test_stat_failure_returns_allowed():
    """If stat() raises (missing file, bad permissions), we allow through
    so the real read error is surfaced by the caller."""
    decision = check_file_size(
        "/nonexistent/path/that/cannot/exist.xyz", MID_CTX_MODEL
    )
    assert decision.allowed is True
    assert decision.reason == ""
    assert decision.estimated_tokens == 0


def test_stat_uses_real_file_when_size_not_given(tmp_path):
    """When size_bytes is None, the function stats the file."""
    f = tmp_path / "a.txt"
    f.write_text("x" * 1024)
    decision = check_file_size(str(f), MID_CTX_MODEL)
    assert decision.allowed is True
    assert decision.estimated_tokens == 1024 // CHARS_PER_TOKEN


# ---------------------------------------------------------------------------
# Decision dataclass
# ---------------------------------------------------------------------------


def test_decision_is_frozen_dataclass():
    """FileSizeDecision is immutable."""
    d = FileSizeDecision(True, "", 0, 0)
    with pytest.raises((AttributeError, Exception)):
        d.allowed = False  # type: ignore[misc]


def test_reason_message_is_actionable():
    """The refusal reason must mention offset/limit AND a model-swap option."""
    decision = check_file_size("p", SMALL_CTX_MODEL, size_bytes=500 * 1024)
    assert not decision.allowed
    assert "offset/limit" in decision.reason or "offset" in decision.reason
    assert "model" in decision.reason.lower()


def test_reason_reports_actual_byte_and_token_counts():
    """The user-facing message includes both byte count and token estimate."""
    size = 500 * 1024  # 128K tokens — > 64K budget on gpt-4o
    decision = check_file_size("p", SMALL_CTX_MODEL, size_bytes=size)
    assert not decision.allowed
    # Either formatted with commas or plain — check both the byte and token
    # values appear (commas since we format ``{:,}``).
    assert f"{size:,}" in decision.reason
    assert f"{size // CHARS_PER_TOKEN:,}" in decision.reason
