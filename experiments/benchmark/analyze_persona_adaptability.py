#!/usr/bin/env python3
"""Summarize controlled persona adaptability results."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
from matplotlib.ticker import PercentFormatter
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PERSONA_DIR = PROJECT_ROOT / "energybridge" / "roleplay" / "personas"
METHOD_ORDER = ["EnergyBridge", "mpc_dynamic", "rule_milp", "rl_ppo_pref_v2"]
METHOD_LABEL = {
    "EnergyBridge": "EnergyBridge",
    "mpc_dynamic": "MPC",
    "rule_milp": "Rule+MILP",
    "rl_ppo_pref_v2": "RL",
}
PERSONA_LABEL = {
    "paper_adapt_a_price_cooperative": "A price",
    "paper_adapt_b_comfort_gated": "B comfort",
    "paper_adapt_c_irregular_cautious": "C cautious",
    "paper_adapt_d_ideal_dr": "D ideal DR",
    "paper_adapt_e_caregiver_low_dr": "E caregiver",
}


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _mean(values: list[float]) -> float | None:
    return float(np.mean(values)) if values else None


def _temperature_stats(result: dict[str, Any]) -> dict[str, float | None]:
    rows = result.get("daily_trace_rows") or []

    def values(predicate) -> list[float]:
        out: list[float] = []
        for row in rows:
            if not isinstance(row, dict) or not predicate(row):
                continue
            value = _float(row.get("indoor_temperature_c"))
            if value is not None:
                out.append(value)
        return out

    return {
        "occupied_avg_temp_c": _mean(values(lambda row: bool(row.get("occupied")))),
        "vpp_avg_temp_c": _mean(values(lambda row: bool(row.get("vpp_active")))),
        "occupied_vpp_avg_temp_c": _mean(
            values(lambda row: bool(row.get("occupied")) and bool(row.get("vpp_active")))
        ),
    }


def _persona_context(persona_id: str) -> dict[str, Any]:
    persona = _load_json(PERSONA_DIR / f"{persona_id}.json")
    weights = ((persona.get("preferences") or {}).get("scoring_weights") or {})
    ac = ((persona.get("appliances") or {}).get("ac") or {})
    tags = persona.get("tags") or {}
    return {
        "persona_label": PERSONA_LABEL.get(persona_id, persona_id),
        "comfort_weight": _float(weights.get("comfort"), 0.0),
        "energy_weight": _float(weights.get("energy"), 0.0),
        "vpp_weight": _float(weights.get("vpp"), 0.0),
        "preferred_min_c": _float(ac.get("setpoint_preferred_min_c")),
        "preferred_max_c": _float(ac.get("setpoint_preferred_max_c")),
        "tag_schedule": tags.get("schedule", ""),
        "tag_comfort": tags.get("comfort", ""),
        "tag_price": tags.get("price", ""),
        "tag_control": tags.get("control", ""),
    }


def _records(summary_json: Path) -> list[dict[str, Any]]:
    rows = json.loads(summary_json.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for row in rows:
        result_path = Path(str(row.get("output_dir") or "")) / "benchmark_result.json"
        result = _load_json(result_path)
        if not result:
            continue
        persona_id = str(row.get("persona_id") or result.get("persona_id") or "")
        method = str(row.get("method") or result.get("method") or "")
        ctx = _persona_context(persona_id)
        event = (result.get("vpp_event_log") or [{}])[0]
        temp_stats = _temperature_stats(result)
        price_metrics = result.get("day_ahead_price_metrics") or {}
        records.append(
            {
                "persona_id": persona_id,
                **ctx,
                "method": method,
                "method_label": METHOD_LABEL.get(method, method),
                "user_pref_score": _float(result.get("user_pref_score", row.get("user_pref_score"))),
                "acceptance_rate": _float(
                    result.get("vpp_plan_acceptance_rate", row.get("vpp_plan_acceptance_rate"))
                ),
                "acceptance_probability": _float(
                    result.get(
                        "vpp_plan_acceptance_probability_avg",
                        row.get("vpp_plan_acceptance_probability_avg"),
                    )
                ),
                "energy_kwh_per_day": _float(result.get("energy_kwh_per_day", row.get("energy_kwh_per_day"))),
                "daily_cost": _float(price_metrics.get("total_cost_eur"), _float(row.get("day_ahead_total_cost_eur"))),
                "vpp_window_energy_kwh": _float(result.get("vpp_window_energy_kwh", row.get("vpp_window_energy_kwh"))),
                "event_setpoint_c": _float(event.get("setpoint")),
                "agent_setpoint_c": _float(result.get("agent_setpoint_c")),
                "strategy_label": ((event.get("selected_strategy") or {}).get("label") or ""),
                "strategy_source": ((event.get("selected_strategy") or {}).get("source") or ""),
                "occupied_avg_temp_c": temp_stats["occupied_avg_temp_c"],
                "vpp_avg_temp_c": temp_stats["vpp_avg_temp_c"],
                "occupied_vpp_avg_temp_c": temp_stats["occupied_vpp_avg_temp_c"],
                "output_dir": str(row.get("output_dir") or ""),
            }
        )
    if not records:
        raise ValueError(f"No records found in {summary_json}")
    return records


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _df_to_markdown(df: pd.DataFrame, *, index: bool = True) -> str:
    frame = df.reset_index() if index else df.copy()
    columns = [str(col) for col in frame.columns]
    body = [["" if pd.isna(value) else str(value) for value in row] for row in frame.itertuples(index=False, name=None)]
    widths = [len(col) for col in columns]
    for row in body:
        for idx, cell in enumerate(row):
            widths[idx] = max(widths[idx], len(cell))
    header = "| " + " | ".join(columns[idx].ljust(widths[idx]) for idx in range(len(columns))) + " |"
    sep = "| " + " | ".join("-" * widths[idx] for idx in range(len(columns))) + " |"
    rows = [
        "| " + " | ".join(row[idx].ljust(widths[idx]) for idx in range(len(columns))) + " |"
        for row in body
    ]
    return "\n".join([header, sep, *rows])


def _pivot(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    order = [label for label in PERSONA_LABEL.values() if label in set(df["persona_label"])]
    columns = [METHOD_LABEL[m] for m in METHOD_ORDER if METHOD_LABEL[m] in set(df["method_label"])]
    pivot = df.pivot(index="persona_label", columns="method_label", values=metric)
    return pivot.reindex(index=order, columns=columns)


def _plot_core(df: pd.DataFrame, path: Path) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(18, 12), constrained_layout=True)
    configs = [
        ("user_pref_score", "User score", "User score / 5", False, (0, 5)),
        ("daily_cost", "Daily cost", "Daily electricity cost", True, None),
        ("vpp_window_energy_kwh", "VPP-window energy", "kWh during VPP window", True, None),
        ("acceptance_rate", "Realized acceptance", "Accepted VPP events", False, (0, 1)),
    ]
    for ax, (metric, title, ylabel, lower_better, ylim) in zip(axes.ravel(), configs):
        pivot = _pivot(df, metric)
        x = np.arange(len(pivot.index))
        width = 0.18
        for idx, col in enumerate(pivot.columns):
            values = pivot[col].to_numpy(dtype=float)
            ax.bar(x + (idx - 1.5) * width, values, width=width, label=col)
        ax.set_title(title, fontweight="bold")
        ax.set_ylabel(ylabel + (" (lower is better)" if lower_better else ""))
        ax.set_xticks(x)
        ax.set_xticklabels(pivot.index, rotation=15, ha="right")
        if ylim:
            ax.set_ylim(*ylim)
        if metric == "acceptance_rate":
            ax.yaxis.set_major_formatter(PercentFormatter(xmax=1.0, decimals=0))
        ax.legend(fontsize=9)
    fig.suptitle("Controlled Persona Adaptability: Same Appliances, Same Event", fontsize=18, fontweight="bold")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=220)
    plt.close(fig)


def _plot_eb_mechanism(df: pd.DataFrame, path: Path) -> None:
    eb = df[df["method"] == "EnergyBridge"].copy()
    eb = eb.sort_values("persona_label")
    x = np.arange(len(eb))
    labels = eb["persona_label"].tolist()

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(16, 11), constrained_layout=True)

    ax = axes[0, 0]
    ax.bar(x - 0.2, eb["comfort_weight"], width=0.2, label="Comfort weight", color="#90be6d")
    ax.bar(x, eb["energy_weight"], width=0.2, label="Energy weight", color="#f9c74f")
    ax.bar(x + 0.2, eb["vpp_weight"], width=0.2, label="VPP weight", color="#577590")
    ax.set_title("Persona Preference Weights", fontweight="bold")
    ax.set_ylim(0, 0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.legend()

    ax = axes[0, 1]
    ax.plot(x, eb["event_setpoint_c"], marker="o", label="EB VPP setpoint", color="#d00000")
    ax.plot(x, eb["preferred_max_c"], marker="s", label="Preferred max", color="#457b9d")
    ax.set_title("EB Temperature Adaptation", fontweight="bold")
    ax.set_ylabel("Setpoint (C)")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=15, ha="right")
    ax.legend()

    ax = axes[1, 0]
    ax.bar(x, eb["daily_cost"], color="#fb8500")
    ax.set_title("EB Cost Tradeoff by Persona", fontweight="bold")
    ax.set_ylabel("Daily cost")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=15, ha="right")

    ax = axes[1, 1]
    ax.bar(x, eb["vpp_window_energy_kwh"], color="#219ebc")
    ax.set_title("EB VPP Load by Persona", fontweight="bold")
    ax.set_ylabel("kWh during VPP window")
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=15, ha="right")
    for idx, label in enumerate(eb["strategy_label"].fillna("")):
        if label:
            ax.text(idx, eb["vpp_window_energy_kwh"].iloc[idx], str(label)[:18], ha="center", va="bottom", fontsize=8)

    fig.suptitle("EnergyBridge Learns Different Acceptable Strategies", fontsize=18, fontweight="bold")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=220)
    plt.close(fig)


def _write_markdown(df: pd.DataFrame, path: Path, *, source: Path) -> None:
    summary = df.groupby("method_label", sort=False)[
        ["user_pref_score", "daily_cost", "vpp_window_energy_kwh", "acceptance_rate"]
    ].mean().round(3)
    eb = df[df["method"] == "EnergyBridge"].copy().sort_values("persona_label")
    lines = [
        "# Controlled Persona Adaptability Summary",
        "",
        f"- Source summary: `{source}`",
        "- Controlled design: same non-AC appliance suite and same VPP event across all personas.",
        "",
        "## Method Means",
        "",
        _df_to_markdown(summary),
        "",
        "## EB Persona-Specific Strategy Signal",
        "",
        _df_to_markdown(
            eb[
                [
                    "persona_label",
                    "comfort_weight",
                    "energy_weight",
                    "vpp_weight",
                    "preferred_max_c",
                    "event_setpoint_c",
                    "daily_cost",
                    "vpp_window_energy_kwh",
                    "strategy_label",
                ]
            ].round(3),
            index=False,
        ),
        "",
        "Interpretation: comfort/caregiver profiles keep lower event setpoints and accept comfort-first explanations; price/grid-cooperative profiles allow warmer event setpoints and lower-cost operation. The baselines reuse much more similar appliance timing and receive lower or zero realized VPP acceptance.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze controlled persona adaptability results.")
    parser.add_argument("--summary-json", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()
    summary_json = Path(args.summary_json)
    out_dir = Path(args.output_dir)
    rows = _records(summary_json)
    df = pd.DataFrame(rows)
    csv_path = out_dir / "persona_adaptability_records.csv"
    core_path = out_dir / "persona_adaptability_core.png"
    eb_path = out_dir / "energybridge_adaptation_mechanism.png"
    md_path = out_dir / "persona_adaptability_summary.md"
    _write_csv(rows, csv_path)
    _plot_core(df, core_path)
    _plot_eb_mechanism(df, eb_path)
    _write_markdown(df, md_path, source=summary_json)
    print(f"[OK] csv : {csv_path}")
    print(f"[OK] core: {core_path}")
    print(f"[OK] eb  : {eb_path}")
    print(f"[OK] md  : {md_path}")


if __name__ == "__main__":
    main()
