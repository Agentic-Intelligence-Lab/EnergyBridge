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
import matplotlib.colors as mcolors
import numpy as np
import pandas as pd

_BENCH_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
DEFAULT_RESULTS_ROOT = _PROJECT_ROOT / "benchmark_results"

ENERGYBRIDGE_METHOD_ID = "EnergyBridge"
METHOD_ORDER = [ENERGYBRIDGE_METHOD_ID, "mpc_dynamic", "mpc_ep", "rl_ppo_3day", "rl_ppo_pref_v2"]
METHOD_LABEL = {
    ENERGYBRIDGE_METHOD_ID: "EnergyBridge",
    "agent": "EnergyBridge",
    "mpc_dynamic": "MPC Dynamic",
    "mpc_ep": "MPC EP",
    "rl_ppo_3day": "RL PPO",
    "rl_ppo_pref_v2": "RL PPO Pref-v2",
}


def _canonical_method(method: str) -> str:
    key = str(method or ENERGYBRIDGE_METHOD_ID).strip().lower()
    aliases = {
        "agent": ENERGYBRIDGE_METHOD_ID,
        "energybridge": ENERGYBRIDGE_METHOD_ID,
        "mpc": "mpc_dynamic",
        "rl": "rl_ppo_3day",
        "rl_ppo": "rl_ppo_3day",
        "rl_ppo_3day": "rl_ppo_3day",
        "rl_ppo_pref_v2": "rl_ppo_pref_v2",
        "rl_pref_v2": "rl_ppo_pref_v2",
    }
    return aliases.get(key, key)
def _latest_date_dir(results_root: Path) -> Path:
    date_dirs = [p for p in results_root.iterdir() if p.is_dir() and p.name[:4].isdigit()]
    if not date_dirs:
        raise FileNotFoundError(f"No dated result directories found under {results_root}")
    return sorted(date_dirs, key=lambda p: p.name)[-1]


