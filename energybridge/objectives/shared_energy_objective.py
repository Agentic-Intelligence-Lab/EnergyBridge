"""Shared MPC-aligned controllable cost for RL reward.

This module provides a step-level cost function that matches the structure
of `home_objective_v15.py` but only includes terms that the current RL v2
action space (5 dims: AC, washer, dishwasher, WH flag, WH temp) can influence.

Excluded terms (not controllable by current RL action):
  - EV (soc, arrival, departure, charger)
  - Dryer
  - Grid DR contribution (no baseline available at decision time)
  - Grid slack (grid_max unavailable)
  - LLM / roleplay / token costs

The cost is designed so that:
  reward = -reward_scale * total_cost - rl_slack_scale * terminal_slack
"""

from __future__ import annotations

import numpy as np

# ── MPC-aligned weights (from PDF v1.5) ──────────────────────────────────
MPC_ALIGNED_WEIGHTS = {
    # Scalar multipliers applied to RL reward
    "reward_scale": 0.01,
    "rl_slack_scale": 0.2,
    # MPC structural weights
    "alpha_cost": 0.25,     # DR weight (slightly higher cost weight)
    "alpha_user": 0.40,     # DR weight
    "alpha_grid": 0.35,     # DR weight
    "lambda_slack": 100.0,
    # Task-related
    "q_time": 2.0,
    "delta_acc_default_c": 1.0,
    # VPP
    "vpp_cap_kwh": 2.0,
    # Terminal penalties (replaces old terminal bonuses)
    "unfinished_washer_penalty": 150.0,
    "unfinished_dishwasher_penalty": 150.0,
    "unfinished_wh_penalty": 80.0,
    "vpp_cap_violation_penalty": 120.0,
    "appliance_in_vpp_penalty": 150.0,
    "wh_in_vpp_penalty": 120.0,
}

EPSILON = 1e-9
SHIFTABLE_NAMES = ("washer", "dishwasher")


def _positive(x: float) -> float:
    return max(0.0, float(x))


def _dt_hours(dt_h: float | None = None) -> float:
    return float(dt_h) if dt_h is not None else (1.0 / 6.0)


# ── Price ────────────────────────────────────────────────────────────────
def _price_at_step(price_current: float, default: float = 1.0) -> float:
    try:
        return max(0.01, float(price_current))
    except (TypeError, ValueError):
        return float(default)


# ── HVAC proxy power ─────────────────────────────────────────────────────
def _hvac_power_kw(setpoint_c: float, indoor_c: float, outdoor_c: float) -> float:
    """Lightweight HVAC power proxy matching home_objective_v15."""
    cooling_lift = max(0.0, outdoor_c - setpoint_c)
    zone_need = max(0.0, indoor_c - setpoint_c)
    return max(0.0, 0.12 * cooling_lift + 0.3 * zone_need)


# ── Shiftable appliance power ────────────────────────────────────────────
def _shiftable_power_kw(name: str, start_h: float | None, app_cfg: dict, skipped: bool) -> float:
    if not app_cfg.get("present", True):
        return 0.0
    if skipped or start_h is None:
        return 0.0
    return float(app_cfg.get("power_kw", 1.0))


# ── Water heater power ───────────────────────────────────────────────────
def _wh_power_kw(appliances: dict, wh_cfg: dict) -> float:
    if not wh_cfg.get("present", True):
        return 0.0
    if appliances.get("water_heater_preheat") is False:
        return 0.0
    start = appliances.get("water_heater_preheat_start_h")
    end = appliances.get("water_heater_preheat_end_h")
    scheduled = bool(appliances.get("water_heater_preheat") is True or start is not None or end is not None)
    if not scheduled:
        return 0.0
    return float(wh_cfg.get("rated_kw", wh_cfg.get("power_kw", 0.0)))


