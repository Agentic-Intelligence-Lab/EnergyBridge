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

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
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


def normalize_persona(persona: dict) -> dict:
    """Accept both legacy scorer personas and current roleplay JSON personas."""
    normalized = dict(persona)
    preferences = persona.get("preferences", {})
    if "scoring_weights" not in normalized:
        normalized["scoring_weights"] = dict(preferences.get("scoring_weights", {}))
    if "vpp_override_prob" not in normalized:
        normalized["vpp_override_prob"] = float(preferences.get("vpp_override_prob", 0.0))
    if "stable_preferences" not in normalized:
        ac = persona.get("appliances", {}).get("ac", {})
        weights = normalized.get("scoring_weights", {})
        normalized["stable_preferences"] = {
            "comfort_priority": float(weights.get("comfort", 0.5)),
            "cost_priority": float(weights.get("energy", 0.3)),
            "grid_priority": float(weights.get("vpp", 0.2)),
            "preferred_temp_min": float(ac.get("setpoint_preferred_min_c", 24.0)),
            "preferred_temp_max": float(ac.get("setpoint_preferred_max_c", 26.0)),
            "allow_pre_cooling": True,
            "allow_temp_drift": float(ac.get("temp_tolerance_c", 1.0)) > 0.0,
        }
    normalized.setdefault(
        "temp_tolerance",
        float(persona.get("appliances", {}).get("ac", {}).get("temp_tolerance_c", 1.5)),
    )
    prompts = persona.get("llm_prompts", {})
    normalized.setdefault("roleplay_user_prompt", prompts.get("system_prompt", ""))
    normalized.setdefault("summary", persona.get("description", ""))
    return normalized


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
_APPLIANCE_KEYS = ("washer", "dishwasher", "dryer", "water_heater", "ev")


def _get_appliance_presence(vpp_context: dict) -> dict:
    raw = (vpp_context or {}).get("appliances", {}) if isinstance(vpp_context, dict) else {}
    return {k: bool((raw.get(k, False) if isinstance(raw, dict) else False)) for k in _APPLIANCE_KEYS}


def _strategy_appliance_plan_cn(strategy_id: str, presence: dict) -> str:
    if not presence:
        return ""
    parts = []
    if presence.get("washer"):
        parts.append("洗衣机错峰")
    if presence.get("dishwasher"):
        parts.append("洗碗机错峰")
    if presence.get("dryer"):
        parts.append("烘干机错峰")
    if presence.get("water_heater"):
        parts.append("热水器18:00前预热")
    if presence.get("ev"):
        parts.append("EV避开18:00-19:00")
    if not parts:
        return ""

    if strategy_id == "A":
        head = "尽量保舒适，电器以温和错峰为主"
    elif strategy_id == "B":
        head = "舒适与削峰平衡，电器主动错峰"
    else:
        head = "优先削峰，电器尽量后移"
    return f"{head}：" + "，".join(parts)


def _strategy_appliance_pref_en(strategy_id: str, presence: dict) -> str:
    if not presence:
        return ""
    actions = []
    if presence.get("washer"):
        actions.append("shift washer away from 18:00-19:00")
    if presence.get("dishwasher"):
        actions.append("shift dishwasher away from 18:00-19:00")
    if presence.get("dryer"):
        actions.append("shift dryer away from 18:00-19:00")
    if presence.get("water_heater"):
        actions.append("finish water-heater preheat before 18:00")
    if presence.get("ev"):
        actions.append("keep EV charging out of 18:00-19:00")
    if not actions:
        return ""

    if strategy_id == "A":
        prefix = "Keep comfort as priority while"
    elif strategy_id == "B":
        prefix = "Balance comfort and demand response by"
    else:
        prefix = "Prioritize peak reduction by"
    return f"{prefix} " + ", ".join(actions) + "."


def _compose_pref_with_appliances(selected: dict, presence: dict) -> str:
    base = selected.get("user_pref", selected.get("description", "")).strip()
    sid = str(selected.get("id", "")).upper()
    tail = _strategy_appliance_pref_en(sid, presence).strip()
    if not tail:
        return base
    if not base:
        return tail
    return f"{base} {tail}"

