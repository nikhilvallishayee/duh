# D.U.H. Vulnerability Monitoring — Full Design

**ADR:** [ADR-053](../../adrs/ADR-053-continuous-vulnerability-monitoring.md)
**Date:** 2026-04-14
**Status:** Proposed

## 0. Summary

Three-layer pluggable security module under `duh/security/`:

1. **CLI batch** (`duh security init | scan | diff | exception ...`)
2. **Scanner plugin system** via `importlib.metadata` entry points
3. **Runtime policy resolver** on the existing 28-event hook bus

One shared `SecurityPolicy` config (`.duh/security.json` + `pyproject.toml` fallback). Bundled Minimal tier is pure-Python, offline-capable. Extended and Paranoid tiers are opt-in at wizard time.

Answers threat-model questions A (supply chain) + B (source-level exploits) + C (LLM-specific, tactical layer). Defers architectural LLM hardening (taint propagation, confirmation tokens, lethal trifecta, signed manifests) to [ADR-054](../../adrs/ADR-054-llm-specific-security-hardening.md).

---

## 1. Architecture overview

```
  ┌──────────────────── .duh/security.json ──────────────────────┐
  │    .duh/security-exceptions.json   [tool.duh.security]       │
  └──────────────────────────────┬───────────────────────────────┘
                                 │ SecurityPolicy (pydantic v2)
        ┌────────────────────────┼────────────────────────┐
        ▼                        ▼                        ▼
  ┌──────────┐            ┌────────────┐           ┌────────────┐
  │ Layer 1  │            │  Layer 2   │           │  Layer 3   │
  │ CLI      │            │  Plugins   │           │  Runtime   │
  │ batch    │            │ (scanners, │           │  hook      │
  │          │            │  entry-pt) │           │  resolver  │
  └─────┬────┘            └─────┬──────┘           └─────┬──────┘
        │                        │                        │
        │   duh security scan    │ duh.security.scanners  │  PRE_TOOL_USE
        │   duh security init    │ entry_points           │  POST_TOOL_USE
        │   duh security diff    │                        │  SESSION_START
        │   duh security except  │                        │  SESSION_END
        │                        │                        │
        └────────────┬───────────┴───────────┬────────────┘
                     ▼                        ▼
              ┌──────────────────────────────────────────┐
              │  duh.security.engine                     │
              │  - ScannerRegistry (entry-point discovery)│
              │  - Runner (in-process + subprocess)      │
              │  - FindingStore (cached SARIF-ish)       │
              │  - Resolver (policy → decision)          │
              │  - ExceptionStore (alias-expanded)       │
              └──────────────────────────────────────────┘
```

### Module layout

```
duh/security/
├── __init__.py            # Public API: scan(), init(), resolve()
├── config.py              # Pydantic SecurityPolicy, dual-config loader
├── engine.py              # ScannerRegistry, Runner, FindingStore
├── finding.py             # Finding, Severity, Location dataclasses
├── policy.py              # resolve() — policy-as-data decision function
├── exceptions.py          # ExceptionStore, alias expansion, expiry
├── wizard.py              # duh security init interactive flow
├── cli.py                 # duh security subcommand dispatch
├── hooks.py               # PRE/POST_TOOL_USE / SESSION_* bindings
├── ci_templates/
│   ├── __init__.py
│   ├── github_actions.py  # security.yml generator + dependabot.yml
│   └── security_md.py     # SECURITY.md generator
└── scanners/
    ├── __init__.py        # Scanner Protocol, InProcessScanner, SubprocessScanner
    ├── ruff_sec.py
    ├── pip_audit.py
    ├── detect_secrets.py
    ├── cyclonedx_sbom.py
    ├── semgrep_ext.py
    ├── osv_scanner.py
    ├── gitleaks.py
    ├── bandit_fallback.py
    ├── duh_repo.py
    ├── duh_mcp_schema.py
    ├── duh_mcp_pin.py
    ├── duh_sandbox_lint.py
    └── duh_oauth_lint.py
```

### Shared state objects

| Object | Lifetime | Mutability |
|---|---|---|
| `SecurityPolicy` | Per-session | Frozen (pydantic) |
| `ExceptionStore` | Process | Mutable — CLI writes |
| `FindingStore` | Per-session, cached to `.duh/security-cache.json` | Append-only within a scan run |
| `ScannerRegistry` | Per-process | Loaded once from entry points |

---

## 2. Config schema

### 2.1 Precedence

```
1. CLI flags                    (--severity=critical, --scanner=pip-audit)
2. .duh/security.json           (project-local, primary)
3. [tool.duh.security] in pyproject.toml
4. ~/.config/duh/security.json  (user defaults across projects)
5. Built-in defaults
```

### 2.2 `.duh/security.json` example

