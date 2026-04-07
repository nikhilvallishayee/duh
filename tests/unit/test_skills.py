"""Tests for the skills system (ADR-017).

Covers:
- SkillDef creation and rendering
- Frontmatter parsing (various YAML subsets)
- Loading skills from directories
- Loading all skills with precedence
- SkillTool invocation
"""

from __future__ import annotations

from pathlib import Path

import pytest

from duh.kernel.skill import (
    SkillDef,
    _parse_frontmatter,
    _skill_from_file,
    load_all_skills,
    load_skills_dir,
)
from duh.kernel.tool import ToolContext
from duh.tools.skill_tool import SkillTool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def ctx() -> ToolContext:
    return ToolContext(cwd=".")


SAMPLE_SKILL = """\
---
name: commit
description: Create a git commit with a well-crafted message.
when-to-use: When the user asks to commit changes.
allowed-tools:
  - Bash
  - Read
model: sonnet
argument-hint: Optional commit message override
---

Review the staged changes and create a commit.

$ARGUMENTS
"""

MINIMAL_SKILL = """\
---
name: greet
description: Say hello.
---

Hello, $ARGUMENTS!
"""


# ===========================================================================
# SkillDef
# ===========================================================================


class TestSkillDef:
    def test_basic_creation(self):
        s = SkillDef(name="test", description="A test skill")
        assert s.name == "test"
        assert s.description == "A test skill"
        assert s.when_to_use == ""
        assert s.allowed_tools == []
        assert s.model == ""
        assert s.content == ""
        assert s.argument_hint == ""
        assert s.source_path == ""

    def test_full_creation(self):
        s = SkillDef(
            name="commit",
            description="Create a commit",
            when_to_use="When committing",
            allowed_tools=["Bash", "Read"],
            model="opus",
            content="Do the commit $ARGUMENTS",
            argument_hint="message",
            source_path="/path/to/commit.md",
        )
        assert s.name == "commit"
        assert s.allowed_tools == ["Bash", "Read"]
        assert s.model == "opus"
        assert s.source_path == "/path/to/commit.md"

    def test_render_with_arguments(self):
        s = SkillDef(name="test", description="t", content="Hello $ARGUMENTS world")
        assert s.render("there") == "Hello there world"

    def test_render_without_arguments(self):
        s = SkillDef(name="test", description="t", content="Hello $ARGUMENTS world")
        assert s.render("") == "Hello  world"
        assert s.render() == "Hello  world"

    def test_render_no_placeholder(self):
        s = SkillDef(name="test", description="t", content="No placeholder here")
        assert s.render("ignored") == "No placeholder here"

    def test_render_multiple_placeholders(self):
        s = SkillDef(name="test", description="t", content="$ARGUMENTS and $ARGUMENTS")
        assert s.render("x") == "x and x"


# ===========================================================================
# Frontmatter parsing
# ===========================================================================


class TestParseFrontmatter:
    def test_simple_key_value(self):
        text = "---\nname: hello\ndescription: world\n---\nbody"
        meta, body = _parse_frontmatter(text)
        assert meta["name"] == "hello"
        assert meta["description"] == "world"
        assert body == "body"

    def test_list_values(self):
        text = "---\ntools:\n  - Bash\n  - Read\n---\ncontent"
        meta, body = _parse_frontmatter(text)
        assert meta["tools"] == ["Bash", "Read"]
        assert body == "content"

    def test_no_frontmatter(self):
        text = "Just some text without frontmatter"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_empty_value_starts_list(self):
        text = "---\nitems:\n  - one\n  - two\n---\n"
        meta, body = _parse_frontmatter(text)
        assert meta["items"] == ["one", "two"]

    def test_quoted_values(self):
        text = '---\nname: "quoted value"\n---\nbody'
        meta, body = _parse_frontmatter(text)
        assert meta["name"] == "quoted value"

    def test_single_quoted_values(self):
        text = "---\nname: 'single quoted'\n---\nbody"
        meta, body = _parse_frontmatter(text)
        assert meta["name"] == "single quoted"

    def test_quoted_list_items(self):
        text = '---\ntools:\n  - "Bash Tool"\n  - \'Read Tool\'\n---\n'
        meta, body = _parse_frontmatter(text)
        assert meta["tools"] == ["Bash Tool", "Read Tool"]

    def test_preserves_body(self):
        text = "---\nname: x\n---\nline 1\nline 2\nline 3\n"
        meta, body = _parse_frontmatter(text)
        assert body == "line 1\nline 2\nline 3\n"

    def test_hyphenated_keys(self):
        text = "---\nwhen-to-use: always\nargument-hint: msg\n---\n"
        meta, body = _parse_frontmatter(text)
        assert meta["when-to-use"] == "always"
        assert meta["argument-hint"] == "msg"

    def test_empty_file(self):
        meta, body = _parse_frontmatter("")
        assert meta == {}
        assert body == ""

    def test_frontmatter_only(self):
        text = "---\nname: x\n---\n"
        meta, body = _parse_frontmatter(text)
        assert meta["name"] == "x"
        assert body == ""


