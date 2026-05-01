# Review — Token-Bucket Rate Limiter

## Summary
Implementation matches the ADR. Tests cover the three acceptance
criteria. One nit before merge.

## Concerns
- **Style** (`implementation.py`, `__init__`): cast-to-float on already-
  float inputs is harmless but redundant. Either drop the casts or add
  a docstring note that ints are accepted.

## Verdict
APPROVE WITH NITS
