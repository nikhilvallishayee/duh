"""Worker-scoped view over a coordinator's RLM REPL — ADR-029.

A coordinator owns the session's single :class:`RLMRepl`. Workers
spawned by the coordinator do **not** get their own REPL; they get an
:class:`RLMHandleView` — a thin wrapper that exposes only an explicit
whitelist of handle names. Calls referencing other names raise
``ValueError("handle not exposed: ...")`` *before* reaching the
underlying REPL, so the worker's view is its complete attack surface.

Read-only by construction: the view does not expose ``bind`` or
``exec_code``. ``slice`` is supported because slicing produces a new
worker-local handle (in the underlying REPL) — but ADR-029 §"Binding
back" treats worker-local handles as ephemeral until the bridge
commits them to the coordinator's namespace under a namespaced name.
For this iteration, ``slice`` is forwarded with a name-visibility
check on the *source* handle; the new handle is added to the view's
visibility set so the worker can read what it just sliced.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from duh.duhwave.rlm.repl import RLMRepl


@dataclass(slots=True)
class RLMHandleView:
    """A worker's read-only view of selected coordinator handles.

    Attributes:
        repl:    The coordinator's :class:`RLMRepl`. Shared, not copied.
        exposed: Handle names the worker is allowed to address.
    """

    repl: RLMRepl
    exposed: set[str] = field(default_factory=set)

    @classmethod
    def from_names(cls, repl: RLMRepl, names: list[str] | tuple[str, ...]) -> "RLMHandleView":
        """Construct a view exposing exactly ``names`` from ``repl``."""
        return cls(repl=repl, exposed=set(names))

    # ---- visibility check -----------------------------------------

    def _check(self, name: str) -> None:
        if name not in self.exposed:
            raise ValueError(f"handle not exposed: {name}")

    # ---- read-only RLM operations ---------------------------------

    async def peek(
        self,
        name: str,
        *,
        start: int = 0,
        end: int = 4096,
        mode: str = "chars",
    ) -> str:
        """Forward to :meth:`RLMRepl.peek` after the visibility check."""
        self._check(name)
        return await self.repl.peek(name, start=start, end=end, mode=mode)

    async def search(
        self,
        name: str,
        pattern: str,
        *,
        max_hits: int = 50,
    ) -> list[dict[str, Any]]:
        """Forward to :meth:`RLMRepl.search` after the visibility check."""
        self._check(name)
        return await self.repl.search(name, pattern, max_hits=max_hits)

    async def slice(
        self,
        source: str,
        start: int,
        end: int,
        bind_as: str,
    ) -> Any:
        """Slice ``source[start:end]`` into a fresh handle ``bind_as``.

        The new handle becomes visible through this view (added to
        ``exposed``) so the worker can address what it just produced.
        """
        self._check(source)
        handle = await self.repl.slice(source, start, end, bind_as)
        # The slice produces a new handle in the underlying REPL; expose
        # it to the worker so subsequent peeks succeed. Worker-local in
        # ADR-029's sense — the bridge decides whether to promote it to
        # the coordinator's namespace at completion time.
        self.exposed.add(bind_as)
        return handle

    # ---- introspection --------------------------------------------

    def list_exposed(self) -> list[str]:
        """Names visible to the worker, in deterministic order."""
        return sorted(self.exposed)
