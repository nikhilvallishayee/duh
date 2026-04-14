# D.U.H. LLM-Specific Security Hardening — Full Design

**ADR:** [ADR-054](../../adrs/ADR-054-llm-specific-security-hardening.md)
**Prerequisite:** [ADR-053](../../adrs/ADR-053-continuous-vulnerability-monitoring.md)
**Date:** 2026-04-14
**Status:** Proposed

## 0. Summary

ADR-053 ships a pluggable vulnerability monitoring module. That is a safety net. This spec is the defense.

Every published RCE against an LLM coding agent in 2024–2026 — Claude Code CVE-2025-59536, Codex CVE-2025-59532, Cursor CurXecute + MCPoison, EchoLeak, postmark-mcp, IDEsaster, CVE-2026-35022 — reduces to one root cause: **the agent treats model output, file content, tool output, and MCP metadata as trusted capability material when it should be treated as untrusted data.** Every researcher in the space (DeepMind/CaMeL, DataFilter, Simon Willison's "lethal trifecta", MITRE ATLAS v5.4, OWASP LLM Top 10 2025, OWASP Top 10 for Agentic Applications) converges on the same fix: **taint propagation**.

This spec turns that fix into eight workstreams. Each is independently shippable and individually valuable. Sequencing matters because later items depend on earlier ones. Total estimated effort: 8–10 weeks, probably split across two release cycles.

---

## 1. Architecture overview

Eight workstreams, grouped by dependency graph:

```
  ┌─────────────────────────────────────────────────────────────┐
  │   7.1 UntrustedStr + context builder tagging (keystone)     │
  └─────────┬───────────────────────────────────┬───────────────┘
            │                                   │
            ▼                                   ▼
  ┌──────────────────┐              ┌──────────────────────────┐
  │ 7.2 Confirmation │              │ 7.3 Lethal trifecta      │
  │     tokens       │              │     capability matrix    │
  └──────────────────┘              └──────────────────────────┘
            │                                   │
            │                                   ▼
            │                       ┌──────────────────────────┐
            │                       │ 7.6 MCP Unicode +        │
            │                       │     subprocess sandbox   │
            │                       └──────────────────────────┘
            │
  Independent (no taint dependency):
  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
  │ 7.4 Per-hook FS  │  │ 7.5 sys.addaudit │  │ 7.8 Provider     │
  │     namespacing  │  │     hook bridge  │  │     diff fuzzer  │
  └──────────────────┘  └──────────────────┘  └──────────────────┘

  ┌──────────────────────────────────────────────────────────────┐
  │ 7.7 Signed hook manifests + TOFU store (can ship anytime)   │
  └──────────────────────────────────────────────────────────────┘
```

---

## 2. Workstream 7.1 — `UntrustedStr` + context builder tagging

### 2.1 Design

Subclass `str` to carry a `source` tag. Propagate the tag through every string operation. Stamp incoming strings at every origin point. Gate dangerous tool calls on untainted-origin or confirmation token.

```python
# duh/kernel/untrusted.py

from enum import Enum
from typing import Literal

class TaintSource(str, Enum):
    USER_INPUT   = "user_input"     # untainted — REPL prompt, /continue, AskUserQuestion response
    MODEL_OUTPUT = "model_output"   # tainted
    TOOL_OUTPUT  = "tool_output"    # tainted
    FILE_CONTENT = "file_content"   # tainted
    MCP_OUTPUT   = "mcp_output"     # tainted
    NETWORK      = "network"        # tainted
    SYSTEM       = "system"         # untainted — D.U.H.'s own prompts, config, skills

UNTAINTED_SOURCES: frozenset[TaintSource] = frozenset({TaintSource.USER_INPUT, TaintSource.SYSTEM})


class UntrustedStr(str):
    """str subclass that carries a TaintSource. Propagates through
    concat, format, slice, join, replace, strip, split, %-format.

    Any str method that returns a str → returns UntrustedStr with the
    same source. Any str method that returns a non-str (len, count,
    startswith, find, index, hash, bool, iter) → returns normally.

    Mixing two UntrustedStr with different sources → the more-tainted
    source wins (tainted > untainted).
    """
    __slots__ = ("_source",)

    _source: TaintSource

    def __new__(cls, value: str | bytes, source: TaintSource = TaintSource.MODEL_OUTPUT):
        instance = super().__new__(cls, value)
        instance._source = source
        return instance

    @property
    def source(self) -> TaintSource:
        return self._source

    def is_tainted(self) -> bool:
        return self._source not in UNTAINTED_SOURCES

    # Method overrides (returning UntrustedStr preserving source):
    def __add__(self, other): ...
    def __radd__(self, other): ...
    def __mod__(self, other): ...       # %-format
    def __mul__(self, n): ...
    def format(self, *args, **kwargs): ...
    def format_map(self, mapping): ...
    def join(self, iterable): ...
    def replace(self, old, new, count=-1): ...
    def strip(self, chars=None): ...
    def lstrip(self, chars=None): ...
    def rstrip(self, chars=None): ...
    def split(self, sep=None, maxsplit=-1): ...
    def rsplit(self, sep=None, maxsplit=-1): ...
    def splitlines(self, keepends=False): ...
    def lower(self): ...
    def upper(self): ...
    def title(self): ...
    def casefold(self): ...
    def capitalize(self): ...
    def swapcase(self): ...
    def expandtabs(self, tabsize=8): ...
    def center(self, width, fillchar=" "): ...
    def ljust(self, width, fillchar=" "): ...
    def rjust(self, width, fillchar=" "): ...
    def zfill(self, width): ...
    def translate(self, table): ...
    def encode(self, encoding="utf-8", errors="strict"): ...
    def removeprefix(self, prefix): ...
    def removesuffix(self, suffix): ...
    def __getitem__(self, key): ...     # slicing

    # Methods returning non-str pass through:
    # len, count, startswith, endswith, find, rfind, index, rindex,
    # isdigit, isalpha, isspace, istitle, isupper, islower, isnumeric,
    # isdecimal, isalnum, isidentifier, isprintable, isascii,
    # __len__, __hash__, __bool__, __contains__, __iter__, __repr__, __str__


def merge_source(a: str, b: str) -> TaintSource:
    """Combine two source tags; tainted wins over untainted."""
    a_src = getattr(a, "_source", TaintSource.SYSTEM)
    b_src = getattr(b, "_source", TaintSource.SYSTEM)
    if a_src in UNTAINTED_SOURCES and b_src in UNTAINTED_SOURCES:
        return a_src  # system beats user_input arbitrarily
    if a_src in UNTAINTED_SOURCES:
        return b_src
    if b_src in UNTAINTED_SOURCES:
        return a_src
    return a_src  # both tainted; first wins
```

### 2.2 Origin points

Every place a string enters D.U.H.'s context:

| Origin | Source tag | File |
|---|---|---|
| REPL prompt input | `USER_INPUT` | `duh/cli/repl.py` |
| `-p/--prompt` flag | `USER_INPUT` | `duh/cli/runner.py` |
| stream-json `user` message | `USER_INPUT` | `duh/cli/sdk_runner.py` |
| `AskUserQuestion` response | `USER_INPUT` | `duh/tools/ask_user_tool.py` |
| Model streaming text | `MODEL_OUTPUT` | `duh/adapters/*.py` — every provider |
| Tool result output | `TOOL_OUTPUT` | `duh/adapters/native_executor.py`, `mcp_executor.py` |
| File reads via `Read` tool | `FILE_CONTENT` | `duh/tools/read.py` |
| File reads via `Grep`/`Glob` | `FILE_CONTENT` | `duh/tools/grep.py`, `duh/tools/glob_tool.py` |
| `WebFetch` body | `NETWORK` | `duh/tools/web_fetch.py` |
| MCP `call_tool` response | `MCP_OUTPUT` | `duh/adapters/mcp_executor.py` |
| System prompt, D.U.H. skills, config | `SYSTEM` | `duh/cli/runner.py`, `duh/kernel/skill.py` |

### 2.3 Files touched

```
duh/kernel/untrusted.py          (new, ~400 LOC)
duh/kernel/context_builder.py    (tag at assembly)
duh/kernel/messages.py           (Message.text must preserve tags)
duh/adapters/simple_compactor.py (preserve tags through compaction)
duh/adapters/model_compactor.py  (preserve tags through summarization)
duh/adapters/anthropic.py        (tag outgoing model_output)
duh/adapters/openai.py           (tag outgoing model_output)
duh/adapters/openai_chatgpt.py   (tag outgoing model_output)
duh/adapters/ollama.py           (tag outgoing model_output)
duh/adapters/stub_provider.py    (tag outgoing model_output)
duh/adapters/native_executor.py  (tag tool outputs)
duh/adapters/mcp_executor.py     (tag MCP outputs)
duh/tools/read.py                (tag file content)
duh/tools/grep.py                (tag file content)
duh/tools/glob_tool.py           (tag filenames)
duh/tools/web_fetch.py           (tag network bodies)
duh/cli/repl.py                  (tag user_input)
duh/cli/runner.py                (tag system prompts + user_input)
duh/cli/sdk_runner.py            (tag user_input)
duh/kernel/redact.py             (preserve tags through redaction)
```

Approximately 15 existing files + 1 new module. The test matrix against CPython's `str` API surface is the hardest part.

### 2.4 Debugging aid

```bash
DUH_TAINT_DEBUG=1   # print tag at every string operation
DUH_TAINT_STRICT=1  # raise on tag loss (for CI / debugging)
```

`DUH_TAINT_STRICT` is vital during implementation — any str method that silently drops the tag throws loudly, so the test matrix catches the landmines.

### 2.5 Acceptance criteria

- `DUH_TAINT_STRICT=1 pytest tests/` passes — no tag loss anywhere
- Every new and modified adapter tags its outputs
- Test: dangerous-tool call originating from a `TaintSource.MODEL_OUTPUT` parent-chain is refused with a clear error explaining confirmation token need
- Test: same call originating from `TaintSource.USER_INPUT` passes
- Benchmark: string-heavy workload regresses no more than 5% (preserving taint is O(1) per method call; the overhead is method dispatch)

---

## 3. Workstream 7.2 — Confirmation token gating

### 3.1 Design

Dangerous tool invocations require a cryptographic token that only user-origin events can mint. The token is HMAC-bound to `(session_id, tool, input_hash)` and expires on the next model turn.

```python
# duh/kernel/confirmation.py

import hmac, hashlib, os, time, json
from typing import Any

class ConfirmationMinter:
    """Mints single-use tokens for dangerous tool calls.
    Only code with access to the session key may mint; tokens are
    validated by the policy resolver before dangerous tools run."""

    def __init__(self, session_key: bytes) -> None:
        self._key = session_key  # 32 bytes, generated at session start
        self._issued: set[str] = set()  # consumed tokens

    def mint(self, session_id: str, tool: str, input_obj: dict) -> str:
        input_hash = hashlib.sha256(
            json.dumps(input_obj, sort_keys=True).encode()
        ).hexdigest()
        ts = int(time.time())
        payload = f"{session_id}|{tool}|{input_hash}|{ts}"
        sig = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()[:16]
        token = f"duh-confirm-{ts}-{sig}"
        return token

    def validate(self, token: str, session_id: str, tool: str, input_obj: dict) -> bool:
        if token in self._issued:
            return False  # no replay
        try:
            _, ts_str, sig = token.rsplit("-", 2)
            ts = int(ts_str.replace("duh-confirm", "").lstrip("-"))
        except Exception:
            return False
        # accept tokens minted within last 5 minutes
        if time.time() - ts > 300:
            return False
        input_hash = hashlib.sha256(
            json.dumps(input_obj, sort_keys=True).encode()
        ).hexdigest()
        payload = f"{session_id}|{tool}|{input_hash}|{ts}"
        expected = hmac.new(self._key, payload.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return False
        self._issued.add(token)
        return True
```

### 3.2 When tokens are required

Dangerous tools (`Bash`, `Write`, `Edit`, `MultiEdit`, `NotebookEdit`, `WebFetch`, `Docker`, `HTTP`) require a token **only if** the event chain that led to the tool call contains tainted origins. The policy resolver inspects `event.chain` (the list of content items that contributed to the assistant's decision) and checks whether any are tainted.

```python
# duh/security/policy.py (extended from ADR-053)

def resolve(event, policy, findings, exceptions, *, minter: ConfirmationMinter | None = None):
    # ... ADR-053 finding-based logic ...

    if event.tool in _DANGEROUS_TOOLS and any_tainted(event.chain):
        token = event.input.get("_duh_confirm")
        if not token or not minter.validate(token, event.session_id, event.tool, event.input):
            return PolicyDecision(
                action="block",
                reason="dangerous tool called from tainted context without confirmation",
                findings=(),
                remediation=(
                    "Confirm this action interactively, or add a user-origin "
                    "/continue in the REPL. The token is minted only by user input."
                ),
            )
    return PolicyDecision(action="allow", ...)
```

### 3.3 Token minting sites

Only events with `TaintSource.USER_INPUT` mint tokens:

- REPL `input()` return at the prompt
- `/continue` slash command
- `AskUserQuestion` tool response (already user input)
- `duh security confirm <tool-id>` explicit CLI command
- Planned-step approval in plan mode

Everything else (model output, tool output, file content, MCP output, network) **cannot mint**. There is no fallback.

### 3.4 Scripted / SDK sessions

For non-interactive flows (CI, batch agents), the SDK runner accepts a `--pre-confirm` flag that mints tokens for a declared allowlist of tool+input pairs. The allowlist is a JSON file, auditable, and every token use is logged. This is the only way a scripted session can run dangerous tools against tainted context — and it forces the operator to declare exactly what's permitted.

### 3.5 Files touched

```
duh/kernel/confirmation.py        (new, ~200 LOC)
duh/kernel/engine.py              (session key generation at start)
duh/kernel/loop.py                (pass chain to policy resolver)
duh/kernel/tool.py                (ToolContext.confirm_token field)
duh/cli/repl.py                   (mint on /continue)
duh/cli/sdk_runner.py             (--pre-confirm allowlist loader)
duh/security/policy.py            (resolver gate)
duh/tools/ask_user_tool.py        (mint on answer)
```

### 3.6 Acceptance criteria

- Test: prompt injection in a file → model proposes `Bash rm -rf ~` → blocked with remediation message
- Test: same prompt with user typing `/continue` → mints token → Bash runs
- Test: scripted session with `--pre-confirm bash:rm.json` → runs exactly the allowlisted commands
- Test: token replay after consumption → blocked
- Test: token from a different session → blocked

---

## 4. Workstream 7.3 — Lethal trifecta capability matrix

### 4.1 Design

Refuse to start a session where all three of `READ_PRIVATE`, `READ_UNTRUSTED`, `NETWORK_EGRESS` capabilities are simultaneously enabled, unless the user explicitly acknowledges.

```python
# duh/security/trifecta.py

from enum import Flag, auto

class Capability(Flag):
    NONE            = 0
    READ_PRIVATE    = auto()  # Read, MemoryRecall, Grep on cwd, Database
    READ_UNTRUSTED  = auto()  # WebFetch, WebSearch, MCP_OUTPUT, MCP tools
    NETWORK_EGRESS  = auto()  # WebFetch, Bash (if unsandboxed), HTTP, Docker
    FS_WRITE        = auto()  # Write, Edit, MultiEdit, NotebookEdit
    EXEC            = auto()  # Bash, Docker, Skill, Agent, NotebookEdit kernel

LETHAL_TRIFECTA = Capability.READ_PRIVATE | Capability.READ_UNTRUSTED | Capability.NETWORK_EGRESS

def compute_session_capabilities(enabled_tools: list[Tool]) -> Capability:
    caps = Capability.NONE
    for tool in enabled_tools:
        caps |= tool.capabilities
    return caps

def check_trifecta(caps: Capability, policy: SecurityPolicy) -> None:
    if (caps & LETHAL_TRIFECTA) == LETHAL_TRIFECTA:
        if not policy.trifecta_acknowledged:
            raise LethalTrifectaError(
                "This session enables all three of READ_PRIVATE, READ_UNTRUSTED, "
                "NETWORK_EGRESS simultaneously. This combination is the classic "
                "exfiltration trifecta — data read from private sources can be "
                "smuggled out via untrusted content through network egress.\n\n"
                "To proceed, either:\n"
                "  - Disable one of: WebFetch / WebSearch / MCP untrusted servers\n"
                "  - Disable the source of READ_PRIVATE (don't run in a sensitive cwd)\n"
                "  - Acknowledge with: duh --i-understand-the-lethal-trifecta\n"
                "  - Or set trifecta_acknowledged: true in .duh/security.json"
            )
```

### 4.2 Tool capability declarations

Every tool declares its capabilities in a new class-level attribute:

```python
class BashTool:
    name = "Bash"
    capabilities = Capability.EXEC | Capability.NETWORK_EGRESS | Capability.FS_WRITE
    ...

class ReadTool:
    name = "Read"
    capabilities = Capability.READ_PRIVATE
    ...

class WebFetchTool:
    name = "WebFetch"
    capabilities = Capability.READ_UNTRUSTED | Capability.NETWORK_EGRESS
    ...

class MCPTool:
    # Set per-server — trusted MCP is READ_PRIVATE only, untrusted MCP is both.
    # Default: conservative, tainted.
    capabilities = Capability.READ_UNTRUSTED | Capability.NETWORK_EGRESS
    ...
```

### 4.3 Check site

The engine runs `check_trifecta()` at `SESSION_START`. Failures raise before the first model call, so sessions either start clean or refuse loudly.

### 4.4 Files touched

```
duh/security/trifecta.py    (new, ~150 LOC)
duh/kernel/tool.py          (Capability flag + Tool base class attribute)
duh/tools/*.py              (add capabilities to every tool — ~25 files)
duh/kernel/engine.py        (check at SESSION_START)
duh/cli/parser.py           (--i-understand-the-lethal-trifecta flag)
duh/config.py               (trifecta_acknowledged key)
```

### 4.5 Acceptance criteria

- Default D.U.H. session with full tool set refuses to start
- Disabling `WebFetch` + `WebSearch` allows start
- `--i-understand-the-lethal-trifecta` flag allows start with loud warning logged
- `trifecta_acknowledged: true` in `.duh/security.json` allows start silently (operator decision documented in config)

---

## 5. Workstream 7.4 — Per-hook filesystem namespacing

### 5.1 Design

Each registered hook gets a private temp directory created at registration and revoked after its event fires. Filesystem writes go through a helper that rewrites paths into the namespace.

```python
# duh/hooks.py (extension to ADR-013)

class HookContext:
    """Per-hook runtime context. Created fresh per event firing."""
    hook_name: str
    tmp_dir: Path       # Private per-hook tempdir
    allowed_read: frozenset[Path]   # Whitelist of readable paths
    allowed_write: frozenset[Path]  # Whitelist of writable paths (defaults to {tmp_dir})

    def open(self, path: str | Path, mode: str = "r"):
        resolved = Path(path).resolve()
        if "w" in mode or "a" in mode or "+" in mode:
            if resolved not in self.allowed_write and not any(
                resolved.is_relative_to(w) for w in self.allowed_write
            ):
                raise HookFSViolation(
                    f"hook '{self.hook_name}' wrote outside namespace: {resolved}"
                )
        else:
            if resolved not in self.allowed_read and not any(
                resolved.is_relative_to(r) for r in self.allowed_read
            ):
                raise HookFSViolation(
                    f"hook '{self.hook_name}' read outside namespace: {resolved}"
                )
        return open(resolved, mode)
```

Hooks receive `HookContext` as a parameter and use `ctx.open()` instead of `builtins.open()`. The tempdir is created at `register()` time, passed through each firing, and removed at hook unregister or process exit.

### 5.2 Migration

Existing hooks use `builtins.open()` directly — that keeps working. The namespace is opt-in via a `sandbox: true` field on `HookConfig`. Hooks declared with `sandbox: true` get the namespace enforcement; others keep current semantics.

This is a transition path. Phase 1: opt-in. Phase 2: default on with opt-out. Phase 3: hard requirement.

### 5.3 Files touched

```
duh/hooks.py                (HookContext, sandboxed open wrapper)
duh/plugins.py              (pass HookContext at event firing)
duh/security/hooks.py       (D.U.H.'s own security hooks adopt sandbox: true)
tests/unit/test_hook_sandbox.py  (new)
```

### 5.4 Acceptance criteria

- Hook with `sandbox: true` writing to `~/.ssh/id_rsa` → `HookFSViolation`
- Same hook writing to `ctx.tmp_dir / "log.txt"` → OK
- Same hook reading `/etc/passwd` → `HookFSViolation`
- Temp dir removed after `HookEvent.SESSION_END`
- Existing hooks without `sandbox: true` → unchanged

---

## 6. Workstream 7.5 — `sys.addaudithook` telemetry bridge (PEP 578)

### 6.1 Design

Install a Python audit hook at `Py_Initialize` time. Events flow into the D.U.H. hook bus as a new `AUDIT` event type. Explicitly telemetry, not enforcement.

```python
# duh/kernel/audit.py

import sys
from typing import Any
from duh.hooks import HookEvent, execute_hooks

WATCHED_EVENTS = frozenset({
    "open",
    "socket.connect", "socket.gethostbyname",
    "subprocess.Popen", "os.exec", "os.posix_spawn",
    "compile", "exec",
    "ctypes.dlopen", "ctypes.cdata",
    "import",
    "pickle.find_class", "marshal.loads",
    "urllib.Request",
    "ssl.wrap_socket",
})

_registry: Any = None  # set by install()

def install(registry) -> None:
    """Install the audit hook. Safe to call once per process."""
    global _registry
    _registry = registry
    sys.addaudithook(_audit_handler)

def _audit_handler(event: str, args: tuple) -> None:
    if event not in WATCHED_EVENTS:
        return
    if event == "import":
        name = args[0] if args else ""
        if name not in ("pickle", "marshal", "code", "dis", "compile"):
            return
    # Fire-and-forget; never raise from audit handler (would crash Python)
    try:
        if _registry:
            asyncio.run_coroutine_threadsafe(
                execute_hooks(_registry, HookEvent.AUDIT, {
                    "audit_event": event,
                    "args": _sanitize(args),
                }),
                asyncio.get_event_loop(),
            )
    except Exception:
        pass  # audit hooks must never raise
```

### 6.2 Fast path

Global audit hooks fire on every `open`, `import`, `exec`. Naive implementation halves process throughput. Fast path:

- Early return on `event not in WATCHED_EVENTS` — this is the critical optimization
- `WATCHED_EVENTS` is a `frozenset` (O(1) lookup)
- Benchmark target: <2% overhead on a normal D.U.H. session

### 6.3 Documentation

Docstring at the top of `audit.py` and in SECURITY.md:

> **This is telemetry, not enforcement.** PEP 578 audit hooks observe events but cannot prevent them. For enforcement, D.U.H. uses OS-level sandboxing (Seatbelt on macOS, Landlock on Linux). Audit events feed the D.U.H. hook bus so user-defined SIEM rules can match, alert, and log — but a malicious process can always bypass the audit hook via C extensions, `ctypes`, or forking.

### 6.4 Files touched

```
duh/kernel/audit.py                   (new, ~200 LOC)
duh/kernel/__main__.py                (install at startup)
duh/hooks.py                          (add HookEvent.AUDIT)
tests/unit/test_audit_hook.py         (new)
tests/benchmarks/test_audit_perf.py   (new — regression test)
```

### 6.5 Acceptance criteria

- `sys.addaudithook` is registered before first user code runs
- Opening `~/.ssh/id_rsa` fires `AUDIT` event with `audit_event="open"`
- `import pickle` fires `AUDIT` event
- Benchmark: D.U.H. startup + 100 tool calls regresses <2% vs baseline

---

## 7. Workstream 7.6 — MCP Unicode normalization + subprocess sandbox

### 7.1 Design

Two parts:

**Unicode normalization** — on MCP handshake, NFKC-normalize every tool description and parameter doc. Reject any description whose normalized form differs from original (GlassWorm attack class, zero-width / bidi / tag characters). This extends ADR-053's `duh-mcp-schema` scanner from passive linting to active rejection.

**Subprocess sandbox** — MCP stdio server subprocesses run under the same Seatbelt/Landlock profile as `Bash`. An MCP server that needs network egress declares it in its manifest; the loader composes that with D.U.H.'s policy and enforces the intersection. Servers without declared capabilities get the minimal profile.

### 7.2 Unicode normalization code

```python
# duh/adapters/mcp_unicode.py

import unicodedata
import regex as re  # need \p{Cf} support

# Characters we reject even after NFKC
_REJECT_CATEGORIES = frozenset({"Cf"})  # format characters incl. zero-width, bidi
_TAG_BLOCK = re.compile(r"[\U000E0000-\U000E007F]")  # Unicode Tag Characters
_VS = re.compile(r"[\uFE00-\uFE0F\U000E0100-\U000E01EF]")  # variation selectors

def normalize_mcp_description(text: str) -> tuple[str, list[str]]:
    """Return (normalized_text, list_of_reasons_to_reject).
    Empty reasons list means the description is safe."""
    issues: list[str] = []
    nfkc = unicodedata.normalize("NFKC", text)
    if nfkc != text:
        issues.append("NFKC normalization changed the text")

    for ch in text:
        if unicodedata.category(ch) in _REJECT_CATEGORIES:
            issues.append(f"format-class char: U+{ord(ch):04X}")

    if _TAG_BLOCK.search(text):
        issues.append("contains Unicode Tag Characters (U+E0000..U+E007F)")

    if _VS.search(text):
        issues.append("contains invisible variation selectors")

    return nfkc, issues
```

### 7.3 Subprocess sandbox integration

```python
# duh/adapters/mcp_executor.py (extended)

async def _start_stdio(self, params: StdioServerParameters) -> tuple:
    sandbox_policy = self._compute_mcp_sandbox_policy(params)
    if sandbox_policy is not None:
        # Wrap command via SandboxCommand
        from duh.adapters.sandbox.policy import SandboxCommand, detect_sandbox_type
        sandbox_cmd = SandboxCommand.build(
            command=params.command,
            policy=sandbox_policy,
            sandbox_type=detect_sandbox_type(),
        )
        params = StdioServerParameters(
            command=sandbox_cmd.argv[0],
            args=sandbox_cmd.argv[1:] + list(params.args),
            env=params.env,
        )
    ctx = stdio_client(params)
    read_stream, write_stream = await ctx.__aenter__()
    return ctx, read_stream, write_stream

def _compute_mcp_sandbox_policy(self, params) -> SandboxPolicy | None:
    """Returns SandboxPolicy to apply, or None to skip sandboxing."""
    declared = self._server_manifests.get(params.command, DEFAULT_MCP_MANIFEST)
    return SandboxPolicy(
        writable_paths=declared.writable_paths,
        readable_paths=declared.readable_paths,
        network_allowed=declared.network_allowed,
    )
```

### 7.4 Files touched

```
duh/adapters/mcp_unicode.py           (new, ~100 LOC)
duh/adapters/mcp_executor.py          (extend _start_stdio + handshake lint)
duh/adapters/mcp_manifest.py          (new, ~150 LOC — server manifest loader)
duh/adapters/sandbox/policy.py        (already exists)
tests/unit/test_mcp_unicode.py        (new)
tests/unit/test_mcp_subprocess_sandbox.py  (new)
```

### 7.5 Acceptance criteria

- MCP tool description with `Ignore\u200Bprevious` rejected with "format-class char U+200B"
- MCP tool description with Unicode Tag Characters rejected
- MCP stdio subprocess without declared network → sandbox denies network
- MCP stdio subprocess with declared `network_allowed=True` → sandbox permits network
- Unicode check never regresses legitimate multilingual descriptions (round-trip test against CJK, emoji, combining marks)

---

## 8. Workstream 7.7 — Signed hook manifests + TOFU store

### 8.1 Design

Every plugin ships a signed manifest declaring its capabilities. D.U.H. verifies the signature against a TOFU trust store on first load.

```json
{
  "plugin_name": "duh-coverage-reporter",
  "version": "1.2.3",
  "author": "alice@example.com",
  "capabilities": {
    "hook_events": ["POST_TOOL_USE", "SESSION_END"],
    "can_observe_tools": true,
    "fs_read_paths": ["./coverage"],
    "fs_write_paths": ["./.duh/coverage"],
    "network_egress": false
  },
  "signature": {
    "method": "sigstore",
    "bundle_b64": "..."
  }
}
```

### 8.2 Verification flow

```python
def load_plugin(spec: PluginSpec, trust_store: TrustStore) -> Plugin:
    manifest_path = spec.path / "manifest.json"
    if not manifest_path.exists():
        raise PluginError("no manifest.json")

    manifest = json.loads(manifest_path.read_text())
    result = trust_store.verify(manifest)

    if result.status == "trusted":
        pass  # known good
    elif result.status == "first_use":
        if not console.confirm_tofu(manifest):
            raise PluginError("user refused TOFU trust")
        trust_store.add(manifest)
    elif result.status == "revoked":
        raise PluginError(f"plugin signing key revoked: {result.reason}")
    elif result.status == "signature_mismatch":
        raise PluginError(
            f"plugin signature invalid — possible tampering. "
            f"Saved signature: {result.known}, new: {result.provided}"
        )
    else:
        raise PluginError(f"unknown verification status: {result.status}")

    return _instantiate_plugin(spec, manifest)
```

### 8.3 Files touched

```
duh/plugins/manifest.py           (new, ~300 LOC)
duh/plugins/trust_store.py        (new, ~200 LOC)
duh/plugins.py                    (call verify on load)
tests/unit/test_plugin_manifest.py (new)
tests/unit/test_plugin_trust.py    (new)
```

### 8.4 Acceptance criteria

- First load of a new plugin → TOFU prompt, user accepts, hash recorded
- Second load same hash → passes silently
- Second load with different signature → rejected
- Revoked key → rejected with revocation reason
- Plugin without manifest → rejected

---

## 9. Workstream 7.8 — Provider adapter differential fuzzer

### 9.1 Design

Property-based test using `hypothesis`: given the same tool_use JSON, all five provider adapters produce equivalent parsed `ToolUseBlock` objects. Catches schema confusion where an attacker crafts a tool call that looks benign to the router and malicious to the executor.

```python
# tests/property/test_provider_equivalence.py

from hypothesis import given, strategies as st
from duh.adapters.anthropic import AnthropicProvider
from duh.adapters.openai import OpenAIProvider
from duh.adapters.openai_chatgpt import OpenAIChatGPTProvider
from duh.adapters.ollama import OllamaProvider
from duh.adapters.stub_provider import StubProvider

tool_use_json = st.fixed_dictionaries({
    "type": st.just("tool_use"),
    "id": st.text(min_size=1, max_size=32),
    "name": st.sampled_from(["Bash", "Read", "Write", "WebFetch"]),
    "input": st.recursive(
        st.one_of(st.text(), st.integers(), st.booleans(), st.none()),
        lambda children: st.dictionaries(st.text(), children)
                      | st.lists(children, max_size=5),
        max_leaves=8,
    ),
})

@given(block=tool_use_json)
def test_all_adapters_agree_on_tool_use(block):
    """Every adapter parses the same tool_use dict into equivalent
    internal representation. Any divergence is a router/executor
    confusion attack surface."""
    parsed = []
    for adapter_cls in [AnthropicProvider, OpenAIProvider, OpenAIChatGPTProvider,
                        OllamaProvider, StubProvider]:
        parsed.append(adapter_cls._parse_tool_use_block(block))
    ref = parsed[0]
    for p in parsed[1:]:
        assert p.id == ref.id
        assert p.name == ref.name
        assert p.input == ref.input
```

### 9.2 Files touched

```
duh/adapters/*.py                         (expose _parse_tool_use_block classmethod)
tests/property/test_provider_equivalence.py  (new)
tests/property/__init__.py                    (new)
```

### 9.3 Acceptance criteria

- `hypothesis` generates 10,000 tool_use JSON samples per test run
- Test runs in CI nightly (not blocking PR — too slow)
- Single mismatch blocks release
- Known good provider subset baselined so new providers slot in

---

## 10. Rollout plan

8 phases, ~10 weeks total, deliverable in chunks:

| Phase | Weeks | Deliverable | Gates |
|---|---|---|---|
| **7.1** | 1–3 | `UntrustedStr` + context builder tagging | `DUH_TAINT_STRICT=1 pytest` passes; <5% perf regression |
| **7.2** | 3–4 | Confirmation tokens | Prompt injection test → blocked; `/continue` unblocks |
| **7.3** | 4 | Lethal trifecta matrix | Default session refused without override |
| **7.4** | 5 | Per-hook FS namespacing | `HookFSViolation` on cross-namespace write; existing hooks unaffected |
| **7.5** | 6 | `sys.addaudithook` bridge | `AUDIT` events fire; <2% perf regression |
| **7.6** | 7 | MCP Unicode + subprocess sandbox | GlassWorm-style descriptions rejected; MCP subprocess sandboxed |
| **7.7** | 8–9 | Signed hook manifests + TOFU | First-load prompt; revocation works |
| **7.8** | 9–10 | Provider differential fuzzer | 10K samples pass per run; nightly CI |

**Exit criteria for the whole ADR-054:** D.U.H. v0.5.0 ships with taint propagation default-on, confirmation tokens required for dangerous tools from tainted context, lethal trifecta refused by default, all 5 provider adapters property-equivalent under hypothesis, and documentation clearly distinguishing the telemetry (audit hooks) from enforcement (OS sandbox) layers.

---

## 11. Non-goals

- **Rego / OPA.** Explicitly rejected. Adds a sidecar and a DSL for decisions that are 90% dict lookups.
- **Custom Python interpreter.** We don't replace `str` with a patched builtin. `UntrustedStr` is a subclass that coexists with plain `str`.
- **MCP capability negotiation protocol.** We don't extend the MCP wire protocol. Server manifests are D.U.H.-side metadata.
- **Cross-process taint.** Taint doesn't propagate across subprocess boundaries. Once data leaves the D.U.H. process, sandbox and network policy take over.
- **Runtime model re-execution.** We don't re-run the model to "sanitize" tainted output. The CaMeL pattern in D.U.H. is pure taint propagation, not dual-LLM.
