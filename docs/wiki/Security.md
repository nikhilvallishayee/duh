# Security Guide

D.U.H. implements defense in depth across every layer of the agent-tool pipeline.
This guide covers each security subsystem, how it works, and how to configure it.

---

## 1. Security Philosophy

Three principles govern every security decision in D.U.H.:

**Defense in depth.** No single mechanism is trusted to stop an attack. Taint tracking, confirmation gates, permission checks, sandbox confinement, and audit logging all operate independently. If one layer fails, the next one catches it.

**Assume breach.** The model is treated as an untrusted actor. Its output is taint-tagged from the moment it arrives. Dangerous tool calls originating from model output require a cryptographic confirmation token before execution. The system never assumes the model is acting in good faith.

**Verify everything.** Tool schemas are validated at registration time. Bash commands are parsed with an AST tokenizer before execution. MCP server descriptions are scanned for invisible Unicode at handshake time. Environment variables are checked against a hijack blocklist. Nothing passes through unchecked.

---

## 2. Trifecta Check

### What It Is

The "lethal trifecta" is the combination of three capabilities in a single session:

| Capability | Examples |
|---|---|
| `READ_PRIVATE` | Read, MemoryRecall, Grep on cwd, Database, LSP |
| `READ_UNTRUSTED` | WebFetch, WebSearch, MCP tool output |
| `NETWORK_EGRESS` | WebFetch, unsandboxed Bash, HTTP, Docker |

When all three are active simultaneously, data read from private sources can be exfiltrated through untrusted content via network egress. This is Simon Willison's classic exfiltration vector.

### Why It Is Dangerous

A model that can read your private files, process untrusted web content, and make outbound network requests has the complete attack surface for data exfiltration. Even without malicious intent, prompt injection in untrusted content could instruct the model to leak private data over the network.

### How It Works

At session startup, `compute_session_capabilities()` unions the `Capability` flags from every registered tool. If the result includes all three trifecta flags, `check_trifecta()` raises `LethalTrifectaError` and the session refuses to start.

### How to Acknowledge

If you understand the risk and need all three capabilities, you have three options:

```bash
# CLI flag
duh --i-understand-the-lethal-trifecta

# Config file (.duh/security.json)
{ "trifecta_acknowledged": true }

# Or: disable one of the three capabilities
# (e.g., remove WebFetch to drop NETWORK_EGRESS)
```

**Source:** `duh/security/trifecta.py`

---

## 3. Taint Tracking

### UntrustedStr

`UntrustedStr` is a `str` subclass that carries a `TaintSource` tag through every string operation. When the model emits text, it is wrapped as:

```python
model_out = UntrustedStr(provider_stream, TaintSource.MODEL_OUTPUT)
```

Every `str` method that returns a new string (`upper()`, `split()`, `format()`, `+`, `%`, `join()`, `replace()`, slicing, etc.) is overridden to propagate the taint tag. When two strings with different sources are combined, the tainted source wins:

```python
safe = "prefix: "                                  # plain str (SYSTEM)
tainted = UntrustedStr("payload", TaintSource.MODEL_OUTPUT)
result = safe + tainted                            # UntrustedStr, MODEL_OUTPUT
```

### TaintSource Enum

| Source | Tainted? | Origin |
|---|---|---|
| `USER_INPUT` | No | REPL input, `/continue`, AskUserQuestion |
| `SYSTEM` | No | D.U.H. prompts, config, skill definitions |
| `MODEL_OUTPUT` | Yes | LLM provider response stream |
| `TOOL_OUTPUT` | Yes | Native tool execution results |
| `FILE_CONTENT` | Yes | File reads |
| `MCP_OUTPUT` | Yes | MCP server tool results |
| `NETWORK` | Yes | Web fetch, HTTP responses |

Only `USER_INPUT` and `SYSTEM` are untainted. Everything else is treated as potentially adversarial.

### Debug and Strict Modes

```bash
# Print every taint-preserving str operation to stderr
DUH_TAINT_DEBUG=1 duh ...

# Raise TaintLossError if any operation silently drops taint
DUH_TAINT_STRICT=1 duh ...
```

**Source:** `duh/kernel/untrusted.py`

---

## 4. Confirmation Gates

### Dangerous Tool Detection

The policy resolver maintains a hardcoded set of dangerous tools:

```
Bash, Write, Edit, MultiEdit, NotebookEdit, WebFetch, Docker, HTTP
```

When a dangerous tool is called and the event chain contains any tainted source (model output, tool output, MCP output, etc.), the call is blocked unless a valid confirmation token is provided.

