"""User preference scorer using LLM roleplay evaluator (fallback: rule-based).

Scoring dimensions (M1):
  comfort_score  — thermal comfort satisfaction (1-5)
  energy_score   — energy usage / cost satisfaction (1-5)
  vpp_score      — VPP demand-response handling satisfaction (1-5)
  satisfaction_score — overall weighted sum (1-5, backward compat)

Enhancement (persona-aware):
  - Accepts persona dict from personas.py for differentiated scoring weights
  - Prompts written in English; persona dialogue style injected
  - Dialogue log written to logs/dialogue_{building}_{persona}_{city}_{method}.jsonl
  - VPP override: comfort_sensitive persona may express strong discomfort pre-event

M2 — zone_group_scores: office only, per-group comfort scores {Core, Bottom, Middle, Top}.
L3 — past_events included so Day 3 agent sees Day 1+2 feedback.
"""
from __future__ import annotations
import sys, json, random, datetime
from pathlib import Path

PROJECT_ROOT = Path("/home/ha_agent/work/EnergyBridge")
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BENCH_DIR = Path(__file__).parent
LOG_DIR   = BENCH_DIR / "logs" / "dialogue"
LOG_DIR.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------------------
# Legacy flat personas (backward compatibility when no persona dict supplied)
# ---------------------------------------------------------------------------
OFFICE_PERSONA = {
    "id": "office_default",
    "display_name": "Office User",
    "summary": "Office worker who values thermal comfort and willing to cooperate with grid demand.",
    "speaking_language": "en",
    "stable_preferences": {
        "comfort_priority": 0.6, "cost_priority": 0.3, "grid_priority": 0.1,
        "preferred_temp_min": 22.0, "preferred_temp_max": 26.0,
        "allow_pre_cooling": True, "allow_temp_drift": True,
    },
    "scoring_weights": {"comfort": 0.60, "energy": 0.30, "vpp": 0.10},
    "vpp_override_prob": 0.0,
}
FAMILY_PERSONA = {
    "id": "family_default",
    "display_name": "Home User",
    "summary": "Home resident who wants basic comfort and is willing to support grid peak-shaving.",
    "speaking_language": "en",
    "stable_preferences": {
        "comfort_priority": 0.5, "cost_priority": 0.2, "grid_priority": 0.3,
        "preferred_temp_min": 24.0, "preferred_temp_max": 26.0,
        "allow_pre_cooling": True, "allow_temp_drift": True,
    },
    "scoring_weights": {"comfort": 0.50, "energy": 0.20, "vpp": 0.30},
    "vpp_override_prob": 0.0,
}


# ---------------------------------------------------------------------------
# Dialogue logger
# ---------------------------------------------------------------------------
def _append_dialogue_log(log_path: Path, entry: dict) -> None:
    """Append one JSON-Lines entry to the dialogue log file."""
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  [DialogueLog] write error: {e}")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _int_score(val, default=3) -> int:
    try:
        v = int(round(float(val)))
        return max(1, min(5, v))
    except (TypeError, ValueError):
        return default