def get_user_preference_input(
    building: str,
    event_index: int,
    vpp_context: dict,
    past_events: list,
    persona: dict | None = None,
    log_path: Path | None = None,
    human_mode: bool = False,
) -> str:
    """Get user preference statement BEFORE agent acts on a VPP event.

    Workflow:
      1. LLM generates 3 candidate strategies (A=comfort, B=balanced, C=savings).
      2. All 3 are printed to the log for visibility.
      3. In automated mode: auto-selects based on persona comfort_priority.
         In human_mode=True: prints choices and waits for terminal input (Feature 2).
      4. Returns the selected strategy's 'user_pref' text, which is injected into
         the AC agent's prompt for that event.

    Falls back to hardcoded defaults if LLM strategy generation fails.
    """
    if persona is None:
        persona = OFFICE_PERSONA if building == "office" else FAMILY_PERSONA
    else:
        persona = normalize_persona(persona)

    # VPP override: comfort_sensitive persona may bypass strategy menu
    override_prob = persona.get("vpp_override_prob", 0.0)
    if override_prob > 0 and random.random() < override_prob:
        override_msg = (
            "I really don't want the temperature to go above 26°C during this VPP event. "
            "My comfort is the top priority — please keep it below 26°C if at all possible."
        )
        _log_and_return(log_path, persona, event_index, "override_prefill", override_msg)
        return override_msg

    # Step 1: Generate 3 candidate strategies
    candidates = generate_vpp_strategy_candidates(
        building, event_index, vpp_context, past_events, persona
    )

    # Step 2: Display all strategies
    appliance_presence = _get_appliance_presence(vpp_context)
    print(f"  ┌─[Strategy Candidates | VPP event {event_index}]{'─'*30}")
    for c in candidates:
        print(f"  │  [{c['id']}] {c['label']}  —  {c['description']}  ({c['tradeoff']})")
        plan_cn = _strategy_appliance_plan_cn(str(c.get('id', '')).upper(), appliance_presence)
        if plan_cn:
            print(f"  │      电器控制: {plan_cn}")
    print(f"  └{'─'*56}")

    # Step 3: Select strategy
    selected = _auto_select_strategy(candidates, persona)

    if human_mode:
        # --- Human-in-the-loop: prompt for selection ---
        auto_id = selected["id"]
        print(f"  ┌─[请选择策略 | VPP event {event_index}]{'─'*28}")
        print(f"  │  输入 A / B / C 选择策略，或直接输入自定义偏好文字")
        print(f"  │  直接回车 = 采用自动推荐 [{auto_id}] {selected['label']}")
        print(f"  └{'─'*56}")
        try:
            raw_choice = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            raw_choice = ""

        if raw_choice.upper() in ("A", "B", "C"):
            override = next((c for c in candidates if c["id"] == raw_choice.upper()), None)
            if override:
                selected = override
                print(f"  [Strategy Selected  | event={event_index}] → [{selected['id']}] {selected['label']} (human)")
                pref = _compose_pref_with_appliances(selected, appliance_presence)
                _log_and_return(log_path, persona, event_index, "strategy_human",
                                pref, extra={"selected_id": selected["id"], "human_input": raw_choice,
                                             "candidates": [{k: c[k] for k in ("id","label","description")}
                                                            for c in candidates]})
                return pref
        elif raw_choice:
            # custom free-text preference
            print(f"  [Strategy Selected  | event={event_index}] → [custom] (human)")
            _log_and_return(log_path, persona, event_index, "strategy_human_custom",
                            raw_choice, extra={"human_input": raw_choice,
                                               "candidates": [{k: c[k] for k in ("id","label","description")}
                                                              for c in candidates]})
            return raw_choice
        else:
            print(f"  [Strategy Selected  | event={event_index}] → [{selected['id']}] {selected['label']} (human→auto)")

    else:
        print(f"  [Strategy Selected  | event={event_index}] → [{selected['id']}] {selected['label']} (auto)")

    pref = _compose_pref_with_appliances(selected, appliance_presence)
    mode_tag = "human_auto" if human_mode else "auto"
    _log_and_return(log_path, persona, event_index, f"strategy_{mode_tag}",
                    pref, extra={"selected_id": selected["id"],
                                 "candidates": [{k: c[k] for k in ("id","label","description")}
                                                for c in candidates]})
    return pref


