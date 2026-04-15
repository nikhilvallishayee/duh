"""Semantic exit codes for CI/CD integration.

Standard codes so CI pipelines can distinguish outcomes:

    0   SUCCESS          Task completed successfully
    1   ERROR            General error
    2   NEEDS_HUMAN      Agent needs human input (permission denied, ambiguous task)
    3   BUDGET_EXCEEDED  Cost or turn limit hit
    4   PROVIDER_ERROR   API/provider failure
   10   TIMEOUT          Max turns or wall-clock timeout
   11   PARTIAL          Some work done but task incomplete
   12   CONTEXT_FULL     Context window exhausted
"""

from __future__ import annotations

SUCCESS = 0
ERROR = 1
NEEDS_HUMAN = 2
BUDGET_EXCEEDED = 3
PROVIDER_ERROR = 4
TIMEOUT = 10
PARTIAL = 11
CONTEXT_FULL = 12

# Patterns in error text that indicate a provider-level failure
_PROVIDER_ERROR_PATTERNS: tuple[str, ...] = (
    "rate_limit",
    "overloaded",
    "authentication_error",
    "invalid x-api-key",
    "Could not resolve authentication",
    "credit balance is too low",
)

# Patterns that indicate context window exhaustion
_CONTEXT_FULL_PATTERNS: tuple[str, ...] = (
    "prompt is too long",
    "context window",
    "context_length_exceeded",
)


def classify_error(error_text: str) -> int:
    """Return the appropriate exit code for an error string.

    Checks against known provider and context-window patterns; falls back
    to the generic ERROR code.
    """
    lower = error_text.lower()
    for pat in _CONTEXT_FULL_PATTERNS:
        if pat.lower() in lower:
            return CONTEXT_FULL
    for pat in _PROVIDER_ERROR_PATTERNS:
        if pat.lower() in lower:
            return PROVIDER_ERROR
    return ERROR
