"""Skill system -- load and manage markdown skill definitions.

See ADR-017 for the full rationale.

A skill is a markdown file with YAML frontmatter containing
structured metadata (name, description, when-to-use, etc.)
and a prompt template body with ``$ARGUMENTS`` substitution.

Skills are loaded from (in precedence order, last wins by name):
1. ``~/.claude/skills/`` (Claude Code user-global — compat)
2. ``~/.config/duh/skills/`` (D.U.H. user-global)
3. ``.claude/skills/`` (Claude Code project-local — compat)
4. ``.duh/skills/`` (D.U.H. project-local, highest priority)

Supports two layouts:
- Flat: ``skills/my-skill.md``
- Directory: ``skills/my-skill/SKILL.md`` (Claude Code convention)
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
        user_invocable: Whether users can invoke this skill via /name.
        context: Execution context — 'inline' or 'fork'.
        agent: Agent type when context is 'fork'.
        effort: Thinking effort level for model.
        paths: File path triggers (glob patterns).
    """

    name: str
    description: str
    when_to_use: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""
    content: str = ""
    argument_hint: str = ""
    source_path: str = ""
    user_invocable: bool = True
    context: str = "inline"
    agent: str = ""
    effort: str = ""
    paths: list[str] = field(default_factory=list)

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
    r"\A---[ \t]*\n(.*?\n)---[ \t]*\n?",
    re.DOTALL,
)

_H1_RE = re.compile(r"^#\s+(.+)", re.MULTILINE)


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
# Skill loading from a single file
# ---------------------------------------------------------------------------

def _parse_list_field(raw: Any) -> list[str]:
    """Parse a field that can be a comma-separated string or a list."""
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        return [t.strip() for t in raw.split(",") if t.strip()]
    return []


def _parse_bool_field(raw: Any) -> bool:
    """Parse a boolean field (YAML true/false or string 'true'/'false')."""
    if raw is True or raw == "true":
        return True
    return False


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
        # Fall back to parent directory name (for SKILL.md in dir) or filename stem
        if path.name.upper() == "SKILL.MD" and path.parent.name != "skills":
            name = path.parent.name
        else:
            name = path.stem

    if not description:
        # Fall back to first H1 in markdown body
        h1_match = _H1_RE.search(body)
        if h1_match:
            description = h1_match.group(1).strip()

    if not description:
        logger.warning("Skill %r in %s has no description, skipping.", name, path)
        return None

    # Parse optional fields — support both hyphenated (Claude Code) keys
    allowed_tools = _parse_list_field(meta.get("allowed-tools", []))
    paths = _parse_list_field(meta.get("paths", []))

    # user-invocable: default True
    user_invocable_raw = meta.get("user-invocable", "true")
    user_invocable = _parse_bool_field(user_invocable_raw) if user_invocable_raw != "true" else True

    # Model: 'inherit' means empty (use parent)
    model = meta.get("model", "")
    if model == "inherit":
        model = ""

    return SkillDef(
        name=name,
        description=description,
        when_to_use=meta.get("when-to-use", ""),
        allowed_tools=allowed_tools,
        model=model,
        content=body.strip(),
        argument_hint=meta.get("argument-hint", ""),
        source_path=str(path),
        user_invocable=user_invocable,
        context=meta.get("context", "inline"),
        agent=meta.get("agent", ""),
        effort=meta.get("effort", ""),
        paths=paths,
    )


# ---------------------------------------------------------------------------
# Directory scanning
# ---------------------------------------------------------------------------

def load_skills_dir(path: str | Path) -> list[SkillDef]:
    """Load all skill files from a directory.

    Supports two layouts:
    - Flat: ``path/my-skill.md``
    - Directory: ``path/my-skill/SKILL.md`` (Claude Code convention)

    Subdirectories without a ``SKILL.md`` at their root are treated as
    namespace directories and recursed into, allowing nested layouts such as:
    ``path/category/my-skill/SKILL.md``

    Args:
        path: Directory to scan for skill files.

    Returns:
        List of loaded SkillDef objects.
    """
    path = Path(path)
    if not path.is_dir():
        return []

    skills: list[SkillDef] = []

    for entry in sorted(path.iterdir()):
        # Flat .md file
        if entry.is_file() and entry.suffix == ".md":
            skill = _skill_from_file(entry)
            if skill is not None:
                skills.append(skill)

        # Directory — check for SKILL.md, else recurse as namespace
        elif entry.is_dir():
            skill_md = entry / "SKILL.md"
            if skill_md.is_file():
                skill = _skill_from_file(skill_md)
                if skill is not None:
                    skills.append(skill)
            else:
                # Namespace directory — recurse into it
                skills.extend(load_skills_dir(entry))

    return skills


def load_all_skills(cwd: str = ".") -> list[SkillDef]:
    """Load skills from all standard locations.

    Locations (loaded in precedence order, last wins by name):
    1. ``~/.claude/skills/`` (Claude Code user-global — compat)
    2. ``~/.config/duh/skills/`` (D.U.H. user-global)
    3. ``.claude/skills/`` relative to cwd (Claude Code project — compat)
    4. ``.duh/skills/`` relative to cwd (D.U.H. project, highest priority)

    Args:
        cwd: Current working directory (for project-local skills).

    Returns:
        Deduplicated list of SkillDef objects.
    """
    seen: dict[str, SkillDef] = {}

    # 1. Claude Code user-global skills
    claude_user_dir = Path("~/.claude/skills").expanduser()
    for skill in load_skills_dir(claude_user_dir):
        seen[skill.name] = skill

    # 2. D.U.H. user-global skills
    duh_user_dir = Path("~/.config/duh/skills").expanduser()
    for skill in load_skills_dir(duh_user_dir):
        seen[skill.name] = skill

    # 3. Claude Code project-local skills
    claude_project_dir = Path(cwd) / ".claude" / "skills"
    for skill in load_skills_dir(claude_project_dir):
        seen[skill.name] = skill

    # 4. D.U.H. project-local skills (highest priority)
    duh_project_dir = Path(cwd) / ".duh" / "skills"
    for skill in load_skills_dir(duh_project_dir):
        seen[skill.name] = skill

    return list(seen.values())
