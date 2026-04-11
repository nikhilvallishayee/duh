"""Connection pre-warming — reduce first-turn latency.

Fires a lightweight model ping in the background at REPL startup.
The warmed connection is reused by the provider for the first real turn.

    task = asyncio.create_task(prewarm_connection(provider.stream))
    # ... REPL startup continues ...
    # First real query benefits from warm connection
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class PrewarmResult:
    """Result of a pre-warming attempt."""
    success: bool
    latency_ms: float = 0.0
    error: str = ""


async def prewarm_connection(
    call_model: Any,
    *,
    timeout: float = 10.0,
) -> PrewarmResult:
    """Make a lightweight model ping to warm the connection.

    Sends a minimal prompt and discards the response. The HTTP connection
    and any TLS handshake are cached by the underlying HTTP client,
    reducing latency for the first real query.

    Never raises — failures are logged and returned as PrewarmResult.
    """
    import asyncio
    from duh.kernel.messages import Message

    start = time.monotonic()

    try:
        async for _event in call_model(
            messages=[Message(role="user", content="hi")],
            system_prompt="Reply with a single word.",
            model="",  # use default
        ):
            # Consume events but discard them
            pass

        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug("Pre-warm completed in %.0fms", elapsed_ms)
        return PrewarmResult(success=True, latency_ms=elapsed_ms)

    except asyncio.TimeoutError:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug("Pre-warm timed out after %.0fms", elapsed_ms)
        return PrewarmResult(success=False, latency_ms=elapsed_ms, error="timeout")

    except Exception as e:
        elapsed_ms = (time.monotonic() - start) * 1000
        logger.debug("Pre-warm failed: %s (%.0fms)", e, elapsed_ms)
        return PrewarmResult(success=False, latency_ms=elapsed_ms, error=str(e))
