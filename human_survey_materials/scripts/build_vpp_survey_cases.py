#!/usr/bin/env python3
"""Build human-readable VPP survey cases from EnergyBridge benchmark results.

The output is intended for human preference calibration. Participants should see
only the role card, event context, and natural-language strategy/outcome. Method
labels are kept in the CSV/JSON for researcher analysis, not for the survey UI.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MAIN_DIR = PROJECT_ROOT / "paper_results" / "01_main_household_5x2_final"
DEFAULT_PERSONA_RESULTS_DIR = PROJECT_ROOT / "benchmark_results" / "2026-07-07_mainfig_refresh_v1"
DEFAULT_HEMA_PERSONA_RESULTS_DIR = PROJECT_ROOT / "benchmark_results" / "2026-07-21_hema_persona_tianjin_1day_v1"
DEFAULT_OUT = PROJECT_ROOT / "human_survey_materials" / "sample_cases"
DEFAULT_HOUSEHOLD_METHODS = ["EnergyBridge", "hema_agent", "mpc_dynamic", "rule_milp", "rl_ppo_pref_v2"]
DEFAULT_PERSONA_METHODS = ["EnergyBridge", "hema_agent", "mpc_dynamic", "rule_milp", "rl_ppo_pref_v2"]
ROLE_CARD_PATH = PROJECT_ROOT / "human_survey_materials" / "role_cards" / "fixed_role_cards_zh_en.json"
HOUSEHOLD_DIR = PROJECT_ROOT / "energybridge" / "roleplay" / "households"
PERSONA_DIR = PROJECT_ROOT / "energybridge" / "roleplay" / "personas"

ROLE_CARD_BY_HOUSEHOLD = {
    "household_s1_dual_commuter_standard": "role_a_price_cooperative_commuter",
    "household_s2_multigeneration_caregiver": "role_e_caregiver_low_dr",
    "household_s3_hybrid_work_from_home": "role_b_home_comfort_gated",
    "household_s4_ev_commuter_flexible": "role_f_ev_commuter_optimizer",
    "household_s5_shared_roommates_irregular": "role_c_irregular_cautious",
}

BASIC_PERSONAS = {
    "role_a": "basic_role_a_commuter_price_cooperative",
    "role_b": "basic_role_b_home_comfort_gated",
    "role_c": "basic_role_c_irregular_cautious",
    "role_d": "basic_role_d_commuter_ideal_dr",
    "role_e": "basic_role_e_caregiver_low_dr",
    "role_f": "basic_role_f_commuter_ev_optimizer",
}

ROLE_CARD_BY_PERSONA = {
    "basic_role_a_commuter_price_cooperative": "role_a_price_cooperative_commuter",
    "basic_role_b_home_comfort_gated": "role_b_home_comfort_gated",
    "basic_role_c_irregular_cautious": "role_c_irregular_cautious",
    "basic_role_d_commuter_ideal_dr": "role_d_ideal_dr_participant",
    "basic_role_e_caregiver_low_dr": "role_e_caregiver_low_dr",
    "basic_role_f_commuter_ev_optimizer": "role_f_ev_commuter_optimizer",
}

PERSONA_TITLE_ZH = {
    "basic_role_a_commuter_price_cooperative": "规律通勤者 - 价格合作型",
    "basic_role_b_home_comfort_gated": "居家用户 - 舒适优先型",
    "basic_role_c_irregular_cautious": "日程不规律者 - 高确认需求型",
    "basic_role_d_commuter_ideal_dr": "规律通勤者 - 理想需求响应型",
    "basic_role_e_caregiver_low_dr": "家庭照护者 - 低需求响应型",
    "basic_role_f_commuter_ev_optimizer": "规律通勤者 - EV 优化型",
}

PERSONA_DESC_ZH = {
    "basic_role_a_commuter_price_cooperative": "工作日白天通常不在家，18:30 左右到家；晚间用电集中。愿意为了省钱调整家务时间，但希望知道大概能省多少。温度需求中等，可以接受小幅调整和确认。",
    "basic_role_b_home_comfort_gated": "全天在家，可能远程办公；对温度变化敏感，不愿为了小额节省牺牲舒适。只接受非常短、非常小的温控调整，并希望提前确认。",
    "basic_role_c_irregular_cautious": "日程高度不规律，经常加班、临时外出或出差。愿意听节能建议，但每次动作前都需要清楚解释和确认。",
    "basic_role_d_commuter_ideal_dr": "日程规律，温度容忍度较高，愿意在预设边界内让系统自动调度。家务任务可灵活移动，重视电费节省和需求响应奖励。",
    "basic_role_e_caregiver_low_dr": "家中有老人或儿童，舒适、安全和稳定性优先。通常不适合作为需求响应目标，系统应以提醒和异常告警为主。",
    "basic_role_f_commuter_ev_optimizer": "拥有电动车，晚上回家充电、早晨离家。愿意让系统自动安排低价充电，但必须保证第二天出发前达到目标电量。",
}

PERSONA_METHOD_DIR = {
    "EnergyBridge": "{role}_EnergyBridge_{city}_7days",
    "mpc_dynamic": "{role}_mpc_dynamic_H6_{city}_7days",
    "rule_milp": "{role}_rule_milp_{city}_7days",
    "rl_ppo_pref_v2": "{role}_rl_ppo_pref_v2_{city}_7days",
    "hema_agent": "{role}_hema_agent_{city}_1days",
}

METHOD_SOURCE_HINT = {
    "EnergyBridge": "personalized agent plan with user-facing explanation",
    "hema_agent": "generic agent plan with short explanation",
    "mpc_dynamic": "optimization baseline translated into ordinary language",
    "rule_milp": "rule and MILP baseline translated into ordinary language",
    "rl_ppo_pref_v2": "RL policy translated into ordinary language",
}

METHOD_LABEL = {
    "EnergyBridge": "EnergyBridge",
    "agent": "EnergyBridge",
    "hema_agent": "HEMA agent",
    "mpc_dynamic": "MPC",
    "rule_milp": "Rule+MILP",
    "rl_ppo_pref_v2": "RL",
}

SERVICE_NAMES_ZH = {
    "washer": "洗衣机",
    "dishwasher": "洗碗机",
    "dryer": "烘干机",
    "water_heater": "热水器",
    "ev": "电动车充电",
}

SERVICE_NAMES_EN = {
    "washer": "washer",
    "dishwasher": "dishwasher",
    "dryer": "dryer",
    "water_heater": "water heater",
    "ev": "EV charging",
}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_role_cards() -> dict[str, dict[str, Any]]:
    if not ROLE_CARD_PATH.exists():
        return {}
    cards = _read_json(ROLE_CARD_PATH)
    if not isinstance(cards, list):
        return {}
    return {str(card.get("role_card_id")): card for card in cards if isinstance(card, dict)}


def _load_household_config(household_id: Any) -> tuple[dict[str, Any], str]:
    path = HOUSEHOLD_DIR / f"{household_id}.json"
    if not path.exists():
        return {}, ""
    data = _read_json(path)
    return data if isinstance(data, dict) else {}, str(path)


def _load_persona_config(persona_alias: str) -> tuple[dict[str, Any], str]:
    persona_id = BASIC_PERSONAS.get(persona_alias, persona_alias)
    path = PERSONA_DIR / f"{persona_id}.json"
    if not path.exists():
        return {}, ""
    data = _read_json(path)
    return data if isinstance(data, dict) else {}, str(path)


def _clean_text(text: Any, *, max_len: int = 900) -> str:
    text = str(text or "").replace("\n", " ").strip()
    text = re.sub(r"\s+", " ", text)
    return text[:max_len].rstrip()


def _fmt_time(hour: Any) -> str:
    try:
        h = float(hour) % 24.0
    except (TypeError, ValueError):
        return "unknown"
    hh = int(h)
    mm = int(round((h - hh) * 60))
    if mm == 60:
        hh = (hh + 1) % 24
        mm = 0
    return f"{hh:02d}:{mm:02d}"


def _fmt_num(value: Any, digits: int = 1) -> str:
    try:
        return f"{float(value):.{digits}f}"
    except (TypeError, ValueError):
        return "unknown"


def _city_zh(city: Any) -> str:
    mapping = {"Tianjin": "天津", "Germany": "德国", "tianjin": "天津", "germany": "德国"}
    return mapping.get(str(city), str(city or "未知地区"))


def _fmt_hour_text(hour: Any) -> str:
    formatted = _fmt_time(hour)
    return formatted if formatted != "unknown" else "未知"


def _service_list(items: list[Any], names: dict[str, str]) -> str:
    if not items:
        return ""
    return "、".join(names.get(str(x), str(x)) for x in items)


def _household_profile_text(
    household: dict[str, Any],
    role_card: dict[str, Any],
    *,
    lang: str,
) -> str:
    side = role_card.get(lang) if isinstance(role_card.get(lang), dict) else {}
    appliances = household.get("appliances") if isinstance(household.get("appliances"), dict) else {}
    prefs = household.get("preferences") if isinstance(household.get("preferences"), dict) else {}
    weights = prefs.get("scoring_weights") if isinstance(prefs.get("scoring_weights"), dict) else {}
    members = household.get("members") if isinstance(household.get("members"), list) else []
    ac = appliances.get("ac") if isinstance(appliances.get("ac"), dict) else {}
    water = appliances.get("water_heater") if isinstance(appliances.get("water_heater"), dict) else {}
    ev = appliances.get("ev") if isinstance(appliances.get("ev"), dict) else {}

    baseline = role_card.get("generic_vpp_baseline_acceptance")
    try:
        baseline_pct = f"{float(baseline) * 100:.0f}/100"
    except (TypeError, ValueError):
        baseline_pct = "unknown"

    present_devices = [
        name
        for name, cfg in appliances.items()
        if isinstance(cfg, dict) and bool(cfg.get("present")) and name != "refrigerator"
    ]
    member_bits = []
    for member in members[:5]:
        if not isinstance(member, dict):
            continue
        role = member.get("household_role") or member.get("member_id")
        weight = member.get("decision_weight")
        if weight is not None:
            member_bits.append(f"{role} (weight {weight})")
        else:
            member_bits.append(str(role))

    if lang == "zh":
        device_names = {
            "ac": "空调",
            "washer": "洗衣机",
            "dryer": "烘干机",
            "dishwasher": "洗碗机",
            "water_heater": "热水器",
            "ev": "电动车",
        }
        devices = "、".join(device_names.get(x, x) for x in present_devices) or "未列出"
        weight_text = (
            f"舒适 {float(weights.get('comfort', 0.0)):.2f}，电费/节能 {float(weights.get('energy', 0.0)):.2f}，VPP {float(weights.get('vpp', 0.0)):.2f}"
            if weights
            else "未列出"
        )
        ac_text = "空调偏好未列出"
        if ac:
            ac_text = (
                f"空调偏好约 {ac.get('setpoint_preferred_min_c', '未知')}-{ac.get('setpoint_preferred_max_c', '未知')}°C，"
                f"温度容忍约 {ac.get('temp_tolerance_c', '未知')}°C"
            )
        water_text = ""
        if water:
            water_text = f"热水通常需要在 {_fmt_hour_text(water.get('bath_required_h'))} 前准备好。"
        ev_text = ""
        if ev and ev.get("present"):
            ev_text = (
                f"EV 通常 {_fmt_hour_text(ev.get('arrival_h'))} 到家接入，"
                f"次日 {_fmt_hour_text(ev.get('departure_h'))} 前需要达到目标电量。"
            )
        return (
            f"用户/家庭：{household.get('display_name') or household.get('id')}\n"
            f"真实场景摘要：{household.get('description', '')}\n"
            f"家庭成员和决策权重：{'; '.join(member_bits) or '未列出'}。\n"
            f"可调设备：{devices}。\n"
            f"偏好权重：{weight_text}。\n"
            f"舒适与日程约束：{ac_text}。{water_text} {ev_text}\n"
            f"接受倾向锚点：看到具体策略前，请参考这个角色/家庭的普通 VPP 接受倾向，大约从 {baseline_pct} 开始。\n"
            f"判断提示：{side.get('adjustment_prompt', '')}\n"
            f"推理重点：{side.get('reasoning_focus', '')}"
        )

    device_names_en = {
        "ac": "AC",
        "washer": "washer",
        "dryer": "dryer",
        "dishwasher": "dishwasher",
        "water_heater": "water heater",
        "ev": "EV",
    }
    devices = ", ".join(device_names_en.get(x, x) for x in present_devices) or "not listed"
    weight_text = (
        f"comfort {float(weights.get('comfort', 0.0)):.2f}, cost/energy {float(weights.get('energy', 0.0)):.2f}, VPP {float(weights.get('vpp', 0.0)):.2f}"
        if weights
        else "not listed"
    )
    ac_text = "AC preference is not listed"
    if ac:
        ac_text = (
            f"AC preference is about {ac.get('setpoint_preferred_min_c', 'unknown')}-{ac.get('setpoint_preferred_max_c', 'unknown')}°C, "
            f"with temperature tolerance around {ac.get('temp_tolerance_c', 'unknown')}°C"
        )
    water_text = ""
    if water:
        water_text = f"Hot water is usually needed by {_fmt_hour_text(water.get('bath_required_h'))}."
    ev_text = ""
    if ev and ev.get("present"):
        ev_text = (
            f"EV usually plugs in around {_fmt_hour_text(ev.get('arrival_h'))} "
            f"and must be ready before {_fmt_hour_text(ev.get('departure_h'))} next day."
        )
    return (
        f"User/household: {household.get('display_name') or household.get('id')}\n"
        f"Real scenario summary: {household.get('description', '')}\n"
        f"Members and decision weights: {'; '.join(member_bits) or 'not listed'}.\n"
        f"Controllable devices: {devices}.\n"
        f"Preference weights: {weight_text}.\n"
        f"Comfort and routine constraints: {ac_text}. {water_text} {ev_text}\n"
        f"Acceptance anchor: before reading the specific strategy, start from about {baseline_pct} willingness for this role/household.\n"
        f"Judgment cue: {side.get('adjustment_prompt', '')}\n"
        f"Reasoning focus: {side.get('reasoning_focus', '')}"
    )


def _acceptance_tendency_text(baseline_acceptance: Any, *, lang: str) -> str:
    try:
        accept = max(0.0, min(1.0, float(baseline_acceptance)))
    except (TypeError, ValueError):
        return "未列出" if lang == "zh" else "not listed"
    if lang == "zh":
        if accept >= 0.8:
            level = "偏高"
        elif accept >= 0.5:
            level = "中等"
        elif accept >= 0.25:
            level = "偏低"
        else:
            level = "很低"
        return f"普通情况下约 {accept * 100:.0f}/100，属于{level}；具体策略仍会根据舒适、日程和解释质量上下调整"
    if accept >= 0.8:
        level = "high"
    elif accept >= 0.5:
        level = "moderate"
    elif accept >= 0.25:
        level = "low"
    else:
        level = "very low"
    return f"about {accept * 100:.0f}/100 in ordinary cases, a {level} baseline; adjust up or down based on comfort, schedule fit, and explanation quality"


def _persona_profile_text(
    persona: dict[str, Any],
    role_card: dict[str, Any],
    *,
    lang: str,
) -> str:
    schedule = persona.get("schedule") if isinstance(persona.get("schedule"), dict) else {}
    appliances = persona.get("appliances") if isinstance(persona.get("appliances"), dict) else {}
    ac = appliances.get("ac") if isinstance(appliances.get("ac"), dict) else {}
    water = appliances.get("water_heater") if isinstance(appliances.get("water_heater"), dict) else {}
    ev = appliances.get("ev") if isinstance(appliances.get("ev"), dict) else {}
    washer = appliances.get("washer") if isinstance(appliances.get("washer"), dict) else {}
    dishwasher = appliances.get("dishwasher") if isinstance(appliances.get("dishwasher"), dict) else {}
    baseline_acceptance = role_card.get("generic_vpp_baseline_acceptance")
    role_side = role_card.get(lang) if isinstance(role_card.get(lang), dict) else {}

    devices = [
        name
        for name, cfg in appliances.items()
        if isinstance(cfg, dict) and bool(cfg.get("present")) and name != "refrigerator"
    ]
    judgment_cue = str(role_side.get("adjustment_prompt", ""))
    if "ev" not in devices:
        judgment_cue = judgment_cue.replace("热水、家务、EV 和回家舒适", "热水、家务和回家舒适")
        judgment_cue = judgment_cue.replace("hot water, chores, EV, and return-home comfort", "hot water, chores, and return-home comfort")
    if lang == "zh":
        persona_id = str(persona.get("id") or "")
        title = PERSONA_TITLE_ZH.get(persona_id, str(persona.get("display_name") or persona_id))
        description = PERSONA_DESC_ZH.get(persona_id, str(persona.get("description") or ""))
        device_names = {
            "ac": "空调",
            "washer": "洗衣机",
            "dryer": "烘干机",
            "dishwasher": "洗碗机",
            "water_heater": "热水器",
            "ev": "电动车",
        }
        device_text = "、".join(device_names.get(x, x) for x in devices) or "未列出"
        occupancy = str(schedule.get("occupancy_pattern", "unknown"))
        if occupancy == "stay_at_home":
            routine = (
                f"通常全天在家；起床约 {_fmt_hour_text(schedule.get('wake_h'))}，"
                f"洗澡/热水需求约 {_fmt_hour_text(schedule.get('bath_shower_h'))}，睡觉约 {_fmt_hour_text(schedule.get('sleep_h'))}"
            )
        else:
            routine = (
                f"通常 {_fmt_hour_text(schedule.get('leaves_home_h'))} 离家，"
                f"{_fmt_hour_text(schedule.get('returns_home_h'))} 到家，"
                f"洗澡/热水需求约 {_fmt_hour_text(schedule.get('bath_shower_h'))}，睡觉约 {_fmt_hour_text(schedule.get('sleep_h'))}"
            )
        ac_text = "空调偏好未列出"
        if ac:
            ac_text = (
                f"空调舒适区间约 {ac.get('setpoint_preferred_min_c', '未知')}-{ac.get('setpoint_preferred_max_c', '未知')}°C，"
                f"容忍偏离约 {ac.get('temp_tolerance_c', '未知')}°C"
            )
        chore_bits = []
        if washer.get("present"):
            chore_bits.append(
                f"洗衣机偏好 {_fmt_hour_text(washer.get('preferred_h'))}，可运行区间 {_fmt_hour_text(washer.get('earliest_h'))}-{_fmt_hour_text(washer.get('latest_h'))}"
            )
        if dishwasher.get("present"):
            chore_bits.append(
                f"洗碗机偏好 {_fmt_hour_text(dishwasher.get('preferred_h'))}，可运行区间 {_fmt_hour_text(dishwasher.get('earliest_h'))}-{_fmt_hour_text(dishwasher.get('latest_h'))}"
            )
        if water.get("present"):
            chore_bits.append(f"热水需要在 {_fmt_hour_text(water.get('bath_required_h'))} 前可用")
        if ev.get("present"):
            chore_bits.append(
                f"EV {_fmt_hour_text(ev.get('arrival_h'))} 到家接入，次日 {_fmt_hour_text(ev.get('departure_h'))} 前需达到目标电量"
            )
        return (
            f"角色：{title}\n"
            f"角色说明：{description}\n"
            f"日程：{routine}；日程波动约 {schedule.get('schedule_variability_h', '未知')} 小时。\n"
            f"可调设备：{device_text}。\n"
            f"舒适边界：{ac_text}。\n"
            f"设备日程：{'；'.join(chore_bits) or '未列出'}。\n"
            f"VPP 接受倾向锚点：{_acceptance_tendency_text(baseline_acceptance, lang='zh')}。\n"
            f"判断提示：{judgment_cue}\n"
            "请按照这个角色来判断，不要代入你自己的真实家庭。"
        )

    device_names_en = {
        "ac": "AC",
        "washer": "washer",
        "dryer": "dryer",
        "dishwasher": "dishwasher",
        "water_heater": "water heater",
        "ev": "EV",
    }
    device_text = ", ".join(device_names_en.get(x, x) for x in devices) or "not listed"
    occupancy = str(schedule.get("occupancy_pattern", "unknown"))
    if occupancy == "stay_at_home":
        routine = (
            f"usually at home all day; wakes around {_fmt_hour_text(schedule.get('wake_h'))}, "
            f"needs hot water around {_fmt_hour_text(schedule.get('bath_shower_h'))}, sleeps around {_fmt_hour_text(schedule.get('sleep_h'))}"
        )
    else:
        routine = (
            f"usually leaves home around {_fmt_hour_text(schedule.get('leaves_home_h'))}, "
            f"returns around {_fmt_hour_text(schedule.get('returns_home_h'))}, "
            f"needs hot water around {_fmt_hour_text(schedule.get('bath_shower_h'))}, sleeps around {_fmt_hour_text(schedule.get('sleep_h'))}"
        )
    ac_text = "AC preference is not listed"
    if ac:
        ac_text = (
            f"AC comfort range about {ac.get('setpoint_preferred_min_c', 'unknown')}-{ac.get('setpoint_preferred_max_c', 'unknown')}°C, "
            f"with tolerance around {ac.get('temp_tolerance_c', 'unknown')}°C"
        )
    chore_bits = []
    if washer.get("present"):
        chore_bits.append(
            f"washer preferred at {_fmt_hour_text(washer.get('preferred_h'))}, allowed {_fmt_hour_text(washer.get('earliest_h'))}-{_fmt_hour_text(washer.get('latest_h'))}"
        )
    if dishwasher.get("present"):
        chore_bits.append(
            f"dishwasher preferred at {_fmt_hour_text(dishwasher.get('preferred_h'))}, allowed {_fmt_hour_text(dishwasher.get('earliest_h'))}-{_fmt_hour_text(dishwasher.get('latest_h'))}"
        )
    if water.get("present"):
        chore_bits.append(f"hot water must be ready by {_fmt_hour_text(water.get('bath_required_h'))}")
    if ev.get("present"):
        chore_bits.append(
            f"EV plugs in around {_fmt_hour_text(ev.get('arrival_h'))} and must reach target charge before {_fmt_hour_text(ev.get('departure_h'))} next day"
        )
    return (
        f"Role: {persona.get('display_name') or persona.get('id')}\n"
        f"Role description: {persona.get('description', '')}\n"
        f"Routine: {routine}; schedule variability about {schedule.get('schedule_variability_h', 'unknown')} h.\n"
        f"Controllable devices: {device_text}.\n"
        f"Comfort boundary: {ac_text}.\n"
        f"Device routine: {'; '.join(chore_bits) or 'not listed'}.\n"
        f"VPP acceptance anchor: {_acceptance_tendency_text(baseline_acceptance, lang='en')}.\n"
        f"Judgment cue: {judgment_cue}\n"
        "Please judge as this role, not as your own real household."
    )


def _presentation_style(method: Any) -> str:
    method = str(method)
    if method in {"EnergyBridge", "agent"}:
        return "personalized_explanation"
    if method == "hema_agent":
        return "generic_agent_explanation"
    return "plain_strategy_translation"


def _plan_time(value: Any) -> str:
    if value is None:
        return "unknown"
    return _fmt_time(value)


def _event_proposed_plan(event: dict[str, Any], gate: dict[str, Any]) -> dict[str, Any]:
    if isinstance(gate.get("proposed_plan"), dict) and gate.get("proposed_plan"):
        plan = dict(gate["proposed_plan"])
        plan.setdefault("setpoint", event.get("setpoint"))
        return plan
    actions: dict[str, Any] = {}
    for key in ("selected_strategy", "vpp_trigger_actions"):
        source = event.get(key)
        if isinstance(source, dict):
            nested = source.get("appliance_actions")
            if isinstance(nested, dict):
                actions.update(nested)
    for decision in event.get("day_decisions") or []:
        if not isinstance(decision, dict):
            continue
        decision_actions = decision.get("actions") or decision.get("raw_appliance_actions")
        if isinstance(decision_actions, dict) and decision_actions:
            actions.update({k: v for k, v in decision_actions.items() if v is not None})
            break
    return {
        "setpoint": event.get("setpoint"),
        "appliance_actions": actions,
        "reason": event.get("user_input") or event.get("reason"),
    }


def _event_default_plan(gate: dict[str, Any]) -> dict[str, Any]:
    return gate.get("default_plan") if isinstance(gate.get("default_plan"), dict) else {}


def _plan_summary_zh(plan: dict[str, Any]) -> str:
    actions = plan.get("appliance_actions") if isinstance(plan.get("appliance_actions"), dict) else {}
    parts: list[str] = []
    if plan.get("setpoint") is not None:
        try:
            setpoint = float(plan.get("setpoint"))
        except (TypeError, ValueError):
            setpoint = None
        if setpoint is not None and setpoint >= 35.0:
            parts.append(f"VPP 窗口临时暂停制冷（仿真等效设定约 {_fmt_num(setpoint)}°C）")
        else:
            parts.append(f"空调设定约 {_fmt_num(plan.get('setpoint'))}°C")
    if actions.get("washer_skip") is True:
        parts.append("洗衣机跳过")
    elif actions.get("washer_start_h") is not None:
        parts.append(f"洗衣机 {_plan_time(actions.get('washer_start_h'))} 开始")
    if actions.get("dryer_skip") is True:
        parts.append("烘干机跳过")
    elif actions.get("dryer_start_h") is not None:
        parts.append(f"烘干机 {_plan_time(actions.get('dryer_start_h'))} 开始")
    if actions.get("dishwasher_skip") is True:
        parts.append("洗碗机跳过")
    elif actions.get("dishwasher_start_h") is not None:
        parts.append(f"洗碗机 {_plan_time(actions.get('dishwasher_start_h'))} 开始")
    if actions.get("water_heater_preheat") is True or actions.get("water_heater_preheat_start_h") is not None:
        start = _plan_time(actions.get("water_heater_preheat_start_h"))
        end = _plan_time(actions.get("water_heater_preheat_end_h"))
        temp = actions.get("water_heater_preheat_temp_c")
        if temp is not None:
            parts.append(f"热水器 {start}-{end} 预热到约 {_fmt_num(temp)}°C")
        else:
            parts.append(f"热水器 {start}-{end} 预热")
    if actions.get("ev_charge_start_h") is not None or actions.get("ev_charge_end_h") is not None:
        parts.append(f"EV 充电 {_plan_time(actions.get('ev_charge_start_h'))}-{_plan_time(actions.get('ev_charge_end_h'))}")
    if not parts and plan.get("reason"):
        parts.append("没有明确列出家电动作，只给出控制原因")
    return "；".join(parts) + "。" if parts else "未找到明确策略动作。"


def _plan_summary_en(plan: dict[str, Any]) -> str:
    actions = plan.get("appliance_actions") if isinstance(plan.get("appliance_actions"), dict) else {}
    parts: list[str] = []
    if plan.get("setpoint") is not None:
        try:
            setpoint = float(plan.get("setpoint"))
        except (TypeError, ValueError):
            setpoint = None
        if setpoint is not None and setpoint >= 35.0:
            parts.append(f"temporarily pause cooling during the VPP window (simulation-equivalent setpoint about {_fmt_num(setpoint)}°C)")
        else:
            parts.append(f"AC setpoint about {_fmt_num(plan.get('setpoint'))}°C")
    if actions.get("washer_skip") is True:
        parts.append("skip washer")
    elif actions.get("washer_start_h") is not None:
        parts.append(f"washer starts at {_plan_time(actions.get('washer_start_h'))}")
    if actions.get("dryer_skip") is True:
        parts.append("skip dryer")
    elif actions.get("dryer_start_h") is not None:
        parts.append(f"dryer starts at {_plan_time(actions.get('dryer_start_h'))}")
    if actions.get("dishwasher_skip") is True:
        parts.append("skip dishwasher")
    elif actions.get("dishwasher_start_h") is not None:
        parts.append(f"dishwasher starts at {_plan_time(actions.get('dishwasher_start_h'))}")
    if actions.get("water_heater_preheat") is True or actions.get("water_heater_preheat_start_h") is not None:
        start = _plan_time(actions.get("water_heater_preheat_start_h"))
        end = _plan_time(actions.get("water_heater_preheat_end_h"))
        temp = actions.get("water_heater_preheat_temp_c")
        if temp is not None:
            parts.append(f"water heater preheats {start}-{end} to about {_fmt_num(temp)}°C")
        else:
            parts.append(f"water heater preheats {start}-{end}")
    if actions.get("ev_charge_start_h") is not None or actions.get("ev_charge_end_h") is not None:
        parts.append(f"EV charging { _plan_time(actions.get('ev_charge_start_h')) }-{ _plan_time(actions.get('ev_charge_end_h')) }")
    if not parts and plan.get("reason"):
        parts.append("no explicit appliance actions, only a control reason")
    return "; ".join(parts) + "." if parts else "No explicit strategy action found."


def _raw_strategy_explanation(event: dict[str, Any], plan: dict[str, Any]) -> str:
    explanation = event.get("strategy_explanation") if isinstance(event.get("strategy_explanation"), dict) else {}
    selected = event.get("selected_strategy") if isinstance(event.get("selected_strategy"), dict) else {}
    return _clean_text(
        explanation.get("natural_language")
        or plan.get("reason")
        or event.get("reason")
        or selected.get("preference_text"),
        max_len=1600,
    )


def _explanation_claims(raw_text: str) -> list[str]:
    text = raw_text.lower()
    claims: list[str] = []
    def has_any(words: list[str]) -> bool:
        return any(re.search(rf"\b{re.escape(word)}\b", text) for word in words)

    if any(k in text for k in ["comfort", "setpoint", "temperature"]) or has_any(["ac"]):
        claims.append("comfort")
    if has_any(["cost", "price", "saving", "savings", "cheap"]):
        claims.append("cost")
    if "demand response" in text or has_any(["vpp", "peak", "grid"]):
        claims.append("vpp")
    if has_any(["washer", "dishwasher", "dryer", "laundry", "chores"]):
        claims.append("chores")
    if "hot-water" in text or "hot water" in text or has_any(["water", "heater", "shower"]):
        claims.append("hot_water")
    if has_any(["ev", "charge", "charging"]):
        claims.append("ev")
    if "future-feasible" in text or has_any(["routine", "calendar", "arrival", "sleep"]):
        claims.append("schedule")
    return claims


def _structured_explanation_summary(event: dict[str, Any], *, lang: str) -> str:
    explanation = event.get("strategy_explanation") if isinstance(event.get("strategy_explanation"), dict) else {}
    if not explanation:
        return ""
    window = str(explanation.get("vpp_window") or "the VPP window")
    protected = " ".join(str(item) for item in (explanation.get("protected_constraints") or [])).lower()
    controls = " ".join(str(item) for item in (explanation.get("user_control") or [])).lower()
    recommended = explanation.get("recommended_actions") if isinstance(explanation.get("recommended_actions"), list) else []
    devices = {
        str(item.get("device")).lower()
        for item in recommended
        if isinstance(item, dict) and item.get("device")
    }
    expected = explanation.get("expected_benefit") if isinstance(explanation.get("expected_benefit"), dict) else {}
    shift_kw = expected.get("load_shift_kw_estimate")

    if lang == "zh":
        parts = [f"系统说明：这些调整只用于 {window} 削峰，事件后会恢复正常控制"]
        protections = []
        if "hot water" in protected or "shower" in protected or "bath" in protected:
            protections.append("洗澡前热水可用")
        if "ev" in devices or re.search(r"\bev\b", protected) or "charge" in protected:
            protections.append("出发前 EV 电量达标")
        if devices.intersection({"washer", "dishwasher", "dryer"}) or any(
            word in protected for word in ("washer", "dishwasher", "dryer", "chore")
        ):
            protections.append("必要家务仍会完成")
        if protections:
            parts.append("并明确保证" + "、".join(protections))
        if controls:
            parts.append("你可以取消方案；感到不适时可立即恢复平常设置")
        try:
            parts.append(f"预计可转移约 {float(shift_kw):.1f} kW 负荷，费用收益按实际电价或 VPP 规则结算")
        except (TypeError, ValueError):
            pass
        return "；".join(parts) + "。"

    parts = [f"System explanation: these changes apply only during {window}, with normal control restored afterward"]
    protections = []
    if "hot water" in protected or "shower" in protected or "bath" in protected:
        protections.append("hot water before the shower")
    if "ev" in devices or re.search(r"\bev\b", protected) or "charge" in protected:
        protections.append("target EV charge before departure")
    if devices.intersection({"washer", "dishwasher", "dryer"}) or any(
        word in protected for word in ("washer", "dishwasher", "dryer", "chore")
    ):
        protections.append("completion of required chores")
    if protections:
        parts.append("it explicitly protects " + ", ".join(protections))
    if controls:
        parts.append("you can cancel the plan and restore the usual setting if uncomfortable")
    try:
        parts.append(f"the estimated shifted load is about {float(shift_kw):.1f} kW, with savings determined by the actual tariff or VPP rules")
    except (TypeError, ValueError):
        pass
    return "; ".join(parts) + "."


def _real_explanation_summary_zh(method: Any, event: dict[str, Any], plan: dict[str, Any]) -> str:
    style = _presentation_style(method)
    if style == "plain_strategy_translation":
        return "该方法没有生成面向用户的个性化解释；这里只把真实控制动作翻译成人话。"
    if style == "personalized_explanation":
        structured = _structured_explanation_summary(event, lang="zh")
        if structured:
            return structured
    raw = _raw_strategy_explanation(event, plan)
    claims = _explanation_claims(raw)
    if claims:
        actions = []
        if "comfort" in claims:
            actions.append("把空调控制在合理舒适范围")
        if any(item in claims for item in ("chores", "hot_water", "ev")):
            actions.append("尽量把可调设备移出 VPP 时段并保证必要服务")
        if "schedule" in claims:
            actions.append("兼顾用户日程")
        if "cost" in claims:
            actions.append("考虑低价时段或节省")
        return "系统说明：为了配合本次削峰，将" + "，".join(actions or ["执行上述调整"]) + "，事件后恢复正常运行。"
    return f"真实生成的用户说明摘要：{raw[:260]}" if raw else "真实输出中没有找到额外用户说明。"


def _real_explanation_summary_en(method: Any, event: dict[str, Any], plan: dict[str, Any]) -> str:
    style = _presentation_style(method)
    if style == "plain_strategy_translation":
        return "This method did not generate a personalized user-facing explanation; only the real control actions are translated."
    if style == "personalized_explanation":
        structured = _structured_explanation_summary(event, lang="en")
        if structured:
            return structured
    raw = _raw_strategy_explanation(event, plan)
    claims = _explanation_claims(raw)
    if claims:
        actions = []
        if "comfort" in claims:
            actions.append("keep AC within a reasonable comfort range")
        if any(item in claims for item in ("chores", "hot_water", "ev")):
            actions.append("move flexible devices outside the VPP window while protecting required services")
        if "schedule" in claims:
            actions.append("respect the user's schedule")
        if "cost" in claims:
            actions.append("consider low-price timing or savings")
        return "System explanation: to support this peak event, the plan will " + ", ".join(actions or ["apply the listed changes"]) + " and return to normal operation afterward."
    return f"Summary of the real generated user explanation: {raw[:260]}" if raw else "No additional user-facing explanation was found in the real output."


def _risk_summary_zh(event: dict[str, Any], gate: dict[str, Any]) -> str:
    intrusion = gate.get("intrusion") or {}
    risks: list[str] = []
    if intrusion.get("vpp_conflicts"):
        risks.append("仍可能有可移动家电落在 VPP 时段")
    if intrusion.get("skip_devices"):
        risks.append("可能牺牲部分原定家务或设备服务")
    try:
        comfort_score = float(event.get("comfort_score"))
        if comfort_score < 3.0:
            risks.append("舒适体验可能偏弱")
    except (TypeError, ValueError):
        pass
    try:
        vpp_score = float(event.get("vpp_score"))
        if vpp_score < 3.0:
            risks.append("VPP 避峰效果可能不稳定")
    except (TypeError, ValueError):
        pass
    if not risks:
        return "主要风险不突出，被试可重点判断该策略是否符合角色的个人优先级。"
    return "主要风险：" + "；".join(risks) + "。"


def _risk_summary_en(event: dict[str, Any], gate: dict[str, Any]) -> str:
    intrusion = gate.get("intrusion") or {}
    risks: list[str] = []
    if intrusion.get("vpp_conflicts"):
        risks.append("some flexible appliances may still run during the VPP window")
    if intrusion.get("skip_devices"):
        risks.append("some planned chores or device services may be sacrificed")
    try:
        comfort_score = float(event.get("comfort_score"))
        if comfort_score < 3.0:
            risks.append("comfort may be weak")
    except (TypeError, ValueError):
        pass
    try:
        vpp_score = float(event.get("vpp_score"))
        if vpp_score < 3.0:
            risks.append("VPP peak reduction may be unreliable")
    except (TypeError, ValueError):
        pass
    if not risks:
        return "No major risk is obvious; participants should focus on whether this fits the role's priorities."
    return "Main risks: " + "; ".join(risks) + "."


def _strategy_text_zh(event: dict[str, Any], gate: dict[str, Any], *, method: Any) -> str:
    intrusion = gate.get("intrusion") or {}
    plan = _event_proposed_plan(event, gate)
    parts = []
    parts.append("真实生成的 VPP 策略：" + _plan_summary_zh(plan))
    default_plan = _event_default_plan(gate)
    if default_plan and default_plan.get("setpoint") is not None and plan.get("setpoint") is not None:
        parts.append(f"普通日计划空调约 {_fmt_num(default_plan.get('setpoint'))}°C，用于对比。")
    if intrusion.get("vpp_conflicts"):
        parts.append("注意：策略中仍可能有设备与 VPP 窗口冲突。")
    if intrusion.get("skip_devices"):
        parts.append("注意：策略可能跳过某些原本需要完成的服务。")
    parts.append(_real_explanation_summary_zh(method, event, plan))
    return " ".join(parts)


def _strategy_text_en(event: dict[str, Any], gate: dict[str, Any], *, method: Any) -> str:
    intrusion = gate.get("intrusion") or {}
    plan = _event_proposed_plan(event, gate)
    parts = []
    parts.append("Real generated VPP strategy: " + _plan_summary_en(plan))
    default_plan = _event_default_plan(gate)
    if default_plan and default_plan.get("setpoint") is not None and plan.get("setpoint") is not None:
        parts.append(f"Ordinary daily-plan AC setpoint is about {_fmt_num(default_plan.get('setpoint'))}°C for comparison.")
    if intrusion.get("vpp_conflicts"):
        parts.append("Note: some appliance timing may still conflict with the VPP window.")
    if intrusion.get("skip_devices"):
        parts.append("Note: some required service may be skipped.")
    parts.append(_real_explanation_summary_en(method, event, plan))
    return " ".join(parts)


def _event_context_zh(raw: dict[str, Any], row: dict[str, Any], event: dict[str, Any]) -> str:
    day = int(event.get("day") or 1)
    start = _fmt_time(event.get("trigger_h", 18.0))
    end = _fmt_time(event.get("end_h", 19.0))
    city = _city_zh(row.get("city") or raw.get("weather") or "")
    demand = _fmt_num(event.get("demand_target_kw") or event.get("demand_target_shed_kwh"), 2)
    return f"地区：{city}。第 {day} 天，VPP 事件时间为 {start}-{end}，电网希望家庭在这 1 小时内少用约 {demand} kWh 的电。"


def _event_context_en(raw: dict[str, Any], row: dict[str, Any], event: dict[str, Any]) -> str:
    day = int(event.get("day") or 1)
    start = _fmt_time(event.get("trigger_h", 18.0))
    end = _fmt_time(event.get("end_h", 19.0))
    city = row.get("city") or raw.get("weather") or ""
    demand = _fmt_num(event.get("demand_target_kw") or event.get("demand_target_shed_kwh"), 2)
    return f"Region: {city}. Day {day}. The VPP event is {start}-{end}. The grid asks this home to use about {demand} kWh less electricity during this 1-hour window."


def _role_card_text(card: dict[str, Any], lang: str) -> str:
    side = card.get(lang) if isinstance(card.get(lang), dict) else {}
    baseline = card.get("generic_vpp_baseline_acceptance")
    baseline_pct = "unknown"
    try:
        baseline_pct = f"{float(baseline) * 100:.0f}%"
    except (TypeError, ValueError):
        pass
    if lang == "zh":
        return (
            f"标题：{side.get('title', card.get('role_card_id'))}\n"
            f"角色设定：{side.get('narrative', side.get('role_prompt', ''))}\n"
            f"日程：{side.get('daily_context', '')}\n"
            f"偏好线索：{side.get('preference_cues', side.get('comfort_and_devices', ''))}\n"
            f"接受倾向锚点：{baseline_pct}。{side.get('decision_anchor', side.get('baseline_acceptance_instruction', ''))}\n"
            f"如何调整判断：{side.get('adjustment_prompt', '')}\n"
            f"推理重点：{side.get('reasoning_focus', '')}"
        )
    return (
        f"Title: {side.get('title', card.get('role_card_id'))}\n"
        f"Role prompt: {side.get('narrative', side.get('role_prompt', ''))}\n"
        f"Daily context: {side.get('daily_context', '')}\n"
        f"Preference cues: {side.get('preference_cues', side.get('comfort_and_devices', ''))}\n"
        f"Acceptance anchor: {baseline_pct}. {side.get('decision_anchor', side.get('baseline_acceptance_instruction', ''))}\n"
        f"How to adjust judgment: {side.get('adjustment_prompt', '')}\n"
        f"Reasoning focus: {side.get('reasoning_focus', '')}"
    )


def _accepted_outcome_zh(event: dict[str, Any], gate: dict[str, Any]) -> str:
    actual = _fmt_num(event.get("actual_kwh"), 2)
    shed = _fmt_num(event.get("actual_shed_kwh"), 2)
    if gate.get("accepted") is False:
        return (
            "如果接受：系统会执行上面的 VPP 策略。这个样本的完整实验真实执行分支是“拒绝后回退”，"
            "因此接受分支没有单独重复仿真；请主要根据策略文本判断如果接受会不会符合角色需求。"
        )
    return (
        f"如果接受：系统执行该策略。本样本真实执行了接受分支；VPP 窗口实际用电约 {actual} kWh，估计削减约 {shed} kWh。"
        f"结果提示：{_risk_summary_zh(event, gate)} 请按照角色卡判断这个结果是否值得满意。"
    )


def _accepted_outcome_en(event: dict[str, Any], gate: dict[str, Any]) -> str:
    actual = _fmt_num(event.get("actual_kwh"), 2)
    shed = _fmt_num(event.get("actual_shed_kwh"), 2)
    if gate.get("accepted") is False:
        return (
            "If accepted: the system would execute the VPP strategy above. In the full experiment, this sample's realized branch "
            "was rejection and fallback, so the accepted branch was not separately re-simulated. Judge mainly from whether the "
            "strategy text would fit the role."
        )
    return (
        f"If accepted: the system executes this strategy. This sample realized the accepted branch; actual VPP-window electricity is about {actual} kWh, "
        f"with estimated shed about {shed} kWh. Result note: {_risk_summary_en(event, gate)} "
        f"Please judge satisfaction from the role card."
    )


def _rejected_outcome_zh(event: dict[str, Any], gate: dict[str, Any]) -> str:
    default_plan = gate.get("default_plan") or {}
    fallback = "用户拒绝后，系统回到普通日计划或用户手动舒适习惯；这可能导致 VPP 窗口仍有部分家电或舒适负荷。"
    if default_plan.get("setpoint") is not None:
        fallback += f" 普通计划空调约 {_fmt_num(default_plan.get('setpoint'))}°C。"
    if gate.get("accepted") is False:
        actual = _fmt_num(event.get("actual_kwh"), 2)
        shed = _fmt_num(event.get("actual_shed_kwh"), 2)
        fallback += f" 本样本真实执行了拒绝/回退分支；VPP 窗口实际用电约 {actual} kWh，估计削减约 {shed} kWh。"
    return f"如果拒绝：{fallback} 被试应按角色卡判断：保留控制权带来的安心感，是否足以抵消电费/VPP 配合的下降。"


def _rejected_outcome_en(event: dict[str, Any], gate: dict[str, Any]) -> str:
    default_plan = gate.get("default_plan") or {}
    fallback = "If rejected, the system falls back to the ordinary daily plan or a manual comfort routine; some appliance or comfort load may still occur during the VPP window."
    if default_plan.get("setpoint") is not None:
        fallback += f" Ordinary-plan AC target is about {_fmt_num(default_plan.get('setpoint'))}°C."
    if gate.get("accepted") is False:
        actual = _fmt_num(event.get("actual_kwh"), 2)
        shed = _fmt_num(event.get("actual_shed_kwh"), 2)
        fallback += f" This sample realized the rejection/fallback branch; actual VPP-window electricity is about {actual} kWh, with estimated shed about {shed} kWh."
    return f"{fallback} Participants should judge from the role card whether keeping user control is worth the lower cost/VPP cooperation."


def _load_rows(summary_paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in summary_paths:
        for row in _read_json(path):
            row = dict(row)
            row["_summary_json"] = str(path)
            rows.append(row)
    return rows


def build_household_cases(summary_paths: list[Path], *, max_cases: int, methods: list[str]) -> list[dict[str, Any]]:
    rows = _load_rows(summary_paths)
    role_cards = _load_role_cards()
    candidates: list[dict[str, Any]] = []
    method_set = set(methods)
    for row in rows:
        if str(row.get("method")) not in method_set:
            continue
        result_path = Path(row.get("output_dir", "")) / "benchmark_result.json"
        if not result_path.exists():
            continue
        raw = _read_json(result_path)
        events = raw.get("vpp_event_log") or []
        gates = {str(g.get("event_id")): g for g in (raw.get("vpp_plan_gate_events") or [])}
        for event in events:
            gate = event.get("vpp_acceptance_gate") or gates.get(str(event.get("id"))) or {}
            if not gate:
                continue
            household_id = row.get("household_id") or row.get("persona_id")
            case_id = (
                f"{row.get('city','city').lower()}__{household_id}__"
                f"{row.get('method')}__day{int(event.get('day') or 1)}__{event.get('id')}"
            )
            role_card_id = ROLE_CARD_BY_HOUSEHOLD.get(str(household_id), "role_c_irregular_cautious")
            role_card = role_cards.get(role_card_id, {})
            household, household_json_path = _load_household_config(household_id)
            source_method = row.get("method")
            presentation_style = _presentation_style(source_method)
            candidates.append(
                {
                    "case_id": case_id,
                    "role_card_id": role_card_id,
                    "role_baseline_acceptance_probability": role_card.get("generic_vpp_baseline_acceptance"),
                    "source_user_json_path": household_json_path,
                    "user_profile_text_zh": _household_profile_text(household, role_card, lang="zh") if household else _role_card_text(role_card, "zh"),
                    "user_profile_text_en": _household_profile_text(household, role_card, lang="en") if household else _role_card_text(role_card, "en"),
                    "survey_visible_method": "",
                    "source_method": source_method,
                    "source_method_label": METHOD_LABEL.get(str(source_method), str(source_method)),
                    "strategy_presentation_style": presentation_style,
                    "city": row.get("city"),
                    "household_id": household_id,
                    "day": int(event.get("day") or 1),
                    "event_id": event.get("id"),
                    "vpp_start": _fmt_time(event.get("trigger_h")),
                    "vpp_end": _fmt_time(event.get("end_h")),
                    "event_context_zh": _event_context_zh(raw, row, event),
                    "event_context_en": _event_context_en(raw, row, event),
                    "strategy_text_zh": _strategy_text_zh(event, gate, method=source_method),
                    "strategy_text_en": _strategy_text_en(event, gate, method=source_method),
                    "accepted_outcome_zh": _accepted_outcome_zh(event, gate),
                    "accepted_outcome_en": _accepted_outcome_en(event, gate),
                    "rejected_outcome_zh": _rejected_outcome_zh(event, gate),
                    "rejected_outcome_en": _rejected_outcome_en(event, gate),
                    "reference_gate_acceptance_probability": gate.get("acceptance_probability"),
                    "reference_gate_accepted": gate.get("accepted"),
                    "reference_stable_draw": gate.get("stable_draw"),
                    "reference_user_score": event.get("score"),
                    "reference_comfort_score": event.get("comfort_score"),
                    "reference_energy_score": event.get("energy_score"),
                    "reference_vpp_score": event.get("vpp_score"),
                    "actual_vpp_kwh": event.get("actual_kwh"),
                    "actual_shed_kwh": event.get("actual_shed_kwh"),
                    "primary_reference_feedback": _clean_text(
                        event.get("controller_feedback") or event.get("member_feedback_summary") or event.get("comment"),
                        max_len=1200,
                    ),
                    "source_summary_json": row.get("_summary_json"),
                    "source_benchmark_result_json": str(result_path),
                    "researcher_notes": "Do not show source_method to participant unless testing method bias.",
                }
            )
    # Stratified deterministic sample: greedily cover roles, methods, and accepted/rejected cases.
    chosen: list[dict[str, Any]] = []
    seen_case_ids: set[str] = set()
    role_order = list(dict.fromkeys(ROLE_CARD_BY_HOUSEHOLD.values()))
    accepted_order = [True, False]
    buckets: dict[tuple[str, str, bool, str], list[dict[str, Any]]] = {}
    for item in candidates:
        key = (
            str(item["source_method"]),
            str(item["role_card_id"]),
            bool(item["reference_gate_accepted"]),
            str(item["city"]),
        )
        buckets.setdefault(key, []).append(item)
    available_keys = list(buckets)
    role_counts: Counter[str] = Counter()
    method_counts: Counter[str] = Counter()
    accepted_counts: Counter[bool] = Counter()
    city_counts: Counter[str] = Counter()
    role_index = {role: idx for idx, role in enumerate(role_order)}
    method_index = {method: idx for idx, method in enumerate(methods)}
    while len(chosen) < max_cases and available_keys:
        available_keys.sort(
            key=lambda key: (
                role_counts[key[1]],
                method_counts[key[0]],
                accepted_counts[key[2]],
                city_counts[key[3]],
                role_index.get(key[1], 999),
                method_index.get(key[0], 999),
                0 if key[2] is False else 1,
                key[3],
            )
        )
        key = available_keys.pop(0)
        bucket = buckets.get(key) or []
        item = next((x for x in bucket if x["case_id"] not in seen_case_ids), None)
        if item is None:
            continue
        chosen.append(item)
        seen_case_ids.add(str(item["case_id"]))
        method, role, accepted, city = key
        method_counts[method] += 1
        role_counts[role] += 1
        accepted_counts[accepted] += 1
        city_counts[city] += 1
    if len(chosen) < max_cases:
        for item in candidates:
            if item["case_id"] in seen_case_ids:
                continue
            chosen.append(item)
            seen_case_ids.add(str(item["case_id"]))
            if len(chosen) >= max_cases:
                break
    chosen = chosen[:max_cases]
    for idx, item in enumerate(chosen, start=1):
        item["participant_case_id"] = f"VPP-{idx:03d}"
    return chosen


def _persona_result_rows(
    results_dir: Path,
    *,
    hema_results_dir: Path,
    methods: list[str],
    cities: list[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for persona_alias, persona_id in BASIC_PERSONAS.items():
        for city in cities:
            for method in methods:
                template = PERSONA_METHOD_DIR.get(method)
                if template is None:
                    missing.append(
                        {
                            "persona_alias": persona_alias,
                            "persona_id": persona_id,
                            "city": city,
                            "method": method,
                            "reason": "unknown_method_template",
                        }
                    )
                    continue
                root = hema_results_dir if method == "hema_agent" else results_dir
                output_dir = root / template.format(role=persona_alias, city=city)
                result_path = output_dir / "benchmark_result.json"
                if not result_path.exists():
                    missing.append(
                        {
                            "persona_alias": persona_alias,
                            "persona_id": persona_id,
                            "city": city,
                            "method": method,
                            "expected_result_json": str(result_path),
                            "reason": "missing_result_json",
                        }
                    )
                    continue
                rows.append(
                    {
                        "profile_mode": "persona",
                        "persona_alias": persona_alias,
                        "persona_id": persona_id,
                        "city": city,
                        "method": method,
                        "output_dir": str(output_dir),
                    }
                )
    return rows, missing


def _event_records(raw: dict[str, Any], *, events_per_run: int | None) -> list[dict[str, Any]]:
    events = [event for event in (raw.get("vpp_event_log") or []) if isinstance(event, dict)]
    if events_per_run is None:
        return events
    return events[: max(0, events_per_run)]


def build_persona_cases(
    results_dir: Path,
    *,
    hema_results_dir: Path,
    max_cases: int | None,
    methods: list[str],
    cities: list[str],
    events_per_run: int | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rows, missing = _persona_result_rows(
        results_dir,
        hema_results_dir=hema_results_dir,
        methods=methods,
        cities=cities,
    )
    role_cards = _load_role_cards()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        result_path = Path(row.get("output_dir", "")) / "benchmark_result.json"
        raw = _read_json(result_path)
        gates = {str(g.get("event_id")): g for g in (raw.get("vpp_plan_gate_events") or []) if isinstance(g, dict)}
        persona, persona_json_path = _load_persona_config(str(row["persona_alias"]))
        role_card_id = ROLE_CARD_BY_PERSONA.get(str(row["persona_id"]), "")
        role_card = role_cards.get(role_card_id, {})
        try:
            baseline_accept = max(0.0, min(1.0, float(role_card.get("generic_vpp_baseline_acceptance"))))
        except (TypeError, ValueError):
            baseline_accept = None
        for event in _event_records(raw, events_per_run=events_per_run):
            gate = event.get("vpp_acceptance_gate") or gates.get(str(event.get("id"))) or {}
            if not isinstance(gate, dict):
                gate = {}
            case_id = (
                f"{row['city']}__{row['persona_alias']}__"
                f"{row['method']}__day{int(event.get('day') or 1)}__{event.get('id')}"
            )
            source_method = row["method"]
            candidates.append(
                {
                    "case_id": case_id,
                    "profile_mode": "persona",
                    "role_card_id": role_card_id,
                    "persona_alias": row["persona_alias"],
                    "role_baseline_acceptance_probability": baseline_accept,
                    "source_user_json_path": persona_json_path,
                    "user_profile_text_zh": _persona_profile_text(persona, role_card, lang="zh") if persona else "",
                    "user_profile_text_en": _persona_profile_text(persona, role_card, lang="en") if persona else "",
                    "survey_visible_method": "",
                    "source_method": source_method,
                    "source_method_label": METHOD_LABEL.get(str(source_method), str(source_method)),
                    "source_method_hint": METHOD_SOURCE_HINT.get(str(source_method), ""),
                    "strategy_presentation_style": _presentation_style(source_method),
                    "city": row["city"],
                    "household_id": "",
                    "persona_id": row["persona_id"],
                    "day": int(event.get("day") or 1),
                    "event_id": event.get("id"),
                    "vpp_start": _fmt_time(event.get("trigger_h")),
                    "vpp_end": _fmt_time(event.get("end_h")),
                    "event_context_zh": _event_context_zh(raw, row, event),
                    "event_context_en": _event_context_en(raw, row, event),
                    "strategy_text_zh": _strategy_text_zh(event, gate, method=source_method),
                    "strategy_text_en": _strategy_text_en(event, gate, method=source_method),
                    "accepted_outcome_zh": _accepted_outcome_zh(event, gate),
                    "accepted_outcome_en": _accepted_outcome_en(event, gate),
                    "rejected_outcome_zh": _rejected_outcome_zh(event, gate),
                    "rejected_outcome_en": _rejected_outcome_en(event, gate),
                    "reference_gate_acceptance_probability": gate.get("acceptance_probability"),
                    "reference_gate_accepted": gate.get("accepted"),
                    "reference_stable_draw": gate.get("stable_draw"),
                    "reference_user_score": event.get("score"),
                    "reference_comfort_score": event.get("comfort_score"),
                    "reference_energy_score": event.get("energy_score"),
                    "reference_vpp_score": event.get("vpp_score"),
                    "actual_vpp_kwh": event.get("actual_kwh"),
                    "actual_shed_kwh": event.get("actual_shed_kwh"),
                    "primary_reference_feedback": _clean_text(
                        event.get("controller_feedback") or event.get("member_feedback_summary") or event.get("comment"),
                        max_len=1200,
                    ),
                    "source_summary_json": "",
                    "source_benchmark_result_json": str(result_path),
                    "researcher_notes": "Do not show source_method to participant unless testing method bias. This persona case uses a real generated single-user benchmark event.",
                }
            )
    method_index = {method: idx for idx, method in enumerate(methods)}
    persona_index = {persona: idx for idx, persona in enumerate(BASIC_PERSONAS)}
    city_index = {city: idx for idx, city in enumerate(cities)}
    candidates.sort(
        key=lambda item: (
            persona_index.get(str(item.get("persona_alias")), 999),
            city_index.get(str(item.get("city")), 999),
            method_index.get(str(item.get("source_method")), 999),
            int(item.get("day") or 1),
            str(item.get("event_id") or ""),
        )
    )
    if max_cases is not None:
        candidates = candidates[:max_cases]
    for idx, item in enumerate(candidates, start=1):
        item["participant_case_id"] = f"VPP-{idx:03d}"
    return candidates, missing


def write_outputs(cases: list[dict[str, Any]], out_dir: Path, *, missing_sources: list[dict[str, Any]] | None = None) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cleaned_vpp_survey_cases.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if cases:
        with (out_dir / "cleaned_vpp_survey_cases.csv").open("w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(cases[0].keys()))
            writer.writeheader()
            writer.writerows(cases)
    zh = ["# VPP 真人问卷案例样本\n"]
    en = ["# Sample VPP Human Survey Cases\n"]
    zh_stage1 = ["# VPP 真人问卷：第一阶段（策略判断）\n"]
    en_stage1 = ["# VPP Human Survey: Stage 1 (Strategy Judgment)\n"]
    zh_outcomes = ["# VPP 真人问卷：第二阶段结果分支（RA 使用）\n\n请只展示与参与者第一阶段选择相符的一个结果。\n"]
    en_outcomes = ["# VPP Human Survey: Stage 2 Outcome Branches (RA Use)\n\nShow only the outcome matching the participant's Stage 1 choice.\n"]
    previous_role_card_id = None
    for idx, case in enumerate(cases, start=1):
        profile_mode = str(case.get("profile_mode") or "household")
        profile_label_zh = "用户画像" if profile_mode == "persona" else "用户/家庭配置"
        profile_label_en = "User persona" if profile_mode == "persona" else "User/household profile"
        profile_id = case.get("role_card_id") or case.get("persona_id") or case.get("household_id")
        if profile_id != previous_role_card_id:
            zh_stage1 += [
                f"\n## 角色卡：`{profile_id}`\n",
                f"\n{case.get('user_profile_text_zh', '')}\n",
                f"\n共同情境：{case['event_context_zh']}\n",
            ]
            en_stage1 += [
                f"\n## Role Card: `{profile_id}`\n",
                f"\n{case.get('user_profile_text_en', '')}\n",
                f"\nShared context: {case['event_context_en']}\n",
            ]
            previous_role_card_id = profile_id
        zh_stage1 += [
            f"\n### 案例 {case['participant_case_id']}\n",
            f"策略建议：{case['strategy_text_zh']}\n",
            (
                "\n请回答：① 0-100 接受概率和接受/拒绝；② 最多 3 个关键因素及 1-3 句话理由；"
                "③ 策略说明帮助程度 1-5。作答后再向 RA 获取对应结果。\n"
            ),
        ]
        en_stage1 += [
            f"\n### Case {case['participant_case_id']}\n",
            f"Strategy suggestion: {case['strategy_text_en']}\n",
            (
                "\nAnswer: (1) 0-100 acceptance probability and accept/reject; (2) up to three key factors "
                "and a 1-3 sentence reason; (3) explanation helpfulness from 1-5. Ask the RA for the matching outcome afterward.\n"
            ),
        ]
        zh_outcomes += [
            f"\n## {case['participant_case_id']}\n",
            f"接受后结果：{case['accepted_outcome_zh']}\n",
            f"\n拒绝后结果：{case['rejected_outcome_zh']}\n",
            "\n展示一个分支后，请收集 1-5 满意度及 1-3 句话结果反馈。\n",
        ]
        en_outcomes += [
            f"\n## {case['participant_case_id']}\n",
            f"Accepted outcome: {case['accepted_outcome_en']}\n",
            f"\nRejected outcome: {case['rejected_outcome_en']}\n",
            "\nAfter showing one branch, collect 1-5 satisfaction and 1-3 sentences of outcome feedback.\n",
        ]
        zh += [
            f"\n## 案例 {idx}: {case['participant_case_id']}\n",
            f"{profile_label_zh} ID：`{profile_id}`\n",
            f"\n### {profile_label_zh}简介\n\n{case.get('user_profile_text_zh', '')}\n",
            f"情境：{case['event_context_zh']}\n",
            f"策略建议：{case['strategy_text_zh']}\n",
            (
                "\n第一阶段（3 题）：给出 0-100 的最终接受概率，并选择接受或拒绝；选择最多 3 个"
                "关键因素，用 1-3 句话说明为什么该策略使你偏离角色卡的基准倾向；最后对策略说明"
                "的帮助程度打 1-5 分。\n"
            ),
            f"\n接受后结果：{case['accepted_outcome_zh']}\n",
            f"\n拒绝后结果：{case['rejected_outcome_zh']}\n",
            "\n第二阶段（2 题）：只阅读与你选择相符的结果，给出 1-5 满意度评分；用 1-3 句话说明原因，不满意时写出下一次最应改的一点。\n",
        ]
        en += [
            f"\n## Case {idx}: {case['participant_case_id']}\n",
            f"{profile_label_en} ID: `{profile_id}`\n",
            f"\n### {profile_label_en} Profile\n\n{case.get('user_profile_text_en', '')}\n",
            f"Context: {case['event_context_en']}\n",
            f"Strategy suggestion: {case['strategy_text_en']}\n",
            (
                "\nStage 1 (3 items): Give a 0-100 final acceptance probability and choose accept or reject; "
                "select up to three key factors and explain in 1-3 sentences why the strategy moved you away from "
                "the role-card baseline; then rate explanation helpfulness from 1-5.\n"
            ),
            f"\nAccepted outcome: {case['accepted_outcome_en']}\n",
            f"\nRejected outcome: {case['rejected_outcome_en']}\n",
            "\nStage 2 (2 items): Read only the outcome matching your choice, rate satisfaction from 1-5, and explain the score in 1-3 sentences; if dissatisfied, include the most important change for next time.\n",
        ]
    (out_dir / "survey_cases_readable_zh.md").write_text("".join(zh), encoding="utf-8")
    (out_dir / "survey_cases_readable_en.md").write_text("".join(en), encoding="utf-8")
    (out_dir / "survey_stage1_zh.md").write_text("".join(zh_stage1), encoding="utf-8")
    (out_dir / "survey_stage1_en.md").write_text("".join(en_stage1), encoding="utf-8")
    (out_dir / "survey_stage2_outcomes_zh.md").write_text("".join(zh_outcomes), encoding="utf-8")
    (out_dir / "survey_stage2_outcomes_en.md").write_text("".join(en_outcomes), encoding="utf-8")
    summary = {
        "case_count": len(cases),
        "case_ids": [c["case_id"] for c in cases],
        "methods_in_researcher_data": sorted({str(c["source_method"]) for c in cases}),
        "role_cards": sorted({str(c["role_card_id"]) for c in cases}),
        "profile_modes": sorted({str(c.get("profile_mode") or "household") for c in cases}),
        "cities": sorted({str(c.get("city")) for c in cases}),
        "missing_source_count": len(missing_sources or []),
        "missing_sources": missing_sources or [],
        "note": "Method labels are for researcher analysis only and should not be shown to participants in the main calibration study.",
    }
    (out_dir / "generation_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build human-readable VPP survey cases.")
    parser.add_argument(
        "--profile-mode",
        choices=["persona", "household"],
        default="persona",
        help="persona builds the 6 basic single-user role cards; household keeps the older household case generator.",
    )
    parser.add_argument(
        "--summary-json",
        nargs="*",
        default=[
            str(DEFAULT_MAIN_DIR / "household_matrix_summary_tianjin_7days_H6_cap76_merged.json"),
            str(DEFAULT_MAIN_DIR / "household_matrix_summary_germany_7days_H6_cap76_merged.json"),
        ],
        help="Household-mode matrix summary JSON files to read.",
    )
    parser.add_argument(
        "--persona-results-dir",
        default=str(DEFAULT_PERSONA_RESULTS_DIR),
        help="Persona-mode benchmark result root.",
    )
    parser.add_argument(
        "--hema-results-dir",
        default=str(DEFAULT_HEMA_PERSONA_RESULTS_DIR),
        help="Persona-mode HEMA benchmark result root. HEMA is stored separately from the original persona matrix.",
    )
    parser.add_argument(
        "--cities",
        nargs="*",
        default=["tianjin"],
        help="Persona-mode cities to include.",
    )
    parser.add_argument(
        "--events-per-run",
        default="1",
        help="Persona-mode number of VPP events per persona/city/method run. Use 'all' for all events.",
    )
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    parser.add_argument(
        "--max-cases",
        type=int,
        default=0,
        help="Maximum cases to write. Use 0 to keep all selected cases.",
    )
    parser.add_argument(
        "--methods",
        nargs="*",
        default=None,
        help="Researcher-side source methods to sample. Method names are not shown to participants.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    max_cases = None if int(args.max_cases) <= 0 else int(args.max_cases)
    if args.events_per_run == "all":
        events_per_run = None
    else:
        events_per_run = int(args.events_per_run)
    if args.profile_mode == "persona":
        methods = list(args.methods or DEFAULT_PERSONA_METHODS)
        cases, missing = build_persona_cases(
            Path(args.persona_results_dir).expanduser().resolve(),
            hema_results_dir=Path(args.hema_results_dir).expanduser().resolve(),
            max_cases=max_cases,
            methods=methods,
            cities=[str(city).lower() for city in args.cities],
            events_per_run=events_per_run,
        )
    else:
        methods = list(args.methods or DEFAULT_HOUSEHOLD_METHODS)
        summary_paths = [Path(p).expanduser().resolve() for p in args.summary_json]
        cases = build_household_cases(summary_paths, max_cases=max_cases or 20, methods=methods)
        missing = []
    write_outputs(cases, Path(args.out_dir).expanduser().resolve(), missing_sources=missing)
    print(f"Wrote {len(cases)} cases to {Path(args.out_dir).expanduser().resolve()}")
    if missing:
        print(f"Skipped {len(missing)} missing source combinations; see generation_summary.json.")


if __name__ == "__main__":
    main()
