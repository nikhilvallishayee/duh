"""Property tests for duh.kernel.redact — secrets redaction engine.

Tests invariants that must hold for *any* input:
- redact_secrets() never raises on arbitrary input
- redact_secrets() is idempotent (applying twice == applying once)
- redact_secrets() output never contains known secret patterns
- No catastrophic backtracking (under 100ms for 10KB input)
- Non-secret content is preserved verbatim
- Redaction never increases the count of secret-like patterns
"""

from __future__ import annotations

import re
import time

from hypothesis import HealthCheck, given, settings, assume, strategies as st

from duh.kernel.redact import REDACTED, redact_secrets, _PATTERNS

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

# General text — covers Unicode, whitespace, punctuation.
_arbitrary_text = st.text(
    alphabet=st.characters(
        whitelist_categories=("L", "N", "P", "S", "Z"),
    ),
    max_size=2000,
)

# Printable ASCII text for targeted tests.
_ascii_text = st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    max_size=2000,
)

# Known secret patterns that redact_secrets must catch.
_anthropic_key = st.from_regex(r"sk-ant-api03-[A-Za-z0-9_-]{20,60}", fullmatch=True)
_openai_proj_key = st.from_regex(r"sk-proj-[A-Za-z0-9_-]{20,60}", fullmatch=True)
_openai_key = st.from_regex(r"sk-[A-Za-z0-9_-]{20,60}", fullmatch=True)
_aws_key = st.from_regex(r"AKIA[0-9A-Z]{16}", fullmatch=True)
_github_token = st.from_regex(r"ghp_[A-Za-z0-9_]{20,40}", fullmatch=True)
_bearer_token = st.builds(
    lambda t: f"Bearer {t}",
    st.from_regex(r"[A-Za-z0-9._-]{10,40}", fullmatch=True),
)

_any_secret = st.one_of(
    _anthropic_key,
    _openai_proj_key,
    _openai_key,
    _aws_key,
    _github_token,
    _bearer_token,
)

# Text that definitely has no secret-like content.
_benign_text = st.text(
    alphabet=st.sampled_from("abcdefghij 0123456789\n\t.,!?()[]{}"),
    min_size=1,
    max_size=500,
)


# ---------------------------------------------------------------------------
# Property: never raises on arbitrary input
# ---------------------------------------------------------------------------

@given(text=_arbitrary_text)
@settings(max_examples=500)
def test_redact_secrets_never_crashes(text: str) -> None:
    """redact_secrets() must not raise on any input."""
    result = redact_secrets(text)
    assert isinstance(result, str)


# ---------------------------------------------------------------------------
# Property: idempotent — redacting twice equals redacting once
# ---------------------------------------------------------------------------

@given(text=_arbitrary_text)
@settings(max_examples=500)
def test_redact_secrets_is_idempotent(text: str) -> None:
    """Applying redact_secrets twice must produce the same output as once.
    If it doesn't, the redacted placeholder is being re-processed incorrectly."""
    once = redact_secrets(text)
    twice = redact_secrets(once)
    assert once == twice, (
        f"Not idempotent:\n  once:  {once[:200]!r}\n  twice: {twice[:200]!r}"
    )


# ---------------------------------------------------------------------------
# Property: output never contains known secret patterns
# ---------------------------------------------------------------------------

@given(
    prefix=_benign_text,
    secret=_any_secret,
    suffix=_benign_text,
)
@settings(max_examples=500)
def test_known_secrets_are_always_redacted(
    prefix: str, secret: str, suffix: str,
) -> None:
    """When a known secret pattern is embedded in text, the output must
    not contain the original secret value."""
    text = f"{prefix} {secret} {suffix}"
    result = redact_secrets(text)
    # The exact secret should not survive redaction
    assert secret not in result, (
        f"Secret {secret[:30]!r}... survived redaction in output: {result[:200]!r}"
    )


# ---------------------------------------------------------------------------
# Property: Anthropic API keys are always caught
# ---------------------------------------------------------------------------

@given(key=_anthropic_key)
@settings(max_examples=500)
def test_anthropic_keys_always_redacted(key: str) -> None:
    """sk-ant-api03-... keys must be replaced with [REDACTED]."""
    result = redact_secrets(f"key={key}")
    assert key not in result
    assert REDACTED in result


# ---------------------------------------------------------------------------
# Property: AWS access keys are always caught
# ---------------------------------------------------------------------------

@given(key=_aws_key)
@settings(max_examples=500)
def test_aws_keys_always_redacted(key: str) -> None:
    """AKIA... keys must be replaced with [REDACTED]."""
    result = redact_secrets(f"aws_access_key_id={key}")
    assert key not in result
    assert REDACTED in result


# ---------------------------------------------------------------------------
# Property: GitHub tokens are always caught
# ---------------------------------------------------------------------------

@given(token=_github_token)
@settings(max_examples=500)
def test_github_tokens_always_redacted(token: str) -> None:
    """ghp_... tokens must be replaced with [REDACTED]."""
    result = redact_secrets(f"GITHUB_TOKEN={token}")
    assert token not in result
    assert REDACTED in result


