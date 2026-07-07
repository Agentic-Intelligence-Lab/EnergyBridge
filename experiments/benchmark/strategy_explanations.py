"""VPP strategy explanation helpers for EnergyPlus family benchmarks."""
from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


SCHEMA_VERSION = "vpp_strategy_explanation_v1"


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
        return f"{int(round(hours))}小时"
    return f"{hours:.1f}小时"


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
            f"{ac_min:.1f}-{ac_max:.1f}°C" if ac_min is not None and ac_max is not None else "授权舒适范围"
        )
        if tags.get("schedule") == "caregiver" or tags.get("control") == "low_auto_accept":
            ac_rationale = f"空调保持在{float(setpoint_c):.1f}°C（{range_text}内），不为VPP越过护理/舒适边界。"
        else:
            ac_rationale = f"只在{range_text}内临时调整，窗口结束后恢复舒适设定。"
        actions.append(
            {
                "device": "ac",
                "action": "setpoint",
                "amount": round(float(setpoint_c), 1),
                "unit": "C",
                "duration": f"{window}（约{duration}）",
                "rationale": ac_rationale,
            }
        )

    for name, label in (
        ("washer", "洗衣机"),
        ("dishwasher", "洗碗机"),
        ("dryer", "烘干机"),
    ):
        dev = _present_device_config(appliance_config, name)
        if not dev:
            continue
        start = _action_value(appliance_actions, day_decisions, f"{name}_start_h")
        skip = _action_value(appliance_actions, day_decisions, f"{name}_skip")
        dev_duration = _duration_text(float(dev.get("duration_h", 1.0) or 1.0))
        if skip is True:
            summary = f"今天不启动{label}；仅在任务确实不需要时使用跳过。"
            command = {"skip": True}
        elif start is not None:
            summary = f"{label}安排在{_fmt_hour(start)}开始，运行约{dev_duration}，避开{window}。"
            command = {"start_h": _float_or_none(start), "duration_h": _float_or_none(dev.get("duration_h"))}
        elif dev.get("dr_adjustable", dev.get("shiftable", True)) is False:
            pref = dev.get("preferred_h")
            summary = f"{label}是固定/非DR可调任务，保持用户常规时间{_fmt_hour(pref)}。"
            command = {"routine_start_h": _float_or_none(pref), "dr_adjustable": False}
        else:
            summary = f"{label}在{window}内不启动；如当天需要运行，安排到窗口外。"
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
            summary = f"电热水器本窗口不预热，避免{window}内额外用电。"
            command = {"preheat": False}
        elif start is not None and end is not None:
            temp_text = f"，目标{float(temp):.0f}°C" if temp is not None else ""
            if wh.get("dr_adjustable", True) is False:
                summary = (
                    f"热水器保持固定预热{_fmt_hour(start)}-{_fmt_hour(end)}{temp_text}，"
                    "保障洗浴热水，不把该例程作为削峰资源。"
                )
            else:
                summary = f"热水器在{_fmt_hour(start)}-{_fmt_hour(end)}预热{temp_text}，洗浴前保温，避开{window}。"
            command = {
                "preheat_start_h": _float_or_none(start),
                "preheat_end_h": _float_or_none(end),
                "preheat_temp_c": _float_or_none(temp),
            }
        else:
            routine_start = wh.get("pre_heat_window_start_h")
            routine_end = wh.get("pre_heat_window_end_h")
            summary = f"热水器保持常规预热{_fmt_hour(routine_start)}-{_fmt_hour(routine_end)}，不为VPP牺牲洗浴可用性。"
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
                "duration": "按热水需求窗口",
                "rationale": summary,
            }
        )

    ev = _present_device_config(appliance_config, "ev")
    if ev:
        start = _action_value(appliance_actions, day_decisions, "ev_charge_start_h")
        end = _action_value(appliance_actions, day_decisions, "ev_charge_end_h")
        target_soc = _float_or_none(ev.get("target_soc"))
        dep = ev.get("departure_h")
        target_text = f"{target_soc:.0%} SOC" if target_soc is not None else "目标SOC"
        if start is not None and end is not None:
            summary = f"EV充电窗口设为{_fmt_hour(start)}-{_fmt_hour(end)}，避开{window}并保证{_fmt_hour(dep)}前达到{target_text}。"
            command = {"charge_start_h": _float_or_none(start), "charge_end_h": _float_or_none(end)}
        else:
            summary = f"EV不在{window}充电；必要时从窗口后开始补能，保证{_fmt_hour(dep)}前达到{target_text}。"
            command = {"avoid_window": window, "departure_h": _float_or_none(dep), "target_soc": target_soc}
        actions.append(
            {
                "device": "ev",
                "action": "charge_window",
                "amount": command,
                "unit": "hour_of_day",
                "duration": "直到满足SOC约束",
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
        constraints.append(f"室温策略不得越过用户偏好舒适范围 {ac_min:.1f}-{ac_max:.1f}°C，{window}结束后自动恢复。")
    else:
        constraints.append("室温策略必须留在已授权舒适边界内，事件结束后自动恢复。")
    if tags.get("control") in {"confirm_required", "suggestion_first", "low_auto_accept", "privacy_sensitive"}:
        constraints.append("用户保留事件级确认权；未确认的更大幅度控制不能自动执行。")
    if tags.get("schedule") == "caregiver" or schedule.get("vulnerable_members"):
        members = ", ".join(schedule.get("vulnerable_members") or ["caregiving routine"])
        constraints.append(f"护理/脆弱成员约束优先（{members}）；不把安全和稳定性作为削峰资源。")
    ev = _present_device_config(appliance_config, "ev")
    if ev:
        target = _float_or_none(ev.get("target_soc"))
        target_text = f"{target:.0%} SOC" if target is not None else "目标SOC"
        constraints.append(f"EV必须在{_fmt_hour(ev.get('departure_h'))}出发前达到{target_text}。")
    wh = _present_device_config(appliance_config, "water_heater")
    if wh:
        constraints.append(f"热水器必须保障{_fmt_hour(wh.get('bath_required_h'))}前洗浴热水可用。")
    fixed = []
    for name in ("washer", "dishwasher", "dryer"):
        dev = _present_device_config(appliance_config, name)
        if dev and dev.get("dr_adjustable", dev.get("shiftable", True)) is False:
            fixed.append(name)
    if wh and wh.get("dr_adjustable", True) is False:
        fixed.append("water_heater")
    if fixed:
        constraints.append("固定/非DR可调任务保持原例程：" + ", ".join(fixed) + "。")
    constraints.append(f"所有可控非空调负荷都不得安排在VPP窗口 {window} 内运行。")
    return constraints


def _user_control_notes(persona_config: dict | None, appliance_config: dict | None) -> list[str]:
    tags = (persona_config or {}).get("tags") or {}
    _, ac_max = _comfort_bounds(appliance_config)
    restore = f"{ac_max:.1f}°C或用户常用设定" if ac_max is not None else "用户常用设定"
    notes = [
        "用户可以在事件开始前或事件进行中取消、暂停或改成保守方案。",
        f"如果感觉不舒适，空调立即恢复到{restore}，设备任务重新排到窗口外。",
        "任何超出本次授权边界的动作都需要重新确认，不能被默认延续到下一次事件。",
    ]
    if tags.get("control") == "high_trust_auto":
        notes.append("即使当前允许自动执行，用户仍可随时接管并要求恢复。")
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
        message = "当前没有可安全转移的可控设备负荷；收益主要来自提醒用户避免在窗口内新增非关键用电。"
    elif target_kw is not None and 0.0 < target_kw <= 0.75:
        message = f"本次参考目标约{target_kw:.2f}kW，采用低干扰动作即可，重点是把可控非空调负荷移出窗口。"
    elif target_kw is not None and target_kw > 0:
        message = f"本次参考削峰目标约{target_kw:.2f}kW；计划优先转移约{estimate_kw:.1f}kW的可控设备负荷。"
    elif estimate_kw > 0:
        message = f"预计可把约{estimate_kw:.1f}kW的可控设备负荷移出VPP窗口，并形成可核验的响应记录。"
    else:
        message = "当前可转移负荷有限，收益主要来自避免事件窗口内新增可控负荷。"
    return {
        "load_shift_kw_estimate": estimate_kw,
        "target_shed_kw": target_kw,
        "target_shed_kwh_or_cap_kwh": target_kwh,
        "recommended_bid_kw": recommended_bid_kw,
        "compensation_note": "不编造具体金额；若项目有补偿或分时电价，按实际VPP/电价结算规则计算。",
        "message": message,
    }


def _alternatives(persona_config: dict | None, appliance_config: dict | None, event: dict | None) -> list[dict]:
    window = _fmt_window(event)
    tags = (persona_config or {}).get("tags") or {}
    ac_min, ac_max = _comfort_bounds(appliance_config)
    comfort_text = f"{ac_min:.1f}-{ac_max:.1f}°C" if ac_min is not None and ac_max is not None else "授权舒适范围"
    alternatives = [
        {
            "name": "保守方案",
            "summary": f"空调保持舒适设定，只把可控设备移出{window}。",
            "tradeoff": "舒适风险最低，削峰能力较小。",
        },
        {
            "name": "平衡方案",
            "summary": f"空调在{comfort_text}内小幅调整，洗衣/热水/EV等避开{window}。",
            "tradeoff": "兼顾用户体验和VPP响应，是默认建议。",
        },
        {
            "name": "增强响应方案",
            "summary": "在用户再次确认后，采用更靠近舒适上限的空调设定，并提前完成可蓄能任务。",
            "tradeoff": "削峰更强，但需要明确授权和事件后快速恢复。",
        },
    ]
    if tags.get("schedule") == "caregiver" or tags.get("control") == "low_auto_accept":
        alternatives[2] = {
            "name": "仅提醒方案",
            "summary": "不自动调节空调或护理相关任务，只提示避免在VPP窗口启动非关键设备。",
            "tradeoff": "最保护护理稳定性，但VPP贡献最低。",
        }
    ev = _present_device_config(appliance_config, "ev")
    if ev:
        alternatives.append(
            {
                "name": "EV优先方案",
                "summary": f"所有削峰动作以{_fmt_hour(ev.get('departure_h'))}前达到目标SOC为硬约束，必要时从{window}结束后立刻充电。",
                "tradeoff": "保障出行，可能减少可用削峰时间。",
            }
        )
    return alternatives[:3]


def _personalization_notes(persona_config: dict | None) -> list[str]:
    role, label = _persona_role(persona_config)
    tags = (persona_config or {}).get("tags") or {}
    notes = [f"画像: {label}。"]
    if role == "A":
        notes.append("强调晚高峰负荷和可量化影响，同时保证到家舒适。")
    elif role == "B":
        notes.append("舒适和确认权优先，只允许短时、微小、可撤销调整。")
    elif role == "C":
        notes.append("解释基于当前事件和实时输入，不依赖昨天的历史习惯。")
    elif role == "D":
        notes.append("可在预设边界内自动执行，并适合展示响应收益和执行回顾。")
    elif role == "E":
        notes.append("护理安全与稳定性优先，VPP只能作为低风险提醒或非关键负荷避让。")
    elif role == "F":
        notes.append("EV出发SOC是硬约束，充电优化必须先保证次日行程。")
    if tags.get("price") in {"price_sensitive", "price_driven"}:
        notes.append("用户需要看到负荷/电价影响，但不能编造补偿金额。")
    if tags.get("control") in {"confirm_required", "suggestion_first"}:
        notes.append("用建议和确认语言，而不是默认持续授权。")
    return notes


def _why_request(event: dict | None, demand_context: dict | None, capacity_context: dict | None) -> str:
    window = _fmt_window(event)
    demand_context = demand_context or {}
    target_kw = _float_or_none(demand_context.get("target_shed_kw") or demand_context.get("demand_target_kw"))
    if target_kw is not None and target_kw > 0:
        return f"{window}是VPP需求响应窗口，电网侧希望家庭在这段晚高峰减少约{target_kw:.2f}kW的可调负荷。"
    assessment = ((capacity_context or {}).get("assessment") or {}) if isinstance(capacity_context, dict) else {}
    bid = _float_or_none(assessment.get("recommended_bid_kw"))
    if bid is not None and bid > 0:
        return f"{window}是VPP需求响应窗口，家庭容量评估建议可承诺约{bid:.2f}kW的低风险响应。"
    return f"{window}是VPP需求响应窗口，目标是在不破坏舒适和服务约束的前提下减少事件窗口用电。"


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
        str(item.get("rationale", "")).strip().rstrip("。；; ")
        for item in actions[:4]
        if item.get("rationale")
    ]
    action_text = "；".join(action_parts)
    alt_text = " / ".join(str(item.get("name", "")) for item in alternatives[:3] if item.get("name"))
    strategy_name = "仅提醒方案" if any(
        isinstance(item, dict) and item.get("name") == "仅提醒方案"
        for item in alternatives
    ) else "平衡方案"
    protect_text = protected[0] if protected else "舒适和服务约束优先。"
    control_text = user_control[0] if user_control else "用户可以随时取消。"
    return (
        f"{why} 建议采用{strategy_name}：{action_text}。 "
        f"保护边界：{protect_text} {control_text} "
        f"预期收益：{benefit.get('message', '')} 可选方案包括：{alt_text}。"
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
        "language": "zh-CN",
        "persona_id": str((persona_config or {}).get("id", "")),
        "persona_role": role,
        "persona_role_label": role_label,
        "method": method,
        "city": city,
        "event_id": (event or {}).get("id"),
        "vpp_window": _fmt_window(event),
        "agent_reason": str(reason or ""),
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
        if value:
            merged[key] = value
    for key in ("recommended_actions", "protected_constraints", "user_control", "alternatives", "personalization_notes"):
        value = _coerce_list(raw.get(key))
        if value:
            merged[key] = value
    benefit = _coerce_dict(raw.get("expected_benefit"))
    if benefit:
        merged["expected_benefit"] = {**base["expected_benefit"], **benefit}
    structured = _coerce_dict(raw.get("structured_control_constraints"))
    if structured:
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
    merged["llm_raw_explanation"] = raw
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
