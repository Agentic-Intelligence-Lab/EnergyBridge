"""Adapter from real VPP-1 outputs to EnergyBridge internal grid signal."""

from __future__ import annotations

import sys
from pathlib import Path

from energybridge.grid.vpp_1.schemas import EnergyBridgeGridSignal, VPP1RawSignal


def _ensure_vpp1_import_path() -> None:
    project_root = Path(__file__).resolve().parents[3]
    vpp1_src = project_root / "VPP-1" / "src"
    if str(vpp1_src) not in sys.path:
        sys.path.insert(0, str(vpp1_src))


def adapt_vpp1_signal(raw_signal: VPP1RawSignal) -> EnergyBridgeGridSignal:
    return {
        "type": str(raw_signal.get("eventCode", "NORMAL")),
        "start_time": str(raw_signal.get("windowStart", "")),
        "end_time": str(raw_signal.get("windowEnd", "")),
        "target_reduction_kw": float(raw_signal.get("reductionTargetKw", 0.0) or 0.0),
        "price_level": str(raw_signal.get("tariff", "normal")).lower(),
    }


def load_vpp1_dispatch(mode: str) -> dict:
    _ensure_vpp1_import_path()

    from vpp_1.simulation.scenario_config import create_default_scenario_config
    from vpp_1.simulation.vpp_1_runner import VPP1Runner

    runner = VPP1Runner(create_default_scenario_config())
    return runner.run_once(mode=mode)


def derive_local_target_reduction_kw(vpp_task: dict, vpp_query: dict, home_state: dict) -> float:
    hvac_power_kw = float(home_state.get("hvac_power_kw", 2.0) or 2.0)
    task_type = str(vpp_task.get("task_type", "INVITATION_DEMAND_RESPONSE"))
    trigger_reason = str(vpp_task.get("trigger_reason", "REGIONAL_PEAK_LOAD"))
    duration_minutes = float(vpp_query.get("query_window", {}).get("duration_minutes", 30) or 30)

    base_ratio = 0.18
    if "EMERGENCY" in task_type:
        base_ratio = 0.32
    if trigger_reason in {"LOCAL_OVERLOAD", "POWER_SHORTAGE"}:
        base_ratio += 0.08
    if duration_minutes > 60:
        base_ratio -= 0.04

    local_target = hvac_power_kw * max(0.12, min(base_ratio, 0.45))
    return round(max(0.2, min(local_target, hvac_power_kw * 0.5)), 3)


def adapt_vpp1_result_to_grid_signal(
    vpp_result: dict,
    home_state: dict,
) -> tuple[EnergyBridgeGridSignal, dict, dict]:
    task = dict(vpp_result.get("task", {}))
    query = dict(vpp_result.get("query", {}))

    task_type = str(task.get("task_type", "INVITATION_DEMAND_RESPONSE"))
    trigger_reason = str(task.get("trigger_reason", "REGIONAL_PEAK_LOAD"))
    price_level = "normal"
    if trigger_reason == "PRICE_SIGNAL":
        price_level = "high"
    if "EMERGENCY" in task_type or trigger_reason in {"LOCAL_OVERLOAD", "POWER_SHORTAGE"}:
        price_level = "critical"
    elif trigger_reason == "REGIONAL_PEAK_LOAD":
        price_level = "high"

    local_target_reduction_kw = derive_local_target_reduction_kw(task, query, home_state)
    event_type = "EMERGENCY_DR" if "EMERGENCY" in task_type else "DR_EVENT"

    grid_signal: EnergyBridgeGridSignal = {
        "type": event_type,
        "start_time": str(query.get("query_window", {}).get("start_time", task.get("start_time", ""))),
        "end_time": str(query.get("query_window", {}).get("end_time", task.get("end_time", ""))),
        "target_reduction_kw": local_target_reduction_kw,
        "price_level": price_level,
        "vpp_task_id": str(task.get("task_id", "")),
        "vpp_query_id": str(query.get("query_id", "")),
        "vpp_task_type": task_type,
        "vpp_trigger_reason": trigger_reason,
        "vpp_required_capacity_kw": float(task.get("required_capacity_kw", 0.0) or 0.0),
        "vpp_target_query_capacity_kw": float(task.get("target_query_capacity_kw", 0.0) or 0.0),
        "vpp_local_target_basis": "derived_from_vpp1_task_and_local_hvac_capacity",
    }
    return grid_signal, task, query
