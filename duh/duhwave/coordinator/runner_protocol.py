"""Worker runner contract — ADR-029.

The :class:`Spawn` tool does not own an agent loop. The host process
wires a concrete runner at startup and injects it into the Spawn tool
instance; this module defines the type contract so other modules can
reference it without dragging in the engine.

A :data:`WorkerRunner` is an async callable that takes:

- a :class:`~duh.duhwave.task.registry.Task` (the worker's identity,
  prompt, tools allowlist, expose list, etc.), and
- an :class:`~duh.duhwave.coordinator.view.RLMHandleView` scoped to the
  task's ``expose_handles``,

and returns the worker's final result text. The Task lifecycle
(transitions to RUNNING / COMPLETED / FAILED) is the executor's
responsibility, not the runner's.
"""
from __future__ import annotations

from typing import Awaitable, Callable, TYPE_CHECKING

if TYPE_CHECKING:
    from duh.duhwave.coordinator.view import RLMHandleView
    from duh.duhwave.task.registry import Task


WorkerRunner = Callable[["Task", "RLMHandleView"], Awaitable[str]]
"""Async callable: ``(Task, RLMHandleView) -> final-result-text``.

The host injects a concrete implementation at process start. The
:class:`Spawn` tool wraps this in an
:class:`~duh.duhwave.task.executors.InProcessExecutor`-compatible
:data:`AgentRunner` (which takes only a ``Task``) by closing over the
constructed view.
"""
