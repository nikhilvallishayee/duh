"""Secrets redaction — strip sensitive values from text before it reaches the model.

Catches common secret patterns:
- API keys: sk-ant-*, sk-proj-*, sk-*, AKIA*, ghp_*, gho_*, ghs_*
- Bearer tokens
- Private keys (PEM blocks)
- Passwords in URLs
- Generic secret/key/token assignments

    from duh.kernel.redact import redact_secrets
    safe_text = redact_secrets(tool_output)
"""

from __future__ import annotations

import re

REDACTED = "[REDACTED]"

# Cheap substring screen for the generic-assignment pattern.
# The full regex below has a `[A-Za-z0-9_-]*` prefix that is *very* expensive
# on long inputs without any of these substrings (catastrophic backtracking on
# 100KB of repeated characters). Searching for the literal first costs a
# straight memchr scan and short-circuits the whole pattern when none are
# present.
_GENERIC_KEYWORDS = (
    "secret",
    "api_key",
    "apikey",
    "api-key",
    "token",
    "password",
    "passwd",
    "credential",
    "auth",
)


def _has_generic_keyword(text: str) -> bool:
    lowered = text.lower()
    return any(kw in lowered for kw in _GENERIC_KEYWORDS)


# Ordered list of (compiled regex, replacement, needs_keyword_screen).
# Order matters: more specific patterns first.
_PATTERNS: list[tuple[re.Pattern[str], str | object, bool]] = [
    # PEM private keys (multi-line)
    (re.compile(
        r"-----BEGIN\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----"
        r"[\s\S]*?"
        r"-----END\s+(?:RSA\s+|EC\s+|DSA\s+|OPENSSH\s+)?PRIVATE\s+KEY-----",
        re.MULTILINE,
    ), REDACTED, False),

    # Anthropic API keys: sk-ant-api03-...
    (re.compile(r"sk-ant-api\d{2}-[A-Za-z0-9_-]+"), REDACTED, False),

    # OpenAI API keys: sk-proj-... or sk-...
    (re.compile(r"sk-proj-[A-Za-z0-9_-]{10,}"), REDACTED, False),
    (re.compile(r"sk-[A-Za-z0-9_-]{20,}"), REDACTED, False),

    # AWS access keys: AKIA...
    (re.compile(r"AKIA[0-9A-Z]{16}"), REDACTED, False),

    # GitHub tokens: ghp_, gho_, ghs_, ghr_
    (re.compile(r"gh[poshr]_[A-Za-z0-9_]{20,}"), REDACTED, False),

    # Bearer tokens
    (re.compile(r"Bearer\s+[A-Za-z0-9._-]{10,}", re.IGNORECASE), f"Bearer {REDACTED}", False),

    # Passwords in URLs: protocol://user:password@host
    (re.compile(r"(://[^:]+:)([^@]+)(@)"), rf"\1{REDACTED}\3", False),

    # Generic secret/key/token/password assignments.
    # Matches: SECRET_KEY="value", api_key=value, TOKEN='value', password: "value".
    # Gated behind a substring screen — see _has_generic_keyword above.
    (re.compile(
        r"(?i)"
        r"(?:[A-Za-z0-9_-]*(?:secret|api[_-]?key|token|password|passwd|credential|auth)[A-Za-z0-9_-]*)"
        r"""(?:\s*[:=]\s*["']?)"""
        r"""([^"'\s,;}{)]+)""",
    ), lambda m: m.group(0).replace(m.group(1), REDACTED), True),  # type: ignore[misc]
]


def redact_secrets(text: str) -> str:
    """Redact secrets from text, returning the sanitized version.

    Applies each pattern in order. Patterns are designed to avoid
    false positives on normal code/prose while catching the most
    common secret formats.
    """
    if not text:
        return text

    result = text
    for pattern, replacement, needs_screen in _PATTERNS:
        if needs_screen and not _has_generic_keyword(result):
            continue
        if callable(replacement):
            result = pattern.sub(replacement, result)
        else:
            result = pattern.sub(replacement, result)

    return result
