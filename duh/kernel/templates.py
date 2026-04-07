"""Prompt template system -- load and manage reusable prompt templates.

A template is a markdown file with YAML frontmatter containing
structured metadata (name, description) and a prompt body with
``$PROMPT`` substitution.

Templates are loaded from (in precedence order, last wins by name):
1. ``~/.config/duh/templates/`` (user-global)
2. ``.duh/templates/`` (project-local, highest priority)

Example template (code-review.md):

    ---
    name: code-review
    description: Wrap a prompt in a code review context.
    ---

    You are an expert code reviewer. Review the following with an eye for
    correctness, performance, security, and readability.

    $PROMPT
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# TemplateDef dataclass
# ---------------------------------------------------------------------------

@dataclass
class TemplateDef:
    """A loaded prompt template definition.

    Attributes:
        name: Unique template identifier (used in /template invocation).
        description: Short description for discovery.
        content: The prompt template body (after frontmatter).
        source_path: Filesystem path the template was loaded from.
    """

    name: str
    description: str
    content: str = ""
    source_path: str = ""

    def render(self, prompt: str = "") -> str:
        """Render the template, substituting ``$PROMPT``.

        Args:
            prompt: The user's prompt to substitute.

        Returns:
            The rendered prompt text.
        """
        return self.content.replace("$PROMPT", prompt)


# ---------------------------------------------------------------------------
# Minimal YAML frontmatter parser (reuse from skill.py)
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\n(.*?\n)---[ \t]*\n?",
    re.DOTALL,
)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown string.

    Handles the subset of YAML needed for template metadata:
    - ``key: value`` scalars (strings)

    Does NOT depend on PyYAML.

    Args:
        text: The full markdown file content.

    Returns:
        Tuple of (frontmatter dict, body text after frontmatter).
    """
    match = _FRONTMATTER_RE.match(text)
    if not match:
        return {}, text

    yaml_block = match.group(1)
    body = text[match.end():]

    meta: dict[str, Any] = {}

    for line in yaml_block.splitlines():
        colon_idx = line.find(":")
        if colon_idx > 0:
            key = line[:colon_idx].strip()
            value = line[colon_idx + 1:].strip()

            # Remove surrounding quotes from value
            if (value.startswith('"') and value.endswith('"')) or \
               (value.startswith("'") and value.endswith("'")):
                value = value[1:-1]

            if value:
                meta[key] = value

    return meta, body


# ---------------------------------------------------------------------------
# Template loading from a single file
# ---------------------------------------------------------------------------

def _template_from_file(path: Path) -> TemplateDef | None:
    """Load a single template from a markdown file.

    Returns None if the file cannot be parsed or lacks required fields.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to read template file %s: %s", path, exc)
        return None

    meta, body = _parse_frontmatter(text)

    name = meta.get("name", "")
    description = meta.get("description", "")

    if not name:
        name = path.stem

    if not description:
        logger.warning("Template %r in %s has no description, skipping.", name, path)
        return None

    return TemplateDef(
        name=name,
        description=description,
        content=body.strip(),
        source_path=str(path),
    )


# ---------------------------------------------------------------------------
# Directory scanning
# ---------------------------------------------------------------------------

def load_templates_dir(path: str | Path) -> list[TemplateDef]:
    """Load all template files from a directory.

    Args:
        path: Directory to scan for template files (*.md).

    Returns:
        List of loaded TemplateDef objects.
    """
    path = Path(path)
    if not path.is_dir():
        return []

    templates: list[TemplateDef] = []

    for entry in sorted(path.iterdir()):
        if entry.is_file() and entry.suffix == ".md":
            tmpl = _template_from_file(entry)
            if tmpl is not None:
                templates.append(tmpl)

    return templates


def load_all_templates(cwd: str = ".") -> list[TemplateDef]:
    """Load templates from all standard locations.

    Locations (loaded in precedence order, last wins by name):
    1. ``~/.config/duh/templates/`` (user-global)
    2. ``.duh/templates/`` relative to cwd (project-local, highest priority)

    Args:
        cwd: Current working directory (for project-local templates).

    Returns:
        Deduplicated list of TemplateDef objects.
    """
    seen: dict[str, TemplateDef] = {}

    # 1. User-global templates
    user_dir = Path("~/.config/duh/templates").expanduser()
    for tmpl in load_templates_dir(user_dir):
        seen[tmpl.name] = tmpl

    # 2. Project-local templates (highest priority)
    project_dir = Path(cwd) / ".duh" / "templates"
    for tmpl in load_templates_dir(project_dir):
        seen[tmpl.name] = tmpl

    return list(seen.values())