```json
{
  "version": 1,
  "mode": "strict",
  "fail_on": ["critical", "high"],
  "report_on": ["medium", "high", "critical"],
  "block_on_new_only": true,
  "on_scanner_error": "continue",
  "max_db_staleness_days": 7,
  "allow_network": true,
  "exceptions_file": ".duh/security-exceptions.json",
  "cache_file": ".duh/security-cache.json",

  "scanners": {
    "ruff-sec":         { "enabled": true,  "args": [] },
    "pip-audit":        { "enabled": true,  "requirement_file": "pyproject.toml" },
    "detect-secrets":   { "enabled": true,  "baseline": ".duh/.secrets.baseline" },
    "cyclonedx-sbom":   { "enabled": true,  "output": "sbom.cdx.json" },
    "duh-repo":         { "enabled": true },
    "duh-mcp-schema":   { "enabled": true },
    "duh-mcp-pin":      { "enabled": true },
    "duh-sandbox-lint": { "enabled": true,  "enforce": true },
    "duh-oauth-lint":   { "enabled": true,  "enforce": true },

    "semgrep":     { "enabled": "auto", "config": ["p/python","r/python.security"] },
    "osv-scanner": { "enabled": "auto" },
    "gitleaks":    { "enabled": "auto" },
    "bandit":      { "enabled": false }
  },

  "runtime": {
    "enabled": true,
    "block_pre_tool_use": true,
    "rescan_on_dep_change": true,
    "session_start_audit": true,
    "session_end_summary": true,
    "resolver_timeout_s": 5.0,
    "fail_open_on_timeout": true
  },

  "ci": {
    "generate_github_actions": true,
    "template": "standard"
  }
}
```

### 2.3 Mode presets

`mode` is sugar that sets `fail_on` / `report_on` / `runtime.block_pre_tool_use`:

| Mode | `fail_on` | `report_on` | `block_pre_tool_use` | `on_scanner_error` |
|---|---|---|---|---|
| `advisory` | `[]` | `["low","medium","high","critical"]` | `false` | `"warn"` |
| `strict` *(default)* | `["critical","high"]` | `["medium","high","critical"]` | `true` | `"continue"` |
| `paranoid` | `["critical","high","medium"]` | `["low","medium","high","critical"]` | `true` | `"fail"` |

Explicit keys always override the preset.

### 2.4 `pyproject.toml` escape hatch

```toml
[tool.duh.security]
mode = "strict"
fail_on = ["critical", "high"]
allow_network = true

[tool.duh.security.scanners.ruff-sec]
enabled = true

[tool.duh.security.scanners.semgrep]
enabled = "auto"
config = ["p/python", "r/python.security"]

[tool.duh.security.runtime]
enabled = true
block_pre_tool_use = true
```

Read with `tomllib` (stdlib since 3.11). Precedence below `.duh/security.json`.

### 2.5 `"auto"` semantics

`"auto"` = "wizard-detect at init time and use if available at runtime; skip silently otherwise". Lets the same config file travel between laptops with different tooling.

### 2.6 Pydantic model highlights

```python
class SecurityPolicy(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    mode: Literal["advisory", "strict", "paranoid"] = "strict"
    fail_on: tuple[Severity, ...] = ("critical", "high")
    report_on: tuple[Severity, ...] = ("medium", "high", "critical")
    block_on_new_only: bool = True
    on_scanner_error: Literal["continue", "warn", "fail"] = "continue"
    max_db_staleness_days: int = Field(default=7, ge=1, le=90)
    allow_network: bool = True
    exceptions_file: Path = Path(".duh/security-exceptions.json")
    cache_file: Path = Path(".duh/security-cache.json")

    scanners: dict[str, ScannerConfig] = Field(default_factory=dict)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    ci: CIConfig = Field(default_factory=CIConfig)

    @model_validator(mode="after")
    def _apply_mode_preset(self) -> "SecurityPolicy":
        # apply mode defaults without clobbering explicit fields
        ...
```

The model exports `security.schema.json` via `model_json_schema()` for IDE autocomplete and reusable-workflow consumers.

---

## 3. Scanner protocol + registry

### 3.1 Protocol

```python
# duh/security/scanners/__init__.py

from typing import Protocol, Literal
from pathlib import Path
from duh.security.finding import Finding
from duh.security.config import ScannerConfig

Tier = Literal["minimal", "extended", "paranoid", "custom"]

class Scanner(Protocol):
    name: str
    tier: Tier
    default_severity: tuple[Severity, ...]

    def available(self) -> bool:
        """True if this scanner can run right now.
        InProcessScanner → check import; SubprocessScanner → check binary on PATH."""
        ...

    async def scan(
        self,
        target: Path,
        cfg: ScannerConfig,
        *,
        changed_files: list[Path] | None = None,
    ) -> list[Finding]:
        ...
```

### 3.2 Base classes

```python
class InProcessScanner:
    """Pure-Python scanner importing its implementation."""
    name: str
    tier: Tier
    _module_name: str  # for availability check

    def available(self) -> bool:
        return importlib.util.find_spec(self._module_name) is not None

    async def scan(...) -> list[Finding]:
        # subclasses implement actual scan; base provides:
        # - error wrapping (scanner raises → engine converts to ScannerResult)
        # - asyncio.wait_for timeout
        # - changed_files filtering passthrough
        raise NotImplementedError

class SubprocessScanner:
    """Shell-out scanner with a JSON/SARIF output parser."""
    name: str
    tier: Tier
    _binary: str
    _argv_template: list[str]        # placeholders: {target} {config} {changed}
    _parser: Callable[[bytes], list[Finding]]

    def available(self) -> bool:
        return shutil.which(self._binary) is not None

    async def scan(...) -> list[Finding]:
        # subclasses override _argv_template / _parser; base provides:
        # - asyncio.create_subprocess_exec with stdout/stderr capture
        # - stderr streamed to FindingStore.log (visibility on hangs)
        # - parser invoked on stdout; parse errors become DUH-SCANNER-ERROR findings
        # - timeout + graceful SIGTERM → SIGKILL
        raise NotImplementedError
```

