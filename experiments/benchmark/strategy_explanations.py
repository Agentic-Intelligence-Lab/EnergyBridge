"""VPP strategy explanation helpers for EnergyPlus family benchmarks."""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "vpp_strategy_explanation_v1"
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def contains_cjk_text(value: Any) -> bool:
    """Return true when a value contains CJK text that should not enter EnergyBridge output."""
    if isinstance(value, str):
        return bool(_CJK_RE.search(value))
    if isinstance(value, dict):
        return any(contains_cjk_text(key) or contains_cjk_text(item) for key, item in value.items())
    if isinstance(value, (list, tuple, set)):
        return any(contains_cjk_text(item) for item in value)
    return False


def english_only_text(value: Any, default: str = "") -> str:
    text = str(value).strip() if value is not None else ""
    return default if contains_cjk_text(text) else text


def _fmt_hour(hour: Any) -> str:
    try:
        h = float(hour) % 24.0
    except (TypeError, ValueError):
        return "?"
    hh = int(h)
    mm = int(round((h - hh) * 60))
    if mm >= 60:
        hh = (hh + 1) % 24
        mm = 0
    return f"{hh:02d}:{mm:02d}"


def _fmt_window(event: dict | None) -> str:
    event = event or {}
    return f"{_fmt_hour(event.get('trigger_h', 18.0))}-{_fmt_hour(event.get('end_h', 19.0))}"


def _duration_h(event: dict | None) -> float:
    event = event or {}
    try:
        return max(0.0, float(event.get("end_h", 19.0)) - float(event.get("trigger_h", 18.0)))
    except (TypeError, ValueError):
        return 1.0


def _duration_text(hours: float) -> str:
    if abs(hours - round(hours)) < 1e-6:
        return f"{int(round(hours))} h"
    return f"{hours:.1f} h"


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _persona_role(persona_config: dict | None) -> tuple[str, str]:
    persona_id = str((persona_config or {}).get("id", "unknown"))
    match = re.match(r"basic_role_([a-f])(?:_|$)", persona_id)
    if not match:
        return "", persona_id
    role = match.group(1).upper()
    labels = {
        "A": "Regular Commuter - Price-Cooperative",
        "B": "Stay-at-Home - Comfort-Gated",
        "C": "Irregular Schedule - High-Confirmation",
        "D": "Regular Commuter - Ideal DR Candidate",
        "E": "Family Caregiver - Low DR Value",
        "F": "Regular Commuter - EV Optimiser",
    }
    return role, labels.get(role, persona_id)


def _tag_value(persona_config: dict | None, key: str, default: str = "") -> str:
    return str(((persona_config or {}).get("tags") or {}).get(key, default) or default)


def _ac_config(appliance_config: dict | None) -> dict:
    return ((appliance_config or {}).get("ac") or {})


def _comfort_bounds(appliance_config: dict | None) -> tuple[float | None, float | None]:
    ac = _ac_config(appliance_config)
    return (
        _float_or_none(ac.get("setpoint_preferred_min_c")),
        _float_or_none(ac.get("setpoint_preferred_max_c")),
    )


def _action_value(actions: dict | None, day_decisions: list[dict] | None, key: str) -> Any:
    actions = actions or {}
    if key in actions and actions.get(key) is not None:
        return actions.get(key)
    for decision in day_decisions or []:
        if not isinstance(decision, dict):
            continue
        raw = decision.get("raw_appliance_actions") or {}
        act = decision.get("actions") or {}
        if key in raw and raw.get(key) is not None:
            return raw.get(key)
        if key in act and act.get(key) is not None:
            return act.get(key)
    return None


def _present_device_config(appliance_config: dict | None, name: str) -> dict:
    cfg = ((appliance_config or {}).get(name) or {})
    return cfg if bool(cfg.get("present", False)) else {}


def _estimate_controllable_kw(appliance_config: dict | None) -> float:
    cfg = appliance_config or {}
    total = 0.0
    for name in ("washer", "dishwasher", "dryer"):
        dev = cfg.get(name) or {}
        if dev.get("present") and dev.get("dr_adjustable", dev.get("shiftable", True)) is not False:
            total += float(dev.get("power_kw", 0.0) or 0.0)
    wh = cfg.get("water_heater") or {}
    if wh.get("present") and wh.get("dr_adjustable", True) is not False:
        total += float(wh.get("rated_kw", 0.0) or 0.0)
    ev = cfg.get("ev") or {}
    if ev.get("present") and ev.get("dr_adjustable", True) is not False:
        total += float(ev.get("power_kw", ev.get("charger_kw", 0.0)) or 0.0)
    return round(max(0.0, total), 3)