def _rule_score(persona: dict, mean_temp_c: float, pmv_ok_fraction: float,
                energy_kwh_per_day: float,
                zone_group_temps: dict | None = None,
                washer_completed: bool = True,
                washer_during_vpp: bool = False) -> dict:
    """Rule-based fallback scorer using persona weights."""
    weights = persona.get("scoring_weights", {"comfort": 0.5, "energy": 0.3, "vpp": 0.2})
    sp = persona.get("stable_preferences", {})
    t_min = sp.get("preferred_temp_min", 23.0)
    t_max = sp.get("preferred_temp_max", 26.0)
    tolerance = persona.get("temp_tolerance", 1.5)

    # comfort_score: based on mean temp vs preferred range
    if t_min <= mean_temp_c <= t_max:
        comfort_score = 5
    elif mean_temp_c < t_min - tolerance or mean_temp_c > t_max + tolerance:
        comfort_score = 1
    elif mean_temp_c < t_min or mean_temp_c > t_max:
        overshoot = abs(mean_temp_c - (t_max if mean_temp_c > t_max else t_min))
        comfort_score = max(2, 5 - int(overshoot / 0.5))
    else:
        comfort_score = 3
    # boost from PMV ok fraction
    if pmv_ok_fraction >= 0.95:
        comfort_score = min(5, comfort_score + 1)

    # energy_score: reward lower consumption
    if energy_kwh_per_day < 20:
        energy_score = 5
    elif energy_kwh_per_day < 30:
        energy_score = 4
    elif energy_kwh_per_day < 50:
        energy_score = 3
    elif energy_kwh_per_day < 70:
        energy_score = 2
    else:
        energy_score = 1

    # vpp_score: based on setpoint compliance proxy
    if mean_temp_c >= 26.0:   # user tolerated warmth => VPP likely complied
        vpp_score = 4
    elif mean_temp_c >= 25.5:
        vpp_score = 3
    else:
        vpp_score = 2

    # washer penalty
    if not washer_completed:
        vpp_score = max(1, vpp_score - 1)
        comfort_score = max(1, comfort_score - 1)
    if washer_during_vpp:
        vpp_score = max(1, vpp_score - 1)   # ran washer added peak load

    # weighted overall
    w_c = weights.get("comfort", 0.5)
    w_e = weights.get("energy", 0.3)
    w_v = weights.get("vpp", 0.2)
    total = w_c * comfort_score + w_e * energy_score + w_v * vpp_score
    overall = max(1, min(5, int(round(total))))

    comment = f"rule_based: temp={mean_temp_c:.1f}C pmv_ok={pmv_ok_fraction*100:.0f}%"
    return {
        "score": overall, "comfort_score": comfort_score,
        "energy_score": energy_score, "vpp_score": vpp_score,
        "label": ["very_dissatisfied","dissatisfied","neutral","satisfied","very_satisfied"][overall-1],
        "comment": comment, "zone_comfort_scores": None,
    }


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def get_user_preference_input(
    building: str,
    event_index: int,
    vpp_context: dict,
    past_events: list,
    persona: dict | None = None,
    log_path: Path | None = None,
) -> str:
    """Get roleplay user preference statement BEFORE agent acts on a VPP event.

    With persona-aware enhancement:
    - Uses persona's roleplay_user_prompt as the LLM system guidance
    - comfort_sensitive: 50% chance of injecting strong override language
    - Returns English preference statement

    Returns user preference statement (str).
    """
    if persona is None:
        persona = OFFICE_PERSONA if building == "office" else FAMILY_PERSONA

    # VPP override: comfort_sensitive may express strong resistance
    override_prob = persona.get("vpp_override_prob", 0.0)
    if override_prob > 0 and random.random() < override_prob:
        override_msg = (
            f"I really don't want the temperature to go above 26°C during this VPP event. "
            f"My comfort is the top priority — please keep it below 26°C if at all possible."
        )
        _log_and_return(log_path, persona, event_index, "override_prefill", override_msg)
        return override_msg

    # L3: summarize past event scores as learning context
    learned_ctx = ""
    if past_events:
        summaries = [
            {"event": e["id"], "score": e.get("score"),
             "comfort": e.get("comfort_score"), "energy": e.get("energy_score"),
             "vpp": e.get("vpp_score"), "comment": e.get("comment", "")[:60]}
            for e in past_events
        ]
        learned_ctx = f"Past VPP experiences: {summaries}"

    scenario = {
        "event_type": "VPP demand response",
        "event_index": event_index,
        "vpp_context": vpp_context,
        "learned_from_past": learned_ctx,
        "question": "How do you feel about the upcoming demand response event? What matters most to you today?",
    }
    memory_snapshot = {"past_vpp_events": past_events[-2:]} if past_events else {}
    history_summary = [
        {"event": e["id"], "score": e.get("score"), "my_comment": e.get("comment", "")[:60]}
        for e in (past_events or [])
    ]

    # Build a richer persona dict for the roleplay LLM
    rp_persona = dict(persona)
    if "roleplay_user_prompt" in persona:
        rp_persona["summary"] = persona["roleplay_user_prompt"]

    try:
        from energybridge.utils.config import load_llm_config
        if not load_llm_config(use_key="USE_LLM").use_llm:
            raise RuntimeError("LLM off")
        from energybridge.llm.roleplay_user import RoleplayUserSimulator
        r = RoleplayUserSimulator().generate_user_input(
            persona=rp_persona,
            turn_index=event_index,
            scenario=scenario,
            memory_snapshot=memory_snapshot,
            history_summary=history_summary,
        )
        pref = r.get("data", {}).get("user_input", "")
        print(f"  [RoleplayUser event={event_index} persona={persona['id']}] {pref[:80]}")
        _log_and_return(log_path, persona, event_index, "llm", pref)
        return pref
    except Exception as e:
        print(f"  [RoleplayUser] get_preference failed: {e}")
        fallback = persona.get("roleplay_user_prompt", persona.get("summary", ""))[:100]
        _log_and_return(log_path, persona, event_index, "fallback", fallback)
        return fallback


def _log_and_return(log_path, persona, event_index, source, text):
    if log_path:
        _append_dialogue_log(log_path, {
            "ts": datetime.datetime.utcnow().isoformat(),
            "persona": persona.get("id", "?"),
            "event_index": event_index,
            "type": "user_input",
            "source": source,
            "text": text,
        })


