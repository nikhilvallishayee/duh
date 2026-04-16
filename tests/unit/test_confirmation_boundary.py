"""Boundary tests for confirmation token expiry (ADR-054, 7.2).

ConfirmationMinter.validate() rejects tokens older than 300 seconds.
These tests nail down the exact inclusive/exclusive semantics of the
boundary so a refactor of the comparison operator (``>`` vs ``>=``)
cannot regress silently.

Note on the clock:
~~~~~~~~~~~~~~~~~~
``confirmation.py`` reads the wall clock via ``time.time()``
(not ``time.monotonic()``) because tokens must survive a process
restart. We monkeypatch ``duh.kernel.confirmation.time.time`` so every
assertion is deterministic independent of the real clock.
"""

from __future__ import annotations

import pytest

from duh.kernel.confirmation import ConfirmationMinter


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def minter() -> ConfirmationMinter:
    # Same key shape used elsewhere in the test suite.
    return ConfirmationMinter(session_key=b"test-key-32-bytes-long-padding!!")


class _Clock:
    """Tiny mutable clock for monkeypatching ``time.time()``."""

    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


# ---------------------------------------------------------------------------
# Exact boundary at the 300s expiry window
# ---------------------------------------------------------------------------

class TestExpiryBoundary:
    def test_token_valid_at_zero_seconds(
        self, minter: ConfirmationMinter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Sanity: a just-minted token with no elapsed time is valid."""
        clock = _Clock(now=1_700_000_000.0)
        monkeypatch.setattr("duh.kernel.confirmation.time.time", clock)

        token = minter.mint("sess-1", "Bash", {"command": "ls"})
        assert minter.validate(
            token, "sess-1", "Bash", {"command": "ls"},
        ) is True

    def test_token_valid_at_299_seconds(
        self, minter: ConfirmationMinter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """At exactly 299s elapsed, the token is still fresh
        (expiry check is ``elapsed > 300``)."""
        clock = _Clock(now=1_700_000_000.0)
        monkeypatch.setattr("duh.kernel.confirmation.time.time", clock)

        token = minter.mint("sess-1", "Bash", {"command": "ls"})
        clock.now += 299.0

        assert minter.validate(
            token, "sess-1", "Bash", {"command": "ls"},
        ) is True

    def test_token_valid_at_exactly_300_seconds(
        self, minter: ConfirmationMinter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """At exactly 300.0s elapsed the check ``time.time() - ts > 300``
        is False, so the token is still accepted. This pins down the
        inclusive vs exclusive boundary: the 300th second is inside the
        window."""
        clock = _Clock(now=1_700_000_000.0)
        monkeypatch.setattr("duh.kernel.confirmation.time.time", clock)

        token = minter.mint("sess-1", "Bash", {"command": "ls"})
        clock.now += 300.0

        # ``elapsed == 300`` → ``300 > 300`` is False → still valid.
        assert minter.validate(
            token, "sess-1", "Bash", {"command": "ls"},
        ) is True

    def test_token_invalid_at_300_point_001_seconds(
        self, minter: ConfirmationMinter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Just past 300s the token must be rejected — the expiry
        check fires for any elapsed time strictly greater than 300s.

        Because the minter coerces its timestamp to ``int`` but the
        validator compares on the current wall clock as a float, we
        bump the clock by a full second to land unambiguously past
        the boundary regardless of truncation direction.
        """
        clock = _Clock(now=1_700_000_000.0)
        monkeypatch.setattr("duh.kernel.confirmation.time.time", clock)

        token = minter.mint("sess-1", "Bash", {"command": "ls"})
        clock.now += 301.0  # 1s past the boundary

        assert minter.validate(
            token, "sess-1", "Bash", {"command": "ls"},
        ) is False

    def test_token_invalid_well_past_expiry(
        self, minter: ConfirmationMinter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Tokens aged an hour past expiry are rejected. Guards
        against accidental removal of the expiry clause."""
        clock = _Clock(now=1_700_000_000.0)
        monkeypatch.setattr("duh.kernel.confirmation.time.time", clock)

        token = minter.mint("sess-1", "Bash", {"command": "ls"})
        clock.now += 3600.0  # one hour later

        assert minter.validate(
            token, "sess-1", "Bash", {"command": "ls"},
        ) is False


# ---------------------------------------------------------------------------
# Determinism sanity: validate uses the monkeypatched clock
# ---------------------------------------------------------------------------

class TestDeterministicClock:
    def test_fresh_token_rejected_when_clock_jumps_forward(
        self, minter: ConfirmationMinter,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Proof that the monkeypatch actually drives validate(): we
        mint at t=T, then jump the clock to T+10_000 and assert the
        token is now expired. If the patch did nothing, a fresh token
        would still be valid."""
        clock = _Clock(now=1_700_000_000.0)
        monkeypatch.setattr("duh.kernel.confirmation.time.time", clock)

        token = minter.mint("sess-1", "Bash", {"command": "ls"})
        clock.now += 10_000.0

        assert minter.validate(
            token, "sess-1", "Bash", {"command": "ls"},
        ) is False
