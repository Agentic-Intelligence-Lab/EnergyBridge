"""User preference scorer using LLM roleplay evaluator (fallback: rule-based).

Scoring dimensions (M1):
  comfort_score  — thermal comfort satisfaction (1-5)
  energy_score   — energy usage / cost satisfaction (1-5)
  vpp_score      — VPP demand-response handling satisfaction (1-5)
  satisfaction_score — overall (1-5, backward compat)

M2 — zone_group_scores: office only, per-group comfort scores {Core, Bottom, Middle, Top}.
L3 — past_events are passed to roleplay user so Day 3 agent sees Day 1+2 feedback.
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
    event_index: 1-based. past_events: list of scored events from prior VPP windows.
    L3: past_events from previous days are included so user can express learned preferences.
    """
    persona = OFFICE_PERSONA if building == "office" else FAMILY_PERSONA
    # L3: summarize past event scores as learning context
    learned_ctx = ""
    if past_events:
        summaries = [{"event": e["id"], "score": e.get("score"),
                      "comfort": e.get("comfort_score"), "energy": e.get("energy_score"),
                      "vpp": e.get("vpp_score"), "comment": e.get("comment","")[:60]}
                     for e in past_events]
        learned_ctx = f"Past VPP experiences: {summaries}"

    scenario = {
        "event_type": "VPP demand response",
        "event_index": event_index,
        "vpp_context": vpp_context,
        "learned_from_past": learned_ctx,
        "question": "How do you feel about the upcoming demand response event? What matters most to you today?",
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
        return persona.get("summary", "")


def score_user_preference(building: str, method: str, mean_temp_c: float,
                           pmv_ok_fraction: float, energy_kwh_per_day: float,
                           agent_setpoint_c=None, event_index: int = 1,
                           user_preference_text: str = "",
                           agent_reason: str = "",
                           zone_group_temps: dict | None = None):
    """Score user satisfaction using roleplay LLM (fallback: rule-based).

    Returns dict with: satisfaction_score, comfort_score, energy_score, vpp_score,
    satisfaction_label, comment, source.
    M2: zone_group_temps = {group: mean_temp} for office zone-aware scoring.
    """
    persona = OFFICE_PERSONA if building == "office" else FAMILY_PERSONA
    home_state = {
        "indoor_temp": round(mean_temp_c, 1),
        "hvac_setpoint": agent_setpoint_c or round(mean_temp_c, 1),
        "energy_per_day_kwh": round(energy_kwh_per_day, 2),
    }
    if method in ("agent", "agent_pmv") and agent_setpoint_c:
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

    # M2: build zone group context for office
    zone_ctx = None
    if zone_group_temps and building == "office":
        zone_ctx = {g: {"mean_temp_c": round(t, 1)} for g, t in zone_group_temps.items()}

    try:
        from energybridge.utils.config import load_llm_config
        if not load_llm_config(use_key="USE_LLM").use_llm:
            raise RuntimeError("LLM off")
        from energybridge.llm.roleplay_user import RoleplayUserSimulator
        r = RoleplayUserSimulator().generate_feedback(
            persona=persona,
            turn_index=event_index,
            selected_strategy=home_state,
            projected_control_plan=control_plan,
            projected_safety_report=safety,
            zone_group_context=zone_ctx,
        )
        fb = r.get("data", {})
        return {
            "score":          _int_score(fb.get("satisfaction_score"), 3),
            "comfort_score":  _int_score(fb.get("comfort_score"), 3),
            "energy_score":   _int_score(fb.get("energy_score"), 3),
            "vpp_score":      _int_score(fb.get("vpp_score"), 3),
            "label":          fb.get("satisfaction_label", "neutral"),
            "comment":        fb.get("comment", ""),
            "zone_comfort_scores": fb.get("zone_comfort_scores"),  # M2
            "source": "roleplay_llm",
        }
    except Exception as e:
        result = _rule_score(persona, mean_temp_c, pmv_ok_fraction, energy_kwh_per_day, zone_group_temps)
        result["source"] = "rule_based_fallback"
        return result


def _int_score(val, default=3):
    """Coerce LLM score to integer 1-5."""
    try:
        return max(1, min(5, int(round(float(val)))))
    except (TypeError, ValueError):
        return default


def _rule_score(persona, mean_t, pmv_ok, e_per_day, zone_group_temps=None):
    p = persona.get("stable_preferences", {})
    t_lo = p.get("preferred_temp_min", 22.0); t_hi = p.get("preferred_temp_max", 26.0)
    cw = p.get("comfort_priority", 0.6); ew = p.get("cost_priority", 0.3)
    gw = p.get("grid_priority", 0.1)

    # comfort_score
    if t_lo <= mean_t <= t_hi: cs = 5.0
    elif mean_t < t_lo: cs = max(1.0, 5.0 - (t_lo - mean_t) * 1.5)
    else: cs = max(1.0, 5.0 - (mean_t - t_hi) * 1.5)
    cs = min(5.0, cs + pmv_ok * 0.5)
    comfort_score = max(1, min(5, round(cs)))

    # energy_score
    if e_per_day < 30: es = 5
    elif e_per_day < 50: es = 4
    elif e_per_day < 100: es = 3
    elif e_per_day < 200: es = 2
    else: es = 1
    energy_score = es

    # vpp_score — rule-based: assume moderate compliance
    vpp_score = 3

    # overall
    total = round(min(5.0, max(1.0, cw * cs + ew * es + gw * 3.0)), 2)
    labels = {5: "very_satisfied", 4: "satisfied", 3: "neutral",
              2: "dissatisfied", 1: "very_dissatisfied"}
    overall = max(1, min(5, round(total)))

    # M2: zone group scores from temperatures
    zone_scores = None
    if zone_group_temps:
        zone_scores = {}
        for g, t in zone_group_temps.items():
            if t_lo <= t <= t_hi: zs = 5
            elif t < t_lo: zs = max(1, round(5.0 - (t_lo - t) * 1.5))
            else: zs = max(1, round(5.0 - (t - t_hi) * 1.5))
            zone_scores[g] = zs

    return {
        "score": overall, "comfort_score": comfort_score,
        "energy_score": energy_score, "vpp_score": vpp_score,
        "label": labels.get(overall, "neutral"),
        "comment": f"(rule) temp={mean_t:.1f}C pmv_ok={pmv_ok*100:.0f}% e={e_per_day:.0f}kWh/day",
        "zone_comfort_scores": zone_scores,
    }
