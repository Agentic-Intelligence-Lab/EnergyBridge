"""PDF v1.5 home objective for benchmark MPC and post-hoc Agent logging.

The objective follows the PDF structure:

    J = alpha_C C_home + alpha_U D_user + alpha_G D_grid
        + lambda_slack D_slack

It intentionally excludes roleplay score, user preference score, token cost,
latency, LLM cost, and benchmark scoring metrics.
"""

from __future__ import annotations

from .weights import PDF_V15_WEIGHTS_DEFAULT


SHIFTABLE_NAMES = ("washer", "dishwasher", "dryer")
WATER_HEATER_NAME = "water_heater"
EPSILON = 1e-9


def compute_home_objective_v15(
    state: dict,
    action: dict,
    weights: dict | None = None,
) -> dict:
    """Return PDF v1.5 objective terms for one decision-time candidate."""
    weights = dict(PDF_V15_WEIGHTS_DEFAULT if weights is None else weights)
    appliances = dict(action.get("appliances") or {})
    cfg = state.get("appliance_config") or {}

    active_terms: list[str] = []
    inactive_terms: list[str] = []
    proxy_status = {
        "hvac_power": "unavailable",
        "price": "unavailable",
        "base_load": "unavailable",
        "ev_soc": "unavailable",
        "dr_baseline": "unavailable",
        "grid_target": "unavailable",
        "normalization_refs": "fallback_refs",
    }

    price, price_status = _price(state)
    proxy_status["price"] = price_status
    dt_h = _float(state.get("dt_h"), 1.0)
    hvac_power_kw, hvac_proxy, hvac_notes = _hvac_power_kw(action, state)
    proxy_status["hvac_power"] = (
        "direct" if hvac_power_kw is not None and not hvac_proxy
        else "existing_proxy" if hvac_power_kw is not None
        else "unavailable"
    )

    task_power_kw, task_cost_details = _shiftable_power_kw(appliances, state, cfg)
    wh_power_kw, wh_details = _water_heater_power_kw(appliances, cfg)
    ev_power_kw, ev_energy_details = _ev_power_kw(appliances, state, cfg)
    shift_power_kw = task_power_kw + ev_power_kw + wh_power_kw

    cost = _cost_term(
        hvac_power_kw=hvac_power_kw,
        hvac_proxy=hvac_proxy,
        shift_power_kw=shift_power_kw,
        task_cost_details=task_cost_details,
        wh_details=wh_details,
        ev_energy_details=ev_energy_details,
        price=price,
        price_status=price_status,
        dt_h=dt_h,
        active_terms=active_terms,
        inactive_terms=inactive_terms,
    )

    user = _user_term(
        state=state,
        action=action,
        appliances=appliances,
        cfg=cfg,
        weights=weights,
        dt_h=dt_h,
        active_terms=active_terms,
        inactive_terms=inactive_terms,
        proxy_status=proxy_status,
    )

    grid = _grid_term(
        state=state,
        hvac_power_kw=hvac_power_kw,
        shift_power_kw=shift_power_kw,
        dt_h=dt_h,
        active_terms=active_terms,
        inactive_terms=inactive_terms,
        proxy_status=proxy_status,
    )

    slack = _slack_term(
        state=state,
        action=action,
        appliances=appliances,
        cfg=cfg,
        weights=weights,
        hvac_power_kw=hvac_power_kw,
        shift_power_kw=shift_power_kw,
        active_terms=active_terms,
        inactive_terms=inactive_terms,
        proxy_status=proxy_status,
    )

    total = (
        weights.get("alpha_cost", 0.0) * cost["total"]
        + weights.get("alpha_user", 0.0) * user["total"]
        + weights.get("alpha_grid", 0.0) * grid["total"]
        + weights.get("lambda_slack", 0.0) * slack["total"]
    )

    return {
        "version": "home_objective_v15_pdf",
        "total": float(total),
        "cost": cost,
        "user": user,
        "grid": grid,
        "slack": slack,
        "weights": dict(weights),
        "proxy_status": proxy_status,
        "active_terms": active_terms,
        "inactive_or_missing_terms": inactive_terms,
        "diagnostics": {
            "formula": "alpha_cost*C_home + alpha_user*D_user + alpha_grid*D_grid + lambda_slack*D_slack",
            "objective_excludes": [
                "roleplay_score",
                "user_pref_score",
                "token_cost",
                "latency",
                "llm_cost",
                "final_benchmark_score",
            ],
            "hvac_power_proxy": bool(hvac_proxy),
            "hvac_power_notes": hvac_notes,
            "water_heater_included": bool(wh_details.get("present", False)),
            "water_heater": {
                "wh_model": "shiftable_load_proxy",
                "wh_tank_model_available": False,
                "todo": "Add tank-temperature or readiness forecast later if available.",
                "notes": [
                    "WH contributes as shiftable/service load via schedule timing, rated power, and readiness slack.",
                    "No water-tank thermal model is used.",
                ],
            },
        },
    }


