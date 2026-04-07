# ADR-021: NDJSON SDK Protocol

**Status**: Implemented  
**Date**: 2026-04-07

## Decision

D.U.H. implements the Claude Agent SDK's bidirectional NDJSON stream-json protocol (`--input-format stream-json --output-format stream-json`). This enables drop-in replacement of Claude Code as the CLI backend for any Claude Agent SDK consumer.

## Protocol

- Control requests/responses (initialize handshake)
- User messages via stdin NDJSON
- Assistant/result messages via stdout NDJSON
- U+2028/U+2029 line separator escaping for NDJSON safety

## Files

- `duh/cli/ndjson.py` — NDJSON helpers
- `duh/cli/sdk_runner.py` — Protocol handler
- `bin/duh-sdk-shim` — SDK CLI shim

## Verification

Tested end-to-end with Claude Agent SDK v0.1.56 and Universal Companion API.