def _device_actions(
    *,
    persona_config: dict | None,
    appliance_config: dict | None,
    event: dict | None,
    setpoint_c: float | None,
    appliance_actions: dict | None,
    day_decisions: list[dict] | None,
) -> list[dict]:
    window = _fmt_window(event)
    duration = _duration_text(_duration_h(event))
    ac_min, ac_max = _comfort_bounds(appliance_config)
    tags = (persona_config or {}).get("tags") or {}
    actions: list[dict] = []

    if setpoint_c is not None:
        range_text = (
            f"{ac_min:.1f}-{ac_max:.1f}°C" if ac_min is not None and ac_max is not None else "the authorized comfort range"
        )
        if tags.get("schedule") == "caregiver" or tags.get("control") == "low_auto_accept":
            ac_rationale = (
                f"Keep the AC setpoint at {float(setpoint_c):.1f}°C within {range_text}; "
                "do not cross caregiving or comfort boundaries for the VPP event."
            )
        else:
            ac_rationale = f"Temporarily adjust only within {range_text}, then restore comfort after the VPP window."
        actions.append(
            {
                "device": "ac",
                "action": "setpoint",
                "amount": round(float(setpoint_c), 1),
                "unit": "C",
                "duration": f"{window} (about {duration})",
                "rationale": ac_rationale,
            }
        )

    for name, label in (
        ("washer", "washer"),
        ("dishwasher", "dishwasher"),
        ("dryer", "dryer"),
    ):
        dev = _present_device_config(appliance_config, name)
        if not dev:
            continue
        start = _action_value(appliance_actions, day_decisions, f"{name}_start_h")
        skip = _action_value(appliance_actions, day_decisions, f"{name}_skip")
        dev_duration = _duration_text(float(dev.get("duration_h", 1.0) or 1.0))
        if skip is True:
            summary = f"Do not run the {label} today; use skip only when the task is genuinely unnecessary."
            command = {"skip": True}
        elif start is not None:
            summary = f"Start the {label} at {_fmt_hour(start)} for about {dev_duration}, avoiding {window}."
            command = {"start_h": _float_or_none(start), "duration_h": _float_or_none(dev.get("duration_h"))}
        elif dev.get("dr_adjustable", dev.get("shiftable", True)) is False:
            pref = dev.get("preferred_h")
            summary = f"The {label} is fixed or non-DR-adjustable, so keep the user's routine time at {_fmt_hour(pref)}."
            command = {"routine_start_h": _float_or_none(pref), "dr_adjustable": False}
        else:
            summary = f"Do not start the {label} inside {window}; if it must run today, schedule it outside the window."
            command = {"avoid_window": window}
        actions.append(
            {
                "device": name,
                "action": "schedule",
                "amount": command,
                "unit": "hour_of_day",
                "duration": dev_duration,
                "rationale": summary,
            }
        )

    wh = _present_device_config(appliance_config, "water_heater")
    if wh:
        start = _action_value(appliance_actions, day_decisions, "water_heater_preheat_start_h")
        end = _action_value(appliance_actions, day_decisions, "water_heater_preheat_end_h")
        temp = _action_value(appliance_actions, day_decisions, "water_heater_preheat_temp_c")
        preheat = _action_value(appliance_actions, day_decisions, "water_heater_preheat")
        if preheat is False:
            summary = f"Do not preheat the water heater in this window; avoid extra load during {window}."
            command = {"preheat": False}
        elif start is not None and end is not None:
            temp_text = f" at {float(temp):.0f}°C" if temp is not None else ""
            if wh.get("dr_adjustable", True) is False:
                summary = (
                    f"Keep the fixed water-heater preheat window {_fmt_hour(start)}-{_fmt_hour(end)}{temp_text}; "
                    "protect bath-time hot water and do not use this routine as a shedding resource."
                )
            else:
                summary = (
                    f"Preheat the water heater from {_fmt_hour(start)} to {_fmt_hour(end)}{temp_text}; "
                    f"store heat before bath time and avoid {window}."
                )
            command = {
                "preheat_start_h": _float_or_none(start),
                "preheat_end_h": _float_or_none(end),
                "preheat_temp_c": _float_or_none(temp),
            }
        else:
            routine_start = wh.get("pre_heat_window_start_h")
            routine_end = wh.get("pre_heat_window_end_h")
            summary = (
                f"Keep the routine water-heater preheat window {_fmt_hour(routine_start)}-{_fmt_hour(routine_end)}; "
                "do not sacrifice bath-time availability for VPP response."
            )
            command = {
                "routine_preheat_start_h": _float_or_none(routine_start),
                "routine_preheat_end_h": _float_or_none(routine_end),
            }
        actions.append(
            {
                "device": "water_heater",
                "action": "preheat",
                "amount": command,
                "unit": "hour_of_day",
                "duration": "according to the hot-water service window",
                "rationale": summary,
            }
        )

    ev = _present_device_config(appliance_config, "ev")
    if ev:
        start = _action_value(appliance_actions, day_decisions, "ev_charge_start_h")
        end = _action_value(appliance_actions, day_decisions, "ev_charge_end_h")
        target_soc = _float_or_none(ev.get("target_soc"))
        dep = ev.get("departure_h")
        target_text = f"{target_soc:.0%} SOC" if target_soc is not None else "the target SOC"
        if start is not None and end is not None:
            summary = (
                f"Set the EV charging window to {_fmt_hour(start)}-{_fmt_hour(end)}, "
                f"avoid {window}, and reach {target_text} before {_fmt_hour(dep)}."
            )
            command = {"charge_start_h": _float_or_none(start), "charge_end_h": _float_or_none(end)}
        else:
            summary = (
                f"Do not charge the EV during {window}; if needed, start after the window "
                f"and reach {target_text} before {_fmt_hour(dep)}."
            )
            command = {"avoid_window": window, "departure_h": _float_or_none(dep), "target_soc": target_soc}
        actions.append(
            {
                "device": "ev",
                "action": "charge_window",
                "amount": command,
                "unit": "hour_of_day",
                "duration": "until the SOC constraint is satisfied",
                "rationale": summary,
            }
        )

    return actions


