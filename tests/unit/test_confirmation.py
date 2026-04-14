"""Tests for HMAC-bound confirmation token minting and validation."""

from __future__ import annotations

import time

import pytest

from duh.kernel.confirmation import ConfirmationMinter


@pytest.fixture()
def minter() -> ConfirmationMinter:
    return ConfirmationMinter(session_key=b"test-key-32-bytes-long-padding!!")


def test_mint_returns_prefixed_token(minter: ConfirmationMinter) -> None:
    token = minter.mint("sess-1", "Bash", {"command": "ls"})
    assert token.startswith("duh-confirm-")
    assert len(token) > 20


def test_validate_accepts_fresh_token(minter: ConfirmationMinter) -> None:
    token = minter.mint("sess-1", "Bash", {"command": "ls"})
    assert minter.validate(token, "sess-1", "Bash", {"command": "ls"}) is True


def test_validate_rejects_replay(minter: ConfirmationMinter) -> None:
    token = minter.mint("sess-1", "Bash", {"command": "ls"})
    minter.validate(token, "sess-1", "Bash", {"command": "ls"})  # consume
    assert minter.validate(token, "sess-1", "Bash", {"command": "ls"}) is False


def test_validate_rejects_wrong_session(minter: ConfirmationMinter) -> None:
    token = minter.mint("sess-1", "Bash", {"command": "ls"})
    assert minter.validate(token, "sess-2", "Bash", {"command": "ls"}) is False


def test_validate_rejects_wrong_tool(minter: ConfirmationMinter) -> None:
    token = minter.mint("sess-1", "Bash", {"command": "ls"})
    assert minter.validate(token, "sess-1", "Write", {"command": "ls"}) is False


def test_validate_rejects_wrong_input(minter: ConfirmationMinter) -> None:
    token = minter.mint("sess-1", "Bash", {"command": "ls"})
    assert minter.validate(token, "sess-1", "Bash", {"command": "rm -rf /"}) is False


def test_validate_rejects_garbage() -> None:
    m = ConfirmationMinter(session_key=b"x" * 32)
    assert m.validate("not-a-token", "s", "t", {}) is False
    assert m.validate("", "s", "t", {}) is False


def test_validate_rejects_expired_token(minter: ConfirmationMinter) -> None:
    import hashlib
    import hmac as _hmac
    import json
    ts = int(time.time()) - 301  # expired
    input_hash = hashlib.sha256(json.dumps({"command": "ls"}, sort_keys=True).encode()).hexdigest()
    payload = f"sess-1|Bash|{input_hash}|{ts}"
    sig = _hmac.new(b"test-key-32-bytes-long-padding!!", payload.encode(), hashlib.sha256).hexdigest()[:16]
    expired_token = f"duh-confirm-{ts}-{sig}"
    assert minter.validate(expired_token, "sess-1", "Bash", {"command": "ls"}) is False
