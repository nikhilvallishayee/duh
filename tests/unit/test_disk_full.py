"""Tests for graceful handling of ENOSPC (disk full) in persistence paths.

The harness must never crash a session because the disk ran out of
space while persisting a background artefact (session JSONL, memory
facts). Instead it should log a warning and keep the in-memory state
intact so the user can free space and retry.

These tests simulate ENOSPC by monkeypatching filesystem entry points
to raise OSError(28, "No space left on device").
"""

from __future__ import annotations

import builtins
import errno
import logging
import os
import tempfile
from pathlib import Path

import pytest

from duh.adapters.file_store import FileStore
from duh.adapters.memory_store import FileMemoryStore
from duh.kernel.messages import Message


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _enospc(*_args, **_kwargs):  # noqa: ANN001 - generic patch target
    raise OSError(errno.ENOSPC, "No space left on device")


# ---------------------------------------------------------------------------
# FileStore.save() under ENOSPC
# ---------------------------------------------------------------------------

class TestFileStoreSaveEnospc:
    async def test_save_logs_warning_when_mkstemp_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If ``tempfile.mkstemp`` raises ENOSPC, save() must log a
        warning and return without raising. Session stays in memory."""
        store = FileStore(base_dir=tmp_path)
        msg = Message(
            role="user", content="keep me alive",
            id="m-disk-1", timestamp="2025-01-01T00:00:00+00:00",
        )

        monkeypatch.setattr(
            "duh.adapters.file_store.tempfile.mkstemp", _enospc,
        )

        with caplog.at_level(logging.WARNING, logger="duh.adapters.file_store"):
            # Must not raise — disk-full is expected to be swallowed
            await store.save("sess-disk-1", [msg])

        # Clear warning surfaced to user
        assert any(
            "disk full" in rec.getMessage().lower()
            for rec in caplog.records
        ), f"expected disk-full warning, got: {[r.getMessage() for r in caplog.records]}"

        # No partial file landed at the final path
        assert not (tmp_path / "sess-disk-1.jsonl").exists()

    async def test_save_logs_warning_when_write_fails(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """If writing to the temp file raises ENOSPC, save() must log,
        clean up the temp file, and return without crashing."""
        store = FileStore(base_dir=tmp_path)
        msg = Message(
            role="user", content="oops",
            id="m-disk-2", timestamp="2025-01-01T00:00:00+00:00",
        )

        real_fdopen = os.fdopen

        class _BrokenWriter:
            def __init__(self, real_file):
                self._real_file = real_file

            def __enter__(self):
                return self

            def __exit__(self, *args):
                self._real_file.__exit__(*args)
                return False

            def write(self, _data):  # noqa: ANN001
                raise OSError(errno.ENOSPC, "No space left on device")

        def _fdopen_patched(fd, *args, **kwargs):
            real = real_fdopen(fd, *args, **kwargs)
            return _BrokenWriter(real)

        monkeypatch.setattr(
            "duh.adapters.file_store.os.fdopen", _fdopen_patched,
        )

        with caplog.at_level(logging.WARNING, logger="duh.adapters.file_store"):
            await store.save("sess-disk-2", [msg])

        assert any(
            "disk full" in rec.getMessage().lower()
            for rec in caplog.records
        )
        # No stale .tmp file left behind in the sessions dir
        leftover = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
        assert leftover == [], f"stray temp files: {leftover}"

    async def test_save_propagates_non_disk_full_oserror(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-ENOSPC OSErrors (e.g., permission denied) must still
        surface — we only swallow disk-full, not every IO error."""
        store = FileStore(base_dir=tmp_path)
        msg = Message(
            role="user", content="hi",
            id="m-disk-3", timestamp="2025-01-01T00:00:00+00:00",
        )

        def _eacces(*_a, **_kw):
            raise OSError(errno.EACCES, "Permission denied")

        monkeypatch.setattr(
            "duh.adapters.file_store.tempfile.mkstemp", _eacces,
        )

        with pytest.raises(OSError) as exc_info:
            await store.save("sess-disk-3", [msg])
        assert exc_info.value.errno == errno.EACCES


# ---------------------------------------------------------------------------
# FileMemoryStore.store_fact() under ENOSPC
# ---------------------------------------------------------------------------

class TestFileMemoryStoreStoreFactEnospc:
    def test_store_fact_logs_warning_on_enospc(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """``store_fact`` must log a warning and return the in-memory
        entry instead of crashing when ``open()`` raises ENOSPC on the
        append-line fast path."""
        # Point the memory store at an isolated tmp path
        monkeypatch.setattr(
            "duh.adapters.memory_store.config_dir", lambda: tmp_path,
        )
        store = FileMemoryStore(cwd=str(tmp_path))

        # Patch Path.open only for the facts file write
        real_open = Path.open

        def _patched_open(self, *args, **kwargs):  # noqa: ANN001
            mode = args[0] if args else kwargs.get("mode", "r")
            if "a" in mode or "w" in mode:
                raise OSError(errno.ENOSPC, "No space left on device")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", _patched_open)

        with caplog.at_level(logging.WARNING, logger="duh.adapters.memory_store"):
            entry = store.store_fact("k1", "v1", tags=["t"])

        # Returned the in-memory entry anyway — caller doesn't crash
        assert entry["key"] == "k1"
        assert entry["value"] == "v1"

        assert any(
            "disk full" in rec.getMessage().lower()
            for rec in caplog.records
        ), f"expected disk-full warning, got: {[r.getMessage() for r in caplog.records]}"

    def test_store_fact_reraises_non_disk_full(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Non-ENOSPC OSErrors still propagate — store_fact only
        tolerates ENOSPC/EDQUOT, never hides generic IO errors."""
        monkeypatch.setattr(
            "duh.adapters.memory_store.config_dir", lambda: tmp_path,
        )
        store = FileMemoryStore(cwd=str(tmp_path))

        real_open = Path.open

        def _patched_open(self, *args, **kwargs):  # noqa: ANN001
            mode = args[0] if args else kwargs.get("mode", "r")
            if "a" in mode or "w" in mode:
                raise OSError(errno.EACCES, "Permission denied")
            return real_open(self, *args, **kwargs)

        monkeypatch.setattr(Path, "open", _patched_open)

        with pytest.raises(OSError) as exc_info:
            store.store_fact("k2", "v2")
        assert exc_info.value.errno == errno.EACCES
