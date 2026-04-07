"""Injectable dependencies — the seams of D.U.H.

Every external dependency the kernel needs is injected here.
Tests swap in fakes. Adapters provide real implementations.
The kernel never imports a provider SDK directly.

The dependency injection pattern — elevated to a first-class concept.

    deps = Deps(
        call_model=anthropic_adapter.stream,
        run_tool=tool_executor.run,
        approve=interactive_approver.check,
        compact=auto_compactor.compact,
    )
    engine = Engine(deps=deps, tools=my_tools)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Awaitable
import uuid as _uuid

from duh.kernel.messages import Message


# Type aliases for the dependency signatures
CallModelFn = Callable[..., AsyncGenerator[Any, None]]
RunToolFn = Callable[..., Awaitable[Any]]
ApproveFn = Callable[..., Awaitable[dict[str, Any]]]
CompactFn = Callable[..., Awaitable[Any]]
UuidFn = Callable[[], str]


@dataclass
class Deps:
    """All external dependencies the kernel needs.

    Each field is a callable that the kernel invokes.
    Swap any of them for testing or to change behavior.
    """

    # Required: how to call the model (async generator yielding stream events)
    call_model: CallModelFn | None = None

    # Required: how to execute a tool
    run_tool: RunToolFn | None = None

    # Optional: how to check if a tool is approved (default: auto-allow)
    approve: ApproveFn | None = None

    # Optional: how to compact messages when context is too large
    compact: CompactFn | None = None

    # Optional: UUID generator (injectable for deterministic tests)
    uuid: UuidFn = field(default_factory=lambda: lambda: str(_uuid.uuid4()))
