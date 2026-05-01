"""Trigger-to-spawn dispatcher — closes the daemon's e2e arc.

ADR-031 §B and ADR-032 §C together describe the loop:

    listener  ─→  TriggerLog (jsonl append)
                         │
                         └→ DISPATCHER (this module)
                              │
                              ├→ SubscriptionMatcher.route(trigger)
                              │      → swarm + agent_id
                              ├→ build a Task (IN_PROCESS)
                              ├→ register in swarm's HostState.registry
                              ├→ run via attached HostRunner
                              ├→ append result to agent.outbox (if set)
                              └→ append event log line ("trigger.spawned",
                                  "trigger.completed", or "trigger.failed")

The dispatcher reads ``triggers.jsonl`` once at startup (replay), then
polls for new entries every :data:`POLL_INTERVAL` seconds. A future
follow-up could use ``inotify`` / ``fsevents`` for true push delivery;
poll keeps the implementation portable.

Paused swarms are honoured: triggers routed to a paused swarm are
recorded as ``trigger.skipped_paused`` and not spawned.

Failure isolation: a runner exception transitions the Task to FAILED
and logs ``trigger.failed`` — it never escapes back into the
dispatcher loop.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path

from duh.duhwave.cli.host_state import HostState
from duh.duhwave.cli.runner import HostRunner
from duh.duhwave.coordinator.role import BUILTIN_ROLES
from duh.duhwave.ingress.matcher import SubscriptionMatcher
from duh.duhwave.ingress.triggers import Trigger, TriggerLog
from duh.duhwave.spec.parser import AgentSpec
from duh.duhwave.task.registry import Task, TaskStatus, TaskSurface


#: Seconds between TriggerLog scans. Keep small for demo responsiveness;
#: the file is tiny so the overhead is negligible.
POLL_INTERVAL = 0.5


@dataclass(slots=True)
class DispatchResult:
    """One trigger's outcome — exposed via the event log."""

    trigger_id: str
    swarm: str
    agent_id: str | None
    task_id: str | None
    status: str  # "spawned" | "completed" | "failed" | "no_match" | "paused"
    detail: str = ""


class Dispatcher:
    """Watches a TriggerLog, routes new triggers to agents, and runs them.

    Single instance per daemon. Holds:

    - ``log``: the global :class:`TriggerLog` (one file per host).
    - ``swarms``: mapping ``name → HostState``.
    - ``matcher``: built once from the union of all installed swarms.
    - ``runner``: the host's attached :class:`HostRunner`.
    """

    def __init__(
        self,
        *,
        log: TriggerLog,
        swarms: dict[str, HostState],
        runner: HostRunner,
        poll_interval: float = POLL_INTERVAL,
    ) -> None:
        self.log = log
        self.swarms = swarms
        self.runner = runner
        self._poll_interval = poll_interval
        self._stopping = asyncio.Event()
        self._loop_task: asyncio.Task[None] | None = None
        # Triggers seen in the file already — keyed by correlation_id so
        # restarts that re-replay the file don't double-fire.
        self._seen: set[str] = set()
        # One :class:`SubscriptionMatcher` per swarm; the dispatcher
        # walks them in load order and the first hit wins. Swarm-aware
        # routing is intentionally outside the matcher itself — the
        # matcher knows nothing about swarms (per ADR-031 §B.3).
        self._per_swarm: list[tuple[str, SubscriptionMatcher]] = [
            (name, SubscriptionMatcher.from_spec(state.spec))
            for name, state in swarms.items()
        ]

    # ── lifecycle ────────────────────────────────────────────────────

    async def start(self) -> None:
        """Replay existing triggers as already-seen, then start polling.

        Replay-as-seen avoids re-spawning tasks for triggers the host
        already processed before its previous restart.
        """
        for t in self.log.replay():
            self._seen.add(t.correlation_id)
        self._loop_task = asyncio.create_task(self._poll_loop(), name="dispatcher")

    async def stop(self) -> None:
        """Signal the poll loop to exit and cancel its task. Idempotent."""
        self._stopping.set()
        if self._loop_task is not None:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except (asyncio.CancelledError, Exception):
                pass
            self._loop_task = None

    async def _poll_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self._tick()
            except Exception as e:
                # Never let dispatcher death stall the daemon.
                for state in self.swarms.values():
                    state.append_event("dispatcher.error", f"{type(e).__name__}: {e}")
            try:
                await asyncio.wait_for(
                    self._stopping.wait(), timeout=self._poll_interval
                )
            except asyncio.TimeoutError:
                pass

    async def _tick(self) -> None:
        """Process all new triggers since last tick."""
        for trigger in self.log.replay():
            if trigger.correlation_id in self._seen:
                continue
            self._seen.add(trigger.correlation_id)
            await self._handle_trigger(trigger)

    # ── routing + spawn ─────────────────────────────────────────────

    async def _handle_trigger(self, trigger: Trigger) -> DispatchResult:
        """Route one trigger; return the outcome (also appended to event log)."""
        swarm_name: str | None = None
        agent_id: str | None = None
        for name, matcher in self._per_swarm:
            hit = matcher.route(trigger)
            if hit is not None:
                swarm_name = name
                agent_id = hit
                break
        if agent_id is None or swarm_name is None:
            # No subscription matched — log it once for observability.
            for state in self.swarms.values():
                state.append_event(
                    "trigger.unrouted",
                    f"corr={trigger.correlation_id[:8]} kind={trigger.kind.value} "
                    f"source={trigger.source}",
                )
            return DispatchResult(
                trigger_id=trigger.correlation_id,
                swarm="",
                agent_id=None,
                task_id=None,
                status="no_match",
            )

        state = self.swarms[swarm_name]
        if state.is_paused():
            state.append_event(
                "trigger.skipped_paused",
                f"corr={trigger.correlation_id[:8]} agent={agent_id}",
            )
            return DispatchResult(
                trigger_id=trigger.correlation_id,
                swarm=swarm_name,
                agent_id=agent_id,
                task_id=None,
                status="paused",
            )

        agent = _find_agent(state.spec.agents, agent_id)
        if agent is None:
            state.append_event(
                "trigger.no_agent",
                f"corr={trigger.correlation_id[:8]} agent={agent_id}",
            )
            return DispatchResult(
                trigger_id=trigger.correlation_id,
                swarm=swarm_name,
                agent_id=agent_id,
                task_id=None,
                status="no_match",
                detail="agent not in spec",
            )

        # ── build + register task ──
        task_id = state.registry.new_id()
        prompt = _build_prompt(trigger)
        system_prompt = _system_prompt_for(agent)
        task = Task(
            task_id=task_id,
            session_id=f"{swarm_name}-{state.spec.version}",
            parent_id=None,
            surface=TaskSurface.IN_PROCESS,
            prompt=prompt,
            model=agent.model,
            tools_allowlist=tuple(agent.tools) if agent.tools else (),
            metadata={
                "role": agent.id,
                "trigger_id": trigger.correlation_id,
                "trigger_kind": trigger.kind.value,
                "trigger_source": trigger.source,
            },
        )
        state.registry.register(task)
        state.append_event(
            "trigger.spawned",
            f"corr={trigger.correlation_id[:8]} task={task_id} agent={agent_id}",
        )

        # ── run ──
        state.registry.transition(task_id, TaskStatus.RUNNING)
        try:
            result_text = await self.runner(prompt, system_prompt, agent.model)
        except Exception as e:
            state.registry.transition(
                task_id, TaskStatus.FAILED, error=f"{type(e).__name__}: {e}"
            )
            state.append_event(
                "trigger.failed",
                f"corr={trigger.correlation_id[:8]} task={task_id} "
                f"err={type(e).__name__}: {e}",
            )
            return DispatchResult(
                trigger_id=trigger.correlation_id,
                swarm=swarm_name,
                agent_id=agent_id,
                task_id=task_id,
                status="failed",
                detail=str(e),
            )

        state.registry.transition(task_id, TaskStatus.COMPLETED, result=result_text)
        # Outbox: optional per-agent JSONL sink.
        outbox_path = _outbox_path(state, agent)
        if outbox_path is not None:
            _append_outbox(outbox_path, trigger, agent, result_text)
        state.append_event(
            "trigger.completed",
            f"corr={trigger.correlation_id[:8]} task={task_id} "
            f"out={len(result_text)}b",
        )
        return DispatchResult(
            trigger_id=trigger.correlation_id,
            swarm=swarm_name,
            agent_id=agent_id,
            task_id=task_id,
            status="completed",
        )


