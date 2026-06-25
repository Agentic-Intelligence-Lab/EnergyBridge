"""Rule + MILP oracle-style baseline for the family benchmark.

HVAC uses a transparent PMV comfort rule: pick the warmest allowed cooling
setpoint whose steady-state PMV is still acceptable, because that is the
lowest-cost cooling policy inside the user's comfort envelope.  Independent
appliances are scheduled with a small mixed-integer program over feasible
service windows.  If PuLP is unavailable, the same independent binary choice
problem is solved by exact enumeration.
"""

from __future__ import annotations

import random
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
            "price": (
                "direct"
                if price_profile is not None and (run_start_date is not None or getattr(price_profile, "is_recurring", False))
                else "flat_energy_proxy"
            ),
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


def plan_rule_milp_options(
    *,
    state: dict,
    price_profile: Any = None,
    run_start_date: Any = None,
    max_options: int = 5,
) -> dict:
    """Return PMV setpoint guidance plus equal-objective MILP appliance options.

    This is intended for hybrid agent methods: Rule+MILP proposes physically
    valid cost/VPP-optimal appliance schedules, then an agent can choose among
    them or make a small user-preference adjustment.
    """
    groups = _build_candidate_groups(
        state,
        price_profile=price_profile,
        run_start_date=run_start_date,
    )
    selected, solver_meta = _solve_binary_choice_milp(groups)
    optimal_groups = {
        name: _optimal_candidates(candidates)
        for name, candidates in groups.items()
        if candidates
    }
    options = _assemble_strategy_options(optimal_groups, max_options=max_options)
    selected_action = {key: None for key in APPLIANCE_ACTION_KEYS}
    for candidate in selected:
        selected_action.update(candidate.get("appliances") or {})
    return {
        "version": "rule_milp_candidate_options_v1",
        "hvac": _pmv_setpoint_options(state),
        "solver": solver_meta,
        "selected_rule_milp_action": selected_action,
        "appliance_candidate_groups": {
            name: [_candidate_public_view(candidate) for candidate in candidates]
            for name, candidates in optimal_groups.items()
        },
        "strategy_options": options,
        "notes": [
            "Each strategy option combines per-appliance equal-objective MILP choices.",
            "Options avoid VPP-window non-AC appliance operation when such a schedule is feasible.",
            "No user preference is encoded here; the agent should select or lightly adjust using user memory.",
        ],
    }


def _pmv_setpoint_options(state: dict) -> dict:
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
    rows = [
        {
            "setpoint_c": sp,
            "pmv": round(_pmv(sp), 3),
            "pmv_ok": abs(_pmv(sp)) <= 0.5,
        }
        for sp in candidates
    ]
    feasible = [row["setpoint_c"] for row in rows if row["pmv_ok"]]
    recommended = max(feasible) if feasible else _choose_pmv_cost_min_setpoint(state)
    return {
        "preferred_range_c": [round(pref_min, 1), round(pref_max, 1)],
        "allowed_range_c": [round(allowed_min, 1), round(allowed_max, 1)],
        "pmv_feasible_setpoints_c": feasible,
        "cost_min_pmv_setpoint_c": round(float(recommended), 1),
        "candidate_setpoints": rows,
        "rule": "choose the warmest PMV-feasible setpoint for pure cost minimization",
    }


def _optimal_candidates(candidates: list[dict]) -> list[dict]:
    if not candidates:
        return []
    min_objective = min(float(item.get("objective", 0.0)) for item in candidates)
    optimal = [
        item
        for item in candidates
        if abs(float(item.get("objective", 0.0)) - min_objective) <= 1e-9
    ]
    return sorted(
        optimal,
        key=lambda item: (
            float(item.get("objective", 0.0)),
            float(item.get("cost", 0.0)),
            float(item.get("start_abs", 10**9)),
            str(item.get("label", "")),
        ),
    )


