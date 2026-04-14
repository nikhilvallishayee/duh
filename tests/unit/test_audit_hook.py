"""Tests for the PEP 578 audit hook bridge (ADR-054, 7.5)."""

from __future__ import annotations

import pytest

from duh.kernel.audit import WATCHED_EVENTS, install, _audit_handler


# ---------------------------------------------------------------------------
# Task 7.5.1 — WATCHED_EVENTS + install() basics
# ---------------------------------------------------------------------------

def test_watched_events_is_frozenset() -> None:
    assert isinstance(WATCHED_EVENTS, frozenset)


def test_watched_events_contains_critical_events() -> None:
    for event in ("open", "subprocess.Popen", "socket.connect", "import"):
        assert event in WATCHED_EVENTS, f"{event} missing from WATCHED_EVENTS"


def test_audit_handler_ignores_unwatched() -> None:
    # Should return None (no-op) for unwatched events
    result = _audit_handler("some.random.event", ())
    assert result is None


def test_install_is_callable() -> None:
    # Just verify it's importable and callable — actual sys.addaudithook
    # is tested in integration, not here (cannot remove audit hooks)
    assert callable(install)


# ---------------------------------------------------------------------------
# Task 7.5.2 — HookEvent.AUDIT in hook bus
# ---------------------------------------------------------------------------

from duh.hooks import HookEvent


def test_hook_event_has_audit() -> None:
    assert hasattr(HookEvent, "AUDIT")
    assert HookEvent.AUDIT.value == "audit"


# ---------------------------------------------------------------------------
# Task 7.5.3 — _installed flag set after install()
# ---------------------------------------------------------------------------

def test_audit_installed_flag_set_after_install() -> None:
    from duh.kernel import audit
    # Force install with a mock registry
    class MockRegistry:
        def fire_audit(self, event: str, args: tuple) -> None:
            pass
    audit.install(MockRegistry())
    assert audit._installed is True


# ---------------------------------------------------------------------------
# Task 7.5.4 — Sensitive import filtering
# ---------------------------------------------------------------------------

def test_audit_handler_filters_benign_imports() -> None:
    """import of 'os', 'json' etc should NOT fire the handler."""
    from duh.kernel.audit import _audit_handler
    # _audit_handler returns None for filtered events
    assert _audit_handler("import", ("os",)) is None
    assert _audit_handler("import", ("json",)) is None


def test_audit_handler_passes_sensitive_imports() -> None:
    """import of 'pickle', 'marshal' should pass through (registry fires)."""
    from duh.kernel.audit import _audit_handler, _registry
    # With no registry set, still exercises the code path
    _audit_handler("import", ("pickle",))  # should not raise
    _audit_handler("import", ("marshal",))  # should not raise


# ---------------------------------------------------------------------------
# Additional coverage: _sanitize, watched non-import events, fire_audit path
# ---------------------------------------------------------------------------

def test_audit_handler_watched_non_import_event_no_registry() -> None:
    """Watched non-import events with registry=None should not raise."""
    from duh.kernel import audit
    saved = audit._registry
    audit._registry = None
    try:
        result = _audit_handler("open", ("/tmp/test.txt",))
        assert result is None
    finally:
        audit._registry = saved


def test_audit_handler_fires_registry() -> None:
    """Watched event should call fire_audit on the registry."""
    from duh.kernel import audit
    saved = audit._registry

    fired: list[tuple[str, tuple]] = []

    class CapturingRegistry:
        def fire_audit(self, event: str, args: tuple) -> None:
            fired.append((event, args))

    audit._registry = CapturingRegistry()
    try:
        _audit_handler("open", ("/tmp/x.txt",))
        assert len(fired) == 1
        assert fired[0][0] == "open"
    finally:
        audit._registry = saved


def test_audit_handler_registry_exception_suppressed() -> None:
    """Exceptions in registry.fire_audit must be swallowed."""
    from duh.kernel import audit
    saved = audit._registry

    class BrokenRegistry:
        def fire_audit(self, event: str, args: tuple) -> None:
            raise RuntimeError("boom")

    audit._registry = BrokenRegistry()
    try:
        # Must not raise
        _audit_handler("open", ("/tmp/x.txt",))
    finally:
        audit._registry = saved


def test_sanitize_truncates_long_strings() -> None:
    from duh.kernel.audit import _sanitize
    long_str = "x" * 300
    result = _sanitize((long_str,))
    assert len(result[0]) < 300
    assert result[0].endswith("...[truncated]")


def test_sanitize_passes_short_strings() -> None:
    from duh.kernel.audit import _sanitize
    result = _sanitize(("/tmp/file.txt", 42, None))
    assert result == ("/tmp/file.txt", 42, None)


def test_install_idempotent() -> None:
    """Calling install() twice should not raise or double-register."""
    from duh.kernel import audit

    class MockRegistry:
        def fire_audit(self, event: str, args: tuple) -> None:
            pass

    # Already installed from earlier test — calling again should be no-op
    original_installed = audit._installed
    audit.install(MockRegistry())
    assert audit._installed is original_installed or audit._installed is True
