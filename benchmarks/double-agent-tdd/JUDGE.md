# Judgment rubric (identical for every judge)

You are judging a candidate implementation of a "double-agent TDD flow"
feature added to the D.U.H. repository. You will be given:

1. `TASK.md` — the exact prompt that was given to the candidate.
2. The full diff (`diff.patch`) vs the baseline commit.
3. The list of files added/modified (`files.txt`).
4. Any session log the candidate's CLI produced (`session.log`).

## Score each dimension from 0 to 5

Use whole numbers. 0 = absent or broken. 5 = exemplary.

| # | Dimension | What 5 looks like |
|---|-----------|-------------------|
| 1 | **ADR quality** | ADR exists, predates the implementation, names the problem, presents real alternatives, explains the Driver/Navigator contract. |
| 2 | **Implementation completeness** | Real wiring into D.U.H. — a new subcommand / slash command / public API path. Not just a skeleton. |
| 3 | **Use of existing abstractions** | Reuses Engine / PlanMode / agents.py / Swarm where they fit. Does not reinvent infrastructure. |
| 4 | **Test coverage of the six-phase contract** | Tests assert each phase transition, RED-distinct-from-GREEN, refactor application, and end-to-end happy path. |
| 5 | **Documentation updates** | README section with a worked example, wiki page/section, help text coherent with the implementation. |
| 6 | **Code quality** | Clean names, small functions, no dead code, consistent with existing style. |
| 7 | **Protocol adherence** | No git commits, no pushes, working tree contains the changes. |

## Output format (strict)

Return **only** a single JSON object. No preamble, no markdown fences, no
commentary:

```json
{
  "target": "<agent-id-as-given>",
  "scores": {
    "adr_quality": 0,
    "implementation_completeness": 0,
    "use_of_abstractions": 0,
    "test_coverage": 0,
    "documentation": 0,
    "code_quality": 0,
    "protocol_adherence": 0
  },
  "total_out_of_35": 0,
  "one_line_summary": "…",
  "strengths": ["…"],
  "weaknesses": ["…"]
}
```

`strengths` and `weaknesses` are each a list of up to 3 short bullets.
`one_line_summary` is a single sentence under 140 characters.
`total_out_of_35` is the sum of the 7 `scores` — you compute it.

Do not reason out loud. Do not include anything outside the JSON object.