### 3.3 Entry-point discovery

```toml
[project.entry-points."duh.security.scanners"]
ruff-sec          = "duh.security.scanners.ruff_sec:RuffSecScanner"
pip-audit         = "duh.security.scanners.pip_audit:PipAuditScanner"
detect-secrets    = "duh.security.scanners.detect_secrets:DetectSecretsScanner"
cyclonedx-sbom    = "duh.security.scanners.cyclonedx_sbom:CycloneDXScanner"
duh-repo          = "duh.security.scanners.duh_repo:RepoScanner"
duh-mcp-schema    = "duh.security.scanners.duh_mcp_schema:MCPSchemaScanner"
duh-mcp-pin       = "duh.security.scanners.duh_mcp_pin:MCPPinScanner"
duh-sandbox-lint  = "duh.security.scanners.duh_sandbox_lint:SandboxLintScanner"
duh-oauth-lint    = "duh.security.scanners.duh_oauth_lint:OAuthLintScanner"
semgrep           = "duh.security.scanners.semgrep_ext:SemgrepScanner"
osv-scanner       = "duh.security.scanners.osv_scanner:OSVScanner"
gitleaks          = "duh.security.scanners.gitleaks:GitleaksScanner"
bandit            = "duh.security.scanners.bandit_fallback:BanditScanner"
```

Third parties publish scanner packages independently:

```toml
# my-custom-scanner/pyproject.toml
[project.entry-points."duh.security.scanners"]
my-custom = "my_custom_scanner:MyScanner"
```

`pip install duh-cli[security] my-custom-scanner` → `duh security scan` picks it up.

### 3.4 `Finding` dataclass

```python
@dataclass(frozen=True, slots=True)
class Finding:
    id: str                          # "CVE-2025-12345", "DUH-MCP-001", "B602"
    aliases: tuple[str, ...]
    scanner: str
    severity: Severity
    message: str
    description: str
    location: Location               # file, line_start, line_end, snippet
    package: str | None
    version: str | None
    fixed_in: str | None
    cwe: tuple[int, ...]
    metadata: dict[str, Any]
    fingerprint: str                 # sha256(id + file + line + scanner)

    def to_sarif(self) -> dict: ...
    def to_json(self) -> dict: ...
    @classmethod
    def from_json(cls, data: dict) -> "Finding": ...
```

`fingerprint` is the deduplication and delta-diff key. Stability is a test invariant.

---

## 4. Bundled scanners

### 4.1 Minimal tier (ships with `pip install duh-cli[security]`)

| Scanner | Kind | Runtime cost | Notes |
|---|---|---|---|
| `ruff-sec` | InProcess (`ruff check --select S`) | ~1s / 14K LOC | Replaces Bandit for 85% of rules at 25x speed |
| `pip-audit` | InProcess (`pip_audit.cli`) | ~3–5s (lock), ~10–20s (requirements) | Cached in `~/.duh/security-cache/pip-audit-db` |
| `detect-secrets` | InProcess (`detect_secrets.core.scan`) | ~2s / 14K LOC | Baseline-delta native |
| `cyclonedx-sbom` | InProcess (`cyclonedx_py`) | ~1s | Emits CycloneDX 1.7 JSON |
| `duh-repo` | Custom InProcess | <50ms | Fires at SESSION_START |
| `duh-mcp-schema` | Custom InProcess | <10ms / tool | Fires per MCP connect |
| `duh-mcp-pin` | Custom InProcess | <5ms / tool | Fires per MCP connect |
| `duh-sandbox-lint` | Custom InProcess | ~1s delta-only | Hard-enforced on D.U.H.'s own CI |
| `duh-oauth-lint` | Custom InProcess | <500ms delta-only | Hard-enforced on D.U.H.'s own CI |

Full Minimal scan on D.U.H.-sized project: **under 10 seconds**.

### 4.2 Custom scanner detail

#### `duh-repo` — project-file RCE defense (CVE-2025-59536 class)
Runs at SESSION_START, before any other hook. Refuses to auto-load `.duh/hooks/*`, `.duh/mcp.json`, `.duh/settings.json`, `.env`, `.envrc`, `.tool-versions` unless the current cwd is on an explicit `trusted_paths` allowlist (stored in `~/.duh/trusted_paths.json`). Requires interactive TOFU: first load shows SHA256, user types `yes` to trust. On subsequent loads, any hash change re-triggers the dialog. Never honors repo-local `DUH_BASE_URL` / `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` env overrides — those must come from shell or user config.

#### `duh-mcp-schema` — MCP tool poisoning defense
Runs on MCP handshake. Lints every tool `description` and parameter doc:
- Regex + classifier for imperative verbs targeting the model (`ignore previous`, `always also call`, `before responding`)
- Reject zero-width characters (U+200B..U+200D, U+FEFF)
- Reject bidi overrides (U+202A..U+202E, U+2066..U+2069)
- Reject Unicode Tag Characters (U+E0000..U+E007F) — GlassWorm attack
- Reject invisible variation selectors (U+FE00..U+FE0F, U+E0100..U+E01EF)
- Detect base64 blobs >32 bytes, raise `DUH-MCP-BASE64`
- Detect exfil-pattern URLs (`curl`, `wget`, IP literals, suspicious TLDs)
- NFKC-normalize — any reshape raises `DUH-MCP-UNICODE`

