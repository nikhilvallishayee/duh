"""Tests for duh.cli.exit_codes — semantic exit codes and classify_error."""

from __future__ import annotations

from duh.cli.exit_codes import (
    SUCCESS,
    ERROR,
    NEEDS_HUMAN,
    BUDGET_EXCEEDED,
    PROVIDER_ERROR,
    TIMEOUT,
    PARTIAL,
    CONTEXT_FULL,
    classify_error,
)


# ------------------------------------------------------------------
# Constants have correct values
# ------------------------------------------------------------------

class TestExitCodeValues:
    def test_success(self):
        assert SUCCESS == 0

    def test_error(self):
        assert ERROR == 1

    def test_needs_human(self):
        assert NEEDS_HUMAN == 2

    def test_budget_exceeded(self):
        assert BUDGET_EXCEEDED == 3

    def test_provider_error(self):
        assert PROVIDER_ERROR == 4

    def test_timeout(self):
        assert TIMEOUT == 10

    def test_partial(self):
        assert PARTIAL == 11

    def test_context_full(self):
        assert CONTEXT_FULL == 12

    def test_all_unique(self):
        codes = [SUCCESS, ERROR, NEEDS_HUMAN, BUDGET_EXCEEDED,
                 PROVIDER_ERROR, TIMEOUT, PARTIAL, CONTEXT_FULL]
        assert len(codes) == len(set(codes))


# ------------------------------------------------------------------
# classify_error
# ------------------------------------------------------------------

class TestClassifyError:
    def test_generic_error_returns_error(self):
        assert classify_error("something went wrong") == ERROR

    def test_empty_string_returns_error(self):
        assert classify_error("") == ERROR

    def test_rate_limit_returns_provider_error(self):
        assert classify_error("rate_limit: too many requests") == PROVIDER_ERROR

    def test_overloaded_returns_provider_error(self):
        assert classify_error("API is overloaded right now") == PROVIDER_ERROR

    def test_authentication_error_returns_provider_error(self):
        assert classify_error("authentication_error: bad key") == PROVIDER_ERROR

    def test_invalid_api_key_returns_provider_error(self):
        assert classify_error("invalid x-api-key header") == PROVIDER_ERROR

    def test_auth_resolution_returns_provider_error(self):
        assert classify_error("Could not resolve authentication for provider") == PROVIDER_ERROR

    def test_credit_balance_returns_provider_error(self):
        assert classify_error("Your credit balance is too low") == PROVIDER_ERROR

    def test_prompt_too_long_returns_context_full(self):
        assert classify_error("prompt is too long for model") == CONTEXT_FULL

    def test_context_window_returns_context_full(self):
        assert classify_error("exceeded the context window") == CONTEXT_FULL

    def test_context_length_exceeded_returns_context_full(self):
        assert classify_error("context_length_exceeded") == CONTEXT_FULL

    def test_case_insensitive(self):
        assert classify_error("RATE_LIMIT reached") == PROVIDER_ERROR
        assert classify_error("PROMPT IS TOO LONG") == CONTEXT_FULL

    def test_context_full_takes_priority_over_provider(self):
        """If an error mentions both patterns, context_full wins
        (it is checked first)."""
        assert classify_error("prompt is too long, also rate_limit") == CONTEXT_FULL