### Confirmation Tokens

Tokens are minted by `ConfirmationMinter` and are:

- **Session-bound:** The token includes the `session_id` in its HMAC payload.
- **Tool-bound:** The token includes the tool name.
- **Input-bound:** The token includes a SHA-256 hash of the tool input dict.
- **Time-limited:** Tokens expire after 5 minutes (300 seconds).
- **Single-use:** Once validated, the token is added to an `_issued` set and cannot be reused.

### HMAC Minting

The token format is `duh-confirm-{timestamp}-{sig}` where `sig` is the first 16 hex characters of `HMAC-SHA256(session_key, "{session_id}|{tool}|{input_hash}|{timestamp}")`.

Only user-origin events (REPL input, `/continue`) can trigger token minting. The model cannot mint its own confirmation tokens.

### Resolution Flow

```
Tool call arrives
  -> Is tool in DANGEROUS_TOOLS?
     No  -> allow
     Yes -> Is any ancestor in the event chain tainted?
            No  -> allow (untainted context)
            Yes -> Is there a valid confirmation token?
                   Yes -> allow (consume token)
                   No  -> block ("Confirm interactively or add a user-origin /continue")
```

**Source:** `duh/kernel/confirmation.py`, `duh/security/policy.py`

---

## 5. Permission System

### Approval Modes

D.U.H. implements a three-tier approval model via `TieredApprover`:

| Mode | Read Tools | Write Tools | Command Tools |
|---|---|---|---|
| `suggest` (default) | Auto-approved | Needs approval | Needs approval |
| `auto-edit` | Auto-approved | Auto-approved | Needs approval |
| `full-auto` | Auto-approved | Auto-approved | Auto-approved |

Tool categories:

- **Read:** Read, Glob, Grep, ToolSearch, WebSearch, MemoryRecall, Skill
- **Write:** Write, Edit, MultiEdit, NotebookEdit, worktree tools, MemoryStore
- **Command:** Bash, WebFetch, Task, HTTP, Database, Docker, GitHub

In `auto-edit` and `full-auto` modes, D.U.H. warns at startup if the working directory is not inside a git repository (no way to revert bad edits).

### Git Safety Check

Regardless of approval mode -- including `full-auto` -- destructive git commands are always blocked:

- `git push --force` / `git push -f`
- `git reset --hard`
- `git clean -f` (any flag combination containing `f`)
- `git branch -D`

These require explicit confirmation because they are irreversible even with git.

### Per-Session Cache

The `SessionPermissionCache` remembers tool approval decisions within a session:

| Response | Meaning |
|---|---|
| `y` | Yes, this time only (not cached) |
| `a` | Always allow this tool for this session |
| `n` | No, this time only (not cached) |
| `N` | Never allow this tool for this session |

The cache is in-memory only; it is never persisted to disk. Each new session starts clean.

### --dangerously-skip-permissions

```bash
duh --dangerously-skip-permissions -p "deploy to staging"
```

This flag switches to `AutoApprover`, which approves all tool calls without prompting. It is intended for sandboxed environments and CI pipelines. It does **not** bypass the git safety check -- destructive git commands are still blocked.

**Source:** `duh/adapters/approvers.py`, `duh/kernel/permission_cache.py`

---

## 6. Secrets Redaction

### redact_secrets()

The `redact_secrets()` function strips sensitive values from text before it reaches the model or gets logged. It applies pattern matching in priority order:

| Pattern | Example |
|---|---|
| PEM private keys | `-----BEGIN RSA PRIVATE KEY-----...` |
| Anthropic API keys | `sk-ant-api03-...` |
| OpenAI API keys | `sk-proj-...`, `sk-...` (20+ chars) |
| AWS access keys | `AKIA...` (16 uppercase chars) |
| GitHub tokens | `ghp_...`, `gho_...`, `ghs_...`, `ghr_...` |
| Bearer tokens | `Bearer eyJ...` |
| Passwords in URLs | `postgres://user:password@host` |
| Generic assignments | `SECRET_KEY="value"`, `api_key=value`, `token: "value"` |

The generic assignment pattern is gated behind a keyword screen: the full regex is only applied if the text contains one of `secret`, `api_key`, `apikey`, `api-key`, `token`, `password`, `passwd`, `credential`, or `auth`. This prevents catastrophic backtracking on large inputs that contain none of these substrings.

### Opt-In