def _cost_term(
    *,
    hvac_power_kw: float | None,
    hvac_proxy: bool,
    shift_power_kw: float,
    task_cost_details: dict,
    wh_details: dict,
    ev_energy_details: dict,
    price: float,
    price_status: str,
    dt_h: float,
    active_terms: list[str],
    inactive_terms: list[str],
) -> dict:
    missing = []
    notes = [f"price_proxy: {price_status}"] if price_status != "direct" else []
    hvac_cost = 0.0
    if hvac_power_kw is None:
        missing.append("hvac_power_kw")
        inactive_terms.append("cost.hvac")
        notes.append("HVAC cost inactive because no clean power estimate is available.")
    else:
        hvac_cost = price * hvac_power_kw * dt_h
        active_terms.append("cost.hvac")
        if hvac_proxy:
            notes.append("hvac_power_proxy: true")

    shift_cost = price * shift_power_kw * dt_h
    if shift_power_kw > 0:
        active_terms.append("cost.shiftable")
    else:
        inactive_terms.append("cost.shiftable")
    if wh_details.get("scheduled") and wh_details.get("power_kw", 0.0) > 0:
        active_terms.append("cost.water_heater")
    elif wh_details.get("present"):
        inactive_terms.append("cost.water_heater")

    raw_total = hvac_cost + shift_cost
    ref = max(1.0, price * 10.0 * dt_h)
    notes.append("normalization_ref_proxy: cost_ref_10kwh_equivalent")
    normalized_total = raw_total / ref
    return {
        "total": float(normalized_total),
        "raw": {
            "home_cost": float(raw_total),
            "hvac_cost": float(hvac_cost),
            "shiftable_cost": float(shift_cost),
            "price": float(price),
            "dt_h": float(dt_h),
            "hvac_power_kw": None if hvac_power_kw is None else float(hvac_power_kw),
            "shift_power_kw": float(shift_power_kw),
            "tasks": task_cost_details,
            "water_heater": wh_details,
            "ev": ev_energy_details,
        },
        "normalized": {
            "home_cost": float(normalized_total),
            "reference": float(ref),
        },
        "available": hvac_power_kw is not None or shift_power_kw > 0,
        "missing": missing,
        "notes": notes,
    }


def _user_term(
    *,
    state: dict,
    action: dict,
    appliances: dict,
    cfg: dict,
    weights: dict,
    dt_h: float,
    active_terms: list[str],
    inactive_terms: list[str],
    proxy_status: dict,
) -> dict:
    temp = _temp_discomfort(state, cfg, dt_h)
    pref = _setpoint_pref_discomfort(state, action, cfg, weights, dt_h)
    time = _task_time_discomfort(appliances, state, cfg, weights)
    ev = _ev_discomfort(appliances, state, cfg)
    proxy_status["ev_soc"] = "direct" if ev["active"] else "unavailable"

    term_items = (("temp", temp), ("pref", pref), ("time", time), ("ev", ev))
    active_values = []
    available_terms = []
    missing_terms = []
    notes = []
    for name, item in term_items:
        if item.get("active"):
            active_terms.append(f"user.{name}")
            available_terms.append(name)
            active_values.append(float(item.get("total", 0.0)))
        else:
            inactive_terms.append(f"user.{name}")
            missing_terms.append(name)
        notes.extend(item.get("notes") or [])

    total = sum(active_values) / len(active_values) if active_values else 0.0
    return {
        "total": float(total),
        "temp": temp,
        "pref": pref,
        "time": time,
        "ev": ev,
        "available_terms": available_terms,
        "missing_terms": missing_terms,
        "notes": notes,
    }


def _grid_term(
    *,
    state: dict,
    hvac_power_kw: float | None,
    shift_power_kw: float,
    dt_h: float,
    active_terms: list[str],
    inactive_terms: list[str],
    proxy_status: dict,
) -> dict:
    peak = _grid_peak(state, hvac_power_kw, shift_power_kw, dt_h, proxy_status)
    dr = _dr_contribution(state)
    if peak["active"]:
        active_terms.append("grid.peak")
    else:
        inactive_terms.append("grid.peak")
    if dr["active"]:
        active_terms.append("grid.dr_contribution")
    else:
        inactive_terms.append("grid.dr_contribution")

    total = float(peak["total"] - dr["normalized"].get("dr_contribution", 0.0))
    return {
        "total": total,
        "peak": peak,
        "dr_contribution": dr,
        "available": bool(peak["active"] or dr["active"]),
        "missing": list(dict.fromkeys(peak.get("missing", []) + dr.get("missing", []))),
        "notes": list(dict.fromkeys(peak.get("notes", []) + dr.get("notes", []))),
    }


def _slack_term(
    *,
    state: dict,
    action: dict,
    appliances: dict,
    cfg: dict,
    weights: dict,
    hvac_power_kw: float | None,
    shift_power_kw: float,
    active_terms: list[str],
    inactive_terms: list[str],
    proxy_status: dict,
) -> dict:
    hvac = _hvac_slack(state, action, cfg)
    task = _task_slack(appliances, state, cfg)
    ev = _ev_slack(appliances, state, cfg)
    grid = _grid_slack(state, hvac_power_kw, shift_power_kw, proxy_status)

    total = hvac["total"] + task["total"] + ev["total"] + grid["total"]
    for name, item in (("hvac", hvac), ("task", task), ("ev", ev), ("grid", grid)):
        if item.get("active"):
            active_terms.append(f"slack.{name}")
        else:
            inactive_terms.append(f"slack.{name}")

    return {
        "total": float(total),
        "hvac": hvac,
        "task": task,
        "ev": ev,
        "grid": grid,
        "available_terms": [
            name
            for name, item in (("hvac", hvac), ("task", task), ("ev", ev), ("grid", grid))
            if item.get("active")
        ],
        "missing_terms": [
            name
            for name, item in (("hvac", hvac), ("task", task), ("ev", ev), ("grid", grid))
            if not item.get("active")
        ],
        "notes": [
            "lambda_slack is applied only in top-level total.",
            f"lambda_slack={weights.get('lambda_slack', 100.0)}",
        ],
    }


