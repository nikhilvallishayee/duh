"""Engine — session lifecycle wrapper around the query loop.

The Engine owns the conversation state for a session:
- Message history
- Turn counting
- Usage tracking
- Session identity

It delegates the actual model calling to the query loop.

    engine = Engine(deps=my_deps, tools=my_tools)
    async for event in engine.run("fix the bug"):
        handle(event)
    # engine.messages now contains the full conversation
"""

from __future__ import annotations

import uuid as _uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator

from duh.kernel.deps import Deps
from duh.kernel.loop import query
from duh.kernel.messages import Message, UserMessage
from duh.kernel.tokens import count_tokens, estimate_cost, format_cost


@dataclass
class EngineConfig:
    """Configuration for an Engine session."""
    model: str = ""
    system_prompt: str | list[str] = ""
    tools: list[Any] = field(default_factory=list)
    thinking: dict[str, Any] | None = None
    tool_choice: str | dict[str, Any] | None = None
    max_turns: int = 1000
    cwd: str = "."


class Engine:
    """Session lifecycle wrapper around the query loop.

    One Engine per conversation. Call run() for each user message.
    The engine maintains message history across runs.
    """

    def __init__(self, deps: Deps, config: EngineConfig | None = None, **kwargs: Any):
        self._deps = deps
        self._config = config or EngineConfig(**kwargs)
        self._messages: list[Message] = []
        self._session_id = str(_uuid.uuid4())
        self._turn_count = 0
        self._total_input_tokens = 0
        self._total_output_tokens = 0

    @property
    def messages(self) -> list[Message]:
        return list(self._messages)

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def turn_count(self) -> int:
        return self._turn_count

    @property
    def total_input_tokens(self) -> int:
        return self._total_input_tokens

    @property
    def total_output_tokens(self) -> int:
        return self._total_output_tokens

    @property
    def model(self) -> str:
        return self._config.model

    def estimated_cost(self, model: str | None = None) -> float:
        """Return estimated session cost in USD."""
        m = model or self._config.model
        return estimate_cost(m, self._total_input_tokens, self._total_output_tokens)

    def cost_summary(self, model: str | None = None) -> str:
        """Return a human-readable cost summary string."""
        m = model or self._config.model
        cost = self.estimated_cost(m)
        return (
            f"Input tokens:  ~{self._total_input_tokens:,}\n"
            f"Output tokens: ~{self._total_output_tokens:,}\n"
            f"Estimated cost: {format_cost(cost)} ({m})"
        )

    async def run(
        self,
        prompt: str | list[Any],
        *,
        max_turns: int | None = None,
        model: str | None = None,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Submit a user message and stream the response.

        Yields the same events as kernel.query(), plus:
        - {"type": "session", "session_id": "...", "turn": N}
        """
        # Add user message
        user_msg = Message(role="user", content=prompt if isinstance(prompt, str) else prompt)
        self._messages.append(user_msg)
        self._turn_count += 1

        # Estimate input tokens (all messages + system prompt sent to model)
        prompt_text = prompt if isinstance(prompt, str) else str(prompt)
        sys_text = (
            self._config.system_prompt
            if isinstance(self._config.system_prompt, str)
            else " ".join(self._config.system_prompt)
        )
        input_estimate = count_tokens(prompt_text) + count_tokens(sys_text)
        # Include prior message context sent with this turn
        for m in self._messages[:-1]:
            input_estimate += count_tokens(
                m.text if isinstance(m, Message) else str(m)
            )
        self._total_input_tokens += input_estimate

        yield {
            "type": "session",
            "session_id": self._session_id,
            "turn": self._turn_count,
        }

        # Run the query loop
        async for event in query(
            messages=self._messages,
            system_prompt=self._config.system_prompt,
            deps=self._deps,
            tools=self._config.tools,
            max_turns=max_turns or self._config.max_turns,
            model=model or self._config.model,
            thinking=self._config.thinking,
            tool_choice=self._config.tool_choice,
        ):
            event_type = event.get("type", "")

            # Track assistant messages in history and count output tokens
            if event_type == "assistant":
                msg = event.get("message")
                if isinstance(msg, Message):
                    self._messages.append(msg)
                    self._total_output_tokens += count_tokens(msg.text)

            yield event