def _assemble_strategy_options(
    optimal_groups: dict[str, list[dict]],
    *,
    max_options: int,
) -> list[dict]:
    group_names = sorted(name for name, candidates in optimal_groups.items() if candidates)
    if not group_names:
        return [
            {
                "option_id": "milp_opt_1",
                "appliances": {},
                "selected_labels": [],
                "objective_total": 0.0,
                "cost_total": 0.0,
                "vpp_penalty_total": 0.0,
                "optimality": "no_unlocked_present_services",
            }
        ]
    count = max(1, min(int(max_options), max(len(optimal_groups[name]) for name in group_names)))
    options: list[dict] = []
    seen: set[tuple] = set()
    for idx in range(count):
        appliances = {key: None for key in APPLIANCE_ACTION_KEYS}
        selected_labels: list[str] = []
        objective_total = 0.0
        cost_total = 0.0
        vpp_penalty_total = 0.0
        for group_name in group_names:
            candidates = optimal_groups[group_name]
            candidate = candidates[idx % len(candidates)]
            appliances.update(candidate.get("appliances") or {})
            selected_labels.append(str(candidate.get("label", group_name)))
            objective_total += float(candidate.get("objective", 0.0))
            cost_total += float(candidate.get("cost", 0.0))
            vpp_penalty_total += float(candidate.get("vpp_penalty", 0.0))
        non_null = {key: value for key, value in appliances.items() if value is not None}
        signature = tuple(sorted(non_null.items()))
        if signature in seen:
            continue
        seen.add(signature)
        options.append(
            {
                "option_id": f"milp_opt_{len(options) + 1}",
                "appliances": non_null,
                "selected_labels": selected_labels,
                "objective_total": round(objective_total, 6),
                "cost_total": round(cost_total, 6),
                "vpp_penalty_total": round(vpp_penalty_total, 6),
                "optimality": "equal_objective_milp",
            }
        )
    return options


def _candidate_public_view(candidate: dict) -> dict:
    return {
        "label": candidate.get("label"),
        "appliances": {
            key: value
            for key, value in (candidate.get("appliances") or {}).items()
            if value is not None
        },
        "objective": round(float(candidate.get("objective", 0.0)), 6),
        "cost": round(float(candidate.get("cost", 0.0)), 6),
        "vpp_penalty": round(float(candidate.get("vpp_penalty", 0.0)), 6),
        "start_abs": candidate.get("start_abs"),
        "duration_h": candidate.get("duration_h"),
    }


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
    duration = max(GRID_H, _float(cfg.get("duration_h"), 1.0))
    power_kw = _float(cfg.get("power_kw"), 1.5)
    flexible = bool(cfg.get("shiftable", True)) and bool(cfg.get("dr_adjustable", True))

    if not flexible:
        fixed_start = _float(cfg.get("fixed_start_h"), earliest)
        starts = [_abs_hour(fixed_start, day_idx, earliest, latest)]
    else:
        start_min, start_max = _window_abs(day_idx, earliest, latest)
        start_max -= duration
        start_min = max(start_min, _ceil_to_grid(sim_h))
        # Role-play scoring is performed at 24:00 for each event day.  A
        # next-morning start from an overnight user window cannot satisfy the
        # same-day service check, and hour-of-day actions such as 01:30 are
        # also rejected by the current per-day shift API.  Keep emitted
        # starts executable and completed before midnight.
        day_end_abs = (day_idx + 1) * 24.0
        start_max = min(
            start_max,
            day_end_abs - duration - GRID_H,
            _latest_start_before_run_end(state, duration),
        )
        starts = _grid_values(start_min, start_max, GRID_H)
    candidates = []
    latest_start = _latest_start_before_run_end(state, duration)
    for start_abs in sorted(set(round(v, 6) for v in starts)):
        if start_abs + 1e-6 < sim_h:
            continue
        if start_abs > latest_start + 1e-6:
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
    bath_abs = day_idx * 24.0 + bath_h
    # Rule+MILP is an oracle-style baseline: keep the hot-water service
    # deadline, but allow thermal preheat to move away from VPP/expensive
    # windows even for personas whose ordinary routine is non-DR-adjustable.
    start_min = max(day_idx * 24.0, _ceil_to_grid(sim_h))
    start_max = min(max(start_min, bath_abs - duration), _latest_start_before_run_end(state, duration))
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
    # The EV simulator stores explicit charge windows by the current day_idx.
    # A pure next-morning window such as 00:00-03:00 is cheap but cannot serve
    # today's evening arrival: after midnight the simulator reads the next
    # day's window instead.  Keep Rule+MILP's EV candidates inside the same
    # local day so every emitted window is physically executable.
    day_end_abs = (day_idx + 1) * 24.0
    start_max = min(
        end_abs - duration,
        day_end_abs - duration,
        _latest_start_before_run_end(state, duration),
    )
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
    state: dict,
    price_profile: Any,
    run_start_date: Any,
) -> dict:
    cost = _interval_cost(start_abs, duration_h, power_kw, price_profile, run_start_date)
    vpp_penalty = BIG_VPP_PENALTY if _overlaps_any_vpp(start_abs, start_abs + duration_h, state) else 0.0
    return {
        "label": label,
        "appliances": dict(appliances),
        "objective": float(cost + vpp_penalty),
        "cost": float(cost),
        "vpp_penalty": float(vpp_penalty),
        "start_abs": round(float(start_abs), 6),
        "duration_h": round(float(duration_h), 6),
        "power_kw": round(float(power_kw), 6),
    }


