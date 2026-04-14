"""Generator for `SECURITY.md`.

Covers: supported versions, private advisory reporting link, disclosure
timeline (ack 3 business days, triage 7 days, critical fix 30 days,
medium fix 90 days), credit policy, safe harbor, scope, and verification
steps (pip install + PEP 740 attestations).

See ADR-053 and docs/superpowers/specs/2026-04-14-vuln-monitoring-design.md
Section 4.4.
"""

from __future__ import annotations

__all__ = ["generate"]


_TEMPLATE = """\
# Security Policy

## Supported Versions

Only the latest minor release line receives security updates.

| Version | Supported |
|---------|-----------|
| {minor_line}.x   | :white_check_mark: |
| < {minor_line}   | :x:                |

## Reporting a Vulnerability

**Please do not open a public issue for security reports.**

File a private advisory via GitHub:

  https://github.com/OWNER/REPO/security/advisories/new

If GitHub is unavailable, email the maintainers listed in `pyproject.toml`
with subject `[SECURITY] {project_name}`.

Include:
- Affected version(s) and commit SHA
- Reproduction steps (minimal test case preferred)
- Expected vs. observed behavior
- Suggested fix or mitigation (optional)
- Your name / handle for credit (optional)

## Disclosure Timeline

We follow a coordinated disclosure model:

| Phase                      | Target                    |
|----------------------------|---------------------------|
| Acknowledge receipt        | within 3 business days    |
| Initial triage + severity  | within 7 days             |
| Critical / High fix        | within 30 days            |
| Medium / Low fix           | within 90 days            |
| Public advisory            | same day as fixed release |

If a 0-day is being actively exploited, we will accelerate this timeline
and ship an out-of-band release.

## Scope

In-scope:
- `{project_name}` source code in this repository
- Published artifacts on PyPI under the `{project_name}` name
- First-party scanners under `duh/security/scanners/`

Out of scope (report upstream):
- Vulnerabilities in third-party dependencies (use their advisory channel)
- Issues in GitHub Actions used by our CI (report to the action maintainer)
- Social engineering of maintainers

## Credit

We credit reporters in the public advisory and CHANGELOG unless you
request otherwise. Our Hall of Fame lives in `docs/security/hall-of-fame.md`.

## Safe Harbor

We will not pursue legal action against researchers who:

1. Make a good-faith effort to avoid privacy violations, destruction of
   data, and interruption of service
2. Give us reasonable time to respond before public disclosure
3. Do not exploit vulnerabilities beyond what is needed to prove impact
4. Comply with all applicable laws

If in doubt, contact us first. We will work with you.

## Verifying Releases

Releases ship with PEP 740 provenance attestations, visible on the PyPI
Release page. To verify before install:

```bash
pip install {project_name}=={latest_version}
# Inspect attestation:
python -m pip show --verbose {project_name}
```

You can also download the attestation JSON directly from
`https://pypi.org/project/{project_name}/{latest_version}/#provenance`
and verify it with Sigstore `cosign` tooling.
"""


def generate(*, project_name: str, latest_version: str) -> str:
    """Return a rendered `SECURITY.md` body for the given project."""
    # Derive "0.4" from "0.4.0".
    parts = latest_version.split(".")
    minor_line = ".".join(parts[:2]) if len(parts) >= 2 else latest_version
    return _TEMPLATE.format(
        project_name=project_name,
        latest_version=latest_version,
        minor_line=minor_line,
    )
