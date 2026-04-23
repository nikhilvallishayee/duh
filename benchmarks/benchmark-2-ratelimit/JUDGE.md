# Judgment rubric (identical for every judge)

You are judging a candidate implementation of a distributed rate limiter.
You will be given:

1. `TASK.md` — the exact prompt given to the candidate.
2. The full diff (`diff.patch`) from the baseline scaffold.
3. The list of files added/modified (`files.txt`).
4. Any session log the CLI produced (`session.log`).
5. **The adversarial-suite pass rate** (`adversarial.json`) — pass
   counts from a hidden property-test suite run against the candidate's
   code. Use this as primary evidence for dimension 4.

## Score each dimension from 0 to 5

Use whole numbers. 0 = absent or broken. 5 = exemplary.

| # | Dimension | What 5 looks like |
|---|-----------|-------------------|
| 1 | **ADR quality** | ADR exists, predates implementation, covers algorithm choice + Redis concurrency + clock-skew, presents ≥2 rejected alternatives with real tradeoff analysis. |
| 2 | **Implementation completeness** | Both algorithms, both backends, decorator, and middleware all present and wired. No TODOs, no stubs. |
| 3 | **Correctness under happy-path tests** | Agent-authored test suite runs green on the agent's own code. |
| 4 | **Adversarial correctness** | Hidden adversarial suite passes. Score = ceil(5 × pass_fraction). 5 = all pass. 0 = <10% pass. |
| 5 | **Concurrency discipline** | No TOCTOU bugs visible in diff. Proper use of locks / CAS / Lua atomicity for the backend chosen. |
| 6 | **Design document quality** | `docs/design.md` clearly explains request flow, failure modes, degradation strategy. Diagrams are legible. |
| 7 | **API ergonomics** | `enforce`, decorator, middleware follow conventions. Response headers are spec-compliant. |
| 8 | **Test coverage + property tests** | Line coverage substantial; property tests assert real invariants (never over-grant); concurrency tests actually race. |
| 9 | **Code quality** | Clean separation of concerns. Backend abstraction isn't leaking Redis-isms into algorithms. Small functions. |
| 10 | **Protocol adherence** | No commits, no pushes, working tree contains the changes. |

## Output format (strict)

Return **only** a single JSON object. No preamble, no markdown fences,
no commentary:

```json
{
  "target": "<agent-id-as-given>",
  "scores": {
    "adr_quality": 0,
    "implementation_completeness": 0,
    "correctness_happy_path": 0,
    "adversarial_correctness": 0,
    "concurrency_discipline": 0,
    "design_doc": 0,
    "api_ergonomics": 0,
    "test_coverage": 0,
    "code_quality": 0,
    "protocol_adherence": 0
  },
  "total_out_of_50": 0,
  "adversarial_pass_rate": 0.0,
  "one_line_summary": "…",
  "strengths": ["…"],
  "weaknesses": ["…"]
}
```

`strengths` and `weaknesses` are each up to 3 short bullets.
`one_line_summary` is a single sentence under 140 characters.
`total_out_of_50` is the sum of the 10 `scores`.
`adversarial_pass_rate` is the float from `adversarial.json` if
present, else 0.0.

Do not reason out loud. Do not include anything outside the JSON object.