def _protected_constraints(persona_config: dict | None, appliance_config: dict | None, event: dict | None) -> list[str]:
    tags = (persona_config or {}).get("tags") or {}
    schedule = (persona_config or {}).get("schedule") or {}
    window = _fmt_window(event)
    ac_min, ac_max = _comfort_bounds(appliance_config)
    constraints: list[str] = []
    if ac_min is not None and ac_max is not None:
        constraints.append(
            f"Indoor-temperature control must stay within the user's preferred comfort range "
            f"{ac_min:.1f}-{ac_max:.1f}°C and auto-restore after {window}."
        )
    else:
        constraints.append("Indoor-temperature control must stay within authorized comfort boundaries and auto-restore after the event.")
    if tags.get("control") in {"confirm_required", "suggestion_first", "low_auto_accept", "privacy_sensitive"}:
        constraints.append("The user keeps event-level confirmation authority; larger unconfirmed actions cannot execute automatically.")
    if tags.get("schedule") == "caregiver" or schedule.get("vulnerable_members"):
        members = ", ".join(schedule.get("vulnerable_members") or ["caregiving routine"])
        constraints.append(f"Caregiving or vulnerable-member constraints come first ({members}); safety and stability are not shedding resources.")
    ev = _present_device_config(appliance_config, "ev")
    if ev:
        target = _float_or_none(ev.get("target_soc"))
        target_text = f"{target:.0%} SOC" if target is not None else "the target SOC"
        constraints.append(f"The EV must reach {target_text} before departure at {_fmt_hour(ev.get('departure_h'))}.")
    wh = _present_device_config(appliance_config, "water_heater")
    if wh:
        constraints.append(f"The water heater must keep bath-time hot water available before {_fmt_hour(wh.get('bath_required_h'))}.")
    fixed = []
    for name in ("washer", "dishwasher", "dryer"):
        dev = _present_device_config(appliance_config, name)
        if dev and dev.get("dr_adjustable", dev.get("shiftable", True)) is False:
            fixed.append(name)
    if wh and wh.get("dr_adjustable", True) is False:
        fixed.append("water_heater")
    if fixed:
        constraints.append("Fixed or non-DR-adjustable tasks keep their original routines: " + ", ".join(fixed) + ".")
    constraints.append(f"No present controllable non-AC load may be scheduled to run inside the VPP window {window}.")
    return constraints


