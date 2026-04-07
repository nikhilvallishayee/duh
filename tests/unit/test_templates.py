"""Tests for the prompt template system.

Covers:
- TemplateDef creation and rendering
- Frontmatter parsing
- Loading templates from directories
- Loading all templates with precedence
- /template REPL command handling
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from duh.kernel.templates import (
    TemplateDef,
    _parse_frontmatter,
    _template_from_file,
    load_all_templates,
    load_templates_dir,
)
from duh.cli.repl import _handle_slash, _handle_template_command
from duh.kernel.deps import Deps
from duh.kernel.engine import Engine, EngineConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_TEMPLATE = """\
---
name: code-review
description: Wrap a prompt in a code review context.
---

You are an expert code reviewer. Review the following with an eye for
correctness, performance, security, and readability.

$PROMPT
"""

MINIMAL_TEMPLATE = """\
---
name: explain
description: Ask for a clear explanation.
---

Explain the following clearly and concisely:

$PROMPT
"""


def _make_engine() -> Engine:
    deps = Deps(call_model=AsyncMock(), run_tool=AsyncMock())
    config = EngineConfig(model="test-model")
    return Engine(deps=deps, config=config)


def _make_deps() -> Deps:
    return Deps(call_model=AsyncMock(), run_tool=AsyncMock())


# ===========================================================================
# TemplateDef
# ===========================================================================


class TestTemplateDef:
    def test_basic_creation(self):
        t = TemplateDef(name="test", description="A test template")
        assert t.name == "test"
        assert t.description == "A test template"
        assert t.content == ""
        assert t.source_path == ""

    def test_render_substitutes_prompt(self):
        t = TemplateDef(name="t", description="d", content="Review this: $PROMPT")
        assert t.render("my code") == "Review this: my code"

    def test_render_empty_prompt(self):
        t = TemplateDef(name="t", description="d", content="Do: $PROMPT now")
        assert t.render("") == "Do:  now"
        assert t.render() == "Do:  now"

    def test_render_no_placeholder(self):
        t = TemplateDef(name="t", description="d", content="No placeholder here")
        assert t.render("ignored") == "No placeholder here"

    def test_render_multiple_placeholders(self):
        t = TemplateDef(name="t", description="d", content="$PROMPT and $PROMPT")
        assert t.render("x") == "x and x"


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

    def test_no_frontmatter(self):
        text = "Just text"
        meta, body = _parse_frontmatter(text)
        assert meta == {}
        assert body == text

    def test_quoted_values(self):
        text = '---\nname: "quoted"\n---\nbody'
        meta, body = _parse_frontmatter(text)
        assert meta["name"] == "quoted"


# ===========================================================================
# _template_from_file
# ===========================================================================


class TestTemplateFromFile:
    def test_full_template(self, tmp_path: Path):
        f = tmp_path / "code-review.md"
        f.write_text(SAMPLE_TEMPLATE)
        tmpl = _template_from_file(f)
        assert tmpl is not None
        assert tmpl.name == "code-review"
        assert tmpl.description == "Wrap a prompt in a code review context."
        assert "$PROMPT" in tmpl.content
        assert tmpl.source_path == str(f)

    def test_name_defaults_to_stem(self, tmp_path: Path):
        f = tmp_path / "my-template.md"
        f.write_text("---\ndescription: Nameless template\n---\ncontent $PROMPT")
        tmpl = _template_from_file(f)
        assert tmpl is not None
        assert tmpl.name == "my-template"

    def test_missing_description_skips(self, tmp_path: Path):
        f = tmp_path / "bad.md"
        f.write_text("---\nname: bad\n---\nno description field")
        tmpl = _template_from_file(f)
        assert tmpl is None

    def test_nonexistent_file(self, tmp_path: Path):
        tmpl = _template_from_file(tmp_path / "nope.md")
        assert tmpl is None


# ===========================================================================
# load_templates_dir
# ===========================================================================


class TestLoadTemplatesDir:
    def test_loads_md_files(self, tmp_path: Path):
        (tmp_path / "a.md").write_text(
            "---\nname: alpha\ndescription: Alpha\n---\nalpha $PROMPT"
        )
        (tmp_path / "b.md").write_text(
            "---\nname: beta\ndescription: Beta\n---\nbeta $PROMPT"
        )
        templates = load_templates_dir(tmp_path)
        assert len(templates) == 2
        names = {t.name for t in templates}
        assert names == {"alpha", "beta"}

    def test_empty_dir(self, tmp_path: Path):
        assert load_templates_dir(tmp_path) == []

    def test_nonexistent_dir(self):
        assert load_templates_dir("/nonexistent/path") == []

    def test_skips_non_md_files(self, tmp_path: Path):
        (tmp_path / "readme.txt").write_text("not a template")
        (tmp_path / "ok.md").write_text(
            "---\nname: ok\ndescription: OK\n---\ncontent"
        )
        assert len(load_templates_dir(tmp_path)) == 1


# ===========================================================================
# load_all_templates
# ===========================================================================


class TestLoadAllTemplates:
    def test_project_overrides_user(self, tmp_path: Path, monkeypatch):
        user_dir = tmp_path / "user_cfg" / "templates"
        user_dir.mkdir(parents=True)
        (user_dir / "review.md").write_text(
            "---\nname: review\ndescription: User version\n---\nuser $PROMPT"
        )

        project_dir = tmp_path / "project" / ".duh" / "templates"
        project_dir.mkdir(parents=True)
        (project_dir / "review.md").write_text(
            "---\nname: review\ndescription: Project version\n---\nproject $PROMPT"
        )

        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            s = str(self)
            if s.startswith("~"):
                return Path(str(tmp_path / "user_cfg")).parent / s[2:].lstrip("/").replace(
                    ".config/duh/templates", "user_cfg/templates"
                )
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)

        templates = load_all_templates(str(tmp_path / "project"))
        reviews = [t for t in templates if t.name == "review"]
        assert len(reviews) == 1
        assert reviews[0].description == "Project version"

    def test_empty_when_no_templates(self, tmp_path: Path, monkeypatch):
        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()

        original_expanduser = Path.expanduser

        def fake_expanduser(self):
            s = str(self)
            if s.startswith("~"):
                return Path(str(fake_home)) / s[2:].lstrip("/")
            return original_expanduser(self)

        monkeypatch.setattr(Path, "expanduser", fake_expanduser)
        assert load_all_templates(str(tmp_path)) == []


# ===========================================================================
# /template REPL command (_handle_template_command)
# ===========================================================================


class TestHandleTemplateCommand:
    def _make_state(self) -> dict:
        return {
            "templates": {
                "code-review": TemplateDef(
                    name="code-review",
                    description="Code review context",
                    content="Review: $PROMPT",
                ),
                "explain": TemplateDef(
                    name="explain",
                    description="Explain clearly",
                    content="Explain: $PROMPT",
                ),
            },
            "active": None,
        }

    def test_list_templates(self, capsys):
        state = self._make_state()
        _handle_template_command("list", state)
        out = capsys.readouterr().out
        assert "code-review" in out
        assert "explain" in out

    def test_list_empty(self, capsys):
        state = {"templates": {}, "active": None}
        _handle_template_command("list", state)
        out = capsys.readouterr().out
        assert "No templates" in out

    def test_use_sets_active(self, capsys):
        state = self._make_state()
        _handle_template_command("use code-review", state)
        assert state["active"] == "code-review"
        out = capsys.readouterr().out
        assert "code-review" in out

    def test_use_clears_active(self, capsys):
        state = self._make_state()
        state["active"] = "code-review"
        _handle_template_command("use", state)
        assert state["active"] is None
        out = capsys.readouterr().out
        assert "cleared" in out.lower()

    def test_use_nonexistent(self, capsys):
        state = self._make_state()
        _handle_template_command("use nonexistent", state)
        assert state["active"] is None
        out = capsys.readouterr().out
        assert "not found" in out.lower()

    def test_oneshot_render(self, capsys):
        state = self._make_state()
        _handle_template_command("code-review check this function", state)
        out = capsys.readouterr().out
        assert "Review: check this function" in out
        assert state["active"] is None  # should NOT change active

    def test_oneshot_nonexistent(self, capsys):
        state = self._make_state()
        _handle_template_command("bogus some prompt", state)
        out = capsys.readouterr().out
        assert "not found" in out.lower()

    def test_list_shows_active_marker(self, capsys):
        state = self._make_state()
        state["active"] = "explain"
        _handle_template_command("list", state)
        out = capsys.readouterr().out
        assert "(active)" in out


# ===========================================================================
# /template via _handle_slash
# ===========================================================================


class TestSlashTemplate:
    def test_template_list_via_slash(self, capsys):
        engine = _make_engine()
        state = {
            "templates": {
                "cr": TemplateDef(name="cr", description="Code review", content="$PROMPT"),
            },
            "active": None,
        }
        keep, model = _handle_slash(
            "/template list", engine, "m", _make_deps(), template_state=state
        )
        assert keep is True
        out = capsys.readouterr().out
        assert "cr" in out

    def test_template_use_via_slash(self, capsys):
        engine = _make_engine()
        state = {
            "templates": {
                "cr": TemplateDef(name="cr", description="Code review", content="$PROMPT"),
            },
            "active": None,
        }
        keep, model = _handle_slash(
            "/template use cr", engine, "m", _make_deps(), template_state=state
        )
        assert keep is True
        assert state["active"] == "cr"
