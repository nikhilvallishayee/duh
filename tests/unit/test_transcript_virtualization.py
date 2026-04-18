"""Tests for TUI transcript virtualization (ADR-073 Wave 3 task 12).

The TUI caps the number of mounted ``MessageWidget`` instances at
``Config.tui_max_mounted_messages`` (default 500).  When the cap is
exceeded, the *oldest* MessageWidget is unmounted and replaced with a
compact ``Static`` placeholder that reports the total archived count.

These tests exercise the virtualization path directly — they do not
rely on the engine event loop, just on ``_add_widget`` and
``_enforce_mount_cap`` (via an active pilot so Textual is happy mounting
widgets).

Key invariants verified:
    * Below cap  → every MessageWidget stays mounted.
    * Above cap  → oldest evicted first (FIFO), count is preserved.
    * Active streaming assistant widget is never evicted.
    * Config override (``max_mounted_messages`` ctor arg) wins.
    * Placeholder Static reports the correct archive count.
    * Session store (engine._messages) is untouched by widget eviction.
    * Rapid back-to-back adds don't raise.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

# Skip the whole module if Textual is not installed in this environment.
textual = pytest.importorskip("textual", reason="textual not installed")

from duh.ui.app import DuhApp  # noqa: E402
from duh.ui.widgets import MessageWidget  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _fake_engine() -> MagicMock:
    """Return a minimal engine mock suitable for DuhApp construction."""

    async def _run(_prompt: str):  # pragma: no cover — unused in these tests
        if False:
            yield {}

    engine = MagicMock()
    engine.run = _run
    engine.total_input_tokens = 0
    engine.total_output_tokens = 0
    engine.session_id = "test-session"
    engine._messages = []
    engine._session_store = None
    return engine


def _count_mounted_messages(app: DuhApp) -> int:
    """Return the number of MessageWidget instances currently in the DOM."""
    log = app.query_one("#message-log")
    return sum(1 for c in log.children if isinstance(c, MessageWidget))


def _archive_placeholder_text(app: DuhApp) -> str | None:
    """Return the placeholder's rendered text, or None if no placeholder."""
    if app._archive_placeholder is None:
        return None
    # Textual's Static stores the renderable internally; ``render()``
    # returns a Rich ``Content`` whose ``str(...)`` produces the plain
    # text (markup stripped).
    return str(app._archive_placeholder.render())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTranscriptVirtualization:
    async def test_below_cap_all_messages_stay_mounted(self):
        """100 messages added with cap=500 → all 100 remain mounted."""
        app = DuhApp(engine=_fake_engine(), max_mounted_messages=500)
        async with app.run_test(size=(100, 40)):
            for i in range(100):
                await app._new_user_message(f"msg {i}")
            assert len(app._mounted_messages) == 100
            assert _count_mounted_messages(app) == 100
            assert app._archived_message_count == 0
            assert app._archive_placeholder is None

    async def test_above_cap_evicts_oldest(self):
        """With cap=10, adding 15 user messages → 10 live + 5 archived."""
        app = DuhApp(engine=_fake_engine(), max_mounted_messages=10)
        async with app.run_test(size=(100, 40)):
            for i in range(15):
                await app._new_user_message(f"msg {i}")
            assert len(app._mounted_messages) == 10
            assert _count_mounted_messages(app) == 10
            assert app._archived_message_count == 5

    async def test_eviction_is_fifo(self):
        """The oldest message is evicted first (checked via content)."""
        app = DuhApp(engine=_fake_engine(), max_mounted_messages=5)
        async with app.run_test(size=(100, 40)):
            for i in range(8):
                await app._new_user_message(f"msg-{i}")
            # After 8 adds with cap=5, messages 0..2 should be archived.
            # The first still-mounted message should be "msg-3".
            assert app._mounted_messages[0]._content == "msg-3"
            assert app._mounted_messages[-1]._content == "msg-7"
            assert app._archived_message_count == 3

    async def test_archive_placeholder_reports_correct_count(self):
        """The placeholder text shows the accumulated archive count."""
        app = DuhApp(engine=_fake_engine(), max_mounted_messages=3)
        async with app.run_test(size=(100, 40)):
            for i in range(8):
                await app._new_user_message(f"msg {i}")
            assert app._archived_message_count == 5
            text = _archive_placeholder_text(app)
            assert text is not None
            assert "5 older messages" in text

    async def test_singular_placeholder_wording(self):
        """Exactly one archived message uses the singular form."""
        app = DuhApp(engine=_fake_engine(), max_mounted_messages=5)
        async with app.run_test(size=(100, 40)):
            for i in range(6):
                await app._new_user_message(f"msg {i}")
            assert app._archived_message_count == 1
            text = _archive_placeholder_text(app)
            assert text is not None
            assert "1 older message archived" in text

    async def test_active_assistant_is_never_evicted(self):
        """A MessageWidget marked as _active_assistant survives cap enforcement."""
        app = DuhApp(engine=_fake_engine(), max_mounted_messages=3)
        async with app.run_test(size=(100, 40)):
            # Pin an assistant as active — simulates the middle of a stream.
            active = await app._new_assistant_message()
            app._active_assistant = active
            # Now add way more than the cap. The active widget should
            # remain mounted even if it's the oldest.
            for i in range(10):
                await app._new_user_message(f"msg {i}")
            assert active in app._mounted_messages
            assert active.parent is not None

    async def test_session_store_unaffected_by_eviction(self):
        """Evicting widgets does not touch engine._messages."""
        engine = _fake_engine()
        # Populate session store as the engine would.
        engine._messages = [f"m{i}" for i in range(20)]
        app = DuhApp(engine=engine, max_mounted_messages=3)
        async with app.run_test(size=(100, 40)):
            for i in range(10):
                await app._new_user_message(f"msg {i}")
            # Widget-tree eviction MUST NOT mutate the engine's store.
            assert engine._messages == [f"m{i}" for i in range(20)]

    async def test_config_override_via_ctor_arg(self):
        """``max_mounted_messages=2`` caps at 2 regardless of Config default."""
        app = DuhApp(engine=_fake_engine(), max_mounted_messages=2)
        async with app.run_test(size=(100, 40)):
            for i in range(5):
                await app._new_user_message(f"msg {i}")
            assert len(app._mounted_messages) == 2
            assert app._archived_message_count == 3

    async def test_zero_cap_disables_virtualization(self):
        """``max_mounted_messages=0`` keeps every message mounted."""
        app = DuhApp(engine=_fake_engine(), max_mounted_messages=0)
        async with app.run_test(size=(100, 40)):
            for i in range(20):
                await app._new_user_message(f"msg {i}")
            assert len(app._mounted_messages) == 20
            assert app._archived_message_count == 0
            assert app._archive_placeholder is None

    async def test_rapid_adds_do_not_raise(self):
        """Adding many messages back-to-back is safe (no exceptions)."""
        app = DuhApp(engine=_fake_engine(), max_mounted_messages=10)
        async with app.run_test(size=(100, 40)):
            # 200 adds crossing the cap several times.
            for i in range(200):
                await app._new_user_message(f"msg {i}")
            assert len(app._mounted_messages) == 10
            assert app._archived_message_count == 190

    async def test_clear_resets_virtualization_state(self):
        """After the bookkeeping reset that ``/clear`` performs, the
        placeholder is gone and eviction restarts from zero."""
        app = DuhApp(engine=_fake_engine(), max_mounted_messages=3)
        async with app.run_test(size=(100, 40)):
            # Over-fill to create a placeholder.
            for i in range(6):
                await app._new_user_message(f"msg {i}")
            assert app._archived_message_count == 3
            assert app._archive_placeholder is not None

            # Simulate the state reset that /clear performs inline.
            log = app.query_one("#message-log")
            await log.remove_children()
            app._mounted_messages.clear()
            app._archived_message_count = 0
            app._archive_placeholder = None

            # Now add a single message — no archive, no placeholder.
            await app._new_user_message("after clear")
            assert len(app._mounted_messages) == 1
            assert app._archived_message_count == 0
            assert app._archive_placeholder is None

    async def test_config_field_default_is_500(self):
        """Default ``Config.tui_max_mounted_messages`` is 500."""
        from duh.config import Config
        assert Config().tui_max_mounted_messages == 500
