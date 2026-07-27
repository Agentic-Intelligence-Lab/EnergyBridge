#!/usr/bin/env python3
"""Analyze strategy adaptability with acceptance/fallback disabled."""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PERSONA_DIR = PROJECT_ROOT / "energybridge" / "roleplay" / "personas"

METHOD_ORDER = ["EnergyBridge", "hema_agent", "mpc_dynamic", "rule_milp", "rl_ppo_pref_v2"]
METHOD_LABEL = {
    "EnergyBridge": "EnergyBridge",
    "agent": "EnergyBridge",
    "mpc_dynamic": "MPC",
    "rule_milp": "Rule+MILP",
    "rl_ppo_pref_v2": "RL",
    "hema_agent": "HEMA agent",
}
METHOD_COLOR = {
    "EnergyBridge": "#1f77b4",
    "MPC": "#ff7f0e",
    "Rule+MILP": "#2ca02c",
    "RL": "#d62728",
    "HEMA agent": "#9467bd",
}
PERSONA_LABEL = {
    "paper_adapt_a_price_cooperative": "Price-sensitive",
    "paper_adapt_b_comfort_gated": "Comfort-sensitive",
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


def _method_label(method: str) -> str:
    return METHOD_LABEL.get(str(method), str(method))


def _time_distance_h(a: float, b: float) -> float:
    delta = abs((float(a) % 24.0) - (float(b) % 24.0))
    return min(delta, 24.0 - delta)


def _interval_overlaps(start: float, end: float, win_start: float, win_end: float) -> bool:
    if end < start:
        return _interval_overlaps(start, 24.0, win_start, win_end) or _interval_overlaps(0.0, end, win_start, win_end)
    if win_end < win_start:
        return _interval_overlaps(start, end, win_start, 24.0) or _interval_overlaps(start, end, 0.0, win_end)
    return max(start, win_start) < min(end, win_end)


def _actions_from_result(result: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    if isinstance(event.get("vpp_trigger_actions"), dict):
        candidates.append(event.get("vpp_trigger_actions") or {})
    for decision in event.get("day_decisions") or []:
        if not isinstance(decision, dict):
            continue
        for key in ("actions", "raw_appliance_actions"):
            actions = decision.get(key)
            if isinstance(actions, dict):
                candidates.append(actions)
    for day in result.get("daily_trace_rows") or []:
        if isinstance(day, dict) and isinstance(day.get("actions"), dict):
            candidates.append(day.get("actions") or {})
    if not candidates:
        return {}

    def score(actions: dict[str, Any]) -> int:
        return sum(1 for value in actions.values() if value not in ("", None))

    best = max(candidates, key=score)
    merged = dict(best)
    trigger_actions = event.get("vpp_trigger_actions") or {}
    if isinstance(trigger_actions, dict):
        merged.update({k: v for k, v in trigger_actions.items() if v not in ("", None)})
    return merged


def _routine_fit_score(
    *,
    persona: dict[str, Any],
    result: dict[str, Any],
    event: dict[str, Any],
) -> tuple[float, str]:
    appliances = persona.get("appliances") or {}
    schedule = persona.get("schedule") or {}
    tags = persona.get("tags") or {}
    weights = ((persona.get("preferences") or {}).get("scoring_weights") or {})
    actions = _actions_from_result(result, event)
    event_start = _float(event.get("trigger_h"), 18.0) or 18.0
    event_end = _float(event.get("end_h"), 19.0) or 19.0
    event_start_hod = event_start % 24.0
    event_end_hod = event_end % 24.0
    comfort_w = _float(weights.get("comfort"), 0.0) or 0.0
    price_user = tags.get("price") in {"price_sensitive", "price_driven"} and comfort_w < 0.5
    occupied_pattern = str(schedule.get("occupancy_pattern", ""))
    returns_home = _float(schedule.get("returns_home_h"))
    home_sensitive = occupied_pattern == "stay_at_home" or (
        returns_home is not None
        and (event_start_hod - 0.5) <= (returns_home % 24.0) <= (event_end_hod + 0.5)
    )

    # This metric intentionally excludes HVAC setpoint quality because the
    # no-gate report plots VPP setpoint as its own panel.  Schedule fit focuses
    # on appliance timing, service completion, VPP-window conflicts, and hot
    # water reasonableness.
    factors = [f"home_sensitive={int(home_sensitive)}", f"price_user={int(price_user)}"]
    scored: list[tuple[float, float]] = []

    for name in ("washer", "dishwasher", "dryer"):
        cfg = appliances.get(name) or {}
        if not cfg.get("present"):
            continue
        if actions.get(f"{name}_skip") is True:
            service_score = 0.0
        else:
            start = _float(actions.get(f"{name}_start_h"))
            if start is None:
                service_score = 0.25
            else:
                duration = _float(cfg.get("duration_h"), 1.0) or 1.0
                earliest = _float(cfg.get("earliest_h"), 0.0) or 0.0
                latest = _float(cfg.get("latest_h"), 24.0) or 24.0
                preferred = _float(cfg.get("preferred_h"), earliest) or earliest
                in_window = earliest <= start <= latest and start + duration <= latest + 0.25
                preference = max(0.0, 1.0 - _time_distance_h(start, preferred) / 4.0)
                avoids_event = not _interval_overlaps(start, start + duration, event_start_hod, event_end_hod)
                service_score = (
                    0.60 * (1.0 if in_window else 0.35)
                    + 0.25 * (1.0 if avoids_event else 0.35)
                    + 0.15 * preference
                )
        scored.append((0.20, max(0.0, min(1.0, service_score))))
        factors.append(f"{name}={service_score:.2f}")

    wh = appliances.get("water_heater") or {}
    if wh.get("present"):
        if actions.get("water_heater_preheat") is False:
            wh_score = 0.15
        else:
            start = _float(actions.get("water_heater_preheat_start_h"))
            end = _float(actions.get("water_heater_preheat_end_h"))
            bath = _float(wh.get("bath_required_h"), schedule.get("bath_shower_h", 21.0)) or 21.0
            if start is None or end is None:
                wh_score = 0.30
            else:
                if end <= start:
                    end = min(24.0, start + 2.0)
                deadline = 1.0 if end <= bath else max(0.0, 1.0 - (end - bath) / 2.0)
                window_start = _float(wh.get("pre_heat_window_start_h"), 14.0) or 14.0
                window_end = _float(wh.get("pre_heat_window_end_h"), bath) or bath
                in_window = window_start <= start and end <= max(window_end, bath)
                avoids_event = not _interval_overlaps(start, end, event_start_hod, event_end_hod)
                temp_c = _float(actions.get("water_heater_preheat_temp_c"), _float(wh.get("normal_temp_c"), 62.0)) or 62.0
                if temp_c <= 65.0:
                    temp_quality = 1.0
                elif temp_c >= 72.0:
                    temp_quality = 0.0
                else:
                    temp_quality = max(0.0, 1.0 - (temp_c - 65.0) / 7.0)
                wh_score = (
                    0.50 * deadline
                    + 0.15 * (1.0 if avoids_event else 0.35)
                    + 0.30 * temp_quality
                    + 0.05 * (1.0 if in_window else 0.45)
                )
        scored.append((0.20, max(0.0, min(1.0, wh_score))))
        factors.append(f"hot_water={wh_score:.2f}")

    total_weight = sum(weight for weight, _ in scored) or 1.0
    score = sum(weight * value for weight, value in scored) / total_weight
    return round(float(max(0.0, min(1.0, score))), 6), "; ".join(factors)


def _records(summary_json: Path, personas: set[str]) -> list[dict[str, Any]]:
    rows = json.loads(summary_json.read_text(encoding="utf-8"))
    records: list[dict[str, Any]] = []
    for row in rows:
        persona_id = str(row.get("persona_id") or "")
        if personas and persona_id not in personas:
            continue
        method = str(row.get("method") or "")
        if method not in METHOD_ORDER:
            continue
        result_path = Path(str(row.get("output_dir") or "")) / "benchmark_result.json"
        result = _load_json(result_path)
        persona = _load_json(PERSONA_DIR / f"{persona_id}.json")
        event = (result.get("vpp_event_log") or [{}])[0]
        price_metrics = result.get("day_ahead_price_metrics") or {}
        routine_fit, routine_factors = _routine_fit_score(persona=persona, result=result, event=event)
        records.append(
            {
                "persona_id": persona_id,
                "persona_label": PERSONA_LABEL.get(persona_id, persona_id),
                "method": method,
                "method_label": _method_label(method),
                "user_pref_score": _float(result.get("user_pref_score"), _float(row.get("user_pref_score"))),
                "daily_cost": _float(price_metrics.get("total_cost_eur"), _float(row.get("day_ahead_total_cost_eur"))),
                "event_setpoint_c": _float(event.get("setpoint")),
                "routine_fit_score": routine_fit,
                "routine_fit_factors": routine_factors,
                "vpp_window_energy_kwh": _float(result.get("vpp_window_energy_kwh"), _float(row.get("vpp_window_energy_kwh"))),
                "energy_kwh_per_day": _float(result.get("energy_kwh_per_day"), _float(row.get("energy_kwh_per_day"))),
                "acceptance_rate": _float(result.get("vpp_plan_acceptance_rate"), _float(row.get("vpp_plan_acceptance_rate"))),
                "strategy_label": ((event.get("selected_strategy") or {}).get("label") or ""),
                "output_dir": str(row.get("output_dir") or ""),
            }
        )
    if not records:
        raise ValueError(f"No matching records in {summary_json}")
    return records


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)


def _pivot(df: pd.DataFrame, metric: str) -> pd.DataFrame:
    personas = [label for label in PERSONA_LABEL.values() if label in set(df["persona_label"])]
    methods = [_method_label(method) for method in METHOD_ORDER if _method_label(method) in set(df["method_label"])]
    return df.pivot(index="persona_label", columns="method_label", values=metric).reindex(index=personas, columns=methods)


def _plot_metric(df: pd.DataFrame, metric: str, title: str, ylabel: str, path: Path, *, ylim: tuple[float, float] | None = None) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, ax = plt.subplots(figsize=(9.5, 5.8), constrained_layout=True)
    _draw_metric(ax, df, metric, title, ylabel, ylim=ylim)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=220)
    plt.close(fig)


