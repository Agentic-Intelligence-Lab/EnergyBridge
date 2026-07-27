#!/usr/bin/env python3
"""Analyze capacity accuracy by execution bucket for all five methods.

The unified capacity runner produces one guarded P70 report per July event
using the same method's June memory.  This postprocessor:

1. restricts every method to the common July 1--30 context set;
2. uses the manuscript metric R = actual / reported;
3. separately reports all-event accuracy, accuracy in the stored
   execution-consistency bucket, and their observed joint coverage:

       observed joint coverage
           = P(execution-consistent and 0.8 <= R <= 1.2)

The imported snapshots do not retain the final method-neutral consent bit.
Their ``vpp_appliance_avoidance_success`` field is therefore reported as an
execution-consistency proxy, not renamed as consent.  The script deliberately
does not synthesize accepted labels from an aggregate acceptance rate.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "paper_results" / "18_capacity_authorization_adjusted"
DEFAULT_MAIN_SUMMARY_DIR = REPO_ROOT / "paper_results" / "01_main_household_5x2_final"

METHODS = ("eb", "hema", "mpc", "rule_milp", "rl")
METHOD_LABELS = {
    "eb": "EnergyBridge",
    "hema": "HEMA",
    "mpc": "MPC",
    "rule_milp": "Rule+MILP",
    "rl": "PPO",
}
METHOD_COLORS = {
    "eb": "#2F6B9A",
    "hema": "#5B8E7D",
    "mpc": "#D18F3B",
    "rule_milp": "#9B6A9E",
    "rl": "#7A7A7A",
}
RAW_FILENAMES = {
    "eb": "eb_june_to_july_dry.json",
    "hema": "hema_june_to_july_dry.json",
    "mpc": "mpc_june_to_july_dry.json",
    "rule_milp": "rule_milp_june_to_july_dry.json",
    "rl": "rl_june_to_july_dry.json",
}
COMMON_RAW_FILENAMES = {
    method: filename.replace(
        "_june_to_july_dry.json",
        "_june_common285_to_july_matched300_dry.json",
    )
    for method, filename in RAW_FILENAMES.items()
}
JULY_MEMORY_PATHS = {
    "eb": REPO_ROOT
    / "dr_capacity_memory_toolkit/july_2025_daily_eb/data/energybridge_daily_dr_memory.json",
    "hema": REPO_ROOT
    / "dr_capacity_memory_toolkit/july_2025_daily_hema/data/hema_agent_daily_dr_memory.json",
    "mpc": REPO_ROOT
    / "dr_capacity_memory_toolkit/july_2025_daily_mpc/data/mpc_dynamic_daily_dr_memory.json",
    "rule_milp": REPO_ROOT
    / "dr_capacity_memory_toolkit/july_2025_daily_rule_milp/data/rule_milp_daily_dr_memory.json",
    "rl": REPO_ROOT
    / "dr_capacity_memory_toolkit/july_2025_daily_rl/data/rl_ppo_pref_v2_daily_dr_memory.json",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-dir", type=Path, required=True)
    parser.add_argument("--common-pool-raw-dir", type=Path)
    parser.add_argument("--main-summary-dir", type=Path, default=DEFAULT_MAIN_SUMMARY_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--event-hour",
        default="any",
        help=(
            "Use 'any' (default) for the matched July 1--30 audit. Supply an "
            "hour such as 18 for an event-hour sensitivity subset."
        ),
    )
    return parser.parse_args()


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def load_events(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path)
    events = payload.get("events", payload) if isinstance(payload, dict) else payload
    return list(events)


def context_key(row: dict[str, Any]) -> tuple[str, str, str]:
    return (
        str(row.get("household_id") or row.get("entity_id") or ""),
        str(row.get("city") or "").lower(),
        str(row.get("start_date") or "")[:10],
    )


def wilson_interval(successes: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return math.nan, math.nan
    p = successes / total
    denominator = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denominator
    margin = (
        z
        * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total))
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def common_july_contexts(event_hour: float | None) -> set[tuple[str, str, str]]:
    context_sets = []
    for path in JULY_MEMORY_PATHS.values():
        events = load_events(path)
        context_sets.append(
            {
                context_key(event)
                for event in events
                if str(event.get("start_date") or "")[:10] <= "2025-07-30"
                and (
                    event_hour is None
                    or abs(
                        float(
                            event.get("hour_of_day")
                            if event.get("hour_of_day") is not None
                            else event.get("trigger_h")
                        )
                        % 24.0
                        - event_hour
                    )
                    < 1e-9
                )
            }
        )
    common = set.intersection(*context_sets)
    expected = 300 if event_hour is None else 90 if event_hour == 18.0 else None
    if expected is not None and len(common) != expected:
        raise ValueError(
            f"Expected {expected} common July contexts at hour={event_hour}, "
            f"found {len(common)}"
        )
    return common


def matched_capacity_rows(
    method: str,
    raw_path: Path,
    common_contexts: set[tuple[str, str, str]],
) -> list[dict[str, Any]]:
    memory_events = load_events(JULY_MEMORY_PATHS[method])
    memory_by_id = {
        str(event.get("memory_event_id") or ""): event for event in memory_events
    }
    rows = load_json(raw_path)
    selected: list[dict[str, Any]] = []
    for row in rows:
        event = memory_by_id.get(str(row.get("memory_event_id") or ""))
        if event is None or context_key(event) not in common_contexts:
            continue
        actual = row.get("actual_shed_kwh")
        reported = row.get("reported_capacity_kwh")
        valid = (
            actual is not None
            and reported is not None
            and float(actual) > 0.0
            and float(reported) > 0.0
        )
        ratio = float(actual) / float(reported) if valid else math.nan
        passed = bool(valid and 0.8 <= ratio <= 1.2)
        selected.append(
            {
                "method": method,
                "memory_event_id": row.get("memory_event_id"),
                "household_id": event.get("household_id"),
                "city": event.get("city"),
                "start_date": event.get("start_date"),
                "actual_shed_kwh": actual,
                "reported_capacity_kwh": reported,
                "actual_to_reported_ratio": ratio,
                "within_20pct": passed,
                "execution_consistency_proxy": bool(
                    event.get("vpp_appliance_avoidance_success")
                ),
            }
        )
    if len(selected) != len(common_contexts):
        raise ValueError(
            f"{method}: expected {len(common_contexts)} matched rows, found {len(selected)}"
        )
    return selected


def summarize_capacity_rows(selected: list[dict[str, Any]]) -> dict[str, Any]:
    valid_rows = [row for row in selected if math.isfinite(row["actual_to_reported_ratio"])]
    pass_count = sum(row["within_20pct"] for row in selected)
    errors = [
        float(row["reported_capacity_kwh"]) - float(row["actual_shed_kwh"])
        for row in valid_rows
    ]
    ratios = [float(row["actual_to_reported_ratio"]) for row in valid_rows]
    metrics = {
        "events": len(selected),
        "valid_capacity_events": len(valid_rows),
        "capacity_pass_events": int(pass_count),
        "capacity_pass_rate": pass_count / len(selected) if selected else math.nan,
        "capacity_mae_kwh": float(np.mean(np.abs(errors))) if errors else math.nan,
        "capacity_rmse_kwh": (
            float(np.sqrt(np.mean(np.square(errors)))) if errors else math.nan
        ),
        "capacity_mean_actual_to_reported_ratio": (
            float(np.mean(ratios)) if ratios else math.nan
        ),
    }
    return metrics


def summarize_decomposition(events: list[dict[str, Any]]) -> dict[str, Any]:
    all_capacity = summarize_capacity_rows(events)
    proxy_capacity = summarize_capacity_rows(
        [event for event in events if event["execution_consistency_proxy"]]
    )
    total = all_capacity["events"]
    proxy_total = proxy_capacity["events"]
    proxy_passes = proxy_capacity["capacity_pass_events"]
    proxy_rate_ci = wilson_interval(proxy_total, total)
    proxy_pass_ci = wilson_interval(proxy_passes, proxy_total)
    proxy_joint_ci = wilson_interval(proxy_passes, total)
    return {
        "matched_capacity_events": total,
        "all_capacity_pass_events": all_capacity["capacity_pass_events"],
        "all_capacity_pass_rate": all_capacity["capacity_pass_rate"],
        "all_capacity_mae_kwh": all_capacity["capacity_mae_kwh"],
        "execution_proxy_events": proxy_total,
        "execution_proxy_rate": proxy_total / total if total else math.nan,
        "execution_proxy_wilson_95_low": proxy_rate_ci[0],
        "execution_proxy_wilson_95_high": proxy_rate_ci[1],
        "execution_proxy_pass_events": proxy_passes,
        "execution_proxy_pass_rate": proxy_capacity["capacity_pass_rate"],
        "execution_proxy_pass_wilson_95_low": proxy_pass_ci[0],
        "execution_proxy_pass_wilson_95_high": proxy_pass_ci[1],
        "execution_proxy_joint_pass_events": proxy_passes,
        "execution_proxy_joint_coverage": (
            proxy_passes / total if total else math.nan
        ),
        "execution_proxy_joint_wilson_95_low": proxy_joint_ci[0],
        "execution_proxy_joint_wilson_95_high": proxy_joint_ci[1],
    }


def validate_execution_proxy(summary_dir: Path) -> dict[str, Any]:
    confusion: Counter[tuple[bool, bool]] = Counter()
    files_read = 0
    for city in ("tianjin", "germany"):
        path = summary_dir / f"household_matrix_summary_{city}_7days_H6_cap76_merged.json"
        if not path.exists():
            continue
        for summary_row in load_json(path):
            result_path = Path(str(summary_row.get("output_dir") or "")) / "benchmark_result.json"
            if not result_path.exists():
                continue
            files_read += 1
            result = load_json(result_path)
            for event in result.get("vpp_event_log") or []:
                gate = event.get("vpp_acceptance_gate") or {}
                if "accepted" not in gate:
                    continue
                consent = bool(gate.get("accepted"))
                proxy = bool(event.get("vpp_appliance_avoidance_success"))
                confusion[(consent, proxy)] += 1
    total = sum(confusion.values())
    agree = confusion[(False, False)] + confusion[(True, True)]
    consent_positive = confusion[(True, True)] + confusion[(True, False)]
    proxy_positive = confusion[(True, True)] + confusion[(False, True)]
    observed = agree / total if total else math.nan
    consent_rate = consent_positive / total if total else math.nan
    proxy_rate = proxy_positive / total if total else math.nan
    expected = (
        consent_rate * proxy_rate + (1.0 - consent_rate) * (1.0 - proxy_rate)
        if total
        else math.nan
    )
    kappa = (observed - expected) / (1.0 - expected) if total and expected < 1.0 else math.nan
    return {
        "summary_files_read": files_read,
        "events": total,
        "true_negative": confusion[(False, False)],
        "true_positive": confusion[(True, True)],
        "false_positive": confusion[(False, True)],
        "false_negative": confusion[(True, False)],
        "agreement_rate": observed,
        "precision_for_consent": (
            confusion[(True, True)] / proxy_positive if proxy_positive else math.nan
        ),
        "recall_for_consent": (
            confusion[(True, True)] / consent_positive if consent_positive else math.nan
        ),
        "cohen_kappa": kappa,
        "note": (
            "Proxy validation uses the original main-run consent gate where both "
            "fields coexist; manuscript acceptance counts may include the separate "
            "method-neutral presentation replay."
        ),
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def make_figure(rows: list[dict[str, Any]], output_dir: Path) -> None:
    labels = [METHOD_LABELS[row["method"]] for row in rows]
    x = np.arange(len(rows))
    all_event = np.asarray([row["all_capacity_pass_rate"] for row in rows])
    effective = np.asarray([row["execution_proxy_pass_rate"] for row in rows])
    joint = np.asarray([row["execution_proxy_joint_coverage"] for row in rows])

    fig, axes = plt.subplots(1, 2, figsize=(8.0, 3.25), constrained_layout=True)

    width = 0.36
    axes[0].bar(
        x - width / 2,
        all_event * 100.0,
        width,
        color="#8FB8D8",
        edgecolor="white",
        label=r"All-event pass $B_m^{all}$",
    )
    effective_bars = axes[0].bar(
        x + width / 2,
        effective * 100.0,
        width,
        color="#345E7D",
        edgecolor="white",
        label=r"Execution-bucket pass $B_m^{E}$",
    )
    for bar, row in zip(effective_bars, rows):
        if math.isfinite(float(row["execution_proxy_pass_rate"])):
            axes[0].text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + 1.5,
                f"n={row['execution_proxy_events']}",
                ha="center",
                va="bottom",
                fontsize=7,
            )
    axes[0].set_ylabel("Target-band pass rate (%)")
    axes[0].set_title("(a) Accuracy reported separately")
    axes[0].set_xticks(x, labels, rotation=20, ha="right")
    axes[0].set_ylim(0, 112)
    axes[0].grid(axis="y", alpha=0.25)
    axes[0].legend(frameon=False, loc="upper right", fontsize=8)

    lower = np.asarray(
        [
            row["execution_proxy_joint_coverage"]
            - row["execution_proxy_joint_wilson_95_low"]
            for row in rows
        ]
    )
    upper = np.asarray(
        [
            row["execution_proxy_joint_wilson_95_high"]
            - row["execution_proxy_joint_coverage"]
            for row in rows
        ]
    )
    colors = [METHOD_COLORS[row["method"]] for row in rows]
    axes[1].bar(
        x,
        joint * 100.0,
        color=colors,
        edgecolor="white",
        yerr=np.vstack([lower, upper]) * 100.0,
        error_kw={"elinewidth": 1.0, "capsize": 3.0, "capthick": 1.0},
    )
    for index, value in enumerate(joint * 100.0):
        axes[1].text(index, value + 2.0, f"{value:.1f}", ha="center", va="bottom", fontsize=8)
    axes[1].set_ylabel("Observed joint coverage (%)")
    axes[1].set_title(r"(b) $Q_m^{E}=P(E_m \cap 0.8\!\leq\!R\!\leq\!1.2)$")
    axes[1].set_xticks(x, labels, rotation=20, ha="right")
    axes[1].set_ylim(0, 100)
    axes[1].grid(axis="y", alpha=0.25)

    for suffix in ("pdf", "png"):
        fig.savefig(
            output_dir / f"capacity_authorization_adjusted.{suffix}",
            dpi=220,
            bbox_inches="tight",
        )
    plt.close(fig)


def main() -> None:
    args = parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    event_hour = None if str(args.event_hour).lower() == "any" else float(args.event_hour)
    common_contexts = common_july_contexts(event_hour)

    summary_rows: list[dict[str, Any]] = []
    event_rows: list[dict[str, Any]] = []
    for method in METHODS:
        raw_path = args.raw_dir / RAW_FILENAMES[method]
        if not raw_path.exists():
            raise FileNotFoundError(f"Missing raw evaluation output: {raw_path}")
        events = matched_capacity_rows(method, raw_path, common_contexts)
        event_rows.extend(events)
        summary_rows.append(
            {
                "method": method,
                "label": METHOD_LABELS[method],
                **summarize_decomposition(events),
            }
        )

    robustness_rows: list[dict[str, Any]] = []
    if args.common_pool_raw_dir:
        for row in summary_rows:
            method = row["method"]
            common_path = args.common_pool_raw_dir / COMMON_RAW_FILENAMES[method]
            if not common_path.exists():
                raise FileNotFoundError(f"Missing common-pool output: {common_path}")
            common_events = matched_capacity_rows(method, common_path, common_contexts)
            common_summary = summarize_decomposition(common_events)
            robustness_rows.append(
                {
                    "method": method,
                    "label": METHOD_LABELS[method],
                    "own_pool_all_capacity_pass_rate": row[
                        "all_capacity_pass_rate"
                    ],
                    "common_pool285_all_capacity_pass_rate": common_summary[
                        "all_capacity_pass_rate"
                    ],
                    "own_pool_execution_proxy_pass_rate": row[
                        "execution_proxy_pass_rate"
                    ],
                    "common_pool285_execution_proxy_pass_rate": common_summary[
                        "execution_proxy_pass_rate"
                    ],
                    "own_pool_execution_proxy_joint_coverage": row[
                        "execution_proxy_joint_coverage"
                    ],
                    "common_pool285_execution_proxy_joint_coverage": common_summary[
                        "execution_proxy_joint_coverage"
                    ],
                }
            )

    proxy_validation = validate_execution_proxy(args.main_summary_dir)
    write_csv(args.output_dir / "capacity_authorization_adjusted_summary.csv", summary_rows)
    write_csv(args.output_dir / "matched_capacity_event_rows.csv", event_rows)
    if robustness_rows:
        write_csv(args.output_dir / "common_pool_robustness.csv", robustness_rows)
    (args.output_dir / "capacity_authorization_adjusted_summary.json").write_text(
        json.dumps(
            {
                "metric": (
                    "P(execution-consistency proxy and 0.8 <= "
                    "actual_shed_kwh / reported_capacity_kwh <= 1.2)"
                ),
                "capacity_policy": "top5 same-method June memory, deterministic guarded P70",
                "query_scope": (
                    "common July 1--30 household-city-day contexts"
                    + (
                        " with the same 18:00 event hour"
                        if event_hour == 18.0
                        else " with method-specific event hours"
                        if event_hour is None
                        else f" with the same {event_hour:g}:00 event hour"
                    )
                ),
                "query_contexts_per_method": len(common_contexts),
                "event_hour_filter": event_hour,
                "primary_bucket_definition": (
                    "Stored vpp_appliance_avoidance_success execution-consistency "
                    "proxy; this is not renamed as final consent"
                ),
                "consent_limit": (
                    "The imported capacity snapshots do not retain the newer "
                    "method-neutral per-event consent bit. Aggregate acceptance "
                    "rates are not used to synthesize event labels, so this "
                    "analysis cannot report final consent-conditioned accuracy."
                ),
                "execution_consistency_proxy_field": "vpp_appliance_avoidance_success",
                "proxy_validation": proxy_validation,
                "rows": summary_rows,
                "common_pool_robustness": robustness_rows,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    make_figure(summary_rows, args.output_dir)

    for row in summary_rows:
        print(
            f"{row['label']:<12} B_all={100 * row['all_capacity_pass_rate']:5.1f}%  "
            f"B_exec={100 * row['execution_proxy_pass_rate']:5.1f}%  "
            f"Q_exec={100 * row['execution_proxy_joint_coverage']:5.1f}%  "
            f"n_exec={row['execution_proxy_events']}"
        )
    if proxy_validation["events"]:
        print(
            "proxy agreement="
            f"{100 * proxy_validation['agreement_rate']:.1f}% "
            f"kappa={proxy_validation['cohen_kappa']:.3f}"
        )


if __name__ == "__main__":
    main()
