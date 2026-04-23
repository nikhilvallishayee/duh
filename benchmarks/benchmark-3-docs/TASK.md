# Task (identical prompt given to every agent)

You are inside a snapshot of the D.U.H. (Duh is a Universal Harness)
codebase at commit `645d91a`. The source tree is your working
directory. There is also an empty `docs-new/` directory — write all
your documentation there.

**Do not modify any file outside `docs-new/`.** You may (and should)
read anything in the repo freely, but the existing `docs/` tree and
all source code is read-only for this task. Do not `cd` to any other
path.

## What to produce

A coherent documentation set under `docs-new/` — three artefacts plus
an index — grounded in the actual source tree.

1. **`docs-new/ARCHITECTURE.md`** — 3000–5000 words.
   - Top-level module layout with a component diagram (ASCII or
     mermaid).
   - Data flow for a single user request from CLI parse to model
     response render.
   - Extension points: how to add a new provider adapter, a new tool,
     a new slash command. Reference specific files.
   - Security architecture summary (taint propagation, confirmation
     tokens, sandbox layers).
   - Threading / concurrency model.
   - Known tradeoffs and non-goals.

2. **`docs-new/TUTORIAL.md`** — 2000–3500 words.
   - "From zero to your first custom tool": install, hello-world,
     stub-mode run, then walk through building a custom tool
     end-to-end (schema, implementation, registration, approval
     policy, first call).
   - Every code block must be runnable against this pinned checkout —
     use real symbols, real imports, real constructors. Do not invent
     APIs.
   - Include expected output for each step.

3. **`docs-new/API.md`** — 2500–4500 words.
   - Cover every public class and function in `duh/kernel/` and
     `duh/ports/`.
   - Signature, purpose, parameters, return type, exceptions, one
     short example. Organised by module.
   - Signatures must match the source — not paraphrase, not
     approximate.

4. **`docs-new/index.md`** — landing page linking the three docs above
   with a one-paragraph description each.

## Constraints

- Every symbol mentioned in the tutorial or API reference must exist
  in the codebase with the documented signature. A hidden consistency
  harness will run after your session and record the pass rate.
- Every feature mentioned in the architecture doc must be reachable
  from at least one path in the tutorial or API reference.

## Working-tree protocol

- Do **not** `git commit`. Leave every change in the working tree.
- Do **not** `git push`.
- Do **not** modify any file outside `docs-new/`. Only `docs-new/`
  may be written.
- Reading source is expected and encouraged.

## Scope boundary

You are evaluated on:

- Architecture doc: structure, flow-of-control, extension points,
  non-goals.
- Tutorial: runnable code, expected output, pedagogical ordering.
- API reference: coverage of `duh/kernel/` + `duh/ports/`, signature
  accuracy.
- Cross-artefact coherence: same names, same signatures, same
  concepts across all three docs.
- Faithfulness to the codebase: hidden automated consistency checks
  (symbol existence, signature match, import check, coverage).
- Reading discipline (the session log will show whether you opened
  source).
- Writing quality. Protocol adherence.

A short trailing summary of what you did, written to stdout, is
helpful but not graded.
