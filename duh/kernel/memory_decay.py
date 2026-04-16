"""Memory decay -- clean up stale facts.

Facts have access timestamps. Unused facts decay over time.
A fact's relevance score is computed from:
- Recency of last access
- Creation date
- Access count

Low-scoring facts are garbage-collected to keep the memory store
lean and relevant.

See ADR-069 P2 for the full rationale.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)


def _parse_timestamp(ts: str) -> datetime | None:
    """Parse an ISO-8601 timestamp, returning None on failure."""
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def _days_since(dt: datetime | None, now: datetime) -> float:
    """Return the number of days between *dt* and *now*.

    Returns a large sentinel (9999) when *dt* is None.
    """
    if dt is None:
        return 9999.0
    delta = now - dt
    return max(0.0, delta.total_seconds() / 86400.0)


def score_facts(
    facts: list[dict[str, Any]],
    *,
    now: datetime | None = None,
) -> list[tuple[dict[str, Any], float]]:
    """Score facts by relevance. Lower score = more likely to decay.

    Scoring formula (all components normalized to roughly 0-1 range):
        score = recency_score * 0.5
              + creation_score * 0.2
              + access_count_score * 0.3

    Where:
        recency_score  = max(0, 1 - days_since_last_access / 180)
        creation_score = max(0, 1 - days_since_creation / 365)
        access_count_score = min(1, access_count / 10)

    Returns a list of (fact, score) tuples sorted by score descending
    (highest relevance first).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    # Ensure 'now' is offset-aware for comparison
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    scored: list[tuple[dict[str, Any], float]] = []

    for fact in facts:
        last_accessed = _parse_timestamp(fact.get("last_accessed", ""))
        created = _parse_timestamp(fact.get("timestamp", ""))
        access_count = int(fact.get("access_count", 0))

        # If no last_accessed, fall back to creation timestamp
        if last_accessed is None:
            last_accessed = created

        days_since_access = _days_since(last_accessed, now)
        days_since_creation = _days_since(created, now)

        recency_score = max(0.0, 1.0 - days_since_access / 180.0)
        creation_score = max(0.0, 1.0 - days_since_creation / 365.0)
        access_count_score = min(1.0, access_count / 10.0)

        score = (
            recency_score * 0.5
            + creation_score * 0.2
            + access_count_score * 0.3
        )
        scored.append((fact, round(score, 4)))

    scored.sort(key=lambda x: x[1], reverse=True)
    return scored


def gc_memories(
    store: Any,
    *,
    max_facts: int = 200,
    min_score: float = 0.1,
    now: datetime | None = None,
) -> int:
    """Remove low-scoring facts from *store*. Returns count removed.

    Two pruning passes:
    1. Remove all facts scoring below *min_score*.
    2. If still above *max_facts*, remove the lowest-scoring facts
       until within the cap.

    Args:
        store: A FileMemoryStore (or anything with list_facts/delete_fact).
        max_facts: Maximum number of facts to keep.
        min_score: Minimum score threshold; facts below this are removed.
        now: Reference timestamp for scoring (default: UTC now).

    Returns:
        Number of facts removed.
    """
    all_facts = store.list_facts()
    if not all_facts:
        return 0

    scored = score_facts(all_facts, now=now)
    removed = 0

    # Pass 1: remove facts below min_score
    to_remove_keys: list[str] = []
    surviving: list[tuple[dict[str, Any], float]] = []
    for fact, score in scored:
        if score < min_score:
            to_remove_keys.append(fact.get("key", ""))
        else:
            surviving.append((fact, score))

    # Pass 2: if still over cap, remove lowest-scoring survivors
    if len(surviving) > max_facts:
        # surviving is sorted by score descending; keep top max_facts
        excess = surviving[max_facts:]
        surviving = surviving[:max_facts]
        for fact, _score in excess:
            to_remove_keys.append(fact.get("key", ""))

    for key in to_remove_keys:
        if key and store.delete_fact(key):
            removed += 1

    if removed > 0:
        logger.info("Memory GC: removed %d stale facts", removed)

    return removed