# ── Comfort ──────────────────────────────────────────────────────────────
def _comfort_cost(indoor_c: float, ac_cfg: dict, occupied: bool, dt_h: float, w: dict) -> float:
    """MPC user.temp equivalent: per-degree^2 violation outside comfort band."""
    if not occupied:
        return 0.0
    lo = float(ac_cfg.get("setpoint_preferred_min_c", 24.0)) - float(ac_cfg.get("temp_tolerance_c", 1.0))
    hi = float(ac_cfg.get("setpoint_preferred_max_c", 26.0)) + float(ac_cfg.get("temp_tolerance_c", 1.0))
    raw = (_positive(lo - indoor_c) ** 2 + _positive(indoor_c - hi) ** 2) * dt_h
    ref = max(EPSILON, (hi - lo) ** 2)
    return float(raw / ref)


def _pref_cost(setpoint_c: float, ac_cfg: dict, occupied: bool, dt_h: float, w: dict) -> float:
    """MPC user.pref equivalent: penalty for deviating from preferred setpoint."""
    if not occupied:
        return 0.0
    pref = (float(ac_cfg.get("setpoint_preferred_min_c", 24.0)) +
            float(ac_cfg.get("setpoint_preferred_max_c", 26.0))) / 2.0
    delta = w.get("delta_acc_default_c", 1.0)
    raw = (_positive(abs(setpoint_c - pref) / max(EPSILON, delta) - 1.0) ** 2) * dt_h
    return float(raw)


def _task_time_cost(name: str, start_h: float | None, app_cfg: dict, skipped: bool, w: dict) -> float:
    """MPC user.time equivalent: penalty for scheduling away from preferred window."""
    if not app_cfg.get("present", True):
        return 0.0
    if skipped:
        return 1.0  # max penalty for skip
    if start_h is None:
        return 0.0  # no decision yet
    duration_h = float(app_cfg.get("duration_h", 2.0))
    finish_h = start_h + duration_h
    pref_h = float(app_cfg.get("preferred_h", start_h))
    pref_lo = pref_h
    pref_hi = pref_h + duration_h
    allowed = 24.0  # proxy: full day
    d_win = (_positive(pref_lo - start_h) + _positive(finish_h - pref_hi)) / allowed
    q_time = float(w.get("q_time", 2.0))
    return float(d_win ** q_time)


def _wh_time_cost(appliances: dict, wh_cfg: dict, w: dict) -> float:
    """MPC water_heater time penalty."""
    if not wh_cfg.get("present", True):
        return 0.0
    if appliances.get("water_heater_preheat") is False:
        return 1.0  # explicit disable = max penalty
    start_h = appliances.get("water_heater_preheat_start_h")
    end_h = appliances.get("water_heater_preheat_end_h")
    if start_h is None or end_h is None:
        return 0.0
    pref_lo = float(wh_cfg.get("pre_heat_window_start_h", start_h))
    pref_hi = float(wh_cfg.get("pre_heat_window_end_h", end_h))
    bath_h = float(wh_cfg.get("bath_required_h", pref_hi))
    allowed = max(EPSILON, (bath_h - pref_lo) if bath_h > pref_lo else 24.0)
    d_win = (_positive(pref_lo - float(start_h)) + _positive(float(end_h) - pref_hi)) / allowed
    d_win += _positive(float(end_h) - bath_h) / allowed
    q_time = float(w.get("q_time", 2.0))
    return float(d_win ** q_time)


# ── VPP grid cost ────────────────────────────────────────────────────────
def _vpp_peak_cost(total_power_kw: float, vpp_target_kw: float, dt_h: float) -> float:
    """MPC grid.peak: quadratic penalty for exceeding VPP target power."""
    if vpp_target_kw <= 0.0:
        return 0.0
    raw = (_positive(total_power_kw - vpp_target_kw) ** 2) * dt_h
    ref = max(EPSILON, vpp_target_kw ** 2)
    return float(raw / ref)


# ── Slack ─────────────────────────────────────────────────────────────────
def _setpoint_slack(setpoint_c: float) -> float:
    """Penalty for setpoint outside [22, 28]."""
    return _positive(22.0 - setpoint_c) ** 2 + _positive(setpoint_c - 28.0) ** 2


def _task_deadline_slack(name: str, start_h: float | None, app_cfg: dict, day_idx: int) -> float:
    """Penalty for appliance scheduled past its deadline."""
    if not app_cfg.get("present", True) or start_h is None:
        return 0.0
    deadline_h = float(app_cfg.get("latest_h", app_cfg.get("deadline_h", 23.0)))
    finish_h = float(start_h) + float(app_cfg.get("duration_h", 2.0))
    late = _positive(finish_h - deadline_h)
    return float(late ** 2)