Redaction is not applied globally by default. Tool implementations opt in by passing `redact=True` or by calling `redact_secrets()` on their output before returning it. The audit logger also applies its own field-level redaction (see section 8).

**Source:** `duh/kernel/redact.py`

---

## 7. Bash Security

### AST Parsing

Before executing a shell command, D.U.H. tokenizes it into structural segments using `bash_ast.py`. The tokenizer handles:

- Pipe chains (`|`)
- Logical operators (`&&`, `||`)
- Semicolons (`;`)
- Subshells (`$(...)` and backticks)
- Process substitutions (`<(...)`, `>(...)`)
- Heredocs (`<<EOF ... EOF`)
- ANSI-C quoting (`$'...'`)
- Quoted strings (single and double)

Each segment is classified independently, which catches dangerous commands hidden after a pipe (e.g., `curl ... | bash`). A full-command regex scan also runs to catch cross-segment patterns. The subcommand fanout is capped at 50 segments to prevent DoS.

Safe wrapper commands (`timeout`, `time`, `nice`, `nohup`, `env`, `stdbuf`) are stripped before classification so the inner command is what gets evaluated.

### Risk Classification

Commands are classified into three levels:

| Level | Action | Examples |
|---|---|---|
| `dangerous` | Blocked | `rm -rf /`, fork bombs, `curl \| bash`, `sudo`, `LD_PRELOAD=`, `dd if=/dev/zero`, pipe-to-shell, `/dev/tcp/` |
| `moderate` | Warning | `chmod`, `rm -flag`, `kill -9`, `sed -i`, `docker rm`, `git push --force` |
| `safe` | Allowed | Everything else |

The pattern set covers both Unix (bash/sh) and Windows (PowerShell) commands. PowerShell patterns include `Remove-Item -Recurse -Force`, `Invoke-Expression`, `Set-ExecutionPolicy Unrestricted`, `Format-Volume`, and more.

### Env Var Allowlist and Binary Hijack Detection

A 90+ entry allowlist (`SAFE_ENV_VARS`) covers common environment variables for shells, build tools, Go, Rust, Node.js, Python, Java, Ruby, Docker, CI, Git, and proxies.

Binary hijack patterns are detected via regex:

```
LD_PRELOAD, DYLD_INSERT_LIBRARIES, DYLD_LIBRARY_PATH,
LD_LIBRARY_PATH (with path traversal), and any LD_* / DYLD_* prefix
```

These are blocked at classification time because they allow an attacker to override which shared libraries are loaded -- a critical privilege escalation vector.

**Source:** `duh/tools/bash_security.py`, `duh/tools/bash_ast.py`

---

## 8. Audit Logging

### Structured JSONL

Every tool invocation is recorded as a single JSON line in `~/.config/duh/audit.jsonl`. Each entry contains:

```json
{
  "ts": "2026-04-15T10:30:00+0000",
  "sid": "session-abc123",
  "tool": "Bash",
  "input": {"command": "ls -la"},
  "status": "ok",
  "ms": 42
}
```

The `status` field is one of `ok`, `error`, or `denied`.

### Field Redaction

Before writing, the audit logger automatically:

1. **Redacts sensitive keys:** Any input field whose name contains `key`, `token`, `secret`, `password`, `credential`, or `auth` has its value replaced with `[REDACTED]`.
2. **Truncates large values:** String values longer than 500 characters are truncated to 100 characters with `...[truncated]`.

### /audit Command

Inside the REPL, use `/audit` to view recent entries:

```
> /audit         # show last 20 entries
> /audit 50      # show last 50 entries
```

Output format:

```
  Last 5 audit entries:
    2026-04-15T10:30:00+0000  Bash                  ok       42ms
    2026-04-15T10:30:01+0000  Read                  ok       3ms
    2026-04-15T10:30:02+0000  Edit                  ok       15ms
```

### duh audit CLI

From the command line:

```bash
duh audit              # last 20 entries, human-readable
duh audit -n 50        # last 50 entries
duh audit --json       # raw JSONL output (for piping to jq, SIEM, etc.)
```

### PEP 578 Audit Hook

D.U.H. also installs a Python PEP 578 audit hook that observes low-level runtime events: `open`, `socket.connect`, `subprocess.Popen`, `os.exec`, `ctypes.dlopen`, `compile`, `exec`, `pickle.find_class`, and more. These events are forwarded to the hook bus for SIEM integration. The audit hook is telemetry only -- it observes but does not block. Enforcement is handled by OS-level sandboxing.

**Source:** `duh/security/audit.py`, `duh/kernel/audit.py`

