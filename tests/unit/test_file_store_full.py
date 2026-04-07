"""Full coverage for duh.adapters.file_store — error paths, default dir."""

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from duh.adapters.file_store import FileStore, _default_base_dir
from duh.kernel.messages import Message


class TestDefaultBaseDir:
    def test_returns_home_duh_sessions(self):
        result = _default_base_dir()
        assert result == Path.home() / ".duh" / "sessions"


class TestFileStoreDefaultDir:
    def test_uses_default_when_none(self):
        store = FileStore()
        assert store._base_dir == Path.home() / ".duh" / "sessions"


class TestSaveErrorPath:
    async def test_save_write_error_cleans_up_temp(self, tmp_path):
        """When atomic write fails, temp file is cleaned up and error re-raised."""
        store = FileStore(base_dir=tmp_path)
        msg = Message(role="user", content="hello", id="m1", timestamp="t1")
        # First, save a message successfully
        await store.save("s1", [msg])

        # Now make the directory read-only to cause os.replace to fail
        session_path = tmp_path / "s1.jsonl"

        msg2 = Message(role="assistant", content="world", id="m2", timestamp="t2")

        # Patch os.replace to raise
        with patch("duh.adapters.file_store.os.replace", side_effect=OSError("disk full")):
            with pytest.raises(OSError, match="disk full"):
                await store.save("s1", [msg, msg2])

    async def test_save_error_temp_file_already_gone(self, tmp_path):
        """When cleanup of temp file fails (file already gone), error is still raised."""
        store = FileStore(base_dir=tmp_path)
        msg = Message(role="user", content="hi", id="m1", timestamp="t1")

        def fail_replace(src, dst):
            # Remove the temp file before the cleanup code gets to it
            try:
                os.unlink(src)
            except OSError:
                pass
            raise OSError("replace failed")

        with patch("duh.adapters.file_store.os.replace", side_effect=fail_replace):
            with pytest.raises(OSError, match="replace failed"):
                await store.save("s1", [msg])