# ---------------------------------------------------------------------------
# VPP Strategy Candidate Generation  (Feature 1)
# ---------------------------------------------------------------------------
_STRATEGY_DEFAULTS = [
    {
        "id": "A", "label": "舒适优先",
        "description": "保持设定点25°C，接受较高电耗",
        "tradeoff": "舒适度最高，电耗偏多",
        "user_pref": (
            "Comfort first: please keep the temperature at 25°C. "
            "I'm willing to use more energy to stay comfortable during this VPP event."
        ),
    },
    {
        "id": "B", "label": "平衡策略",
        "description": "升温至26°C，家电提前完成或延后",
        "tradeoff": "轻微温漂，节电约15%",
        "user_pref": (
            "Balanced: I'm okay with a brief setpoint rise to 26°C and shifting "
            "shiftable appliances away from the VPP window to reduce peak load."
        ),
    },
    {
        "id": "C", "label": "节能优先",
        "description": "升温至27°C，所有可平移家电延迟至VPP后",
        "tradeoff": "明显温漂，节电约30%",
        "user_pref": (
            "Energy saving first: please raise the setpoint to 27°C and delay all "
            "shiftable appliances past the VPP window — I can tolerate the warmth."
        ),
    },
]


def generate_vpp_strategy_candidates(
    building: str,
    event_index: int,
    vpp_context: dict,
    past_events: list,
    persona: dict | None = None,
) -> list[dict]:
    """Generate 3 VPP response strategy candidates (A=comfort, B=balanced, C=savings).

    Tries LLM first; falls back to hardcoded defaults.
    Each item: {id, label, description, tradeoff, user_pref}.
    'user_pref' is the English preference statement injected into the AC agent prompt.
    """
    if persona is None:
        persona = OFFICE_PERSONA if building == "office" else FAMILY_PERSONA
    else:
        persona = normalize_persona(persona)

    try:
        from energybridge.utils.config import load_llm_config
        if not load_llm_config(use_key="USE_LLM").use_llm:
            raise RuntimeError("LLM off")
        from energybridge.llm.client import LLMClient

        sp = persona.get("stable_preferences", {})
        past_summary = [
            {"event": e["id"], "score": e.get("score"), "comment": e.get("comment", "")[:50]}
            for e in (past_events or [])
        ]
        _ap = _get_appliance_presence(vpp_context)
        _active_appliances = [k for k, v in _ap.items() if v]
        _active_appliances_text = ",".join(_active_appliances) if _active_appliances else "none"

        sys_prompt = (
            "You are an energy management strategy advisor for a smart home VPP demand-response system. "
            "Generate 3 distinct response strategies for the upcoming peak-shaving event. "
            "Strategy A = comfort-first, B = balanced, C = energy-saving. "
            "Tailor them to the user persona and explicitly include appliance control. "
            "If appliances are available, mention how to handle washer, dishwasher, dryer, water heater, and EV in strategy text. "
            'Return ONLY a JSON array of exactly 3 objects, each with keys: '
            '"id" ("A"/"B"/"C"), '
            '"label" (Chinese label ≤6 chars), '
            '"description" (Chinese action summary ≤64 chars), '
            '"tradeoff" (Chinese tradeoff ≤30 chars), '
            '"user_pref" (English preference statement ≤140 chars, will be injected into AC agent prompt).'
        )
        user_msg = (
            f"Building={building}. VPP event #{event_index}. "
            f"Context: {json.dumps(vpp_context, ensure_ascii=False)}. "
            f"Active appliances: {_active_appliances_text}. "
            f"Persona: comfort_priority={sp.get('comfort_priority', 0.5)}, "
            f"preferred_range={sp.get('preferred_temp_min', 24)}-{sp.get('preferred_temp_max', 26)}°C. "
            f"Past events: {json.dumps(past_summary, ensure_ascii=False)}."
        )
        resp = LLMClient().chat_with_metrics(
            sys_prompt, user_msg, max_retries=3, retry_base_delay=1.0
        )
        raw = resp["text"].strip()
        if raw.startswith("```"):
            raw = "\n".join(ln for ln in raw.splitlines() if not ln.startswith("```"))
        candidates = json.loads(raw)
        if isinstance(candidates, list) and len(candidates) == 3:
            required = ("id", "label", "description", "tradeoff", "user_pref")
            for c in candidates:
                if not all(k in c for k in required):
                    raise ValueError(f"missing keys in candidate: {c}")
            return candidates
        raise ValueError(f"unexpected shape: {type(candidates)}")
    except Exception as e:
        print(f"  [StrategyGen] LLM failed ({e}), using defaults")
        return list(_STRATEGY_DEFAULTS)  # copy so mutations don't affect template