---

## 9. Tool Schema Validation

At tool registration time, `validate_tool_schema()` checks every tool's `input_schema` for structural correctness:

| Check | Severity |
|---|---|
| Root `type` must be `"object"` | Error (raises `SchemaValidationError`) |
| `properties` must be a dict | Error |
| `input_schema` must be a dict | Error |
| Property `type` must be a valid JSON Schema type | Warning |
| `required` entries must reference existing properties | Warning |
| Missing `description` on properties | Warning |

Valid JSON Schema types: `string`, `number`, `integer`, `boolean`, `array`, `object`, `null`. Union types (e.g., `["string", "null"]`) are also validated element by element.

Critical errors raise `SchemaValidationError` immediately. Warnings are returned as a list of human-readable strings and logged but do not block registration.

This catches malformed MCP tool schemas at discovery time, before they cause cryptic API errors downstream during tool calls.

**Source:** `duh/kernel/schema_validator.py`

---

## 10. Sandbox Policies

### Architecture

D.U.H. uses a two-layer sandbox architecture:

1. **`SandboxPolicy`** -- a platform-independent dataclass describing what is allowed (writable paths, readable paths, network access).
2. **Platform adapters** -- translate the policy into OS-native enforcement:
   - **macOS:** Seatbelt (`sandbox-exec`)
   - **Linux:** Landlock (kernel 5.13+)

### Seatbelt (macOS)

Generates an Apple Sandbox Profile Language (`.sb`) file following a default-deny model:

1. Deny everything by default: `(deny default)`
2. Allow global file reads (required for bash, shared libraries)
3. Allow file writes **only** to: specified paths + `/tmp` + `/private/tmp` + `/private/var/tmp` + `/dev/null` + `/dev/tty` + `~/.duh`
4. Allow process execution (`process-exec`, `process-fork`) but **not** the `process*` wildcard (which would grant `process-info`, `process-codesign`, etc.)
5. Allow or deny network based on policy
6. Allow basic system operations: `signal`, `sysctl-read`, `mach-lookup`, `ipc-posix-shm-read*`

Profile injection is prevented by escaping quotes and backslashes in path strings. The profile is written to a temp file and the command is wrapped as:

```
sandbox-exec -f /tmp/duh_xxx.sb bash -c "original command"
```

### Landlock (Linux)

Generates a Python wrapper script that uses `ctypes` to call the Landlock syscalls (`landlock_create_ruleset`, `landlock_add_rule`, `landlock_restrict_self`) before `exec`-ing the target command. This is necessary because Landlock restricts the *calling* process, not a child.

Access masks:

| Permission | Flags |
|---|---|
| Read | `READ_FILE`, `READ_DIR` |
| Write | `WRITE_FILE`, `REMOVE_DIR`, `REMOVE_FILE`, `MAKE_DIR`, `MAKE_REG`, `MAKE_SYM` |
| Execute | `EXECUTE` |

Always-writable paths: `/tmp`, `/var/tmp`, `~/.duh`. Execute paths: `/usr`, `/bin`, `/sbin`, `/nix`, `/opt`.

If Landlock is unavailable (kernel < 5.13), the wrapper exits with code 198 (`SANDBOX_UNAVAILABLE`). This is **fail-closed** -- the command is not run unsandboxed.

### Network Policy

Three modes control network access:

| Mode | OS-Level | App-Level |
|---|---|---|
| `FULL` | Network allowed | All methods allowed |
| `LIMITED` | Network allowed | Only `GET`, `HEAD`, `OPTIONS` |
| `NONE` | Network denied (Seatbelt `deny network*`) | All blocked |

`LIMITED` mode is enforced at the application layer (in the WebFetch tool). Host-level filtering supports `allowed_hosts` and `denied_hosts` lists with subdomain matching.

### Auto-Detection

`detect_sandbox_type()` checks the current platform:

- macOS: looks for `sandbox-exec` in `$PATH`
- Linux: probes the `landlock_create_ruleset` syscall via `ctypes`
- Other platforms: returns `NONE`

**Source:** `duh/adapters/sandbox/policy.py`, `duh/adapters/sandbox/seatbelt.py`, `duh/adapters/sandbox/landlock.py`, `duh/adapters/sandbox/network.py`

---

## 11. MCP Security

### Subprocess Sandboxing

When connecting to an MCP server over stdio transport, D.U.H. wraps the server process in the platform-native sandbox. The `MCPManifest` declares the server's required capabilities (writable paths, readable paths, network access), which are translated into a `SandboxPolicy` and applied via Seatbelt or Landlock.