# ===========================================================================
# _skill_from_file
# ===========================================================================


class TestSkillFromFile:
    def test_full_skill_file(self, tmp_path: Path):
        f = tmp_path / "commit.md"
        f.write_text(SAMPLE_SKILL)
        skill = _skill_from_file(f)
        assert skill is not None
        assert skill.name == "commit"
        assert skill.description == "Create a git commit with a well-crafted message."
        assert skill.when_to_use == "When the user asks to commit changes."
        assert skill.allowed_tools == ["Bash", "Read"]
        assert skill.model == "sonnet"
        assert skill.argument_hint == "Optional commit message override"
        assert "$ARGUMENTS" in skill.content
        assert skill.source_path == str(f)

    def test_minimal_skill_file(self, tmp_path: Path):
        f = tmp_path / "greet.md"
        f.write_text(MINIMAL_SKILL)
        skill = _skill_from_file(f)
        assert skill is not None
        assert skill.name == "greet"
        assert skill.description == "Say hello."
        assert skill.allowed_tools == []
        assert skill.model == ""

    def test_name_defaults_to_stem(self, tmp_path: Path):
        f = tmp_path / "my-skill.md"
        f.write_text("---\ndescription: A skill without a name field\n---\ncontent")
        skill = _skill_from_file(f)
        assert skill is not None
        assert skill.name == "my-skill"

    def test_missing_description_skips(self, tmp_path: Path):
        f = tmp_path / "bad.md"
        f.write_text("---\nname: bad\n---\nno description")
        skill = _skill_from_file(f)
        assert skill is None

    def test_nonexistent_file(self, tmp_path: Path):
        skill = _skill_from_file(tmp_path / "nope.md")
        assert skill is None

    def test_no_frontmatter(self, tmp_path: Path):
        f = tmp_path / "plain.md"
        f.write_text("Just plain text, no frontmatter")
        skill = _skill_from_file(f)
        # No description -> None
        assert skill is None


# ===========================================================================
# load_skills_dir
# ===========================================================================


class TestLoadSkillsDir:
    def test_loads_all_md_files(self, tmp_path: Path):
        (tmp_path / "a.md").write_text(
            "---\nname: alpha\ndescription: Alpha skill\n---\nalpha content"
        )
        (tmp_path / "b.md").write_text(
            "---\nname: beta\ndescription: Beta skill\n---\nbeta content"
        )
        skills = load_skills_dir(tmp_path)
        assert len(skills) == 2
        names = {s.name for s in skills}
        assert names == {"alpha", "beta"}

    def test_skips_non_md_files(self, tmp_path: Path):
        (tmp_path / "readme.txt").write_text("not a skill")
        (tmp_path / "skill.md").write_text(
            "---\nname: s\ndescription: S\n---\ncontent"
        )
        skills = load_skills_dir(tmp_path)
        assert len(skills) == 1

    def test_empty_dir(self, tmp_path: Path):
        assert load_skills_dir(tmp_path) == []

    def test_nonexistent_dir(self):
        assert load_skills_dir("/nonexistent/path") == []

    def test_sorted_by_filename(self, tmp_path: Path):
        (tmp_path / "z.md").write_text(
            "---\nname: zeta\ndescription: Z\n---\n"
        )
        (tmp_path / "a.md").write_text(
            "---\nname: alpha\ndescription: A\n---\n"
        )
        skills = load_skills_dir(tmp_path)
        assert skills[0].name == "alpha"
        assert skills[1].name == "zeta"


# ===========================================================================
# load_all_skills
# ===========================================================================


