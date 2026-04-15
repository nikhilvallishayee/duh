# D.U.H. — Terminal Session Recordings

Realistic terminal session simulations for embedding in READMEs, blog posts, and the website.
All sessions use the same tool names and output format as the real D.U.H. binary.

---

## Session 1 — Print Mode (`-p`)

Single-shot, non-interactive. The fastest path to a result.

```
$ duh -p "fix the bug in auth.py"
⠋ Thinking...
I'll read the file first to understand the issue.

⠸ Running Read(file="auth.py")
Found the bug: token validation checks `expires_at < now` but should be `<=` — tokens
expiring at exactly the current second are incorrectly rejected.

⠹ Running Edit(file="auth.py", old_string="expires_at < now", new_string="expires_at <= now")
✓ Applied edit to auth.py (line 42).

⠼ Running Bash(command="python -m pytest tests/test_auth.py -q")
...........
11 passed in 0.43s

✓ All 11 tests passing.

Done in 3 turns.
```

---

## Session 2 — Interactive REPL (multi-turn)

Full REPL session with tool calls, corrections, and a /cost summary at the end.

```
$ duh
D.U.H. v0.1.0  |  provider: anthropic  |  model: claude-sonnet-4-6
Type /help for commands. Ctrl-D to exit.

> add a /healthz endpoint to the FastAPI app in server.py

⠋ Thinking...
I'll look at server.py first to understand the existing structure.

⠸ Running Read(file="server.py")
I can see the FastAPI app is in `app = FastAPI()` at line 3. There are 4 existing
routes. I'll add /healthz after the existing routes.

⠹ Running Edit(
  file="server.py",
  old_string='if __name__ == "__main__":',
  new_string='@app.get("/healthz")\nasync def healthz():\n    return {"status": "ok"}\n\n\nif __name__ == "__main__":'
)
✓ Applied edit to server.py.

Let me verify it works.

⠼ Running Bash(command="python -c \"from server import app; print([r.path for r in app.routes])\"")
['/openapi.json', '/docs', '/redoc', '/users', '/users/{user_id}', '/healthz']

The /healthz endpoint is live. All existing routes are untouched.

> now write a test for it

⠋ Thinking...

⠸ Running Glob(pattern="tests/test_*.py")
Found: tests/test_users.py, tests/test_auth.py

⠹ Running Read(file="tests/test_users.py")
Using TestClient pattern from the existing test file.

⠺ Running Write(file="tests/test_healthz.py", content="...")
✓ Wrote tests/test_healthz.py (18 lines).

⠻ Running Bash(command="python -m pytest tests/test_healthz.py -v")
PASSED tests/test_healthz.py::test_healthz_returns_200
PASSED tests/test_healthz.py::test_healthz_payload

2 passed in 0.31s

All tests pass. The /healthz endpoint returns HTTP 200 with `{"status": "ok"}`.

> /cost
Session cost: $0.0031  |  tokens in: 4,821  out: 612  |  turns: 6

> /exit
Goodbye.
```

---

## Session 3 — TUI Mode (Rich terminal layout)

What the interactive REPL looks like with full Rich rendering enabled.

```
╔══════════════════════════════════════════════════════════════════════════════╗
║  D.U.H.  v0.1.0    provider: anthropic    model: claude-sonnet-4-6          ║
╚══════════════════════════════════════════════════════════════════════════════╝

 ┌─ Conversation ──────────────────────────────────────────────────────────── ─┐
 │                                                                              │
 │  You  refactor the database module to use a connection pool                  │
 │                                                                              │
 │  ⠸  Running Read(file="db.py")                                              │
 │                                                                              │
 │  Assistant  I can see `db.py` uses a single global `sqlite3.connect()`      │
 │  call at import time. I'll replace it with a thread-safe connection          │
 │  pool using `queue.Queue`.                                                   │
 │                                                                              │
 │  ⠹  Running Edit(file="db.py")                                              │
 │  ✓  Applied 3 edits                                                          │
 │                                                                              │
 │  ⠼  Running Bash(command="python -m pytest tests/test_db.py -q")            │
 │  ✓  18/18 tests passing (0.87s)                                              │
 │                                                                              │
 │  Refactoring complete. Pool size defaults to 5; configurable via             │
 │  `DB_POOL_SIZE` env var. Thread safety verified with concurrent test.        │
 │                                                                              │
 └──────────────────────────────────────────────────────────────────────────── ┘

 ┌─ Input ──────────────────────────────────────────────────────────────────── ─┐
 │  > _                                                                         │
 └──────────────────────────────────────────────────────────────────────────── ┘

 turns: 4  |  tokens: 3,241 / 200,000  |  cost: $0.0018  |  /help for commands
```