def _temp_discomfort(state: dict, cfg: dict, dt_h: float) -> dict:
    ac_cfg = _cfg(cfg, "ac")
    indoor = _float_or_none(state.get("temp_c"))
    lo, hi, band_notes = _comfort_bounds(ac_cfg)
    missing = []
    notes = list(band_notes)
    if indoor is None:
        missing.append("temp_c")
    if lo is None:
        missing.append("comfort_lower_c")
    if hi is None:
        missing.append("comfort_upper_c")
    if missing:
        return {
            "total": 0.0,
            "raw": 0.0,
            "normalized": 0.0,
            "active": False,
            "missing": missing,
            "notes": notes,
        }

    occupancy = _float_or_none(state.get("occupancy"))
    if occupancy is None:
        occupancy = 1.0
        notes.append("occupancy_proxy: constant_1.0")
    raw = occupancy * (_positive(lo - indoor) ** 2 + _positive(indoor - hi) ** 2) * dt_h
    ref = max(1.0, (hi - lo) ** 2)
    return {
        "total": float(raw / ref),
        "raw": float(raw),
        "normalized": float(raw / ref),
        "active": True,
        "missing": [],
        "notes": notes,
        "comfort_bounds_c": {"lower": float(lo), "upper": float(hi)},
        "occupancy": float(occupancy),
    }


def _setpoint_pref_discomfort(
    state: dict,
    action: dict,
    cfg: dict,
    weights: dict,
    dt_h: float,
) -> dict:
    ac_cfg = _cfg(cfg, "ac")
    setpoint = _float_or_none(action.get("setpoint"))
    preferred = _preferred_setpoint(ac_cfg)
    missing = []
    notes = []
    if setpoint is None:
        missing.append("candidate_setpoint")
    if preferred is None:
        missing.append("preferred_setpoint")
    delta_acc = _float_or_none(ac_cfg.get("delta_acc_c"))
    if delta_acc is None:
        delta_acc = _float(weights.get("delta_acc_default_c"), 1.0)
        notes.append(f"delta_acc_proxy: default_{delta_acc:.1f}C")
    if missing:
        return {
            "total": 0.0,
            "raw": 0.0,
            "normalized": 0.0,
            "active": False,
            "missing": missing,
            "notes": notes,
        }
    occupancy = _float_or_none(state.get("occupancy"))
    if occupancy is None:
        occupancy = 1.0
        notes.append("occupancy_proxy: constant_1.0")
    raw = occupancy * (_positive(abs(setpoint - preferred) / max(EPSILON, delta_acc) - 1.0) ** 2) * dt_h
    return {
        "total": float(raw),
        "raw": float(raw),
        "normalized": float(raw),
        "active": True,
        "missing": [],
        "notes": notes,
        "preferred_setpoint_c": float(preferred),
        "delta_acc_c": float(delta_acc),
    }


def _task_time_discomfort(
    appliances: dict,
    state: dict,
    cfg: dict,
    weights: dict,
) -> dict:
    details = {}
    missing_terms = []
    notes = []
    total = 0.0
    any_present = False
    for name in SHIFTABLE_NAMES:
        app_cfg = _cfg(cfg, name)
        if not _present(app_cfg):
            details[name] = {"active": False, "reason": "not_present", "total": 0.0}
            continue
        any_present = True
        item = _one_task_time_discomfort(name, appliances, state, app_cfg, weights)
        details[name] = item
        total += item["total"]
        missing_terms.extend(item.get("missing", []))
        notes.extend(item.get("notes", []))
    wh_cfg = _cfg(cfg, WATER_HEATER_NAME)
    if _present(wh_cfg):
        any_present = True
        wh_item = _water_heater_time_discomfort(appliances, wh_cfg, weights)
        details[WATER_HEATER_NAME] = wh_item
        total += wh_item["total"]
        missing_terms.extend(wh_item.get("missing", []))
        notes.extend(wh_item.get("notes", []))
    else:
        details[WATER_HEATER_NAME] = {
            "active": False,
            "reason": "not_present",
            "total": 0.0,
        }
    return {
        "total": float(total),
        "raw": details,
        "normalized": float(total),
        "active": any_present,
        "missing": list(dict.fromkeys(missing_terms)),
        "notes": list(dict.fromkeys(notes)),
    }