def _find_summary_json(
    results_root: Path,
    date_dir: Path | None,
    city: str,
    horizon: int,
    days: int | None,
) -> Path:
    root = date_dir or _latest_date_dir(results_root)
    batch_dir = root / "_batch_logs"
    candidates: list[Path] = []
    if days is not None:
        candidates.append(batch_dir / f"baseline_matrix_summary_{city.lower()}_{days}days_H{horizon}.json")
    candidates.append(batch_dir / f"baseline_matrix_summary_{city.lower()}_H{horizon}.json")
    candidates.extend(sorted(batch_dir.glob(f"baseline_matrix_summary_{city.lower()}_*days_H{horizon}.json")))
    for candidate in candidates:
        if candidate.exists():
            return candidate
    checked = "\n  ".join(str(path) for path in candidates)
    raise FileNotFoundError(f"Summary JSON not found. Checked:\n  {checked}")


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
        method = _canonical_method(row["method"])
        records.append(
            {
                "persona_id": row["persona_id"],
                "method": method,
                "method_label": METHOD_LABEL.get(method, method),
                "status": row.get("status", ""),
                "user_pref_score": _as_float(result.get("user_pref_score")),
                "energy_kwh_total": _as_float(result.get("energy_kwh_total")),
                "vpp_window_energy_kwh": _as_float(result.get("vpp_window_energy_kwh")),
                "appliance_vpp_avoidance_rate": _as_float(result.get("appliance_vpp_avoidance_rate")),
                "appliance_task_completion_rate": _as_float(result.get("appliance_task_completion_rate")),
                "appliance_shift_success_rate": _as_float(result.get("appliance_shift_success_rate")),
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


def _artifact_name(prefix: str, filename: str) -> str:
    return f"{prefix}_{filename}" if prefix else filename


def _write_markdown(df: pd.DataFrame, report_dir: Path, summary_json: Path, prefix: str = "") -> Path:
    md_path = report_dir / _artifact_name(prefix, "baseline_matrix_report.md")
    summary = (
        df.groupby("method", observed=True)[
            ["user_pref_score", "energy_kwh_total", "vpp_window_energy_kwh", "appliance_shift_success_rate"]
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
                    "appliance_shift_success_rate",
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
    best_shift = summary["appliance_shift_success_rate"].idxmax()
    lines.append(f"- Best average user score: **{best}**")
    lines.append(f"- Lowest average total energy: **{lowest_energy}**")
    lines.append(f"- Best average appliance shift success rate: **{best_shift}**")
    lines.append("")
    lines.append("The matrix is calendar-aware and uses the 3-day benchmark window (Day 1 to Day 3).")
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return md_path


def _plot_report(df: pd.DataFrame, report_dir: Path, prefix: str = "") -> Path:
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes_arr = plt.subplots(2, 2, figsize=(22, 15), constrained_layout=True)
    axes = {
        "score": axes_arr[0, 0],
        "energy": axes_arr[0, 1],
        "vpp_energy": axes_arr[1, 0],
        "shift": axes_arr[1, 1],
    }

    def _metric_matrix(metric: str) -> pd.DataFrame:
        pivot = (
            df.pivot(index="persona_id", columns="method", values=metric)
            .reindex(columns=METHOD_ORDER)
        )
        pivot.columns = [METHOD_LABEL[c] for c in pivot.columns]
        return pivot

    def _soft_cmap(cmap_name: str) -> mcolors.Colormap:
        base = plt.get_cmap(cmap_name)
        # Avoid the darkest end of the palette so cell labels stay readable in reports.
        colors = base(np.linspace(0.20, 0.72, 256))
        return mcolors.LinearSegmentedColormap.from_list(f"{cmap_name}_soft", colors)

    def _label_color(im: Any, value: float) -> str:
        if np.isnan(value):
            return "#6b7280"
        rgba = im.cmap(im.norm(value))
        luminance = 0.2126 * rgba[0] + 0.7152 * rgba[1] + 0.0722 * rgba[2]
        return "#ffffff" if luminance < 0.48 else "#17202a"

    def _draw_metric_table(
        ax,
        pivot: pd.DataFrame,
        *,
        title: str,
        cmap: str,
        fmt: str,
        cbar_label: str,
        vmin: float | None = None,
        vmax: float | None = None,
        lower_is_better: bool = False,
        suffix: str = "",
    ) -> None:
        data = pivot.to_numpy(dtype=float)
        cmap_name = f"{cmap}_r" if lower_is_better else cmap
        im = ax.imshow(data, cmap=_soft_cmap(cmap_name), aspect="auto", vmin=vmin, vmax=vmax)
        ax.set_title(title, fontsize=14, fontweight="bold")
        ax.set_xticks(np.arange(len(pivot.columns)))
        ax.set_xticklabels(list(pivot.columns), rotation=0)
        ax.set_yticks(np.arange(len(pivot.index)))
        ax.set_yticklabels(list(pivot.index), fontsize=9)
        ax.set_xlabel("")
        ax.set_ylabel("")
        ax.set_xticks(np.arange(-0.5, len(pivot.columns), 1), minor=True)
        ax.set_yticks(np.arange(-0.5, len(pivot.index), 1), minor=True)
        ax.grid(which="minor", color="white", linewidth=1.5)
        ax.tick_params(which="minor", bottom=False, left=False)
        for i in range(data.shape[0]):
            for j in range(data.shape[1]):
                value = data[i, j]
                if np.isnan(value):
                    label = "N/A"
                else:
                    label = f"{value:{fmt}}{suffix}"
                ax.text(
                    j,
                    i,
                    label,
                    ha="center",
                    va="center",
                    color=_label_color(im, value),
                    fontsize=9,
                    fontweight="bold",
                )
        cbar = fig.colorbar(im, ax=ax, fraction=0.046, pad=0.02)
        cbar.set_label(cbar_label)

    score_matrix = _metric_matrix("user_pref_score")
    energy_matrix = _metric_matrix("energy_kwh_total")
    vpp_energy_matrix = _metric_matrix("vpp_window_energy_kwh")
    shift_matrix = _metric_matrix("appliance_shift_success_rate")

    _draw_metric_table(
        axes["score"],
        score_matrix,
        title="Persona x Method User Score",
        cmap="YlGnBu",
        fmt=".2f",
        cbar_label="User score / 5",
        vmin=0,
        vmax=5,
    )
    _draw_metric_table(
        axes["energy"],
        energy_matrix,
        title="Persona x Method Total Energy",
        cmap="YlOrBr",
        fmt=".1f",
        cbar_label="Total energy kWh (lower is better)",
        lower_is_better=True,
        suffix="",
    )
    _draw_metric_table(
        axes["vpp_energy"],
        vpp_energy_matrix,
        title="Persona x Method VPP Window Energy",
        cmap="Oranges",
        fmt=".2f",
        cbar_label="VPP-window kWh (lower is better)",
        lower_is_better=True,
    )
    _draw_metric_table(
        axes["shift"],
        shift_matrix,
        title="Persona x Method Appliance Shift Success",
        cmap="PuBuGn",
        fmt=".0%",
        cbar_label="Completed tasks shifted away from VPP",
        vmin=0,
        vmax=1,
    )

    fig.suptitle("EnergyBridge Baseline Matrix Report", fontsize=24, fontweight="bold")
    png_path = report_dir / _artifact_name(prefix, "baseline_matrix_report.png")
    fig.savefig(png_path, dpi=220)
    plt.close(fig)
    return png_path


def _write_csv(df: pd.DataFrame, report_dir: Path, prefix: str = "") -> Path:
    csv_path = report_dir / _artifact_name(prefix, "baseline_matrix_report_table.csv")
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
        choices=["Tianjin", "Beijing", "Shanghai", "Germany"],
        help="City name used in the matrix summary file.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Optional simulation length used in the matrix summary suffix, e.g. 7 for *_7days_H6.",
    )
    parser.add_argument(
        "--summary-json",
        default="",
        help="Optional explicit matrix summary JSON path. Overrides --date/--city/--days discovery.",
    )
    parser.add_argument(
        "--output-dir",
        default="",
        help="Optional report output directory. Defaults to <summary-dir>/baseline_matrix_report.",
    )
    parser.add_argument(
        "--artifact-prefix",
        default="",
        help="Optional prefix for generated report artifact filenames, e.g. 3day_tianjin.",
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
    summary_json = (
        Path(args.summary_json)
        if args.summary_json
        else _find_summary_json(results_root, date_dir, args.city, args.horizon, args.days)
    )
    if not summary_json.exists():
        raise FileNotFoundError(f"Summary JSON not found: {summary_json}")
    summary_root = summary_json.parent
    report_dir = Path(args.output_dir) if args.output_dir else summary_root / "baseline_matrix_report"
    report_dir.mkdir(parents=True, exist_ok=True)

    rows = _load_matrix_rows(summary_json)
    df = _build_dataframe(rows)
    df = df.copy()
    method_text = df["method"].astype(str)
    df["method_label"] = method_text.map(METHOD_LABEL).fillna(method_text)

    md_path = _write_markdown(df, report_dir, summary_json, args.artifact_prefix)
    csv_path = _write_csv(df, report_dir, args.artifact_prefix)
    png_path = _plot_report(df, report_dir, args.artifact_prefix)

    print(f"[OK] markdown: {md_path}")
    print(f"[OK] csv     : {csv_path}")
    print(f"[OK] figure  : {png_path}")


if __name__ == "__main__":
    main()
