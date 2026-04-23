"""Aggregate the 18 judge scores into a single scoreboard.md."""

from __future__ import annotations

import json
import pathlib
import statistics

HERE = pathlib.Path(__file__).parent
JUDGMENTS_DIR = HERE / "results" / "judgments"
META_GLOB = HERE / "results"
SCOREBOARD = HERE / "results" / "scoreboard.md"

AGENTS = [
    "claude-code-opus",
    "duh-opus",
    "codex-gpt54",
    "duh-gpt54",
    "gemini-cli-3.1",
    "duh-gemini-3.1",
    "duh-llama4-scout",
    "duh-gpt-oss-120b",
    "duh-qwen3-32b",
]
JUDGES = ["j-opus", "j-gpt54", "j-g31"]

DIMENSIONS = [
    "adr_quality",
    "implementation_completeness",
    "use_of_abstractions",
    "test_coverage",
    "documentation",
    "code_quality",
    "protocol_adherence",
]


def load_score(judge: str, agent: str) -> dict:
    p = JUDGMENTS_DIR / judge / f"{agent}.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def load_meta(agent: str) -> dict:
    p = META_GLOB / agent / "meta.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return {}


def total(score: dict) -> int | None:
    s = score.get("scores")
    if not isinstance(s, dict):
        return None
    return sum(int(s.get(d, 0)) for d in DIMENSIONS)


def render() -> str:
    lines: list[str] = []
    lines.append("# Double-Agent TDD Benchmark — Scoreboard")
    lines.append("")
    lines.append("Mean across 3 judges, /35. Per-judge columns show raw sums.")
    lines.append("")
    lines.append("| Agent | j-opus | j-gpt54 | j-g31 | Mean /35 | Mean /5 | Elapsed | Diff bytes | Exit |")
    lines.append("|-------|--------|---------|-------|---------:|--------:|--------:|-----------:|-----:|")
    rows = []
    for agent in AGENTS:
        totals = []
        per_judge_cells = []
        for judge in JUDGES:
            sc = load_score(judge, agent)
            t = total(sc)
            if t is None:
                per_judge_cells.append("–")
            else:
                per_judge_cells.append(str(t))
                totals.append(t)
        mean_35 = statistics.mean(totals) if totals else 0.0
        mean_5 = mean_35 / 7 if totals else 0.0
        meta = load_meta(agent)
        elapsed = meta.get("elapsed_s", "–")
        diff_bytes = meta.get("diff_bytes", "–")
        exit_code = meta.get("exit_code", "–")
        rows.append((mean_35, agent, per_judge_cells, mean_35, mean_5, elapsed, diff_bytes, exit_code))
    rows.sort(key=lambda r: r[0], reverse=True)
    for _, agent, cells, mean_35, mean_5, elapsed, diff_bytes, exit_code in rows:
        lines.append(
            f"| `{agent}` | {cells[0]} | {cells[1]} | {cells[2]} | "
            f"{mean_35:.1f} | {mean_5:.2f} | {elapsed}s | {diff_bytes} | {exit_code} |"
        )

    # Per-dimension breakdown.
    lines.append("")
    lines.append("## Per-dimension mean (averaged across 3 judges)")
    lines.append("")
    header = "| Agent | " + " | ".join(d.replace("_", " ") for d in DIMENSIONS) + " |"
    sep = "|-------|" + ("---:|" * len(DIMENSIONS))
    lines.append(header)
    lines.append(sep)
    for agent in AGENTS:
        means = []
        for d in DIMENSIONS:
            vals = []
            for judge in JUDGES:
                sc = load_score(judge, agent).get("scores", {})
                if isinstance(sc, dict) and d in sc:
                    try:
                        vals.append(int(sc[d]))
                    except (TypeError, ValueError):
                        pass
            means.append(f"{statistics.mean(vals):.1f}" if vals else "–")
        lines.append(f"| `{agent}` | " + " | ".join(means) + " |")

    # Judge disagreement: flag any target with judge-spread > 3 points.
    lines.append("")
    lines.append("## Judge disagreement")
    lines.append("")
    disagree = []
    for agent in AGENTS:
        # Pair each judge with its total; keep the pairing so the label
        # matches the value in the report.
        pairs = [(j, total(load_score(j, agent))) for j in JUDGES]
        present = [(j, t) for j, t in pairs if t is not None]
        if len(present) >= 2:
            vals = [t for _, t in present]
            spread = max(vals) - min(vals)
            if spread > 3:
                disagree.append((agent, spread, present))
    if disagree:
        for agent, spread, present in disagree:
            tot_str = ", ".join(f"{j}={t}" for j, t in present)
            lines.append(f"- `{agent}`: spread {spread} — {tot_str}")
    else:
        lines.append("No target showed >3-point judge spread. Means are stable.")

    # Summaries.
    lines.append("")
    lines.append("## One-line summaries (per judge, per target)")
    lines.append("")
    for agent in AGENTS:
        lines.append(f"### `{agent}`")
        for judge in JUDGES:
            sc = load_score(judge, agent)
            s = sc.get("one_line_summary", sc.get("error", "–"))
            lines.append(f"- **{judge}**: {s}")
        lines.append("")
    return "\n".join(lines)


def main() -> None:
    SCOREBOARD.parent.mkdir(parents=True, exist_ok=True)
    SCOREBOARD.write_text(render())
    print(f"wrote {SCOREBOARD}")


if __name__ == "__main__":
    main()