#### `duh-mcp-pin` — MCP rug-pull defense (CVE-2025-54136 MCPoison class)
On first connect to a server, computes `sha256(schema + command + args + env)` for every tool and stores it in `~/.duh/mcp_trust.json`:

```json
{
  "github-mcp-server": {
    "tools": {
      "create_issue": {
        "hash": "sha256:…",
        "approved_at": "2026-04-14T19:00:00Z",
        "approved_by": "nikhil@local"
      }
    }
  }
}
```

On subsequent connects, any tool whose hash changed is rejected with a diff view: `duh-sec: mcp server 'github-mcp-server' tool 'create_issue' changed — re-approve? [schema diff shown]`. User must re-approve or disable the tool.

#### `duh-sandbox-lint` — sandbox profile bypass defense (CVE-2025-59532 class)
AST-walks the D.U.H. source tree looking for `UntrustedStr`-tagged values (once ADR-054 lands) or, in the interim, any f-string / `.format()` / string concat that reaches into `Seatbelt.generate_profile()` / `Landlock.add_rule()` / any `.sb` file write. Flagged at `high` severity. Hard-enforced on D.U.H.'s own CI — test suite fails if new code introduces a flow. Replays CVE-2025-59532's `cwd=../..` PoC as a regression test fixture.

#### `duh-oauth-lint` — localhost OAuth hardening
Walks `duh/auth/openai_chatgpt.py` and any future OAuth adapter:
- Reject `0.0.0.0` bindings; require `127.0.0.1`
- Reject `SO_REUSEADDR` or fall-through-port patterns
- Require HMAC-bound `state` to (PID, start_time)
- Require `0o600` on credential files at load time
- Require owner check (`st_uid == os.getuid()`)
- Require symlink refusal (`os.path.islink()` check before read)
- Require exact redirect URL match (no prefix matching)
- Require PKCE S256, not plain
- Forbid logging the Authorization header or access token

### 4.3 Extended tier

| Scanner | Install | When to enable |
|---|---|---|
| `semgrep` CE | `pip install semgrep` | Richer rule coverage; first-class diff-aware via `semgrep ci --baseline-ref` |
| `osv-scanner` | `brew install osv-scanner` / `go install` | Polyglot dep scanning, OSV.dev direct |
| `gitleaks` | `brew install gitleaks` | Full git-history secret scan |
| `bandit` (fallback) | `pip install bandit[toml]` | Only if ruff-sec coverage is insufficient |

Wizard detects availability at init time and offers to enable each.

### 4.4 Paranoid tier (GitHub Actions templates)

When the user answers "GitHub Actions" + "paranoid template" at wizard time, D.U.H. writes:

**`.github/workflows/security.yml`** — jobs:
- `dependency-review` (on PR only, ~10s) — `actions/dependency-review-action@<SHA> # v4.9.0`, `fail-on-severity: high`
- `codeql` (on PR + schedule, ~2–3 min) — `github/codeql-action/init@<SHA> # v3.35.1`, `build-mode: none`, `security-extended` on schedule, `security-and-quality` on PR
- `python-sast` (on PR, ~30–60s) — ruff-sec + pip-audit + bandit SARIF upload
- `workflow-audit` (~5s) — `zizmorcore/zizmor-action@<SHA>`
- `scorecard` (on push + schedule, not PR, ~1–2 min) — `ossf/scorecard-action@<SHA> # v2.4.3`
- Every job starts with `step-security/harden-runner@<SHA> # v2.17.0` in `egress-policy: audit`

All actions SHA-pinned with `# vX.Y.Z` trailing comment. Dependabot keeps them current.

**`.github/dependabot.yml`** — weekly grouped updates for `pip` + `github-actions`.

**`.github/workflows/publish.yml` amended** — `pypa/gh-action-pypi-publish@<SHA> # v1.14.0+` with Trusted Publishing (OIDC). Removes any long-lived `PYPI_API_TOKEN`. PEP 740 attestations fire automatically.

**`SECURITY.md`** — private advisory flow, supported versions, disclosure timeline, safe harbor, hall of fame.

Expected total CI wall-clock: **~2.5 min** (CodeQL is the long pole, runs parallel with existing 30s test job).

---

## 5. Wizard UX + Exception model

### 5.1 `duh security init` flow

