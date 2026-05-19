"""User preference scorer using LLM roleplay evaluator (fallback: rule-based).

Two modes:
  PMV loop  : score_user_preference() -- roleplay LLM scores AFTER the fact
  Agent loop: get_user_preference_input() -> agent acts -> score_user_preference()
              True user-in-the-loop: preference injected BEFORE agent decision.
"""
from __future__ import annotations
import sys
from pathlib import Path
PROJECT_ROOT = Path("/home/ha_agent/work/EnergyBridge")
if str(PROJECT_ROOT) not in sys.path: sys.path.insert(0, str(PROJECT_ROOT))

OFFICE_PERSONA = {"display_name":"办公室用户",
    "summary":"在办公室工作，注重热舒适，希望在舒适的前提下节省能源和配合电网需求。",
    "speaking_language":"zh-cn",
    "stable_preferences":{"comfort_priority":0.6,"cost_priority":0.3,"grid_priority":0.1,
        "preferred_temp_min":22.0,"preferred_temp_max":26.0,
        "allow_pre_cooling":True,"allow_temp_drift":True}}
FAMILY_PERSONA = {"display_name":"家庭用户",
    "summary":"在家居住，希望保持基本舒适并配合电网削峰，对温度有一定容忍度。",
    "speaking_language":"zh-cn",
    "stable_preferences":{"comfort_priority":0.5,"cost_priority":0.2,"grid_priority":0.3,
        "preferred_temp_min":24.0,"preferred_temp_max":26.0,
        "allow_pre_cooling":True,"allow_temp_drift":True}}


def get_user_preference_input(building: str, event_index: int,
                               vpp_context: dict, past_events: list) -> str:
    """Get roleplay user preference BEFORE agent acts on a VPP event.

    Returns user preference statement (str) to inject into agent prompt.
    event_index: 1-based (1=first event, 2=second, 3=third).
    past_events: list of {"id", "score", "comment"} from prior events.
    """
    persona = OFFICE_PERSONA if building == "office" else FAMILY_PERSONA
    scenario = {
        "event_type": "VPP demand response",
        "event_index": event_index,
        "vpp_context": vpp_context,
        "question": "How do you feel about the upcoming demand response event? What matters most to you?",
    }
    memory_snapshot = {"past_vpp_events": past_events[-2:]} if past_events else {}
    history_summary = [{"event": e["id"], "score": e.get("score"),
                        "my_comment": e.get("comment", "")[:60]}
                       for e in (past_events or [])]
    try:
        from energybridge.utils.config import load_llm_config
        if not load_llm_config(use_key="USE_LLM").use_llm:
            raise RuntimeError("LLM off")
        from energybridge.llm.roleplay_user import RoleplayUserSimulator
        r = RoleplayUserSimulator().generate_user_input(
            persona=persona,
            turn_index=event_index,
            scenario=scenario,
            memory_snapshot=memory_snapshot,
            history_summary=history_summary,
        )
        pref = r.get("data", {}).get("user_input", "")
        print(f"  [RoleplayUser event={event_index}] preference: {pref[:80]}")
        return pref
    except Exception as e:
        print(f"  [RoleplayUser] get_preference failed: {e}")
        # Fallback: return persona summary as preference
        return persona.get("summary", "")


def score_user_preference(building: str, method: str, mean_temp_c: float,
                           pmv_ok_fraction: float, energy_kwh_per_day: float,
                           agent_setpoint_c=None, event_index: int = 1,
                           user_preference_text: str = "",
                           agent_reason: str = ""):
    """Score user satisfaction using roleplay LLM (fallback: rule-based).

    For PMV: called after VPP window, no prior preference injection.
    For Agent: called after VPP window; user_preference_text is what the roleplay
               user expressed BEFORE the agent acted.
    event_index: 1-based, used as turn_index so roleplay user can track learning.
    """
    persona = OFFICE_PERSONA if building == "office" else FAMILY_PERSONA
    home_state = {
        "indoor_temp": round(mean_temp_c, 1),
        "hvac_setpoint": agent_setpoint_c or round(mean_temp_c, 1),
        "energy_per_day_kwh": round(energy_kwh_per_day, 2),
    }
    if method == "agent" and agent_setpoint_c:
        rationale = f"LLM agent set cooling setpoint to {agent_setpoint_c}C during VPP DR event."
        if agent_reason:
            rationale += f" Agent reason: {agent_reason[:100]}"
        if user_preference_text:
            rationale += f" | User had expressed: {user_preference_text[:80]}"
        control_plan = {"action": "set_hvac_temperature", "setpoint": agent_setpoint_c,
                        "rationale": rationale}
    else:
        control_plan = {
            "action": "set_hvac_temperature",
            "setpoint": round(mean_temp_c, 1),
            "rationale": (f"PMV reactive controller: mean_temp={mean_temp_c:.1f}C, "
                          f"pmv_comfort_ok={pmv_ok_fraction*100:.0f}%. No VPP adaptation."),
        }
    safety = {"status": "approved", "reason": "Within safe operation bounds."}
    try:
        from energybridge.utils.config import load_llm_config
        if not load_llm_config(use_key="USE_LLM").use_llm:
            raise RuntimeError("LLM off")
        from energybridge.llm.roleplay_user import RoleplayUserSimulator
        r = RoleplayUserSimulator().generate_feedback(
            persona=persona,
            turn_index=event_index,          # track event progression
            selected_strategy=home_state,
            projected_control_plan=control_plan,
            projected_safety_report=safety,
        )
        fb = r.get("data", {})
        return {
            "score": fb.get("satisfaction_score"),
            "label": fb.get("satisfaction_label", "?"),
            "comment": fb.get("comment", ""),
            "source": "roleplay_llm",
        }
    except Exception as e:
        result = _rule_score(persona, mean_temp_c, pmv_ok_fraction, energy_kwh_per_day)
        result["source"] = "rule_based_fallback"
        return result


def _rule_score(persona, mean_t, pmv_ok, e_per_day):
    p = persona.get("stable_preferences", {})
    t_lo = p.get("preferred_temp_min", 22.0); t_hi = p.get("preferred_temp_max", 26.0)
    cw = p.get("comfort_priority", 0.6); ew = p.get("cost_priority", 0.3)
    if t_lo <= mean_t <= t_hi: cs = 5.0
    elif mean_t < t_lo: cs = max(1.0, 5.0 - (t_lo - mean_t) * 1.5)
    else: cs = max(1.0, 5.0 - (mean_t - t_hi) * 1.5)
    cs += pmv_ok * 0.5
    es = 5.0 if e_per_day < 50 else (4.0 if e_per_day < 100 else (3.0 if e_per_day < 200 else 2.0))
    total = round(min(5.0, max(1.0, cw * cs + ew * es)), 2)
    labels = {5: "very satisfied", 4: "satisfied", 3: "neutral",
              2: "dissatisfied", 1: "very dissatisfied"}
    return {"score": total, "label": labels.get(round(total), "neutral"),
            "comment": f"(rule-based) temp={mean_t:.1f}C pmv_ok={pmv_ok*100:.0f}%"}
