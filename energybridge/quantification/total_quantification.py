"""Reference A3 action-conditioned 90% DR capacity quantification."""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from statistics import mean
from typing import Any, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parents[2]
REFERENCE_ROOT = Path(
    os.getenv(
        "ENERGYBRIDGE_REFERENCE_QUANTIFICATION_ROOT",
        str(PROJECT_ROOT / "data" / "reference_quantification"),
    )
)
DEFAULT_PREDICTIONS = (
    REFERENCE_ROOT
    / "pbase_method_benchmark/A3_conformal_weather_adjusted/predictions_2026-06-15_to_2026-06-17.csv"
)
DEFAULT_STRATEGY = REFERENCE_ROOT / "Total_Quantification/configs/example_evening_shed_strategy.json"
DT_HOURS = 10.0 / 60.0
BASELINE_UNCERTAINTY_SHARE = 1.0
REFERENCE_CAPACITY_MULTIPLIER = 0.9
VPP_TARGET_CAPACITY_MULTIPLIER = 1.2

DEVICE_POWER_KEYS = {
    "ev": "baseline_ev_power_expected_kw",
    "ewh": "baseline_ewh_power_expected_kw",
    "dishwasher": "baseline_dishwasher_power_expected_kw",
    "washer": "baseline_washer_power_expected_kw",
    "dryer": "baseline_dryer_power_expected_kw",
    "hvac": "baseline_hvac_power_expected_kw",
}
DEFAULT_EFFECTIVENESS = {
    "ev": 1.0, "dishwasher": 1.0, "washer": 1.0, "dryer": 1.0, "ewh": 0.75, "hvac": 1.0,
}
DEFAULT_RELIABILITY = {
    "ev": 0.90, "dishwasher": 0.85, "washer": 0.85, "dryer": 0.85, "ewh": 0.75, "hvac": 0.50,
}


def quantify_agent_vpp_events(
    vpp_events: Sequence[Mapping[str, Any]],
    predictions_path: Path = DEFAULT_PREDICTIONS,
    strategy_path: Path = DEFAULT_STRATEGY,
) -> dict[str, dict[str, Any]]:
    """Apply the reference Total_Quantification formula to Agent VPP windows."""
    predictions = _read_csv(predictions_path)
    strategy = json.loads(strategy_path.read_text(encoding="utf-8"))
    source_dates = sorted({datetime.fromisoformat(row["timestamp"]).date() for row in predictions})
    output: dict[str, dict[str, Any]] = {}
    if not source_dates:
        return output
    for index, event in enumerate(vpp_events):
        source_index = index % len(source_dates)
        source_start = datetime.combine(source_dates[source_index], datetime.min.time()) + timedelta(
            hours=float(event["trigger_h"]) % 24
        )
        duration_h = float(event["end_h"]) - float(event["trigger_h"])
        source_end = source_start + timedelta(hours=duration_h)
        event_rows = [
            row for row in predictions
            if source_start <= datetime.fromisoformat(row["timestamp"]) < source_end
        ]
        quantified = [_quantify_row(row, strategy.get("actions", [])) for row in event_rows]
        summary = _summarize_event(quantified, source_start, source_end)
        if index >= len(source_dates):
            summary["source_reused"] = True
            summary["source_cycle_index"] = source_index + 1
            summary["source_cycle_size"] = len(source_dates)
        output[str(event["id"])] = summary
    return output