# ---------------------------------------------------------------------------
# Property: Bearer tokens are always caught
# ---------------------------------------------------------------------------

@given(token=_bearer_token)
@settings(max_examples=500)
def test_bearer_tokens_always_redacted(token: str) -> None:
    """Bearer ... tokens must be replaced."""
    result = redact_secrets(f"Authorization: {token}")
    # The actual token value (after "Bearer ") should not appear
    token_value = token.split(" ", 1)[1]
    assert token_value not in result
    assert REDACTED in result


# ---------------------------------------------------------------------------
# Property: passwords in URLs are always caught
# ---------------------------------------------------------------------------

@given(
    user=st.from_regex(r"[a-z]{3,10}", fullmatch=True),
    password=st.from_regex(r"[A-Za-z0-9!#$%^&*]{5,20}", fullmatch=True),
    host=st.from_regex(r"[a-z]{3,10}\.[a-z]{2,5}", fullmatch=True),
)
@settings(max_examples=500)
def test_url_passwords_always_redacted(
    user: str, password: str, host: str,
) -> None:
    """Passwords in protocol://user:password@host URLs must be redacted."""
    assume("@" not in password)  # @ in password would confuse the regex
    # Ensure password doesn't appear elsewhere in the URL (user, host, scheme)
    # so we can verify it was specifically the password that got redacted.
    assume(password not in user and password not in host and password not in "https")
    url = f"https://{user}:{password}@{host}/path"
    result = redact_secrets(url)
    assert password not in result, (
        f"URL password {password!r} survived redaction: {result!r}"
    )
    assert REDACTED in result


# ---------------------------------------------------------------------------
# Property: no catastrophic backtracking (100ms budget for 10KB input)
# ---------------------------------------------------------------------------

@given(text=st.text(
    alphabet=st.characters(min_codepoint=32, max_codepoint=126),
    min_size=5000,
    max_size=10000,
))
@settings(max_examples=100, suppress_health_check=[HealthCheck.large_base_example])
def test_no_catastrophic_backtracking(text: str) -> None:
    """redact_secrets() must finish within 100ms for 10KB input.
    The generic-assignment pattern previously had catastrophic backtracking
    on long inputs — the keyword screen was added to prevent it."""
    start = time.monotonic()
    redact_secrets(text)
    elapsed = time.monotonic() - start
    assert elapsed < 0.1, (
        f"redact_secrets() took {elapsed*1000:.1f}ms on {len(text)}-byte input "
        f"— possible catastrophic backtracking"
    )


# ---------------------------------------------------------------------------
# Property: benign content is preserved unchanged
# ---------------------------------------------------------------------------

@given(text=_benign_text)
@settings(max_examples=500)
def test_benign_content_preserved(text: str) -> None:
    """Text with no secret-like patterns must pass through unchanged.
    False positives are a usability bug."""
    result = redact_secrets(text)
    assert result == text, (
        f"Benign text was modified:\n  input:  {text[:200]!r}\n  output: {result[:200]!r}"
    )


# ---------------------------------------------------------------------------
# Property: empty and whitespace-only input preserved
# ---------------------------------------------------------------------------

@given(text=st.from_regex(r"\s{0,100}", fullmatch=True))
@settings(max_examples=500)
def test_whitespace_input_preserved(text: str) -> None:
    """Whitespace-only strings (including empty) must survive unchanged."""
    result = redact_secrets(text)
    assert result == text


# ---------------------------------------------------------------------------
# Property: generic assignments with secret keywords are caught
# ---------------------------------------------------------------------------

@given(
    keyword=st.sampled_from([
        "secret", "api_key", "apikey", "api-key",
        "token", "password", "passwd", "credential", "auth",
    ]),
    value=st.from_regex(r"[A-Za-z0-9]{8,30}", fullmatch=True),
)
@settings(max_examples=500)
def test_generic_secret_assignments_redacted(keyword: str, value: str) -> None:
    """SECRET_KEY=value, api_key="value", TOKEN='value' etc. must be caught."""
    # Test multiple assignment formats
    for fmt in [
        f"MY_{keyword.upper()}={value}",
        f'{keyword}="{value}"',
        f"{keyword}: {value}",
    ]:
        result = redact_secrets(fmt)
        assert value not in result, (
            f"Generic secret {value!r} survived in format {fmt!r}: {result!r}"
        )
        assert REDACTED in result


# ---------------------------------------------------------------------------
# Property: PEM private keys are always caught
# ---------------------------------------------------------------------------

@given(body=st.from_regex(r"[A-Za-z0-9+/=\n]{20,100}", fullmatch=True))
@settings(max_examples=500)
def test_pem_private_keys_redacted(body: str) -> None:
    """PEM private key blocks must be fully redacted."""
    pem = f"-----BEGIN RSA PRIVATE KEY-----\n{body}\n-----END RSA PRIVATE KEY-----"
    result = redact_secrets(pem)
    assert "BEGIN" not in result or REDACTED in result
    assert body not in result, (
        f"PEM key body survived redaction: {result[:200]!r}"
    )