# ---- helpers ---------------------------------------------------------


def _find_agent(agents: tuple[AgentSpec, ...], agent_id: str) -> AgentSpec | None:
    for a in agents:
        if a.id == agent_id:
            return a
    return None


def _system_prompt_for(agent: AgentSpec) -> str:
    """Resolve the agent's system prompt.

    Topology can declare ``system_prompt`` inline or as a ``prompts/<name>.md``
    reference (resolved by the bundle installer). For now we trust the
    parsed string; if absent, fall back to the worker built-in.
    """
    if agent.system_prompt:
        return str(agent.system_prompt)
    role = BUILTIN_ROLES.get(agent.role)
    return role.system_prompt if role else ""


def _build_prompt(trigger: Trigger) -> str:
    """Render the trigger as a user-prompt string.

    For v1: a small structured envelope so the agent can see kind +
    source + payload without us inventing a templating DSL. Topology
    ``prompt_template`` support is a follow-up.
    """
    return (
        f"## trigger\n"
        f"kind: {trigger.kind.value}\n"
        f"source: {trigger.source}\n"
        f"received_at: {trigger.received_at}\n\n"
        f"## payload\n"
        f"```json\n{json.dumps(trigger.payload, indent=2)}\n```\n"
    )


def _outbox_path(state: HostState, agent: AgentSpec) -> Path | None:
    """Resolve the agent's outbox path from the topology.

    Looks at ``agent.options["outbox"]`` (a string path; relative paths
    are resolved against the swarm's ``state/`` directory). Absent → no
    outbox is written.
    """
    raw = agent.options.get("outbox")
    if not raw:
        return None
    p = Path(str(raw))
    if not p.is_absolute():
        p = state.state_dir / p
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _append_outbox(path: Path, trigger: Trigger, agent: AgentSpec, result_text: str) -> None:
    record = {
        "ts": time.time(),
        "agent": agent.id,
        "trigger_id": trigger.correlation_id,
        "trigger_kind": trigger.kind.value,
        "trigger_source": trigger.source,
        "result": result_text,
    }
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")


__all__ = ["Dispatcher", "DispatchResult"]
