# ADR-051: OAuth Provider Authentication

## Status
Accepted — implemented 2026-04-15

## Context
Until this point, provider authentication in D.U.H. was limited to API-key
credentials supplied through environment variables or a project `.env` file.
That is enough for Anthropic and for OpenAI's pay-per-token API, but two gaps
emerged:

1. **ChatGPT Plus / Pro subscribers** can access Codex-family models
   (`gpt-5.2-codex`, `gpt-5.1-codex`, etc.) *only* through the ChatGPT backend,
   which requires an OAuth access token — not an API key. Without OAuth support
   D.U.H. cannot reach those models at all.
2. **Secrets leaking into shells** is a real risk: API keys in environment
   variables show up in `env`, `ps -E`, core dumps, and CI logs. A persistent,
   permission-restricted credential store is the minimum hardening story.

Codex CLI's Rust reference solves both with a `~/.codex/auth.json`-style store
plus PKCE OAuth. We want the same capability in a small, focused Python form.

## Decision
Add a `duh/auth/` package that owns all provider credentials:

- `duh/auth/store.py` — a tiny JSON-backed store at `~/.config/duh/auth.json`
  with `0o600` permissions. Namespaced per provider:
  `{"providers": {"openai": {...}, "anthropic": {...}}}`.
- `duh/auth/anthropic.py` — API-key interactive setup and retrieval helpers,
  plus a `connect_anthropic_oauth()` entry point that delegates to
  `anthropic_oauth.py`.
- `duh/auth/anthropic_oauth.py` — PKCE OAuth flow (authorize → callback →
  token exchange → refresh) against `platform.claude.com`, using the
  Anthropic production client ID. The flow runs a local loopback HTTP server
  on port 1456 to capture the redirect, matches `state` to prevent CSRF, and
  stores access + refresh tokens with an `expires_at_ms` timestamp. Tokens
  are refreshed transparently when within 60 s of expiry. Supports
  `ANTHROPIC_AUTH_TOKEN` env var as a shortcut (skips the browser flow).
  The access token is used as a Bearer token in the `Authorization` header
  when calling `api.anthropic.com/v1/messages`.
- `duh/auth/openai_chatgpt.py` — PKCE OAuth flow (authorize → callback →
  token exchange → refresh) against `auth.openai.com`, plus API-key fallback.
  The flow runs a local loopback HTTP server on port 1455 to capture the
  redirect, matches `state` to prevent CSRF, and stores access + refresh
  tokens with an `expires_at_ms` timestamp. Tokens are refreshed
  transparently when within 60 s of expiry.

The credentials layer has no knowledge of adapters — it is a pure store +
flow module. Adapters import from it when they need a token.

## Consequences

### Positive
- ChatGPT Plus/Pro subscribers can use Codex models.
- Anthropic users can authenticate via browser-based OAuth (PKCE) instead of
  managing API keys, matching the Claude Code authentication experience.
- Secrets no longer need to live in long-lived environment variables.
- Permissions on `auth.json` (`0o600`) follow least-privilege principles.
- The auth layer is the same shape used by the Codex CLI, easing future
  compatibility (import/export of credentials).

### Negative
- One more moving piece to document; users now have *two* places credentials
  can come from (env var + store).
- OAuth flow requires a free local port (1455 for OpenAI, 1456 for Anthropic).
  Docker-in-Docker or tightly sandboxed environments may not be able to
  complete the flow; we provide a "paste the URL" fallback for those cases.

### Risks
- A bug in the store could leak credentials. Mitigated by: chmod on write,
  JSON-only format, unit tests for read/write round-trips.
- Refresh tokens are long-lived. Users who suspect compromise should delete
  `~/.config/duh/auth.json`; we expose no CLI "revoke" yet.

## Implementation Notes
- Files: `duh/auth/store.py`, `duh/auth/anthropic.py`,
  `duh/auth/anthropic_oauth.py`, `duh/auth/openai_chatgpt.py`.
- Dependencies: `httpx` (already a hard dep) for HTTP calls.
- Testing: unit tests mock `httpx.Client`, feed canned OAuth responses, and
  verify the round-trip token lifecycle plus refresh on expiry.
- Anthropic OAuth endpoints: authorize at `platform.claude.com/oauth/authorize`,
  token at `platform.claude.com/v1/oauth/token`, profile at
  `api.anthropic.com/api/oauth/profile`.
- Anthropic OAuth scopes: `user:inference user:profile`.
- Provider registry (`duh/providers/registry.py`) checks for OAuth tokens
  before falling back to API key; `AnthropicProvider` accepts an `oauth_token`
  kwarg and sets the `Authorization: Bearer` header accordingly.
- REPL `/connect anthropic` offers interactive choice: (1) OAuth browser login,
  (2) API key.
- Related: ADR-052 (ChatGPT Codex adapter) consumes this layer.
