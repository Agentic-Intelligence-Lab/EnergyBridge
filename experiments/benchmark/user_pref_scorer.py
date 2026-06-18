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
from energybridge.roleplay.calendar import calendar_brief_for_prompt, calendar_context_for_event

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BENCH_DIR = Path(__file__).parent
LOG_DIR   = BENCH_DIR / "logs" / "dialogue"
LOG_DIR.mkdir(parents=True, exist_ok=True)

class StrategyPreference(str):
    """String preference text with attached candidate/selection trace metadata."""

    def __new__(cls, text: str, strategy_trace: dict | None = None):
        obj = str.__new__(cls, text)
        obj.strategy_trace = strategy_trace or {}
        return obj

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
                washer_during_vpp: bool = False,
                skipped_task_count: int = 0,
                skipped_devices: list[str] | None = None) -> dict:
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

    if skipped_task_count > 0:
        skipped_names = ", ".join(skipped_devices or [])
        return {
            "score": 1,
            "comfort_score": max(1, comfort_score),
            "energy_score": max(1, energy_score),
            "vpp_score": 1,
            "label": "very_dissatisfied",
            "comment": f"rule_based: skipped required task(s): {skipped_names or skipped_task_count}",
            "zone_comfort_scores": None,
        }

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


def _service_from_action_key(key: str) -> str | None:
    key = str(key)
    if key.startswith("washer_"):
        return "washer"
    if key.startswith("dishwasher_"):
        return "dishwasher"
    if key.startswith("dryer_"):
        return "dryer"
    if key.startswith("water_heater_"):
        return "water_heater"
    if key.startswith("ev_"):
        return "ev"
    return None


def _services_from_actions(actions: dict | None) -> set[str]:
    services: set[str] = set()
    if not isinstance(actions, dict):
        return services
    for key, value in actions.items():
        if value is None:
            continue
        service = _service_from_action_key(str(key))
        if service:
            services.add(service)
    return services


def _fmt_hour_for_window(hour: float) -> str:
    h = float(hour) % 24.0
    hh = int(h)
    mm = int(round((h - hh) * 60))
    if mm >= 60:
        hh = (hh + 1) % 24
        mm = 0
    return f"{hh:02d}:{mm:02d}"


def _vpp_window_from_context(vpp_context: dict | None) -> tuple[float, float, str]:
    ctx = vpp_context or {}
    start = float(ctx.get("trigger_h", ctx.get("hour", 18.0))) % 24.0
    if "end_h" in ctx:
        end = float(ctx.get("end_h", start + float(ctx.get("duration_h", 1.0)))) % 24.0
    else:
        end = (start + float(ctx.get("duration_h", 1.0))) % 24.0
    return start, end, f"{_fmt_hour_for_window(start)}-{_fmt_hour_for_window(end)}"


def _get_appliance_presence(vpp_context: dict) -> dict:
    raw = (vpp_context or {}).get("appliances", {}) if isinstance(vpp_context, dict) else {}
    return {k: bool((raw.get(k, False) if isinstance(raw, dict) else False)) for k in _APPLIANCE_KEYS}


def _appliance_control_profile(persona: dict | None, vpp_context: dict | None) -> dict:
    """Return present/controllable/fixed status for each appliance.

    VPP runtime context only tells the role-play layer whether a device exists.
    The persona JSON carries the critical second bit: whether the agent is
    allowed to shift/control that device. Strategy text must not promise control
    over fixed routines, or low-DR users will reasonably punish the agent.
    """
    presence = _get_appliance_presence(vpp_context or {})
    app_cfg = (persona or {}).get("appliances", {}) or {}
    profile: dict = {}
    for name in _APPLIANCE_KEYS:
        cfg = app_cfg.get(name, {}) or {}
        present = bool(presence.get(name, False) or cfg.get("present", False))
        if name in {"washer", "dishwasher", "dryer"}:
            controllable = present and bool(cfg.get("shiftable", True)) and bool(cfg.get("dr_adjustable", True))
        elif name == "water_heater":
            controllable = present and bool(cfg.get("dr_adjustable", True))
        elif name == "ev":
            controllable = present and cfg.get("dr_adjustable") is not False
        else:
            controllable = present
        profile[name] = {
            "present": present,
            "controllable": bool(controllable),
            "fixed": bool(present and not controllable),
        }
    return profile


def _profile_present(profile: dict, name: str) -> bool:
    raw = profile.get(name, False)
    if isinstance(raw, dict):
        return bool(raw.get("present", False))
    return bool(raw)


def _profile_controllable(profile: dict, name: str) -> bool:
    raw = profile.get(name, False)
    if isinstance(raw, dict):
        return bool(raw.get("present", False) and raw.get("controllable", False))
    return bool(raw)


def _profile_fixed_names(profile: dict) -> list[str]:
    names: list[str] = []
    for name in _APPLIANCE_KEYS:
        raw = profile.get(name, False)
        if isinstance(raw, dict) and raw.get("present") and raw.get("fixed"):
            names.append(name)
    return names


def _profile_controllable_names(profile: dict) -> list[str]:
    return [name for name in _APPLIANCE_KEYS if _profile_controllable(profile, name)]


def _fixed_appliance_constraints(persona: dict | None) -> list[str]:
    """Return present devices that the controller cannot shift for DR."""
    app = (persona or {}).get("appliances", {}) or {}
    fixed: list[str] = []
    for name in ("washer", "dishwasher", "dryer"):
        cfg = app.get(name, {}) or {}
        if bool(cfg.get("present")) and (
            not bool(cfg.get("shiftable", True)) or not bool(cfg.get("dr_adjustable", True))
        ):
            fixed.append(name)
    wh = app.get("water_heater", {}) or {}
    if bool(wh.get("present")) and not bool(wh.get("dr_adjustable", True)):
        fixed.append("water_heater")
    ev = app.get("ev", {}) or {}
    if bool(ev.get("present")) and ev.get("dr_adjustable") is False:
        fixed.append("ev")
    return fixed


def _low_disruption_strategy_language(persona: dict | None) -> bool:
    """True when strategy text should avoid cost/savings/DR-push framing."""
    tags = (persona or {}).get("tags", {}) or {}
    return (
        tags.get("price") in {"low_incentive", "price_indifferent"}
        or tags.get("grid_value") in {"low_value", "uncertain_flex"}
        or tags.get("control") in {"confirm_required", "low_auto_accept", "privacy_sensitive"}
        or tags.get("task") in {"rigid", "semi_rigid"}
    )