def _auto_select_strategy(candidates: list[dict], persona: dict) -> dict:
    """Select strategy based on persona comfort_priority (A≥0.65 / B 0.40-0.65 / C<0.40)."""
    sp = persona.get("stable_preferences", {})
    cp = float(sp.get("comfort_priority", 0.5))
    if cp >= 0.65:
        sel_id = "A"
    elif cp >= 0.40:
        sel_id = "B"
    else:
        sel_id = "C"
    return next((c for c in candidates if c["id"] == sel_id), candidates[1])


def _log_and_return(log_path, persona, event_index, source, text, extra: dict | None = None):
    if log_path:
        entry = {
            "ts": datetime.datetime.utcnow().isoformat(),
            "persona": persona.get("id", "?"),
            "event_index": event_index,
            "type": "user_input",
            "source": source,
            "text": text,
        }
        if extra:
            entry.update(extra)
        _append_dialogue_log(log_path, entry)


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
    human_mode: bool = False,
):
    """Score user satisfaction using roleplay LLM (fallback: rule-based).

    Returns dict with: satisfaction_score, comfort_score, energy_score, vpp_score,
    satisfaction_label, comment, source.
    M2: zone_group_temps = {group: mean_temp} for office zone-aware scoring.
    Persona-aware: uses persona's scoring_weights for weighted overall.
    """
    if persona is None:
        persona = OFFICE_PERSONA if building == "office" else FAMILY_PERSONA
    else:
        persona = normalize_persona(persona)

    # Human-in-the-loop scoring: print event summary and ask for terminal input
    if human_mode:
        sp_str = f"{agent_setpoint_c:.1f}°C" if agent_setpoint_c else f"{mean_temp_c:.1f}°C"
        print(f"  ╔═[VPP事件{event_index} 满意度评分]{'═'*36}")
        print(f"  ║  VPP期间室内均温: {mean_temp_c:.1f}°C   设定点: {sp_str}")
        print(f"  ║  今日用电: {energy_kwh_per_day:.2f} kWh   舒适达标率: {pmv_ok_fraction*100:.0f}%")
        if agent_reason:
            print(f"  ║  Agent理由: {agent_reason[:100]}")
        print(f"  ╚{'═'*52}")
        print("  请对本次VPP处理结果评分（1=非常不满 / 5=非常满意），直接回车=3：")
        try:
            raw_score = input("  > ").strip()
            score = max(1, min(5, int(raw_score))) if raw_score.isdigit() else 3
        except (EOFError, KeyboardInterrupt):
            score = 3
        print("  可选：留下简短反馈（直接回车跳过）：")
        try:
            comment = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            comment = ""
        return {
            "score": score, "comfort_score": score, "energy_score": score,
            "vpp_score": score, "label": "human", "comment": comment or "—",
            "zone_comfort_scores": None, "source": "human",
        }

    home_state = {
        "indoor_temp": round(mean_temp_c, 1),
        "hvac_setpoint": agent_setpoint_c or round(mean_temp_c, 1),
        "energy_per_day_kwh": round(energy_kwh_per_day, 2),
        "washer_completed": washer_completed,
        "washer_during_vpp": washer_during_vpp,
    }
    if method in ("agent", "agent_pmv", "rl", "mpc", "mpc_dynamic", "mpc_ep") and agent_setpoint_c:
        if method == "rl":
            controller = "RL baseline"
        elif method in ("mpc", "mpc_dynamic", "mpc_ep"):
            controller = "MPC baseline"
        else:
            controller = "LLM agent"
        rationale = (
            f"{controller} set cooling setpoint to {agent_setpoint_c}°C during VPP DR event. "
            f"Controller explanation: {agent_reason[:100]}"
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
