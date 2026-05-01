"""Persistent Task primitive — ADR-030.

A :class:`Task` is the unit of agency in duhwave: a persistent record
that survives restarts, is resumable, and runs on any of three
execution surfaces with one shared lifecycle.
"""
from __future__ import annotations

from duh.duhwave.task.executors import (
    InProcessExecutor,
    SubprocessExecutor,
    TaskExecutor,
)
from duh.duhwave.task.registry import (
    Task,
    TaskRegistry,
    TaskStatus,
    TaskSurface,
)
from duh.duhwave.task.remote import RemoteExecutor, RemoteExecutorError

__all__ = [
    "Task",
    "TaskRegistry",
    "TaskStatus",
    "TaskSurface",
    "TaskExecutor",
    "InProcessExecutor",
    "SubprocessExecutor",
    "RemoteExecutor",
    "RemoteExecutorError",
]
