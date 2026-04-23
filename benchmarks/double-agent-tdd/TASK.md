# Task (identical prompt given to every agent)

You are working inside a fresh clone of the D.U.H. (Duh is a Universal
Harness) repository — the source tree sits in your current working
directory. **Do all work inside the current working directory. Do not
`cd` to any other path, and do not edit files anywhere else.** The
project is a provider-agnostic AI coding agent written in Python.

## What to build

Add a **double-agent TDD flow** to D.U.H. using the red-green-refactor
driver/navigator pair-programming method. Two cooperating agents:

- The **Driver** writes the failing test (RED), then writes the minimum
  code to make it pass (GREEN), then refactors.
- The **Navigator** reviews the test spec before RED, suggests refactors
  after GREEN, and validates the final refactor.

The flow proceeds through the six phases in strict order:

1. Navigator drafts the test spec.
2. Driver writes the failing test. Tests are run. The test MUST fail — if
   it passes, the spec was not tight enough and Navigator revises.
3. Driver writes the minimum code to make the test pass (GREEN).
4. Navigator proposes a refactor.
5. Driver applies the refactor.
6. Navigator validates tests still pass and the refactor landed cleanly.

## Required deliverables

1. **ADR before you start implementing.** Write an architecture decision
   record under `adrs/` or `docs/adrs/` that lays out: the problem, the
   chosen design, alternatives considered, and the contract between
   Driver and Navigator. Then implement against that ADR.

2. **Implementation.** Wire the flow into D.U.H. as a real feature — a
   new subcommand, slash command, or public API the user can trigger.
   Reuse existing abstractions (Engine, PlanMode, SwarmTool,
   `duh/agents.py`) where they fit. Do not duplicate infrastructure.

3. **Unit tests.** Cover:
   - Each phase transition happens in order.
   - RED phase surfaces the assertion failure distinctly from GREEN.
   - Navigator's refactor suggestion is actually applied before
     validation.
   - The full flow completes end-to-end against a stub provider.

4. **Documentation updates:**
   - `README.md` — a new section describing the feature with a short
     example.
   - Wiki (`docs/wiki/`) — a new page or a subsection of an existing
     page with usage, flags, and a worked example.
   - Any other user-facing doc touched by the feature (slash command
     help, `--help` output, etc.).

## Working-tree protocol

- Do **not** `git commit`. Leave every change in the working tree.
- Do **not** `git push`.
- Do create new files freely; do modify existing files freely.
- If you run tests, that is fine — but do not revert changes based on
  test failures; leave the state you produced.

## Scope boundary

You are evaluated on:
- Whether an ADR exists before the first code change.
- Whether the implementation is wired in (not just skeletal).
- Whether the tests actually test the six-phase contract.
- Whether the README, wiki, and help text were updated coherently.
- Whether the feature uses the existing D.U.H. abstractions (agents,
  engine, tools) rather than reinventing them.

Stop when you have completed deliverables 1–4 above. A short trailing
summary of what you did, written to stdout, is helpful but not graded.
