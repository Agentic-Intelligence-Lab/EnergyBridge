from pathlib import Path

from experiments.benchmark.run_capacity_consent_joined_replay import (
    METHODS,
    _write_report,
    summarize,
)


def test_capacity_report_exposes_only_three_paper_metrics(
    tmp_path: Path,
) -> None:
    rows = []
    for method in METHODS:
        rows.extend(
            [
                {
                    "method": method,
                    "accepted": True,
                    "within_20pct": True,
                },
                {
                    "method": method,
                    "accepted": False,
                    "within_20pct": False,
                },
            ]
        )

    summary = summarize(rows, apply_is=False)
    report = tmp_path / "README.md"
    _write_report(report, summary, closed_loop=True)
    text = report.read_text(encoding="utf-8")

    assert "Accepted-only accuracy" in text
    assert "Acceptance rate" in text
    assert "Overall accurate coverage" in text
    assert "All-event pass" not in text
    assert "Accepted n / N" not in text
    for row in summary:
        assert row["joint_accurate_coverage"] == (
            row["acceptance_rate"] * row["accepted_only_pass_rate"]
        )
