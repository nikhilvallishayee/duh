"""Tests for the ci_templates package: SHA pin registry + generators."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from duh.security.ci_templates import PINNED_ACTIONS, PinnedAction


def test_pinned_actions_is_non_empty_mapping() -> None:
    assert isinstance(PINNED_ACTIONS, dict)
    assert len(PINNED_ACTIONS) >= 10


def test_every_pinned_action_has_40char_sha() -> None:
    sha_re = re.compile(r"^[0-9a-f]{40}$")
    for name, pin in PINNED_ACTIONS.items():
        assert isinstance(pin, PinnedAction), name
        assert sha_re.match(pin.sha), f"{name}: {pin.sha!r} is not a 40-char SHA"


def test_zizmor_action_is_pinned_to_real_sha() -> None:
    """Regression: zizmor-action must have a real SHA, not a TODO placeholder."""
    pin = PINNED_ACTIONS["zizmorcore/zizmor-action"]
    assert pin.sha != "TODO", "zizmor-action SHA is still a TODO placeholder"
    assert len(pin.sha) == 40, f"zizmor-action SHA is not 40 chars: {pin.sha!r}"
    assert pin.sha == "b1d7e1fb5de872772f31590499237e7cce841e8e"
    assert pin.version == "v0.5.3"


def test_every_pinned_action_has_version_comment() -> None:
    for name, pin in PINNED_ACTIONS.items():
        assert pin.version.startswith("v"), f"{name}: {pin.version!r}"


def test_required_actions_are_present() -> None:
    required = {
        "step-security/harden-runner",
        "actions/checkout",
        "actions/dependency-review-action",
        "github/codeql-action/init",
        "github/codeql-action/analyze",
        "github/codeql-action/upload-sarif",
        "ossf/scorecard-action",
        "zizmorcore/zizmor-action",
        "actions/setup-python",
        "actions/cache",
        "actions/upload-artifact",
        "pypa/gh-action-pypi-publish",
    }
    missing = required - set(PINNED_ACTIONS)
    assert not missing, f"missing pinned actions: {sorted(missing)}"


def test_pinned_action_render_emits_sha_and_comment() -> None:
    pin = PINNED_ACTIONS["actions/checkout"]
    rendered = pin.render()
    assert "actions/checkout@" in rendered
    assert pin.sha in rendered
    assert f"# {pin.version}" in rendered


from duh.security.ci_templates.github_actions import (
    WorkflowTemplate,
    generate_workflow,
)


def test_generate_workflow_minimal_has_required_jobs() -> None:
    body = generate_workflow(template=WorkflowTemplate.MINIMAL)
    assert "name: Security" in body
    assert "dependency-review:" in body
    assert "python-sast:" in body
    assert "workflow-audit:" in body
    # Minimal template MUST NOT contain CodeQL or Scorecard
    assert "codeql:" not in body
    assert "scorecard:" not in body


def test_generate_workflow_minimal_pins_all_actions_with_40char_sha() -> None:
    body = generate_workflow(template=WorkflowTemplate.MINIMAL)
    # Every `uses:` line must be pinned with a 40-char SHA — no TODO placeholders.
    import re
    sha_re = re.compile(r"uses:\s+([^\s]+)@([^\s]+)")
    for match in sha_re.finditer(body):
        ref = match.group(2)
        assert re.fullmatch(r"[0-9a-f]{40}", ref), f"unpinned: {match.group(0)}"


def test_generate_workflow_minimal_triggers_on_pr_and_push() -> None:
    body = generate_workflow(template=WorkflowTemplate.MINIMAL)
    assert "pull_request:" in body
    assert "push:" in body


def test_generate_workflow_minimal_sets_permissions_least_privilege() -> None:
    body = generate_workflow(template=WorkflowTemplate.MINIMAL)
    assert "permissions:" in body
    assert "contents: read" in body
    # Each job that writes SARIF needs security-events: write.
    assert "security-events: write" in body


def test_generate_workflow_standard_adds_harden_runner_audit() -> None:
    body = generate_workflow(template=WorkflowTemplate.STANDARD)
    # Every job should open with Harden-Runner in audit mode.
    assert "step-security/harden-runner@" in body
    assert "egress-policy: audit" in body


def test_generate_workflow_standard_adds_codeql_default_suite() -> None:
    body = generate_workflow(template=WorkflowTemplate.STANDARD)
    assert "codeql:" in body
    assert "github/codeql-action/init@" in body
    assert "github/codeql-action/analyze@" in body
    assert "languages: python" in body
    assert "build-mode: none" in body
    # Standard: queries: security-and-quality on PR, security-extended on schedule
    assert "security-and-quality" in body


def test_generate_workflow_standard_has_all_minimal_jobs_too() -> None:
    body = generate_workflow(template=WorkflowTemplate.STANDARD)
    assert "dependency-review:" in body
    assert "python-sast:" in body
    assert "workflow-audit:" in body


def test_generate_workflow_standard_does_not_include_scorecard() -> None:
    body = generate_workflow(template=WorkflowTemplate.STANDARD)
    assert "scorecard:" not in body


def test_generate_workflow_paranoid_adds_scorecard_job() -> None:
    body = generate_workflow(template=WorkflowTemplate.PARANOID)
    assert "scorecard:" in body
    assert "ossf/scorecard-action@" in body
    # Scorecard must not run on pull_request (it needs the token).
    # We implement this via `if: github.event_name != 'pull_request'`.
    assert "github.event_name != 'pull_request'" in body


def test_generate_workflow_paranoid_keeps_all_standard_jobs() -> None:
    body = generate_workflow(template=WorkflowTemplate.PARANOID)
    for job in ("dependency-review:", "python-sast:", "workflow-audit:", "codeql:", "scorecard:"):
        assert job in body, f"missing job: {job}"


def test_generate_workflow_paranoid_scorecard_uses_harden_runner() -> None:
    body = generate_workflow(template=WorkflowTemplate.PARANOID)
    # The scorecard job must open with harden-runner just like every other
    # job in the standard/paranoid templates.
    scorecard_start = body.index("scorecard:")
    scorecard_section = body[scorecard_start:]
    assert "step-security/harden-runner@" in scorecard_section.split("\n\n", 1)[0] + "\n"


from duh.security.ci_templates.github_actions import generate_dependabot


def test_generate_dependabot_version_is_2() -> None:
    body = generate_dependabot()
    assert "version: 2" in body


def test_generate_dependabot_has_pip_and_actions_ecosystems() -> None:
    body = generate_dependabot()
    assert "package-ecosystem: \"pip\"" in body
    assert "package-ecosystem: \"github-actions\"" in body


def test_generate_dependabot_is_weekly_and_grouped() -> None:
    body = generate_dependabot()
    assert "interval: \"weekly\"" in body
    assert "groups:" in body


from duh.security.ci_templates.security_md import generate as generate_security_md


def test_generate_security_md_has_supported_versions_table() -> None:
    body = generate_security_md(project_name="duh-cli", latest_version="0.4.0")
    assert "## Supported Versions" in body
    assert "| Version" in body
    assert "0.4.x" in body


def test_generate_security_md_has_private_advisory_link() -> None:
    body = generate_security_md(project_name="duh-cli", latest_version="0.4.0")
    assert "## Reporting a Vulnerability" in body
    assert "/security/advisories/new" in body


def test_generate_security_md_has_disclosure_timeline() -> None:
    body = generate_security_md(project_name="duh-cli", latest_version="0.4.0")
    assert "acknowledge" in body.lower()
    assert "3 business days" in body
    assert "7 days" in body
    assert "30 days" in body
    assert "90 days" in body


def test_generate_security_md_has_credit_and_safe_harbor() -> None:
    body = generate_security_md(project_name="duh-cli", latest_version="0.4.0")
    assert "## Credit" in body or "Hall of Fame" in body
    assert "## Safe Harbor" in body


def test_generate_security_md_has_pep740_verification_steps() -> None:
    body = generate_security_md(project_name="duh-cli", latest_version="0.4.0")
    assert "pip install" in body
    assert "PEP 740" in body or "attestation" in body.lower()
