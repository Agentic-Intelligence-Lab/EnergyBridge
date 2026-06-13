"""Lightweight DR potential estimator for the EnergyBridge HEMS layer.

The estimator is intentionally rule based. It combines device-state feasibility
with optional baseline/counterfactual power, so a household agent can answer VPP
requests without training data.
"""

from __future__ import annotations

from datetime import datetime
from math import ceil
from typing import Any, Dict, Iterable, Mapping

WATER_CP_KJ_PER_KG_K = 4.186
EPS = 1e-9


def estimate_dr_potential(
    observation: Mapping[str, Any],
    device_config: Mapping[str, Any],
    baseline_power: Mapping[str, float] | None = None,
    horizon_steps: int = 1,
) -> Dict[str, Any]:
    """Estimate feasible DR capacity from the current HEMS observation.

    Args:
        observation: A row from the HEMS device layer observation/timeseries.
        device_config: Parsed appliance YAML config.
        baseline_power: Optional counterfactual power by device name or
            ``{device}_power_kw`` key. When present, baseline-limited shed is
            also reported.
        horizon_steps: Number of timesteps in the requested horizon.

    Returns:
        A JSON-serializable dictionary with total and per-device capacity.
    """
    dt_hours = _dt_hours(observation, device_config)
    timestamp = str(observation.get("timestamp", ""))
    now = _parse_timestamp(timestamp, device_config)
    devices_cfg = dict(device_config.get("devices", {}))
    baseline_power = baseline_power or {}

    devices: Dict[str, Dict[str, Any]] = {}
    for name, cfg_any in devices_cfg.items():
        cfg = dict(cfg_any or {})
        if not bool(cfg.get("enabled", True)):
            continue
        device_type = str(cfg.get("type", name))
        if device_type == "ev_charger":
            result = _estimate_ev(name, cfg, observation, baseline_power, now, dt_hours, horizon_steps)
        elif device_type == "electric_water_heater":
            result = _estimate_water_heater(name, cfg, observation, baseline_power, dt_hours, horizon_steps)
        elif device_type in {"hvac_cooling", "cooling_hvac"}:
            result = _estimate_hvac_cooling(name, cfg, observation, baseline_power, dt_hours, horizon_steps)
        elif device_type == "task_appliance":
            result = _estimate_task(name, cfg, observation, baseline_power, now, dt_hours, horizon_steps)
        else:
            result = _empty_device(name, [f"unsupported_type:{device_type}"])
        devices[name] = result

    total = _sum_devices(devices.values())
    return {
        "timestamp": timestamp,
        "method": "state_physical_with_optional_baseline",
        "dt_hours": dt_hours,
        "horizon_steps": max(1, int(horizon_steps)),
        "total": total,
        "devices": devices,
        "confidence": {"type": "deterministic_rules"},
    }


