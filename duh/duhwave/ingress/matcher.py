"""Subscription matching — ADR-031 §B.3.

Holds ``(TriggerKind, source_pattern) → agent_id`` mappings parsed from
a :class:`~duh.duhwave.spec.parser.SwarmSpec` and routes incoming
:class:`~duh.duhwave.ingress.triggers.Trigger` records to the agent
that should handle them.

Source patterns are :func:`fnmatch.fnmatch` globs against the trigger's
``source`` string. The first matching subscription wins; this is by
intent — the topology is hand-authored and subscription order is
authorial.
"""
from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from duh.duhwave.ingress.triggers import Trigger, TriggerKind

if TYPE_CHECKING:  # pragma: no cover
    from duh.duhwave.spec.parser import SwarmSpec

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class _Subscription:
    """One row of the routing table."""

    kind: TriggerKind
    source_pattern: str
    target_agent_id: str


class SubscriptionMatcher:
    """Route triggers to target agents declared in a SwarmSpec.

    Parameters
    ----------
    subscriptions:
        Pre-built list of subscriptions. Most callers should use
        :meth:`from_spec` instead.
    """

    def __init__(self, subscriptions: list[_Subscription] | None = None) -> None:
        self._subscriptions: list[_Subscription] = list(subscriptions or [])

    @classmethod
    def from_spec(cls, spec: "SwarmSpec") -> "SubscriptionMatcher":
        """Build a matcher from a parsed swarm topology.

        Trigger spec ``kind`` strings that don't map to a known
        :class:`TriggerKind` are skipped with a warning — the spec
        parser is structural-only today and will gain stricter
        validation later.
        """
        subs: list[_Subscription] = []
        for t in spec.triggers:
            try:
                kind = TriggerKind(t.kind)
            except ValueError:
                logger.warning(
                    "ignoring trigger spec with unknown kind: %r", t.kind
                )
                continue
            subs.append(
                _Subscription(
                    kind=kind,
                    source_pattern=t.source,
                    target_agent_id=t.target_agent_id,
                )
            )
        return cls(subs)

    def route(self, trigger: Trigger) -> str | None:
        """Return the target agent id for ``trigger``, or ``None``.

        First match wins. Source pattern is matched against
        ``trigger.source`` via :func:`fnmatch.fnmatch`.
        """
        for sub in self._subscriptions:
            if sub.kind != trigger.kind:
                continue
            if fnmatch.fnmatch(trigger.source, sub.source_pattern):
                return sub.target_agent_id
        return None

    def __len__(self) -> int:
        return len(self._subscriptions)