def _user_control_notes(persona_config: dict | None, appliance_config: dict | None) -> list[str]:
    tags = (persona_config or {}).get("tags") or {}
    _, ac_max = _comfort_bounds(appliance_config)
    restore = f"{ac_max:.1f}°C or the user's usual setpoint" if ac_max is not None else "the user's usual setpoint"
    notes = [
        "The user can cancel, pause, or switch to the conservative option before or during the event.",
        f"If the user feels uncomfortable, restore the AC to {restore} immediately and reschedule device tasks outside the window.",
        "Any action beyond this event's authorization boundary requires renewed confirmation and cannot carry over by default.",
    ]
    if tags.get("control") == "high_trust_auto":
        notes.append("Even when automation is currently allowed, the user can take over and request restoration at any time.")
    return notes


def _benefit(
    *,
    appliance_config: dict | None,
    demand_context: dict | None,
    capacity_context: dict | None,
) -> dict:
    estimate_kw = _estimate_controllable_kw(appliance_config)
    demand_context = demand_context or {}
    target_kw = _float_or_none(demand_context.get("target_shed_kw") or demand_context.get("demand_target_kw"))
    target_kwh = _float_or_none(
        demand_context.get("target_shed_kwh")
        or demand_context.get("demand_target_shed_kwh")
        or demand_context.get("target_kwh")
    )
    assessment = ((capacity_context or {}).get("assessment") or {}) if isinstance(capacity_context, dict) else {}
    recommended_bid_kw = _float_or_none(assessment.get("recommended_bid_kw"))
    if estimate_kw <= 0.0:
        message = "No controllable device load can be safely shifted now; the benefit is mainly avoiding new noncritical load during the window."
    elif target_kw is not None and 0.0 < target_kw <= 0.75:
        message = f"The reference target is about {target_kw:.2f} kW, so low-disruption actions are enough; focus on moving controllable non-AC load out of the window."
    elif target_kw is not None and target_kw > 0:
        message = f"The reference shedding target is about {target_kw:.2f} kW; the plan prioritizes shifting roughly {estimate_kw:.1f} kW of controllable device load."
    elif estimate_kw > 0:
        message = f"The plan can move roughly {estimate_kw:.1f} kW of controllable device load out of the VPP window and create a verifiable response record."
    else:
        message = "Shiftable load is limited; the main benefit is avoiding new controllable load during the event window."
    return {
        "load_shift_kw_estimate": estimate_kw,
        "target_shed_kw": target_kw,
        "target_shed_kwh_or_cap_kwh": target_kwh,
        "recommended_bid_kw": recommended_bid_kw,
        "compensation_note": "Do not invent a monetary amount; if compensation or TOU pricing applies, calculate it from the actual VPP or tariff settlement rules.",
        "message": message,
    }


def _alternatives(persona_config: dict | None, appliance_config: dict | None, event: dict | None) -> list[dict]:
    window = _fmt_window(event)
    tags = (persona_config or {}).get("tags") or {}
    ac_min, ac_max = _comfort_bounds(appliance_config)
    comfort_text = f"{ac_min:.1f}-{ac_max:.1f}°C" if ac_min is not None and ac_max is not None else "the authorized comfort range"
    alternatives = [
        {
            "name": "Conservative option",
            "summary": f"Keep the AC at the comfort setting and only move controllable devices out of {window}.",
            "tradeoff": "Lowest comfort risk, smaller shedding capability.",
        },
        {
            "name": "Balanced option",
            "summary": f"Make a small AC adjustment within {comfort_text} and avoid {window} for laundry, hot water, EV charging, and other controllable devices.",
            "tradeoff": "Balances user experience and VPP response; this is the default recommendation.",
        },
        {
            "name": "Enhanced response option",
            "summary": "With renewed confirmation, use an AC setpoint closer to the comfort upper bound and complete storable-energy tasks earlier.",
            "tradeoff": "Stronger shedding, but it requires explicit authorization and fast post-event restoration.",
        },
    ]
    if tags.get("schedule") == "caregiver" or tags.get("control") == "low_auto_accept":
        alternatives[2] = {
            "name": "Advisory-only option",
            "summary": "Do not automatically adjust AC or caregiving-related routines; only remind the household to avoid starting noncritical devices in the VPP window.",
            "tradeoff": "Best for caregiving stability, but lowest VPP contribution.",
        }
    ev = _present_device_config(appliance_config, "ev")
    if ev:
        alternatives.append(
            {
                "name": "EV-priority option",
                "summary": f"Treat reaching target SOC before {_fmt_hour(ev.get('departure_h'))} as a hard constraint; if needed, start charging immediately after {window}.",
                "tradeoff": "Protects mobility, but may reduce available shedding time.",
            }
        )
    return alternatives[:3]