def build_vpp_preference_memory_notes(past_events: list | None, persona: dict | None = None) -> list[str]:
    """Extract compact, reusable strategy-generation rules from past feedback."""
    events = list(past_events or [])
    if not events:
        return []

    notes: list[str] = []
    persona = normalize_persona(persona or FAMILY_PERSONA)
    tags = persona.get("tags", {}) or {}
    fixed_appliances = set(_fixed_appliance_constraints(persona))
    comments = " ".join(str(e.get("comment", "")) for e in events).lower()
    user_inputs = " ".join(str(e.get("user_input", "")) for e in events).lower()
    low_scores = [e for e in events if int(e.get("score") or 3) <= 3]
    low_score_text = " ".join(
        (str(e.get("comment", "")) + " " + str(e.get("user_input", ""))).lower()
        for e in low_scores
    )
    recent = events[-3:]

    def _score(event: dict, key: str, default: int = 3) -> int:
        try:
            return max(1, min(5, int(round(float(event.get(key, default))))))
        except (TypeError, ValueError):
            return default

    def _has_controllable_service_issue(event: dict) -> bool:
        summary = event.get("appliance_summary") or {}
        if not isinstance(summary, dict):
            return False
        for name, info in summary.items():
            if not isinstance(info, dict) or not bool(info.get("present")):
                continue
            if name in {"washer", "dishwasher", "dryer"} and bool(info.get("skipped")):
                return True
            if name not in fixed_appliances and bool(info.get("ran_during_vpp")):
                return True
        return False

    def _has_fixed_vpp_overlap(event: dict) -> bool:
        summary = event.get("appliance_summary") or {}
        if not isinstance(summary, dict):
            return False
        for name, info in summary.items():
            if (
                name in fixed_appliances
                and isinstance(info, dict)
                and bool(info.get("present"))
                and bool(info.get("ran_during_vpp"))
            ):
                return True
        return False

    recent_positive = (
        len(recent) >= 2
        and all(_score(e, "score") >= 4 for e in recent[-2:])
        and all(_score(e, "comfort_score") >= 4 for e in recent[-2:])
    )
    recent_clean = recent_positive and not any(_has_controllable_service_issue(e) for e in recent[-2:])
    any_too_warm = any(
        word in comments + " " + user_inputs
        for word in ("too warm", "above 26", "26.5", "hot", "uncomfortable", "temperature drift")
    )
    normal_comfort = tags.get("comfort") in {None, "", "normal_comfort"}
    exploration_allowed = (
        recent_clean
        and not low_scores
        and normal_comfort
        and tags.get("control") not in {"confirm_required", "low_auto_accept", "privacy_sensitive"}
        and not _low_disruption_strategy_language(persona)
        and not any_too_warm
    )

    if _low_disruption_strategy_language(persona) or any(
        word in comments for word in ("cost talk", "saving", "savings", "money", "irrelevant", "annoying")
    ):
        notes.append(
            "Keep user-facing explanations centered on comfort preservation, routine stability, and low-risk event support."
        )
    if any(word in low_score_text for word in ("pointless", "weak", "limited", "not very useful", "partial")):
        notes.append(
            "Set expectations that fixed routines can limit VPP impact; do not overpromise grid response."
        )
    if (
        any(word in low_score_text for word in ("return home", "home arrival", "commute home", "arrive home"))
        and any(word in low_score_text for word in ("warm", "26.5", "above 26", "temperature drift"))
    ):
        notes.append(
            "For events near return-home or dinner time, avoid the warm edge of the comfort range and restore comfort immediately after the event."
        )
    if any(word in low_score_text for word in ("above 26", "26.5", "too warm")):
        notes.append(
            "Treat 26C as the practical comfort ceiling unless the user explicitly accepts a warmer setting for that event."
        )
    if any(word in comments for word in ("fixed load", "fixed loads", "fixed hot-water", "routine unchanged")):
        notes.append(
            "Treat fixed/non-DR-adjustable appliances as constraints, not controllable levers."
        )
    if any(_has_fixed_vpp_overlap(e) for e in recent):
        notes.append(
            "When fixed appliances overlap the event, acknowledge the constraint and focus future actions on controllable devices and comfort-safe HVAC only."
        )
    if any(word in user_inputs + comments for word in ("confirm", "only", "comfort-safe", "tiny adjustment")):
        notes.append(
            "Keep each event within the selected confirmation boundary; avoid stronger actions than the user approved."
        )
    if any(word in comments for word in ("comfort stayed", "comfort kept", "within range", "acceptable")):
        notes.append(
            "Repeat the proven pattern: keep AC inside the preferred range and mention that comfort/routine are protected."
        )
    if exploration_allowed:
        notes.append(
            "Recent events scored well with comfort preserved; cautiously improve energy by using the warm edge of the preferred AC range during VPP, then restore without overcooling."
        )
        notes.append(
            "For future daily planning, avoid unnecessary cooling below the user's comfortable mid-to-warm range unless safety or explicit feedback asks for colder air."
        )
    elif recent_positive and any_too_warm:
        notes.append(
            "Do not escalate energy-saving temperature exploration because prior feedback mentioned warmth; keep the next event closer to the normal comfort target."
        )
    if any(e.get("target_achieved") is False for e in recent) and recent_clean and not _low_disruption_strategy_language(persona):
        notes.append(
            "If a future VPP target is missed without comfort complaints, strengthen controllable load shifting and use the warmest still-comfortable AC setting inside the preferred range."
        )

    deduped: list[str] = []
    for note in notes:
        if note not in deduped:
            deduped.append(note)
    return deduped[:5]


def _strategy_appliance_plan_text(
    strategy_id: str,
    presence: dict,
    vpp_context: dict | None = None,
    persona: dict | None = None,
) -> str:
    if not presence:
        return ""
    start_h, _, window_text = _vpp_window_from_context(vpp_context)
    low_disruption = _low_disruption_strategy_language(persona)
    parts = []
    if _profile_controllable(presence, "washer"):
        parts.append("shift washer away from VPP")
    if _profile_controllable(presence, "dishwasher"):
        parts.append("shift dishwasher away from VPP")
    if _profile_controllable(presence, "dryer"):
        parts.append("shift dryer away from VPP")
    if _profile_controllable(presence, "water_heater"):
        parts.append(f"finish water-heater preheat before {_fmt_hour_for_window(start_h)}")
    if _profile_controllable(presence, "ev"):
        parts.append(f"keep EV charging out of {window_text}")
    fixed = _profile_fixed_names(presence)
    if not parts and not fixed:
        return ""

    if low_disruption:
        if strategy_id == "A":
            head = "Protect comfort and routine first"
        elif strategy_id == "B":
            head = "Use only low-disruption support"
        else:
            head = "Support the event without disrupting routine"
    elif strategy_id == "A":
        head = "Prioritize comfort with gentle appliance shifting"
    elif strategy_id == "B":
        head = "Balance comfort and peak reduction with active shifting"
    else:
        head = "Prioritize peak reduction and move controllable loads"
    text = f"{head}: " + (", ".join(parts) if parts else "adjust only controllable HVAC/load")
    if fixed:
        text += "; keep fixed appliances unchanged (" + ", ".join(fixed) + ")"
    return text