```
$ duh security init

D.U.H. Security — interactive setup.

Detected:
  - Python project (pyproject.toml)
  - Git repository on GitHub (github.com/you/proj)
  - Runtime: CPython 3.12
  - Docker: not installed
  - Go toolchain: not installed
  - Available scanners: ruff-sec, pip-audit, detect-secrets, cyclonedx-sbom,
    duh-repo, duh-mcp-schema, duh-mcp-pin, duh-sandbox-lint, duh-oauth-lint
  - Optional (Extended tier): semgrep[pip], osv-scanner[go], gitleaks[go]

? Security posture:  (↑↓ move, Enter select)
  Advisory     — findings reported, nothing blocks
> Strict       — block LLM tool calls on CRITICAL/HIGH      [recommended]
  Paranoid     — block on MEDIUM+, every session starts with audit

? Enable runtime hooks?  (gates tool calls during agent execution)  (Y/n) Y

? Install optional Extended scanners?
  [ ] semgrep  — 3,000+ rules, 100MB pip install, ~30s scan
  [ ] osv-scanner  — Go binary, polyglot dep scan
  [ ] gitleaks  — Go binary, git-history secret scan
  (Space to toggle, Enter to confirm.)
  > none

? Generate GitHub Actions security workflow?  (Y/n) Y

? Workflow template:
> minimal      — dependency-review + pip-audit + ruff-sec + zizmor
  standard     — minimal + CodeQL (default suite) + Harden-Runner audit
  paranoid     — standard + Scorecard weekly + CodeQL security-extended

? Install pre-push git hook?
  Runs `duh security scan --baseline @{upstream}` before every push.
  Bypass with `git push --no-verify`. Remove with `duh security hook uninstall git`.
  (Y/n) Y

? Generate SECURITY.md from template?  (Y/n) Y

? Import existing scanner configs?
  Found:
  [x] .bandit       → merge ignores into duh-sec exception file
  [x] .semgrepignore → merge into duh-sec exception file

? Pin scanner versions?  (recommended — prevents silent rule drift)  (Y/n) Y

Writing .duh/security.json ...................... done
Writing .duh/security-exceptions.json (empty) ... done
Writing .duh/.secrets.baseline ................... done
Writing .github/workflows/security.yml ........... done
Writing .github/dependabot.yml ................... done
Updating .github/workflows/publish.yml ........... done (PEP 740 + Trusted Publishing)
Writing SECURITY.md .............................. done
Installing pre-push hook ......................... done
  (To disable later: duh security hook uninstall git, or
   git push --no-verify to bypass once.)

Next steps:
  duh security scan               — run all enabled scanners once
  duh security status             — show active findings + exceptions
  duh security doctor             — diagnose scanner installs + CI config
```

Key properties:
- **Atomic partial writes.** Each answer is persisted before the next question. Ctrl-C leaves consistent state.
- **Dry-run mode.** `duh security init --dry-run` prints every file that would be written to stdout, byte-identical to the real run, without touching disk.
- **Detection first, questions second.** The detection pass prints what it found; the user can sanity-check before answering.
- **Pre-push hint.** When the git hook is installed, the wizard prints the disable command loudly so the user always knows the escape.

### 5.2 Exception schema

`.duh/security-exceptions.json` (committed to git; per-user override at `~/.config/duh/exceptions.json` merged at read time):

```json
{
  "version": 1,
  "exceptions": [
    {
      "id": "CVE-2025-12345",
      "aliases": ["GHSA-wxyz-1234-5678", "OSV-2025-12345"],
      "scope": {
        "scanner": "pip-audit",
        "package": "requests",
        "version_range": "<2.32",
        "file_glob": null
      },
      "reason": "patch pending upstream, exploitability blocked by our egress policy",
      "added_by": "nikhil@laptop",
      "added_at": "2026-04-14T19:00:00Z",
      "expires_at": "2026-06-01T00:00:00Z",
      "ticket": "SEC-442",
      "permanent": false
    }
  ]
}
```

Field-by-field rules:

| Field | Rule |
|---|---|
| `id` | Canonical scanner ID (`CVE-*`, `GHSA-*`, `B602`, `DUH-MCP-001`, etc.) |
| `aliases` | Auto-populated at add time from scanner output; supports alias-expanded matching |
| `scope` | Narrows the exception to specific package/file/version; `null` fields mean "any" |
| `reason` | **Required.** CLI refuses exception without `--reason` |
| `added_by` | `$USER@$(hostname)` captured at creation; provenance, not authentication |
| `added_at` / `expires_at` | ISO-8601 UTC. CLI refuses `expires_at` in the past. Default cap: 90 days. `--long-term` allows up to 365 days |
| `ticket` | Optional freeform reference |
| `permanent` | Default `false`. `--permanent` CLI flag required to set true; prints a loud warning |

### 5.3 Exception CLI

```
duh security exception add <ID> --reason=<text> --expires=<date> [--scope=pkg=name,version=<range>] [--ticket=<ref>] [--permanent] [--long-term]
duh security exception list [--expiring-soon] [--expired] [--scanner=<name>]
duh security exception remove <ID>
duh security exception renew <ID> --expires=<date>
duh security exception audit                # prints expired, unused, dead entries
duh security exception import --from=.bandit
```

### 5.4 Exception audit loop

1. **`SESSION_START` hook** — if `runtime.session_start_audit: true`, scan exception file for entries expiring in next 7 days; emit `NOTIFICATION` event.
2. **`duh security scan`** — any exception with `expires_at < now()` treated as removed; underlying finding fires normally.
3. **Weekly cron job** (via GitHub Actions paranoid template) — runs `duh security exception audit`, posts a PR comment or issue on expired/unused entries.

**Unused detection:** every scan writes matched exception IDs to `.duh/security-cache.json`. An exception matched zero findings over 30 days is flagged for removal.

---

## 6. Runtime hook integration + PR delta gating

### 6.1 Policy resolver — single pure function

