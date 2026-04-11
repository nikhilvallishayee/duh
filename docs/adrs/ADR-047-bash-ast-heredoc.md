# ADR-047: Bash AST Heredoc and Process Substitution

**Status**: Proposed  
**Date**: 2026-04-11  
**Depends on**: ADR-034 (Bash AST Parser)

## Context

The Bash AST parser (ADR-034) tokenizes shell commands by splitting on pipes, logical operators, and semicolons, then classifying each segment. It handles `$(...)` subshells, backticks, and quoted strings. Three constructs remain unhandled:

1. **Heredocs** (`<<EOF...EOF`) — the tokenizer currently treats the heredoc body as if it were regular command text, potentially splitting on `|` or `&&` within the heredoc body and producing false-positive security classifications.

2. **Process substitution** (`<(cmd)` and `>(cmd)`) — these are treated as redirect operators followed by parenthesized groups, but the inner command is not extracted or classified.

3. **ANSI-C quoting** (`$'...'`) — the `$` prefix before the single quote is not recognized by the quote masker, so escape sequences like `\n` and `\t` inside `$'...'` can cause incorrect tokenization.

All three are commonly used in legitimate shell workflows (multi-line configs, diff comparisons, formatted output). Missing them means the security classifier either misclassifies safe commands or misses dangerous ones hidden inside these constructs.

## Decision

### Heredoc Extraction

Add a `_extract_heredocs()` function that scans for `<<[-]?DELIM` patterns, captures everything between the marker line and the terminating delimiter, and removes the heredoc body from the command text before operator splitting. Heredoc bodies are data, not code — they are not classified as separate segments. Supports:
- `<<EOF` (standard)
- `<<-EOF` (tab-stripped)
- `<<'EOF'` and `<<"EOF"` (quoted delimiters)

### Process Substitution

Add a `_extract_process_subs()` function that handles `<(...)` and `>(...)` by tracking parenthesis depth (like the existing `$(...)` extractor). The inner command is extracted as a `SUBSHELL` segment for classification. The outer command retains a placeholder.

### ANSI-C Quoting

Extend `_mask_quotes()` to recognize `$'...'` patterns via the regex `\$'(?:[^'\\]|\\.)*'`. These are masked before regular quote detection, preventing backslash escape sequences from interfering with tokenization.

### Processing Order

The tokenizer pipeline becomes:
1. Strip comments
2. Mask quotes (including ANSI-C)
3. Extract heredocs (remove bodies)
4. Re-mask after heredoc removal
5. Extract process substitutions
6. Re-mask after process sub removal
7. Extract `$(...)` and backtick subshells
8. Split on operators
9. Classify each segment

## Consequences

### Positive
- Heredoc bodies no longer cause false positives from operators in data
- Dangerous commands inside process substitutions are now classified
- ANSI-C quoting no longer breaks tokenization
- All three constructs are common in real-world shell scripts

### Negative
- The tokenizer pipeline grows from 5 steps to 9 — more complex
- Heredoc extraction requires line-by-line scanning, slightly slower for multi-line commands

### Risks
- Nested heredocs (heredoc inside a process substitution inside a heredoc) are rare but theoretically possible. The current implementation handles one level of nesting. Deeper nesting falls back to conservative classification.