def _solve_binary_choice_milp(groups: dict[str, list[dict]]) -> tuple[list[dict], dict]:
    if not groups:
        return [], {"solver": "no_present_unlocked_services", "status": "empty"}
    tie_meta: list[dict] = []
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
            # The current MILP has one independent choose-one constraint per
            # appliance.  If several candidates have identical cost/VPP
            # objective, treat them as equivalent optima and randomly select
            # one instead of encoding an implicit user-time preference.
            choice, tie = _random_min_objective_candidate(group, candidates)
            selected.append(choice)
            if tie is not None:
                tie_meta.append(tie)
        return selected, {
            "solver": "pulp_cbc_milp",
            "status": pulp.LpStatus.get(status, str(status)),
            "tie_break": "random_among_equal_objective",
            "random_tie_groups": tie_meta,
        }
    except Exception as exc:
        selected = []
        for group, candidates in groups.items():
            choice, tie = _random_min_objective_candidate(group, candidates)
            selected.append(choice)
            if tie is not None:
                tie_meta.append(tie)
        return selected, {
            "solver": "exact_enumeration_fallback",
            "status": "ok",
            "fallback_reason": str(exc)[:120],
            "tie_break": "random_among_equal_objective",
            "random_tie_groups": tie_meta,
        }


def _random_min_objective_candidate(group: str, candidates: list[dict]) -> tuple[dict, dict | None]:
    min_objective = min(float(item.get("objective", 0.0)) for item in candidates)
    tied = [
        item
        for item in candidates
        if abs(float(item.get("objective", 0.0)) - min_objective) <= 1e-9
    ]
    choice = random.choice(tied)
    if len(tied) <= 1:
        return choice, None
    return choice, {
        "group": group,
        "objective": round(min_objective, 6),
        "candidate_count": len(tied),
        "selected_label": choice.get("label"),
        "candidate_labels": [item.get("label") for item in tied],
    }


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
    if price_profile is None:
        return 1.0
    try:
        if isinstance(run_start_date, datetime):
            start = run_start_date
        elif run_start_date is None and getattr(price_profile, "is_recurring", False):
            start = datetime(2000, 1, 1)
        elif run_start_date is None:
            return 1.0
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


def _run_end_abs(state: dict) -> float:
    for key in ("run_end_abs_h", "run_end_h"):
        try:
            value = float(state.get(key))
            if value > 0:
                return value
        except (TypeError, ValueError):
            pass
    try:
        sim_days = int(state.get("sim_days", 0) or 0)
        if sim_days > 0:
            return float(sim_days * 24.0)
    except (TypeError, ValueError):
        pass
    return float("inf")


def _latest_start_before_run_end(state: dict, duration_h: float) -> float:
    """Latest start that can complete before the simulation stops.

    EnergyPlus may not call the appliance step exactly at the final boundary, so
    a schedule ending precisely at run_end can be physically executed yet not
    marked completed.  Keep one planning grid of slack at finite run endings.
    """
    run_end = _run_end_abs(state)
    if run_end == float("inf"):
        return run_end
    return run_end - float(duration_h) - GRID_H


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