def _water_heater_time_discomfort(
    appliances: dict,
    wh_cfg: dict,
    weights: dict,
) -> dict:
    missing = []
    notes = [
        "wh_model: shiftable_load_proxy",
        "wh_tank_model_available: false",
        "todo: Add tank-temperature or readiness forecast later if available.",
    ]
    if appliances.get("water_heater_preheat") is False:
        return {
            "total": 1.0,
            "active": True,
            "start_h": None,
            "finish_h": None,
            "missing": [],
            "notes": notes + ["explicit_wh_disable_uses_max_time_inconvenience"],
        }

    start_h = _float_or_none(appliances.get("water_heater_preheat_start_h"))
    end_h = _float_or_none(appliances.get("water_heater_preheat_end_h"))
    bath_h = _float_or_none(wh_cfg.get("bath_required_h"))
    pref_lo = _float_or_none(wh_cfg.get("pre_heat_window_start_h"))
    pref_hi = _float_or_none(wh_cfg.get("pre_heat_window_end_h"))
    if start_h is None or end_h is None:
        missing.append("water_heater_preheat_schedule")
        return {
            "total": 0.0,
            "active": True,
            "start_h": None,
            "finish_h": None,
            "missing": missing,
            "notes": notes + ["WH timing active but no candidate preheat schedule was supplied."],
        }
    if bath_h is None:
        missing.append("water_heater_bath_required_h")
    if pref_lo is None or pref_hi is None:
        pref_lo = start_h
        pref_hi = end_h
        notes.append("wh_preferred_window_proxy: candidate_schedule")
    finish_h = end_h
    allowed_window = _window_length_h(pref_lo, bath_h if bath_h is not None else pref_hi)
    d_win = (_positive(pref_lo - start_h) + _positive(finish_h - pref_hi)) / allowed_window
    if bath_h is not None:
        d_win += _positive(finish_h - bath_h) / allowed_window
    q_time = _float(weights.get("q_time"), 2.0)
    total = d_win**q_time
    return {
        "total": float(total),
        "active": True,
        "start_h": float(start_h),
        "finish_h": float(finish_h % 24.0),
        "d_win": float(d_win),
        "q_time": float(q_time),
        "missing": missing,
        "notes": notes,
    }


def _one_task_time_discomfort(
    name: str,
    appliances: dict,
    state: dict,
    app_cfg: dict,
    weights: dict,
) -> dict:
    missing = []
    notes = []
    if appliances.get(f"{name}_skip") is True:
        return {
            "total": 1.0,
            "active": True,
            "start_h": None,
            "finish_h": None,
            "missing": [],
            "notes": ["explicit_skip_uses_max_time_inconvenience"],
        }

    start_h = _float_or_none(appliances.get(f"{name}_start_h"))
    if start_h is None:
        start_h = _current_or_default_start_h(name, state, _float(app_cfg.get("preferred_h"), 14.0))
        notes.append("candidate_start_proxy: preferred_or_existing_schedule")
    duration_h = _float(app_cfg.get("duration_h"), 1.0)
    finish_h = start_h + duration_h
    pref_lo = _float_or_none(app_cfg.get("preferred_start_h"))
    pref_hi = _float_or_none(app_cfg.get("preferred_end_h"))
    if pref_lo is None or pref_hi is None:
        preferred_h = _float_or_none(app_cfg.get("preferred_h"))
        if preferred_h is None:
            missing.append(f"{name}_preferred_window")
            preferred_h = start_h
        pref_lo = preferred_h
        pref_hi = preferred_h + duration_h
        notes.append("preferred_window_proxy: preferred_h_plus_duration")

    earliest_h = _float_or_none(app_cfg.get("earliest_h"))
    latest_h = _float_or_none(app_cfg.get("deadline_h"))
    if latest_h is None:
        latest_h = _float_or_none(app_cfg.get("latest_h"))
    if earliest_h is None or latest_h is None:
        allowed_window = 24.0
        notes.append("allowed_window_proxy: 24h")
    else:
        allowed_window = max(EPSILON, _window_length_h(earliest_h, latest_h))
    d_win = (_positive(pref_lo - start_h) + _positive(finish_h - pref_hi)) / allowed_window
    q_time = _float(weights.get("q_time"), 2.0)
    task_weight = _float(app_cfg.get("task_weight"), 1.0)
    total = task_weight * (d_win**q_time)
    return {
        "total": float(total),
        "active": True,
        "start_h": float(start_h),
        "finish_h": float(finish_h % 24.0),
        "d_win": float(d_win),
        "q_time": float(q_time),
        "task_weight": float(task_weight),
        "missing": missing,
        "notes": notes,
    }


def _ev_discomfort(appliances: dict, state: dict, cfg: dict) -> dict:
    ev_cfg = _cfg(cfg, "ev")
    if not _present(ev_cfg, default=False):
        return {
            "total": 0.0,
            "active": False,
            "missing": [],
            "notes": ["EV not present."],
        }

    data = _ev_soc_inputs(appliances, state, ev_cfg)
    if data["missing"]:
        return {
            "total": 0.0,
            "active": False,
            "missing": data["missing"],
            "notes": [
                "ev_soc_model: unavailable",
                "ev_term_active: false",
                "todo: Expose EV current SOC, required SOC, battery capacity, charger power, arrival/departure time.",
            ],
        }
    end_soc = _ev_forward_soc(data)
    denom = max(EPSILON, data["required_soc"] - data["current_soc"])
    shortfall = _positive(data["required_soc"] - end_soc) / denom
    total = shortfall**2
    return {
        "total": float(total),
        "active": True,
        "missing": [],
        "notes": ["ev_soc_model: direct_fields"],
        "current_soc": float(data["current_soc"]),
        "projected_departure_soc": float(end_soc),
        "required_soc": float(data["required_soc"]),
        "shortfall_ratio": float(shortfall),
    }


