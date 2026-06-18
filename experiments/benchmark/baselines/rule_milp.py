"""Rule + MILP oracle-style baseline for the family benchmark.

HVAC uses a transparent PMV comfort rule: pick the warmest allowed cooling
setpoint whose steady-state PMV is still acceptable, because that is the
lowest-cost cooling policy inside the user's comfort envelope.  Independent
appliances are scheduled with a small mixed-integer program over feasible
service windows.  If PuLP is unavailable, the same independent binary choice
problem is solved by exact enumeration.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from math import ceil
from typing import Any


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
BIG_VPP_PENALTY = 10_000.0
GRID_H = 0.5


def plan_rule_milp_action(
    *,
    state: dict,
    price_profile: Any = None,
    run_start_date: Any = None,
) -> dict:
    """Return an LLM-compatible action for the rule+MILP baseline."""
    action = {
        "setpoint": _choose_pmv_cost_min_setpoint(state),
        "next_check_hour": None,
        "reason": "rule_milp",
        "appliances": {key: None for key in APPLIANCE_ACTION_KEYS},
    }
    groups = _build_candidate_groups(
        state,
        price_profile=price_profile,
        run_start_date=run_start_date,
    )
    selected, solver_meta = _solve_binary_choice_milp(groups)
    total_cost = 0.0
    for candidate in selected:
        total_cost += float(candidate.get("objective", 0.0))
        action["appliances"].update(candidate.get("appliances") or {})
    action["objective_terms"] = {
        "version": "rule_milp_cost_min_v1",
        "total": total_cost,
        "cost": {"total": total_cost, "raw": {"candidate_cost": total_cost}},
        "user": {"total": 0.0},
        "grid": {"total": 0.0},
        "slack": {"total": 0.0},
        "weights": {"cost": 1.0},
        "proxy_status": {
            "price": "direct" if price_profile is not None and run_start_date is not None else "flat_energy_proxy",
            "hvac": "pmv_rule",
            "appliance_schedule": solver_meta.get("solver", "unknown"),
        },
        "active_terms": ["cost.shiftable", "user.service", "user.pmv"],
        "inactive_or_missing_terms": [],
        "diagnostics": {
            "formula": "min sum(price_t * appliance_power_t) with service and no-VPP-overlap penalties",
            "solver": solver_meta,
            "candidate_groups": {
                group: [
                    {
                        "label": cand.get("label"),
                        "objective": round(float(cand.get("objective", 0.0)), 6),
                        "cost": round(float(cand.get("cost", 0.0)), 6),
                        "vpp_penalty": round(float(cand.get("vpp_penalty", 0.0)), 6),
                    }
                    for cand in candidates
                ]
                for group, candidates in groups.items()
            },
        },
    }
    action["reason"] = (
        f"rule_milp {solver_meta.get('solver', 'solver')} "
        f"cost={total_cost:.3f} sp={action['setpoint']:.1f}"
    )
    return action


def _choose_pmv_cost_min_setpoint(state: dict) -> float:
    cfg = ((state.get("appliance_config") or {}).get("ac", {}) or {})
    pref_min = _float(cfg.get("setpoint_preferred_min_c"), 24.0)
    pref_max = _float(cfg.get("setpoint_preferred_max_c"), 26.0)
    tol = _float(cfg.get("temp_tolerance_c"), 1.0)
    allowed_min = max(22.0, pref_min - tol)
    allowed_max = min(28.0, pref_max + tol)
    candidates = [
        round(allowed_min + idx * 0.5, 1)
        for idx in range(int(round((allowed_max - allowed_min) / 0.5)) + 1)
    ]
    ok = [sp for sp in candidates if abs(_pmv(sp)) <= 0.5]
    if ok:
        return max(ok)
    current = _float(state.get("current_setpoint_c"), (pref_min + pref_max) / 2.0)
    temp = _float(state.get("temp_c"), current)
    if _pmv(temp) > 0.5:
        return round(max(allowed_min, current - 0.5), 1)
    if _pmv(temp) < -0.5:
        return round(min(allowed_max, current + 0.5), 1)
    return round(min(allowed_max, max(allowed_min, current)), 1)


def _build_candidate_groups(state: dict, *, price_profile: Any, run_start_date: Any) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    cfg = state.get("appliance_config") or {}
    for name in SHIFTABLE_NAMES:
        dev_cfg = cfg.get(name, {}) or {}
        if bool(dev_cfg.get("present", False)) and not _service_locked_for_today(name, state):
            groups[name] = _shiftable_candidates(name, dev_cfg, state, price_profile, run_start_date)
    wh_cfg = cfg.get("water_heater", {}) or {}
    if bool(wh_cfg.get("present", False)) and not _service_locked_for_today("water_heater", state):
        groups["water_heater"] = _water_heater_candidates(wh_cfg, state, price_profile, run_start_date)
    ev_cfg = cfg.get("ev", {}) or {}
    if bool(ev_cfg.get("present", False)) and not _service_locked_for_today("ev", state):
        groups["ev"] = _ev_candidates(ev_cfg, state, price_profile, run_start_date)
    return {name: candidates for name, candidates in groups.items() if candidates}


def _shiftable_candidates(
    name: str,
    cfg: dict,
    state: dict,
    price_profile: Any,
    run_start_date: Any,
) -> list[dict]:
    day_idx = int(state.get("day_idx", 0))
    sim_h = _float(state.get("sim_h"), day_idx * 24.0)
    earliest = _float(cfg.get("earliest_h"), 8.0)
    latest = _float(cfg.get("latest_h"), 22.0)
    preferred = _float(cfg.get("preferred_h"), earliest)
    duration = max(GRID_H, _float(cfg.get("duration_h"), 1.0))
    power_kw = _float(cfg.get("power_kw"), 1.5)
    flexible = bool(cfg.get("shiftable", True)) and bool(cfg.get("dr_adjustable", True))

    if not flexible:
        starts = [_abs_hour(preferred, day_idx, earliest, latest)]
    else:
        start_min, start_max = _window_abs(day_idx, earliest, latest)
        start_max -= duration
        start_min = max(start_min, _ceil_to_grid(sim_h))
        starts = _grid_values(start_min, start_max, GRID_H)
        pref_abs = _abs_hour(preferred, day_idx, earliest, latest)
        if start_min <= pref_abs <= start_max:
            starts.append(pref_abs)
    candidates = []
    for start_abs in sorted(set(round(v, 6) for v in starts)):
        if start_abs + 1e-6 < sim_h:
            continue
        hod = start_abs % 24.0
        appliances = {
            f"{name}_start_h": round(hod, 2),
            f"{name}_skip": False,
        }
        candidates.append(
            _candidate(
                label=f"{name}@{hod:.2f}",
                appliances=appliances,
                start_abs=start_abs,
                duration_h=duration,
                power_kw=power_kw,
                preferred_abs=_abs_hour(preferred, day_idx, earliest, latest),
                state=state,
                price_profile=price_profile,
                run_start_date=run_start_date,
            )
        )
    return candidates


def _water_heater_candidates(
    cfg: dict,
    state: dict,
    price_profile: Any,
    run_start_date: Any,
) -> list[dict]:
    day_idx = int(state.get("day_idx", 0))
    sim_h = _float(state.get("sim_h"), day_idx * 24.0)
    power_kw = _float(cfg.get("rated_kw"), 2.0)
    bath_h = _float(cfg.get("bath_required_h"), 21.0)
    default_start = _float(cfg.get("pre_heat_window_start_h"), max(0.0, bath_h - 4.0))
    default_end = _float(cfg.get("pre_heat_window_end_h"), max(default_start + 1.0, bath_h - 1.0))
    duration = max(GRID_H, default_end - default_start if default_end > default_start else 2.0)
    flexible = bool(cfg.get("dr_adjustable", True))
    bath_abs = day_idx * 24.0 + bath_h
    if not flexible:
        starts = [day_idx * 24.0 + default_start]
    else:
        start_min = max(day_idx * 24.0, _ceil_to_grid(sim_h))
        start_max = max(start_min, bath_abs - duration)
        starts = _grid_values(start_min, start_max, GRID_H)
        default_abs = day_idx * 24.0 + default_start
        if start_min <= default_abs <= start_max:
            starts.append(default_abs)
    candidates = []
    for start_abs in sorted(set(round(v, 6) for v in starts)):
        end_abs = start_abs + duration
        appliances = {
            "water_heater_preheat": True,
            "water_heater_preheat_start_h": round(start_abs % 24.0, 2),
            "water_heater_preheat_end_h": round(end_abs % 24.0, 2),
            "water_heater_preheat_temp_c": 65.0,
        }
        candidates.append(
            _candidate(
                label=f"water_heater@{start_abs % 24.0:.2f}",
                appliances=appliances,
                start_abs=start_abs,
                duration_h=duration,
                power_kw=power_kw,
                preferred_abs=day_idx * 24.0 + default_start,
                state=state,
                price_profile=price_profile,
                run_start_date=run_start_date,
            )
        )
    return candidates


def _ev_candidates(
    cfg: dict,
    state: dict,
    price_profile: Any,
    run_start_date: Any,
) -> list[dict]:
    day_idx = int(state.get("day_idx", 0))
    sim_h = _float(state.get("sim_h"), day_idx * 24.0)
    charger_kw = _float(cfg.get("charger_kw"), 7.0)
    efficiency = _float(cfg.get("efficiency"), 0.92)
    needed_kwh = _ev_needed_kwh(cfg, state)
    duration = max(GRID_H, ceil((needed_kwh / max(0.1, charger_kw * efficiency)) / GRID_H) * GRID_H)
    arrival = _float(cfg.get("arrival_h"), 18.0)
    departure = _float(cfg.get("departure_h"), 7.5)
    start_min = max(day_idx * 24.0 + arrival, _ceil_to_grid(sim_h))
    end_abs = day_idx * 24.0 + departure
    if departure <= arrival:
        end_abs += 24.0
    start_max = end_abs - duration
    starts = _grid_values(start_min, start_max, GRID_H)
    candidates = []
    for start_abs in sorted(set(round(v, 6) for v in starts)):
        end = start_abs + duration
        appliances = {
            "ev_mode": None,
            "ev_charge_start_h": round(start_abs % 24.0, 2),
            "ev_charge_end_h": round(end % 24.0, 2),
        }
        candidates.append(
            _candidate(
                label=f"ev@{start_abs % 24.0:.2f}",
                appliances=appliances,
                start_abs=start_abs,
                duration_h=duration,
                power_kw=charger_kw,
                preferred_abs=end_abs - duration,
                state=state,
                price_profile=price_profile,
                run_start_date=run_start_date,
            )
        )
    if not candidates:
        candidates.append(
            {
                "label": "ev_delay_fallback",
                "appliances": {"ev_mode": None, "ev_charge_start_h": None, "ev_charge_end_h": None},
                "objective": BIG_VPP_PENALTY,
                "cost": 0.0,
                "vpp_penalty": BIG_VPP_PENALTY,
            }
        )
    return candidates


def _candidate(
    *,
    label: str,
    appliances: dict,
    start_abs: float,
    duration_h: float,
    power_kw: float,
    preferred_abs: float,
    state: dict,
    price_profile: Any,
    run_start_date: Any,
) -> dict:
    cost = _interval_cost(start_abs, duration_h, power_kw, price_profile, run_start_date)
    vpp_penalty = BIG_VPP_PENALTY if _overlaps_any_vpp(start_abs, start_abs + duration_h, state) else 0.0
    time_penalty = abs(float(start_abs) - float(preferred_abs)) * 1e-4
    return {
        "label": label,
        "appliances": dict(appliances),
        "objective": float(cost + vpp_penalty + time_penalty),
        "cost": float(cost),
        "vpp_penalty": float(vpp_penalty),
    }


def _solve_binary_choice_milp(groups: dict[str, list[dict]]) -> tuple[list[dict], dict]:
    if not groups:
        return [], {"solver": "no_present_unlocked_services", "status": "empty"}
    try:
        import pulp  # type: ignore

        problem = pulp.LpProblem("rule_milp_appliance_schedule", pulp.LpMinimize)
        variables: dict[tuple[str, int], Any] = {}
        for group, candidates in groups.items():
            group_vars = []
            for idx, candidate in enumerate(candidates):
                var = pulp.LpVariable(f"x_{group}_{idx}", lowBound=0, upBound=1, cat="Binary")
                variables[(group, idx)] = var
                group_vars.append(var)
            problem += pulp.lpSum(group_vars) == 1, f"choose_one_{group}"
        problem += pulp.lpSum(
            float(candidate.get("objective", 0.0)) * variables[(group, idx)]
            for group, candidates in groups.items()
            for idx, candidate in enumerate(candidates)
        )
        status = problem.solve(pulp.PULP_CBC_CMD(msg=False))
        selected = []
        for group, candidates in groups.items():
            chosen_idx = min(
                range(len(candidates)),
                key=lambda idx: (
                    -float(variables[(group, idx)].value() or 0.0),
                    float(candidates[idx].get("objective", 0.0)),
                ),
            )
            selected.append(candidates[chosen_idx])
        return selected, {"solver": "pulp_cbc_milp", "status": pulp.LpStatus.get(status, str(status))}
    except Exception as exc:
        selected = [min(candidates, key=lambda item: float(item.get("objective", 0.0))) for candidates in groups.values()]
        return selected, {"solver": "exact_enumeration_fallback", "status": "ok", "fallback_reason": str(exc)[:120]}


def _service_locked_for_today(name: str, state: dict) -> bool:
    day_idx = int(state.get("day_idx", 0))
    results = (state.get("appliance_results") or {}).get(name, [])
    if 0 <= day_idx < len(results):
        result = results[day_idx]
        if result.get("completed") or result.get("skipped"):
            return True
        if name == "water_heater" and result.get("preheat_used"):
            return True
        if name == "ev" and result.get("target_reached"):
            return True
    prefix = f"{name}:"
    for line in state.get("appliance_status_lines") or []:
        if isinstance(line, str) and line.startswith(prefix):
            if "RUNNING" in line or "done_today" in line or "skipped_today" in line:
                return True
    return False


def _interval_cost(start_abs: float, duration_h: float, power_kw: float, price_profile: Any, run_start_date: Any) -> float:
    step_h = GRID_H
    end_abs = start_abs + duration_h
    t = start_abs
    total = 0.0
    while t < end_abs - 1e-9:
        dt = min(step_h, end_abs - t)
        total += power_kw * dt * _price_at_sim_hour(t, price_profile, run_start_date)
        t += dt
    return total


def _price_at_sim_hour(sim_h: float, price_profile: Any, run_start_date: Any) -> float:
    if price_profile is None or run_start_date is None:
        return 1.0
    try:
        if isinstance(run_start_date, datetime):
            start = run_start_date
        else:
            start = datetime.combine(run_start_date, datetime.min.time())
        price = price_profile.price_at(start + timedelta(hours=float(sim_h)))
        return 1.0 if price is None else float(price)
    except Exception:
        return 1.0


def _overlaps_any_vpp(start_abs: float, end_abs: float, state: dict) -> bool:
    events = []
    event = state.get("vpp_event")
    if isinstance(event, dict):
        events.append(event)
    history_event = (state.get("history") or {}).get("vpp_event")
    if isinstance(history_event, dict):
        events.append(history_event)
    for ev in events:
        try:
            if max(start_abs, float(ev["trigger_h"])) < min(end_abs, float(ev["end_h"])):
                return True
        except (KeyError, TypeError, ValueError):
            continue
    return False


def _ev_needed_kwh(cfg: dict, state: dict) -> float:
    capacity = _float(cfg.get("capacity_kwh"), 60.0)
    target_soc = _float(cfg.get("target_soc"), 0.8)
    daily_drive = _float(cfg.get("daily_drive_kwh"), 8.0)
    current_soc = None
    for line in state.get("appliance_status_lines") or []:
        if not isinstance(line, str) or not line.startswith("ev:"):
            continue
        marker = "SOC="
        if marker in line:
            try:
                current_soc = float(line.split(marker, 1)[1].split("%", 1)[0]) / 100.0
            except (ValueError, IndexError):
                current_soc = None
    if current_soc is None:
        return max(0.0, daily_drive)
    return max(daily_drive, max(0.0, target_soc - current_soc) * capacity)


def _window_abs(day_idx: int, earliest_h: float, latest_h: float) -> tuple[float, float]:
    base = day_idx * 24.0
    end = base + latest_h
    if latest_h < earliest_h:
        end += 24.0
    return base + earliest_h, end


def _abs_hour(hod: float, day_idx: int, earliest_h: float, latest_h: float) -> float:
    base = day_idx * 24.0
    if latest_h < earliest_h and hod < earliest_h:
        return base + 24.0 + hod
    return base + hod


def _grid_values(start: float, end: float, step: float) -> list[float]:
    if end + 1e-9 < start:
        return []
    count = int(round((end - start) / step))
    return [round(start + idx * step, 6) for idx in range(count + 1)]


def _ceil_to_grid(value: float, step: float = GRID_H) -> float:
    return ceil((float(value) - 1e-9) / step) * step


def _pmv(tdb: float, rh: float = 55.0) -> float:
    try:
        from pythermalcomfort.models import pmv_ppd_iso

        result = pmv_ppd_iso(tdb=tdb, tr=tdb, vr=0.1, rh=rh, met=1.1, clo=0.5, limit_inputs=False)
        return float(result.pmv)
    except Exception:
        neutral = 33.5 - 3.5 * 1.1 - 3.0 * 0.5
        return round(0.5 * (float(tdb) - neutral) + (rh - 50.0) * 0.007, 3)


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)
