"""Ports — abstract interfaces the kernel depends on.

Adapters implement these. The kernel never imports concrete implementations.
"""

from duh.ports.approver import ApprovalGate
from duh.ports.context import ContextManager
from duh.ports.executor import ToolExecutor
from duh.ports.memory import MemoryHeader, MemoryStore
from duh.ports.provider import ModelProvider
from duh.ports.store import SessionStore

__all__ = [
    "ApprovalGate",
    "ContextManager",
    "MemoryHeader",
    "MemoryStore",
    "ModelProvider",
    "SessionStore",
    "ToolExecutor",
]