def _quantify_row(row: Mapping[str, Any], actions: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    q05, q50, q95 = _f(row.get("q05_kw")), _f(row.get("q50_kw")), _f(row.get("q95_kw"))
    expected_total = discounted_total = 0.0
    out: dict[str, float] = {
        "p_base_q05_kw": round(q05, 6),
        "p_base_q50_kw": round(q50, 6),
        "p_base_q95_kw": round(q95, 6),
    }
    for action in actions:
        device = _normalize_device(str(action.get("device", "")))
        if device not in DEVICE_POWER_KEYS:
            continue
        baseline_power = _f(row.get(DEVICE_POWER_KEYS[device], 0.0))
        expected = max(
            0.0,
            baseline_power
            * _action_response_factor(action)
            * _f(action.get("effectiveness", DEFAULT_EFFECTIVENESS.get(device, 1.0))),
        )
        expected_total += expected
        discounted = expected * _clamp(
            _f(action.get("reliability", DEFAULT_RELIABILITY.get(device, 0.8))), 0.0, 1.0
        )
        discounted_total += discounted
        out[f"{device}_expected_shed_kw"] = round(expected, 6)
        out[f"{device}_discounted_shed_kw"] = round(discounted, 6)
    conservative = max(0.0, discounted_total - BASELINE_UNCERTAINTY_SHARE * max(0.0, q50 - q05))
    out.update({
        "expected_shed_kw": round(expected_total, 6),
        "discounted_shed_before_pbase_margin_kw": round(discounted_total, 6),
        "reported_shed_90_kw": round(conservative, 6),
        "p_dr_hat_q50_kw": round(max(0.0, q50 - expected_total), 6),
        "p_dr_hat_conservative_kw": round(max(0.0, q50 - conservative), 6),
        "expected_shed_kwh_step": round(expected_total * DT_HOURS, 6),
        "reported_shed_90_kwh_step": round(conservative * DT_HOURS, 6),
    })
    return out


def _summarize_event(rows: Sequence[Mapping[str, float]], start: datetime, end: datetime) -> dict[str, Any]:
    if not rows:
        return {"status": "not_computed", "reason": "No A3 prediction rows matched the event window"}
    duration_hours = round(len(rows) * DT_HOURS, 6)
    avg_base_q50_kw = round(mean(row["p_base_q50_kw"] for row in rows), 6)
    avg_reported_capacity_90_kw = round(mean(row["reported_shed_90_kw"] for row in rows), 6)
    vpp_target_capacity_kw = round(
        avg_reported_capacity_90_kw
        * VPP_TARGET_CAPACITY_MULTIPLIER
        / REFERENCE_CAPACITY_MULTIPLIER,
        6,
    )
    vpp_target_kwh = round(
        max(0.1, avg_base_q50_kw * duration_hours - vpp_target_capacity_kw * duration_hours),
        6,
    )
    summary = {
        "status": "computed",
        "method": "reference_A3_conformal_action_conditioned_90",
        "source_start": start.isoformat(sep=" "),
        "source_end": end.isoformat(sep=" "),
        "rows": len(rows),
        "duration_hours": duration_hours,
        "avg_p_base_q05_kw": round(mean(row["p_base_q05_kw"] for row in rows), 6),
        "avg_p_base_q50_kw": avg_base_q50_kw,
        "avg_p_base_q95_kw": round(mean(row["p_base_q95_kw"] for row in rows), 6),
        "avg_expected_shed_kw": round(mean(row["expected_shed_kw"] for row in rows), 6),
        "avg_discounted_shed_before_pbase_margin_kw": round(
            mean(row["discounted_shed_before_pbase_margin_kw"] for row in rows), 6),
        "avg_reported_capacity_90_kw": avg_reported_capacity_90_kw,
        "firm_min_capacity_90_kw": round(min(row["reported_shed_90_kw"] for row in rows), 6),
        "peak_reported_capacity_90_kw": round(max(row["reported_shed_90_kw"] for row in rows), 6),
        "expected_shed_energy_kwh": round(sum(row["expected_shed_kwh_step"] for row in rows), 6),
        "reported_shed_90_energy_kwh": round(sum(row["reported_shed_90_kwh_step"] for row in rows), 6),
        "avg_p_dr_hat_q50_kw": round(mean(row["p_dr_hat_q50_kw"] for row in rows), 6),
        "avg_p_dr_hat_conservative_kw": round(
            mean(row["p_dr_hat_conservative_kw"] for row in rows), 6),
        "vpp_target_capacity_120_kw": vpp_target_capacity_kw,
        "vpp_target_capacity_multiplier": VPP_TARGET_CAPACITY_MULTIPLIER,
        "vpp_target_reference_multiplier": REFERENCE_CAPACITY_MULTIPLIER,
        "vpp_target_capacity_energy_kwh": round(vpp_target_capacity_kw * duration_hours, 6),
        "vpp_target_kwh": vpp_target_kwh,
        "vpp_target_source": "avg_reported_capacity_90_kw * (1.2 / 0.9)",
    }
    for device in DEVICE_POWER_KEYS:
        expected_key = f"{device}_expected_shed_kw"
        discounted_key = f"{device}_discounted_shed_kw"
        summary[f"{device}_avg_expected_shed_kw"] = round(mean(row.get(expected_key, 0.0) for row in rows), 6)
        summary[f"{device}_avg_discounted_shed_kw"] = round(
            mean(row.get(discounted_key, 0.0) for row in rows), 6)
        summary[f"{device}_expected_shed_energy_kwh"] = round(
            sum(row.get(expected_key, 0.0) * DT_HOURS for row in rows), 6)
    return summary


def _action_response_factor(action: Mapping[str, Any]) -> float:
    device = _normalize_device(str(action.get("device", "")))
    command = str(action.get("command", ""))
    if device == "hvac":
        if command in {"raise_cooling_setpoint", "lower_heating_setpoint"}:
            return _clamp(0.05 + 0.30 * max(0.0, _f(action.get("delta_c", 0.0))), 0.0, 0.65)
        return 1.0 if command in {"disable_hvac", "hvac_off"} else 0.0
    return 1.0


def _normalize_device(device: str) -> str:
    aliases = {
        "water_heater": "ewh", "electric_water_heater": "ewh",
        "clothes_washer": "washer", "clothes_dryer": "dryer",
    }
    return aliases.get(device.strip().lower(), device.strip().lower())


def _read_csv(path: Path) -> list[dict[str, Any]]:
    with path.open("r", newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _f(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
