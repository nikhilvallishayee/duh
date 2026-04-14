"""Tests for ExceptionStore: alias, scope, expiry, permanent, unused detection."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from duh.security.exceptions import Exception as SecException, ExceptionStore
from duh.security.finding import Finding, Location, Severity


def _mk_finding(id: str, aliases: tuple[str, ...] = (), package: str | None = None) -> Finding:
    return Finding.create(
        id=id,
        aliases=aliases,
        scanner="pip-audit",
        severity=Severity.HIGH,
        message="m",
        description="",
        location=Location(file="pyproject.toml", line_start=1, line_end=1, snippet=""),
        package=package,
        version="2.31.0",
    )


def _now() -> datetime:
    return datetime(2026, 4, 14, 19, 0, 0, tzinfo=timezone.utc)


def test_add_exception_requires_reason(tmp_path: Path) -> None:
    store = ExceptionStore(path=tmp_path / "exceptions.json")
    with pytest.raises(ValueError, match="reason"):
        store.add(
            id="CVE-2025-1",
            reason="",
            expires_at=_now() + timedelta(days=30),
            added_by="nikhil@localhost",
            added_at=_now(),
        )


def test_add_exception_rejects_past_expiry(tmp_path: Path) -> None:
    store = ExceptionStore(path=tmp_path / "exceptions.json")
    with pytest.raises(ValueError, match="past"):
        store.add(
            id="CVE-2025-1",
            reason="ok",
            expires_at=_now() - timedelta(days=1),
            added_by="n",
            added_at=_now(),
        )


def test_add_exception_rejects_over_90_days(tmp_path: Path) -> None:
    store = ExceptionStore(path=tmp_path / "exceptions.json")
    with pytest.raises(ValueError, match="90"):
        store.add(
            id="CVE-2025-1",
            reason="ok",
            expires_at=_now() + timedelta(days=120),
            added_by="n",
            added_at=_now(),
        )


def test_add_exception_long_term_allows_365(tmp_path: Path) -> None:
    store = ExceptionStore(path=tmp_path / "exceptions.json")
    store.add(
        id="CVE-2025-1",
        reason="ok",
        expires_at=_now() + timedelta(days=200),
        added_by="n",
        added_at=_now(),
        long_term=True,
    )
    assert len(store.all()) == 1


def test_covers_by_id(tmp_path: Path) -> None:
    store = ExceptionStore(path=tmp_path / "exceptions.json")
    store.add(
        id="CVE-2025-1",
        reason="r",
        expires_at=_now() + timedelta(days=30),
        added_by="n",
        added_at=_now(),
    )
    assert store.covers(_mk_finding("CVE-2025-1"), at=_now()) is True
    assert store.covers(_mk_finding("CVE-2025-2"), at=_now()) is False


def test_covers_by_alias(tmp_path: Path) -> None:
    store = ExceptionStore(path=tmp_path / "exceptions.json")
    store.add(
        id="CVE-2025-1",
        aliases=("GHSA-xxxx-yyyy-zzzz",),
        reason="r",
        expires_at=_now() + timedelta(days=30),
        added_by="n",
        added_at=_now(),
    )
    assert store.covers(_mk_finding("GHSA-xxxx-yyyy-zzzz"), at=_now()) is True
    # Finding reports primary id but has CVE in aliases
    assert store.covers(_mk_finding("GHSA-xxxx-yyyy-zzzz", aliases=("CVE-2025-1",)), at=_now()) is True


def test_expired_does_not_cover(tmp_path: Path) -> None:
    store = ExceptionStore(path=tmp_path / "exceptions.json")
    store.add(
        id="CVE-2025-1",
        reason="r",
        expires_at=_now() + timedelta(days=1),
        added_by="n",
        added_at=_now(),
    )
    later = _now() + timedelta(days=2)
    assert store.covers(_mk_finding("CVE-2025-1"), at=later) is False


def test_permanent_never_expires(tmp_path: Path) -> None:
    store = ExceptionStore(path=tmp_path / "exceptions.json")
    store.add(
        id="CVE-2025-1",
        reason="r",
        expires_at=_now() + timedelta(days=30),
        added_by="n",
        added_at=_now(),
        permanent=True,
    )
    far_future = _now() + timedelta(days=10_000)
    assert store.covers(_mk_finding("CVE-2025-1"), at=far_future) is True


def test_scope_package_narrowing(tmp_path: Path) -> None:
    store = ExceptionStore(path=tmp_path / "exceptions.json")
    store.add(
        id="CVE-2025-1",
        reason="r",
        expires_at=_now() + timedelta(days=30),
        added_by="n",
        added_at=_now(),
        scope={"package": "requests"},
    )
    assert store.covers(_mk_finding("CVE-2025-1", package="requests"), at=_now()) is True
    assert store.covers(_mk_finding("CVE-2025-1", package="rich"), at=_now()) is False


def test_remove(tmp_path: Path) -> None:
    store = ExceptionStore(path=tmp_path / "exceptions.json")
    store.add(
        id="CVE-2025-1",
        reason="r",
        expires_at=_now() + timedelta(days=30),
        added_by="n",
        added_at=_now(),
    )
    assert store.remove("CVE-2025-1") is True
    assert store.remove("CVE-2025-1") is False


def test_renew_extends_expiry(tmp_path: Path) -> None:
    store = ExceptionStore(path=tmp_path / "exceptions.json")
    store.add(
        id="CVE-2025-1",
        reason="r",
        expires_at=_now() + timedelta(days=30),
        added_by="n",
        added_at=_now(),
    )
    new_expiry = _now() + timedelta(days=60)
    store.renew("CVE-2025-1", new_expiry)
    assert store.get("CVE-2025-1").expires_at == new_expiry


def test_audit_reports_expired(tmp_path: Path) -> None:
    store = ExceptionStore(path=tmp_path / "exceptions.json")
    store.add(
        id="CVE-2025-1",
        reason="r",
        expires_at=_now() + timedelta(days=1),
        added_by="n",
        added_at=_now(),
    )
    audit = store.audit(at=_now() + timedelta(days=2))
    assert "CVE-2025-1" in audit.expired


def test_expiring_within(tmp_path: Path) -> None:
    store = ExceptionStore(path=tmp_path / "exceptions.json")
    store.add(
        id="CVE-2025-1",
        reason="r",
        expires_at=_now() + timedelta(days=3),
        added_by="n",
        added_at=_now(),
    )
    expiring = store.expiring_within(days=7, at=_now())
    assert any(e.id == "CVE-2025-1" for e in expiring)


def test_persist_and_reload(tmp_path: Path) -> None:
    path = tmp_path / "exceptions.json"
    store = ExceptionStore(path=path)
    store.add(
        id="CVE-2025-1",
        reason="r",
        expires_at=_now() + timedelta(days=30),
        added_by="n",
        added_at=_now(),
    )
    store.save()

    reloaded = ExceptionStore.load(path)
    assert len(reloaded.all()) == 1
    assert reloaded.get("CVE-2025-1").reason == "r"