# ── Main cost function ────────────────────────────────────────────────────
def compute_shared_controllable_cost(
    *,
    setpoint_c: float,
    indoor_c: float,
    outdoor_c: float,
    occupied: bool,
    vpp_active: bool,
    vpp_target_kw: float | None = None,
    price_current: float = 1.0,
    dt_h: float | None = None,
    appliance_config: dict | None = None,
    appliance_actions: dict | None = None,
    day_idx: int = 0,
    vpp_avoid_info: dict | None = None,
    wh_ready_info: dict | None = None,
) -> dict:
    """Compute MPC-aligned step-level controllable cost.

    Returns a flat dict with all cost components for logging and reward.
    Lower cost = better.
    """
    cfg = appliance_config or {}
    actions = appliance_actions or {}
    ac_cfg = cfg.get("ac", {}) or {}
    dt = _dt_hours(dt_h)
    w = MPC_ALIGNED_WEIGHTS

    # ── Power calculations ────────────────────────────────────────────
    hvac_kw = _hvac_power_kw(setpoint_c, indoor_c, outdoor_c)
    price = _price_at_step(price_current)

    shift_kw = 0.0
    for name in SHIFTABLE_NAMES:
        app_cfg = cfg.get(name, {}) or {}
        skipped = bool(actions.get(f"{name}_skip"))
        start_h = actions.get(f"{name}_start_h")
        shift_kw += _shiftable_power_kw(name, start_h, app_cfg, skipped)

    wh_cfg = cfg.get("water_heater", {}) or {}
    wh_kw = _wh_power_kw(actions, wh_cfg)
    total_controllable_kw = hvac_kw + shift_kw + wh_kw

    # ── Cost components ───────────────────────────────────────────────
    cost_hvac = price * hvac_kw * dt
    cost_shiftable = price * shift_kw * dt
    cost_wh = price * wh_kw * dt
    cost_price = price * total_controllable_kw * dt

    cost_comfort = _comfort_cost(indoor_c, ac_cfg, occupied, dt, w)
    cost_pref = _pref_cost(setpoint_c, ac_cfg, occupied, dt, w)

    cost_user_time = 0.0
    for name in SHIFTABLE_NAMES:
        app_cfg = cfg.get(name, {}) or {}
        skipped = bool(actions.get(f"{name}_skip"))
        start_h = actions.get(f"{name}_start_h")
        cost_user_time += _task_time_cost(name, start_h, app_cfg, skipped, w)
    cost_user_time += _wh_time_cost(actions, wh_cfg, w)

    # VPP peak
    target_kw = vpp_target_kw if vpp_target_kw is not None else (
        float(w["vpp_cap_kwh"]) if vpp_active else 0.0
    )
    cost_vpp_peak = _vpp_peak_cost(total_controllable_kw, target_kw, dt) if vpp_active else 0.0

    # VPP energy
    total_kwh = total_controllable_kw * dt
    cost_vpp_window = total_kwh if vpp_active else 0.0

    # VPP appliance overlap (binary flag)
    appliance_in_vpp = 0.0
    if vpp_active and actions:
        for name in SHIFTABLE_NAMES:
            skipped = bool(actions.get(f"{name}_skip"))
            if not skipped:
                start_h = actions.get(f"{name}_start_h")
                if start_h is not None:
                    app_cfg = cfg.get(name, {}) or {}
                    dur = float(app_cfg.get("duration_h", 2.0))
                    if start_h <= 19.0 and (start_h + dur) > 18.0:
                        appliance_in_vpp = 1.0
                        break
        if actions.get("water_heater_preheat") is True:
            wh_start = actions.get("water_heater_preheat_start_h")
            wh_end = actions.get("water_heater_preheat_end_h")
            if wh_start is not None and wh_end is not None:
                if float(wh_start) < 19.0 and float(wh_end) > 18.0:
                    appliance_in_vpp = 1.0

    # Slack
    slack_hvac = _setpoint_slack(setpoint_c)
    slack_task = 0.0
    for name in SHIFTABLE_NAMES:
        app_cfg = cfg.get(name, {}) or {}
        start_h = actions.get(f"{name}_start_h")
        skipped = bool(actions.get(f"{name}_skip"))
        if not skipped:
            slack_task += _task_deadline_slack(name, start_h, app_cfg, day_idx)

    # ── Aggregate ─────────────────────────────────────────────────────
    cost_home = cost_hvac + cost_shiftable + cost_wh  # raw energy cost
    cost_user = cost_comfort + cost_pref + cost_user_time
    cost_grid = cost_vpp_peak
    cost_slack = slack_hvac + slack_task

    total_cost = (
        w["alpha_cost"] * cost_home
        + w["alpha_user"] * cost_user
        + w["alpha_grid"] * cost_grid
        + w["lambda_slack"] * cost_slack
    )

    return {
        "total_cost": float(total_cost),
        "cost_home": float(cost_home),
        "cost_user": float(cost_user),
        "cost_grid": float(cost_grid),
        "cost_slack": float(cost_slack),
        "cost_hvac": float(cost_hvac),
        "cost_shiftable": float(cost_shiftable),
        "cost_water_heater": float(cost_wh),
        "cost_price": float(cost_price),
        "cost_comfort": float(cost_comfort),
        "cost_pref": float(cost_pref),
        "cost_user_time": float(cost_user_time),
        "cost_vpp_window": float(cost_vpp_window),
        "cost_vpp_peak": float(cost_vpp_peak),
        "cost_appliance_in_vpp": float(appliance_in_vpp),
        "cost_wh_in_vpp": 0.0,  # placeholder, covered by appliance_in_vpp
        "cost_task_deadline": float(slack_task),
        "cost_unfinished": 0.0,  # terminal only
        # Raw values for debugging
        "hvac_kw": float(hvac_kw),
        "shift_kw": float(shift_kw),
        "wh_kw": float(wh_kw),
        "total_kw": float(total_controllable_kw),
        "occupied": float(occupied),
        "vpp_active": float(vpp_active),
    }