def _strategy_appliance_plan_cn(
    strategy_id: str,
    presence: dict,
    vpp_context: dict | None = None,
    persona: dict | None = None,
) -> str:
    """Backward-compatible alias; returns English text despite the old name."""
    return _strategy_appliance_plan_text(strategy_id, presence, vpp_context, persona)


def _strategy_appliance_pref_en(
    strategy_id: str,
    presence: dict,
    vpp_context: dict | None = None,
    persona: dict | None = None,
) -> str:
    if not presence:
        return ""
    start_h, _, window_text = _vpp_window_from_context(vpp_context)
    low_disruption = _low_disruption_strategy_language(persona)
    actions = []
    if _profile_controllable(presence, "washer"):
        actions.append(f"shift washer away from {window_text}")
    if _profile_controllable(presence, "dishwasher"):
        actions.append(f"shift dishwasher away from {window_text}")
    if _profile_controllable(presence, "dryer"):
        actions.append(f"shift dryer away from {window_text}")
    if _profile_controllable(presence, "water_heater"):
        actions.append(f"finish water-heater preheat before {_fmt_hour_for_window(start_h)}")
    if _profile_controllable(presence, "ev"):
        actions.append(f"keep EV charging out of {window_text}")
    fixed = _profile_fixed_names(presence)
    if not actions and not fixed:
        return ""

    if low_disruption:
        if strategy_id == "A":
            prefix = "Protect comfort and routine while"
        elif strategy_id == "B":
            prefix = "Make only low-disruption event support by"
        else:
            prefix = "Use the strongest still-routine-safe support by"
    elif strategy_id == "A":
        prefix = "Keep comfort as priority while"
    elif strategy_id == "B":
        prefix = "Balance comfort and demand response by"
    else:
        prefix = "Prioritize peak reduction by"
    if actions:
        text = f"{prefix} " + ", ".join(actions) + "."
    else:
        text = "Use only comfort-safe controllable actions."
    if fixed:
        text += " Keep fixed appliance routines unchanged: " + ", ".join(fixed) + "."
    if low_disruption:
        text += " Keep the explanation centered on comfort and routine only."
    return text


def _compose_pref_with_appliances(selected: dict, presence: dict, vpp_context: dict | None = None) -> str:
    base = selected.get("user_pref", selected.get("description", "")).strip()
    if selected.get("_profile_aligned"):
        return base
    sid = str(selected.get("id", "")).upper()
    tail = _strategy_appliance_pref_en(sid, presence, vpp_context).strip()
    if not tail:
        return base
    if not base:
        return tail
    return f"{base} {tail}"


def _candidate_trace_items(
    candidates: list[dict],
    presence: dict,
    vpp_context: dict | None,
    persona: dict | None,
) -> list[dict]:
    items = []
    for candidate in candidates:
        sid = str(candidate.get("id", "")).upper()
        items.append({
            "id": sid,
            "label": candidate.get("label", ""),
            "description": candidate.get("description", ""),
            "tradeoff": candidate.get("tradeoff", ""),
            "user_pref": candidate.get("user_pref", ""),
            "appliance_plan": _strategy_appliance_plan_text(sid, presence, vpp_context, persona),
            "appliance_plan_cn": _strategy_appliance_plan_text(sid, presence, vpp_context, persona),
            "appliance_pref_en": _strategy_appliance_pref_en(sid, presence, vpp_context, persona),
        })
    return items


def _selected_trace_item(
    selected: dict,
    *,
    source: str,
    preference_text: str,
    selection_meta: dict | None = None,
    human_input: str = "",
) -> dict:
    return {
        "id": selected.get("id", ""),
        "label": selected.get("label", ""),
        "description": selected.get("description", ""),
        "tradeoff": selected.get("tradeoff", ""),
        "source": source,
        "human_input": human_input,
        "preference_text": preference_text,
        "selection_meta": selection_meta or {},
    }


def _strategy_user_pref_for_profile(
    candidate: dict,
    profile: dict,
    vpp_context: dict | None,
    persona: dict | None,
) -> str:
    """Build a concise user preference that only promises controllable actions."""
    sid = str(candidate.get("id", "")).upper()
    sp = (persona or {}).get("stable_preferences", {}) or {}
    pref_min = float(sp.get("preferred_temp_min", 24.0))
    pref_max = float(sp.get("preferred_temp_max", 26.0))
    low_disruption = _low_disruption_strategy_language(persona)
    if low_disruption:
        if sid == "A":
            base = f"Comfort first: keep AC within {pref_min:.1f}-{pref_max:.1f}°C and protect routine."
        elif sid == "B":
            base = f"Low-disruption support: allow only a tiny AC adjustment within {pref_min:.1f}-{pref_max:.1f}°C."
        else:
            base = f"Routine-safe support: use the warmest comfortable AC setting within {pref_min:.1f}-{pref_max:.1f}°C."
    elif sid == "A":
        base = f"Comfort first: keep AC within {pref_min:.1f}-{pref_max:.1f}°C."
    elif sid == "B":
        base = f"Balanced: allow only a brief AC adjustment within {pref_min:.1f}-{pref_max:.1f}°C."
    else:
        base = f"Energy-aware: use the warmest still-comfortable AC setting within {pref_min:.1f}-{pref_max:.1f}°C."
    tail = _strategy_appliance_pref_en(sid, profile, vpp_context, persona)
    return f"{base} {tail}".strip()