def score_user_preference(
    building: str,
    method: str,
    mean_temp_c: float,
    pmv_ok_fraction: float,
    energy_kwh_per_day: float,
    agent_setpoint_c=None,
    event_index: int = 1,
    user_preference_text: str = "",
    agent_reason: str = "",
    zone_group_temps: dict | None = None,
    persona: dict | None = None,
    washer_completed: bool = True,
    washer_during_vpp: bool = False,
    log_path: Path | None = None,
):
    """Score user satisfaction using roleplay LLM (fallback: rule-based).

    Returns dict with: satisfaction_score, comfort_score, energy_score, vpp_score,
    satisfaction_label, comment, source.
    M2: zone_group_temps = {group: mean_temp} for office zone-aware scoring.
    Persona-aware: uses persona's scoring_weights for weighted overall.
    """
    if persona is None:
        persona = OFFICE_PERSONA if building == "office" else FAMILY_PERSONA

    home_state = {
        "indoor_temp": round(mean_temp_c, 1),
        "hvac_setpoint": agent_setpoint_c or round(mean_temp_c, 1),
        "energy_per_day_kwh": round(energy_kwh_per_day, 2),
        "washer_completed": washer_completed,
        "washer_during_vpp": washer_during_vpp,
    }
    if method in ("agent", "agent_pmv") and agent_setpoint_c:
        rationale = (
            f"LLM agent set cooling setpoint to {agent_setpoint_c}°C during VPP DR event. "
            f"Agent reason: {agent_reason[:100]}"
        )
        if user_preference_text:
            rationale += f" | User had expressed: {user_preference_text[:80]}"
        if not washer_completed:
            rationale += " | NOTE: washing machine task was NOT completed today."
        if washer_during_vpp:
            rationale += " | NOTE: washing machine ran DURING VPP window (added peak load)."
        control_plan = {
            "action": "set_hvac_temperature",
            "setpoint": agent_setpoint_c,
            "rationale": rationale,
        }
    else:
        control_plan = {
            "action": "set_hvac_temperature",
            "setpoint": round(mean_temp_c, 1),
            "rationale": (
                f"PMV reactive controller: mean_temp={mean_temp_c:.1f}°C, "
                f"pmv_comfort_ok={pmv_ok_fraction*100:.0f}%. No VPP adaptation."
            ),
        }

    safety = {"status": "approved", "reason": "Within safe operation bounds."}

    # M2: build zone group context for office
    zone_ctx = None
    if zone_group_temps and building == "office":
        zone_ctx = {g: {"mean_temp_c": round(t, 1)} for g, t in zone_group_temps.items()}

    # Build richer persona for feedback LLM
    rp_persona = dict(persona)
    if "roleplay_user_prompt" in persona:
        rp_persona["summary"] = persona["roleplay_user_prompt"]

    try:
        from energybridge.utils.config import load_llm_config
        if not load_llm_config(use_key="USE_LLM").use_llm:
            raise RuntimeError("LLM off")
        from energybridge.llm.roleplay_user import RoleplayUserSimulator
        r = RoleplayUserSimulator().generate_feedback(
            persona=rp_persona,
            turn_index=event_index,
            selected_strategy=home_state,
            projected_control_plan=control_plan,
            projected_safety_report=safety,
            zone_group_context=zone_ctx,
        )
        fb = r.get("data", {})
        result = {
            "score":           _int_score(fb.get("satisfaction_score"), 3),
            "comfort_score":   _int_score(fb.get("comfort_score"), 3),
            "energy_score":    _int_score(fb.get("energy_score"), 3),
            "vpp_score":       _int_score(fb.get("vpp_score"), 3),
            "label":           fb.get("satisfaction_label", "neutral"),
            "comment":         fb.get("comment", ""),
            "zone_comfort_scores": fb.get("zone_comfort_scores"),
            "source": "roleplay_llm",
        }
    except Exception as e:
        result = _rule_score(persona, mean_temp_c, pmv_ok_fraction, energy_kwh_per_day,
                             zone_group_temps, washer_completed, washer_during_vpp)
        result["source"] = "rule_based_fallback"

    # Dialogue log
    if log_path:
        _append_dialogue_log(log_path, {
            "ts": datetime.datetime.utcnow().isoformat(),
            "persona": persona.get("id", "?"),
            "event_index": event_index,
            "type": "feedback",
            "method": method,
            "scores": {
                "overall": result["score"],
                "comfort": result["comfort_score"],
                "energy":  result["energy_score"],
                "vpp":     result["vpp_score"],
            },
            "comment": result.get("comment", ""),
            "washer_completed": washer_completed,
            "washer_during_vpp": washer_during_vpp,
            "source": result.get("source", "?"),
        })

    return result
