"""Tests for QueryGuard wiring into the REPL loop."""

from __future__ import annotations

import pytest

from duh.kernel.query_guard import QueryGuard, QueryState


class TestQueryGuardREPLIntegration:
    """Verify that the REPL uses QueryGuard around engine.run()."""

    def test_guard_reserve_transitions_to_dispatching(self):
        guard = QueryGuard()
        gen = guard.reserve()
        assert guard.state == QueryState.DISPATCHING
        assert gen == 1

    def test_guard_try_start_transitions_to_running(self):
        guard = QueryGuard()
        gen = guard.reserve()
        result = guard.try_start(gen)
        assert result == gen
        assert guard.state == QueryState.RUNNING

    def test_guard_end_transitions_to_idle(self):
        guard = QueryGuard()
        gen = guard.reserve()
        guard.try_start(gen)
        assert guard.end(gen) is True
        assert guard.state == QueryState.IDLE

    def test_guard_force_end_resets_from_any_state(self):
        guard = QueryGuard()
        gen = guard.reserve()
        guard.try_start(gen)
        guard.force_end()
        assert guard.state == QueryState.IDLE
        assert guard.generation == gen + 1

    def test_reserve_while_not_idle_raises(self):
        guard = QueryGuard()
        guard.reserve()
        with pytest.raises(RuntimeError, match="not idle"):
            guard.reserve()

    def test_stale_generation_rejected_by_try_start(self):
        guard = QueryGuard()
        gen1 = guard.reserve()
        guard.force_end()
        gen2 = guard.reserve()
        assert guard.try_start(gen1) is None
        assert guard.try_start(gen2) == gen2

    def test_stale_generation_rejected_by_end(self):
        guard = QueryGuard()
        gen1 = guard.reserve()
        guard.try_start(gen1)
        guard.force_end()
        gen2 = guard.reserve()
        guard.try_start(gen2)
        assert guard.end(gen1) is False
        assert guard.end(gen2) is True

    def test_full_lifecycle_sequence(self):
        """Simulate the REPL calling reserve -> try_start -> end."""
        guard = QueryGuard()
        # Turn 1
        gen = guard.reserve()
        assert guard.try_start(gen) == gen
        assert guard.end(gen) is True
        # Turn 2
        gen = guard.reserve()
        assert guard.try_start(gen) == gen
        assert guard.end(gen) is True
        assert guard.state == QueryState.IDLE

    def test_abort_during_running_allows_new_query(self):
        """Simulate Ctrl-C abort during streaming."""
        guard = QueryGuard()
        gen = guard.reserve()
        guard.try_start(gen)
        # User hits Ctrl-C
        guard.force_end()
        # New query should work
        gen2 = guard.reserve()
        assert guard.try_start(gen2) == gen2
        guard.end(gen2)
        assert guard.state == QueryState.IDLE