def _draw_metric(ax: Any, df: pd.DataFrame, metric: str, title: str, ylabel: str, *, ylim: tuple[float, float] | None = None) -> None:
    pivot = _pivot(df, metric)
    x = np.arange(len(pivot.index))
    width = 0.18
    for idx, col in enumerate(pivot.columns):
        values = pivot[col].to_numpy(dtype=float)
        ax.bar(
            x + (idx - (len(pivot.columns) - 1) / 2) * width,
            values,
            width=width,
            label=col,
            color=METHOD_COLOR.get(col),
        )
    ax.set_title(title, fontweight="bold")
    ax.set_ylabel(ylabel)
    ax.set_xticks(x)
    ax.set_xticklabels(pivot.index, rotation=0)
    if ylim:
        ax.set_ylim(*ylim)
    ax.legend(fontsize=9)
    for container in ax.containers:
        ax.bar_label(container, fmt="%.2f", fontsize=8, padding=2)


def _plot_panel(df: pd.DataFrame, path: Path) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(2, 2, figsize=(16, 10), constrained_layout=True)
    configs = [
        ("user_pref_score", "User score", "score / 5", (0, 5)),
        ("daily_cost", "Daily cost", "normalized cost", None),
        ("event_setpoint_c", "VPP event AC setpoint", "setpoint (C)", None),
        ("routine_fit_score", "Schedule fit", "routine-fit score", (0, 1)),
    ]
    for ax, (metric, title, ylabel, ylim) in zip(axes.ravel(), configs):
        _draw_metric(ax, df, metric, title, ylabel, ylim=ylim)
    fig.suptitle("Persona Adaptability Without Acceptance/Fallback", fontsize=18, fontweight="bold")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=220)
    plt.close(fig)


