#!/usr/bin/env python3
"""Measure whether household-specific score differences persist across regions.

This diagnostic operates on the frozen 5-household x 2-region benchmark
summaries.  For each method it compares between-household score variation with
the score change induced by moving the same household from Tianjin to Germany.
It is intentionally descriptive: the result is an internal benchmark sanity
check, not a population-level estimate of household heterogeneity.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
from pathlib import Path
from typing import Any


METHOD_ORDER = [
    "EnergyBridge",
    "hema_agent",
    "mpc_dynamic",
    "rule_milp",
    "rl_ppo_pref_v2",
]

METHOD_LABELS = {
    "EnergyBridge": "EnergyBridge",
    "hema_agent": "HEMA",
    "mpc_dynamic": "MPC Dynamic",
    "rule_milp": "Rule+MILP",
    "rl_ppo_pref_v2": "RL PPO Pref-v2",
}


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[2]
    default_results = repo_root / "paper_results" / "01_main_household_5x2_final"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tianjin-json",
        type=Path,
        default=default_results
        / "household_matrix_summary_tianjin_7days_H6_cap76_merged.json",
    )
    parser.add_argument(
        "--germany-json",
        type=Path,
        default=default_results
        / "household_matrix_summary_germany_7days_H6_cap76_merged.json",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=repo_root / "paper_results" / "14_household_systematic_effects",
    )
    return parser.parse_args()


def load_index(path: Path) -> dict[tuple[str, str], dict[str, Any]]:
    rows = json.loads(path.read_text(encoding="utf-8"))
    index: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (str(row["method"]), str(row["household_id"]))
        if key in index:
            raise ValueError(f"duplicate method/household pair in {path}: {key}")
        index[key] = row
    return index


def pearson(xs: list[float], ys: list[float]) -> float:
    if len(xs) != len(ys) or len(xs) < 2:
        raise ValueError("Pearson correlation requires paired vectors")
    x_mean = statistics.fmean(xs)
    y_mean = statistics.fmean(ys)
    numerator = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    x_ss = sum((x - x_mean) ** 2 for x in xs)
    y_ss = sum((y - y_mean) ** 2 for y in ys)
    denominator = math.sqrt(x_ss * y_ss)
    return numerator / denominator if denominator else float("nan")


def load_daily_scores(row: dict[str, Any]) -> list[float]:
    result_path = Path(str(row["output_dir"])) / "benchmark_result.json"
    result = json.loads(result_path.read_text(encoding="utf-8"))
    scores = result.get("user_pref_scores")
    if not isinstance(scores, list) or not scores:
        raise ValueError(f"missing user_pref_scores in {result_path}")
    return [float(value) for value in scores]


def daily_variance_components(
    groups: dict[str, list[float]],
) -> dict[str, float | int]:
    sizes = {len(values) for values in groups.values()}
    if len(sizes) != 1:
        raise ValueError(f"unbalanced daily score groups: {sorted(sizes)}")
    observations_per_household = sizes.pop()
    household_count = len(groups)
    all_scores = [score for values in groups.values() for score in values]
    grand_mean = statistics.fmean(all_scores)
    between_ss = observations_per_household * sum(
        (statistics.fmean(values) - grand_mean) ** 2 for values in groups.values()
    )
    within_ss = sum(
        sum((score - statistics.fmean(values)) ** 2 for score in values)
        for values in groups.values()
    )
    between_ms = between_ss / (household_count - 1)
    within_ms = within_ss / (len(all_scores) - household_count)
    icc = (between_ms - within_ms) / (
        between_ms + (observations_per_household - 1) * within_ms
    )
    pooled_within_sd = math.sqrt(within_ms)
    return {
        "daily_observations": len(all_scores),
        "daily_observations_per_household": observations_per_household,
        "pooled_within_household_daily_sd": pooled_within_sd,
        "within_household_mean_standard_error": pooled_within_sd
        / math.sqrt(observations_per_household),
        "daily_score_icc": icc,
        "household_eta_squared": between_ss / (between_ss + within_ss),
    }


def analyze(
    tianjin: dict[tuple[str, str], dict[str, Any]],
    germany: dict[tuple[str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    method_rows: list[dict[str, Any]] = []
    pair_rows: list[dict[str, Any]] = []

    for method in METHOD_ORDER:
        t_households = {h for m, h in tianjin if m == method}
        g_households = {h for m, h in germany if m == method}
        if t_households != g_households:
            raise ValueError(
                f"unmatched households for {method}: "
                f"Tianjin-only={sorted(t_households - g_households)}, "
                f"Germany-only={sorted(g_households - t_households)}"
            )

        t_scores: list[float] = []
        g_scores: list[float] = []
        household_means: list[float] = []
        score_gaps: list[float] = []
        acceptance_gaps: list[float] = []
        daily_groups: dict[str, list[float]] = {}
        for household in sorted(t_households):
            t_row = tianjin[(method, household)]
            g_row = germany[(method, household)]
            t_score = float(t_row["user_pref_score"])
            g_score = float(g_row["user_pref_score"])
            t_accept = float(t_row["vpp_plan_acceptance_rate"])
            g_accept = float(g_row["vpp_plan_acceptance_rate"])
            t_scores.append(t_score)
            g_scores.append(g_score)
            household_means.append((t_score + g_score) / 2.0)
            score_gaps.append(abs(t_score - g_score))
            acceptance_gaps.append(abs(t_accept - g_accept))
            daily_groups[household] = load_daily_scores(t_row) + load_daily_scores(g_row)
            pair_rows.append(
                {
                    "method": METHOD_LABELS[method],
                    "method_key": method,
                    "household_id": household,
                    "tianjin_score": t_score,
                    "germany_score": g_score,
                    "absolute_region_score_gap": abs(t_score - g_score),
                    "tianjin_acceptance": t_accept,
                    "germany_acceptance": g_accept,
                    "absolute_region_acceptance_gap": abs(t_accept - g_accept),
                }
            )

        household_sd = statistics.stdev(household_means)
        mean_region_gap = statistics.fmean(score_gaps)
        method_row: dict[str, Any] = {
                "method": METHOD_LABELS[method],
                "method_key": method,
                "households": len(household_means),
                "between_household_score_sd": household_sd,
                "mean_absolute_region_score_gap": mean_region_gap,
                "household_sd_to_region_gap_ratio": (
                    household_sd / mean_region_gap if mean_region_gap else float("inf")
                ),
                "cross_region_score_pearson_r": pearson(t_scores, g_scores),
                "mean_absolute_acceptance_gap": statistics.fmean(acceptance_gaps),
            }
        method_row.update(daily_variance_components(daily_groups))
        method_rows.append(method_row)
    return method_rows, pair_rows


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def write_markdown(path: Path, rows: list[dict[str, Any]]) -> None:
    lines = [
        "# Cross-region household persistence diagnostic",
        "",
        "The analysis pairs the same five synthetic household types in the frozen "
        "Tianjin and Germany seven-day runs. `Household SD` is the sample standard "
        "deviation of each household's cross-region mean score. `Region gap` is the "
        "mean absolute Tianjin--Germany score difference for a paired household. "
        "This is an internal benchmark diagnostic, not field evidence or a population "
        "variance estimate.",
        "",
        "| Method | Household SD | Region gap | SD / gap | Cross-region r | Acceptance gap |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            "| {method} | {between_household_score_sd:.3f} | "
            "{mean_absolute_region_score_gap:.3f} | "
            "{household_sd_to_region_gap_ratio:.2f} | "
            "{cross_region_score_pearson_r:.3f} | "
            "{mean_absolute_acceptance_gap:.3f} |".format(**row)
        )
    lines.extend(
        [
            "",
            "The day-level decomposition uses 70 method-specific household-days "
            "(5 households x 2 regions x 7 days). For EnergyBridge, the pooled "
            "within-household daily SD is "
            f"{rows[0]['pooled_within_household_daily_sd']:.3f}, its standard error "
            "after 14 observations per household is "
            f"{rows[0]['within_household_mean_standard_error']:.3f}, and the "
            f"one-way daily-score ICC is {rows[0]['daily_score_icc']:.3f}.",
            "",
            "The acceptance gap is zero because the frozen paired setup reuses stable "
            "event draws. It should therefore not be interpreted as independent evidence "
            "of geographic acceptance generalization.",
            "",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    args = parse_args()
    tianjin = load_index(args.tianjin_json)
    germany = load_index(args.germany_json)
    method_rows, pair_rows = analyze(tianjin, germany)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(args.output_dir / "household_systematic_effects.csv", method_rows)
    write_csv(args.output_dir / "household_region_pairs.csv", pair_rows)
    (args.output_dir / "household_systematic_effects.json").write_text(
        json.dumps(method_rows, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    write_markdown(args.output_dir / "household_systematic_effects.md", method_rows)
    print(args.output_dir / "household_systematic_effects.md")


if __name__ == "__main__":
    main()