```python
# duh/security/policy.py

@dataclass(frozen=True, slots=True)
class PolicyDecision:
    action: Literal["allow", "warn", "block"]
    reason: str
    findings: tuple[Finding, ...]
    remediation: str | None

def resolve(
    event: ToolUseEvent,
    policy: SecurityPolicy,
    findings: FindingStore,
    exceptions: ExceptionStore,
) -> PolicyDecision:
    active = findings.active(scope=event.cwd)
    active = [f for f in active if not exceptions.covers(f, at=now())]
    blocking = [f for f in active if f.severity in policy.fail_on]
    warning  = [f for f in active if f.severity in policy.report_on and f not in blocking]

    if event.tool in _DANGEROUS_TOOLS and blocking:
        top = blocking[0]
        return PolicyDecision(
            action="block",
            reason=f"{len(blocking)} unresolved {top.severity} finding(s)",
            findings=tuple(blocking),
            remediation=(
                f"Fix {top.id} (fixed in {top.fixed_in}) or add exception:\n"
                f"  duh security exception add {top.id} --reason='...' --expires=YYYY-MM-DD"
            ),
        )

    if warning:
        return PolicyDecision(action="warn", reason=f"{len(warning)} below threshold",
                              findings=tuple(warning), remediation=None)

    return PolicyDecision(action="allow", reason="clear", findings=(), remediation=None)


_DANGEROUS_TOOLS: frozenset[str] = frozenset({
    "Bash", "Write", "Edit", "MultiEdit", "NotebookEdit",
    "WebFetch", "Docker", "HTTP",
})
```

- **No state beyond args.** Trivially unit-testable.
- **O(n) over active findings.** Agent sessions have tens of findings max.
- **Read-only against cache.** Never triggers a new scan.
- **Escape hatch:** `SecurityPolicy.custom_resolver = "path/to/module.resolve"` for power users.

### 6.2 Hook bindings

```python
# duh/security/hooks.py

def install(registry: HookRegistry, ctx: SecurityContext) -> None:
    if not ctx.policy.runtime.enabled:
        return

    async def pre_tool_use(event, data):
        try:
            decision = await asyncio.wait_for(
                asyncio.to_thread(
                    resolve, data["event"], ctx.policy, ctx.findings, ctx.exceptions,
                ),
                timeout=ctx.policy.runtime.resolver_timeout_s,
            )
        except asyncio.TimeoutError:
            logger.warning("security resolver timed out, fail-open")
            ctx.console.notify("duh-sec: resolver timeout — allowing tool call")
            return HookResponse(decision="continue")

        if decision.action == "block":
            return HookResponse(decision="block", message=decision.remediation)
        if decision.action == "warn":
            ctx.console.warn(decision.reason)
        return HookResponse(decision="continue")

    async def post_tool_use(event, data):
        if ctx.policy.runtime.rescan_on_dep_change:
            changed = data.get("files_written", [])
            if any(_is_dep_file(p) for p in changed):
                await ctx.engine.scan_delta(changed)

    async def session_start(event, data):
        if ctx.policy.runtime.session_start_audit:
            expiring = ctx.exceptions.expiring_within(days=7)
            if expiring:
                ctx.console.notify(
                    f"{len(expiring)} security exception(s) expire in 7 days"
                )
        ctx.findings.snapshot_for_session(data["session_id"])

    async def session_end(event, data):
        if ctx.policy.runtime.session_end_summary:
            delta = ctx.findings.since_session_start(data["session_id"])
            if delta:
                ctx.console.summary(delta)

    for event_type, callback in [
        (HookEvent.PRE_TOOL_USE,  pre_tool_use),
        (HookEvent.POST_TOOL_USE, post_tool_use),
        (HookEvent.SESSION_START, session_start),
        (HookEvent.SESSION_END,   session_end),
    ]:
        registry.register(HookConfig(
            event=event_type,
            hook_type=HookType.FUNCTION,
            name=f"duh-security-{event_type.value}",
            callback=callback,
            timeout=ctx.policy.runtime.resolver_timeout_s,
        ))
```

The security module registers hooks the same way any user plugin would — it's a first-class consumer of the hook bus, not a bypass of it.

### 6.3 PR delta gating

**Layer-1 delta (CI workflow):**

```yaml
jobs:
  security-delta:
    if: github.event_name == 'pull_request'
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@<SHA>
        with:
          fetch-depth: 0
      - run: |
          duh security scan --baseline origin/${{ github.base_ref }} \
                            --fail-on=high \
                            --sarif=findings.sarif
      - uses: github/codeql-action/upload-sarif@<SHA>
        if: always()
        with:
          sarif_file: findings.sarif
```

`duh security scan --baseline <ref>` steps:
1. Check out `<ref>` to a temp worktree
2. Run enabled scanners against baseline
3. Fingerprint every finding at head and base
4. Report only fingerprints present at head and absent at base
5. Exit non-zero if any net-new finding is in `fail_on`

**Layer-2 delta (scanner-native where available):**

| Scanner | Native delta |
|---|---|
| `semgrep` | `semgrep ci --baseline-ref origin/main` |
| `detect-secrets` | `detect-secrets scan --baseline .secrets.baseline` |
| `actions/dependency-review-action` | Already diff-aware in GHA |
| `ruff-sec`, `duh-*` | Changed-files fast path via `git diff --name-only base..HEAD` |
| Others | Layer-1 fingerprint diff |

**Pre-push git hook:**

Installed by wizard on opt-in. Writes `.git/hooks/pre-push` (or extends an existing one):

```bash
#!/usr/bin/env sh
#
# Installed by `duh security init`.
# To disable once: git push --no-verify
# To remove entirely: duh security hook uninstall git
#
if ! duh security scan --baseline "@{upstream}" --fail-on=high --quiet; then
    echo ""
    echo "duh-sec: push blocked by security findings."
    echo "  Inspect:  duh security scan --baseline @{upstream}"
    echo "  Bypass:   git push --no-verify"
    echo "  Disable:  duh security hook uninstall git"
    exit 1
fi
```

