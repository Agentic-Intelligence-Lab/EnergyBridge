#!/usr/bin/env python3
"""Compare MPC-EP horizon predictions with the realized EnergyPlus meter trace."""

from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
from typing import Any


def _read_facility_power_trace(run_dir: Path) -> list[dict[str, float]]:
    """Return timestep average facility power from ``eplusout.mtr``."""
    meter_path = run_dir / "eplusout.mtr"
    if not meter_path.exists():
        raise FileNotFoundError(f"missing meter file: {meter_path}")

    rows: list[dict[str, float]] = []
    current_window: tuple[float, float] | None = None
    facility_code: int | None = None
    in_data = False
    with meter_path.open(errors="ignore") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            if not in_data:
                parts = [part.strip() for part in line.split(",")]
                if (
                    len(parts) >= 3
                    and parts[0].isdigit()
                    and "Electricity:Facility" in line
                    and "!TimeStep" in line
                ):
                    facility_code = int(parts[0])
            if line.startswith("End of Data Dictionary"):
                in_data = True
                continue
            if not in_data:
                continue
            parts = [part.strip() for part in line.split(",")]
            if not parts or not parts[0].isdigit():
                continue
            code = int(parts[0])
            if code == 2 and len(parts) >= 8:
                day = int(parts[1])
                hour = int(parts[5])
                start_min = float(parts[6])
                end_min = float(parts[7])
                start_h = (day - 1) * 24.0 + (hour - 1) + start_min / 60.0
                end_h = (day - 1) * 24.0 + (hour - 1) + end_min / 60.0
                current_window = (start_h, end_h)
                continue
            if facility_code is None:
                facility_code = 9
            if code != facility_code or current_window is None or len(parts) < 2:
                continue
            try:
                energy_j = float(parts[1])
            except ValueError:
                continue
            start_h, end_h = current_window
            duration_h = max(1e-9, end_h - start_h)
            rows.append(
                {
                    "start_h": start_h,
                    "end_h": end_h,
                    "facility_kw": energy_j / 3_600_000.0 / duration_h,
                }
            )
    return rows


def _actual_power_for_window(trace: list[dict[str, float]], start_h: float, end_h: float) -> float | None:
    weighted_kwh = 0.0
    overlap_h_total = 0.0
    for row in trace:
        overlap_h = max(0.0, min(end_h, row["end_h"]) - max(start_h, row["start_h"]))
        if overlap_h <= 0.0:
            continue
        weighted_kwh += row["facility_kw"] * overlap_h
        overlap_h_total += overlap_h
    if overlap_h_total <= 0.0:
        return None
    return weighted_kwh / overlap_h_total


def _iter_mpc_ep_decisions(result: dict[str, Any]) -> list[dict[str, Any]]:
    decisions: list[dict[str, Any]] = []
    for event in result.get("vpp_event_log") or []:
        for decision in event.get("day_decisions") or []:
            terms = decision.get("objective_terms") or {}
            diagnostics = terms.get("diagnostics") or {}
            prediction = diagnostics.get("dynamic_model_prediction") or {}
            if prediction.get("model") != "energyplus_horizon_predictor_v1":
                continue
            stage_power = prediction.get("stage_total_power_kw") or []
            if not stage_power:
                continue
            decisions.append(
                {
                    "event_id": event.get("id"),
                    "decision_h": float(decision.get("h")),
                    "setpoint_c": decision.get("sp"),
                    "objective_total": terms.get("total"),
                    "predicted_stage_kw": [float(v) for v in stage_power],
                    "horizon_minutes": prediction.get("horizon_minutes"),
                    "idf_path": prediction.get("idf_path"),
                    "epw_path": prediction.get("epw_path"),
                    "replay_policy": prediction.get("replay_policy"),
                    "warmup_note": prediction.get("warmup_note"),
                }
            )
    return decisions


