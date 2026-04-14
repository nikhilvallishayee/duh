"""Tests for QueryGuard concurrent query state machine."""
import asyncio
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


# ---------------------------------------------------------------------------
# ADR-033 gap fixes: asyncio.Lock + cancel_on_new
# ---------------------------------------------------------------------------


class TestQueryGuardAsyncLock:
    """QueryGuard must use asyncio.Lock for async-safe state transitions."""

    @pytest.mark.asyncio
    async def test_has_asyncio_lock(self):
        """QueryGuard must have an _lock attribute that is an asyncio.Lock."""
        guard = QueryGuard()
        assert hasattr(guard, "_lock"), "QueryGuard must have a _lock attribute"
        assert isinstance(guard._lock, asyncio.Lock), "_lock must be an asyncio.Lock"

    @pytest.mark.asyncio
    async def test_async_reserve_transitions_to_dispatching(self):
        """async reserve() must work with asyncio.Lock."""
        guard = QueryGuard()
        gen = await guard.async_reserve()
        assert gen == 1
        assert guard.state == QueryState.DISPATCHING

    @pytest.mark.asyncio
    async def test_async_try_start_transitions_to_running(self):
        guard = QueryGuard()
        gen = await guard.async_reserve()
        result = await guard.async_try_start(gen)
        assert result == gen
        assert guard.state == QueryState.RUNNING

    @pytest.mark.asyncio
    async def test_async_end_transitions_to_idle(self):
        guard = QueryGuard()
        gen = await guard.async_reserve()
        await guard.async_try_start(gen)
        result = await guard.async_end(gen)
        assert result is True
        assert guard.state == QueryState.IDLE

    @pytest.mark.asyncio
    async def test_async_force_end_resets_state(self):
        guard = QueryGuard()
        await guard.async_reserve()
        await guard.async_force_end()
        assert guard.state == QueryState.IDLE
        assert guard.generation == 2

    @pytest.mark.asyncio
    async def test_concurrent_reserve_serializes(self):
        """Two concurrent reserve calls must not both succeed."""
        guard = QueryGuard()
        results = []
        errors = []

        async def try_reserve():
            try:
                gen = await guard.async_reserve()
                results.append(gen)
            except RuntimeError as e:
                errors.append(str(e))

        await asyncio.gather(try_reserve(), try_reserve())
        # One succeeds, one raises
        assert len(results) == 1
        assert len(errors) == 1
        assert "not idle" in errors[0]

    @pytest.mark.asyncio
    async def test_full_async_lifecycle(self):
        """Full async reserve → try_start → end cycle."""
        guard = QueryGuard()
        gen = await guard.async_reserve()
        assert await guard.async_try_start(gen) == gen
        assert await guard.async_end(gen) is True
        assert guard.state == QueryState.IDLE

        # Second cycle
        gen2 = await guard.async_reserve()
        assert gen2 == 2
        assert await guard.async_try_start(gen2) == gen2
        assert await guard.async_end(gen2) is True

    @pytest.mark.asyncio
    async def test_stale_async_end_ignored(self):
        """Stale generation async_end returns False."""
        guard = QueryGuard()
        gen = await guard.async_reserve()
        await guard.async_try_start(gen)
        await guard.async_force_end()
        result = await guard.async_end(gen)
        assert result is False


class TestQueryGuardCancelOnNew:
    """cancel_on_new option: when a new query arrives while one is running,
    cancel the in-flight task before starting the new one."""

    @pytest.mark.asyncio
    async def test_cancel_on_new_default_is_false(self):
        """By default, cancel_on_new is False."""
        guard = QueryGuard()
        assert guard.cancel_on_new is False

    @pytest.mark.asyncio
    async def test_cancel_on_new_can_be_set_true(self):
        """cancel_on_new=True can be passed at construction."""
        guard = QueryGuard(cancel_on_new=True)
        assert guard.cancel_on_new is True

    @pytest.mark.asyncio
    async def test_cancel_on_new_false_raises_when_busy(self):
        """With cancel_on_new=False, reserve while running raises RuntimeError."""
        guard = QueryGuard(cancel_on_new=False)
        gen = await guard.async_reserve()
        await guard.async_try_start(gen)
        with pytest.raises(RuntimeError, match="not idle"):
            await guard.async_reserve()

    @pytest.mark.asyncio
    async def test_cancel_on_new_true_aborts_running_query(self):
        """With cancel_on_new=True, a new reserve cancels the in-flight task
        and transitions to DISPATCHING for the new query."""
        guard = QueryGuard(cancel_on_new=True)

        # Simulate running query
        gen1 = await guard.async_reserve()
        await guard.async_try_start(gen1)
        assert guard.state == QueryState.RUNNING

        # New query comes in — should abort old and start new.
        # Generation bumps twice: once to invalidate old (force), once for new reserve.
        gen2 = await guard.async_reserve()
        assert gen2 > gen1  # strictly newer generation
        assert guard.state == QueryState.DISPATCHING

    @pytest.mark.asyncio
    async def test_cancel_on_new_true_invalidates_old_generation(self):
        """After cancel_on_new triggers, old generation's end() is a no-op."""
        guard = QueryGuard(cancel_on_new=True)
        gen1 = await guard.async_reserve()
        await guard.async_try_start(gen1)

        # New query cancels the first
        gen2 = await guard.async_reserve()

        # Old generation's end is stale
        result = await guard.async_end(gen1)
        assert result is False

        # New query can proceed normally
        await guard.async_try_start(gen2)
        result = await guard.async_end(gen2)
        assert result is True
        assert guard.state == QueryState.IDLE

    @pytest.mark.asyncio
    async def test_cancel_on_new_cancels_asyncio_task(self):
        """With cancel_on_new=True and a registered task, reserve cancels it."""
        guard = QueryGuard(cancel_on_new=True)
        cancelled = asyncio.Event()

        async def long_running():
            try:
                await asyncio.sleep(100)
            except asyncio.CancelledError:
                cancelled.set()
                raise

        task = asyncio.create_task(long_running())
        # Yield so the task starts and reaches its first await
        await asyncio.sleep(0)

        gen1 = await guard.async_reserve()
        await guard.async_try_start(gen1)
        guard.set_current_task(task)

        # New query arrives — triggers task cancellation
        gen2 = await guard.async_reserve()

        # Yield to let the CancelledError propagate through the task's except handler
        await asyncio.sleep(0)
        assert cancelled.is_set(), "In-flight task should have been cancelled"
        assert gen2 > gen1

    @pytest.mark.asyncio
    async def test_set_current_task_and_clear(self):
        """set_current_task stores task; completion clears it."""
        guard = QueryGuard(cancel_on_new=True)
        gen = await guard.async_reserve()
        await guard.async_try_start(gen)

        task = asyncio.create_task(asyncio.sleep(0))
        guard.set_current_task(task)
        assert guard._current_task is task

        await guard.async_end(gen)
        assert guard._current_task is None