If no sandbox is available on the current platform, the server runs unsandboxed (with a logged warning).

### Session Management

MCP connections implement circuit-breaker logic:

- **Session expiry detection:** If a tool call returns HTTP 404 with "session not found", the executor disconnects and reconnects once before retrying.
- **Consecutive error tracking:** After 3 consecutive errors from a server (`MAX_ERRORS_BEFORE_RECONNECT`), the server is marked as **degraded**. All its tools are removed from the active schema and further calls are refused immediately.
- **Phased connection:** Local (stdio) servers connect with concurrency limited to 3. Remote (SSE/HTTP/WebSocket) servers connect with concurrency limited to 20.

### Unicode Normalization

At handshake time, every tool description and parameter description from an MCP server is scanned for hostile Unicode using `normalize_mcp_description()`. The scan detects:

| Category | Unicode Range | Attack Vector |
|---|---|---|
| Zero-width characters | U+200B, U+200C, U+200D, U+FEFF | Invisible spacing that conceals instructions from human review |
| Bidi override characters | Unicode category `Cf` | Reverses display order to hide malicious content (RLO attacks) |
| Tag Characters | U+E0000..U+E007F | Completely invisible; used to smuggle instructions through embedding |
| Variation Selectors | U+FE00-U+FE0F, U+E0100-U+E01EF | Alter glyph appearance while leaving base character unchanged |

NFKC normalization is applied to collapse compatibility equivalents (ligatures, full-width forms). If any suspicious characters are found, `MCPUnicodeError` is raised and the connection is refused.

### Taint Tagging

All MCP tool output is tagged as `TaintSource.MCP_OUTPUT` via `_wrap_mcp_output()`. This means any downstream tool call that uses MCP output in a tainted context will trigger the confirmation gate for dangerous tools.

### Schema Validation

MCP tool schemas are validated at discovery time using the same `validate_tool_schema()` function used for native tools. Critical schema errors are logged. This prevents malformed schemas from causing cryptic API failures during tool execution.

### MCP Output Tainting

Output returned by MCP tools in `MCPExecutor.run()` is wrapped via `_wrap_mcp_output()` so it carries `TaintSource.MCP_OUTPUT` through the taint-tracking system. Downstream tools that see the output can then enforce confirmation gates or deny-listed capabilities on the basis of origin.

**Source:** `duh/adapters/mcp_executor.py`, `duh/adapters/mcp_unicode.py`

---

## 12. Filesystem Boundary (PathPolicy)

### What It Is

`PathPolicy` enforces that file tool operations stay within the project root. Each of `Read`, `Write`, `Edit`, `MultiEdit` consults its configured `PathPolicy` before any I/O.

### How It Works

1. The CLI runners (`runner.py`, `repl.py`, `sdk_runner.py`) construct a `PathPolicy` with `project_root = _find_git_root(cwd) or cwd`.
2. `SessionBuilder` propagates the policy to tools via `get_all_tools(path_policy=...)`.
3. Tools call `path = path.resolve()` **before** boundary check — this closes the symlink-traversal attack (CWE-59, CWE-22). A symlink at `project/innocent.txt -> /etc/shadow` is resolved to `/etc/shadow` and rejected.
4. `check_permissions()` also consults the policy, so the approval layer sees the boundary decision.

### Allowed Paths

By default `PathPolicy` admits `/tmp` in addition to the project root. Pass `allowed_paths=[]` for strict mode (tests use this).

**Source:** `duh/security/path_policy.py`, `duh/tools/read.py`, `duh/tools/write.py`, `duh/tools/edit.py`, `duh/tools/multi_edit.py`

---

## 13. WebFetch SSRF Protection

`WebFetchTool` resolves the target hostname before issuing HTTP and rejects:

- Loopback (`127.0.0.0/8`, `::1`)
- Private ranges (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
- Link-local (`169.254.0.0/16`, `fe80::/10`) — includes AWS/GCP metadata at `169.254.169.254`
- Reserved/multicast/unspecified (`0.0.0.0`, `224.0.0.0/4`)
- Cloud metadata hostnames (`metadata.google.internal`, `metadata.gcp.internal`)

DNS resolution failures are treated as blocks (fail-closed). If any resolved IP in a multi-home DNS response is private, the fetch is rejected — DNS rebinding protection.

**Source:** `duh/tools/web_fetch.py` (`_is_private_ip`, `_validate_url_ssrf`)