### 6.4 Three runtime invariants

1. **Block surfaces through existing `HookResponse`** (ADR-045). No new protocol.
2. **Scans don't run on every `PRE_TOOL_USE`.** Scans run at `SESSION_START`, on `POST_TOOL_USE` dep-file-change, and on explicit `duh security scan`. Resolver is a pure function of cached state.
3. **Resolver fail-open on timeout.** A hung scanner cannot wedge tool execution — timeout → `allow` with a `NOTIFICATION`. The batch scanner (`duh security scan`) stays fail-closed; different blast radius.

---

## 7. Error handling + test plan

### 7.1 Scanner failure isolation

```python
async def _run_scanner(scanner, target, cfg, changed_files, timeout):
    if not scanner.available():
        return ScannerResult(status="skipped", reason=f"{scanner.name} not installed", ...)
    t0 = time.monotonic()
    try:
        findings = await asyncio.wait_for(
            scanner.scan(target, cfg, changed_files=changed_files),
            timeout=timeout,
        )
        return ScannerResult(status="ok", findings=findings, duration_ms=...)
    except asyncio.TimeoutError:
        return ScannerResult(status="timeout", reason=f"exceeded {timeout}s", ...)
    except Exception as exc:
        logger.exception("%s raised", scanner.name)
        return ScannerResult(status="error", reason=repr(exc), ...)
```

**`on_scanner_error` knob:**

| Value | Effect |
|---|---|
| `"continue"` *(default)* | Log, mark skipped, proceed |
| `"warn"` | Same + stderr warning + SARIF error notification |
| `"fail"` *(paranoid default)* | Whole run fails non-zero — prevents "silently broken scanner means no findings" |

### 7.2 Network / advisory DB fallback

1. **No network:** scanners use `~/.duh/security-cache/` HTTP cache first. If cache age > `max_db_staleness_days`, emit `DUH-DB-STALE` at `medium`.
2. **5xx / rate-limited:** retry twice with exponential backoff, then fall back to cache with same staleness warning.
3. **Explicit warming:** `duh security db sync` (CI step in paranoid template) fetches fresh advisories before the scan job.

### 7.3 Graceful degradation decision tree

```
scanner.available() == False
    ├── tier == "minimal"  → skip, add DUH-SEC-001 info finding
    └── tier == "extended" → skip silently (opt-in)

scanner raises
    ├── on_scanner_error == "continue" → log, skip, proceed
    ├── on_scanner_error == "warn"     → + stderr warning + SARIF note
    └── on_scanner_error == "fail"     → abort non-zero

advisory DB stale
    └── always → DUH-DB-STALE @ medium

resolver call
    ├── findings cache valid → apply policy
    ├── cache missing        → fail-open, log, NOTIFICATION
    └── timeout > 5s         → fail-open, log, NOTIFICATION
```

### 7.4 Unit test matrix

| Layer | Test class | Coverage |
|---|---|---|
| `config.py` | `TestPolicyLoad` | dual-config precedence, env expansion, validation errors, `"auto"` resolution, mode preset expansion |
| `engine.py` | `TestRunnerIsolation` | scanner crash isolation, timeout, not-installed, `on_scanner_error` modes, result aggregation, fingerprint dedup |
| `exceptions.py` | `TestExceptionStore` | add/list/renew/remove/audit, alias matching, scope matching, expiry, permanent flag, unused detection, per-user override merge |
| `policy.py` | `TestResolve` | fail-on/report-on partitioning, dangerous-tool gate, custom resolver entry point, empty findings, all-excepted, stale cache |
| `hooks.py` | `TestHookBindings` | four events fire at right times, `HookResponse` block, dep-change rescan, expiry notification, session summary, fail-open on timeout |
| `wizard.py` | `TestWizardFlow` | each question, detection matrix (docker/go/github), dry-run mode, atomic partial writes, Ctrl-C resumption |
| `finding.py` | `TestFindingSerialization` | SARIF round-trip, JSON round-trip, fingerprint stability, CWE mapping |
| `cli.py` | `TestCLI` | every subcommand, error messages, `--dry-run`, `--sarif` output |
| `scanners/ruff_sec.py` | `TestRuffSecScanner` | golden-file input, delta mode, missing ruff install |
| `scanners/pip_audit.py` | `TestPipAuditScanner` | mocked OSV response, cache hit, network failure, CVE fixture |
| `scanners/detect_secrets.py` | `TestDetectSecretsScanner` | baseline creation, delta mode, entropy suppression |
| `scanners/duh_repo.py` | `TestDuhRepoScanner` | repo-local `DUH_BASE_URL` rejected, auto-load refused, TOFU SHA, symlink refusal |
| `scanners/duh_mcp_schema.py` | `TestMCPSchemaScanner` | zero-width/bidi/tag Unicode rejection, imperative verbs, base64 detection, NFKC |
| `scanners/duh_mcp_pin.py` | `TestMCPPinScanner` | first-connect pin, reject on schema change, diff output |
| `scanners/duh_sandbox_lint.py` | `TestSandboxLint` | AST walk finds model-output → `.sb` flow, CVE-2025-59532 regression replay |
| `scanners/duh_oauth_lint.py` | `TestOAuthLint` | `0.0.0.0` flagged, port reuse flagged, 0600 enforced, HMAC state, symlink refusal |

