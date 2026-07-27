#!/usr/bin/env python3
"""Replay frozen traditional-controller proposals through one consent interface."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


BENCHMARK_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BENCHMARK_DIR.parent.parent
if str(BENCHMARK_DIR) not in sys.path:
    sys.path.insert(0, str(BENCHMARK_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# The named mode selects the deterministic, method-agnostic consent model.  It
# must be set before replay so no acceptance LLM calls can enter the comparison.
os.environ["ENERGYBRIDGE_VPP_ACCEPTANCE_GATE"] = "method_neutral_v1"
os.environ["ENERGYBRIDGE_ROLEPLAY_ACCEPTANCE_GATE_USE_LLM"] = "0"

import family_runner as fr  # noqa: E402
from energybridge.roleplay.households import (  # noqa: E402
    load_household_config,
    load_household_member_personas,
)
from run_multi_user_household import _build_physical_household_persona  # noqa: E402


METHODS = ("mpc_dynamic", "rule_milp", "rl_ppo_pref_v2")
DEFAULT_INPUT_DIR = PROJECT_ROOT / "paper_results" / "01_main_household_5x2_final"
DEFAULT_OUTPUT_DIR = (
    PROJECT_ROOT / "paper_results" / "17_traditional_acceptance_method_neutral"
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _reference_plan(gate: dict[str, Any]) -> dict[str, Any]:
    diagnostics = gate.get("adaptability_diagnostics") or {}
    similarity = diagnostics.get("reference_similarity") or {}
    if not similarity:
        for key, value in diagnostics.items():
            if str(key).endswith("_similarity") and isinstance(value, dict):
                similarity = value
                break
    for key in ("reference_plan", "mpc_plan", "rule_milp_plan"):
        value = similarity.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _event_key(event: dict[str, Any]) -> str:
    return str(event.get("id") or event.get("event_id") or "")


def _wilson_interval(accepted: int, total: int, z: float = 1.959963984540054) -> tuple[float, float]:
    if total <= 0:
        return 0.0, 0.0
    p = accepted / total
    denominator = 1.0 + z * z / total
    centre = (p + z * z / (2.0 * total)) / denominator
    margin = z * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total)) / denominator
    return max(0.0, centre - margin), min(1.0, centre + margin)


def _aggregate(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(str(row[field]) for field in fields)].append(row)
    result: list[dict[str, Any]] = []
    for key, items in sorted(groups.items()):
        accepted = sum(bool(item["accepted_new"]) for item in items)
        old_accepted = sum(bool(item["accepted_old"]) for item in items)
        total = len(items)
        lo, hi = _wilson_interval(accepted, total)
        record = {field: value for field, value in zip(fields, key)}
        record.update(
            {
                "accepted_events": accepted,
                "total_events": total,
                "acceptance_rate": round(accepted / total, 6),
                "acceptance_rate_pct": round(100.0 * accepted / total, 2),
                "wilson_95_low_pct": round(100.0 * lo, 2),
                "wilson_95_high_pct": round(100.0 * hi, 2),
                "mean_acceptance_probability": round(
                    sum(float(item["acceptance_probability_new"]) for item in items) / total,
                    6,
                ),
                "old_accepted_events": old_accepted,
                "old_acceptance_rate_pct": round(100.0 * old_accepted / total, 2),
            }
        )
        result.append(record)
    return result


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"No rows for {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _markdown_table(rows: list[dict[str, Any]]) -> str:
    labels = {
        "mpc_dynamic": "MPC (Dynamic)",
        "rule_milp": "Rule+MILP",
        "rl_ppo_pref_v2": "RL (PPO Pref-v2)",
    }
    lines = [
        "| Region | Method | Accepted / Events | Acceptance rate | Mean modeled probability | 95% Wilson CI |",
        "|---|---|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['region']} | {labels.get(row['method'], row['method'])} | "
            f"{row['accepted_events']} / {row['total_events']} | "
            f"{row['acceptance_rate_pct']:.2f}% | "
            f"{100.0 * float(row['mean_acceptance_probability']):.2f}% | "
            f"[{row['wilson_95_low_pct']:.2f}%, {row['wilson_95_high_pct']:.2f}%] |"
        )
    return "\n".join(lines)


def replay(input_dir: Path) -> list[dict[str, Any]]:
    event_rows: list[dict[str, Any]] = []
    persona_cache: dict[str, dict[str, Any]] = {}
    for region in ("Tianjin", "Germany"):
        summary_path = input_dir / (
            f"household_matrix_summary_{region.lower()}_7days_H6_cap76_merged.json"
        )
        summary_rows = _read_json(summary_path)
        selected = [row for row in summary_rows if row.get("method") in METHODS]
        for summary_row in selected:
            household_id = str(summary_row.get("household_id") or summary_row.get("persona_id"))
            if household_id not in persona_cache:
                household = load_household_config(household_id)
                members = load_household_member_personas(household)
                persona_cache[household_id] = _build_physical_household_persona(
                    household,
                    members,
                    days=7,
                )
            persona = persona_cache[household_id]
            appliance_config = persona.get("appliances") or {}
            result_path = Path(str(summary_row["output_dir"])) / "benchmark_result.json"
            run = _read_json(result_path)
            event_log = list(run.get("vpp_event_log") or [])
            event_by_id = {_event_key(event): event for event in event_log}
            gate_events = list(run.get("vpp_plan_gate_events") or [])
            if len(gate_events) != 7:
                raise ValueError(f"Expected 7 gates, found {len(gate_events)} in {result_path}")
            for index, old_gate in enumerate(gate_events):
                event_id = _event_key(old_gate)
                event = event_by_id.get(event_id)
                if event is None:
                    raise KeyError(f"Missing event {event_id} in {result_path}")
                controller_plan = old_gate.get("controller_proposed_plan") or old_gate.get("proposed_plan") or {}
                default_plan = old_gate.get("default_plan") or {}
                neutral_plan = fr._traditional_method_neutral_acceptance_plan(
                    controller_plan,
                    default_plan=default_plan,
                    event=event,
                )
                new_gate = fr._evaluate_vpp_plan_acceptance_gate(
                    method=str(summary_row["method"]),
                    persona_config=persona,
                    appliance_config=appliance_config,
                    event=event,
                    proposed_plan=neutral_plan,
                    default_plan=default_plan,
                    rule_milp_plan=_reference_plan(old_gate),
                    past_events=event_log[:index],
                    user_preference_text=str(event.get("user_input") or ""),
                    current_hod=float(event.get("trigger_h", 0.0)),
                )
                if abs(float(new_gate["stable_draw"]) - float(old_gate["stable_draw"])) > 1e-6:
                    raise ValueError(f"Stable draw changed for {household_id}/{event_id}")
                event_rows.append(
                    {
                        "region": region,
                        "household_id": household_id,
                        "method": str(summary_row["method"]),
                        "event_id": event_id,
                        "day": int(event.get("day", index + 1)),
                        "accepted_new": bool(new_gate["accepted"]),
                        "acceptance_probability_new": float(new_gate["acceptance_probability"]),
                        "stable_draw": float(new_gate["stable_draw"]),
                        "accepted_old": bool(old_gate.get("accepted")),
                        "acceptance_probability_old": float(old_gate.get("acceptance_probability", 0.0)),
                        "proposed_setpoint_c": (new_gate.get("intrusion") or {}).get("proposed_setpoint_c"),
                        "comfort_excess_c": (new_gate.get("intrusion") or {}).get("comfort_excess_c"),
                        "hvac_off": bool((new_gate.get("intrusion") or {}).get("hvac_off")),
                        "changed_service_count": (new_gate.get("intrusion") or {}).get("changed_service_count"),
                        "vpp_conflict_count": len((new_gate.get("intrusion") or {}).get("vpp_conflicts") or []),
                        "acceptance_interface": "method_neutral_action_summary_v1",
                        "source_result": str(result_path),
                    }
                )
    expected = 2 * 5 * len(METHODS) * 7
    if len(event_rows) != expected:
        raise ValueError(f"Expected {expected} replayed events, found {len(event_rows)}")
    return event_rows


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--latex-fig-dir", type=Path, default=None)
    args = parser.parse_args()

    events = replay(args.input_dir.resolve())
    by_region_method = _aggregate(events, ("region", "method"))
    by_method = _aggregate(events, ("method",))
    by_household = _aggregate(events, ("region", "household_id", "method"))
    output_dir = args.output_dir.resolve()
    _write_csv(output_dir / "traditional_acceptance_event_level.csv", events)
    _write_csv(output_dir / "traditional_acceptance_by_region_method.csv", by_region_method)
    _write_csv(output_dir / "traditional_acceptance_by_method.csv", by_method)
    _write_csv(output_dir / "traditional_acceptance_by_household.csv", by_household)

    report = "\n".join(
        [
            "# Traditional-controller acceptance under a method-neutral interface",
            "",
            "Frozen replay over 5 households x 7 daily VPP events x 2 regions. The same stored",
            "physical proposals, household personas/calendars, event contexts, and deterministic",
            "event-level draws are retained. Only the controller-output presentation is normalized.",
            "Rates are simulated household acceptance, not observed human-subject acceptance.",
            "",
            _markdown_table(by_region_method),
            "",
            "## Two-region aggregate",
            "",
            _markdown_table([{"region": "Both", **row} for row in by_method]),
            "",
        ]
    )
    (output_dir / "traditional_acceptance_table.md").write_text(report, encoding="utf-8")

    if args.latex_fig_dir is not None:
        target = args.latex_fig_dir.resolve()
        target.mkdir(parents=True, exist_ok=True)
        _write_csv(target / "traditional_acceptance_rate_table.csv", by_region_method)
        (target / "traditional_acceptance_rate_table.md").write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    main()
