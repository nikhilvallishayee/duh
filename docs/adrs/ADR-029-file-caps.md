# ADR-029: Large File Safety Caps

**Status:** Accepted — partial (Read/Write 50 MB caps enforced; session-state cap
`MAX_SESSION_BYTES` (64 MB) is declared in `duh/adapters/file_store.py` but still not
checked inside `FileStore.save()` — tracked as a known gap)
**Date**: 2026-04-08

## Context

D.U.H. has no size limits on file reads, writes, or session state. A model can request `Read("/dev/urandom")` or write a 500MB generated file, consuming all available memory and crashing the process. Similarly, session persistence can grow unbounded as conversation history accumulates images, tool outputs, and error traces.

The reference TS harness caps reads at ~50MB and has similar guards. Without these limits, duh is vulnerable to both adversarial prompts (model told to read a huge file) and accidental OOM (model tries to read a compiled binary or database file).

## Decision

Enforce three caps, configurable via `duh.toml`:

| Resource | Default Cap | Config Key |
|----------|-------------|------------|
| File read | 50 MB | `limits.max_read_bytes` |
| File write | 50 MB | `limits.max_write_bytes` |
| Session state | 64 MB | `limits.max_session_bytes` |

### Enforcement Points

**Read tool**: Check `os.path.getsize()` before opening. Return a structured error to the model: `"File is {size}MB, exceeding the {cap}MB read limit. Use a range read or a different approach."`

**Write tool**: Check `len(content)` before writing. Reject with a clear message.

**Session persistence**: Check serialized size before saving. If over cap, trigger compaction (ADR-035) before saving. If still over cap after compaction, truncate oldest messages.

```python
MAX_READ_BYTES = 50 * 1024 * 1024  # 50 MB

async def read_file(path: str) -> str:
    size = os.path.getsize(path)
    if size > MAX_READ_BYTES:
        return error_result(f"File too large: {size // 1024 // 1024}MB > {MAX_READ_BYTES // 1024 // 1024}MB limit")
    ...
```

### Binary Detection

Additionally, files that appear to be binary (null bytes in first 8KB) are rejected with a specific message: `"File appears to be binary. Use a hex viewer or specific extraction tool instead."`

## Consequences

### Positive
- Prevents OOM crashes from adversarial or accidental large file access
- Matches proven limits from production TS harness
- Clear error messages guide the model to alternative approaches

### Negative
- Legitimate large file operations require explicit config override
- Binary detection heuristic may misidentify files with null bytes

### Risks
- Session cap interacts with compaction — needs integration testing with ADR-035

## Implementation Notes

- Read cap: `duh/tools/read.py` (`MAX_FILE_READ_BYTES = 50 * 1024 * 1024`). Binary
  detection rejects files with null bytes in the first 8 KB.
- Write cap: `duh/tools/write.py` (`MAX_FILE_WRITE_BYTES = 50 * 1024 * 1024`).
- Session cap: `duh/adapters/file_store.py` (`MAX_SESSION_BYTES = 64 * 1024 * 1024`).
  Constant is defined but `FileStore.save()` does not consult it today — see status
  note above.

Related: ADR-035 (compaction pipeline that should be triggered before truncation).
