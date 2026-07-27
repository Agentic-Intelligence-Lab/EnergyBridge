#!/usr/bin/env python3
"""Join consent decisions with capacity reports on the same event cohort.

The preferred mode consumes freshly simulated, counterfactual-postprocessed
daily matrices for both the June retrieval pool and July query cohort. A
legacy frozen-artifact mode remains available for auditing older results.
Every event is reported from the same method's June memory using the
deterministic guarded P70 reporter. The output separates:

* all-event target-band accuracy;
* target-band accuracy conditional on accepted events; and
* joint coverage, P(accepted and target-band accurate).

Fresh daily-matrix mode is a closed-loop simulation: consent selects the
controller or fallback branch before physical execution.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from experiments.benchmark.replay_traditional_acceptance_method_neutral import (
    replay as replay_traditional_acceptance,
)
from experiments.benchmark.run_capacity_shed_evaluation import (
    _evaluate_one,
    _load_events,
    _memory_path,
    apply_is_correction,
)
from energybridge.quantification.dr_event_memory import build_dr_event_memory
from energybridge.quantification.weather_shift.features import (
    daily_features_for_city,
    event_weather_features,
)

DEFAULT_INPUT_DIR = PROJECT_ROOT / "paper_results" / "01_main_household_5x2_final"
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / "paper_results" / "19_capacity_consent_joined_rerun"
)

METHODS = ("EnergyBridge", "hema_agent", "mpc_dynamic", "rule_milp", "rl_ppo_pref_v2")
TRADITIONAL_METHODS = {"mpc_dynamic", "rule_milp", "rl_ppo_pref_v2"}
CAPACITY_METHOD = {
    "EnergyBridge": "eb",
    "hema_agent": "hema",
    "mpc_dynamic": "mpc",
    "rule_milp": "rule_milp",
    "rl_ppo_pref_v2": "rl",
}
METHOD_LABEL = {
    "EnergyBridge": "EnergyBridge",
    "EnergyBridge+IS": "EnergyBridge+IS",
    "hema_agent": "HEMA",
    "mpc_dynamic": "MPC",
    "rule_milp": "Rule+MILP",
    "rl_ppo_pref_v2": "PPO",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _event_key(event: dict[str, Any]) -> str:
    return str(event.get("id") or event.get("event_id") or "")


def _wilson_interval(
    successes: int,
    total: int,
    z: float = 1.959963984540054,
) -> tuple[float, float]:
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


def _capacity_record(
    event: dict[str, Any],
    *,
    household_id: str,
    region: str,
    method: str,
) -> dict[str, Any]:
    trigger_h = float(event.get("trigger_h") or 0.0)
    baseline = event.get("counterfactual_baseline_kwh")
    if baseline is None:
        baseline = event.get("counterfactual_capacity_upper_bound_kwh")
    if baseline is None:
        baseline = event.get("demand_baseline_kwh")
    if baseline is None:
        baseline = event.get("estimated_baseline_kwh")
    return {
        "memory_event_id": (
            f"joined|{household_id}|{region.lower()}|{method}|{_event_key(event)}"
        ),
        "event_id": _event_key(event),
        "day": event.get("day"),
        "trigger_h": trigger_h,
        "end_h": event.get("end_h"),
        "hour_of_day": trigger_h % 24.0,
        "duration_h": (
            float(event.get("end_h")) - trigger_h
            if event.get("end_h") is not None
            else None
        ),
        "no_dr_baseline_kwh": baseline,
        "realized_delivery_kwh": event.get("actual_shed_kwh"),
        "household_id": household_id,
        "entity_id": household_id,
        "persona_id": household_id,
        "city": region,
        "method": method,
        "start_date": "",
    }


def _fresh_traditional_acceptance(
    input_dir: Path,
) -> dict[tuple[str, str, str, str], dict[str, Any]]:
    rows = replay_traditional_acceptance(input_dir)
    return {
        (
            str(row["region"]).lower(),
            str(row["household_id"]),
            str(row["method"]),
            str(row["event_id"]),
        ): row
        for row in rows
    }


def run_joined_replay(input_dir: Path, *, apply_is: bool) -> list[dict[str, Any]]:
    traditional_acceptance = _fresh_traditional_acceptance(input_dir)
    pools = {
        method: _load_events(_memory_path(CAPACITY_METHOD[method], "june"))
        for method in METHODS
    }
    output_rows: list[dict[str, Any]] = []

    for region in ("Tianjin", "Germany"):
        summary_path = input_dir / (
            f"household_matrix_summary_{region.lower()}_7days_H6_cap76_merged.json"
        )
        selected = [row for row in _read_json(summary_path) if row.get("method") in METHODS]
        for summary_row in selected:
            method = str(summary_row["method"])
            household_id = str(
                summary_row.get("household_id") or summary_row.get("persona_id")
            )
            result_path = Path(str(summary_row["output_dir"])) / "benchmark_result.json"
            run = _read_json(result_path)
            events = list(run.get("vpp_event_log") or [])
            gates = {
                _event_key(gate): gate
                for gate in (run.get("vpp_plan_gate_events") or [])
            }
            if len(events) != 7 or len(gates) != 7:
                raise ValueError(
                    f"Expected seven events and gates in {result_path}; "
                    f"found events={len(events)}, gates={len(gates)}"
                )

            for event in events:
                event_id = _event_key(event)
                if method in TRADITIONAL_METHODS:
                    replay_key = (
                        region.lower(),
                        household_id,
                        method,
                        event_id,
                    )
                    acceptance = traditional_acceptance[replay_key]
                    accepted = bool(acceptance["accepted_new"])
                    acceptance_probability = float(
                        acceptance["acceptance_probability_new"]
                    )
                    acceptance_source = "fresh_method_neutral_replay"
                else:
                    gate = gates[event_id]
                    accepted = bool(gate.get("accepted"))
                    acceptance_probability = float(
                        gate.get("acceptance_probability") or 0.0
                    )
                    acceptance_source = "stored_benchmark_gate"

                record = _capacity_record(
                    event,
                    household_id=household_id,
                    region=region,
                    method=method,
                )
                report = _evaluate_one(
                    record,
                    pools[method],
                    exclude_self=False,
                    client=None,
                    dry_run=True,
                )
                actual = report.get("actual_shed_kwh")
                reported = report.get("reported_capacity_kwh")
                valid = (
                    actual is not None
                    and reported is not None
                    and float(actual) > 0.0
                    and float(reported) > 0.0
                )
                ratio = float(actual) / float(reported) if valid else None
                output_rows.append(
                    {
                        "region": region,
                        "household_id": household_id,
                        "method": method,
                        "event_id": event_id,
                        "day": event.get("day"),
                        "accepted": accepted,
                        "acceptance_probability": acceptance_probability,
                        "acceptance_source": acceptance_source,
                        "actual_shed_kwh": actual,
                        "reported_capacity_kwh": reported,
                        "actual_to_reported_ratio": ratio,
                        "within_20pct": bool(valid and 0.8 <= ratio <= 1.2),
                        "retrieval_count": report.get("retrieval_count"),
                        "recommended_quantile": report.get("recommended_quantile"),
                        "neighbors": report.get("neighbors"),
                        "source_result": str(result_path),
                    }
                )

    expected = 2 * 5 * len(METHODS) * 7
    if len(output_rows) != expected:
        raise ValueError(f"Expected {expected} joined events, found {len(output_rows)}")

    return _apply_is(rows=output_rows, enabled=apply_is)


def run_daily_closed_loop(
    summary_path: Path,
    *,
    apply_is: bool,
    pool_summary_path: Path | None = None,
) -> list[dict[str, Any]]:
    """Evaluate a freshly simulated daily matrix after counterfactual postprocess."""
    pools = (
        _pools_from_daily_summary(pool_summary_path)
        if pool_summary_path is not None
        else {
            method: _load_events(_memory_path(CAPACITY_METHOD[method], "june"))
            for method in METHODS
        }
    )
    output_rows: list[dict[str, Any]] = []
    selected = [
        row for row in _read_json(summary_path) if row.get("method") in METHODS
    ]
    for summary_row in selected:
        method = str(summary_row["method"])
        household_id = str(
            summary_row.get("household_id") or summary_row.get("persona_id")
        )
        region = str(summary_row["city"])
        result_path = Path(str(summary_row["output_dir"])) / "benchmark_result.json"
        run = _read_json(result_path)
        events = list(run.get("vpp_event_log") or [])
        gates = {
            _event_key(gate): gate
            for gate in (run.get("vpp_plan_gate_events") or [])
        }
        if len(events) != 1 or len(gates) != 1:
            raise ValueError(
                f"Expected one event and gate in {result_path}; "
                f"found events={len(events)}, gates={len(gates)}"
            )
        event = events[0]
        event_id = _event_key(event)
        gate = gates[event_id]
        record = _capacity_record(
            event,
            household_id=household_id,
            region=region,
            method=method,
        )
        record["start_date"] = summary_row.get("start_date") or ""
        report = _evaluate_one(
            record,
            pools[method],
            exclude_self=False,
            client=None,
            dry_run=True,
        )
        actual = report.get("actual_shed_kwh")
        reported = report.get("reported_capacity_kwh")
        valid = (
            actual is not None
            and reported is not None
            and float(actual) > 0.0
            and float(reported) > 0.0
        )
        ratio = float(actual) / float(reported) if valid else None
        output_rows.append(
            {
                "region": region,
                "household_id": household_id,
                "method": method,
                "event_id": event_id,
                "day": summary_row.get("memory_source_day"),
                "accepted": bool(gate.get("accepted")),
                "acceptance_probability": float(
                    gate.get("acceptance_probability") or 0.0
                ),
                "acceptance_source": "fresh_closed_loop_method_neutral_gate",
                "actual_shed_kwh": actual,
                "reported_capacity_kwh": reported,
                "actual_to_reported_ratio": ratio,
                "within_20pct": bool(valid and 0.8 <= ratio <= 1.2),
                "retrieval_count": report.get("retrieval_count"),
                "recommended_quantile": report.get("recommended_quantile"),
                "neighbors": report.get("neighbors"),
                "source_result": str(result_path),
            }
        )
    method_counts = {
        method: sum(row["method"] == method for row in output_rows)
        for method in METHODS
    }
    if len(set(method_counts.values())) != 1 or not output_rows:
        raise ValueError(f"Unbalanced daily matrix: {method_counts}")
    return _apply_is(rows=output_rows, enabled=apply_is)


def _pools_from_daily_summary(
    summary_path: Path,
) -> dict[str, list[dict[str, Any]]]:
    rows = _read_json(summary_path)
    pools: dict[str, list[dict[str, Any]]] = {}
    for method in METHODS:
        memory_items: list[tuple[dict[str, Any], dict[str, Any], Path]] = []
        for row in rows:
            if row.get("method") != method:
                continue
            result_path = Path(str(row["output_dir"])) / "benchmark_result.json"
            memory_items.append((_read_json(result_path), row, result_path))
        memory = build_dr_event_memory(memory_items, methods=[method])
        events = list(memory.get("events") or [])
        if not events:
            raise ValueError(
                f"No {method} capacity events built from {summary_path}"
            )
        weather_cache: dict[str, dict[str, dict[str, float]]] = {}
        for event in events:
            city = str(event.get("city") or "")
            if city not in weather_cache:
                weather_cache[city] = daily_features_for_city(city)
            event["weather_features"] = event_weather_features(
                city,
                str(event.get("start_date") or ""),
                daily_cache=weather_cache[city],
            )
        pools[method] = events
    if len(set(len(events) for events in pools.values())) != 1:
        raise ValueError(
            "Unbalanced fresh capacity pools: "
            + ", ".join(f"{method}={len(events)}" for method, events in pools.items())
        )
    return pools


def _apply_is(
    *,
    rows: list[dict[str, Any]],
    enabled: bool,
) -> list[dict[str, Any]]:
    if enabled:
        eb_indices = [
            index
            for index, row in enumerate(rows)
            if row["method"] == "EnergyBridge"
        ]
        eb_rows = apply_is_correction(
            [rows[index] for index in eb_indices],
            PROJECT_ROOT / "importance_sampling" / "IS_result",
        )
        for index, corrected in zip(eb_indices, eb_rows):
            reported_is = corrected.get("reported_capacity_kwh_is")
            actual = corrected.get("actual_shed_kwh")
            valid = (
                actual is not None
                and reported_is is not None
                and float(actual) > 0.0
                and float(reported_is) > 0.0
            )
            ratio_is = float(actual) / float(reported_is) if valid else None
            rows[index]["reported_capacity_kwh_is"] = reported_is
            rows[index]["actual_to_reported_ratio_is"] = ratio_is
            rows[index]["within_20pct_is"] = bool(
                valid and 0.8 <= ratio_is <= 1.2
            )

    return rows


def _summarize_method(
    rows: list[dict[str, Any]],
    *,
    method: str,
    pass_key: str = "within_20pct",
) -> dict[str, Any]:
    accepted_rows = [row for row in rows if row["accepted"]]
    all_passes = sum(bool(row.get(pass_key)) for row in rows)
    accepted_passes = sum(bool(row.get(pass_key)) for row in accepted_rows)
    total = len(rows)
    accepted_total = len(accepted_rows)
    all_ci = _wilson_interval(all_passes, total)
    accepted_ci = _wilson_interval(accepted_passes, accepted_total)
    joint_ci = _wilson_interval(accepted_passes, total)
    return {
        "method": method,
        "label": METHOD_LABEL[method],
        "events": total,
        "all_pass_events": all_passes,
        "all_pass_rate": all_passes / total,
        "all_pass_wilson_95_low": all_ci[0],
        "all_pass_wilson_95_high": all_ci[1],
        "accepted_events": accepted_total,
        "acceptance_rate": accepted_total / total,
        "accepted_pass_events": accepted_passes,
        "accepted_only_pass_rate": (
            accepted_passes / accepted_total if accepted_total else None
        ),
        "accepted_only_wilson_95_low": (
            accepted_ci[0] if accepted_total else None
        ),
        "accepted_only_wilson_95_high": (
            accepted_ci[1] if accepted_total else None
        ),
        "joint_accurate_events": accepted_passes,
        "joint_accurate_coverage": accepted_passes / total,
        "joint_wilson_95_low": joint_ci[0],
        "joint_wilson_95_high": joint_ci[1],
    }


def summarize(
    rows: list[dict[str, Any]],
    *,
    apply_is: bool,
) -> list[dict[str, Any]]:
    summary = [
        _summarize_method(
            [row for row in rows if row["method"] == method],
            method=method,
        )
        for method in METHODS
    ]
    if apply_is:
        summary.insert(
            1,
            _summarize_method(
                [row for row in rows if row["method"] == "EnergyBridge"],
                method="EnergyBridge+IS",
                pass_key="within_20pct_is",
            ),
        )
    return summary


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"No rows for {path}")
    fieldnames: list[str] = []
    for row in rows:
        for key in row:
            if key not in fieldnames:
                fieldnames.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _write_report(
    path: Path,
    summary: list[dict[str, Any]],
    *,
    closed_loop: bool,
) -> None:
    lines = [
        "# Same-cohort capacity and consent rerun",
        "",
        "Consent decisions are joined event by event to guarded-P70 capacity",
        "reports over the same physical cohort.",
        "",
        "| Method | Accepted-only accuracy | Acceptance rate | Overall accurate coverage |",
        "|---|---:|---:|---:|",
    ]
    for row in summary:
        accepted_only = row["accepted_only_pass_rate"]
        accepted_only_text = (
            f"{100 * accepted_only:.1f}%" if accepted_only is not None else "NA"
        )
        lines.append(
            f"| {row['label']} | {accepted_only_text} | "
            f"{100 * row['acceptance_rate']:.1f}% | "
            f"{100 * row['joint_accurate_coverage']:.1f}% |"
        )
    lines.extend(
        [
            "",
            "Joint coverage is the directly observed fraction that is both accepted",
            "and within the manuscript target band; it equals acceptance rate times",
            "accepted-only pass rate up to rounding.",
            "",
        ]
    )
    if closed_loop:
        lines.extend(
            [
                "This daily matrix was freshly simulated: consent selected the",
                "controller or fallback branch before physical execution.",
                "",
            ]
        )
    else:
        lines.extend(
            [
                "This remains a frozen-artifact replay: revised consent does not",
                "feed back into controller execution or later household state.",
                "",
            ]
        )
    path.write_text("\n".join(lines), encoding="utf-8")


def _plot_summary(path: Path, summary: list[dict[str, Any]]) -> None:
    """Write a paper-ready view of conditional accuracy and joint coverage."""
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np

    rows = [row for row in summary if row["method"] != "EnergyBridge+IS"]
    labels = [str(row["label"]) for row in rows]
    y = np.arange(len(rows))
    colors = ["#2b6cb0", "#805ad5", "#319795", "#dd6b20", "#c53030"]

    fig, axes = plt.subplots(1, 2, figsize=(10.2, 4.0), sharey=True)
    panels = [
        (
            "Accepted-only accuracy",
            "accepted_only_pass_rate",
            "accepted_only_wilson_95_low",
            "accepted_only_wilson_95_high",
        ),
        (
            "Joint accurate coverage",
            "joint_accurate_coverage",
            "joint_wilson_95_low",
            "joint_wilson_95_high",
        ),
    ]
    for ax, (title, value_key, low_key, high_key) in zip(axes, panels):
        values = np.asarray([100.0 * float(row[value_key]) for row in rows])
        lows = np.asarray([100.0 * float(row[low_key]) for row in rows])
        highs = np.asarray([100.0 * float(row[high_key]) for row in rows])
        errors = np.vstack((values - lows, highs - values))
        ax.barh(y, values, color=colors, alpha=0.9, height=0.62)
        ax.errorbar(
            values,
            y,
            xerr=errors,
            fmt="none",
            ecolor="#2d3748",
            elinewidth=1.0,
            capsize=3,
        )
        ax.set_xlim(0.0, 105.0)
        ax.set_xlabel("Events within 0.8--1.2 band (%)")
        ax.set_title(title)
        ax.grid(axis="x", linestyle=":", linewidth=0.7, alpha=0.6)
        ax.set_axisbelow(True)
        for index, (value, row) in enumerate(zip(values, rows)):
            annotation = f"{value:.1f}%"
            if value_key == "accepted_only_pass_rate":
                annotation += (
                    f"  (n={row['accepted_events']}, "
                    f"accept={100 * row['acceptance_rate']:.1f}%)"
                )
            else:
                annotation += f"  ({row['joint_accurate_events']}/{row['events']})"
            ax.text(
                min(value + 1.2, 101.0),
                index,
                annotation,
                va="center",
                ha="left" if value <= 78.0 else "right",
                fontsize=8.3,
            )

    axes[0].set_yticks(y, labels)
    axes[0].invert_yaxis()
    fig.suptitle(
        "June-to-July capacity reporting under method-neutral simulated consent",
        fontsize=11.5,
    )
    fig.tight_layout()
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument(
        "--daily-summary",
        type=Path,
        help=(
            "Analyze a freshly simulated daily matrix's "
            "daily_dr_memory_summary_with_counterfactual.json instead of the "
            "frozen 70-event main benchmark."
        ),
    )
    parser.add_argument(
        "--pool-daily-summary",
        type=Path,
        help=(
            "Build every method's retrieval pool from a freshly simulated "
            "counterfactual-postprocessed daily summary. Requires "
            "--daily-summary."
        ),
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--apply-is", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.daily_summary is not None:
        rows = run_daily_closed_loop(
            args.daily_summary.resolve(),
            apply_is=args.apply_is,
            pool_summary_path=(
                args.pool_daily_summary.resolve()
                if args.pool_daily_summary is not None
                else None
            ),
        )
        cohort = "fresh closed-loop daily matrix"
        consent_policy = "fresh closed-loop method-neutral gate for every method"
        capacity_pool = (
            "fresh counterfactual-postprocessed daily matrix"
            if args.pool_daily_summary is not None
            else "committed same-method June memory"
        )
    else:
        if args.pool_daily_summary is not None:
            raise ValueError("--pool-daily-summary requires --daily-summary")
        rows = run_joined_replay(args.input_dir.resolve(), apply_is=args.apply_is)
        cohort = "5 households x 7 events x 2 regions per method"
        consent_policy = (
            "fresh method-neutral replay for traditional controllers; "
            "stored benchmark gate for EnergyBridge and HEMA"
        )
        capacity_pool = "committed same-method June memory"
    summary = summarize(rows, apply_is=args.apply_is)
    (output_dir / "capacity_consent_joined_events.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    csv_rows = [
        {
            **row,
            "neighbors_json": json.dumps(row.pop("neighbors"), sort_keys=True),
        }
        for row in (dict(item) for item in rows)
    ]
    _write_csv(output_dir / "capacity_consent_joined_events.csv", csv_rows)
    paper_summary = [
        {
            "method": row["label"],
            "accepted_only_accuracy": row["accepted_only_pass_rate"],
            "acceptance_rate": row["acceptance_rate"],
            "overall_accurate_coverage": row["joint_accurate_coverage"],
        }
        for row in summary
    ]
    _write_csv(
        output_dir / "capacity_consent_joined_summary.csv",
        paper_summary,
    )
    (output_dir / "capacity_consent_joined_summary.json").write_text(
        json.dumps(
            {
                "metric_ratio": "actual_shed_kwh / reported_capacity_kwh",
                "target_band": [0.8, 1.2],
                "capacity_policy": "top5 same-method June memory, deterministic guarded P70",
                "capacity_pool": capacity_pool,
                "consent_policy": consent_policy,
                "cohort": cohort,
                "closed_loop": args.daily_summary is not None,
                "rows": summary,
            },
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    _write_report(
        output_dir / "README.md",
        summary,
        closed_loop=args.daily_summary is not None,
    )
    _plot_summary(output_dir / "capacity_consent_joined.pdf", summary)
    print((output_dir / "README.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
