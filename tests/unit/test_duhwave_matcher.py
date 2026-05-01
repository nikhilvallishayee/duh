"""Tests for ``duh.duhwave.ingress.matcher.SubscriptionMatcher``.

Subscription matching is fnmatch-based and first-match-wins (ADR-031
§B.3). The ``from_spec`` constructor wires a parsed SwarmSpec into a
routing table; the ``route`` method picks the agent for an inbound
trigger.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from duh.duhwave.ingress.matcher import SubscriptionMatcher
from duh.duhwave.ingress.triggers import Trigger, TriggerKind
from duh.duhwave.spec.parser import parse_swarm


def _write_spec(tmp_path: Path) -> Path:
    """Build a minimal swarm topology with a couple of subscriptions."""
    spec_path = tmp_path / "swarm.toml"
    spec_path.write_text(
        """
[swarm]
name = "matcher-fixture"
version = "0.1.0"
description = "matcher tests"
format_version = 1

[[agents]]
id = "researcher"
role = "researcher"
model = "sonnet"

[[agents]]
id = "reviewer"
role = "reviewer"
model = "sonnet"

[[triggers]]
kind = "webhook"
source = "/github/issues"
target_agent_id = "researcher"

[[triggers]]
kind = "webhook"
source = "/github/pulls"
target_agent_id = "reviewer"

[[triggers]]
kind = "filewatch"
source = "github:foo/*"
target_agent_id = "researcher"
""".strip()
    )
    return spec_path


# ---------------------------------------------------------------------------
# from_spec
# ---------------------------------------------------------------------------


class TestFromSpec:
    def test_builds_correct_mappings(self, tmp_path: Path):
        spec = parse_swarm(_write_spec(tmp_path))
        matcher = SubscriptionMatcher.from_spec(spec)
        # One row per declared trigger.
        assert len(matcher) == 3

    def test_unknown_kind_in_spec_is_skipped(self, tmp_path: Path):
        # Build a spec with a bogus trigger kind. parse_swarm itself is
        # structural-only, so it accepts the kind; the matcher drops it.
        spec_path = tmp_path / "swarm.toml"
        spec_path.write_text(
            """
[swarm]
name = "unknown-kind"
version = "0.1.0"
description = ""
format_version = 1

[[agents]]
id = "a"
role = "researcher"
model = "sonnet"

[[triggers]]
kind = "made-up-kind"
source = "/x"
target_agent_id = "a"

[[triggers]]
kind = "webhook"
source = "/real"
target_agent_id = "a"
""".strip()
        )
        spec = parse_swarm(spec_path)
        matcher = SubscriptionMatcher.from_spec(spec)
        # Unknown kind is dropped; valid one survives.
        assert len(matcher) == 1


# ---------------------------------------------------------------------------
# route()
# ---------------------------------------------------------------------------


class TestRoute:
    def test_kind_and_exact_source_match_wins(self, tmp_path: Path):
        spec = parse_swarm(_write_spec(tmp_path))
        matcher = SubscriptionMatcher.from_spec(spec)
        agent = matcher.route(
            Trigger(kind=TriggerKind.WEBHOOK, source="/github/issues")
        )
        assert agent == "researcher"

        agent = matcher.route(
            Trigger(kind=TriggerKind.WEBHOOK, source="/github/pulls")
        )
        assert agent == "reviewer"

    def test_glob_pattern_matches_multiple_sources(self, tmp_path: Path):
        spec = parse_swarm(_write_spec(tmp_path))
        matcher = SubscriptionMatcher.from_spec(spec)

        bar = matcher.route(
            Trigger(kind=TriggerKind.FILEWATCH, source="github:foo/bar")
        )
        baz = matcher.route(
            Trigger(kind=TriggerKind.FILEWATCH, source="github:foo/baz")
        )
        other = matcher.route(
            Trigger(kind=TriggerKind.FILEWATCH, source="github:other/x")
        )
        assert bar == "researcher"
        assert baz == "researcher"
        assert other is None  # outside the glob

    def test_kind_mismatch_yields_none(self, tmp_path: Path):
        spec = parse_swarm(_write_spec(tmp_path))
        matcher = SubscriptionMatcher.from_spec(spec)
        # Source matches a webhook entry, but kind is filewatch.
        agent = matcher.route(
            Trigger(kind=TriggerKind.FILEWATCH, source="/github/issues")
        )
        assert agent is None

    def test_unmatched_source_yields_none(self, tmp_path: Path):
        spec = parse_swarm(_write_spec(tmp_path))
        matcher = SubscriptionMatcher.from_spec(spec)
        agent = matcher.route(
            Trigger(kind=TriggerKind.WEBHOOK, source="/nope")
        )
        assert agent is None

    def test_empty_matcher_routes_nothing(self):
        matcher = SubscriptionMatcher()
        assert (
            matcher.route(Trigger(kind=TriggerKind.MANUAL, source="any"))
            is None
        )