def compute_terminal_penalty(
    *,
    appliance_config: dict | None = None,
    washer_completed: bool = False,
    dishwasher_completed: bool = False,
    wh_ready: bool = False,
    total_vpp_kwh: float = 0.0,
    appliance_in_vpp_events: int = 0,
) -> dict:
    """Episode-end penalty aligned with benchmark metrics."""
    cfg = appliance_config or {}
    w = MPC_ALIGNED_WEIGHTS

    penalty = 0.0
    breakdown = {}

    # Unfinished appliances
    washer_present = bool((cfg.get("washer", {}) or {}).get("present", False))
    dishwasher_present = bool((cfg.get("dishwasher", {}) or {}).get("present", False))
    wh_present = bool((cfg.get("water_heater", {}) or {}).get("present", False))

    if washer_present and not washer_completed:
        p = w["unfinished_washer_penalty"]
        penalty += p
        breakdown["unfinished_washer"] = float(p)
    if dishwasher_present and not dishwasher_completed:
        p = w["unfinished_dishwasher_penalty"]
        penalty += p
        breakdown["unfinished_dishwasher"] = float(p)
    if wh_present and not wh_ready:
        p = w["unfinished_wh_penalty"]
        penalty += p
        breakdown["unfinished_wh"] = float(p)

    # VPP violations
    if total_vpp_kwh > w["vpp_cap_kwh"]:
        p = w["vpp_cap_violation_penalty"]
        penalty += p
        breakdown["vpp_cap_violation"] = float(p)
    if appliance_in_vpp_events > 0:
        p = w["appliance_in_vpp_penalty"] * appliance_in_vpp_events
        penalty += p
        breakdown["appliance_in_vpp"] = float(p)

    return {
        "terminal_penalty": float(penalty),
        "terminal_breakdown": breakdown,
        "washer_completed": washer_completed,
        "dishwasher_completed": dishwasher_completed,
        "wh_ready": wh_ready,
        "total_vpp_kwh": float(total_vpp_kwh),
        "appliance_in_vpp_events": int(appliance_in_vpp_events),
    }
