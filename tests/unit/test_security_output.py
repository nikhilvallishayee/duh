"""Tests for ``duh security scan`` output formatting (QX-1).

Covers:
- text format produces a table with the correct columns
- sarif format produces valid JSON
- summary line counts are correct
- results are sorted by severity (CRITICAL first)
"""

from __future__ import annotations

import json

import pytest

from duh.security.cli import _build_summary, _sort_findings, format_text, main as security_main
from duh.security.finding import Finding, Location, Severity


# -- helpers -----------------------------------------------------------------

def _make_finding(
    *,
    id: str = "VULN-1",
    scanner: str = "test-scanner",
    severity: Severity = Severity.HIGH,
    message: str = "something bad",
    file: str = "app.py",
    line: int = 42,
) -> Finding:
    return Finding.create(
        id=id,
        aliases=(),
        scanner=scanner,
        severity=severity,
        message=message,
        description="",
        location=Location(file=file, line_start=line, line_end=line, snippet=""),
    )


SAMPLE_FINDINGS = [
    _make_finding(id="LOW-1", severity=Severity.LOW, message="minor issue", scanner="scanner-a", file="z.py", line=10),
    _make_finding(id="CRIT-1", severity=Severity.CRITICAL, message="exec injection", scanner="scanner-b", file="a.py", line=1),
    _make_finding(id="HIGH-1", severity=Severity.HIGH, message="sql injection", scanner="scanner-c", file="b.py", line=5),
    _make_finding(id="MED-1", severity=Severity.MEDIUM, message="weak hash", scanner="scanner-a", file="c.py", line=20),
    _make_finding(id="INFO-1", severity=Severity.INFO, message="debug enabled", scanner="scanner-d", file="d.py", line=3),
    _make_finding(id="HIGH-2", severity=Severity.HIGH, message="path traversal", scanner="scanner-b", file="e.py", line=8),
]


# -- test: text format produces table with correct columns -------------------

class TestTextFormatTable:

    def test_has_header_with_all_columns(self) -> None:
        output = format_text(SAMPLE_FINDINGS, color=False)
        lines = output.splitlines()
        header = lines[0]
        assert "Severity" in header
        assert "Scanner" in header
        assert "Finding" in header
        assert "File:Line" in header

    def test_has_separator_line(self) -> None:
        output = format_text(SAMPLE_FINDINGS, color=False)
        lines = output.splitlines()
        sep = lines[1]
        # separator is all dashes and spaces
        assert set(sep.strip()) == {"-", " "}

    def test_each_finding_has_a_row(self) -> None:
        output = format_text(SAMPLE_FINDINGS, color=False)
        lines = output.splitlines()
        # header + separator + 6 findings + blank + summary = 10
        data_lines = lines[2:-2]  # skip header, sep, blank, summary
        assert len(data_lines) == len(SAMPLE_FINDINGS)

    def test_file_line_format(self) -> None:
        output = format_text(SAMPLE_FINDINGS, color=False)
        assert "a.py:1" in output
        assert "b.py:5" in output
        assert "z.py:10" in output

    def test_empty_findings_shows_summary_only(self) -> None:
        output = format_text([], color=False)
        assert "0 findings" in output
        # No table header for empty results.
        assert "Severity" not in output


# -- test: sarif format produces valid JSON ----------------------------------

