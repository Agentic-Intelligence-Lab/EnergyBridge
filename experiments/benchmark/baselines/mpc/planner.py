"""PDF v1.5 discrete-enumeration MPC baseline for the family benchmark."""

from __future__ import annotations

from ..home_objective_v15 import compute_home_objective_v15
from ..weights import pdf_v15_weights


APPLIANCE_ACTION_KEYS = (
    "washer_start_h",
    "washer_skip",
    "dishwasher_start_h",
    "dishwasher_skip",
    "dryer_start_h",
    "dryer_skip",
    "water_heater_preheat_start_h",
    "water_heater_preheat_end_h",
    "water_heater_preheat_temp_c",
    "water_heater_preheat",
    "ev_mode",
    "ev_charge_start_h",
    "ev_charge_end_h",
)

SHIFTABLE_NAMES = ("washer", "dishwasher", "dryer")
MPC_HORIZON_STEPS = 6
_DYNAMIC_SCORERS = {}
_ENERGYPLUS_SCORERS = {}
_DYNAMIC_SCORER_FAILED = False
_ENERGYPLUS_SCORER_FAILED = False


def plan_mpc_action(
    *,
    state: dict,
    weights: dict | None = None,
) -> dict:
    """Plan one benchmark-compatible MPC action with PDF v1.5 scoring."""
    if weights is None:
        weights = pdf_v15_weights(dr_event=bool(state.get("vpp_active")))
    else:
        weights = dict(weights)
    action = _empty_action(state)

    action = _choose_best_setpoint(action, state, weights)
    for name in SHIFTABLE_NAMES:
        action = _choose_best_shiftable(name, action, state, weights)
    action = _choose_best_ev(action, state, weights)
    action = _choose_best_water_heater(action, state, weights)

    terms = action.get("objective_terms")
    if terms is None:
        _, terms = _score_action(action, state, weights)
    action["objective_terms"] = terms
    action["reason"] = f"mpc_pdf_v15 total={terms['total']:.3f}"
    return action


def _empty_action(state: dict) -> dict:
    current_sp = _float_or_none(state.get("current_setpoint_c"))
    setpoint = current_sp if current_sp is not None else 26.0
    return {
        "setpoint": round(float(setpoint), 1),
        "next_check_hour": None,
        "reason": "mpc_pdf_v15",
        "appliances": {key: None for key in APPLIANCE_ACTION_KEYS},
    }


def _choose_best_setpoint(
    action: dict,
    state: dict,
    weights: dict,
) -> dict:
    current = _float_or_none(state.get("current_setpoint_c"))
    cfg = (state.get("appliance_config") or {}).get("ac", {}) or {}
    pref_min = _float(cfg.get("setpoint_preferred_min_c"), 24.0)
    pref_max = _float(cfg.get("setpoint_preferred_max_c"), 26.0)
    candidates = {25.5, 26.0, 26.5, 27.0, 27.5, pref_min, pref_max}
    if current is not None:
        candidates.update({current - 0.5, current, current + 0.5})
    if state.get("vpp_active"):
        candidates.update({pref_max, pref_max + 0.5, pref_max + 1.0})
    valid = [round(max(22.0, min(28.0, c)), 1) for c in candidates]
    if state.get("vpp_active"):
        vpp_floor = round(max(pref_max + 0.5, current or pref_max), 1)
        raised = [sp for sp in valid if sp >= min(28.0, vpp_floor)]
        if raised:
            valid = raised
    return _best_action(
        ({**action, "setpoint": sp} for sp in sorted(set(valid))),
        state,
        weights,
    )


def _choose_best_shiftable(
    name: str,
    action: dict,
    state: dict,
    weights: dict,
) -> dict:
    cfg = ((state.get("appliance_config") or {}).get(name, {}) or {})
    if not cfg.get("present", True):
        return action
    if _shiftable_locked_for_today(name, state):
        return action

    day_idx = int(state.get("day_idx", 0))
    sim_h = _float(state.get("sim_h"), day_idx * 24.0)
    earliest_h = _float(cfg.get("earliest_h"), 8.0)
    latest_h = _float(cfg.get("latest_h"), 22.0)
    preferred_h = _float(cfg.get("preferred_h"), 14.0)
    duration_h = _float(cfg.get("duration_h"), 1.0)
    flexible = bool(cfg.get("shiftable", True)) and bool(cfg.get("dr_adjustable", True))
    starts = {preferred_h}

    if flexible:
        event = state.get("vpp_event") or {}
        vpp_start = _float_or_none(event.get("trigger_h"))
        vpp_end = _float_or_none(event.get("end_h"))
        if vpp_start is not None and vpp_end is not None:
            starts.add((vpp_start % 24.0) - duration_h)
            starts.add(vpp_end % 24.0)

    results = (state.get("appliance_results") or {}).get(name, [])
    if 0 <= day_idx < len(results):
        scheduled = _float_or_none(results[day_idx].get("scheduled_abs_h"))
        if scheduled is not None:
            starts.add(scheduled % 24.0)

    candidates = []
    for start_h in sorted({_wrap_hour(s) for s in starts}):
        abs_h = _abs_hour(start_h, day_idx, earliest_h, latest_h)
        if abs_h + 1e-6 < sim_h:
            continue
        if not _fits_window(start_h, day_idx, earliest_h, latest_h, duration_h):
            continue
        candidate = _clone(action)
        candidate["appliances"][f"{name}_start_h"] = round(start_h, 2)
        candidate["appliances"][f"{name}_skip"] = False
        candidates.append(candidate)

    return _best_action(candidates or [action], state, weights)


