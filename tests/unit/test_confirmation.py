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
    # Token with correct prefix but non-integer timestamp — hits ValueError branch
    assert m.validate("duh-confirm-NOTANINT-abc", "s", "t", {}) is False


from duh.security.policy import DANGEROUS_TOOLS, any_tainted
from duh.kernel.untrusted import TaintSource, UntrustedStr


def test_dangerous_tools_contains_known_dangerous() -> None:
    for name in ("Bash", "Write", "Edit", "MultiEdit", "NotebookEdit",
                 "WebFetch", "Docker", "HTTP"):
        assert name in DANGEROUS_TOOLS, f"{name} missing from DANGEROUS_TOOLS"


def test_any_tainted_with_all_untainted() -> None:
    chain = [
        UntrustedStr("a", TaintSource.USER_INPUT),
        UntrustedStr("b", TaintSource.SYSTEM),
    ]
    assert any_tainted(chain) is False


def test_any_tainted_with_one_tainted() -> None:
    chain = [
        UntrustedStr("a", TaintSource.USER_INPUT),
        UntrustedStr("b", TaintSource.MODEL_OUTPUT),
    ]
    assert any_tainted(chain) is True


def test_any_tainted_with_plain_str() -> None:
    # Plain str has no source — treated as untainted (SYSTEM default)
    assert any_tainted(["plain"]) is False


from duh.security.policy import resolve_confirmation


def test_resolve_blocks_tainted_bash_without_token() -> None:
    chain = [UntrustedStr("do rm -rf /", TaintSource.MODEL_OUTPUT)]
    result = resolve_confirmation(
        tool="Bash",
        input_obj={"command": "rm -rf /"},
        chain=chain,
        minter=ConfirmationMinter(session_key=b"k" * 32),
        session_id="sess-1",
        token=None,
    )
    assert result.action == "block"
    assert "confirmation" in result.reason.lower()


def test_resolve_allows_tainted_bash_with_valid_token() -> None:
    m = ConfirmationMinter(session_key=b"k" * 32)
    inp = {"command": "rm -rf /"}
    token = m.mint("sess-1", "Bash", inp)
    chain = [UntrustedStr("do rm -rf /", TaintSource.MODEL_OUTPUT)]
    result = resolve_confirmation(
        tool="Bash", input_obj=inp, chain=chain,
        minter=m, session_id="sess-1", token=token,
    )
    assert result.action == "allow"


def test_resolve_allows_untainted_bash_without_token() -> None:
    chain = [UntrustedStr("user said ls", TaintSource.USER_INPUT)]
    result = resolve_confirmation(
        tool="Bash",
        input_obj={"command": "ls"},
        chain=chain,
        minter=ConfirmationMinter(session_key=b"k" * 32),
        session_id="sess-1",
        token=None,
    )
    assert result.action == "allow"


def test_resolve_allows_non_dangerous_tool_without_token() -> None:
    chain = [UntrustedStr("model output", TaintSource.MODEL_OUTPUT)]
    result = resolve_confirmation(
        tool="Read",
        input_obj={"file_path": "/tmp/x"},
        chain=chain,
        minter=ConfirmationMinter(session_key=b"k" * 32),
        session_id="sess-1",
        token=None,
    )
    assert result.action == "allow"


def test_repl_continue_mints_token() -> None:
    from duh.cli.repl import _mint_continue_token
    m = ConfirmationMinter(session_key=b"k" * 32)
    token = _mint_continue_token(m, "sess-1", "Bash", {"command": "ls"})
    assert token.startswith("duh-confirm-")
    assert m.validate(token, "sess-1", "Bash", {"command": "ls"})


def test_ask_user_tool_mints_token() -> None:
    from duh.tools.ask_user_tool import _mint_answer_token
    m = ConfirmationMinter(session_key=b"k" * 32)
    token = _mint_answer_token(m, "sess-1", "Write", {"file_path": "/tmp/x", "content": "y"})
    assert token.startswith("duh-confirm-")
    assert m.validate(token, "sess-1", "Write", {"file_path": "/tmp/x", "content": "y"})


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