def assess_vpp_request(
    observation: Mapping[str, Any],
    device_config: Mapping[str, Any],
    request: Mapping[str, Any],
    baseline_forecast: Mapping[str, float] | None = None,
    household_prior: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Estimate how much capacity the household can safely commit to a VPP.

    The request schema is intentionally small:
    ``{"direction": "down"|"up", "target_kw": float, "duration_minutes": int}``.
    """
    dt_hours = _dt_hours(observation, device_config)
    duration_minutes = float(request.get("duration_minutes", dt_hours * 60.0))
    duration_hours = max(dt_hours, duration_minutes / 60.0)
    horizon_steps = max(1, int(ceil(duration_hours / dt_hours)))
    direction = str(request.get("direction", "down")).lower()
    target_kw = max(0.0, _to_float(request.get("target_kw", 0.0)))

    potential = estimate_dr_potential(
        observation=observation,
        device_config=device_config,
        baseline_power=baseline_forecast,
        horizon_steps=horizon_steps,
    )
    total = potential["total"]

    if direction in {"down", "shed", "reduce"}:
        immediate_kw = float(total["baseline_limited_shed_kw"] if baseline_forecast else total["shed_kw"])
        energy_limited_kw = _energy_limited_kw(float(total["shiftable_energy_kwh"]), duration_hours, immediate_kw)
    elif direction in {"up", "add", "increase"}:
        immediate_kw = float(total["add_kw"])
        energy_limited_kw = immediate_kw
    else:
        raise ValueError(f"Unsupported VPP request direction: {direction}")

    committable_kw = min(target_kw, immediate_kw, energy_limited_kw)
    success_probability = _success_probability(potential, household_prior, committable_kw, target_kw)
    safety_margin = _safety_margin(success_probability)
    recommended_bid_kw = committable_kw * safety_margin

    return {
        "request": {
            "direction": direction,
            "target_kw": target_kw,
            "duration_minutes": duration_minutes,
        },
        "assessment": {
            "committable_kw": round(committable_kw, 6),
            "success_probability": round(success_probability, 6),
            "expected_rebound_kwh": round(float(total["rebound_risk_kw"]) * duration_hours, 6),
            "main_constraints": _main_constraints(potential),
            "recommended_bid_kw": round(recommended_bid_kw, 6),
            "safety_margin": round(safety_margin, 6),
        },
        "potential": potential,
    }


def _estimate_ev(
    name: str,
    cfg: Mapping[str, Any],
    observation: Mapping[str, Any],
    baseline_power: Mapping[str, float],
    now: datetime,
    dt_hours: float,
    horizon_steps: int,
) -> Dict[str, Any]:
    rated_kw = _cfg_float(cfg, "rated_power_kw", 7.0)
    capacity_kwh = _cfg_float(cfg, "battery_capacity_kwh", 60.0)
    efficiency = max(EPS, _cfg_float(cfg, "charging_efficiency", 0.9))
    target_soc = _obs_float(observation, f"{name}_target_soc", _cfg_float(cfg, "target_soc", 0.8))
    soc_limit = target_soc if bool(cfg.get("stop_at_target", True)) else _cfg_float(cfg, "max_soc", 1.0)
    soc = _obs_float(observation, f"{name}_soc", _cfg_float(cfg, "initial_soc", 0.0))
    current_kw = _obs_float(observation, f"{name}_power_kw", 0.0)
    at_home = _obs_bool(observation, f"{name}_at_home", False)
    departure_hour = _cfg_float(cfg, "departure_hour", 7.0)
    arrival_hour = _cfg_float(cfg, "arrival_hour", 18.0)

    constraints = []
    if not at_home:
        constraints.append("ev_not_at_home")
    required_kwh = max(0.0, (soc_limit - soc) * capacity_kwh / efficiency)
    remaining_home_hours = _remaining_home_hours(_hour_decimal(now), arrival_hour, departure_hour, at_home)
    available_window_kwh = remaining_home_hours * rated_kw
    slack_kwh = max(0.0, available_window_kwh - required_kwh)
    horizon_kwh = max(dt_hours, horizon_steps * dt_hours) * max(0.0, current_kw)

    if required_kwh <= EPS:
        constraints.append("at_target_soc")
    if at_home and required_kwh > EPS and slack_kwh <= dt_hours * rated_kw + EPS:
        constraints.append("ev_deadline_margin_low")

    shed_kw = 0.0
    if at_home and current_kw > EPS and slack_kwh > EPS:
        shed_kw = min(current_kw, slack_kwh / dt_hours)
    add_kw = 0.0
    if at_home and required_kwh > EPS:
        max_charge_now_kw = min(rated_kw, required_kwh / dt_hours)
        add_kw = max(0.0, max_charge_now_kw - current_kw)

    shiftable_kwh = min(slack_kwh, horizon_kwh) if shed_kw > EPS else 0.0
    baseline_limited_shed_kw = min(shed_kw, _baseline_shed(name, baseline_power, current_kw))
    reliability = 0.95
    if "ev_deadline_margin_low" in constraints:
        reliability -= 0.25
    if "ev_not_at_home" in constraints or "at_target_soc" in constraints:
        reliability -= 0.05

    result = _device_result(
        shed_kw=shed_kw,
        add_kw=add_kw,
        shiftable_energy_kwh=shiftable_kwh,
        rebound_risk_kw=shed_kw,
        constraints=constraints,
        reliability=reliability,
        baseline_limited_shed_kw=baseline_limited_shed_kw,
    )
    result["state"] = {
        "soc": round(soc, 6),
        "target_soc": round(target_soc, 6),
        "required_kwh": round(required_kwh, 6),
        "slack_kwh": round(slack_kwh, 6),
        "remaining_home_hours": round(remaining_home_hours, 6),
    }
    return result


def _estimate_water_heater(
    name: str,
    cfg: Mapping[str, Any],
    observation: Mapping[str, Any],
    baseline_power: Mapping[str, float],
    dt_hours: float,
    horizon_steps: int,
) -> Dict[str, Any]:
    rated_kw = _cfg_float(cfg, "rated_power_kw", 3.0)
    tank_l = _cfg_float(cfg, "tank_volume_l", 120.0)
    efficiency = max(EPS, _cfg_float(cfg, "thermal_efficiency", 1.0))
    minimum_c = _cfg_float(cfg, "minimum_temperature_c", 10.0)
    setpoint_c = _obs_float(observation, f"{name}_setpoint_c", _cfg_float(cfg, "setpoint_c", 55.0))
    ambient_c = _cfg_float(cfg, "ambient_temperature_c", 20.0)
    loss_per_hour = _cfg_float(cfg, "loss_coefficient_per_hour", 0.02)
    temp_c = _obs_float(observation, f"{name}_temperature_c", _cfg_float(cfg, "initial_temperature_c", setpoint_c))
    current_kw = _obs_float(observation, f"{name}_power_kw", 0.0)
    cap_kwh_per_k = tank_l * WATER_CP_KJ_PER_KG_K / 3600.0

    predicted_no_heat_c = temp_c - loss_per_hour * (temp_c - ambient_c) * dt_hours
    thermal_slack_kwh = max(0.0, predicted_no_heat_c - minimum_c) * cap_kwh_per_k / efficiency
    heat_needed_kwh = max(0.0, setpoint_c - temp_c) * cap_kwh_per_k / efficiency
    constraints = []
    if predicted_no_heat_c <= minimum_c + 0.5:
        constraints.append("water_temperature_margin_low")
    if heat_needed_kwh <= EPS:
        constraints.append("water_heater_at_setpoint")

    shed_kw = current_kw if current_kw > EPS and thermal_slack_kwh >= current_kw * dt_hours else 0.0
    add_kw = max(0.0, min(rated_kw, heat_needed_kwh / dt_hours) - current_kw) if heat_needed_kwh > EPS else 0.0
    horizon_kwh = max(dt_hours, horizon_steps * dt_hours) * max(0.0, shed_kw)
    shiftable_kwh = min(thermal_slack_kwh, horizon_kwh) if shed_kw > EPS else 0.0
    baseline_limited_shed_kw = min(shed_kw, _baseline_shed(name, baseline_power, current_kw))
    reliability = 0.9 - (0.25 if "water_temperature_margin_low" in constraints else 0.0)

    result = _device_result(
        shed_kw=shed_kw,
        add_kw=add_kw,
        shiftable_energy_kwh=shiftable_kwh,
        rebound_risk_kw=shed_kw,
        constraints=constraints,
        reliability=reliability,
        baseline_limited_shed_kw=baseline_limited_shed_kw,
    )
    result["state"] = {
        "temperature_c": round(temp_c, 6),
        "setpoint_c": round(setpoint_c, 6),
        "thermal_slack_kwh": round(thermal_slack_kwh, 6),
        "heat_needed_kwh": round(heat_needed_kwh, 6),
    }
    return result


def _estimate_hvac_cooling(
    name: str,
    cfg: Mapping[str, Any],
    observation: Mapping[str, Any],
    baseline_power: Mapping[str, float],
    dt_hours: float,
    horizon_steps: int,
) -> Dict[str, Any]:
    current_kw = _obs_float(observation, f"{name}_power_kw", _cfg_float(cfg, "current_power_kw", 0.0))
    indoor_temp_c = _obs_float(
        observation, f"{name}_indoor_temp_c", _cfg_float(cfg, "indoor_temperature_c", 26.0)
    )
    outdoor_temp_c = _obs_float(
        observation, f"{name}_outdoor_temp_c", _cfg_float(cfg, "outdoor_temperature_c", 30.0)
    )
    current_setpoint_c = _obs_float(
        observation, f"{name}_setpoint_c", _cfg_float(cfg, "current_setpoint_c", 26.0)
    )
    max_setpoint_c = _cfg_float(cfg, "max_setpoint_c", 27.5)
    min_active_kw = max(EPS, _cfg_float(cfg, "min_active_power_kw", 0.15))

    constraints = []
    slack_c = max(0.0, max_setpoint_c - indoor_temp_c)
    setpoint_headroom_c = max(0.0, max_setpoint_c - current_setpoint_c)
    if current_kw <= min_active_kw:
        constraints.append("hvac_idle")
    if slack_c <= 0.1:
        constraints.append("comfort_limit_reached")
    if setpoint_headroom_c <= 0.1:
        constraints.append("setpoint_ceiling_reached")

    shed_kw = 0.0
    shiftable_kwh = 0.0
    if not constraints:
        temp_factor = min(1.0, slack_c / 1.5)
        setpoint_factor = min(1.0, setpoint_headroom_c / 1.5)
        shed_fraction = min(0.9, 0.2 + 0.4 * temp_factor + 0.2 * setpoint_factor)
        shed_kw = current_kw * max(0.0, shed_fraction)
        shiftable_kwh = shed_kw * max(dt_hours, horizon_steps * dt_hours)

    baseline_limited_shed_kw = min(shed_kw, _baseline_shed(name, baseline_power, current_kw))
    outdoor_penalty = min(0.25, max(0.0, (outdoor_temp_c - indoor_temp_c) / 20.0))
    reliability = 0.88 - outdoor_penalty
    if "hvac_idle" in constraints:
        reliability -= 0.35
    if "comfort_limit_reached" in constraints or "setpoint_ceiling_reached" in constraints:
        reliability -= 0.2

    result = _device_result(
        shed_kw=shed_kw,
        add_kw=0.0,
        shiftable_energy_kwh=shiftable_kwh,
        rebound_risk_kw=shed_kw,
        constraints=constraints,
        reliability=reliability,
        baseline_limited_shed_kw=baseline_limited_shed_kw,
    )
    result["state"] = {
        "indoor_temp_c": round(indoor_temp_c, 6),
        "outdoor_temp_c": round(outdoor_temp_c, 6),
        "setpoint_c": round(current_setpoint_c, 6),
        "max_setpoint_c": round(max_setpoint_c, 6),
        "slack_c": round(slack_c, 6),
        "setpoint_headroom_c": round(setpoint_headroom_c, 6),
    }
    return result


def _estimate_task(
    name: str,
    cfg: Mapping[str, Any],
    observation: Mapping[str, Any],
    baseline_power: Mapping[str, float],
    now: datetime,
    dt_hours: float,
    horizon_steps: int,
) -> Dict[str, Any]:
    rated_kw = _cfg_float(cfg, "rated_power_kw", 1.0)
    duration_minutes = _cfg_float(cfg, "cycle_duration_minutes", 60.0)
    duration_hours = duration_minutes / 60.0
    earliest_start = _cfg_float(cfg, "earliest_start_hour", 0.0)
    latest_finish = _cfg_float(cfg, "latest_finish_hour", 24.0)
    interruptible = bool(cfg.get("interruptible", False))
    state = str(observation.get(f"{name}_state", "idle"))
    current_kw = _obs_float(observation, f"{name}_power_kw", 0.0)
    hour = _hour_decimal(now)
    latest_start = latest_finish - duration_hours
    baseline_kw = _baseline_power(name, baseline_power, current_kw)

    constraints = []
    start_window_open = earliest_start <= hour <= latest_start + EPS
    close_to_deadline = state == "waiting" and latest_start - hour <= dt_hours + EPS
    if state == "finished":
        constraints.append("task_finished")
    elif state == "idle":
        constraints.append("no_task_waiting")
    elif state == "running" and not interruptible:
        constraints.append("non_interruptible_running")
    elif state == "waiting" and not start_window_open:
        constraints.append("outside_start_window")
    if close_to_deadline:
        constraints.append("task_deadline_margin_low")

    shed_kw = 0.0
    shiftable_kwh = 0.0
    if state == "running" and interruptible:
        shed_kw = current_kw
        shiftable_kwh = min(duration_hours * rated_kw, current_kw * horizon_steps * dt_hours)
    elif state == "waiting" and baseline_kw > EPS and not close_to_deadline:
        shed_kw = min(rated_kw, baseline_kw)
        shiftable_kwh = duration_hours * rated_kw

    add_kw = rated_kw if state == "waiting" and start_window_open else 0.0
    baseline_limited_shed_kw = min(shed_kw, max(0.0, baseline_kw - current_kw)) if baseline_power else shed_kw
    reliability = 0.9
    if "task_deadline_margin_low" in constraints:
        reliability -= 0.35
    if "outside_start_window" in constraints or "non_interruptible_running" in constraints:
        reliability -= 0.1

    result = _device_result(
        shed_kw=shed_kw,
        add_kw=add_kw,
        shiftable_energy_kwh=shiftable_kwh,
        rebound_risk_kw=shed_kw,
        constraints=constraints,
        reliability=reliability,
        baseline_limited_shed_kw=baseline_limited_shed_kw,
    )
    result["state"] = {
        "task_state": state,
        "start_window_open": start_window_open,
        "latest_start_hour": round(latest_start, 6),
        "duration_hours": round(duration_hours, 6),
    }
    return result


def _empty_device(name: str, constraints: Iterable[str]) -> Dict[str, Any]:
    return _device_result(0.0, 0.0, 0.0, 0.0, constraints, 0.0, 0.0)


def _device_result(
    shed_kw: float,
    add_kw: float,
    shiftable_energy_kwh: float,
    rebound_risk_kw: float,
    constraints: Iterable[str],
    reliability: float,
    baseline_limited_shed_kw: float,
) -> Dict[str, Any]:
    return {
        "shed_kw": round(max(0.0, shed_kw), 6),
        "add_kw": round(max(0.0, add_kw), 6),
        "baseline_limited_shed_kw": round(max(0.0, baseline_limited_shed_kw), 6),
        "shiftable_energy_kwh": round(max(0.0, shiftable_energy_kwh), 6),
        "max_duration_steps": 1 if shed_kw > EPS else 0,
        "rebound_risk_kw": round(max(0.0, rebound_risk_kw), 6),
        "constraints": list(constraints),
        "reliability": round(min(1.0, max(0.0, reliability)), 6),
    }


def _sum_devices(devices: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    totals = {
        "shed_kw": 0.0,
        "add_kw": 0.0,
        "baseline_limited_shed_kw": 0.0,
        "shiftable_energy_kwh": 0.0,
        "rebound_risk_kw": 0.0,
    }
    weighted_reliability = 0.0
    weight_sum = 0.0
    for device in devices:
        for key in totals:
            totals[key] += float(device.get(key, 0.0))
        weight = float(device.get("shed_kw", 0.0)) + float(device.get("add_kw", 0.0))
        weighted_reliability += weight * float(device.get("reliability", 0.0))
        weight_sum += weight
    totals = {key: round(value, 6) for key, value in totals.items()}
    totals["reliability"] = round(weighted_reliability / weight_sum, 6) if weight_sum > EPS else 0.0
    return totals


def _success_probability(potential: Mapping[str, Any], household_prior: Mapping[str, Any] | None, committable_kw: float, target_kw: float) -> float:
    base = float(potential.get("total", {}).get("reliability", 0.0)) or 0.75
    prior = household_prior or {}
    alpha = _to_float(prior.get("prior_success_alpha", 2.0))
    beta = _to_float(prior.get("prior_failure_beta", 1.0))
    success_count = _to_float(prior.get("success_count", 0.0))
    failure_count = _to_float(prior.get("failure_count", 0.0))
    posterior = (success_count + alpha) / max(EPS, success_count + failure_count + alpha + beta)
    coverage = 1.0 if target_kw <= EPS else min(1.0, committable_kw / target_kw)
    return min(1.0, max(0.0, base * posterior * (0.5 + 0.5 * coverage)))


def _safety_margin(success_probability: float) -> float:
    if success_probability >= 0.9:
        return 0.9
    if success_probability >= 0.75:
        return 0.8
    if success_probability >= 0.6:
        return 0.7
    return 0.5


def _main_constraints(potential: Mapping[str, Any]) -> list[str]:
    constraints: list[str] = []
    devices = potential.get("devices", {})
    if isinstance(devices, Mapping):
        for name, device in devices.items():
            if not isinstance(device, Mapping):
                continue
            for constraint in device.get("constraints", []):
                constraints.append(f"{name}:{constraint}")
    return constraints[:8]


def _energy_limited_kw(shiftable_kwh: float, duration_hours: float, immediate_kw: float) -> float:
    if immediate_kw <= EPS:
        return 0.0
    if shiftable_kwh <= EPS:
        return immediate_kw
    return min(immediate_kw, shiftable_kwh / max(EPS, duration_hours))


def _baseline_shed(name: str, baseline_power: Mapping[str, float], current_kw: float) -> float:
    if not baseline_power:
        return current_kw
    baseline_kw = _baseline_power(name, baseline_power, current_kw)
    return max(0.0, baseline_kw - min(current_kw, baseline_kw)) if current_kw < baseline_kw else baseline_kw


def _baseline_power(name: str, baseline_power: Mapping[str, float], default: float) -> float:
    if name in baseline_power:
        return max(0.0, _to_float(baseline_power[name]))
    power_key = f"{name}_power_kw"
    if power_key in baseline_power:
        return max(0.0, _to_float(baseline_power[power_key]))
    return max(0.0, default)


def _remaining_home_hours(hour: float, arrival_hour: float, departure_hour: float, at_home: bool) -> float:
    if not at_home:
        return 0.0
    if arrival_hour <= departure_hour:
        return max(0.0, departure_hour - hour)
    if hour >= arrival_hour:
        return max(0.0, 24.0 - hour + departure_hour)
    return max(0.0, departure_hour - hour)


def _dt_hours(observation: Mapping[str, Any], device_config: Mapping[str, Any]) -> float:
    if "time_step_minutes" in observation:
        return max(EPS, _to_float(observation["time_step_minutes"]) / 60.0)
    simulation = device_config.get("simulation", {})
    if isinstance(simulation, Mapping):
        return max(EPS, _to_float(simulation.get("time_step_minutes", 15.0)) / 60.0)
    return 0.25


def _parse_timestamp(timestamp: str, device_config: Mapping[str, Any]) -> datetime:
    if timestamp:
        return datetime.fromisoformat(timestamp)
    simulation = device_config.get("simulation", {})
    if isinstance(simulation, Mapping):
        return datetime.fromisoformat(str(simulation.get("start_datetime", "2026-01-01 00:00:00")))
    return datetime(2026, 1, 1)


def _hour_decimal(now: datetime) -> float:
    return now.hour + now.minute / 60.0 + now.second / 3600.0


def _cfg_float(config: Mapping[str, Any], key: str, default: float) -> float:
    return _to_float(config.get(key, default))


def _obs_float(observation: Mapping[str, Any], key: str, default: float) -> float:
    return _to_float(observation.get(key, default))


def _obs_bool(observation: Mapping[str, Any], key: str, default: bool) -> bool:
    value = observation.get(key, default)
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "none", "no"}
    return bool(value)


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