def _grid_peak(
    state: dict,
    hvac_power_kw: float | None,
    shift_power_kw: float,
    dt_h: float,
    proxy_status: dict,
) -> dict:
    target_kw, target_status = _grid_target_kw(state)
    proxy_status["grid_target"] = target_status
    if target_kw is None:
        return {
            "total": 0.0,
            "raw": {},
            "normalized": {},
            "active": False,
            "missing": ["grid_power_target_kw"],
            "notes": ["Peak term inactive because no grid target is available."],
        }
    base_kw = _base_load_kw(state)
    notes = []
    if base_kw is None:
        base_kw = 0.0
        proxy_status["base_load"] = "not_used_controllable_power_only"
        notes.append("base_load_unavailable: using controllable power only for peak term")
    else:
        proxy_status["base_load"] = "direct"
    total_kw = base_kw + (hvac_power_kw or 0.0) + shift_power_kw
    raw = (_positive(total_kw - target_kw) ** 2) * dt_h
    ref = max(EPSILON, target_kw**2)
    if target_status != "direct":
        notes.append(f"grid_target_proxy: {target_status}")
    return {
        "total": float(raw / ref),
        "raw": {
            "peak_penalty": float(raw),
            "total_power_kw": float(total_kw),
            "target_kw": float(target_kw),
            "base_load_kw": float(base_kw),
            "hvac_power_kw": float(hvac_power_kw or 0.0),
            "shift_power_kw": float(shift_power_kw),
        },
        "normalized": {"peak_penalty": float(raw / ref), "reference": float(ref)},
        "active": True,
        "missing": [],
        "notes": notes,
    }


def _dr_contribution(state: dict) -> dict:
    baseline = _float_or_none(state.get("dr_baseline_kw"))
    if baseline is None:
        baseline = _float_or_none((state.get("history") or {}).get("dr_baseline_kw"))
    rebound = _float_or_none(state.get("post_dr_rebound_kw"))
    missing = []
    if baseline is None:
        missing.append("dr_baseline_kw")
    if rebound is None:
        missing.append("post_dr_rebound_kw")
    if missing:
        return {
            "total": 0.0,
            "raw": {},
            "normalized": {"dr_contribution": 0.0},
            "active": False,
            "dr_baseline_available": False,
            "missing": missing,
            "notes": [
                "dr_contribution_active: false",
                "DR baseline and post-DR rebound data are not exposed at decision time.",
            ],
        }
    return {
        "total": 0.0,
        "raw": {"baseline_kw": float(baseline), "post_dr_rebound_kw": float(rebound)},
        "normalized": {"dr_contribution": 0.0},
        "active": False,
        "dr_baseline_available": True,
        "missing": ["actual_dr_reduction_kw"],
        "notes": ["DR baseline exists, but actual reduction trajectory is unavailable pre-simulation."],
    }


def _hvac_slack(state: dict, action: dict, cfg: dict) -> dict:
    setpoint = _float_or_none(action.get("setpoint"))
    indoor = _float_or_none(state.get("temp_c"))
    ac_cfg = _cfg(cfg, "ac")
    missing = []
    if setpoint is None:
        missing.append("candidate_setpoint")
    if setpoint is None:
        return {"total": 0.0, "active": False, "missing": missing, "notes": []}

    setpoint_violation = _positive(22.0 - setpoint) ** 2 + _positive(setpoint - 28.0) ** 2
    safety_lo = _float_or_none(ac_cfg.get("safety_min_c"))
    safety_hi = _float_or_none(ac_cfg.get("safety_max_c"))
    safety_violation = 0.0
    notes = []
    if indoor is not None and safety_lo is not None and safety_hi is not None:
        safety_violation = _positive(safety_lo - indoor) ** 2 + _positive(indoor - safety_hi) ** 2
    else:
        notes.append("severe_indoor_temperature_safety_bound_unavailable")
    total = setpoint_violation + safety_violation
    return {
        "total": float(total),
        "active": True,
        "setpoint_bound_violation": float(setpoint_violation),
        "safety_temperature_violation": float(safety_violation),
        "missing": [],
        "notes": notes,
    }


def _task_slack(appliances: dict, state: dict, cfg: dict) -> dict:
    total = 0.0
    skip_count = 0.0
    deadline_violation_h = 0.0
    details = {}
    any_present = False
    for name in SHIFTABLE_NAMES:
        app_cfg = _cfg(cfg, name)
        if not _present(app_cfg):
            details[name] = {"present": False, "total": 0.0}
            continue
        any_present = True
        if appliances.get(f"{name}_skip") is True:
            skip_count += 1.0
            total += 1.0
            details[name] = {"present": True, "skip": True, "total": 1.0}
            continue
        start_h = _float_or_none(appliances.get(f"{name}_start_h"))
        duration_h = _float(app_cfg.get("duration_h"), 1.0)
        deadline_h = _float_or_none(app_cfg.get("deadline_h"))
        if deadline_h is None:
            deadline_h = _float_or_none(app_cfg.get("latest_h"))
        earliest_h = _float(app_cfg.get("earliest_h"), 8.0)
        if start_h is None or deadline_h is None:
            details[name] = {
                "present": True,
                "skip": False,
                "total": 0.0,
                "missing": ["start_h" if start_h is None else "deadline_h"],
            }
            continue
        abs_start = _abs_hour(start_h, int(state.get("day_idx", 0)), earliest_h, deadline_h)
        abs_finish = abs_start + duration_h
        abs_deadline = _deadline_abs(deadline_h, int(state.get("day_idx", 0)), earliest_h)
        late = _positive(abs_finish - abs_deadline)
        deadline_violation_h += late
        total += late**2
        details[name] = {
            "present": True,
            "skip": False,
            "deadline_violation_h": float(late),
            "total": float(late**2),
        }
    wh_cfg = _cfg(cfg, WATER_HEATER_NAME)
    if _present(wh_cfg):
        any_present = True
        wh = _water_heater_slack(appliances, state, wh_cfg)
        total += wh["total"]
        details[WATER_HEATER_NAME] = wh
    else:
        details[WATER_HEATER_NAME] = {"present": False, "total": 0.0}
    return {
        "total": float(total),
        "active": any_present,
        "skip_count": float(skip_count),
        "deadline_violation_h": float(deadline_violation_h),
        "raw": details,
        "missing": [],
        "notes": ["explicit skip is a high-penalty service violation."],
    }


