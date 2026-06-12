"""State adapter for the benchmark MPC baseline.

The adapter intentionally exposes a plain dict so the final objective can be
swapped without touching the EnergyPlus callback or appliance simulator.
"""

from __future__ import annotations

from typing import Any


def build_mpc_state(
    *,
    sim_h: float,
    hod: float,
    day_idx: int,
    temp_c: float | None,
    outdoor_temp_c: float | None,
    current_setpoint_c: float | None,
    vpp_event: dict | None,
    vpp_target_kwh: float | None,
    appliance_config: dict,
    appliance_suite,
    history: dict | None = None,
) -> dict:
    """Collect safely available benchmark state for candidate scoring."""
    status_lines: list[str] = []
    appliance_results: dict[str, Any] = {}
    appliance_vpp_summary: dict[str, Any] = {}

    if appliance_suite is not None:
        try:
            status_lines = list(appliance_suite.status_lines(sim_h))
        except Exception:
            status_lines = []
        try:
            appliance_results = dict(appliance_suite.all_results())
        except Exception:
            appliance_results = {}
        try:
            appliance_vpp_summary = dict(appliance_suite.vpp_day_summary(day_idx))
        except Exception:
            appliance_vpp_summary = {}

    return {
        "sim_h": float(sim_h),
        "hod": float(hod),
        "day_idx": int(day_idx),
        "temp_c": temp_c,
        "outdoor_temp_c": outdoor_temp_c,
        "current_setpoint_c": current_setpoint_c,
        "vpp_event": dict(vpp_event) if isinstance(vpp_event, dict) else None,
        "vpp_active": bool(vpp_event),
        "vpp_target_kwh": vpp_target_kwh,
        "vpp_start_h": vpp_event.get("trigger_h") if isinstance(vpp_event, dict) else None,
        "vpp_end_h": vpp_event.get("end_h") if isinstance(vpp_event, dict) else None,
        "appliance_config": dict(appliance_config or {}),
        "appliance_status_lines": status_lines,
        "appliance_results": appliance_results,
        # This is an observed simulator summary, not a predictive model.
        "appliance_vpp_summary": appliance_vpp_summary,
        "history": dict(history or {}),
    }