---

## Session 4 — Security Scan

Running the pluggable vulnerability scanner suite with SARIF output.

```
$ duh security scan --sarif-out findings.sarif
D.U.H. Security Scanner  |  policy: minimal  |  13 scanners available

Tier 1 — Minimal
  ruff-sec .................. ok    (  31ms,  0 findings)
  pip-audit ................. WARN  ( 1.4s,   1 finding )
  detect-secrets ............ ok    ( 280ms,  0 findings)
  cyclonedx-sbom ............ ok    ( 410ms,  0 findings)

Tier 1 — D.U.H.-specific
  duh-project-rce ........... ok    (  14ms,  0 findings)   CVE-2025-59536
  duh-mcp-poison ............ ok    (  19ms,  0 findings)   CVE-2025-54136
  duh-sandbox-bypass ........ ok    (  11ms,  0 findings)   CVE-2025-59532
  duh-command-injection ..... ok    (  22ms,  0 findings)   CVE-2026-35022
  duh-oauth-hardening ....... ok    (  16ms,  0 findings)

Summary
  Passed:   8 / 9 scanners
  Warnings: 1
  Errors:   0

Findings:
  [WARN] pip-audit: requests 2.28.2 → CVE-2024-35195 (MEDIUM, fix: upgrade to 2.32.0)

SARIF written to findings.sarif (GitHub Code Scanning compatible).
Run `duh security exception add CVE-2024-35195 --scope project --expires 2026-05-01`
to suppress with a tracked exception.
```

---

## Session 5 — Multi-Provider in One Session

Switching between Anthropic, OpenAI, and Ollama in a single REPL session.

```
$ duh
D.U.H. v0.1.0  |  provider: anthropic  |  model: claude-sonnet-4-6

> explain what this repo does in one sentence

⠋ Thinking...
This repo implements a FastAPI URL shortener with SQLite persistence and a
/healthz endpoint, covered by 20 pytest tests.

> /model openai/gpt-4o
Switched to provider: openai  |  model: gpt-4o
Note: OpenAI API key loaded from OPENAI_API_KEY.

> same question — what does this repo do?

⠋ Thinking...
This repository is a FastAPI-based URL shortener service backed by SQLite,
with full test coverage via pytest.

> /model ollama/llama3.2
Switched to provider: ollama  |  model: llama3.2
Note: Ollama daemon detected at localhost:11434. Model llama3.2 is available.

> what's the main entry point?

⠸ Running Glob(pattern="*.py")
⠹ Running Read(file="server.py")

The main entry point is `server.py` — it imports the FastAPI `app` object and
runs it with uvicorn when executed directly (`if __name__ == "__main__"`).

> /model anthropic/claude-haiku-4-5
Switched to provider: anthropic  |  model: claude-haiku-4-5

> /status
provider:  anthropic
model:     claude-haiku-4-5
session:   abc123  (12 turns, 3 provider switches)
cost:      $0.0041 (anthropic charges only; ollama and openai tracked separately)
context:   8,204 / 200,000 tokens

> /exit
Goodbye.
```

---

## Session 6 — Claude Agent SDK Drop-In (`--output-format stream-json`)

D.U.H. speaks the Claude Agent SDK NDJSON protocol, so it can replace `claude`
wherever that binary is expected (e.g., CI pipelines, SDK harness runners).

```
$ echo '{"prompt": "write hello.py that prints Hello, World!"}' \
    | duh --output-format stream-json --provider stub
{"type":"system","subtype":"init","session_id":"s_01abc","tools":["Read","Write","Edit","Bash","Glob","Grep"]}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I'll create hello.py now."}]}}
{"type":"tool_use","id":"tu_001","name":"Write","input":{"file_path":"hello.py","content":"print(\"Hello, World!\")\n"}}
{"type":"tool_result","tool_use_id":"tu_001","content":[{"type":"text","text":"Wrote hello.py (1 line)."}]}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Done. Run `python hello.py` to verify."}]}}
{"type":"result","subtype":"success","session_id":"s_01abc","total_cost_usd":0.0,"num_turns":2}
```