def _water_heater_slack(appliances: dict, state: dict, wh_cfg: dict) -> dict:
    notes = [
        "wh_model: shiftable_load_proxy",
        "wh_tank_model_available: false",
        "todo: Add tank-temperature or readiness forecast later if available.",
    ]
    if appliances.get("water_heater_preheat") is False:
        return {
            "present": True,
            "skip": True,
            "total": 2.0,
            "missing": [],
            "notes": notes + ["WH explicitly disabled while service is required."],
        }

    start_h = _float_or_none(appliances.get("water_heater_preheat_start_h"))
    end_h = _float_or_none(appliances.get("water_heater_preheat_end_h"))
    bath_h = _float_or_none(wh_cfg.get("bath_required_h"))
    missing = []
    total = 0.0
    late = 0.0
    if bath_h is None:
        missing.append("water_heater_bath_required_h")
    if start_h is None or end_h is None:
        missing.append("water_heater_preheat_schedule")
        if bath_h is not None:
            total += 1.0
    elif bath_h is not None:
        earliest = _float(wh_cfg.get("pre_heat_window_start_h"), start_h)
        abs_end = _abs_hour(end_h, int(state.get("day_idx", 0)), earliest, bath_h)
        abs_bath = _deadline_abs(bath_h, int(state.get("day_idx", 0)), earliest)
        late = _positive(abs_end - abs_bath)
        total += late**2

    ready_at_bath = _water_heater_ready_at_bath(state)
    if ready_at_bath is False:
        total += 1.0
    return {
        "present": True,
        "skip": False,
        "ready_at_bath": ready_at_bath,
        "deadline_violation_h": float(late),
        "total": float(total),
        "missing": missing,
        "notes": notes,
    }


def _ev_slack(appliances: dict, state: dict, cfg: dict) -> dict:
    ev_cfg = _cfg(cfg, "ev")
    if not _present(ev_cfg, default=False):
        return {"total": 0.0, "active": False, "missing": [], "notes": ["EV not present."]}
    data = _ev_soc_inputs(appliances, state, ev_cfg)
    if data["missing"]:
        return {
            "total": 0.0,
            "active": False,
            "missing": data["missing"],
            "notes": ["EV SOC slack unavailable because SOC data is missing."],
        }
    end_soc = _ev_forward_soc(data)
    shortfall = _positive(data["required_soc"] - end_soc)
    return {
        "total": float(shortfall**2),
        "active": True,
        "missing": [],
        "notes": ["EV SOC slack uses direct SOC fields."],
        "soc_shortfall": float(shortfall),
    }


def _grid_slack(
    state: dict,
    hvac_power_kw: float | None,
    shift_power_kw: float,
    proxy_status: dict,
) -> dict:
    grid_max = _float_or_none(state.get("grid_power_max_kw"))
    if grid_max is None:
        return {
            "total": 0.0,
            "active": False,
            "missing": ["grid_power_max_kw"],
            "notes": ["Grid max unavailable; grid slack inactive."],
        }
    base_kw = _base_load_kw(state) or 0.0
    if proxy_status.get("base_load") == "unavailable":
        proxy_status["base_load"] = "zero_proxy_for_grid_slack"
    total_kw = base_kw + (hvac_power_kw or 0.0) + shift_power_kw
    excess = _positive(total_kw - grid_max)
    return {
        "total": float(excess**2),
        "active": True,
        "missing": [],
        "notes": [],
        "total_power_kw": float(total_kw),
        "grid_power_max_kw": float(grid_max),
        "excess_kw": float(excess),
    }


def _price(state: dict) -> tuple[float, str]:
    for key in ("price", "price_per_kwh", "electricity_price", "tou_price"):
        value = _float_or_none(state.get(key))
        if value is not None:
            return max(0.0, value), "direct"
    prices = state.get("price_forecast") or state.get("tou_price_forecast")
    if isinstance(prices, list) and prices:
        value = _float_or_none(prices[0])
        if value is not None:
            return max(0.0, value), "direct_forecast_first_step"
    return 1.0, "constant_1.0"


def _hvac_power_kw(action: dict, state: dict) -> tuple[float | None, bool, list[str]]:
    explicit = _float_or_none(state.get("hvac_power_kw"))
    if explicit is not None:
        return max(0.0, explicit), False, ["hvac_power_kw from state"]
    setpoint = _float_or_none(action.get("setpoint"))
    indoor = _float_or_none(state.get("temp_c"))
    outdoor = _float_or_none(state.get("outdoor_temp_c"))
    if setpoint is None or indoor is None or outdoor is None:
        return None, False, ["HVAC proxy unavailable without setpoint, indoor temp, and outdoor temp."]
    cooling_lift = max(0.0, outdoor - setpoint)
    zone_need = max(0.0, indoor - setpoint)
    power = max(0.0, 0.12 * cooling_lift + 0.3 * zone_need)
    return power, True, ["Reused lightweight decision-time HVAC power proxy."]