def _choose_best_ev(
    action: dict,
    state: dict,
    weights: dict,
) -> dict:
    cfg = ((state.get("appliance_config") or {}).get("ev", {}) or {})
    if not cfg.get("present", False):
        return action
    candidates = []
    arrival_h = _float(cfg.get("arrival_h"), 18.0)
    departure_h = _float(cfg.get("departure_h"), 7.5)
    for start_h in sorted({_wrap_hour(arrival_h), 20.0, 22.0}):
        window = _clone(action)
        window["appliances"]["ev_mode"] = None
        window["appliances"]["ev_charge_start_h"] = start_h
        window["appliances"]["ev_charge_end_h"] = departure_h
        candidates.append(window)
    return _best_action(candidates, state, weights)


def _choose_best_water_heater(
    action: dict,
    state: dict,
    weights: dict,
) -> dict:
    cfg = ((state.get("appliance_config") or {}).get("water_heater", {}) or {})
    if not cfg.get("present", True):
        return action
    candidates = []
    bath_h = _float(cfg.get("bath_required_h"), 21.0)
    default_start = _float(cfg.get("pre_heat_window_start_h"), 15.0)
    default_end = _float(cfg.get("pre_heat_window_end_h"), 18.0)
    flexible = bool(cfg.get("dr_adjustable", True))
    windows = [(default_start, default_end)]
    if flexible:
        windows.extend(
            [
                (15.0, 17.0),
                (16.0, 17.5),
                (max(0.0, bath_h - 4.0), max(0.0, bath_h - 2.0)),
            ]
        )
    for start_h, end_h in windows:
        candidate = _clone(action)
        candidate["appliances"]["water_heater_preheat"] = True
        candidate["appliances"]["water_heater_preheat_start_h"] = start_h
        candidate["appliances"]["water_heater_preheat_end_h"] = end_h
        candidate["appliances"]["water_heater_preheat_temp_c"] = 65.0
        candidates.append(candidate)
    return _best_action(candidates, state, weights)


def _best_action(candidates, state: dict, weights: dict) -> dict:
    best = None
    best_total = None
    best_terms = None
    for candidate in candidates:
        total, terms = _score_action(candidate, state, weights)
        if best is None or total < best_total:
            best = candidate
            best_total = total
            best_terms = terms
    out = _clone(best)
    if best_terms is not None:
        out["objective_terms"] = best_terms
    return out


def _score_action(candidate: dict, state: dict, weights: dict) -> tuple[float, dict]:
    stage_states = [state]
    dynamic_diag = None
    scorer = _scorer_for_state(state)
    if scorer is not None:
        try:
            stage_states, dynamic_diag = scorer.predict_objective_trajectory(state, candidate)
        except Exception as exc:
            dynamic_diag = {
                "model": _predictor_model_name(state),
                "status": "fallback_current_state",
                "error": str(exc)[:160],
            }
    step_terms = [
        compute_home_objective_v15(action=candidate, state=score_state, weights=weights)
        for score_state in stage_states
    ]
    terms = _accumulate_step_terms(step_terms)
    if dynamic_diag is not None:
        terms = dict(terms)
        diagnostics = dict(terms.get("diagnostics") or {})
        diagnostics["dynamic_model_prediction"] = dynamic_diag
        diagnostics["objective_rollout"] = {
            "kind": "finite_horizon_cumulative_cost",
            "horizon_steps": len(step_terms),
            "stage_totals": [round(float(item["total"]), 6) for item in step_terms],
        }
        terms["diagnostics"] = diagnostics
    return float(terms["total"]), terms


def _scorer_for_state(state: dict):
    predictor = str(state.get("mpc_predictor") or "dynamic").lower()
    horizon_steps = _mpc_horizon_steps(state)
    if predictor == "energyplus":
        return _energyplus_scorer(horizon_steps)
    return _dynamic_scorer(horizon_steps)


