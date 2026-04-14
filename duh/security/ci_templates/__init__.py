"""CI template generators for `duh security generate`.

This package emits `.github/workflows/security.yml` (minimal, standard,
paranoid variants), `.github/dependabot.yml`, and `SECURITY.md`.

All GitHub Actions referenced here are pinned to 40-char SHAs with a
trailing `# vX.Y.Z` comment. Dependabot keeps them current.

See ADR-053 and docs/superpowers/specs/2026-04-14-vuln-monitoring-design.md
Section 4.4 for the authoritative pin list.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final, Mapping

__all__ = ["PinnedAction", "PINNED_ACTIONS"]


@dataclass(frozen=True, slots=True)
class PinnedAction:
    """A GitHub Action pinned to a 40-char SHA with a version comment."""

    name: str
    sha: str
    version: str

    def render(self) -> str:
        """Return `<name>@<sha> # <version>` for inclusion in YAML."""
        return f"{self.name}@{self.sha} # {self.version}"


_PINS: Final[tuple[PinnedAction, ...]] = (
    PinnedAction(
        name="step-security/harden-runner",
        sha="0634a2670c59f64b4a01f0f96f84700a4088b9f0",
        version="v2.17.0",
    ),
    PinnedAction(
        name="actions/checkout",
        sha="de0fac2e4500dabe0009e67214ff5f5447ce83dd",
        version="v6.0.2",
    ),
    PinnedAction(
        name="actions/dependency-review-action",
        sha="2031cfc080254a8a887f58cffee85186f0e49e48",
        version="v4.9.0",
    ),
    PinnedAction(
        name="github/codeql-action/init",
        sha="7fc1baf373eb073c686865bd453d412d506a05a2",
        version="v3.35.1",
    ),
    PinnedAction(
        name="github/codeql-action/analyze",
        sha="7fc1baf373eb073c686865bd453d412d506a05a2",
        version="v3.35.1",
    ),
    PinnedAction(
        name="github/codeql-action/upload-sarif",
        sha="7fc1baf373eb073c686865bd453d412d506a05a2",
        version="v3.35.1",
    ),
    PinnedAction(
        name="ossf/scorecard-action",
        sha="f808768d1510423e83855289c910610ca9b43176",
        version="v2.4.3",
    ),
    PinnedAction(
        name="zizmorcore/zizmor-action",
        sha="TODO",  # TODO: pin SHA at adoption time (flagged in research)
        version="v0.1.0",
    ),
    PinnedAction(
        name="actions/setup-python",
        sha="a309ff8b426b58ec0e2a45f0f869d46889d02405",
        version="v6.2.0",
    ),
    PinnedAction(
        name="actions/cache",
        sha="a2bbfa25375fe432b6a289bc6b6cd05ecd0c4c32",
        version="v4.2.0",
    ),
    PinnedAction(
        name="actions/upload-artifact",
        sha="ea165f8d65b6e75b540449e92b4886f43607fa02",
        version="v4.6.2",
    ),
    PinnedAction(
        name="pypa/gh-action-pypi-publish",
        sha="6733eb7d741f0b11ec6a39b58540dab7590f9b7d",
        version="v1.14.0",
    ),
)


PINNED_ACTIONS: Final[Mapping[str, PinnedAction]] = {
    pin.name: pin for pin in _PINS
}
