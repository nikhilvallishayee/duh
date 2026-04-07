"""Skill system -- load and manage markdown skill definitions.

See ADR-017 for the full rationale.

A skill is a markdown file with YAML frontmatter containing
structured metadata (name, description, when-to-use, etc.)
and a prompt template body with ``$ARGUMENTS`` substitution.

Skills are loaded from:
1. ``~/.config/duh/skills/*.md`` (user-global)
2. ``.duh/skills/*.md`` (project-local, overrides by name)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# SkillDef dataclass
# ---------------------------------------------------------------------------

@dataclass
class SkillDef:
    """A loaded skill definition.

    Attributes:
        name: Unique skill identifier (used in /name invocation).
        description: Short description for discovery.
        when_to_use: Guidance for the model on when to invoke this skill.
        allowed_tools: Tools the skill is allowed to use (informational).
        model: Preferred model for this skill.
        content: The prompt template body (after frontmatter).
        argument_hint: Hint about what arguments the skill accepts.
        source_path: Filesystem path the skill was loaded from.
    """

    name: str
    description: str
    when_to_use: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""
    content: str = ""
    argument_hint: str = ""
    source_path: str = ""

    def render(self, arguments: str = "") -> str:
        """Render the skill content, substituting ``$ARGUMENTS``.

        Args:
            arguments: The argument string to substitute.

        Returns:
            The rendered prompt text.
        """
        return self.content.replace("$ARGUMENTS", arguments)


# ---------------------------------------------------------------------------
# Minimal YAML frontmatter parser
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(
    r"\A---[ \t]*\n(.*?\n)---[ \t]*\n",
    re.DOTALL,
)


def _parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse YAML frontmatter from a markdown string.

    Handles the subset of YAML needed for skill metadata:
    - ``key: value`` scalars (strings)
    - ``key:`` followed by ``- item`` lines (lists of strings)

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
    current_key: str | None = None

    for line in yaml_block.splitlines():
        # List item under current key
        stripped = line.strip()
        if stripped.startswith("- ") and current_key is not None:
            item = stripped[2:].strip()
            # Remove surrounding quotes
            if (item.startswith('"') and item.endswith('"')) or \
               (item.startswith("'") and item.endswith("'")):
                item = item[1:-1]
            if isinstance(meta.get(current_key), list):
                meta[current_key].append(item)
            continue

        # Key: value pair
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
                current_key = key
            else:
                # Empty value = start of a list
                meta[key] = []
                current_key = key

    return meta, body


# ---------------------------------------------------------------------------
# Skill loading
# ---------------------------------------------------------------------------

def _skill_from_file(path: Path) -> SkillDef | None:
    """Load a single skill from a markdown file.

    Returns None if the file cannot be parsed or lacks required fields.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:
        logger.warning("Failed to read skill file %s: %s", path, exc)
        return None

    meta, body = _parse_frontmatter(text)

    name = meta.get("name", "")
    description = meta.get("description", "")

    if not name:
        # Fall back to filename stem
        name = path.stem

    if not description:
        logger.warning("Skill %r in %s has no description, skipping.", name, path)
        return None

    # Parse allowed-tools list
    allowed_tools_raw = meta.get("allowed-tools", [])
    if isinstance(allowed_tools_raw, str):
        allowed_tools = [t.strip() for t in allowed_tools_raw.split(",") if t.strip()]
    elif isinstance(allowed_tools_raw, list):
        allowed_tools = allowed_tools_raw
    else:
        allowed_tools = []

    return SkillDef(
        name=name,
        description=description,
        when_to_use=meta.get("when-to-use", ""),
        allowed_tools=allowed_tools,
        model=meta.get("model", ""),
        content=body.strip(),
        argument_hint=meta.get("argument-hint", ""),
        source_path=str(path),
    )


def load_skills_dir(path: str | Path) -> list[SkillDef]:
    """Load all ``.md`` skill files from a directory.

    Args:
        path: Directory to scan for skill files.

    Returns:
        List of loaded SkillDef objects.
    """
    path = Path(path)
    if not path.is_dir():
        return []

    skills: list[SkillDef] = []
    for md_file in sorted(path.glob("*.md")):
        skill = _skill_from_file(md_file)
        if skill is not None:
            skills.append(skill)

    return skills


def load_all_skills(cwd: str = ".") -> list[SkillDef]:
    """Load skills from all standard locations.

    Locations (loaded in order):
    1. ``~/.config/duh/skills/`` (user-global)
    2. ``.duh/skills/`` relative to *cwd* (project-local)

    Project-local skills override user-global skills with the same name.

    Args:
        cwd: Current working directory (for project-local skills).

    Returns:
        Deduplicated list of SkillDef objects.
    """
    seen: dict[str, SkillDef] = {}

    # 1. User-global skills
    user_dir = Path("~/.config/duh/skills").expanduser()
    for skill in load_skills_dir(user_dir):
        seen[skill.name] = skill

    # 2. Project-local skills (override user-global)
    project_dir = Path(cwd) / ".duh" / "skills"
    for skill in load_skills_dir(project_dir):
        seen[skill.name] = skill

    return list(seen.values())
