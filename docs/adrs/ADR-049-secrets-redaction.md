# ADR-049: Secrets Redaction

**Status**: Proposed  
**Date**: 2026-04-11  

## Context

Tool output frequently contains secrets: API keys in environment dumps, bearer tokens in HTTP responses, database passwords in connection strings, and private keys in config files. When this output is sent to the model as tool results, the secrets become part of the conversation context — stored in session transcripts, potentially logged, and visible in any future context that includes the message history.

The reference TS harness redacts secrets before tool output reaches the model. D.U.H. currently has no redaction layer — tool output is sent raw.

## Decision

Introduce `duh/kernel/redact.py` with a single pure function:

```python
def redact_secrets(text: str) -> str
```

### Pattern Coverage

Ordered from most specific to least specific to minimize false positives:

| Pattern | Example | Regex |
|---------|---------|-------|
| PEM private keys | `-----BEGIN RSA PRIVATE KEY-----` | Multi-line block match |
| Anthropic API keys | `sk-ant-api03-...` | `sk-ant-api\d{2}-[A-Za-z0-9_-]{20,}` |
| OpenAI API keys | `sk-proj-...` | `sk-proj-[A-Za-z0-9_-]{20,}` |
| Generic sk- keys | `sk-...` (20+ chars) | `sk-[A-Za-z0-9_-]{20,}` |
| AWS access keys | `AKIAIOSFODNN7EXAMPLE` | `AKIA[0-9A-Z]{16}` |
| GitHub tokens | `ghp_...`, `gho_...`, `ghs_...` | `gh[posh]_[A-Za-z0-9_]{20,}` |
| Bearer tokens | `Bearer eyJ...` | `Bearer\s+[A-Za-z0-9._-]{10,}` |
| URL passwords | `://user:pass@host` | `(://[^:]+:)[^@]+(@)` |
| Generic assignments | `SECRET_KEY="value"` | Key-name heuristic + value capture |

All matches are replaced with `[REDACTED]`. The function is pure (no side effects) and fast (compiled regexes, single pass per pattern).

### Integration Points

The redaction function should be called:
1. In `NativeExecutor.run()` — on tool output before returning to the loop
2. In session transcript serialization — before writing to disk

Integration is deferred to the implementation task; this ADR covers the redaction module itself.

## Consequences

### Positive
- Secrets never reach the model context — reduces exfiltration risk
- Secrets never appear in session transcripts — reduces storage risk
- Pure function with no dependencies — easy to test and maintain
- Compiled regexes make it fast enough for the hot path

### Negative
- False positives: some legitimate strings matching the patterns get redacted (e.g., a variable named `sk-something-long-enough`)
- False negatives: novel secret formats not in the pattern list pass through

### Risks
- Over-redaction could remove important information from tool output. Mitigated by specific patterns (20+ char minimum for API keys) and by ordering patterns from most to least specific.
