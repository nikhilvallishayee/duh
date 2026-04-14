"""Tests for Finding, Severity, Location dataclasses and serialization."""

from __future__ import annotations

import json

import pytest

from duh.security.finding import Finding, Location, Severity


def _make_finding(**overrides) -> Finding:
    base = dict(
        id="CVE-2025-12345",
        aliases=("GHSA-wxyz-1234-5678",),
        scanner="pip-audit",
        severity=Severity.HIGH,
        message="requests <2.32 has HTTP smuggling",
        description="Full advisory text.",
        location=Location(
            file="pyproject.toml",
            line_start=5,
            line_end=5,
            snippet="requests>=2.30",
        ),
        package="requests",
        version="2.31.0",
        fixed_in="2.32.0",
        cwe=(444,),
        metadata={"source": "osv.dev"},
    )
    base.update(overrides)
    return Finding.create(**base)


def test_severity_ordering() -> None:
    order = [Severity.INFO, Severity.LOW, Severity.MEDIUM, Severity.HIGH, Severity.CRITICAL]
    for a, b in zip(order, order[1:]):
        assert a.rank < b.rank


def test_location_is_frozen() -> None:
    loc = Location(file="a.py", line_start=1, line_end=2, snippet="print()")
    with pytest.raises(Exception):
        loc.file = "b.py"  # type: ignore[misc]


def test_finding_fingerprint_is_stable() -> None:
    a = _make_finding()
    b = _make_finding()
    assert a.fingerprint == b.fingerprint
    assert len(a.fingerprint) == 64  # sha256 hex


def test_finding_fingerprint_changes_with_location() -> None:
    a = _make_finding()
    b = _make_finding(location=Location(
        file="pyproject.toml", line_start=99, line_end=99, snippet="x",
    ))
    assert a.fingerprint != b.fingerprint


def test_finding_json_round_trip() -> None:
    original = _make_finding()
    payload = original.to_json()
    dumped = json.dumps(payload)
    restored = Finding.from_json(json.loads(dumped))
    assert restored.id == original.id
    assert restored.fingerprint == original.fingerprint
    assert restored.severity == Severity.HIGH


def test_finding_to_sarif_has_required_keys() -> None:
    f = _make_finding()
    sarif = f.to_sarif()
    assert sarif["ruleId"] == "CVE-2025-12345"
    assert sarif["level"] == "error"  # high → error
    assert "message" in sarif and "text" in sarif["message"]
    assert sarif["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "pyproject.toml"
    assert sarif["partialFingerprints"]["primary"] == f.fingerprint


def test_finding_sarif_level_mapping() -> None:
    assert _make_finding(severity=Severity.CRITICAL).to_sarif()["level"] == "error"
    assert _make_finding(severity=Severity.HIGH).to_sarif()["level"] == "error"
    assert _make_finding(severity=Severity.MEDIUM).to_sarif()["level"] == "warning"
    assert _make_finding(severity=Severity.LOW).to_sarif()["level"] == "note"
    assert _make_finding(severity=Severity.INFO).to_sarif()["level"] == "note"


def test_finding_is_frozen() -> None:
    f = _make_finding()
    with pytest.raises(Exception):
        f.id = "other"  # type: ignore[misc]
