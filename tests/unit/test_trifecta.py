"""Tests for the lethal trifecta capability matrix (ADR-054, 7.3)."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from duh.security.trifecta import (
    Capability,
    LETHAL_TRIFECTA,
    LethalTrifectaError,
    check_trifecta,
    compute_session_capabilities,
)


# ---------------------------------------------------------------------------
# Task 7.3.1: Capability flags + LETHAL_TRIFECTA
# ---------------------------------------------------------------------------


def test_capability_flags_are_distinct() -> None:
    flags = [
        Capability.READ_PRIVATE,
        Capability.READ_UNTRUSTED,
        Capability.NETWORK_EGRESS,
        Capability.FS_WRITE,
        Capability.EXEC,
    ]
    for i, a in enumerate(flags):
        for b in flags[i + 1:]:
            assert a & b == Capability.NONE, f"{a} overlaps {b}"


def test_lethal_trifecta_is_three_flags() -> None:
    assert LETHAL_TRIFECTA == (
        Capability.READ_PRIVATE | Capability.READ_UNTRUSTED | Capability.NETWORK_EGRESS
    )


def test_none_is_zero() -> None:
    assert Capability.NONE.value == 0


# ---------------------------------------------------------------------------
# Task 7.3.2: compute_session_capabilities
# ---------------------------------------------------------------------------


@dataclass
class _FakeTool:
    name: str
    capabilities: Capability


def test_compute_session_caps_empty() -> None:
    assert compute_session_capabilities([]) == Capability.NONE


def test_compute_session_caps_single() -> None:
    tool = _FakeTool(name="Read", capabilities=Capability.READ_PRIVATE)
    assert compute_session_capabilities([tool]) == Capability.READ_PRIVATE


def test_compute_session_caps_union() -> None:
    tools = [
        _FakeTool(name="Read", capabilities=Capability.READ_PRIVATE),
        _FakeTool(
            name="WebFetch",
            capabilities=Capability.READ_UNTRUSTED | Capability.NETWORK_EGRESS,
        ),
    ]
    result = compute_session_capabilities(tools)
    assert result == (
        Capability.READ_PRIVATE | Capability.READ_UNTRUSTED | Capability.NETWORK_EGRESS
    )


# ---------------------------------------------------------------------------
# Task 7.3.3: check_trifecta
# ---------------------------------------------------------------------------


def test_check_trifecta_raises_when_all_three_active() -> None:
    caps = Capability.READ_PRIVATE | Capability.READ_UNTRUSTED | Capability.NETWORK_EGRESS
    with pytest.raises(LethalTrifectaError, match="READ_PRIVATE"):
        check_trifecta(caps, acknowledged=False)


def test_check_trifecta_silent_when_acknowledged() -> None:
    caps = Capability.READ_PRIVATE | Capability.READ_UNTRUSTED | Capability.NETWORK_EGRESS
    check_trifecta(caps, acknowledged=True)  # should not raise


def test_check_trifecta_ok_missing_one() -> None:
    caps = Capability.READ_PRIVATE | Capability.READ_UNTRUSTED  # no NETWORK_EGRESS
    check_trifecta(caps, acknowledged=False)  # should not raise


def test_check_trifecta_ok_none() -> None:
    check_trifecta(Capability.NONE, acknowledged=False)  # should not raise