def _personalization_notes(persona_config: dict | None) -> list[str]:
    role, label = _persona_role(persona_config)
    tags = (persona_config or {}).get("tags") or {}
    notes = [f"Persona: {label}."]
    if role == "A":
        notes.append("Emphasize evening peak load and quantified impact while preserving comfort at home arrival.")
    elif role == "B":
        notes.append("Comfort and confirmation authority come first; allow only short, small, reversible adjustments.")
    elif role == "C":
        notes.append("Base the explanation on the current event and real-time input, not yesterday's habits.")
    elif role == "D":
        notes.append("Automation can execute within preset boundaries; show response benefit and execution review.")
    elif role == "E":
        notes.append("Caregiving safety and stability come first; VPP response should be low-risk advisory action or noncritical load avoidance.")
    elif role == "F":
        notes.append("EV departure SOC is a hard constraint; charging optimization must protect the next trip first.")
    if tags.get("price") in {"price_sensitive", "price_driven"}:
        notes.append("The user needs load or tariff impact, but compensation amounts must not be invented.")
    if tags.get("control") in {"confirm_required", "suggestion_first"}:
        notes.append("Use suggestion and confirmation language, not default continuing authorization.")
    return notes


def _why_request(event: dict | None, demand_context: dict | None, capacity_context: dict | None) -> str:
    window = _fmt_window(event)
    demand_context = demand_context or {}
    target_kw = _float_or_none(demand_context.get("target_shed_kw") or demand_context.get("demand_target_kw"))
    if target_kw is not None and target_kw > 0:
        return f"{window} is a VPP demand-response window; the grid is asking the household to reduce about {target_kw:.2f} kW of adjustable load during this peak period."
    assessment = ((capacity_context or {}).get("assessment") or {}) if isinstance(capacity_context, dict) else {}
    bid = _float_or_none(assessment.get("recommended_bid_kw"))
    if bid is not None and bid > 0:
        return f"{window} is a VPP demand-response window; the household capacity assessment suggests about {bid:.2f} kW of low-risk response."
    return f"{window} is a VPP demand-response window; the goal is to reduce event-window electricity use without breaking comfort or service constraints."


def _structured_constraints(
    *,
    event: dict | None,
    appliance_config: dict | None,
    setpoint_c: float | None,
    appliance_actions: dict | None,
    day_decisions: list[dict] | None,
) -> dict:
    ac_min, ac_max = _comfort_bounds(appliance_config)
    event = event or {}
    devices: dict[str, Any] = {}
    for name in ("washer", "dishwasher", "dryer"):
        if not _present_device_config(appliance_config, name):
            continue
        devices[name] = {
            "start_h": _float_or_none(_action_value(appliance_actions, day_decisions, f"{name}_start_h")),
            "skip": _action_value(appliance_actions, day_decisions, f"{name}_skip"),
            "dr_adjustable": _present_device_config(appliance_config, name).get("dr_adjustable", True),
        }
    if _present_device_config(appliance_config, "water_heater"):
        devices["water_heater"] = {
            "preheat_start_h": _float_or_none(_action_value(appliance_actions, day_decisions, "water_heater_preheat_start_h")),
            "preheat_end_h": _float_or_none(_action_value(appliance_actions, day_decisions, "water_heater_preheat_end_h")),
            "preheat_temp_c": _float_or_none(_action_value(appliance_actions, day_decisions, "water_heater_preheat_temp_c")),
            "preheat": _action_value(appliance_actions, day_decisions, "water_heater_preheat"),
        }
    ev = _present_device_config(appliance_config, "ev")
    if ev:
        devices["ev"] = {
            "charge_start_h": _float_or_none(_action_value(appliance_actions, day_decisions, "ev_charge_start_h")),
            "charge_end_h": _float_or_none(_action_value(appliance_actions, day_decisions, "ev_charge_end_h")),
            "target_soc": _float_or_none(ev.get("target_soc")),
            "departure_h": _float_or_none(ev.get("departure_h")),
        }
    return {
        "event_id": event.get("id"),
        "vpp_window": {
            "start_h": _float_or_none(event.get("trigger_h")),
            "end_h": _float_or_none(event.get("end_h")),
            "text": _fmt_window(event),
        },
        "hvac": {
            "setpoint_c": round(float(setpoint_c), 1) if setpoint_c is not None else None,
            "preferred_min_c": ac_min,
            "preferred_max_c": ac_max,
            "restore_after_h": _float_or_none(event.get("end_h")),
            "auto_restore": True,
        },
        "appliances": devices,
        "hard_constraints": [
            "no_present_controllable_non_ac_load_inside_vpp_window",
            "comfort_and_safety_override_grid_request",
            "user_can_opt_out_or_restore",
        ],
    }