def _plot_score_cost_panel(df: pd.DataFrame, path: Path) -> None:
    plt.style.use("seaborn-v0_8-whitegrid")
    fig, axes = plt.subplots(1, 2, figsize=(15, 5.8), constrained_layout=True)
    configs = [
        ("user_pref_score", "User score", "score / 5", (0, 5)),
        ("daily_cost", "Daily cost", "normalized cost", None),
    ]
    for ax, (metric, title, ylabel, ylim) in zip(axes.ravel(), configs):
        _draw_metric(ax, df, metric, title, ylabel, ylim=ylim)
    fig.suptitle("Persona Adaptability: Score-Cost Tradeoff", fontsize=17, fontweight="bold")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=220)
    plt.close(fig)


def _markdown_table(frame: pd.DataFrame) -> str:
    try:
        return frame.to_markdown()
    except Exception:
        return frame.to_string()


def _write_summary(df: pd.DataFrame, path: Path, *, source: Path) -> None:
    metrics = ["user_pref_score", "daily_cost", "event_setpoint_c", "routine_fit_score"]
    lines = [
        "# No-Gate Persona Adaptability",
        "",
        f"- Source summary: `{source}`",
        "- Acceptance and fallback were disabled; each method executes its own proposed strategy.",
        "- Routine-fit is a deterministic appliance schedule/service diagnostic from the persona schedule and appliance JSON; HVAC comfort is shown separately by the VPP setpoint panel.",
        "",
    ]
    for metric in metrics:
        lines.extend([f"## {metric}", "", _markdown_table(_pivot(df, metric).round(3)), ""])
    eb = df[df["method"] == "EnergyBridge"].copy()
    if not eb.empty:
        lines.extend(
            [
                "## EnergyBridge Strategy Signal",
                "",
                _markdown_table(
                    eb[
                        [
                            "persona_label",
                            "user_pref_score",
                            "daily_cost",
                            "event_setpoint_c",
                            "routine_fit_score",
                            "strategy_label",
                        ]
                    ].round(3)
                ),
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary-json", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--personas",
        nargs="+",
        default=list(PERSONA_LABEL),
        help="Persona ids to include.",
    )
    args = parser.parse_args()

    summary_json = Path(args.summary_json)
    out_dir = Path(args.output_dir)
    rows = _records(summary_json, set(args.personas or []))
    df = pd.DataFrame(rows)

    csv_path = out_dir / "persona_adaptability_no_gate_records.csv"
    _write_csv(rows, csv_path)

    panel_path = out_dir / "persona_adaptability_no_gate_4panel.png"
    score_cost_path = out_dir / "persona_adaptability_no_gate_score_cost.png"
    _plot_panel(df, panel_path)
    _plot_score_cost_panel(df, score_cost_path)
    plots = {
        "user_score": ("user_pref_score", "User score", "score / 5", (0, 5)),
        "daily_cost": ("daily_cost", "Daily cost", "normalized cost", None),
        "event_setpoint": ("event_setpoint_c", "VPP event AC setpoint", "setpoint (C)", None),
        "routine_fit": ("routine_fit_score", "Schedule fit", "routine-fit score", (0, 1)),
    }
    for name, (metric, title, ylabel, ylim) in plots.items():
        _plot_metric(
            df,
            metric,
            title,
            ylabel,
            out_dir / f"persona_adaptability_no_gate_{name}.png",
            ylim=ylim,
        )
    md_path = out_dir / "persona_adaptability_no_gate_summary.md"
    _write_summary(df, md_path, source=summary_json)

    print(f"[OK] csv   : {csv_path}")
    print(f"[OK] panel : {panel_path}")
    print(f"[OK] score : {score_cost_path}")
    print(f"[OK] md    : {md_path}")


if __name__ == "__main__":
    main()
