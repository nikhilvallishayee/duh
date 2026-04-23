"""Aggregate benchmark-3 judge scores + consistency pass rates."""

from __future__ import annotations
import json, pathlib, statistics

HERE = pathlib.Path(__file__).parent
JUDGMENTS_DIR = HERE / "results" / "judgments"
RESULTS_DIR = HERE / "results"
SCOREBOARD = RESULTS_DIR / "scoreboard.md"

AGENTS = ["claude-code-opus", "duh-opus", "codex-gpt54", "duh-gpt54",
          "gemini-cli-3.1", "duh-gemini-3.1"]
JUDGES = ["j-opus", "j-gpt54", "j-g31"]
DIMENSIONS = ["architecture_doc", "tutorial_quality", "api_completeness",
              "cross_artifact_coherence", "faithfulness", "reading_discipline",
              "writing_quality", "structure_navigation", "protocol_adherence"]


def load(j, a):
    p = JUDGMENTS_DIR / j / f"{a}.json"
    return json.loads(p.read_text()) if p.exists() else {}


def load_meta(a):
    p = RESULTS_DIR / a / "meta.json"
    return json.loads(p.read_text()) if p.exists() else {}


def load_cons(a):
    p = RESULTS_DIR / a / "consistency.json"
    return json.loads(p.read_text()) if p.exists() else {}


def total(d):
    s = d.get("scores")
    if not isinstance(s, dict): return None
    return sum(int(s.get(x, 0)) for x in DIMENSIONS)


def render():
    lines = ["# Benchmark 3 — Documentation — Scoreboard", ""]
    lines.append("Mean across 3 judges, /45. Cons = consistency-harness pass rate.")
    lines.append("")
    lines.append("| Agent | j-opus | j-gpt54 | j-g31 | Mean /45 | Cons | Elapsed | Diff |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    rows = []
    for a in AGENTS:
        totals = [total(load(j, a)) for j in JUDGES]
        valid = [t for t in totals if t is not None]
        mean_45 = statistics.mean(valid) if valid else None
        m = load_meta(a); c = load_cons(a)
        rows.append((mean_45, a, totals, m, c))
    rows.sort(key=lambda r: -1 if r[0] is None else r[0], reverse=True)
    for mean_45, a, totals, m, c in rows:
        cells = " | ".join(str(t) if t is not None else "–" for t in totals)
        mean_str = f"{mean_45:.1f}" if mean_45 is not None else "FAIL"
        elapsed = m.get("elapsed_s", "–")
        diff = m.get("diff_bytes", 0)
        diff_str = f"{diff//1024}K" if isinstance(diff, int) and diff > 0 else "–"
        cons_rate = c.get("pass_rate"); cons_str = f"{cons_rate:.0%}" if isinstance(cons_rate, (int, float)) else "–"
        lines.append(f"| `{a}` | {cells} | **{mean_str}** | {cons_str} | {elapsed}s | {diff_str} |")
    SCOREBOARD.parent.mkdir(parents=True, exist_ok=True)
    SCOREBOARD.write_text("\n".join(lines))
    print("\n".join(lines))
    print(f"\nwrote {SCOREBOARD}")


if __name__ == "__main__":
    render()