def _build_natural_language(
    *,
    why: str,
    actions: list[dict],
    protected: list[str],
    user_control: list[str],
    benefit: dict,
    alternatives: list[dict],
) -> str:
    action_parts = [
        str(item.get("rationale", "")).strip().rstrip(".; ")
        for item in actions[:4]
        if item.get("rationale")
    ]
    action_text = "; ".join(action_parts)
    alt_text = " / ".join(str(item.get("name", "")) for item in alternatives[:3] if item.get("name"))
    strategy_name = "Advisory-only option" if any(
        isinstance(item, dict) and item.get("name") == "Advisory-only option"
        for item in alternatives
    ) else "Balanced option"
    protect_text = protected[0] if protected else "Comfort and service constraints come first."
    control_text = user_control[0] if user_control else "The user can cancel at any time."
    return (
        f"{why} Recommended strategy: {strategy_name}. {action_text}. "
        f"Protected boundary: {protect_text} {control_text} "
        f"Expected benefit: {benefit.get('message', '')} Options include: {alt_text}."
    ).strip()


def _review_dimensions(explanation: dict) -> dict:
    return {
        "why_request": bool(explanation.get("why_request")),
        "concrete_device_actions": bool(explanation.get("recommended_actions")),
        "comfort_or_service_constraints": bool(explanation.get("protected_constraints")),
        "user_control_and_opt_out": bool(explanation.get("user_control")),
        "benefit_or_compensation": bool((explanation.get("expected_benefit") or {}).get("message")),
        "alternatives_2plus": len(explanation.get("alternatives") or []) >= 2,
        "structured_constraints": bool(explanation.get("structured_control_constraints")),
        "personalized_to_role": bool(explanation.get("personalization_notes")),
    }


def build_vpp_strategy_explanation(
    *,
    persona_config: dict | None = None,
    appliance_config: dict | None = None,
    event: dict | None = None,
    setpoint_c: float | None = None,
    reason: str = "",
    appliance_actions: dict | None = None,
    day_decisions: list[dict] | None = None,
    demand_context: dict | None = None,
    capacity_context: dict | None = None,
    method: str = "",
    city: str = "",
    source: str = "deterministic",
) -> dict:
    """Build a reviewable explanation for one VPP control strategy."""
    role, role_label = _persona_role(persona_config)
    actions = _device_actions(
        persona_config=persona_config,
        appliance_config=appliance_config,
        event=event,
        setpoint_c=setpoint_c,
        appliance_actions=appliance_actions,
        day_decisions=day_decisions,
    )
    protected = _protected_constraints(persona_config, appliance_config, event)
    user_control = _user_control_notes(persona_config, appliance_config)
    benefit = _benefit(
        appliance_config=appliance_config,
        demand_context=demand_context,
        capacity_context=capacity_context,
    )
    alternatives = _alternatives(persona_config, appliance_config, event)
    why = _why_request(event, demand_context, capacity_context)
    structured = _structured_constraints(
        event=event,
        appliance_config=appliance_config,
        setpoint_c=setpoint_c,
        appliance_actions=appliance_actions,
        day_decisions=day_decisions,
    )
    explanation = {
        "schema_version": SCHEMA_VERSION,
        "source": source,
        "language": "en-US",
        "persona_id": str((persona_config or {}).get("id", "")),
        "persona_role": role,
        "persona_role_label": role_label,
        "method": method,
        "city": city,
        "event_id": (event or {}).get("id"),
        "vpp_window": _fmt_window(event),
        "agent_reason": english_only_text(reason),
        "why_request": why,
        "recommended_actions": actions,
        "protected_constraints": protected,
        "user_control": user_control,
        "expected_benefit": benefit,
        "alternatives": alternatives,
        "structured_control_constraints": structured,
        "personalization_notes": _personalization_notes(persona_config),
    }
    explanation["natural_language"] = _build_natural_language(
        why=why,
        actions=actions,
        protected=protected,
        user_control=user_control,
        benefit=benefit,
        alternatives=alternatives,
    )
    explanation["review_dimensions"] = _review_dimensions(explanation)
    return explanation


