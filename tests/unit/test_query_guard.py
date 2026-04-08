"""Tests for QueryGuard concurrent query state machine."""
import pytest
from duh.kernel.query_guard import QueryGuard, QueryState


def test_initial_state():
    guard = QueryGuard()
    assert guard.state == QueryState.IDLE
    assert guard.generation == 0


def test_reserve():
    guard = QueryGuard()
    gen = guard.reserve()
    assert gen == 1
    assert guard.state == QueryState.DISPATCHING


def test_reserve_while_busy():
    guard = QueryGuard()
    guard.reserve()
    with pytest.raises(RuntimeError, match="not idle"):
        guard.reserve()


def test_try_start():
    guard = QueryGuard()
    gen = guard.reserve()
    result = guard.try_start(gen)
    assert result == gen
    assert guard.state == QueryState.RUNNING


def test_try_start_stale_generation():
    guard = QueryGuard()
    gen = guard.reserve()
    guard.force_end()  # bumps generation
    assert guard.try_start(gen) is None


def test_end():
    guard = QueryGuard()
    gen = guard.reserve()
    guard.try_start(gen)
    assert guard.end(gen) is True
    assert guard.state == QueryState.IDLE


def test_end_stale_generation():
    guard = QueryGuard()
    gen = guard.reserve()
    guard.try_start(gen)
    guard.force_end()
    assert guard.end(gen) is False


def test_force_end():
    guard = QueryGuard()
    guard.reserve()
    guard.force_end()
    assert guard.state == QueryState.IDLE
    assert guard.generation == 2  # reserve bumped to 1, force_end bumps to 2
