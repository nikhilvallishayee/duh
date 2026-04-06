"""The kernel — the smallest possible agentic loop.

Five files. Zero external dependencies. The heart of D.U.H.

    from duh.kernel import Engine, Tool, query

    engine = Engine(tools=[my_tool], provider=my_provider)
    async for event in engine.run("fix the bug"):
        print(event)
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
