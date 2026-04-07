"""Memory integration for the kernel -- types, prompt building, templates.

See ADR-016 for the full rationale.

The kernel uses this module to:
1. Define memory types and their descriptions
2. Build the memory section of the system prompt
3. Provide a frontmatter template for new memory files
"""

from __future__ import annotations

from typing import TYPE_CHECKING

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
    """Read the MEMORY.md index and build a system prompt section.

    Returns an empty string if no MEMORY.md exists, so the caller
    can safely append it to the system prompt parts list.
    """
    index_content = store.read_index()
    if not index_content.strip():
        return ""

    return f"<memory>\n{index_content.rstrip()}\n</memory>"
