"""Auto-extract key facts from conversation for persistent memory.

After a session ends (or after compaction), scan the conversation for:
- File paths mentioned repeatedly
- Error patterns and their solutions
- Architectural decisions made
- User preferences expressed

See ADR-069 P1 for the full rationale.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Callable, Awaitable

if TYPE_CHECKING:
    from duh.kernel.messages import Message

logger = logging.getLogger(__name__)

AUTO_MEMORY_PROMPT = """\
Review this conversation and extract 0-3 key facts worth remembering \
for future sessions.

Rules:
- Only extract non-obvious learnings (not things derivable from code)
- Each fact should be a single sentence
- Include specific details (file paths, function names, error messages)
- If nothing is worth remembering, return empty list

Format: JSON array of {"key": "short-id", "value": "the fact"}

Example output:
[{"key": "test-runner", "value": "Project uses pytest-asyncio for all async tests"}, {"key": "db-choice", "value": "User prefers SQLite over PostgreSQL for local dev"}]

If nothing is worth remembering, output:
[]"""


# Type alias: the model calling function used by the engine.
CallModelFn = Callable[..., Any]


def _messages_to_text(messages: list[Message], max_chars: int = 20000) -> str:
    """Flatten messages to plain text for the extraction prompt.

    Limits total size to *max_chars* to avoid blowing up the extraction
    call with a full 200-turn conversation.  Takes the most recent
    messages first (they're most likely to contain actionable learnings).
    """
    parts: list[str] = []
    total = 0
    for msg in reversed(messages):
        role = getattr(msg, "role", "unknown")
        text = getattr(msg, "text", str(msg))
        chunk = f"[{role}] {text}"
        if total + len(chunk) > max_chars:
            # Include partial tail if we have room
            remaining = max_chars - total
            if remaining > 100:
                parts.append(chunk[:remaining] + "...")
            break
        parts.append(chunk)
        total += len(chunk)
    parts.reverse()
    return "\n\n".join(parts)


def _parse_extraction(raw: str) -> list[dict[str, str]]:
    """Parse the model's JSON response into a list of {key, value} dicts.

    Tolerant of markdown code fences and trailing commas.
    """
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        # Remove opening fence (with optional language tag)
        first_newline = text.index("\n") if "\n" in text else 3
        text = text[first_newline + 1:]
    if text.endswith("```"):
        text = text[:-3]
    text = text.strip()

    if not text or text == "[]":
        return []

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.debug("Auto-memory extraction returned non-JSON: %s", text[:200])
        return []

    if not isinstance(parsed, list):
        logger.debug("Auto-memory extraction returned non-list: %s", type(parsed))
        return []

    results: list[dict[str, str]] = []
    for item in parsed:
        if isinstance(item, dict) and "key" in item and "value" in item:
            results.append({
                "key": str(item["key"]).strip(),
                "value": str(item["value"]).strip(),
            })
    # Cap at 3 facts per extraction
    return results[:3]


async def extract_memories(
    messages: list[Message],
    call_model: CallModelFn,
    *,
    model: str = "",
) -> list[dict[str, str]]:
    """Extract key facts from a conversation using the model.

    Args:
        messages: The conversation history to analyze.
        call_model: Async generator function that calls the model
            (same signature as ``deps.call_model``).
        model: Model identifier to use for extraction. If empty,
            the call_model function uses its default.

    Returns:
        List of ``{"key": "...", "value": "..."}`` dicts (0-3 items).
        Returns empty list on any error.
    """
    if not messages:
        return []

    conversation_text = _messages_to_text(messages)
    if len(conversation_text) < 100:
        # Too short to contain anything worth extracting
        return []

    extraction_prompt = (
        f"{AUTO_MEMORY_PROMPT}\n\n"
        f"<conversation>\n{conversation_text}\n</conversation>"
    )

    # Build a minimal messages list for the extraction call
    from duh.kernel.messages import Message as Msg

    extraction_messages = [
        Msg(role="user", content=extraction_prompt),
    ]

    # Collect the full response text from the model
    response_text = ""
    try:
        kwargs: dict[str, Any] = {
            "messages": extraction_messages,
            "system_prompt": "You are a memory extraction assistant. Output only valid JSON.",
            "tools": [],  # no tools needed
        }
        if model:
            kwargs["model"] = model

        async for event in call_model(**kwargs):
            etype = event.get("type", "") if isinstance(event, dict) else ""
            if etype == "assistant":
                msg = event.get("message")
                if msg is not None:
                    response_text += getattr(msg, "text", "")
            elif etype == "text_delta":
                response_text += event.get("text", "")
    except Exception:
        logger.warning("Auto-memory extraction call failed", exc_info=True)
        return []

    return _parse_extraction(response_text)
