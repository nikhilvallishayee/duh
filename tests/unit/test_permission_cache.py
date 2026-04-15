"""Tests for duh.kernel.permission_cache — per-session permission memory (ADR-066 P0)."""

import pytest

from duh.kernel.permission_cache import SessionPermissionCache


class TestSessionPermissionCache:
    """Unit tests for SessionPermissionCache."""

    def test_unknown_tool_returns_none(self):
        cache = SessionPermissionCache()
        assert cache.check("Bash") is None
        assert cache.check("Write") is None
        assert cache.check("") is None

    def test_always_allow(self):
        cache = SessionPermissionCache()
        cache.record("Bash", "a")
        assert cache.check("Bash") == "allow"

    def test_never_allow(self):
        cache = SessionPermissionCache()
        cache.record("Bash", "N")
        assert cache.check("Bash") == "deny"

    def test_yes_not_cached(self):
        cache = SessionPermissionCache()
        cache.record("Bash", "y")
        assert cache.check("Bash") is None

    def test_no_not_cached(self):
        cache = SessionPermissionCache()
        cache.record("Bash", "n")
        assert cache.check("Bash") is None

    def test_clear_resets_everything(self):
        cache = SessionPermissionCache()
        cache.record("Bash", "a")
        cache.record("Write", "N")
        assert cache.check("Bash") == "allow"
        assert cache.check("Write") == "deny"

        cache.clear()
        assert cache.check("Bash") is None
        assert cache.check("Write") is None

    def test_always_overrides_never(self):
        """Recording 'a' for a previously 'N'-ed tool flips it to allow."""
        cache = SessionPermissionCache()
        cache.record("Bash", "N")
        assert cache.check("Bash") == "deny"
        cache.record("Bash", "a")
        assert cache.check("Bash") == "allow"

    def test_never_overrides_always(self):
        """Recording 'N' for a previously 'a'-ed tool flips it to deny."""
        cache = SessionPermissionCache()
        cache.record("Bash", "a")
        assert cache.check("Bash") == "allow"
        cache.record("Bash", "N")
        assert cache.check("Bash") == "deny"

    def test_independent_tools(self):
        """Decisions for one tool do not affect another."""
        cache = SessionPermissionCache()
        cache.record("Bash", "a")
        cache.record("Write", "N")
        assert cache.check("Bash") == "allow"
        assert cache.check("Write") == "deny"
        assert cache.check("Read") is None


class TestInteractiveApproverWithCache:
    """Integration tests: InteractiveApprover + SessionPermissionCache."""

    @pytest.mark.asyncio
    async def test_cached_allow_skips_prompt(self, monkeypatch):
        from duh.adapters.approvers import InteractiveApprover

        cache = SessionPermissionCache()
        cache.record("Bash", "a")
        approver = InteractiveApprover(permission_cache=cache)

        # input() should never be called — if it is, the test fails
        monkeypatch.setattr("builtins.input", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not prompt")))

        result = await approver.check("Bash", {"command": "ls"})
        assert result["allowed"] is True

    @pytest.mark.asyncio
    async def test_cached_deny_skips_prompt(self, monkeypatch):
        from duh.adapters.approvers import InteractiveApprover

        cache = SessionPermissionCache()
        cache.record("Write", "N")
        approver = InteractiveApprover(permission_cache=cache)

        monkeypatch.setattr("builtins.input", lambda *a, **kw: (_ for _ in ()).throw(AssertionError("should not prompt")))

        result = await approver.check("Write", {"path": "/tmp/x"})
        assert result["allowed"] is False
        assert "cached" in result["reason"].lower()

    @pytest.mark.asyncio
    async def test_always_response_caches(self, monkeypatch):
        """User responds 'a' -> subsequent calls are auto-approved."""
        from duh.adapters.approvers import InteractiveApprover
        import io

        cache = SessionPermissionCache()
        approver = InteractiveApprover(permission_cache=cache)

        monkeypatch.setattr("builtins.input", lambda *a, **kw: "a")
        monkeypatch.setattr("sys.stderr", io.StringIO())

        result = await approver.check("Bash", {"command": "pytest"})
        assert result["allowed"] is True
        assert cache.check("Bash") == "allow"

    @pytest.mark.asyncio
    async def test_never_response_caches(self, monkeypatch):
        """User responds 'N' -> subsequent calls are auto-denied."""
        from duh.adapters.approvers import InteractiveApprover
        import io

        cache = SessionPermissionCache()
        approver = InteractiveApprover(permission_cache=cache)

        monkeypatch.setattr("builtins.input", lambda *a, **kw: "N")
        monkeypatch.setattr("sys.stderr", io.StringIO())

        result = await approver.check("Bash", {"command": "rm -rf /"})
        assert result["allowed"] is False
        assert cache.check("Bash") == "deny"

    @pytest.mark.asyncio
    async def test_yes_response_does_not_cache(self, monkeypatch):
        """User responds 'y' -> not cached, will prompt again."""
        from duh.adapters.approvers import InteractiveApprover
        import io

        cache = SessionPermissionCache()
        approver = InteractiveApprover(permission_cache=cache)

        monkeypatch.setattr("builtins.input", lambda *a, **kw: "y")
        monkeypatch.setattr("sys.stderr", io.StringIO())

        result = await approver.check("Bash", {"command": "ls"})
        assert result["allowed"] is True
        assert cache.check("Bash") is None

    @pytest.mark.asyncio
    async def test_no_cache_still_works(self, monkeypatch):
        """InteractiveApprover without a cache still works normally."""
        from duh.adapters.approvers import InteractiveApprover
        import io

        approver = InteractiveApprover()

        monkeypatch.setattr("builtins.input", lambda *a, **kw: "y")
        monkeypatch.setattr("sys.stderr", io.StringIO())

        result = await approver.check("Bash", {"command": "ls"})
        assert result["allowed"] is True
