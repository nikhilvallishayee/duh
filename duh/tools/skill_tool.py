"""SkillTool -- invoke a loaded skill by name.

See ADR-017 for the full rationale.

The model calls this tool to execute a skill. The tool finds the
skill by name, substitutes ``$ARGUMENTS`` with the provided args,
and returns the rendered skill content.
"""

from __future__ import annotations

from typing import Any

from duh.kernel.skill import SkillDef
from duh.kernel.tool import ToolContext, ToolResult


class SkillTool:
    """Invoke a skill by name.

    Input:
        skill (str): The skill name to invoke.
        args (str, optional): Arguments to pass to the skill template.

    Returns the rendered skill prompt content.
    """

    name = "Skill"
    description = (
        "Invoke a skill by name. Skills are reusable prompt templates "
        "for common workflows (e.g., commit, review-pr). Use the skill "
        "name and optional arguments."
    )
    input_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "skill": {
                "type": "string",
                "description": "The skill name to invoke (e.g., 'commit', 'review-pr').",
            },
            "args": {
                "type": "string",
                "description": "Optional arguments to pass to the skill template.",
            },
        },
        "required": ["skill"],
    }

    def __init__(self, skills: list[SkillDef] | None = None) -> None:
        self._skills: dict[str, SkillDef] = {}
        if skills:
            for skill in skills:
                self._skills[skill.name] = skill

    @property
    def is_read_only(self) -> bool:
        return True

    @property
    def is_destructive(self) -> bool:
        return False

    @property
    def skills(self) -> list[SkillDef]:
        """All registered skills."""
        return list(self._skills.values())

    def add_skill(self, skill: SkillDef) -> None:
        """Register a skill."""
        self._skills[skill.name] = skill

    async def call(self, input: dict[str, Any], context: ToolContext) -> ToolResult:
        skill_name = input.get("skill", "").strip()
        args = input.get("args", "")

        if not skill_name:
            return ToolResult(
                output="skill name is required",
                is_error=True,
            )

        skill = self._skills.get(skill_name)
        if skill is None:
            available = ", ".join(sorted(self._skills.keys()))
            msg = f"Skill not found: {skill_name!r}."
            if available:
                msg += f" Available skills: {available}"
            return ToolResult(output=msg, is_error=True)

        rendered = skill.render(args or "")
        return ToolResult(
            output=rendered,
            metadata={
                "skill_name": skill.name,
                "model": skill.model,
                "allowed_tools": skill.allowed_tools,
            },
        )

    async def check_permissions(
        self, input: dict[str, Any], context: ToolContext
    ) -> dict[str, Any]:
        return {"allowed": True}