def _shiftable_power_kw(appliances: dict, state: dict, cfg: dict) -> tuple[float, dict]:
    total_kw = 0.0
    details = {}
    for name in SHIFTABLE_NAMES:
        app_cfg = _cfg(cfg, name)
        if not _present(app_cfg):
            details[name] = {"present": False, "power_kw": 0.0}
            continue
        if appliances.get(f"{name}_skip") is True:
            details[name] = {"present": True, "skipped": True, "power_kw": 0.0}
            continue
        start_h = _float_or_none(appliances.get(f"{name}_start_h"))
        if start_h is None:
            details[name] = {"present": True, "scheduled": False, "power_kw": 0.0}
            continue
        power_kw = _float(app_cfg.get("power_kw"), 1.0)
        total_kw += power_kw
        details[name] = {
            "present": True,
            "scheduled": True,
            "start_h": float(start_h),
            "power_kw": float(power_kw),
        }
    return total_kw, details


def _water_heater_power_kw(appliances: dict, cfg: dict) -> tuple[float, dict]:
    wh_cfg = _cfg(cfg, WATER_HEATER_NAME)
    if not _present(wh_cfg):
        return 0.0, {
            "present": False,
            "scheduled": False,
            "power_kw": 0.0,
            "wh_model": "shiftable_load_proxy",
            "wh_tank_model_available": False,
        }
    if appliances.get("water_heater_preheat") is False:
        return 0.0, {
            "present": True,
            "scheduled": False,
            "skipped": True,
            "power_kw": 0.0,
            "wh_model": "shiftable_load_proxy",
            "wh_tank_model_available": False,
        }
    start_h = _float_or_none(appliances.get("water_heater_preheat_start_h"))
    end_h = _float_or_none(appliances.get("water_heater_preheat_end_h"))
    scheduled = bool(appliances.get("water_heater_preheat") is True or start_h is not None or end_h is not None)
    power_kw = _float(wh_cfg.get("rated_kw"), _float(wh_cfg.get("power_kw"), 0.0))
    active_power = max(0.0, power_kw) if scheduled else 0.0
    return active_power, {
        "present": True,
        "scheduled": scheduled,
        "power_kw": float(active_power),
        "rated_kw": float(max(0.0, power_kw)),
        "start_h": None if start_h is None else float(start_h),
        "end_h": None if end_h is None else float(end_h),
        "preheat": appliances.get("water_heater_preheat"),
        "preheat_temp_c": _float_or_none(appliances.get("water_heater_preheat_temp_c")),
        "wh_model": "shiftable_load_proxy",
        "wh_tank_model_available": False,
        "todo": "Add tank-temperature or readiness forecast later if available.",
    }


def _ev_power_kw(appliances: dict, state: dict, cfg: dict) -> tuple[float, dict]:
    ev_cfg = _cfg(cfg, "ev")
    if not _present(ev_cfg, default=False):
        return 0.0, {"present": False, "power_kw": 0.0}
    start_h = _float_or_none(appliances.get("ev_charge_start_h"))
    end_h = _float_or_none(appliances.get("ev_charge_end_h"))
    mode = appliances.get("ev_mode")
    if start_h is None or end_h is None:
        return 0.0, {"present": True, "scheduled": False, "power_kw": 0.0}
    power_kw = _float(ev_cfg.get("charger_kw"), _float(ev_cfg.get("power_kw"), 0.0))
    return max(0.0, power_kw), {
        "present": True,
        "scheduled": True,
        "power_kw": float(max(0.0, power_kw)),
        "mode": mode,
    }


def _comfort_bounds(ac_cfg: dict) -> tuple[float | None, float | None, list[str]]:
    notes = []
    lo = _float_or_none(ac_cfg.get("comfort_min_c"))
    hi = _float_or_none(ac_cfg.get("comfort_max_c"))
    if lo is not None and hi is not None:
        return lo, hi, notes
    pref_min = _float_or_none(ac_cfg.get("setpoint_preferred_min_c"))
    pref_max = _float_or_none(ac_cfg.get("setpoint_preferred_max_c"))
    tol = _float_or_none(ac_cfg.get("temp_tolerance_c"))
    if pref_min is not None and pref_max is not None:
        if tol is None:
            tol = 1.5
            notes.append("comfort_band_proxy: preferred_range_plus_default_tolerance")
        else:
            notes.append("comfort_band_proxy: preferred_range_plus_config_tolerance")
        return pref_min - tol, pref_max + tol, notes
    preferred = _preferred_setpoint(ac_cfg)
    if preferred is None:
        return None, None, notes
    band = _float(ac_cfg.get("comfort_band_c"), 2.0)
    notes.append("comfort_band_proxy: preferred_center_plus_default_band")
    return preferred - band / 2.0, preferred + band / 2.0, notes


def _preferred_setpoint(ac_cfg: dict) -> float | None:
    preferred = _float_or_none(ac_cfg.get("setpoint_preferred_c"))
    if preferred is not None:
        return preferred
    pref_min = _float_or_none(ac_cfg.get("setpoint_preferred_min_c"))
    pref_max = _float_or_none(ac_cfg.get("setpoint_preferred_max_c"))
    if pref_min is not None and pref_max is not None:
        return (pref_min + pref_max) / 2.0
    return None


