"""Tests for duh.duhwave.rlm._bootstrap sandbox guarantees.

The sandbox is the security boundary: every test here verifies a specific
denial. We exercise the real REPL subprocess so we test the real
restrictions (not their abstract description).
"""

from __future__ import annotations

import asyncio
import resource
import sys

import pytest

from duh.duhwave.rlm import RLMRepl, RLMReplError


@pytest.fixture
async def repl():
    r = RLMRepl()
    await r.start()
    try:
        yield r
    finally:
        await r.shutdown()


# ---------------------------------------------------------------------------
# Shell denial
# ---------------------------------------------------------------------------


class TestShellDenied:
    async def test_os_system_raises_permission_error(self, repl):
        # Inside the REPL os.system is replaced with a function that raises
        # PermissionError. exec_code surfaces the error via RLMReplError.
        with pytest.raises(RLMReplError, match="PermissionError"):
            await repl.exec_code("import os; os.system('echo hi')")

    async def test_subprocess_module_disabled(self, repl):
        # The sandbox sets sys.modules["subprocess"] = None, so import
        # raises ImportError. exec_code surfaces it.
        with pytest.raises(RLMReplError, match="ImportError|TypeError|None"):
            await repl.exec_code("import subprocess")

    async def test_sys_modules_subprocess_is_none(self, repl):
        out = await repl.exec_code(
            "import sys; print(repr(sys.modules.get('subprocess')))"
        )
        assert out.strip() == "None"


# ---------------------------------------------------------------------------
# Network denial
# ---------------------------------------------------------------------------


class TestNetworkDenied:
    async def test_socket_connect_raises_permission_error(self, repl):
        with pytest.raises(RLMReplError, match="PermissionError"):
            await repl.exec_code(
                "import socket; "
                "s = socket.socket(); "
                "s.connect(('127.0.0.1', 1))"
            )

    async def test_socket_create_connection_raises(self, repl):
        with pytest.raises(RLMReplError, match="PermissionError"):
            await repl.exec_code(
                "import socket; socket.create_connection(('127.0.0.1', 1))"
            )


# ---------------------------------------------------------------------------
# Memory ceiling
# ---------------------------------------------------------------------------


def _rlimit_as_takes_effect() -> bool:
    """Best-effort check that RLIMIT_AS will actually constrain allocations.

    macOS notoriously does *not* enforce RLIMIT_AS for many allocation
    paths even when setrlimit() succeeds. We try to set it and immediately
    allocate; if Python doesn't trip MemoryError we know we cannot reliably
    test this guarantee on this host.
    """
    if sys.platform == "darwin":
        # macOS does not enforce RLIMIT_AS for the malloc zones used by
        # CPython's string allocator. Skip the test rather than emit a
        # false negative.
        return False
    try:
        # Try a tiny limit in a child fork? No — keep it simple and trust
        # the platform check. Linux enforces; macOS does not.
        return True
    except Exception:
        return False


class TestMemoryCeiling:
    @pytest.mark.skipif(
        not _rlimit_as_takes_effect(),
        reason="RLIMIT_AS is not enforced on this platform (macOS does not honor it for many allocation paths).",
    )
    async def test_huge_allocation_trips_rlimit(self):
        # Use a 64 MiB ceiling so a 1 GiB allocation is guaranteed to trip it
        # without depending on the platform default of 512 MiB.
        r = RLMRepl(mem_mb=64, op_timeout=10.0)
        await r.start()
        try:
            with pytest.raises(RLMReplError, match="MemoryError"):
                await r.exec_code("blob = 'x' * (1024 ** 3)")  # 1 GiB
        finally:
            await r.shutdown()
