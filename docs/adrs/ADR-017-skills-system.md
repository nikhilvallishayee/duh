# ADR-017: Skills System

**Status**: Accepted  
**Date**: 2026-04-06

## Context

AI coding agents benefit from discoverable, reusable prompt templates that can be invoked by name. These "skills" let users and organizations encode common workflows (commit, review-pr, simplify, etc.) as markdown files with structured metadata.

### What D.U.H. keeps

| Typical feature | D.U.H. | Rationale |
|-----------------|--------|-----------|
| Skill definitions as markdown | Yes | Natural authoring format |
| YAML frontmatter metadata | Yes | Structured discovery info |
| Project-local skills | Yes | `.duh/skills/` directory |
| User-global skills | Yes | `~/.config/duh/skills/` |
| Argument substitution | Yes | `$ARGUMENTS` placeholder |
| Skill tool for model invocation | Yes | Model calls Skill tool to execute |
| Skill marketplace | No | File-based is sufficient |
| Skill versioning | No | Files are versioned with the repo |
| Skill dependencies | No | Each skill is self-contained |

## Decision

### 1. Skill file format

A skill is a markdown file with YAML frontmatter:

```markdown
---
name: commit
description: Create a git commit with a well-crafted message.
when-to-use: When the user asks to commit changes or create a commit.
allowed-tools:
  - Bash
  - Read
  - Glob
model: sonnet
argument-hint: Optional commit message override
---

Review the staged changes and create a commit.

$ARGUMENTS
```

### 2. Frontmatter fields

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | Yes | string | Unique skill identifier (used in `/name` invocation) |
| `description` | Yes | string | Short description for discovery |
| `when-to-use` | No | string | Guidance for the model on when to invoke this skill |
| `allowed-tools` | No | list[str] | Tools the skill is allowed to use (informational) |
| `model` | No | string | Preferred model for this skill |
| `argument-hint` | No | string | Hint about what arguments the skill accepts |

### 3. Skill discovery paths

Skills are loaded from two locations, in order:

1. **User-global**: `~/.config/duh/skills/*.md`
2. **Project-local**: `.duh/skills/*.md` (relative to cwd)

If both locations contain a skill with the same name, the project-local version takes precedence (closer to the work wins).

### 4. SkillDef dataclass

```python
@dataclass
class SkillDef:
    name: str
    description: str
    when_to_use: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    model: str = ""
    content: str = ""
    argument_hint: str = ""
    source_path: str = ""
```

### 5. Loading implementation

```python
def load_skills_dir(path: Path) -> list[SkillDef]:
    """Load all .md skill files from a directory."""

def load_all_skills(cwd: str = ".") -> list[SkillDef]:
    """Load skills from ~/.config/duh/skills/ and .duh/skills/."""
```

### 6. SkillTool

A tool implementing the Tool protocol that the model can invoke:

```python
class SkillTool:
    name = "Skill"
    input_schema = {
        "properties": {
            "skill": {"type": "string"},
            "args": {"type": "string"}
        },
        "required": ["skill"]
    }
```

The tool finds a skill by name, substitutes `$ARGUMENTS` with the provided args, and returns the skill content as the result.

### 7. System prompt injection

Skill names and descriptions are injected into the system prompt so the model knows what skills are available:

```
Available skills:
- commit: Create a git commit with a well-crafted message.
- review-pr: Review a pull request for quality and correctness.
```

The model can then invoke a skill via the Skill tool or when it sees `/skill-name` in user input.

## Architecture

```
CLI startup
  |
  load_all_skills(cwd)
  |  - ~/.config/duh/skills/*.md   (user-global)
  |  - .duh/skills/*.md            (project-local, overrides)
  |
  Build SkillTool(skills=loaded_skills)
  |
  Inject skill names+descriptions into system prompt
  |
  Engine runs with SkillTool available
```

## Consequences

- Skills are plain markdown files -- easy to author, version, and share
- YAML frontmatter provides structured metadata without a separate manifest
- Project-local skills override user-global skills (same precedence as config)
- `$ARGUMENTS` substitution keeps the templating minimal and predictable
- The model discovers skills via the system prompt, invokes via the Skill tool
- No new dependencies required (frontmatter parsing is minimal YAML subset)
- Future: skill directories, skill includes, skill composition
