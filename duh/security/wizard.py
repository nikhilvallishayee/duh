"""duh security init — interactive wizard skeleton (detection + plan + write)."""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from importlib import metadata as importlib_metadata
from pathlib import Path
from typing import Literal


@dataclass(frozen=True, slots=True)
class Detection:
    is_python: bool
    is_git_repo: bool
    has_github: bool
    has_docker: bool
    has_go: bool
    available_scanners: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Answers:
    mode: Literal["advisory", "strict", "paranoid"]
    enable_runtime: bool
    extended_scanners: tuple[str, ...]
    generate_ci: bool
    ci_template: Literal["minimal", "standard", "paranoid"]
    install_git_hook: bool
    generate_security_md: bool
    import_legacy: bool
    pin_scanner_versions: bool
    # Optional per-scanner enable map.  When provided, only these scanners
    # are enabled in the rendered config; others are written but disabled.
    # ``None`` preserves the legacy behaviour (enable a fixed default set).
    enabled_scanners: tuple[str, ...] | None = None
    # Optional --fail-on severity threshold (e.g. "high").
    fail_on: str | None = None
    # Optional allow-list of filesystem paths the agent may touch.
    allowed_paths: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannedFile:
    path: Path
    content: str
    mode: int = 0o644

    @property
    def body(self) -> str:
        """Alias for `content`; used by Task 5.8 tests."""
        return self.content


# Alias expected by Task 5.8 tests.
PlanItem = PlannedFile


@dataclass(frozen=True, slots=True)
class WizardResult:
    written: tuple[Path, ...]
    dry_run: bool


def detect(*, project_root: Path) -> Detection:
    is_python = (project_root / "pyproject.toml").exists()
    git_dir = project_root / ".git"
    is_git = git_dir.exists()
    has_github = False
    if is_git:
        cfg = git_dir / "config"
        if cfg.exists():
            try:
                text = cfg.read_text(encoding="utf-8")
                has_github = "github.com" in text
            except OSError:
                pass
    has_docker = shutil.which("docker") is not None
    has_go = shutil.which("go") is not None
    try:
        eps = importlib_metadata.entry_points(group="duh.security.scanners")
        available = tuple(sorted(ep.name for ep in eps))
    except Exception:
        available = ()
    return Detection(
        is_python=is_python,
        is_git_repo=is_git,
        has_github=has_github,
        has_docker=has_docker,
        has_go=has_go,
        available_scanners=available,
    )


_DEFAULT_SCANNER_NAMES = (
    "ruff-sec", "pip-audit", "detect-secrets", "cyclonedx-sbom",
    "duh-repo", "duh-mcp-schema", "duh-mcp-pin",
    "duh-sandbox-lint", "duh-oauth-lint",
)


def _security_json(answers: Answers) -> dict:
    if answers.enabled_scanners is None:
        scanners = {name: {"enabled": True} for name in _DEFAULT_SCANNER_NAMES}
    else:
        enabled_set = set(answers.enabled_scanners)
        names = sorted(set(_DEFAULT_SCANNER_NAMES) | enabled_set)
        scanners = {name: {"enabled": name in enabled_set} for name in names}
    doc: dict = {
        "version": 1,
        "mode": answers.mode,
        "scanners": scanners,
        "runtime": {"enabled": answers.enable_runtime},
        "ci": {
            "generate_github_actions": answers.generate_ci,
            "template": answers.ci_template,
        },
    }
    if answers.fail_on:
        doc["fail_on"] = answers.fail_on
    if answers.allowed_paths:
        doc["allowed_paths"] = list(answers.allowed_paths)
    return doc


def render_plan(
    *,
    detection: Detection,
    answers: Answers,
    project_root: Path,
) -> list[PlannedFile]:
    from duh.security.ci_templates.github_actions import (
        WorkflowTemplate,
        generate_dependabot,
        generate_workflow,
    )
    from duh.security.ci_templates.security_md import generate as generate_security_md

    plan: list[PlannedFile] = []
    plan.append(PlannedFile(
        path=project_root / ".duh" / "security.json",
        content=json.dumps(_security_json(answers), indent=2),
    ))
    plan.append(PlannedFile(
        path=project_root / ".duh" / "security-exceptions.json",
        content=json.dumps({"version": 1, "exceptions": []}, indent=2),
    ))
    if answers.generate_ci and detection.has_github:
        plan.append(PlannedFile(
            path=project_root / ".github" / "workflows" / "security.yml",
            content=generate_workflow(
                template=WorkflowTemplate(answers.ci_template),
            ),
        ))
        plan.append(PlannedFile(
            path=project_root / ".github" / "dependabot.yml",
            content=generate_dependabot(),
        ))
    if answers.generate_security_md:
        plan.append(PlannedFile(
            path=project_root / "SECURITY.md",
            content=generate_security_md(
                project_name=project_root.name or "your-project",
                latest_version="0.1.0",
            ),
        ))
    return plan


