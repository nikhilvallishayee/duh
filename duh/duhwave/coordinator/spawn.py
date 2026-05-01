"""The ``Spawn`` tool — coordinator → worker delegation. ADR-029.

The coordinator calls ``Spawn`` to start a child agent against a
selectively-exposed view of its own RLM handles. The worker's final
result is bound back into the coordinator's REPL as a new handle
named by ``bind_as``; subsequent coordinator turns address it via
``Peek`` / ``Search`` / ``Slice`` like any other variable.

Boundary set, runner injection point, role/handle plumbing — full
agent-loop wiring is the host's job and lands in the next ADR-step.
The tool exposes a clean injection API:

    spawn = Spawn(
        repl=coordinator_repl,
        registry=session_task_registry,
        executor_factory=lambda runner: InProcessExecutor(registry, runner),
        worker_runner=host_runner,        # WorkerRunner — host-injected
        parent_role=BUILTIN_ROLES["coordinator"],
        session_id=session_id,
        parent_task_id=None,
        parent_model="inherit",
    )

If ``worker_runner`` is ``None`` at call time the tool returns a
clear runtime error rather than guessing — keeping the seam visible.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable

from duh.duhwave.coordinator.role import Role
from duh.duhwave.coordinator.runner_protocol import WorkerRunner
from duh.duhwave.coordinator.view import RLMHandleView
from duh.duhwave.rlm.repl import RLMRepl
from duh.duhwave.task.executors import AgentRunner, InProcessExecutor
from duh.duhwave.task.registry import Task, TaskRegistry, TaskStatus, TaskSurface
from duh.kernel.tool import ToolContext, ToolResult
from duh.security.trifecta import Capability


# Type alias for an executor factory — the host passes a constructor
# that, given an ``AgentRunner``, returns a configured executor. This
# keeps Spawn from importing the engine.
ExecutorFactory = Callable[[AgentRunner], InProcessExecutor]


@dataclass(slots=True)
class _SpawnDeps:
    """Bundle of dependencies the host injects into the Spawn tool."""

    repl: RLMRepl
    registry: TaskRegistry
    parent_role: Role
    session_id: str
    parent_task_id: str | None
    parent_model: str
    worker_runner: WorkerRunner | None
    executor_factory: ExecutorFactory | None


class Spawn:
    """Run a child agent against a handle-scoped view of the coordinator REPL.

    Available only to the coordinator role; the kernel filters the tool
    out of worker sessions via :func:`filter_tools_for_role`.
    """

    name = "Spawn"
    capabilities = Capability.EXEC
    description = (
        "Spawn a worker agent with a selectively-exposed view of the "
        "coordinator's REPL handles. The worker runs to completion (max_turns "
        "or final answer) and its result text is bound back into the "
        "coordinator's REPL under bind_as."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "The worker's instruction.",
            },
            "expose": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Handle names from the coordinator's REPL the worker can read.",
            },
            "model": {
                "type": "string",
                "default": "inherit",
                "description": "Model size tier or 'inherit' for the parent's model.",
            },
            "max_turns": {
                "type": "integer",
                "default": 5,
                "description": "Maximum turns the worker may take before forced termination.",
            },
            "bind_as": {
                "type": "string",
                "description": "Handle name to bind the worker's result text in the coordinator REPL.",
            },
        },
        "required": ["prompt", "bind_as"],
    }
    is_read_only = False
    is_destructive = False

    def __init__(
        self,
        *,
        repl: RLMRepl,
        registry: TaskRegistry,
        parent_role: Role,
        session_id: str,
        parent_task_id: str | None = None,
        parent_model: str = "inherit",
        worker_runner: WorkerRunner | None = None,
        executor_factory: ExecutorFactory | None = None,
    ) -> None:
        self._deps = _SpawnDeps(
            repl=repl,
            registry=registry,
            parent_role=parent_role,
            session_id=session_id,
            parent_task_id=parent_task_id,
            parent_model=parent_model,
            worker_runner=worker_runner,
            executor_factory=executor_factory,
        )

    # ---- runner-injection seam ------------------------------------

    def attach_runner(self, runner: WorkerRunner) -> None:
        """Set or replace the host-supplied worker runner.

        The runner is called once per ``Spawn`` invocation with the
        constructed Task and a scoped ``RLMHandleView``. See
        :data:`duh.duhwave.coordinator.runner_protocol.WorkerRunner`.
        """
        self._deps.worker_runner = runner

    def attach_executor_factory(self, factory: ExecutorFactory) -> None:
        """Set or replace the host-supplied executor factory."""
        self._deps.executor_factory = factory

    # ---- tool protocol --------------------------------------------

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        # Spawn-depth gate: coordinator role has spawn_depth=1; workers=0.
        # The kernel should already have filtered Spawn out of worker tool
        # sets, but a defence-in-depth check here keeps the property local.
        if self._deps.parent_role.spawn_depth <= 0:
            return {
                "allowed": False,
                "reason": (
                    f"role {self._deps.parent_role.name!r} has no spawn budget left"
                ),
            }
        return {"allowed": True}

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        deps = self._deps

        # 1. Spawn-depth check (also enforced by check_permissions; keep
        #    here so direct .call() tests don't bypass the invariant).
        if deps.parent_role.spawn_depth <= 0:
            return ToolResult(
                output=(
                    f"Spawn error: role {deps.parent_role.name!r} has no "
                    f"spawn budget left (spawn_depth={deps.parent_role.spawn_depth})."
                ),
                is_error=True,
            )

        # 2. Validate input shape.
        prompt = input.get("prompt")
        bind_as = input.get("bind_as")
        if not isinstance(prompt, str) or not prompt.strip():
            return ToolResult(output="Spawn error: 'prompt' must be a non-empty string.", is_error=True)
        if not isinstance(bind_as, str) or not bind_as.strip():
            return ToolResult(output="Spawn error: 'bind_as' must be a non-empty string.", is_error=True)
        if deps.repl.handles.get(bind_as) is not None:
            return ToolResult(
                output=f"Spawn error: handle {bind_as!r} already bound in coordinator REPL.",
                is_error=True,
            )

        expose_raw = input.get("expose", [])
        if not isinstance(expose_raw, (list, tuple)):
            return ToolResult(output="Spawn error: 'expose' must be an array of strings.", is_error=True)
        expose = tuple(str(x) for x in expose_raw)

        # Verify every requested handle exists in the coordinator REPL.
        missing = [n for n in expose if deps.repl.handles.get(n) is None]
        if missing:
            return ToolResult(
                output=f"Spawn error: handles not bound in coordinator REPL: {missing}",
                is_error=True,
            )

        max_turns = int(input.get("max_turns", 5) or 5)
        if max_turns <= 0:
            return ToolResult(output="Spawn error: 'max_turns' must be > 0.", is_error=True)

        model = str(input.get("model", "inherit") or "inherit")
        if model == "inherit":
            model = deps.parent_model or "inherit"

        # 3. Build child Role.
        child_role = deps.parent_role.child_role()

        # 4. Construct Task.
        task_id = deps.registry.new_id()
        task = Task(
            task_id=task_id,
            session_id=deps.session_id,
            parent_id=deps.parent_task_id,
            surface=TaskSurface.IN_PROCESS,
            prompt=prompt,
            model=model,
            tools_allowlist=child_role.tool_allowlist,
            expose_handles=expose,
            metadata={
                "role": child_role.name,
                "max_turns": max_turns,
                "bind_as": bind_as,
            },
        )
        deps.registry.register(task)

        # 5. Build the worker's scoped view of the coordinator REPL.
        view = RLMHandleView.from_names(deps.repl, list(expose))

        # 6. Verify host wired a runner — this is the injection point.
        if deps.worker_runner is None:
            # Mark the task FAILED so it appears in the registry trace.
            deps.registry.transition(
                task_id, TaskStatus.FAILED, error="no worker runner attached"
            )
            return ToolResult(
                output=(
                    "Spawn error: no worker runner attached. The host process "
                    "must call Spawn.attach_runner(...) at startup before the "
                    "coordinator may delegate."
                ),
                is_error=True,
            )

        # 7. Adapt WorkerRunner -> AgentRunner by closing over the view.
        runner = deps.worker_runner

        async def agent_runner(t: Task) -> str:
            return await runner(t, view)

        # 8. Submit to the in-process executor and await the task to a
        #    terminal state, then bind the result.
        if deps.executor_factory is None:
            executor: InProcessExecutor = InProcessExecutor(deps.registry, agent_runner)
        else:
            executor = deps.executor_factory(agent_runner)

        try:
            await executor.submit(task)
        except Exception as e:
            return ToolResult(
                output=f"Spawn error: executor.submit failed: {type(e).__name__}: {e}",
                is_error=True,
            )

        # 9. Wait for terminal state. The InProcessExecutor wraps the
        #    runner in an asyncio.Task we can simply await; if the host
        #    factory returns something else, fall back to polling the
        #    registry record.
        await self._await_terminal(executor, task_id)

        completed = deps.registry.get(task_id)
        assert completed is not None  # registry never evicts within this scope

        # 10. Bind the worker's result back into the coordinator REPL.
        result_text: str = ""
        bind_error: str | None = None
        if completed.status is TaskStatus.COMPLETED:
            result_text = completed.result or ""
            try:
                await deps.repl.bind(bind_as, result_text)
            except Exception as e:
                bind_error = f"{type(e).__name__}: {e}"
        else:
            # On failure / kill, surface the error but still attempt to
            # bind whatever partial text we have (per ADR-029 §"Failure
            # handling": coordinator never sees a silent failure).
            result_text = completed.result or completed.error or ""
            if result_text:
                partial_name = f"{bind_as}__partial"
                if deps.repl.handles.get(partial_name) is None:
                    try:
                        await deps.repl.bind(partial_name, result_text)
                    except Exception as e:
                        bind_error = f"{type(e).__name__}: {e}"

        # 11. Build the tool result.
        status_text = "completed" if completed.status is TaskStatus.COMPLETED else "failed"
        summary = self._summary_line(result_text, completed.error)
        payload = {
            "task_id": task_id,
            "bind_as": bind_as,
            "status": status_text,
            "summary": summary,
        }
        if bind_error:
            payload["bind_error"] = bind_error

        # Format as a short prose digest plus structured fields. The
        # coordinator's dialog sees the prose; programmatic callers can
        # read the metadata.
        output = (
            f"Spawn task_id={task_id} bind_as={bind_as} status={status_text}\n"
            f"summary: {summary}"
        )
        return ToolResult(
            output=output,
            is_error=(status_text != "completed"),
            metadata=payload,
        )

    # ---- helpers --------------------------------------------------

    @staticmethod
    async def _await_terminal(executor: InProcessExecutor, task_id: str) -> None:
        """Wait for the InProcessExecutor's asyncio.Task to complete.

        Falls back to a registry-poll loop if the executor's internal
        ``_asyncio_tasks`` mapping is unavailable (custom factory).
        """
        atask = getattr(executor, "_asyncio_tasks", {}).get(task_id)
        if atask is not None:
            try:
                await atask
            except Exception:
                # The executor catches exceptions from the runner and
                # transitions the task to FAILED itself; nothing to do
                # here.
                pass
            return
        # Generic poll fallback.
        while True:
            t = executor._registry.get(task_id) if hasattr(executor, "_registry") else None  # type: ignore[attr-defined]
            if t is not None and t.status.terminal:
                return
            await asyncio.sleep(0.01)

    @staticmethod
    def _summary_line(result_text: str, error: str | None) -> str:
        """Produce a one-line prose digest for the coordinator's dialog."""
        if error and not result_text:
            return f"(failed: {error})"
        first_line = (result_text.strip().splitlines() or [""])[0]
        if len(first_line) > 200:
            first_line = first_line[:197] + "..."
        return first_line or "(empty)"
