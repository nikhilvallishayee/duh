"""TOML topology parser — ADR-032 §A.

One file = one swarm. Top-level keys::

    [swarm]    name, version, description, format_version
    [[agents]] id, role, model, tools, expose, system_prompt
    [[triggers]] kind, source, target_agent_id, ...
    [[edges]]  from_agent_id, to_agent_id, kind=spawn|message
    [budget]   max_tokens_per_hour, max_usd_per_day, max_concurrent_tasks
    [ingress]  webhook_port, webhook_host, secret (HMAC, ${VAR} interpolated)
    [secrets]  list of env vars referenced via ${VAR}

Validation is structural for the skeleton; full JSON Schema validation
arrives in the next iteration along with ``${VAR}`` interpolation.

``[ingress] secret`` *is* interpolated at parse time — the
:class:`IngressSpec` carries an already-resolved secret string (or
``None``), so the daemon never has to know about env conventions.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

try:  # 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 fallback
    import tomli as tomllib  # type: ignore[no-redef]


# ``${VAR}`` and ``${VAR:-default}`` references inside string values get
# resolved against ``os.environ`` at parse time. Only ``${...}`` is
# expanded — bare ``$VAR`` is left alone so users can put literal dollar
# signs in URLs or paths.
_ENV_REF = re.compile(r"\$\{([A-Z_][A-Z0-9_]*)(?::-([^}]*))?\}")


def _interpolate_env(value: str) -> str:
    """Expand ``${VAR}`` / ``${VAR:-default}`` against the environment."""

    def _sub(match: "re.Match[str]") -> str:
        var_name = match.group(1)
        default = match.group(2) or ""
        return os.environ.get(var_name, default)

    return _ENV_REF.sub(_sub, value)


@dataclass(slots=True)
class AgentSpec:
    """One ``[[agents]]`` row from the topology.

    Fields ``id`` / ``role`` / ``model`` are required. ``tools`` and
    ``expose`` are tuples (immutable, hashable for dispatch use). The
    free-form ``options`` dict carries any keys the parser doesn't
    recognise today — currently only ``outbox`` is consulted (by
    :class:`~duh.duhwave.cli.dispatcher.Dispatcher`).
    """

    id: str
    role: str
    model: str
    tools: tuple[str, ...] = ()
    expose: tuple[str, ...] = ()
    system_prompt: str | None = None
    #: Free-form per-agent options. Recognised today:
    #:   - ``outbox``: file path (relative to swarm state/) where
    #:     completed task results are appended as JSONL — used by the
    #:     dispatcher to make the trigger→agent→reply arc observable.
    options: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class TriggerSpec:
    """One ``[[triggers]]`` row.

    ``kind`` matches a :class:`~duh.duhwave.ingress.triggers.TriggerKind`
    value; ``source`` is a glob the
    :class:`~duh.duhwave.ingress.matcher.SubscriptionMatcher` matches
    against incoming :class:`~duh.duhwave.ingress.triggers.Trigger`
    records. ``options`` carries listener-specific config (e.g.
    ``filter`` regex, ``debounce_ms`` for filewatch).
    """

    kind: str
    source: str
    target_agent_id: str
    options: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class EdgeSpec:
    """One ``[[edges]]`` row — declares an allowed coordinator→worker handoff.

    Edges are documentation today; the runtime enforces flow via
    :class:`~duh.duhwave.coordinator.role.Role` and Spawn semantics.
    Future planners may consult edges to validate spawn graphs at
    install time.
    """

    from_agent_id: str
    to_agent_id: str
    kind: str = "spawn"  # "spawn" | "message"


@dataclass(slots=True)
class BudgetSpec:
    """The ``[budget]`` section — hard ceilings honored by the host.

    Tokens-per-hour and USD-per-day are advisory floors today; the
    ``max_concurrent_tasks`` ceiling is honored by the dispatcher's
    spawn path.
    """

    max_tokens_per_hour: int = 1_000_000
    max_usd_per_day: float = 50.0
    max_concurrent_tasks: int = 4


@dataclass(slots=True)
class IngressSpec:
    """Per-swarm ingress configuration — ADR-031 §B.

    Read from the optional top-level ``[ingress]`` table::

        [ingress]
        webhook_port = 8728
        webhook_host = "127.0.0.1"
        secret       = "${MY_WEBHOOK_SECRET}"   # ${VAR} interpolated at parse time

    The daemon merges every installed swarm's ``IngressSpec`` into one
    physical :class:`WebhookListener`: ports are reconciled (the first
    explicit port wins; a clash logs a warning), and ``secret`` values
    are mapped onto the swarm's webhook source-prefixes so cross-swarm
    secret routing works on a single port.
    """

    webhook_port: int = 8728
    webhook_host: str = "127.0.0.1"
    secret: str | None = None


@dataclass(slots=True)
class SwarmSpec:
    name: str
    version: str
    description: str
    format_version: int
    agents: tuple[AgentSpec, ...]
    triggers: tuple[TriggerSpec, ...]
    edges: tuple[EdgeSpec, ...]
    budget: BudgetSpec
    secrets: tuple[str, ...]
    ingress: IngressSpec = field(default_factory=IngressSpec)


class SwarmSpecError(ValueError):
    """Topology failed structural validation."""


def parse_swarm(path: Path | str) -> SwarmSpec:
    """Read and validate a TOML topology. Raises :class:`SwarmSpecError`."""
    with Path(path).open("rb") as f:
        raw = tomllib.load(f)

    try:
        swarm = raw["swarm"]
    except KeyError as e:
        raise SwarmSpecError("missing [swarm] section") from e

    _AGENT_KNOWN = {"id", "role", "model", "tools", "expose", "system_prompt"}
    agents = tuple(
        AgentSpec(
            id=str(a["id"]),
            role=str(a["role"]),
            model=str(a["model"]),
            tools=tuple(str(t) for t in a.get("tools", ())),
            expose=tuple(str(t) for t in a.get("expose", ())),
            system_prompt=a.get("system_prompt"),
            options={k: v for k, v in a.items() if k not in _AGENT_KNOWN},
        )
        for a in raw.get("agents", [])
    )
    if not agents:
        raise SwarmSpecError("swarm has no agents")

    agent_ids = {a.id for a in agents}

    triggers = tuple(
        TriggerSpec(
            kind=str(t["kind"]),
            source=str(t["source"]),
            target_agent_id=str(t["target_agent_id"]),
            options={k: v for k, v in t.items() if k not in {"kind", "source", "target_agent_id"}},
        )
        for t in raw.get("triggers", [])
    )
    for t in triggers:
        if t.target_agent_id not in agent_ids:
            raise SwarmSpecError(f"trigger targets unknown agent: {t.target_agent_id}")

    edges = tuple(
        EdgeSpec(
            from_agent_id=str(e["from_agent_id"]),
            to_agent_id=str(e["to_agent_id"]),
            kind=str(e.get("kind", "spawn")),
        )
        for e in raw.get("edges", [])
    )
    for e in edges:
        if e.from_agent_id not in agent_ids:
            raise SwarmSpecError(f"edge source unknown: {e.from_agent_id}")
        if e.to_agent_id not in agent_ids:
            raise SwarmSpecError(f"edge target unknown: {e.to_agent_id}")

    budget_raw = raw.get("budget", {})
    budget = BudgetSpec(
        max_tokens_per_hour=int(budget_raw.get("max_tokens_per_hour", 1_000_000)),
        max_usd_per_day=float(budget_raw.get("max_usd_per_day", 50.0)),
        max_concurrent_tasks=int(budget_raw.get("max_concurrent_tasks", 4)),
    )

    secrets = tuple(str(s) for s in raw.get("secrets", []))

    ingress_raw = raw.get("ingress", {})
    if not isinstance(ingress_raw, dict):
        raise SwarmSpecError("[ingress] must be a table")
    ingress_secret_raw = ingress_raw.get("secret")
    ingress_secret: str | None
    if ingress_secret_raw is None:
        ingress_secret = None
    else:
        # ``${VAR}`` interpolated at parse time so the daemon and tests
        # both see a concrete string (or ``None`` if the var was unset
        # and there was no default).
        interpolated = _interpolate_env(str(ingress_secret_raw))
        ingress_secret = interpolated if interpolated != "" else None
    ingress = IngressSpec(
        webhook_port=int(ingress_raw.get("webhook_port", 8728)),
        webhook_host=str(ingress_raw.get("webhook_host", "127.0.0.1")),
        secret=ingress_secret,
    )

    return SwarmSpec(
        name=str(swarm.get("name", "unnamed")),
        version=str(swarm.get("version", "0.0.0")),
        description=str(swarm.get("description", "")),
        format_version=int(swarm.get("format_version", 1)),
        agents=agents,
        triggers=triggers,
        edges=edges,
        budget=budget,
        secrets=secrets,
        ingress=ingress,
    )