class TestLoadAllSkills:
    def test_project_overrides_user(self, tmp_path: Path, monkeypatch):
        # User-global skill
        user_dir = tmp_path / "user" / "skills"
        user_dir.mkdir(parents=True)
        (user_dir / "commit.md").write_text(
            "---\nname: commit\ndescription: User version\n---\nuser commit"
        )

        # Project-local skill with same name
        project_dir = tmp_path / "project" / ".duh" / "skills"
        project_dir.mkdir(parents=True)
        (project_dir / "commit.md").write_text(
            "---\nname: commit\ndescription: Project version\n---\nproject commit"
        )

        monkeypatch.setenv("HOME", str(tmp_path / "user" / ".."))
        # Patch expanduser to use our temp dir
        import duh.kernel.skill as skill_mod
        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            s = str(self)
            if s.startswith("~"):
                return Path(str(tmp_path / "user")) / s[2:].lstrip("/").replace(".config/duh/skills", "skills")
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)

        skills = load_all_skills(str(tmp_path / "project"))
        commit_skills = [s for s in skills if s.name == "commit"]
        assert len(commit_skills) == 1
        assert commit_skills[0].description == "Project version"

    def test_loads_from_project_dir(self, tmp_path: Path, monkeypatch):
        skills_dir = tmp_path / ".duh" / "skills"
        skills_dir.mkdir(parents=True)
        (skills_dir / "test.md").write_text(
            "---\nname: test\ndescription: Test skill\n---\ntest content"
        )

        # Ensure user dir doesn't exist
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        import duh.kernel.skill as skill_mod
        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            s = str(self)
            if s.startswith("~"):
                return Path(str(fake_home)) / s[2:].lstrip("/")
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)

        skills = load_all_skills(str(tmp_path))
        assert len(skills) == 1
        assert skills[0].name == "test"

    def test_empty_when_no_skills(self, tmp_path: Path, monkeypatch):
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        import duh.kernel.skill as skill_mod
        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            s = str(self)
            if s.startswith("~"):
                return Path(str(fake_home)) / s[2:].lstrip("/")
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)

        skills = load_all_skills(str(tmp_path))
        assert skills == []


# ===========================================================================
# SkillTool
# ===========================================================================


class TestSkillTool:
    def _make_tool(self, skills: list[SkillDef] | None = None) -> SkillTool:
        if skills is None:
            skills = [
                SkillDef(name="commit", description="Commit changes", content="Do commit $ARGUMENTS"),
                SkillDef(name="review", description="Review code", content="Review this: $ARGUMENTS"),
            ]
        return SkillTool(skills=skills)

    async def test_invoke_existing_skill(self):
        tool = self._make_tool()
        result = await tool.call({"skill": "commit", "args": "fix typo"}, ctx())
        assert result.is_error is False
        assert "Do commit fix typo" in result.output

    async def test_invoke_without_args(self):
        tool = self._make_tool()
        result = await tool.call({"skill": "commit"}, ctx())
        assert result.is_error is False
        assert "Do commit " in result.output

    async def test_skill_not_found(self):
        tool = self._make_tool()
        result = await tool.call({"skill": "nonexistent"}, ctx())
        assert result.is_error is True
        assert "not found" in result.output.lower()
        assert "commit" in result.output  # lists available skills

    async def test_empty_skill_name(self):
        tool = self._make_tool()
        result = await tool.call({"skill": ""}, ctx())
        assert result.is_error is True
        assert "required" in result.output.lower()

    async def test_missing_skill_key(self):
        tool = self._make_tool()
        result = await tool.call({}, ctx())
        assert result.is_error is True

    async def test_metadata_includes_skill_info(self):
        skills = [
            SkillDef(
                name="deploy",
                description="Deploy",
                model="opus",
                allowed_tools=["Bash"],
                content="Deploy $ARGUMENTS",
            ),
        ]
        tool = self._make_tool(skills)
        result = await tool.call({"skill": "deploy", "args": "prod"}, ctx())
        assert result.metadata["skill_name"] == "deploy"
        assert result.metadata["model"] == "opus"
        assert result.metadata["allowed_tools"] == ["Bash"]

    async def test_no_skills_registered(self):
        tool = self._make_tool(skills=[])
        result = await tool.call({"skill": "anything"}, ctx())
        assert result.is_error is True
        assert "not found" in result.output.lower()

    async def test_is_read_only(self):
        tool = self._make_tool()
        assert tool.is_read_only is True
        assert tool.is_destructive is False

    async def test_add_skill(self):
        tool = self._make_tool(skills=[])
        tool.add_skill(SkillDef(name="new", description="New skill", content="new"))
        result = await tool.call({"skill": "new"}, ctx())
        assert result.is_error is False

    async def test_skills_property(self):
        tool = self._make_tool()
        assert len(tool.skills) == 2

    async def test_check_permissions(self):
        tool = self._make_tool()
        perm = await tool.check_permissions({"skill": "commit"}, ctx())
        assert perm["allowed"] is True

    def test_schema_has_required_fields(self):
        tool = self._make_tool()
        assert tool.name == "Skill"
        assert isinstance(tool.description, str)
        assert tool.input_schema["type"] == "object"
        assert "skill" in tool.input_schema["required"]

    async def test_whitespace_skill_name_trimmed(self):
        tool = self._make_tool()
        result = await tool.call({"skill": "  commit  "}, ctx())
        assert result.is_error is False
        assert "Do commit" in result.output
