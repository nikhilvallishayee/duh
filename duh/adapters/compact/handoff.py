"""Structured handoff summary format for compaction.

Instead of free-form "summarize the conversation", the compaction
prompt asks the model to produce structured sections:

- Current progress: what's been accomplished
- Key decisions: architectural/design choices made
- Active constraints: requirements, limitations, user preferences
- Pending work: TODOs, incomplete tasks
- Critical data: file paths, variable names, error messages referenced
"""

HANDOFF_PROMPT = '''Summarize this conversation as a structured handoff.
Organize your summary into these sections:

## Progress
What has been accomplished so far.

## Decisions
Key architectural or design choices that were made and why.

## Constraints
Requirements, limitations, or user preferences that must be respected.

## Pending
Work that remains to be done, in priority order.

## Context
Critical file paths, variable names, error messages, or data that the
next assistant turn will need to reference.

Be concise. Use bullet points. Preserve specifics (exact file paths,
function names, error messages) — don't generalize.'''