def _align_candidates_to_appliance_profile(
    candidates: list[dict],
    profile: dict,
    vpp_context: dict | None,
    persona: dict | None,
) -> list[dict]:
    """Make strategy candidates consistent with appliance controllability."""
    fixed = _profile_fixed_names(profile)
    controllable = _profile_controllable_names(profile)
    low_disruption = _low_disruption_strategy_language(persona)
    aligned: list[dict] = []
    for raw in candidates:
        c = dict(raw)
        c["user_pref"] = _strategy_user_pref_for_profile(c, profile, vpp_context, persona)
        if low_disruption:
            if str(c.get("id", "")).upper() == "A":
                c["label"] = "Comfort first"
                c["tradeoff"] = "Least disruption, limited support"
            elif str(c.get("id", "")).upper() == "B":
                c["label"] = "Gentle support"
                c["tradeoff"] = "Small adjustment, routine unchanged"
            else:
                c["label"] = "Low-disruption support"
                c["tradeoff"] = "Stronger support while low disruption"
        desc = str(c.get("description", "")).strip()
        if fixed:
            fixed_note = "fixed appliances unchanged"
            desc = f"{desc}; {fixed_note}" if desc else fixed_note
        if not controllable and fixed:
            desc = "Only comfort-safe AC adjustment; fixed appliances unchanged"
        if low_disruption and str(c.get("id", "")).upper() == "C":
            desc = "Gentle support within comfort range; fixed routine unchanged"
        c["description"] = desc[:64]
        c["_profile_aligned"] = True
        aligned.append(c)
    return aligned

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
        tags = persona.get("tags", {}) or {}
        ac_cfg = (persona.get("appliances", {}) or {}).get("ac", {}) or {}
        pref_max = float(ac_cfg.get("setpoint_preferred_max_c", persona.get("preferred_temp_max", 26.0)))
        _, _, window_text = _vpp_window_from_context(vpp_context)
        if _low_disruption_strategy_language(persona):
            override_msg = (
                "For this brief event, I only approve comfort-safe, routine-preserving actions: "
                f"keep AC at or below {pref_max:.1f}°C, do not change fixed appliance routines, "
                f"and keep the explanation about comfort and routine. If support is limited, just keep life smooth."
            )
        elif tags.get("control") == "confirm_required":
            override_msg = (
                "For this event, I confirm only a comfort-first or tiny adjustment plan: "
                f"keep the AC at or below {pref_max:.1f}°C, avoid noticeable temperature drift, "
                f"finish chores outside {window_text} if possible, and do not make any larger automatic changes."
            )
        else:
            override_msg = (
                "I really don't want the temperature to go above 26°C during this VPP event. "
                "My comfort is the top priority — please keep it below 26°C if at all possible."
            )
        _log_and_return(log_path, persona, event_index, "override_prefill", override_msg)
        return StrategyPreference(
            override_msg,
            {
                "event_index": event_index,
                "source": "override_prefill",
                "candidates": [],
                "selected_strategy": {
                    "id": "override",
                    "label": "override_prefill",
                    "source": "override_prefill",
                    "preference_text": override_msg,
                },
                "calendar_context": calendar_context_for_event(persona, event_index, vpp_context),
                "returned_user_pref": override_msg,
            },
        )

    # Step 1: Generate 3 candidate strategies
    calendar_context = calendar_context_for_event(persona, event_index, vpp_context)
    candidates = generate_vpp_strategy_candidates(
        building, event_index, vpp_context, past_events, persona,
        calendar_context=calendar_context,
    )

    # Step 2: Display all strategies
    appliance_presence = _appliance_control_profile(persona, vpp_context)
    candidate_trace = _candidate_trace_items(candidates, appliance_presence, vpp_context, persona)
    print(f"  ┌─[Strategy Candidates | VPP event {event_index}]{'─'*30}")
    for c in candidates:
        print(f"  │  [{c['id']}] {c['label']}  —  {c['description']}  ({c['tradeoff']})")
        plan_cn = _strategy_appliance_plan_cn(
            str(c.get('id', '')).upper(), appliance_presence, vpp_context, persona
        )
        if plan_cn:
            print(f"  │      Appliance control: {plan_cn}")
    print(f"  └{'─'*56}")

    # Step 3: Select strategy
    rule_selected = _auto_select_strategy(candidates, persona)
    selected = rule_selected
    roleplay_selection_meta = {}

    if human_mode:
        # --- Human-in-the-loop: prompt for selection ---
        auto_id = selected["id"]
        print(f"  ┌─[Choose Strategy | VPP event {event_index}]{'─'*28}")
        print(f"  │  Enter A / B / C to choose a strategy, or type a custom preference.")
        print(f"  │  Press Enter = use the auto recommendation [{auto_id}] {selected['label']}")
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
                pref = _compose_pref_with_appliances(selected, appliance_presence, vpp_context)
                trace = {
                    "event_index": event_index,
                    "source": "human",
                    "candidates": candidate_trace,
                    "selected_strategy": _selected_trace_item(
                        selected,
                        source="human",
                        preference_text=pref,
                        human_input=raw_choice,
                    ),
                    "calendar_context": calendar_context,
                    "returned_user_pref": pref,
                }
                _log_and_return(log_path, persona, event_index, "strategy_human",
                                pref, extra={"selected_id": selected["id"], "human_input": raw_choice,
                                             "candidates": candidate_trace,
                                             "selected_strategy": trace["selected_strategy"]})
                return StrategyPreference(pref, trace)
        elif raw_choice:
            # custom free-text preference
            print(f"  [Strategy Selected  | event={event_index}] → [custom] (human)")
            trace = {
                "event_index": event_index,
                "source": "human_custom",
                "candidates": candidate_trace,
                "selected_strategy": {
                    "id": "custom",
                    "label": "custom",
                    "source": "human_custom",
                    "human_input": raw_choice,
                    "preference_text": raw_choice,
                },
                "calendar_context": calendar_context,
                "returned_user_pref": raw_choice,
            }
            _log_and_return(log_path, persona, event_index, "strategy_human_custom",
                            raw_choice, extra={"human_input": raw_choice,
                                               "candidates": candidate_trace,
                                               "selected_strategy": trace["selected_strategy"]})
            return StrategyPreference(raw_choice, trace)
        else:
            print(f"  [Strategy Selected  | event={event_index}] → [{selected['id']}] {selected['label']} (human→auto)")

    else:
        try:
            selected, roleplay_reason, roleplay_selection_meta = _roleplay_select_strategy(
                candidates,
                persona,
                building=building,
                event_index=event_index,
                vpp_context=vpp_context,
                past_events=past_events,
                appliance_presence=appliance_presence,
                calendar_context=calendar_context,
            )
            reason_suffix = f" | {roleplay_reason}" if roleplay_reason else ""
            print(
                f"  [Strategy Selected  | event={event_index}] → "
                f"[{selected['id']}] {selected['label']} (auto roleplay_llm){reason_suffix}"
            )
        except Exception as exc:
            selected = rule_selected
            roleplay_selection_meta = {"source": "rule_fallback", "error": str(exc)[:120]}
            print(
                f"  [Strategy Selected  | event={event_index}] → "
                f"[{selected['id']}] {selected['label']} (auto rule_fallback: {str(exc)[:60]})"
            )

    pref = _compose_pref_with_appliances(selected, appliance_presence, vpp_context)
    mode_tag = "human_auto" if human_mode else roleplay_selection_meta.get("source", "auto")
    trace = {
        "event_index": event_index,
        "source": mode_tag,
        "candidates": candidate_trace,
        "selected_strategy": _selected_trace_item(
            selected,
            source=mode_tag,
            preference_text=pref,
            selection_meta=roleplay_selection_meta,
        ),
        "calendar_context": calendar_context,
        "returned_user_pref": pref,
    }
    _log_and_return(log_path, persona, event_index, f"strategy_{mode_tag}",
                    pref, extra={"selected_id": selected["id"],
                                 "selection_meta": roleplay_selection_meta,
                                 "calendar_context": calendar_context,
                                 "candidates": candidate_trace,
                                 "selected_strategy": trace["selected_strategy"]})
    return StrategyPreference(pref, trace)