def _ev_soc_inputs(appliances: dict, state: dict, ev_cfg: dict) -> dict:
    fields = {
        "current_soc": _first_float(state, ev_cfg, "ev_current_soc", "current_soc", "soc_current", "soc"),
        "required_soc": _first_float(state, ev_cfg, "ev_required_soc", "required_soc", "target_soc", "soc_required"),
        "capacity_kwh": _first_float(state, ev_cfg, "ev_capacity_kwh", "capacity_kwh", "battery_capacity_kwh"),
        "charger_kw": _first_float(state, ev_cfg, "ev_charger_kw", "charger_kw", "power_kw"),
        "arrival_h": _first_float(state, ev_cfg, "ev_arrival_h", "arrival_h"),
        "departure_h": _first_float(state, ev_cfg, "ev_departure_h", "departure_h"),
    }
    missing = [f"ev_{name}" for name, value in fields.items() if value is None]
    fields["missing"] = missing
    start_h = _float_or_none(appliances.get("ev_charge_start_h"))
    end_h = _float_or_none(appliances.get("ev_charge_end_h"))
    fields["start_h"] = start_h
    fields["end_h"] = end_h
    fields["efficiency"] = _float(ev_cfg.get("efficiency"), 0.90)
    if start_h is None:
        fields["missing"].append("ev_charge_start_h")
    if end_h is None:
        fields["missing"].append("ev_charge_end_h")
    return fields


def _ev_forward_soc(data: dict) -> float:
    start_h = float(data["start_h"])
    end_h = float(data["end_h"])
    arrival_h = float(data["arrival_h"])
    departure_h = float(data["departure_h"])
    abs_start = _abs_hour(start_h, 0, arrival_h, departure_h)
    abs_end = _abs_hour(end_h, 0, arrival_h, departure_h)
    if abs_end <= abs_start:
        abs_end += 24.0
    duration_h = max(0.0, abs_end - abs_start)
    added_soc = duration_h * float(data["charger_kw"]) * float(data["efficiency"]) / max(
        EPSILON, float(data["capacity_kwh"])
    )
    return min(1.0, float(data["current_soc"]) + added_soc)


def _grid_target_kw(state: dict) -> tuple[float | None, str]:
    direct = _float_or_none(state.get("grid_power_target_kw"))
    if direct is not None:
        return max(0.0, direct), "direct"
    event = state.get("vpp_event") or {}
    target_kwh = _float_or_none(state.get("vpp_target_kwh"))
    start = _float_or_none(event.get("trigger_h"))
    end = _float_or_none(event.get("end_h"))
    if target_kwh is not None and start is not None and end is not None:
        duration_h = max(EPSILON, end - start)
        return max(0.0, target_kwh / duration_h), "vpp_target_kwh_to_average_power"
    return None, "unavailable"


def _base_load_kw(state: dict) -> float | None:
    for source in (state, state.get("history") or {}):
        for key in ("base_load_kw", "base_load_forecast_kw"):
            value = _float_or_none(source.get(key))
            if value is not None:
                return max(0.0, value)
        value_kwh = _float_or_none(source.get("base_load_forecast_kwh"))
        if value_kwh is not None:
            dt_h = max(EPSILON, _float(state.get("dt_h"), 1.0))
            return max(0.0, value_kwh / dt_h)
    return None


def _water_heater_ready_at_bath(state: dict) -> bool | None:
    summary = state.get("appliance_vpp_summary") or {}
    if isinstance(summary, dict):
        wh_summary = summary.get(WATER_HEATER_NAME)
        if isinstance(wh_summary, dict) and "ready_at_bath" in wh_summary:
            return bool(wh_summary.get("ready_at_bath"))
    day_idx = int(state.get("day_idx", 0))
    results = (state.get("appliance_results") or {}).get(WATER_HEATER_NAME, [])
    if 0 <= day_idx < len(results):
        day_result = results[day_idx]
        if isinstance(day_result, dict) and "ready_at_bath" in day_result:
            return bool(day_result.get("ready_at_bath"))
    return None


def _current_or_default_start_h(name: str, state: dict, preferred_h: float) -> float:
    day_idx = int(state.get("day_idx", 0))
    results = (state.get("appliance_results") or {}).get(name, [])
    if 0 <= day_idx < len(results):
        scheduled = _float_or_none(results[day_idx].get("scheduled_abs_h"))
        if scheduled is not None:
            return scheduled % 24.0
    return preferred_h


def _cfg(config: dict, name: str) -> dict:
    value = config.get(name, {}) if isinstance(config, dict) else {}
    return value if isinstance(value, dict) else {}


def _present(config: dict, default: bool = True) -> bool:
    return bool(config.get("present", default))


def _first_float(state: dict, cfg: dict, *keys: str) -> float | None:
    for key in keys:
        value = _float_or_none(state.get(key))
        if value is not None:
            return value
        value = _float_or_none(cfg.get(key))
        if value is not None:
            return value
    return None


def _float(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _float_or_none(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _positive(value: float) -> float:
    return max(0.0, float(value))


def _window_length_h(start_h: float, end_h: float) -> float:
    length = end_h - start_h
    if length <= 0.0:
        length += 24.0
    return max(EPSILON, length)


def _abs_hour(hod: float, day_idx: int, earliest_h: float, latest_h: float) -> float:
    base = day_idx * 24.0
    if latest_h < earliest_h and hod < earliest_h:
        return base + 24.0 + hod
    return base + hod


def _deadline_abs(deadline_h: float, day_idx: int, earliest_h: float) -> float:
    base = day_idx * 24.0
    if deadline_h < earliest_h:
        return base + 24.0 + deadline_h
    return base + deadline_h