def _predictor_model_name(state: dict) -> str:
    predictor = str(state.get("mpc_predictor") or "dynamic").lower()
    if predictor == "energyplus":
        return "energyplus_horizon_predictor_v1"
    return "deterministic_expected_mpc_dynamic_model_v1"


def _mpc_horizon_steps(state: dict) -> int:
    return max(1, int(_float(state.get("mpc_horizon_steps"), MPC_HORIZON_STEPS)))


def _dynamic_scorer(horizon_steps: int):
    global _DYNAMIC_SCORER_FAILED
    if _DYNAMIC_SCORER_FAILED:
        return None
    if horizon_steps not in _DYNAMIC_SCORERS:
        try:
            from .dynamic_model import DynamicModelScorer

            _DYNAMIC_SCORERS[horizon_steps] = DynamicModelScorer(horizon_steps=horizon_steps)
        except Exception:
            _DYNAMIC_SCORER_FAILED = True
            return None
    return _DYNAMIC_SCORERS[horizon_steps]


def _energyplus_scorer(horizon_steps: int):
    global _ENERGYPLUS_SCORER_FAILED
    if _ENERGYPLUS_SCORER_FAILED:
        return None
    if horizon_steps not in _ENERGYPLUS_SCORERS:
        try:
            from .ep_predictor import EnergyPlusHorizonScorer

            _ENERGYPLUS_SCORERS[horizon_steps] = EnergyPlusHorizonScorer(horizon_steps=horizon_steps)
        except Exception:
            _ENERGYPLUS_SCORER_FAILED = True
            return None
    return _ENERGYPLUS_SCORERS[horizon_steps]


def _accumulate_step_terms(step_terms: list[dict]) -> dict:
    first = step_terms[0]
    last = step_terms[-1]
    cost_total = sum(float(item["cost"]["total"]) for item in step_terms)
    user_total = sum(float(item["user"]["total"]) for item in step_terms)
    grid_total = sum(float(item["grid"]["total"]) for item in step_terms)
    slack_total = sum(float(item["slack"]["total"]) for item in step_terms)
    diagnostics = dict(last.get("diagnostics") or {})
    diagnostics["objective_rollout"] = {
        "kind": "finite_horizon_cumulative_cost",
        "horizon_steps": len(step_terms),
        "stage_totals": [round(float(item["total"]), 6) for item in step_terms],
    }
    return {
        "version": first["version"],
        "total": sum(float(item["total"]) for item in step_terms),
        "cost": {**dict(last["cost"]), "total": cost_total},
        "user": {**dict(last["user"]), "total": user_total},
        "grid": {**dict(last["grid"]), "total": grid_total},
        "slack": {**dict(last["slack"]), "total": slack_total},
        "weights": dict(first["weights"]),
        "proxy_status": dict(last["proxy_status"]),
        "active_terms": sorted({term for item in step_terms for term in item.get("active_terms", [])}),
        "inactive_or_missing_terms": sorted(
            {term for item in step_terms for term in item.get("inactive_or_missing_terms", [])}
        ),
        "diagnostics": diagnostics,
    }


def _shiftable_locked_for_today(name: str, state: dict) -> bool:
    day_idx = int(state.get("day_idx", 0))
    results = (state.get("appliance_results") or {}).get(name, [])
    if 0 <= day_idx < len(results):
        day_result = results[day_idx]
        if day_result.get("completed") or day_result.get("skipped"):
            return True

    prefix = f"{name}:"
    for line in state.get("appliance_status_lines") or []:
        if not isinstance(line, str) or not line.startswith(prefix):
            continue
        if "done_today" in line or "RUNNING" in line or "skipped_today" in line:
            return True
    return False


def _clone(action: dict) -> dict:
    return {
        "setpoint": action.get("setpoint"),
        "next_check_hour": action.get("next_check_hour"),
        "reason": action.get("reason", ""),
        "appliances": dict(action.get("appliances") or {}),
        **({"objective_terms": dict(action["objective_terms"])} if "objective_terms" in action else {}),
    }


def _fits_window(start_h: float, day_idx: int, earliest_h: float, latest_h: float, duration_h: float) -> bool:
    abs_start = _abs_hour(start_h, day_idx, earliest_h, latest_h)
    _, abs_latest = _window_abs(day_idx, earliest_h, latest_h)
    return abs_start + duration_h <= abs_latest + 1e-6


def _abs_hour(hod: float, day_idx: int, earliest_h: float, latest_h: float) -> float:
    base = day_idx * 24.0
    if latest_h < earliest_h and hod < earliest_h:
        return base + 24.0 + hod
    return base + hod


def _window_abs(day_idx: int, earliest_h: float, latest_h: float) -> tuple[float, float]:
    base = day_idx * 24.0
    end = base + latest_h
    if latest_h < earliest_h:
        end += 24.0
    return base + earliest_h, end


def _wrap_hour(value: float) -> float:
    return float(value) % 24.0


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
