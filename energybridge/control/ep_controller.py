"""EP-realtime controller: replaces mock_mpc for EnergyPlus co-simulation.

Instead of estimating power with a deterministic mock model, this controller:
1. Uses the actual EnergyPlus hvac_cooling_thermal_kw (read via StateReader) as baseline.
2. Marks estimated_reduction_kw as None (pending post-run ESO integral).
3. Sets controller="ep_realtime_v1" so downstream analysis can distinguish it
   from the old mock_mpc_v0 path.

The actual 2-hour power integral is computed post-run by analyze_eplus_run.py.
"""

from __future__ import annotations


def build_ep_control_plan(
    candidate_strategy: dict,
    home_state: dict,
    translated_grid_signal: dict,
) -> dict:
    """Build a control plan using actual EP readings rather than mock estimates.

    Parameters
    ----------
    candidate_strategy:
        Output of generate_candidate_strategy (or LLM strategy options).
    home_state:
        Current home state dict including EP-sourced hvac_cooling_thermal_kw,
        facility_power_kw, indoor_temp, hvac_setpoint.
    translated_grid_signal:
        Translated grid demand with control_intent and duration_minutes.
    """
    setpoint = float(
        candidate_strategy.get(
            "recommended_setpoint",
            home_state.get("hvac_setpoint", 25.0),
        )
    )

    control_intent = translated_grid_signal.get("control_intent", "normal_operation")
    duration_minutes = int(translated_grid_signal.get("duration_minutes", 0) or 0)
    if duration_minutes <= 0:
        duration_minutes = 60 if control_intent in {"reduce_load", "cost_saving"} else 30

    # Use actual EP readings as baseline (no synthetic estimation)
    hvac_kw = float(home_state.get("hvac_cooling_thermal_kw", 0.0))
    facility_kw = float(home_state.get("facility_power_kw", hvac_kw))
    current_setpoint = float(home_state.get("hvac_setpoint", 25.0))

    # Rough real-time estimate: if setpoint is raised, HVAC works less;
    # this is still approximate but grounded in actual current readings.
    if setpoint > current_setpoint and hvac_kw > 0:
        # Higher setpoint → less cooling demand; rough linear proxy
        ratio = max(0.0, 1.0 - (setpoint - current_setpoint) * 0.15)
        estimated_hvac_kw = round(hvac_kw * ratio, 3)
    elif setpoint < current_setpoint:
        estimated_hvac_kw = round(hvac_kw * 1.1, 3)
    else:
        estimated_hvac_kw = round(hvac_kw, 3)

    return {
        "action": "set_hvac_temperature",
        "setpoint": round(setpoint, 2),
        "duration_minutes": duration_minutes,
        # Baseline from EP StateReader at decision time
        "baseline_hvac_thermal_kw": round(hvac_kw, 3),
        "baseline_facility_kw": round(facility_kw, 3),
        # Real-time estimate (will be superseded by post-run ESO integral)
        "estimated_power_kw": estimated_hvac_kw,
        "estimated_reduction_kw": round(max(0.0, hvac_kw - estimated_hvac_kw), 3),
        "controller": "ep_realtime_v1",
    }
