# Judgment rubric (identical for every judge)

You are judging a documentation set produced over a pinned D.U.H.
source tree. You will be given:

1. `TASK.md` — the exact prompt given to the candidate.
2. The full diff (`diff.patch`) of docs-new/ (the only directory the
   candidate was permitted to write).
3. The list of files added/modified (`files.txt`).
4. Any session log the CLI produced (`session.log`).
5. **The consistency-harness results** (`consistency.json`) — automated
   checks over the candidate's docs: symbol existence, signature
   match, import check, coverage. Drives dimension 5.

## Score each dimension from 0 to 5

Use whole numbers. 0 = absent or broken. 5 = exemplary.

| # | Dimension | What 5 looks like |
|---|-----------|-------------------|
| 1 | **Architecture doc quality** | Structure, flow-of-control, extension points all covered. Diagrams help rather than decorate. Non-goals stated. |
| 2 | **Tutorial quality** | End-to-end custom-tool path works. Code blocks are runnable. Expected output shown. Pedagogical ordering. |
| 3 | **API reference completeness** | Coverage of `duh/kernel/` + `duh/ports/` is substantial (≥70% of public symbols). Signatures match source. Useful examples. |
| 4 | **Cross-artefact coherence** | Architecture, tutorial, API reference agree on names, signatures, concepts. |
| 5 | **Faithfulness (consistency harness)** | Score = ceil(5 × pass_fraction) from `consistency.json`. 5 = all checks pass. 0 = <10% pass. |
| 6 | **Reading discipline** | Session log shows the agent read source before writing. No invented APIs. |
| 7 | **Writing quality** | Prose is clear, concise, professional. No filler. No hallucinated flavour. |
| 8 | **Structure & navigation** | Index links work. Cross-refs between the three docs. Tables of contents where helpful. |
| 9 | **Protocol adherence** | No commits, no pushes, no files outside `docs-new/`. |

## Output format (strict)

Return **only** a single JSON object. No preamble, no markdown fences,
no commentary:

```json
{
  "target": "<agent-id-as-given>",
  "scores": {
    "architecture_doc": 0,
    "tutorial_quality": 0,
    "api_completeness": 0,
    "cross_artifact_coherence": 0,
    "faithfulness": 0,
    "reading_discipline": 0,
    "writing_quality": 0,
    "structure_navigation": 0,
    "protocol_adherence": 0
  },
  "total_out_of_45": 0,
  "consistency_pass_rate": 0.0,
  "one_line_summary": "…",
  "strengths": ["…"],
  "weaknesses": ["…"]
}
```

`total_out_of_45` is the sum of the 9 `scores`.
`consistency_pass_rate` is the float from `consistency.json` if
present, else 0.0. Do not reason out loud. Nothing outside the JSON.
