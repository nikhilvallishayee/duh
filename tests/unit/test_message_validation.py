"""Tests for message validation and repair before API calls."""

from __future__ import annotations

from duh.kernel.messages import Message, validate_alternation, merge_consecutive


def test_valid_alternation_passes() -> None:
    msgs = [
        Message(role="user", content="hello"),
        Message(role="assistant", content="hi"),
        Message(role="user", content="bye"),
    ]
    result = validate_alternation(msgs)
    assert result == msgs


def test_consecutive_assistant_merged() -> None:
    msgs = [
        Message(role="user", content="hello"),
        Message(role="assistant", content="part 1"),
        Message(role="assistant", content="part 2"),
        Message(role="assistant", content="part 3"),
        Message(role="user", content="thanks"),
    ]
    result = merge_consecutive(msgs)
    assert len(result) == 3
    assert result[0].role == "user"
    assert result[1].role == "assistant"
    assert "part 1" in str(result[1].content)
    assert "part 3" in str(result[1].content)
    assert result[2].role == "user"


def test_consecutive_user_merged() -> None:
    msgs = [
        Message(role="user", content="hi"),
        Message(role="user", content="also this"),
        Message(role="assistant", content="ok"),
    ]
    result = merge_consecutive(msgs)
    assert len(result) == 2
    assert result[0].role == "user"
    assert "hi" in str(result[0].content)
    assert "also this" in str(result[0].content)


def test_single_message_unchanged() -> None:
    msgs = [Message(role="user", content="hi")]
    assert merge_consecutive(msgs) == msgs


def test_empty_list() -> None:
    assert merge_consecutive([]) == []


def test_validate_adds_missing_user() -> None:
    """If session starts with assistant, prepend a user message."""
    msgs = [
        Message(role="assistant", content="I was mid-thought"),
        Message(role="user", content="continue"),
    ]
    result = validate_alternation(msgs)
    assert result[0].role == "user"
    assert len(result) >= 2


def test_merge_then_validate_produces_valid_sequence() -> None:
    """The full pipeline: merge consecutive, then validate alternation."""
    msgs = [
        Message(role="user", content="start"),
        Message(role="assistant", content="a1"),
        Message(role="assistant", content="a2"),
        Message(role="assistant", content="a3"),
        Message(role="user", content="ok"),
        Message(role="assistant", content="done"),
    ]
    merged = merge_consecutive(msgs)
    valid = validate_alternation(merged)
    # Must strictly alternate
    for i in range(1, len(valid)):
        assert valid[i].role != valid[i - 1].role, (
            f"Consecutive {valid[i].role} at [{i-1}] and [{i}]"
        )