# ---------------------------------------------------------------------------
# VPP Strategy Candidate Generation  (Feature 1)
# ---------------------------------------------------------------------------
_STRATEGY_DEFAULTS = [
    {
        "id": "A", "label": "Comfort first",
        "description": "Keep the setpoint near 25°C and accept higher energy use.",
        "tradeoff": "Best comfort, higher energy",
        "user_pref": (
            "Comfort first: please keep the temperature at 25°C. "
            "I'm willing to use more energy to stay comfortable during this VPP event."
        ),
    },
    {
        "id": "B", "label": "Balanced",
        "description": "Raise to 26°C and finish or defer shiftable appliances.",
        "tradeoff": "Small drift, about 15% savings",
        "user_pref": (
            "Balanced: I'm okay with a brief setpoint rise to 26°C and shifting "
            "shiftable appliances away from the VPP window to reduce peak load."
        ),
    },
    {
        "id": "C", "label": "Energy first",
        "description": "Raise to 27°C and delay all shiftable appliances until after VPP.",
        "tradeoff": "Noticeable drift, about 30% savings",
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
    calendar_context: dict | None = None,
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
        _ap = _appliance_control_profile(persona, vpp_context)
        _present_appliances = [k for k in _APPLIANCE_KEYS if _profile_present(_ap, k)]
        _controllable_appliances = _profile_controllable_names(_ap)
        _fixed_appliances = _profile_fixed_names(_ap)
        _active_appliances_text = (
            "present=" + (",".join(_present_appliances) if _present_appliances else "none")
            + "; controllable=" + (",".join(_controllable_appliances) if _controllable_appliances else "none")
            + "; fixed=" + (",".join(_fixed_appliances) if _fixed_appliances else "none")
        )
        _cal = calendar_context or calendar_context_for_event(persona, event_index, vpp_context)
        _cal_brief = calendar_brief_for_prompt(_cal)
        _tags = persona.get("tags", {}) or {}
        _style_notes = []
        if _tags.get("price") in {"low_incentive", "price_indifferent"}:
            _style_notes.append("Use comfort, routine preservation, and low-risk support as the only user-facing rationale.")
            _style_notes.append("Write user_pref without financial or market language unless the user explicitly asks for it.")
        if _tags.get("control") == "confirm_required":
            _style_notes.append("Write each user_pref as an explicit event-level confirmation boundary, not as open-ended permission.")
        if _tags.get("comfort") == "temp_sensitive":
            _style_notes.append("Keep AC recommendations inside the preferred comfort range unless the user explicitly accepts a tiny drift.")
        _style_text = " ".join(_style_notes) if _style_notes else "Use the persona's normal communication style."
        _learned_notes = build_vpp_preference_memory_notes(past_events, persona)
        _learned_text = " ".join(f"- {note}" for note in _learned_notes) if _learned_notes else "No learned feedback rules yet."

        sys_prompt = (
            "You are an energy management strategy advisor for a smart home VPP demand-response system. "
            "Generate 3 distinct response strategies for the upcoming peak-shaving event. "
            "Strategy A = comfort-first, B = balanced, C = energy-saving. "
            "Tailor them to the user persona and explicitly include appliance control. "
            "Use the user's calendar as a hard preference context: do not propose schedules that would miss "
            "appointments, return-home comfort needs, EV departure readiness, hot-water deadlines, or required chores. "
            "If appliances are available, mention how to handle washer, dishwasher, dryer, water heater, and EV in strategy text. "
            "For fixed/non-DR-adjustable appliances, do not imply the controller can move them; describe them as fixed constraints. "
            'Return ONLY a JSON array of exactly 3 objects, each with keys: '
            '"id" ("A"/"B"/"C"), '
            '"label" (short English label), '
            '"description" (English action summary ≤80 chars), '
            '"tradeoff" (English tradeoff ≤60 chars), '
            '"user_pref" (English preference statement ≤140 chars, will be injected into AC agent prompt).'
        )
        user_msg = (
            f"Building={building}. VPP event #{event_index}. "
            f"Context: {json.dumps(vpp_context, ensure_ascii=False)}. "
            f"Active appliances: {_active_appliances_text}. "
            f"{_cal_brief} "
            f"Style/control notes: {_style_text} "
            f"Learned feedback rules from past events: {_learned_text} "
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
            return _align_candidates_to_appliance_profile(candidates, _ap, vpp_context, persona)
        raise ValueError(f"unexpected shape: {type(candidates)}")
    except Exception as e:
        print(f"  [StrategyGen] LLM failed ({e}), using defaults")
        _ap = _appliance_control_profile(persona, vpp_context)
        return _align_candidates_to_appliance_profile(
            list(_STRATEGY_DEFAULTS), _ap, vpp_context, persona
        )  # copy so mutations don't affect template


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


def _roleplay_select_strategy(
    candidates: list[dict],
    persona: dict,
    *,
    building: str,
    event_index: int,
    vpp_context: dict,
    past_events: list,
    appliance_presence: dict,
    calendar_context: dict | None = None,
) -> tuple[dict, str, dict]:
    """Ask the role-play user LLM to choose one candidate strategy."""
    from energybridge.llm.roleplay_user import RoleplayUserSimulator

    strategy_options = []
    for idx, candidate in enumerate(candidates, start=1):
        strategy_options.append({
            "index": idx,
            "id": candidate.get("id"),
            "label": candidate.get("label"),
            "description": candidate.get("description"),
            "tradeoff": candidate.get("tradeoff"),
            "user_pref": candidate.get("user_pref"),
            "appliance_plan_cn": _strategy_appliance_plan_cn(
                str(candidate.get("id", "")).upper(), appliance_presence, vpp_context, persona
            ),
            "appliance_pref_en": _strategy_appliance_pref_en(
                str(candidate.get("id", "")).upper(), appliance_presence, vpp_context, persona
            ),
        })
    scenario = {
        "building": building,
        "event_index": event_index,
        "vpp_context": vpp_context,
        "calendar_context": calendar_context or calendar_context_for_event(persona, event_index, vpp_context),
        "active_appliances": [k for k in _APPLIANCE_KEYS if _profile_present(appliance_presence, k)],
        "controllable_appliances": _profile_controllable_names(appliance_presence),
        "fixed_non_dr_adjustable_appliances": _profile_fixed_names(appliance_presence),
        "past_events": [
            {
                "id": e.get("id"),
                "score": e.get("score"),
                "label": e.get("label"),
                "comment": e.get("comment", ""),
                "user_input": e.get("user_input", ""),
            }
            for e in (past_events or [])
        ],
        "learned_preference_rules": build_vpp_preference_memory_notes(past_events, persona),
        "instruction": (
            "Choose the option this home user would approve before the VPP event. "
            "The returned choice will be injected into the home agent prompt as the user's live preference."
        ),
    }
    result = RoleplayUserSimulator().choose_strategy(
        persona=persona,
        turn_index=event_index,
        scenario=scenario,
        strategy_options=strategy_options,
    )
    data = result.get("data", {})
    selected_index = int(data.get("selected_index", 0))
    if not 1 <= selected_index <= len(candidates):
        raise ValueError(f"invalid selected_index={selected_index}")
    selected = candidates[selected_index - 1]
    reason = str(data.get("reason", "")).strip()
    return selected, reason, {
        "selected_index": selected_index,
        "approved": data.get("approved"),
        "reason": reason,
        "metrics": result.get("metrics", {}),
        "source": "roleplay_llm",
    }


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
    appliance_summary: dict | None = None,
    log_path: Path | None = None,
    human_mode: bool = False,
    vpp_context: dict | None = None,
    vpp_result_context: dict | None = None,
    policy_control_context: dict | None = None,
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
    calendar_context = calendar_context_for_event(
        persona,
        event_index,
        vpp_context or {"hour": 18.0, "duration_h": 1.0},
    )
    calendar_brief = calendar_brief_for_prompt(calendar_context)
    tags = persona.get("tags", {}) or {}
    schedule = persona.get("schedule", {}) or {}
    appliance_cfg = persona.get("appliances", {}) or {}
    fixed_appliances = _fixed_appliance_constraints(persona)
    ac_cfg = appliance_cfg.get("ac", {}) or {}
    pref_min = float(ac_cfg.get("setpoint_preferred_min_c", persona.get("preferred_temp_min", 24.0)))
    pref_max = float(ac_cfg.get("setpoint_preferred_max_c", persona.get("preferred_temp_max", 26.0)))
    pref_tol = float(ac_cfg.get("temp_tolerance_c", persona.get("temp_tolerance", 1.0)))
    protective_user = (
        tags.get("schedule") == "caregiver"
        or tags.get("comfort") == "temp_sensitive"
        or tags.get("control") in {"confirm_required", "low_auto_accept", "privacy_sensitive"}
        or bool(schedule.get("vulnerable_members"))
    )

    # Human-in-the-loop scoring: print event summary and ask for terminal input
    if human_mode:
        sp_str = f"{agent_setpoint_c:.1f}°C" if agent_setpoint_c else f"{mean_temp_c:.1f}°C"
        print(f"  ╔═[VPP Event {event_index} Satisfaction Score]{'═'*32}")
        print(f"  ║  VPP-window mean indoor temp: {mean_temp_c:.1f}°C   setpoint: {sp_str}")
        print(f"  ║  Today's energy: {energy_kwh_per_day:.2f} kWh   comfort pass rate: {pmv_ok_fraction*100:.0f}%")
        if calendar_context.get("available"):
            print(f"  ║  Calendar constraints: {calendar_context.get('summary', '')[:80]}")
        if agent_reason:
            print(f"  ║  Agent rationale: {agent_reason[:100]}")
        print(f"  ╚{'═'*52}")
        print("  Rate this VPP handling (1=very dissatisfied / 5=very satisfied), press Enter=3:")
        try:
            raw_score = input("  > ").strip()
            score = max(1, min(5, int(raw_score))) if raw_score.isdigit() else 3
        except (EOFError, KeyboardInterrupt):
            score = 3
        print("  Optional: leave brief feedback, or press Enter to skip:")
        try:
            comment = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            comment = ""
        print(f"  [Human Score Selected | event={event_index}] → {score}/5 | {comment or '—'}")
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
        "calendar_context": calendar_context,
        "protective_user_mode": protective_user,
        "fixed_non_dr_adjustable_appliances": fixed_appliances,
        "event_preference_counts_as_confirmation": bool(user_preference_text),
    }
    if vpp_result_context:
        home_state["vpp_result"] = vpp_result_context
    appliance_summary = appliance_summary or {}
    skipped_devices = [
        name for name, info in appliance_summary.items()
        if name in {"washer", "dishwasher", "dryer"} and bool(info.get("present")) and bool(info.get("skipped"))
    ]
    skipped_task_count = len(skipped_devices)
    if skipped_devices:
        home_state["skipped_devices"] = skipped_devices
        home_state["service_rule_violated"] = True

    wh_info = appliance_summary.get("water_heater", {}) if appliance_summary else {}
    water_heater_during_vpp = bool(wh_info.get("present") and wh_info.get("ran_during_vpp"))
    if water_heater_during_vpp:
        home_state["water_heater_during_vpp"] = True
    nonfixed_appliances_during_vpp = [
        name
        for name, info in appliance_summary.items()
        if name in {"washer", "dishwasher", "dryer", "water_heater", "ev"}
        and bool(info.get("present"))
        and bool(info.get("ran_during_vpp"))
        and name not in fixed_appliances
    ]
    policy_control_context = dict(policy_control_context or {})
    policy_action_space = set(policy_control_context.get("action_space_services") or [])
    emitted_policy_services = set(policy_control_context.get("emitted_services") or [])
    if not emitted_policy_services:
        emitted_policy_services |= _services_from_actions(policy_control_context.get("vpp_trigger_actions"))
        for decision in policy_control_context.get("day_decisions") or []:
            if isinstance(decision, dict):
                emitted_policy_services |= _services_from_actions(decision.get("actions"))
                emitted_policy_services |= _services_from_actions(decision.get("raw_appliance_actions"))
    present_required_services: set[str] = set()
    for name, info in appliance_summary.items():
        if name not in _APPLIANCE_KEYS or not isinstance(info, dict) or not bool(info.get("present")):
            continue
        if name in {"washer", "dishwasher", "dryer"}:
            if (
                bool(info.get("skipped"))
                or bool(info.get("ran_during_vpp"))
                or info.get("completed") is False
            ):
                present_required_services.add(name)
        elif name == "water_heater":
            if bool(info.get("ran_during_vpp")) or info.get("ready_at_bath") is False:
                present_required_services.add(name)
        elif name == "ev":
            if bool(info.get("ran_during_vpp")) or info.get("target_reached") is False:
                present_required_services.add(name)
    if policy_action_space:
        present_required_services |= {
            name for name, info in appliance_summary.items()
            if name in policy_action_space
            and isinstance(info, dict)
            and bool(info.get("present"))
        }
    unsupported_policy_services = sorted(
        present_required_services - policy_action_space
    ) if policy_action_space else []
    missing_policy_services = sorted(
        present_required_services - emitted_policy_services
    ) if policy_control_context else []
    if policy_control_context:
        home_state["policy_control_context"] = {
            "method": policy_control_context.get("method", method),
            "objective_source": policy_control_context.get("objective_source", ""),
            "action_space_services": sorted(policy_action_space),
            "emitted_services": sorted(emitted_policy_services),
            "present_required_services": sorted(present_required_services),
            "unsupported_policy_services": unsupported_policy_services,
            "missing_policy_services": missing_policy_services,
            "vpp_trigger_actions": policy_control_context.get("vpp_trigger_actions", {}),
            "occupancy_decisions": policy_control_context.get("occupancy_decisions", []),
        }

    policy_scored_method = (
        method in (
            "agent",
            "agent_pmv",
            "EnergyBridge",
            "hema_agent",
            "rl",
            "rl_ppo_3day",
            "rl_ppo_pref_v2",
            "mpc",
            "mpc_dynamic",
            "mpc_ep",
            "rule_milp",
        )
        or str(method).startswith("rl_")
    )
    if policy_scored_method and agent_setpoint_c:
        if method == "rl" or str(method).startswith("rl_"):
            controller = "RL baseline"
        elif method == "hema_agent":
            controller = "HEMA Agent baseline"
        elif method in ("mpc", "mpc_dynamic", "mpc_ep"):
            controller = "MPC baseline"
        elif method == "rule_milp":
            controller = "Rule+MILP baseline"
        else:
            controller = "LLM agent"
        within_preferred = pref_min - pref_tol <= float(agent_setpoint_c) <= pref_max + pref_tol
        if protective_user and within_preferred:
            action_name = "hold_or_minimal_hvac"
            rationale = (
                f"{controller} kept the cooling setpoint at {agent_setpoint_c}°C, within this "
                f"protective user's preferred range ({pref_min:.1f}-{pref_max:.1f}°C). "
                "Do not treat this status/setpoint log as an aggressive DR temperature raise; "
                "score based on actual comfort, whether the event-specific user preference/confirmation was followed, "
                "consent sensitivity, and preserved routines. "
                f"Controller explanation: {agent_reason[:100]}"
            )
        else:
            action_name = "set_hvac_temperature"
            rationale = (
                f"{controller} set cooling setpoint to {agent_setpoint_c}°C during VPP DR event. "
                f"Controller explanation: {agent_reason[:100]}"
            )
        if calendar_context.get("available"):
            rationale += f" | User calendar context: {calendar_brief[:240]}"
        if user_preference_text:
            rationale += (
                f" | User had expressed/confirmed for this event: {user_preference_text[:120]}. "
                "Treat this as the event-specific confirmation boundary for confirm-required personas."
            )
        if fixed_appliances:
            rationale += (
                " | Fixed/non-DR-adjustable appliances that the controller cannot move: "
                + ", ".join(fixed_appliances)
                + ". Do not blame the controller for their fixed operation; evaluate whether controllable actions stayed within the user's consent and comfort."
            )
        if _low_disruption_strategy_language(persona):
            rationale += (
                " | This is a low-disruption user: evaluate mainly comfort, consent, routine smoothness, and whether the explanation stayed low-pressure. "
                "Do not invent a financial or market pitch if the controller explanation did not contain one. "
                "weak VPP contribution from fixed loads is not by itself a communication failure."
            )
        if vpp_result_context:
            ratio = vpp_result_context.get("achievement_ratio")
            achieved = vpp_result_context.get("achieved")
            achieved_text = "unknown" if achieved is None else str(bool(achieved))
            target_mode = vpp_result_context.get("target_mode", "unknown")
            success_text = vpp_result_context.get("success_text", "")
            achievement_text = vpp_result_context.get("achievement_text", "")
            if ratio is not None:
                rationale += (
                    f" | Event-level VPP result: mode={target_mode}, achieved={achieved_text}, "
                    f"actual_shed={vpp_result_context.get('actual_shed_kwh', 'n/a')}kWh, "
                    f"target_shed={vpp_result_context.get('target_shed_kwh', 'n/a')}kWh, "
                    f"ratio={ratio}; {success_text}. {achievement_text}"
                )
            else:
                target_shed = vpp_result_context.get("target_shed_kwh")
                shed_note = (
                    f"target_shed={target_shed}kWh, actual_shed unavailable without same-run no-DR counterfactual, "
                    if target_shed not in (None, "n/a") else ""
                )
                rationale += (
                    f" | Event-level VPP result: mode={target_mode}, achieved={achieved_text}, "
                    f"{shed_note}actual_kwh={vpp_result_context.get('actual_kwh', 'n/a')}kWh, "
                    f"target_cap={vpp_result_context.get('target_kwh', 'n/a')}kWh; {success_text}. {achievement_text}"
                )
        if not washer_completed:
            rationale += " | NOTE: washing machine task was NOT completed today."
        if washer_during_vpp:
            rationale += " | NOTE: washing machine ran DURING VPP window (added peak load)."
        if water_heater_during_vpp:
            if "water_heater" in fixed_appliances:
                rationale += (
                    " | NOTE: water heater operated during the VPP window because this persona's "
                    "bath routine/device is fixed/non-DR-adjustable; this preserves service but weakens grid response. "
                    "This is a fixed-load limitation, not an Agent scheduling violation."
                )
            else:
                rationale += " | NOTE: water heater ran DURING VPP window (added peak load)."
        if skipped_devices:
            rationale += (
                " | CRITICAL SERVICE VIOLATION: agent skipped required appliance task(s): "
                + ", ".join(skipped_devices)
                + ". User should be very dissatisfied; this should score at the lowest level."
            )
        if policy_control_context:
            rationale += (
                " | Policy action evidence for this method: "
                f"action_space={sorted(policy_action_space)}, "
                f"emitted_services={sorted(emitted_policy_services)}, "
                f"present_required_services={sorted(present_required_services)}, "
                f"vpp_trigger_actions={policy_control_context.get('vpp_trigger_actions', {})}, "
                f"occupancy_ac_modes={policy_control_context.get('occupancy_decisions', [])}. "
                "Only count appliance service as method-controlled when it appears in emitted policy actions; "
                "do not credit baseline routines or simulator default completion as policy success."
            )
            if missing_policy_services:
                rationale += (
                    " | CRITICAL APPLIANCE STRATEGY FAILURE: required present controllable appliances "
                    "have no emitted policy strategy/action: "
                    + ", ".join(missing_policy_services)
                    + ". This is a service failure. The role-play user must assign a punitive low overall score, normally 1/5, even if comfort or VPP energy target looks acceptable."
                )
        if str(method).startswith("rl") or str(policy_control_context.get("objective_source", "")).startswith("rl_"):
            rationale += (
                " | CRITICAL RL SCORING RULE: this benchmark evaluates the raw RL policy only. "
                "Do not give RL credit for fallback/default appliance behavior or routine completion that was not emitted by the RL action. "
                "If present required appliances are outside the RL action space or missing from emitted RL actions, treat the task as incomplete and score it as a punitive service failure even if the simulator service outcome later looks completed."
            )
            if unsupported_policy_services:
                rationale += (
                    " Unsupported-by-RL required services: "
                    + ", ".join(unsupported_policy_services)
                    + "."
                )
            if missing_policy_services:
                rationale += (
                    " Required services with no emitted RL control action: "
                    + ", ".join(missing_policy_services)
                    + "."
                )
        control_plan = {
            "action": action_name,
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
        if calendar_context.get("available"):
            control_plan["rationale"] += f" | User calendar context: {calendar_brief[:240]}"

    safety = {"status": "approved", "reason": "Within safe operation bounds."}

    if missing_policy_services:
        result = {
            "score": 1,
            "comfort_score": 2,
            "energy_score": 1,
            "vpp_score": 1,
            "label": "very_dissatisfied",
            "comment": (
                "Required present controllable appliance(s) had no emitted policy strategy/action "
                f"(missing: {', '.join(missing_policy_services)}; "
                f"unsupported_by_action_space: {', '.join(unsupported_policy_services) or 'none'}). "
                "Baseline routine/default completion was not credited as method success."
            ),
            "zone_comfort_scores": None,
            "source": "roleplay_llm",
            "policy_service_guard": {
                "missing_policy_services": missing_policy_services,
                "unsupported_policy_services": unsupported_policy_services,
                "emitted_policy_services": sorted(emitted_policy_services),
                "present_required_services": sorted(present_required_services),
            },
        }
        if str(method).startswith("rl") or str(policy_control_context.get("objective_source", "")).startswith("rl_"):
            result["rl_policy_service_guard"] = result["policy_service_guard"]
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
                    "energy": result["energy_score"],
                    "vpp": result["vpp_score"],
                },
                "comment": result.get("comment", ""),
                "washer_completed": washer_completed,
                "washer_during_vpp": washer_during_vpp,
                "skipped_devices": skipped_devices,
                "source": result.get("source", "?"),
            })
        return result

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
        if vpp_result_context and vpp_result_context.get("achieved") is True:
            comment_lower = str(result.get("comment", "")).lower()
            misleading_miss = any(
                token in comment_lower
                for token in ("missed", "failed", "not met", "not achieved")
            )
            severe_service_issue = bool(
                skipped_task_count or nonfixed_appliances_during_vpp or missing_policy_services
            )
            if (misleading_miss or result["vpp_score"] <= 2) and not severe_service_issue:
                result["vpp_score"] = max(result["vpp_score"], 4)
                result["energy_score"] = max(result["energy_score"], 3)
                if result["comfort_score"] >= 4 and result["score"] < 4:
                    result["score"] = 4
                    result["label"] = "satisfied"
                if misleading_miss:
                    result["comment"] = "VPP target achieved; comfort/routine were preserved."
                result["factual_consistency_guard"] = "corrected_achieved_vpp_missed_label"
        if skipped_task_count > 0:
            skipped_names = ", ".join(skipped_devices)
            result.update({
                "score": 1,
                "comfort_score": min(result["comfort_score"], 2),
                "energy_score": min(result["energy_score"], 2),
                "vpp_score": 1,
                "label": "very_dissatisfied",
                "comment": (
                    f"Required appliance task(s) were skipped ({skipped_names}); "
                    "this violates the user's service rule."
                ),
            })
        if (
            _low_disruption_strategy_language(persona)
            and fixed_appliances
            and result["score"] < 4
            and result["comfort_score"] >= 4
            and agent_setpoint_c is not None
            and pref_min - pref_tol <= float(agent_setpoint_c) <= pref_max + pref_tol
            and vpp_result_context
            and vpp_result_context.get("achieved") is False
            and not nonfixed_appliances_during_vpp
        ):
            result["score"] = 4
            result["label"] = "satisfied"
            result["comment"] = (
                "Comfort/consent preserved; fixed loads limited VPP."
            )
            result["fixed_constraint_satisfaction_guard"] = (
                "overall_user_satisfaction_not_penalized_for_fixed_non_dr_loads"
            )
    except Exception as e:
        result = _rule_score(persona, mean_temp_c, pmv_ok_fraction, energy_kwh_per_day,
                             zone_group_temps, washer_completed, washer_during_vpp,
                             skipped_task_count, skipped_devices)
        result["source"] = "rule_based_fallback"

    if missing_policy_services:
        result["score"] = 1
        result["comfort_score"] = min(result["comfort_score"], 2)
        result["energy_score"] = 1
        result["vpp_score"] = 1
        result["label"] = "very_dissatisfied"
        result["comment"] = (
            "Required present controllable appliance(s) had no emitted policy strategy/action "
            f"(missing: {', '.join(missing_policy_services)}; "
            f"unsupported_by_action_space: {', '.join(unsupported_policy_services) or 'none'}). "
            "Baseline routine/default completion was not credited as method success."
        )
        result["policy_service_guard"] = {
            "missing_policy_services": missing_policy_services,
            "unsupported_policy_services": unsupported_policy_services,
            "emitted_policy_services": sorted(emitted_policy_services),
            "present_required_services": sorted(present_required_services),
        }
        if str(method).startswith("rl") or str(policy_control_context.get("objective_source", "")).startswith("rl_"):
            result["rl_policy_service_guard"] = result["policy_service_guard"]

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
            "skipped_devices": skipped_devices,
            "source": result.get("source", "?"),
        })

    return result
