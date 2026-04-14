"""The kernel — the smallest possible agentic loop.

Five files. Zero external dependencies. The heart of D.U.H.

    from duh.kernel import Engine, Tool, query

    engine = Engine(tools=[my_tool], provider=my_provider)
    async for event in engine.run("fix the bug"):
        print(event)

Audit hook startup
------------------
Call ``duh.kernel.audit.install(hook_registry)`` **once** at startup,
after the HookRegistry is available, to activate the PEP 578 telemetry
bridge (ADR-054, Workstream 7.5).  The hook must be installed early so
dangerous operations that happen during initialisation are captured.

Example::

    from duh.kernel.audit import install as install_audit_hook
    install_audit_hook(hook_registry)
"""

from duh.kernel.deps import Deps
from duh.kernel.engine import Engine
from duh.kernel.loop import query
from duh.kernel.messages import AssistantMessage, Message, UserMessage
from duh.kernel.tool import Tool, ToolResult

__all__ = [
    "AssistantMessage",
    "Deps",
    "Engine",
    "Message",
    "Tool",
    "ToolResult",
    "UserMessage",
    "query",
]
