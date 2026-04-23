"""Aggregate benchmark-2 judge scores + adversarial pass rates."""

from __future__ import annotations
import json, pathlib, statistics

HERE = pathlib.Path(__file__).parent
JUDGMENTS_DIR = HERE / "results" / "judgments"
RESULTS_DIR = HERE / "results"
SCOREBOARD = RESULTS_DIR / "scoreboard.md"

AGENTS = ["claude-code-opus", "duh-opus", "codex-gpt54", "duh-gpt54",
          "gemini-cli-3.1", "duh-gemini-3.1"]
JUDGES = ["j-opus", "j-gpt54", "j-g31"]
DIMENSIONS = [
    "adr_quality", "implementation_completeness", "correctness_happy_path",
    "adversarial_correctness", "concurrency_discipline", "design_doc",
    "api_ergonomics", "test_coverage", "code_quality", "protocol_adherence",
]


def load(j, a):
    p = JUDGMENTS_DIR / j / f"{a}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def load_meta(a):
    p = RESULTS_DIR / a / "meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


def load_adv(a):
    p = RESULTS_DIR / a / "adversarial.json"
    return json.loads(p.read_text()) if p.exists() else {}


def total(d):
    s = d.get("scores")
    if not isinstance(s, dict):
        return None
    return sum(int(s.get(x, 0)) for x in DIMENSIONS)


def render():
    lines = ["# Benchmark 2 — Rate Limiter — Scoreboard", "",
             "Mean across 3 judges, /50. Adv = hidden adversarial pass rate.", ""]
    lines.append("| Agent | j-opus | j-gpt54 | j-g31 | Mean /50 | Adv | Elapsed | Diff |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    rows = []
    for a in AGENTS:
        totals = [total(load(j, a)) for j in JUDGES]
        valid = [t for t in totals if t is not None]
        mean_50 = statistics.mean(valid) if valid else None
        rows.append((mean_50, a, totals, load_meta(a), load_adv(a)))
    rows.sort(key=lambda r: -1 if r[0] is None else r[0], reverse=True)
    for mean_50, a, totals, m, adv in rows:
        cells = " | ".join(str(t) if t is not None else "–" for t in totals)
        mean_str = f"{mean_50:.1f}" if mean_50 is not None else "FAIL"
        elapsed = m.get("elapsed_s", "–")
        diff = m.get("diff_bytes", 0)
        diff_str = f"{diff // 1024}K" if isinstance(diff, int) and diff > 0 else "–"
        rate = adv.get("pass_rate")
        adv_str = f"{rate:.0%}" if isinstance(rate, (int, float)) else "–"
        lines.append(f"| `{a}` | {cells} | **{mean_str}** | {adv_str} | {elapsed}s | {diff_str} |")

    # Per-dimension means
    lines.append("")
    lines.append("## Per-dimension means (3-judge average)")
    lines.append("")
    header = "| Agent | " + " | ".join(d.replace("_", " ")[:14] for d in DIMENSIONS) + " |"
    sep = "|---|" + ("---:|" * len(DIMENSIONS))
    lines.append(header)
    lines.append(sep)
    for mean_50, a, _, _, _ in rows:
        means = []
        for d in DIMENSIONS:
            vals = []
            for j in JUDGES:
                sc = load(j, a).get("scores", {})
                if isinstance(sc, dict) and d in sc:
                    try:
                        vals.append(int(sc[d]))
                    except (TypeError, ValueError):
                        pass
            means.append(f"{statistics.mean(vals):.1f}" if vals else "–")
        lines.append(f"| `{a}` | " + " | ".join(means) + " |")

    # One-line summaries
    lines.append("")
    lines.append("## Judge one-liners")
    lines.append("")
    for mean_50, a, _, _, _ in rows:
        lines.append(f"### `{a}`  (mean {mean_50:.1f}/50)" if mean_50 is not None else f"### `{a}`")
        for j in JUDGES:
            s = load(j, a).get("one_line_summary", "–")
            lines.append(f"- **{j}**: {s}")
        lines.append("")

    SCOREBOARD.parent.mkdir(parents=True, exist_ok=True)
    SCOREBOARD.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\nwrote {SCOREBOARD}")


if __name__ == "__main__":
    render()
