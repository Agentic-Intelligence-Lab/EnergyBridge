#!/usr/bin/env python3
"""Generate a compact report for a baseline matrix run.

This reads the matrix summary JSON produced by run_baseline_matrix.py, then
loads each job's benchmark_result.json for richer metrics. It writes:

- a markdown summary table
- a CSV summary table
- a compact PNG report with grouped bar charts and a score heatmap

The goal is to make a 30-job matrix easy to scan in one glance.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

_BENCH_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
DEFAULT_RESULTS_ROOT = _PROJECT_ROOT / "benchmark_results"

METHOD_ORDER = ["agent", "mpc_dynamic", "mpc_ep"]
METHOD_LABEL = {
    "agent": "Agent",
    "mpc_dynamic": "MPC Dynamic",
    "mpc_ep": "MPC EP",
}
def _latest_date_dir(results_root: Path) -> Path:
    date_dirs = [p for p in results_root.iterdir() if p.is_dir() and p.name[:4].isdigit()]
    if not date_dirs:
        raise FileNotFoundError(f"No dated result directories found under {results_root}")
    return sorted(date_dirs, key=lambda p: p.name)[-1]


def _find_summary_json(results_root: Path, date_dir: Path | None, city: str, horizon: int) -> Path:
    root = date_dir or _latest_date_dir(results_root)
    candidate = root / "_batch_logs" / f"baseline_matrix_summary_{city.lower()}_H{horizon}.json"
    if not candidate.exists():
        raise FileNotFoundError(f"Summary JSON not found: {candidate}")
    return candidate


def _load_matrix_rows(summary_json: Path) -> list[dict[str, Any]]:
    rows = json.loads(summary_json.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"Unexpected matrix summary format in {summary_json}")
    return rows


def _read_result_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _fmt_cell(value: Any) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, float):
        return f"{value:.4f}".rstrip("0").rstrip(".")
    return str(value)


def _df_to_markdown(df: pd.DataFrame, index: bool = True) -> str:
    frame = df.copy()
    if index:
        frame = frame.reset_index()
    columns = list(frame.columns)
    rows = [[_fmt_cell(v) for v in row] for row in frame.itertuples(index=False, name=None)]
    widths = [len(str(col)) for col in columns]
    for row in rows:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))
    header = "| " + " | ".join(str(col).ljust(widths[i]) for i, col in enumerate(columns)) + " |"
    sep = "| " + " | ".join("-" * widths[i] for i in range(len(columns))) + " |"
    body = [
        "| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(columns))) + " |"
        for row in rows
    ]
    return "\n".join([header, sep, *body])


def _build_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for row in rows:
        result_path = Path(row["output_dir"]) / "benchmark_result.json"
        result = _read_result_json(result_path)
        if not result:
            continue
        records.append(
            {
                "persona_id": row["persona_id"],
                "method": row["method"],
                "method_label": METHOD_LABEL.get(row["method"], row["method"]),
                "status": row.get("status", ""),
                "user_pref_score": _as_float(result.get("user_pref_score")),
                "energy_kwh_total": _as_float(result.get("energy_kwh_total")),
                "vpp_window_energy_kwh": _as_float(result.get("vpp_window_energy_kwh")),
                "vpp_demand_achievement_ratio": _as_float(result.get("vpp_demand_achievement_ratio")),
                "appliance_vpp_avoidance_rate": _as_float(result.get("appliance_vpp_avoidance_rate")),
                "appliance_task_completion_rate": _as_float(result.get("appliance_task_completion_rate")),
                "vpp_compliance_rate": _as_float(result.get("vpp_compliance_rate")),
                "llm_call_count": _as_float(result.get("llm_call_count")),
                "elapsed_s": _as_float(row.get("elapsed_s")),
                "output_dir": row.get("output_dir", ""),
            }
        )
    df = pd.DataFrame.from_records(records)
    if df.empty:
        raise ValueError("No usable benchmark_result.json files found for the matrix.")
    df["method"] = pd.Categorical(df["method"], categories=METHOD_ORDER, ordered=True)
    return df.sort_values(["persona_id", "method"]).reset_index(drop=True)


def _write_markdown(df: pd.DataFrame, report_dir: Path, summary_json: Path) -> Path:
    md_path = report_dir / "baseline_matrix_report.md"
    summary = (
        df.groupby("method", observed=True)[
            ["user_pref_score", "energy_kwh_total", "vpp_window_energy_kwh", "vpp_demand_achievement_ratio"]
        ]
        .mean()
        .round(4)
    )
    summary.index = summary.index.map(lambda m: METHOD_LABEL.get(m, m))
    pivot = df.pivot(index="persona_id", columns="method", values="user_pref_score").round(3)
    pivot.columns = [METHOD_LABEL.get(c, c) for c in pivot.columns]

    top_rows = df.sort_values(["user_pref_score", "energy_kwh_total"], ascending=[False, True]).head(10)

    lines: list[str] = []
    lines.append("# EnergyBridge Baseline Matrix Report")
    lines.append("")
    lines.append(f"- Source: `{summary_json}`")
    lines.append(f"- Jobs: `{len(df)}`")
    lines.append("")
    lines.append("## Method Averages")
    lines.append("")
    lines.append(_df_to_markdown(summary))
    lines.append("")
    lines.append("## Persona by Method Score Matrix")
    lines.append("")
    lines.append(_df_to_markdown(pivot))
    lines.append("")
    lines.append("## Top 10 Runs by User Score")
    lines.append("")
    lines.append(
        _df_to_markdown(
            top_rows[
                [
                    "persona_id",
                    "method_label",
                    "user_pref_score",
                    "energy_kwh_total",
                    "vpp_window_energy_kwh",
                    "vpp_demand_achievement_ratio",
                    "appliance_vpp_avoidance_rate",
                    "appliance_task_completion_rate",
                    "elapsed_s",
                ]
            ],
            index=False,
        )
    )
    lines.append("")
    lines.append("## Quick Read")
    lines.append("")
    best = summary["user_pref_score"].idxmax()
    lowest_energy = summary["energy_kwh_total"].idxmin()
    best_vpp = summary["vpp_demand_achievement_ratio"].idxmax()
    lines.append(f"- Best average user score: **{best}**")
    lines.append(f"- Lowest average total energy: **{lowest_energy}**")
    lines.append(f"- Best average VPP achievement ratio: **{best_vpp}**")
    lines.append("")
    lines.append("The matrix is calendar-aware and uses the 3-day benchmark window (Day 1 to Day 3).")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def _plot_report(df: pd.DataFrame, report_dir: Path) -> Path:
    plt.style.use("seaborn-v0_8-whitegrid")
    fig = plt.figure(figsize=(20, 12), constrained_layout=True)
    gs = fig.add_gridspec(2, 2, height_ratios=[1, 1.1])

    axes = {
        "score": fig.add_subplot(gs[0, 0]),
        "energy": fig.add_subplot(gs[0, 1]),
        "vpp": fig.add_subplot(gs[1, 0]),
        "heatmap": fig.add_subplot(gs[1, 1]),
    }

    method_means = (
        df.groupby("method", observed=True)[
            ["user_pref_score", "energy_kwh_total", "vpp_window_energy_kwh", "vpp_demand_achievement_ratio"]
        ]
        .mean()
        .reindex(METHOD_ORDER)
    )
    method_means.index = [METHOD_LABEL[m] for m in method_means.index]

    method_means["user_pref_score"].plot(kind="bar", ax=axes["score"], color=["#244c5a", "#4f6d7a", "#7ea0b7"])
    axes["score"].set_title("Average User Score")
    axes["score"].set_xlabel("")
    axes["score"].set_ylabel("Score / 5")
    axes["score"].set_ylim(0, max(5, method_means["user_pref_score"].max() + 0.5))
    axes["score"].tick_params(axis="x", rotation=0)

    method_means["energy_kwh_total"].plot(kind="bar", ax=axes["energy"], color=["#7aa36b", "#90be6d", "#b7e4c7"])
    axes["energy"].set_title("Average Total Energy")
    axes["energy"].set_xlabel("")
    axes["energy"].set_ylabel("kWh")
    axes["energy"].tick_params(axis="x", rotation=0)

    vpp_bars = method_means["vpp_demand_achievement_ratio"].plot(
        kind="bar", ax=axes["vpp"], color=["#c97a40", "#e9a15d", "#f4c27a"]
    )
    axes["vpp"].axhline(1.0, color="#444444", linestyle="--", linewidth=1)
    axes["vpp"].set_title("Average VPP Achievement Ratio")
    axes["vpp"].set_xlabel("")
    axes["vpp"].set_ylabel("Actual / Target")
    axes["vpp"].tick_params(axis="x", rotation=0)

    pivot = (
        df.pivot(index="persona_id", columns="method", values="user_pref_score")
        .reindex(columns=METHOD_ORDER)
    )
    pivot.columns = [METHOD_LABEL[c] for c in pivot.columns]
    data = pivot.to_numpy(dtype=float)
    im = axes["heatmap"].imshow(data, cmap="YlGnBu", aspect="auto", vmin=0, vmax=5)
    axes["heatmap"].set_xticks(np.arange(len(pivot.columns)))
    axes["heatmap"].set_xticklabels(list(pivot.columns), rotation=0)
    axes["heatmap"].set_yticks(np.arange(len(pivot.index)))
    axes["heatmap"].set_yticklabels(list(pivot.index))
    axes["heatmap"].set_title("Persona vs Method User Score")
    axes["heatmap"].set_xlabel("")
    axes["heatmap"].set_ylabel("")
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            axes["heatmap"].text(j, i, f"{data[i, j]:.2f}", ha="center", va="center", color="#1f1f1f", fontsize=9)
    fig.colorbar(im, ax=axes["heatmap"], label="User score / 5")

    fig.suptitle("EnergyBridge Baseline Matrix Report", fontsize=24, fontweight="bold")
    png_path = report_dir / "baseline_matrix_report.png"
    fig.savefig(png_path, dpi=220)
    plt.close(fig)
    return png_path


def _write_csv(df: pd.DataFrame, report_dir: Path) -> Path:
    csv_path = report_dir / "baseline_matrix_report_table.csv"
    df.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return csv_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate charts/tables for the latest baseline matrix.")
    parser.add_argument(
        "--results-root",
        default=str(DEFAULT_RESULTS_ROOT),
        help="benchmark_results root directory.",
    )
    parser.add_argument("--date", default="", help="Optional date folder (YYYY-MM-DD).")
    parser.add_argument(
        "--city",
        default="Tianjin",
        choices=["Tianjin", "Beijing", "Shanghai"],
        help="City name used in the matrix summary file.",
    )
    parser.add_argument(
        "--horizon",
        type=int,
        default=6,
        help="MPC horizon used in the matrix summary file.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    results_root = Path(args.results_root)
    date_dir = None
    if args.date:
        candidate = Path(args.date)
        if not candidate.is_dir():
            candidate = results_root / args.date
        if not candidate.is_dir():
            raise FileNotFoundError(f"Date directory not found: {args.date}")
        date_dir = candidate
    summary_json = _find_summary_json(results_root, date_dir, args.city, args.horizon)
    summary_root = summary_json.parent
    report_dir = summary_root / "baseline_matrix_report"
    report_dir.mkdir(parents=True, exist_ok=True)

    rows = _load_matrix_rows(summary_json)
    df = _build_dataframe(rows)
    df = df.copy()
    df["method_label"] = df["method"].map(METHOD_LABEL)

    md_path = _write_markdown(df, report_dir, summary_json)
    csv_path = _write_csv(df, report_dir)
    png_path = _plot_report(df, report_dir)

    print(f"[OK] markdown: {md_path}")
    print(f"[OK] csv     : {csv_path}")
    print(f"[OK] figure  : {png_path}")


if __name__ == "__main__":
    main()
