"""Adapter from real VPP-1 outputs to EnergyBridge internal grid signal."""

from __future__ import annotations

import sys
from pathlib import Path

from energybridge.grid.vpp_1.schemas import EnergyBridgeGridDemand, VPP1RawSignal, VPPContext


def _ensure_vpp1_import_path() -> None:
    project_root = Path(__file__).resolve().parents[3]
    vpp1_src = project_root / "VPP-1" / "src"
    if str(vpp1_src) not in sys.path:
        sys.path.insert(0, str(vpp1_src))


def adapt_vpp1_signal(raw_signal: VPP1RawSignal) -> EnergyBridgeGridDemand:
    return {
        "type": str(raw_signal.get("eventCode", "NORMAL")),
        "start_time": str(raw_signal.get("windowStart", "")),
        "end_time": str(raw_signal.get("windowEnd", "")),
        "duration_minutes": int(raw_signal.get("durationMinutes", 60) or 60),
        "target_reduction_kw": float(raw_signal.get("reductionTargetKw", 0.0) or 0.0),
        "price_level": str(raw_signal.get("tariff", "normal")).lower(),
    }


def load_vpp1_dispatch(mode: str) -> dict:
    _ensure_vpp1_import_path()

    from vpp_1.simulation.scenario_config import create_default_scenario_config  # type: ignore[reportMissingImports]
    from vpp_1.simulation.vpp_1_runner import VPP1Runner  # type: ignore[reportMissingImports]

    runner = VPP1Runner(create_default_scenario_config())
    return runner.run_once(mode=mode)


def extract_vpp_context_from_result(
    vpp_result: dict,
) -> VPPContext:
    task = dict(vpp_result.get("task", {}))
    query = dict(vpp_result.get("query", {}))
    query_window = dict(query.get("query_window", {}))
    requested_assessment = dict(query.get("requested_assessment", {}))

    task_type = str(task.get("task_type", "INVITATION_DEMAND_RESPONSE"))
    trigger_reason = str(task.get("trigger_reason", "REGIONAL_PEAK_LOAD"))
    time_scale = str(task.get("time_scale", "DAY_AHEAD"))

    vpp_context: VPPContext = {
        "vpp_task_id": str(task.get("task_id", "")),
        "vpp_query_id": str(query.get("query_id", "")),
        "vpp_task_type": task_type,
        "vpp_time_scale": time_scale,
        "vpp_trigger_reason": trigger_reason,
        "vpp_start_time": str(query_window.get("start_time", task.get("start_time", ""))),
        "vpp_end_time": str(query_window.get("end_time", task.get("end_time", ""))),
        "vpp_notice_minutes": int(task.get("notice_minutes", 0) or 0),
        "vpp_duration_minutes": int(query_window.get("duration_minutes", task.get("duration_minutes", 60)) or 60),
        "vpp_required_capacity_kw": float(task.get("required_capacity_kw", 0.0) or 0.0),
        "vpp_declaration_deadline": str(task.get("declaration_deadline", "") or ""),
        "vpp_response_direction": str(requested_assessment.get("response_direction", "")),
        "vpp_capacity_scope": "upstream_total_capacity",
    }
    return vpp_context