def write_plan(plan: list[PlannedFile], *, dry_run: bool) -> WizardResult:
    if dry_run:
        return WizardResult(written=(), dry_run=True)
    written: list[Path] = []
    for pf in plan:
        pf.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = pf.path.with_suffix(pf.path.suffix + ".tmp")
        tmp.write_text(pf.content, encoding="utf-8")
        tmp.replace(pf.path)
        written.append(pf.path)
    return WizardResult(written=tuple(written), dry_run=False)


# ---------------------------------------------------------------------------
# Interactive wizard
# ---------------------------------------------------------------------------

_VALID_SEVERITIES = ("critical", "high", "medium", "low", "info")


def _prompt(
    question: str,
    *,
    default: str,
    input_fn,
    output_fn,
) -> str:
    """Ask *question* with a default suggestion; return the user's answer.

    A blank reply selects *default*.  EOF/KeyboardInterrupt also returns
    the default so non-interactive harnesses behave deterministically.
    """
    suffix = f" [{default}]" if default else ""
    output_fn(f"  {question}{suffix}: ", end="", flush=True)
    try:
        raw = input_fn("")
    except (EOFError, KeyboardInterrupt):
        output_fn("")
        return default
    raw = raw.strip()
    return raw or default


def _prompt_yn(
    question: str,
    *,
    default: bool,
    input_fn,
    output_fn,
) -> bool:
    suggestion = "Y/n" if default else "y/N"
    answer = _prompt(question, default=suggestion, input_fn=input_fn, output_fn=output_fn)
    if answer == suggestion:
        return default
    return answer.strip().lower() in ("y", "yes", "true", "1")


def run_interactive(
    *,
    project_root: Path,
    detection: Detection,
    input_fn=input,
    output_fn=print,
) -> Answers:
    """Run the interactive ``duh security init`` wizard.

    Walks the user through:

    1. Per-scanner enable choices (defaults to ``y`` when the scanner is
       discovered as installed, else ``N``).
    2. ``--fail-on`` severity threshold (default ``high``).
    3. ``allowed_paths`` (comma-separated; empty = no path restriction).

    Returns an :class:`Answers` value the caller can hand to
    :func:`render_plan`.  No filesystem writes happen here.
    """
    output_fn("duh security init")
    output_fn(f"  project root: {project_root}")

    enabled: list[str] = []
    # If the registry surfaces no installed scanners, fall back to the
    # built-in default list so the user can still opt in to the bundled
    # ones.  Tests and minimal environments may pass an empty tuple to
    # exercise just the severity / paths prompts.
    if detection.available_scanners:
        output_fn("  Available scanners:")
        for name in detection.available_scanners:
            on_by_default = True
            if _prompt_yn(
                f"    enable {name}?",
                default=on_by_default,
                input_fn=input_fn,
                output_fn=output_fn,
            ):
                enabled.append(name)

    fail_on = _prompt(
        "Fail-on severity threshold (critical|high|medium|low|info)",
        default="high",
        input_fn=input_fn,
        output_fn=output_fn,
    ).lower()
    if fail_on not in _VALID_SEVERITIES:
        output_fn(
            f"  warning: '{fail_on}' is not a recognised severity; using 'high'."
        )
        fail_on = "high"

    raw_paths = _prompt(
        "Allowed paths (comma-separated; blank = no restriction)",
        default="",
        input_fn=input_fn,
        output_fn=output_fn,
    )
    allowed = tuple(
        p.strip() for p in raw_paths.split(",") if p.strip()
    ) if raw_paths else ()

    return Answers(
        mode="strict",
        enable_runtime=True,
        extended_scanners=(),
        generate_ci=False,
        ci_template="standard",
        install_git_hook=False,
        generate_security_md=False,
        import_legacy=False,
        pin_scanner_versions=True,
        enabled_scanners=tuple(enabled),
        fail_on=fail_on,
        allowed_paths=allowed,
    )


from dataclasses import dataclass as _dc


@_dc(frozen=True, slots=True)
class LegacyException:
    id: str
    source: str
    scope: dict


def import_legacy_configs(*, project_root: Path) -> list[LegacyException]:
    out: list[LegacyException] = []
    bandit = project_root / ".bandit"
    if bandit.is_file():
        for line in bandit.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("skips"):
                _, _, rhs = line.partition("=")
                for code in rhs.split(","):
                    code = code.strip()
                    if code:
                        out.append(LegacyException(id=code, source=".bandit", scope={}))
    semgrepignore = project_root / ".semgrepignore"
    if semgrepignore.is_file():
        for line in semgrepignore.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            out.append(LegacyException(
                id="SEMGREP-IGNORE", source=".semgrepignore", scope={"file_glob": line},
            ))
    return out