def diagnose_run(run_dir: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    result_path = run_dir / "benchmark_result.json"
    if not result_path.exists():
        raise FileNotFoundError(f"missing benchmark result: {result_path}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    trace = _read_facility_power_trace(run_dir)
    decisions = _iter_mpc_ep_decisions(result)
    rows: list[dict[str, Any]] = []
    errors: list[float] = []
    abs_errors: list[float] = []
    sq_errors: list[float] = []

    for decision in decisions:
        predicted = decision["predicted_stage_kw"]
        horizon_steps = len(predicted)
        step_h = (float(decision.get("horizon_minutes") or horizon_steps * 10.0) / 60.0) / horizon_steps
        actual = []
        for idx, pred_kw in enumerate(predicted):
            start_h = decision["decision_h"] + idx * step_h
            end_h = start_h + step_h
            actual_kw = _actual_power_for_window(trace, start_h, end_h)
            if actual_kw is None:
                continue
            err = pred_kw - actual_kw
            errors.append(err)
            abs_errors.append(abs(err))
            sq_errors.append(err * err)
            actual.append(actual_kw)
            rows.append(
                {
                    "run_name": run_dir.name,
                    "event_id": decision["event_id"],
                    "decision_h": round(decision["decision_h"], 6),
                    "step_index": idx + 1,
                    "window_start_h": round(start_h, 6),
                    "window_end_h": round(end_h, 6),
                    "setpoint_c": decision["setpoint_c"],
                    "predicted_kw": round(pred_kw, 6),
                    "actual_kw": round(actual_kw, 6),
                    "error_kw": round(err, 6),
                    "abs_error_kw": round(abs(err), 6),
                    "objective_total": decision["objective_total"],
                    "idf_path": decision.get("idf_path") or "",
                    "epw_path": decision.get("epw_path") or "",
                    "replay_policy": decision.get("replay_policy") or "",
                    "warmup_note": decision.get("warmup_note") or "",
                }
            )

    summary = {
        "run_name": run_dir.name,
        "method": result.get("method"),
        "decision_count": len(decisions),
        "comparison_points": len(errors),
        "mean_error_kw": sum(errors) / len(errors) if errors else None,
        "mae_kw": sum(abs_errors) / len(abs_errors) if abs_errors else None,
        "rmse_kw": math.sqrt(sum(sq_errors) / len(sq_errors)) if sq_errors else None,
        "mean_predicted_kw": (
            sum(row["predicted_kw"] for row in rows) / len(rows) if rows else None
        ),
        "mean_actual_kw": (
            sum(row["actual_kw"] for row in rows) / len(rows) if rows else None
        ),
    }
    return rows, summary


def _write_outputs(rows: list[dict[str, Any]], summary: dict[str, Any], out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    csv_path = out_dir / f"{summary['run_name']}_mpc_ep_prediction_alignment.csv"
    json_path = out_dir / f"{summary['run_name']}_mpc_ep_prediction_alignment.json"
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
    else:
        csv_path.write_text("", encoding="utf-8")
    json_path.write_text(
        json.dumps({"summary": summary, "rows": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[OK] {summary['run_name']} points={summary['comparison_points']} mae={summary['mae_kw']} rmse={summary['rmse_kw']}")
    print(f"     csv : {csv_path}")
    print(f"     json: {json_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dirs", nargs="+", type=Path, help="MPC-EP benchmark result directories")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("benchmark_results") / "diagnostics",
        help="Directory for diagnostic csv/json files.",
    )
    args = parser.parse_args()

    all_summaries: list[dict[str, Any]] = []
    for run_dir in args.run_dirs:
        rows, summary = diagnose_run(run_dir)
        _write_outputs(rows, summary, args.output_dir)
        all_summaries.append(summary)

    if len(all_summaries) > 1:
        combined_path = args.output_dir / "mpc_ep_prediction_alignment_summary.json"
        combined_path.write_text(json.dumps(all_summaries, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[OK] combined summary: {combined_path}")


if __name__ == "__main__":
    main()