def _coerce_str(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _coerce_list(value: Any) -> list:
    if isinstance(value, list):
        return [item for item in value if item not in (None, "")]
    if value:
        return [value]
    return []


def _coerce_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def normalize_vpp_strategy_explanation(
    raw_explanation: Any,
    *,
    persona_config: dict | None = None,
    appliance_config: dict | None = None,
    event: dict | None = None,
    setpoint_c: float | None = None,
    reason: str = "",
    appliance_actions: dict | None = None,
    day_decisions: list[dict] | None = None,
    demand_context: dict | None = None,
    capacity_context: dict | None = None,
    method: str = "",
    city: str = "",
    source: str = "llm_agent",
) -> dict:
    """Complete an optional LLM explanation with deterministic required fields."""
    base = build_vpp_strategy_explanation(
        persona_config=persona_config,
        appliance_config=appliance_config,
        event=event,
        setpoint_c=setpoint_c,
        reason=reason,
        appliance_actions=appliance_actions,
        day_decisions=day_decisions,
        demand_context=demand_context,
        capacity_context=capacity_context,
        method=method,
        city=city,
        source="deterministic_completion",
    )
    raw = raw_explanation if isinstance(raw_explanation, dict) else {}
    if not raw:
        base["source"] = source + "_deterministic"
        return base

    merged = dict(base)
    merged["source"] = source + "_with_completion"
    for key in ("natural_language", "why_request"):
        value = _coerce_str(raw.get(key))
        if value and not contains_cjk_text(value):
            merged[key] = value
    for key in ("recommended_actions", "protected_constraints", "user_control", "alternatives", "personalization_notes"):
        value = _coerce_list(raw.get(key))
        if value and not contains_cjk_text(value):
            merged[key] = value
    benefit = _coerce_dict(raw.get("expected_benefit"))
    if benefit and not contains_cjk_text(benefit):
        merged["expected_benefit"] = {**base["expected_benefit"], **benefit}
    structured = _coerce_dict(raw.get("structured_control_constraints"))
    if structured and not contains_cjk_text(structured):
        merged["structured_control_constraints"] = {
            **base["structured_control_constraints"],
            **structured,
        }
    if len(merged.get("alternatives") or []) < 2:
        merged["alternatives"] = (merged.get("alternatives") or []) + base["alternatives"]
        merged["alternatives"] = merged["alternatives"][:3]
    if not merged.get("recommended_actions"):
        merged["recommended_actions"] = base["recommended_actions"]
    if not merged.get("natural_language"):
        merged["natural_language"] = base["natural_language"]
    merged["llm_raw_explanation"] = (
        {"omitted": "non_english_text_detected"} if contains_cjk_text(raw) else raw
    )
    merged["review_dimensions"] = _review_dimensions(merged)
    return merged


def collect_strategy_explanation_records(result: Any, persona: dict, city: str) -> list[dict]:
    """Collect one flat review record per VPP event explanation."""
    method = str(getattr(result, "method", "") or "")
    events = list(getattr(result, "vpp_event_log", []) or [])
    records: list[dict] = []
    for idx, event in enumerate(events, start=1):
        if not isinstance(event, dict):
            continue
        explanation = event.get("strategy_explanation")
        if not isinstance(explanation, dict) or not explanation:
            demand_context = {
                "target_shed_kw": event.get("demand_target_kw"),
                "target_shed_kwh": event.get("demand_target_shed_kwh"),
                "target_kwh": event.get("demand_target_kwh"),
            }
            explanation = build_vpp_strategy_explanation(
                persona_config=persona,
                appliance_config=persona.get("appliances", {}),
                event=event,
                setpoint_c=event.get("setpoint"),
                reason=event.get("reason", ""),
                appliance_actions=event.get("vpp_trigger_actions", {}),
                day_decisions=event.get("day_decisions", []),
                demand_context=demand_context,
                capacity_context=event.get("capacity_assessment", {}),
                method=method,
                city=city,
                source="posthoc_summary",
            )
        records.append(
            {
                "persona_id": persona.get("id", ""),
                "persona_display_name": persona.get("display_name") or persona.get("name", ""),
                "city": city,
                "method": method,
                "event_index": idx,
                "event_id": event.get("id", f"vpp{idx}"),
                "day": event.get("day", idx),
                "score": event.get("score"),
                "comfort_score": event.get("comfort_score"),
                "energy_score": event.get("energy_score"),
                "vpp_score": event.get("vpp_score"),
                "reason": event.get("reason", ""),
                "natural_language": explanation.get("natural_language", ""),
                "why_request": explanation.get("why_request", ""),
                "review_dimensions": explanation.get("review_dimensions", {}),
                "strategy_explanation": explanation,
            }
        )
    return records


def format_strategy_explanation_lines(explanation: dict | None, *, indent: str = "    ") -> list[str]:
    if not isinstance(explanation, dict) or not explanation:
        return []
    benefit = explanation.get("expected_benefit") or {}
    alternatives = explanation.get("alternatives") or []
    alt_names = " | ".join(str(item.get("name", "")) for item in alternatives if isinstance(item, dict))
    lines = [
        f"{indent}Strategy explanation:",
        f"{indent}  Why      : {explanation.get('why_request', '')}",
        f"{indent}  Plan     : {explanation.get('natural_language', '')}",
    ]
    if benefit.get("message"):
        lines.append(f"{indent}  Benefit  : {benefit.get('message')}")
    if alt_names:
        lines.append(f"{indent}  Options  : {alt_names}")
    return [line for line in lines if line.strip()]


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def write_strategy_explanation_artifacts(
    records: list[dict],
    output_dir: Path | str,
    *,
    prefix: str = "strategy_explanations",
) -> dict[str, str]:
    """Write JSONL/CSV/Markdown review artifacts and return their paths."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    jsonl_path = out / f"{prefix}.jsonl"
    csv_path = out / f"{prefix}.csv"
    md_path = out / f"{prefix}.md"

    with jsonl_path.open("w", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

    fieldnames = [
        "persona_id",
        "persona_display_name",
        "city",
        "method",
        "event_index",
        "event_id",
        "day",
        "score",
        "comfort_score",
        "energy_score",
        "vpp_score",
        "reason",
        "why_request",
        "natural_language",
        "recommended_actions",
        "protected_constraints",
        "user_control",
        "expected_benefit",
        "alternatives",
        "review_dimensions",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, lineterminator="\n")
        writer.writeheader()
        for record in records:
            exp = record.get("strategy_explanation") or {}
            row = {
                **{key: record.get(key, "") for key in fieldnames},
                "recommended_actions": _json_dumps(exp.get("recommended_actions", [])),
                "protected_constraints": _json_dumps(exp.get("protected_constraints", [])),
                "user_control": _json_dumps(exp.get("user_control", [])),
                "expected_benefit": _json_dumps(exp.get("expected_benefit", {})),
                "alternatives": _json_dumps(exp.get("alternatives", [])),
                "review_dimensions": _json_dumps(record.get("review_dimensions", {})),
            }
            writer.writerow(row)

    md_lines = [
        "# VPP Strategy Explanation Review Data",
        "",
        f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Records: {len(records)}",
        "",
    ]
    for record in records:
        exp = record.get("strategy_explanation") or {}
        score = record.get("score")
        score_text = str(score) if score not in (None, "") else "N/A"
        md_lines.extend(
            [
                f"## {record.get('persona_id', '?')} / {record.get('event_id', '?')}",
                "",
                f"- City: {record.get('city', '')}",
                f"- Method: {record.get('method', '')}",
                f"- Score: {score_text}",
                f"- Why: {exp.get('why_request', '')}",
                "",
                exp.get("natural_language", ""),
                "",
                "Review dimensions:",
            ]
        )
        for key, value in (exp.get("review_dimensions") or {}).items():
            md_lines.append(f"- {key}: {bool(value)}")
        md_lines.extend(["", "Structured constraints:", ""])
        md_lines.append("```json")
        md_lines.append(json.dumps(exp.get("structured_control_constraints", {}), ensure_ascii=False, indent=2))
        md_lines.append("```")
        md_lines.append("")
    while md_lines and md_lines[-1] == "":
        md_lines.pop()
    md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")

    return {"jsonl": str(jsonl_path), "csv": str(csv_path), "markdown": str(md_path)}
