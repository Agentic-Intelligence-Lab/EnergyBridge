"""Adapt EnergyBridge's ApplianceSuite to the reference capacity estimator."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .capacity_estimator import assess_vpp_request


def suite_capacity_inputs(suite: Any, sim_h: float, dt_minutes: float = 10.0) -> tuple[dict, dict]:
    """Build current-state inputs expected by the reference estimator."""
    now = datetime(2026, 7, 1) + timedelta(hours=sim_h)
    observation: dict[str, Any] = {
        "timestamp": now.isoformat(sep=" "),
        "time_step_minutes": dt_minutes,
    }
    devices: dict[str, dict[str, Any]] = {}
    day_idx = int(sim_h // 24)
    hour = sim_h % 24
    current_powers = suite._last_powers

    ev = suite._ev
    devices["ev"] = {
        "enabled": ev.present,
        "type": "ev_charger",
        "rated_power_kw": ev.charger_kw,
        "battery_capacity_kwh": ev.capacity_kwh,
        "target_soc": ev.target_soc,
        "charging_efficiency": ev.efficiency,
        "arrival_hour": ev.arrival_h,
        "departure_hour": ev.departure_h,
        "stop_at_target": True,
    }
    observation.update({
        "ev_soc": ev._soc,
        "ev_target_soc": ev.target_soc,
        "ev_at_home": ev._is_home(sim_h % 24),
        "ev_power_kw": float(current_powers.get("ev", 0.0)),
    })

    water_heater = suite._water_heater
    devices["water_heater"] = {
        "enabled": water_heater.present,
        "type": "electric_water_heater",
        "rated_power_kw": water_heater.rated_kw,
        "tank_volume_l": 120.0,
        "setpoint_c": 55.0,
        "minimum_temperature_c": 42.0,
        "ambient_temperature_c": 20.0,
        "thermal_efficiency": 1.0,
        "loss_coefficient_per_hour": 0.02,
    }
    observation.update({
        "water_heater_temperature_c": 55.0,
        "water_heater_setpoint_c": 55.0,
        "water_heater_power_kw": float(current_powers.get("water_heater", 0.0)),
    })

    for name, appliance in suite._shiftable.items():
        record = appliance._days.get(day_idx)
        skipped = appliance._day_skipped.get(day_idx, False)
        earliest_start = appliance.earliest_h
        latest_finish = appliance.latest_h
        if appliance._overnight:
            if hour >= appliance.earliest_h:
                latest_finish += 24.0
            else:
                earliest_start = 0.0
        if not appliance.present or skipped:
            state = "idle"
        elif record is not None and record.completed:
            state = "finished"
        elif record is not None and record.run_start_abs_h is not None:
            state = "running"
        else:
            state = "waiting"
        devices[name] = {
            "enabled": appliance.present,
            "type": "task_appliance",
            "rated_power_kw": appliance.power_kw,
            "cycle_duration_minutes": appliance.duration_h * 60.0,
            "earliest_start_hour": earliest_start,
            "latest_finish_hour": latest_finish,
            "interruptible": False,
        }
        observation[f"{name}_state"] = state
        observation[f"{name}_power_kw"] = float(current_powers.get(name, 0.0))

    config = {
        "simulation": {"start_datetime": "2026-07-01 00:00:00", "time_step_minutes": dt_minutes},
        "devices": devices,
    }
    return observation, config


def assess_suite_vpp_request(
    suite: Any,
    sim_h: float,
    target_kw: float,
    duration_minutes: float = 60.0,
    household_prior: dict | None = None,
) -> dict:
    """Return a VPP-ready capacity assessment for the current household state."""
    observation, config = suite_capacity_inputs(suite, sim_h)
    result = assess_vpp_request(
        observation=observation,
        device_config=config,
        request={"direction": "down", "target_kw": target_kw, "duration_minutes": duration_minutes},
        household_prior=household_prior,
    )
    result["potential"]["adapter_basis"] = "current_appliance_suite_state"
    return result