### 7.5 Integration tests

`tests/integration/test_security_e2e.py`:

- `test_wizard_init_full_flow` — subprocess, stdin answers, verify every file created
- `test_scan_emits_sarif_loadable_by_github_codeql` — known CVE fixture, SARIF validation
- `test_delta_mode_only_reports_new` — plant A in base, A+B in head, assert only B
- `test_runtime_gates_block_bash_on_high` — fake high-sev finding, mock LLM requesting Bash, assert block
- `test_exception_with_alias_suppresses_both_ids` — except CVE-X, fire GHSA-Y aliased to CVE-X, assert suppressed
- `test_expired_exception_fires_finding_normally` — yesterday expiry, assert surfaces
- `test_pre_push_git_hook_catches_local_commit` — staged secret, run installed hook, assert rejection + disable hint
- `test_ci_template_passes_github_actions_lint` — generate `security.yml`, run `zizmor` + `actionlint`, assert clean

### 7.6 Property tests

- `resolve()` is idempotent for (policy, findings, exceptions)
- Any exception with `expires_at < now()` never suppresses its target
- `Finding.fingerprint` stable across scanner reruns on identical input
- `.duh/security.json` round-trips through pydantic for every valid config

### 7.7 Golden-file tests

- `tests/fixtures/security/safe/` — zero findings
- `tests/fixtures/security/vulnerable/` — 10 seeded issues, one per rule class, exact IDs asserted
- `tests/fixtures/security/cve_replays/CVE-2025-59536/` — repo-file RCE, `duh-repo` scanner catches
- `tests/fixtures/security/cve_replays/CVE-2025-59532/` — sandbox bypass, `duh-sandbox-lint` catches
- `tests/fixtures/security/cve_replays/CVE-2025-54136/` — MCPoison, `duh-mcp-pin` catches
- `tests/fixtures/security/cve_replays/CVE-2026-35022/` — command injection, `ruff-sec` catches

### 7.8 Chaos / mutation tests

- Inject `os._exit(1)` into one scanner mid-run; engine returns partial results with that scanner marked errored
- Zero-byte `osv.json` in cache dir; pip-audit falls back to network or returns `DUH-DB-STALE`
- Provider adapter differential: same tool_use JSON → 5 adapters produce equivalent `ToolUseBlock` (moved from ADR-054 as a regression test)

### 7.9 Coverage target

**100%** (matches current D.U.H. standard). Estimated +1,800 to +2,200 test LOC.

---

## 8. Rollout plan

| Week | Phase | Deliverables | Acceptance |
|---|---|---|---|
| **1** | Skeleton + 4 minimal scanners | `duh/security/` layout, `config.py`, `finding.py`, `engine.py`, `scanners/__init__.py`, `ruff_sec.py`, `pip_audit.py`, `detect_secrets.py`, `cyclonedx_sbom.py`, `cli.py` stub with `duh security scan` | Scan on D.U.H.'s repo emits valid SARIF with a known `DUH-DB-STALE` finding; 4 unit-test classes pass; no regressions in existing 3777 tests |
| **2** | 5 custom D.U.H. scanners | `duh_repo.py`, `duh_mcp_schema.py`, `duh_mcp_pin.py`, `duh_sandbox_lint.py`, `duh_oauth_lint.py`, CVE replay fixtures, `ExceptionStore`, alias expansion | CVE-2025-59536, -59532, -54136 replay fixtures each detected; exception add/list/remove/renew/audit works |
| **3** | Wizard + dual config | `wizard.py`, `cli.py` complete, pyproject.toml escape hatch, detection matrix, dry-run, partial-write safety, legacy config import | `duh security init` walks flow on empty project; all generated files valid; Ctrl-C leaves consistent state; dry-run byte-identical to real run |
| **4** | Runtime hooks + delta mode | `hooks.py` bindings, `policy.resolve()` wired through `HookResponse`, Layer-1 delta (`--baseline`), scanner-native delta, changed-files fast path, pre-push git hook with disable hint | PR runs `duh security scan --baseline origin/main`, blocks on seeded high finding, passes when fixed. Runtime `PRE_TOOL_USE` blocks `Bash` on active high finding, unblocks after `exception add` |
| **5** | CI templates + SECURITY.md + dogfood | Generate `.github/workflows/security.yml` (three variants), `dependabot.yml`, updated `publish.yml` with Trusted Publishing + PEP 740, `SECURITY.md`. D.U.H.'s CI switches to generated workflow | D.U.H.'s own CI runs generated workflow and stays green. Reusable workflow available. `duh security doctor` reports clean. Coverage stays 100%. |

**Exit criteria:** D.U.H. v0.4.0 ships on PyPI with `duh-cli[security]` extra, new security workflow on main, SECURITY.md published, SBOM attested via PEP 740, nine Minimal-tier scanners completing in under 10 seconds.

---

## 9. Out of scope (deferred to ADR-054)

1. Taint-propagating `UntrustedStr` through context builder
2. Confirmation-token gating
3. Lethal trifecta capability matrix
4. Signed plugin / hook manifests
5. Per-hook filesystem namespacing
6. `sys.addaudithook` telemetry bridge (PEP 578)
7. Provider adapter differential fuzzer (property-level)
8. MCP stdio server subprocess sandboxing