class TestSarifFormat:

    def test_sarif_stdout_is_valid_json(
        self, tmp_path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = security_main([
            "scan",
            "--format", "sarif",
            "--project-root", str(tmp_path),
        ])
        assert exit_code == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["version"] == "2.1.0"
        assert "$schema" in payload
        assert "runs" in payload

    def test_sarif_out_flag_implies_sarif(
        self, tmp_path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = security_main([
            "scan",
            "--sarif-out", "-",
            "--project-root", str(tmp_path),
        ])
        assert exit_code == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["version"] == "2.1.0"

    def test_sarif_file_output(self, tmp_path) -> None:
        out = tmp_path / "results.sarif"
        exit_code = security_main([
            "scan",
            "--format", "sarif",
            "--sarif-out", str(out),
            "--project-root", str(tmp_path),
        ])
        assert exit_code == 0
        payload = json.loads(out.read_text())
        assert payload["version"] == "2.1.0"


# -- test: summary line counts are correct -----------------------------------

class TestSummaryLine:

    def test_total_count_matches(self) -> None:
        summary = _build_summary(SAMPLE_FINDINGS)
        assert summary.startswith("6 findings")

    def test_per_severity_counts(self) -> None:
        summary = _build_summary(SAMPLE_FINDINGS)
        assert "1 critical" in summary
        assert "2 high" in summary
        assert "1 medium" in summary
        assert "1 low" in summary
        assert "1 info" in summary

    def test_empty_findings_summary(self) -> None:
        summary = _build_summary([])
        assert summary == "0 findings (none)"

    def test_single_severity_summary(self) -> None:
        findings = [
            _make_finding(id="H1", severity=Severity.HIGH),
            _make_finding(id="H2", severity=Severity.HIGH, file="x.py"),
        ]
        summary = _build_summary(findings)
        assert "2 findings" in summary
        assert "2 high" in summary
        # No other severities mentioned.
        assert "critical" not in summary
        assert "medium" not in summary

    def test_summary_appears_in_text_output(self) -> None:
        output = format_text(SAMPLE_FINDINGS, color=False)
        last_line = output.strip().splitlines()[-1]
        assert "6 findings" in last_line


# -- test: results sorted by severity ---------------------------------------

class TestSeveritySorting:

    def test_sort_order_critical_first(self) -> None:
        sorted_f = _sort_findings(SAMPLE_FINDINGS)
        severities = [f.severity for f in sorted_f]
        assert severities[0] == Severity.CRITICAL
        assert severities[-1] == Severity.INFO

    def test_sort_is_stable_within_same_severity(self) -> None:
        """Findings of the same severity keep their original relative order."""
        sorted_f = _sort_findings(SAMPLE_FINDINGS)
        high_ids = [f.id for f in sorted_f if f.severity == Severity.HIGH]
        assert high_ids == ["HIGH-1", "HIGH-2"]

    def test_text_output_rows_are_severity_ordered(self) -> None:
        output = format_text(SAMPLE_FINDINGS, color=False)
        lines = output.splitlines()
        data_lines = lines[2:-2]  # skip header, sep, blank, summary
        severities_in_output: list[str] = []
        for line in data_lines:
            sev = line.split()[0]  # first token is severity label
            severities_in_output.append(sev)
        expected = ["CRITICAL", "HIGH", "HIGH", "MEDIUM", "LOW", "INFO"]
        assert severities_in_output == expected


# -- test: ANSI color behavior -----------------------------------------------

class TestAnsiColor:

    def test_color_false_no_escape_codes(self) -> None:
        output = format_text(SAMPLE_FINDINGS, color=False)
        assert "\033[" not in output

    def test_color_true_includes_escape_codes(self) -> None:
        output = format_text(SAMPLE_FINDINGS, color=True)
        assert "\033[" in output
        assert "\033[0m" in output  # reset

    def test_color_true_critical_is_bold_red(self) -> None:
        output = format_text(SAMPLE_FINDINGS, color=True)
        assert "\033[1;31mCRITICAL\033[0m" in output


# -- test: CLI integration (text is now default) -----------------------------

class TestDefaultFormat:

    def test_default_scan_outputs_text_not_json(
        self, tmp_path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Without --format, scan should produce text output (not SARIF JSON)."""
        exit_code = security_main([
            "scan",
            "--project-root", str(tmp_path),
        ])
        assert exit_code == 0
        captured = capsys.readouterr()
        # Text format: should NOT be parseable as SARIF JSON with "version" key.
        # (For an empty scan, we get the summary line.)
        assert "findings" in captured.out.lower()
        # Should not be raw JSON:
        try:
            data = json.loads(captured.out)
            # If it parses, it should NOT be SARIF
            assert "version" not in data
        except json.JSONDecodeError:
            pass  # expected for text output

    def test_quiet_suppresses_text_output(
        self, tmp_path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        exit_code = security_main([
            "scan",
            "--quiet",
            "--project-root", str(tmp_path),
        ])
        assert exit_code == 0
        captured = capsys.readouterr()
        assert captured.out == ""
