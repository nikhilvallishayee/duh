"""Memory integration for the kernel -- types, prompt building, templates.

See ADR-016 for the full rationale.

The kernel uses this module to:
1. Define memory types and their descriptions
2. Build the memory section of the system prompt
3. Provide a frontmatter template for new memory files
4. Load persistent cross-session facts into the prompt
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from duh.ports.memory import MemoryStore


# ---------------------------------------------------------------------------
# Memory types
# ---------------------------------------------------------------------------

MEMORY_TYPES: dict[str, str] = {
    "user": "Personal preferences and working style",
    "feedback": "Corrections and 'do this, not that' directives",
    "project": "Project-specific patterns and decisions",
    "reference": "Facts, API signatures, architectural notes",
}


# ---------------------------------------------------------------------------
# Frontmatter template
# ---------------------------------------------------------------------------

FRONTMATTER_TEMPLATE = """\
---
name: {name}
description: {description}
type: {type}
---
"""


def make_frontmatter(
    *,
    name: str,
    description: str,
    type: str,
) -> str:
    """Generate frontmatter for a new memory topic file."""
    return FRONTMATTER_TEMPLATE.format(
        name=name,
        description=description,
        type=type,
    )


# ---------------------------------------------------------------------------
# System prompt builder
# ---------------------------------------------------------------------------

def build_memory_prompt(store: MemoryStore) -> str:
    """Read the MEMORY.md index and persistent facts, build a system prompt section.

    Returns an empty string if no MEMORY.md and no facts exist, so the
    caller can safely append it to the system prompt parts list.

    When persistent facts are available (via ``store.list_facts()``), they
    are appended inside a ``<persistent-facts>`` block within the
    ``<memory>`` tag.
    """
    index_content = store.read_index()
    facts_section = _build_facts_section(store)

    has_index = bool(index_content.strip())
    has_facts = bool(facts_section)

    if not has_index and not has_facts:
        return ""

    parts: list[str] = ["<memory>"]
    if has_index:
        parts.append(index_content.rstrip())
    if has_facts:
        parts.append(facts_section)
    parts.append("</memory>")
    return "\n".join(parts)


def _build_facts_section(store: Any) -> str:
    """Build the <persistent-facts> section from stored facts.

    Returns an empty string if the store has no ``list_facts`` method
    or there are no facts.
    """
    if not hasattr(store, "list_facts"):
        return ""

    try:
        facts: list[dict[str, Any]] = store.list_facts()
    except Exception:
        return ""

    if not facts:
        return ""

    # Show the most recent 20 facts (newest last) to keep prompt manageable
    recent = facts[-20:]
    lines = ["<persistent-facts>"]
    for f in recent:
        key = f.get("key", "?")
        value = f.get("value", "")
        tags = f.get("tags", [])
        tag_str = f" [{', '.join(tags)}]" if tags else ""
        lines.append(f"- {key}: {value}{tag_str}")
    lines.append("</persistent-facts>")
    return "\n".join(lines)
