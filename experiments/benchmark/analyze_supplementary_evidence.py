#!/usr/bin/env python3
"""Build appendix-ready statistical and operational evidence from frozen runs.

The analysis has four parts:
1. paired 70-event EnergyBridge comparisons with a household-region cluster
   bootstrap;
2. household-paired EnergyBridge versus HEMA deltas and LLM resource counts;
3. a no-DR ordinary-routine physical reference; and
4. a held-out capacity retrieval top-k sweep.

All comparisons reuse committed/frozen artifacts and preserve the benchmark's
matched household, region, day, weather, tariff, event, and appliance pairing.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Callable


METHOD_ORDER = ["EnergyBridge", "hema_agent", "mpc_dynamic", "rule_milp", "rl_ppo_pref_v2"]
METHOD_LABEL = {
    "EnergyBridge": "EnergyBridge",
    "hema_agent": "HEMA",
    "mpc_dynamic": "MPC Dynamic",
    "rule_milp": "Rule+MILP",
    "rl_ppo_pref_v2": "RL PPO Pref-v2",
}
BASELINES = ["hema_agent", "mpc_dynamic", "rule_milp", "rl_ppo_pref_v2"]


def parse_args() -> argparse.Namespace:
    repo = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--main-dir",
        type=Path,
        default=repo / "paper_results" / "01_main_household_5x2_final",
    )
    parser.add_argument(
        "--no-dr-dir",
        type=Path,
        default=repo
        / "benchmark_results"
        / "2026-07-21_no_dr_task_completion_fix_5x2_v1"
        / "_batch_logs",
    )
    parser.add_argument(
        "--topk-dir",
        type=Path,
        default=repo / "paper_results" / "15_capacity_topk_sweep",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repo / "paper_results" / "16_supplementary_evidence",
    )
    parser.add_argument("--bootstrap-samples", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=20270721)
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main_summary_path(main_dir: Path, city: str) -> Path:
    return main_dir / f"household_matrix_summary_{city.lower()}_7days_H6_cap76_merged.json"


def no_dr_summary_path(no_dr_dir: Path, city: str) -> Path:
    return no_dr_dir / f"household_matrix_summary_{city.lower()}_7days_H6.json"


def index_summaries(main_dir: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    index: dict[tuple[str, str, str], dict[str, Any]] = {}
    for city in ("Tianjin", "Germany"):
        for row in load_json(main_summary_path(main_dir, city)):
            key = (city, str(row["household_id"]), str(row["method"]))
            if key in index:
                raise ValueError(f"duplicate main summary key: {key}")
            index[key] = row
    return index


def load_result(row: dict[str, Any]) -> dict[str, Any]:
    path = Path(str(row["output_dir"])) / "benchmark_result.json"
    result = load_json(path)
    if int(result.get("exit_code", 0)) != 0:
        raise ValueError(f"nonzero result in {path}")
    return result


def event_acceptance(event: dict[str, Any]) -> float:
    gate = event.get("vpp_acceptance_gate") or {}
    return float(bool(gate.get("accepted", False)))


def build_event_index(
    summary_index: dict[tuple[str, str, str], dict[str, Any]],
) -> tuple[dict[tuple[str, str, int, str], dict[str, float]], dict[tuple[str, str, str], dict[str, Any]]]:
    events: dict[tuple[str, str, int, str], dict[str, float]] = {}
    results: dict[tuple[str, str, str], dict[str, Any]] = {}
    for (city, household, method), row in summary_index.items():
        result = load_result(row)
        results[(city, household, method)] = result
        for event in result.get("vpp_event_log") or []:
            day = int(event["day"])
            key = (city, household, day, method)
            events[key] = {
                "score": float(event.get("score") or 0.0),
                "comfort_score": float(event.get("comfort_score") or 0.0),
                "energy_score": float(event.get("energy_score") or 0.0),
                "vpp_score": float(event.get("vpp_score") or 0.0),
                "accepted": event_acceptance(event),
                "event_energy_kwh": float(event.get("actual_kwh") or 0.0),
                "target_achieved": float(bool(event.get("target_achieved", False))),
                "actual_shed_kwh": float(event.get("actual_shed_kwh") or 0.0),
                "member_score_std": float(event.get("member_score_std") or 0.0),
            }
    return events, results


def percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("empty percentile input")
    position = (len(ordered) - 1) * quantile
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    fraction = position - lower
    return ordered[lower] * (1.0 - fraction) + ordered[upper] * fraction


def cluster_bootstrap_ci(
    cluster_values: dict[tuple[str, str], list[float]],
    *,
    samples: int,
    seed: int,
) -> tuple[float, float]:
    rng = random.Random(seed)
    clusters = sorted(cluster_values)
    draws: list[float] = []
    for _ in range(samples):
        selected = [rng.choice(clusters) for _ in clusters]
        values = [value for cluster in selected for value in cluster_values[cluster]]
        draws.append(statistics.fmean(values))
    return percentile(draws, 0.025), percentile(draws, 0.975)


def paired_comparisons(
    events: dict[tuple[str, str, int, str], dict[str, float]],
    *,
    samples: int,
    seed: int,
) -> list[dict[str, Any]]:
    metric_specs: list[tuple[str, Callable[[dict[str, float], dict[str, float]], float]]] = [
        ("score_gain", lambda eb, other: eb["score"] - other["score"]),
        ("acceptance_gain", lambda eb, other: eb["accepted"] - other["accepted"]),
        ("event_energy_reduction_kwh", lambda eb, other: other["event_energy_kwh"] - eb["event_energy_kwh"]),
        ("target_achievement_gain", lambda eb, other: eb["target_achieved"] - other["target_achieved"]),
    ]
    rows: list[dict[str, Any]] = []
    eb_keys = sorted(key for key in events if key[3] == "EnergyBridge")
    for baseline_idx, baseline in enumerate(BASELINES):
        paired = []
        for city, household, day, _ in eb_keys:
            eb = events[(city, household, day, "EnergyBridge")]
            other_key = (city, household, day, baseline)
            if other_key not in events:
                raise ValueError(f"missing paired event: {other_key}")
            paired.append((city, household, day, eb, events[other_key]))

        row: dict[str, Any] = {
            "baseline": METHOD_LABEL[baseline],
            "baseline_key": baseline,
            "paired_events": len(paired),
            "clusters": len({(city, household) for city, household, _, _, _ in paired}),
            "score_win_rate": statistics.fmean(float(eb["score"] > other["score"]) for _, _, _, eb, other in paired),
        }
        for metric_idx, (name, fn) in enumerate(metric_specs):
            cluster_values: dict[tuple[str, str], list[float]] = defaultdict(list)
            for city, household, _, eb, other in paired:
                cluster_values[(city, household)].append(fn(eb, other))
            values = [value for cluster in cluster_values.values() for value in cluster]
            low, high = cluster_bootstrap_ci(
                cluster_values,
                samples=samples,
                seed=seed + baseline_idx * 100 + metric_idx,
            )
            row[name] = statistics.fmean(values)
            row[f"{name}_ci_low"] = low
            row[f"{name}_ci_high"] = high
        rows.append(row)
    return rows


def household_hema_deltas(
    summary_index: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for city in ("Tianjin", "Germany"):
        households = sorted({key[1] for key in summary_index if key[0] == city})
        for household in households:
            eb = summary_index[(city, household, "EnergyBridge")]
            hema = summary_index[(city, household, "hema_agent")]
            eb_energy = float(eb["vpp_window_energy_avg_per_hour_kwh"])
            hema_energy = float(hema["vpp_window_energy_avg_per_hour_kwh"])
            rows.append(
                {
                    "city": city,
                    "household_id": household,
                    "score_gain": float(eb["user_pref_score"]) - float(hema["user_pref_score"]),
                    "acceptance_gain_pp": 100.0
                    * (float(eb["vpp_plan_acceptance_rate"]) - float(hema["vpp_plan_acceptance_rate"])),
                    "event_energy_reduction_pct": 100.0 * (hema_energy - eb_energy) / hema_energy,
                    "daily_cost_saving": (
                        float(hema["day_ahead_total_cost_eur"]) - float(eb["day_ahead_total_cost_eur"])
                    )
                    / 7.0,
                }
            )
    return rows


def resource_rows(
    results: dict[tuple[str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for city in ("Tianjin", "Germany"):
        for method in ("EnergyBridge", "hema_agent"):
            group = [result for (c, _, m), result in results.items() if c == city and m == method]
            household_days = sum(int(result.get("sim_days") or 7) for result in group)
            prompt = sum(int(result.get("llm_tokens_prompt") or 0) for result in group)
            completion = sum(int(result.get("llm_tokens_completion") or 0) for result in group)
            calls = sum(int(result.get("llm_call_count") or 0) for result in group)
            rows.append(
                {
                    "city": city,
                    "method": METHOD_LABEL[method],
                    "household_days": household_days,
                    "calls_per_household_day": calls / household_days,
                    "prompt_tokens_per_household_day": prompt / household_days,
                    "completion_tokens_per_household_day": completion / household_days,
                    "total_tokens_per_household_day": (prompt + completion) / household_days,
                }
            )
    return rows


def no_dr_rows(
    summary_index: dict[tuple[str, str, str], dict[str, Any]],
    no_dr_dir: Path,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for city in ("Tianjin", "Germany"):
        no_dr = load_json(no_dr_summary_path(no_dr_dir, city))
        eb = [row for (c, _, method), row in summary_index.items() if c == city and method == "EnergyBridge"]
        eb_daily_energy = statistics.fmean(float(row["energy_kwh_per_day"]) for row in eb)
        no_dr_daily_energy = statistics.fmean(float(row["energy_kwh_per_day"]) for row in no_dr)
        eb_event_energy = statistics.fmean(float(row["vpp_window_energy_avg_per_hour_kwh"]) for row in eb)
        no_dr_event_energy = statistics.fmean(float(row["vpp_window_energy_avg_per_hour_kwh"]) for row in no_dr)
        eb_daily_cost = sum(float(row["day_ahead_total_cost_eur"]) for row in eb) / 35.0
        no_dr_daily_cost = sum(float(row["day_ahead_total_cost_eur"]) for row in no_dr) / 35.0
        rows.append(
            {
                "city": city,
                "no_dr_daily_energy_kwh": no_dr_daily_energy,
                "energybridge_daily_energy_kwh": eb_daily_energy,
                "daily_energy_reduction_pct": 100.0 * (no_dr_daily_energy - eb_daily_energy) / no_dr_daily_energy,
                "no_dr_event_energy_kwh": no_dr_event_energy,
                "energybridge_event_energy_kwh": eb_event_energy,
                "event_energy_reduction_pct": 100.0 * (no_dr_event_energy - eb_event_energy) / no_dr_event_energy,
                "no_dr_daily_cost": no_dr_daily_cost,
                "energybridge_daily_cost": eb_daily_cost,
                "daily_cost_reduction_pct": 100.0 * (no_dr_daily_cost - eb_daily_cost) / no_dr_daily_cost,
                "no_dr_physical_task_completion": statistics.fmean(
                    float(row["physical_appliance_task_completion_rate"]) for row in no_dr
                ),
                "energybridge_physical_task_completion": statistics.fmean(
                    float(row["physical_appliance_task_completion_rate"]) for row in eb
                ),
            }
        )
    return rows


def capacity_topk_rows(topk_dir: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in sorted(topk_dir.glob("topk_*.json"), key=lambda item: int(item.stem.split("_")[-1])):
        k = int(path.stem.split("_")[-1])
        accepted = [row for row in load_json(path) if float(row.get("vpp_plan_acceptance_rate") or 0.0) == 1.0]
        if not accepted:
            raise ValueError(f"no accepted rows in {path}")

        def metrics(prefix: str) -> tuple[float, float, float]:
            ratios = [float(row[f"ratio_{prefix}"]) for row in accepted]
            errors = [
                float(row[f"reported_{prefix}_kwh"]) - float(row["actual_shed_kwh"])
                for row in accepted
            ]
            pass_rate = statistics.fmean(float(0.8 <= ratio <= 1.2) for ratio in ratios)
            mae = statistics.fmean(abs(error) for error in errors)
            mean_ratio = statistics.fmean(ratios)
            return pass_rate, mae, mean_ratio

        rag_pass, rag_mae, rag_ratio = metrics("rag")
        is_pass, is_mae, is_ratio = metrics("is")
        rows.append(
            {
                "top_k": k,
                "events": len(accepted),
                "rag_pass_rate": rag_pass,
                "rag_mae_kwh": rag_mae,
                "rag_mean_ratio": rag_ratio,
                "is_pass_rate": is_pass,
                "is_mae_kwh": is_mae,
                "is_mean_ratio": is_ratio,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def fmt_ci(row: dict[str, Any], metric: str, scale: float = 1.0) -> str:
    return (
        f"{row[metric] * scale:.3f} "
        f"[{row[f'{metric}_ci_low'] * scale:.3f}, {row[f'{metric}_ci_high'] * scale:.3f}]"
    )


def write_report(
    path: Path,
    paired: list[dict[str, Any]],
    household: list[dict[str, Any]],
    resources: list[dict[str, Any]],
    no_dr: list[dict[str, Any]],
    topk: list[dict[str, Any]],
    *,
    bootstrap_samples: int,
) -> None:
    lines = [
        "# Supplementary fairness, efficiency, and capacity evidence",
        "",
        "## Paired event comparison",
        "",
        f"Each row uses 70 matched events and a {bootstrap_samples:,}-sample "
        "household-region cluster bootstrap over 10 clusters. Positive values favor EnergyBridge.",
        "",
        "| Baseline | Score gain [95% CI] | Acceptance gain [95% CI] | Event-energy reduction kWh [95% CI] | Target gain [95% CI] | Score win rate |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in paired:
        lines.append(
            f"| {row['baseline']} | {fmt_ci(row, 'score_gain')} | "
            f"{fmt_ci(row, 'acceptance_gain', 100.0)} pp | "
            f"{fmt_ci(row, 'event_energy_reduction_kwh')} | "
            f"{fmt_ci(row, 'target_achievement_gain', 100.0)} pp | "
            f"{100.0 * row['score_win_rate']:.1f}% |"
        )

    lines.extend(
        [
            "",
            "## Household-paired EnergyBridge versus HEMA",
            "",
            "| Region | Household | Score gain | Acceptance gain pp | VPP-energy reduction | Daily cost saving |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in household:
        lines.append(
            f"| {row['city']} | {row['household_id']} | {row['score_gain']:.3f} | "
            f"{row['acceptance_gain_pp']:.1f} | {row['event_energy_reduction_pct']:.1f}% | "
            f"{row['daily_cost_saving']:.3f} |"
        )

    lines.extend(
        [
            "",
            "## LLM resources",
            "",
            "| Region | Method | Calls / household-day | Prompt tokens / household-day | Completion tokens / household-day | Total tokens / household-day |",
            "|---|---|---:|---:|---:|---:|",
        ]
    )
    for row in resources:
        lines.append(
            f"| {row['city']} | {row['method']} | {row['calls_per_household_day']:.1f} | "
            f"{row['prompt_tokens_per_household_day']:.0f} | "
            f"{row['completion_tokens_per_household_day']:.0f} | "
            f"{row['total_tokens_per_household_day']:.0f} |"
        )

    lines.extend(
        [
            "",
            "## No-DR ordinary-routine reference",
            "",
            "The no-DR row is a physical reference, not a participant in user-score or consent ranking.",
            "",
            "| Region | No-DR event kWh | EB event kWh | Event reduction | No-DR daily energy | EB daily energy | Daily reduction |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in no_dr:
        lines.append(
            f"| {row['city']} | {row['no_dr_event_energy_kwh']:.3f} | "
            f"{row['energybridge_event_energy_kwh']:.3f} | {row['event_energy_reduction_pct']:.1f}% | "
            f"{row['no_dr_daily_energy_kwh']:.3f} | {row['energybridge_daily_energy_kwh']:.3f} | "
            f"{row['daily_energy_reduction_pct']:.1f}% |"
        )

    lines.extend(
        [
            "",
            "## Held-out capacity top-k sweep",
            "",
            "| Top-k | Accepted events | RAG pass | RAG MAE | Mean actual/reported | IS pass |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in topk:
        lines.append(
            f"| {row['top_k']} | {row['events']} | {100.0 * row['rag_pass_rate']:.2f}% | "
            f"{row['rag_mae_kwh']:.3f} | {row['rag_mean_ratio']:.3f} | "
            f"{100.0 * row['is_pass_rate']:.2f}% |"
        )
    lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    if args.bootstrap_samples < 100:
        raise SystemExit("--bootstrap-samples must be >= 100")
    summary_index = index_summaries(args.main_dir)
    events, results = build_event_index(summary_index)
    paired = paired_comparisons(
        events,
        samples=args.bootstrap_samples,
        seed=args.seed,
    )
    household = household_hema_deltas(summary_index)
    resources = resource_rows(results)
    no_dr = no_dr_rows(summary_index, args.no_dr_dir)
    topk = capacity_topk_rows(args.topk_dir)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    groups = {
        "paired_event_comparisons": paired,
        "household_hema_deltas": household,
        "llm_resources": resources,
        "no_dr_reference": no_dr,
        "capacity_topk_sweep": topk,
    }
    for name, rows in groups.items():
        write_csv(args.output_dir / f"{name}.csv", rows)
    (args.output_dir / "supplementary_evidence.json").write_text(
        json.dumps(groups, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    write_report(
        args.output_dir / "supplementary_evidence.md",
        paired,
        household,
        resources,
        no_dr,
        topk,
        bootstrap_samples=args.bootstrap_samples,
    )
    print(args.output_dir / "supplementary_evidence.md")


if __name__ == "__main__":
    main()
