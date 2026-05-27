"""
Persona schema definitions for EnergyBridge role-play evaluation.

Based on the 6-dimension behavioral tag framework from:
  家庭用户行为角色建模.md

Each persona JSON file must conform to the schema defined here.
Use validate_persona() to check a loaded dict.
Use to_legacy_dict() to convert to the flat format expected by family_runner.py.
"""
from __future__ import annotations

VALID_TAGS: dict[str, list[str]] = {
    "schedule": ["regular_commuter", "stay_at_home", "night_owl", "irregular", "caregiver"],
    "comfort":  ["temp_tolerant", "normal_comfort", "temp_sensitive", "low_control_tolerance"],
    "task":     ["flexible", "semi_rigid", "rigid", "ev_constrained"],
    "price":    ["price_sensitive", "needs_explanation", "low_incentive", "event_fatigue"],
    "control":  ["high_trust_auto", "suggestion_first", "confirm_required",
                 "privacy_sensitive", "low_auto_accept"],
    "grid_value": ["evening_peak", "stable_flex", "uncertain_flex", "short_peak_cut", "low_value"],
}

_REQUIRED_KEYS = {
    "schema_version", "id", "display_name", "description",
    "tags", "preferences", "schedule", "appliances", "llm_prompts", "meta",
}
_REQUIRED_PREFERENCE_KEYS = {
    "scoring_weights", "vpp_override_prob",
}
# Optional preference keys — defaults applied in to_legacy_dict if absent
_OPTIONAL_PREFERENCE_DEFAULTS = {
    "temp_preferred_min": 24.0,
    "temp_preferred_max": 26.0,
    "temp_tolerance_c":   1.5,
}
_REQUIRED_SCORING_KEYS = {"comfort", "energy", "vpp"}
_REQUIRED_PROMPT_KEYS  = {"system_prompt", "agent_context", "example_responses"}


def validate_persona(data: dict) -> dict:
    """Validate a persona dict. Returns it if valid, raises ValueError otherwise."""
    missing = _REQUIRED_KEYS - set(data.keys())
    if missing:
        raise ValueError(f"Persona '{data.get('id', '?')}' missing keys: {missing}")
    tags = data.get("tags", {})
    for dim, valid in VALID_TAGS.items():
        val = tags.get(dim)
        if val is None:
            raise ValueError(f"Persona '{data['id']}' missing tag: {dim}")
        if val not in valid:
            raise ValueError(
                f"Persona '{data['id']}' tag '{dim}'='{val}' not in {valid}"
            )
    prefs = data.get("preferences", {})
    missing_p = _REQUIRED_PREFERENCE_KEYS - set(prefs.keys())
    if missing_p:
        raise ValueError(f"Persona '{data['id']}' preferences missing: {missing_p}")
    weights = prefs.get("scoring_weights", {})
    if _REQUIRED_SCORING_KEYS - set(weights.keys()):
        raise ValueError(f"Persona '{data['id']}' scoring_weights incomplete")
    prompts = data.get("llm_prompts", {})
    if _REQUIRED_PROMPT_KEYS - set(prompts.keys()):
        raise ValueError(f"Persona '{data['id']}' llm_prompts incomplete")
    return data


def _extract_shiftable(appl: dict, name: str, defaults: dict) -> dict:
    """Extract a shiftable-task appliance config from the appliances dict."""
    raw = appl.get(name, {})
    if not isinstance(raw, dict):
        raw = {}
    return {
        "present":       bool(raw.get("present", defaults.get("present", True))),
        "earliest_h":    float(raw.get("earliest_h", defaults["earliest_h"])),
        "latest_h":      float(raw.get("latest_h",   defaults["latest_h"])),
        "preferred_h":   float(raw.get("preferred_h", defaults["preferred_h"])),
        "duration_h":    float(raw.get("duration_h",  defaults["duration_h"])),
        "power_kw":      float(raw.get("power_kw",    defaults["power_kw"])),
        "shiftable":     bool(raw.get("shiftable", True)),
        "dr_adjustable": bool(raw.get("dr_adjustable", True)),
    }


def _extract_water_heater(appl: dict) -> dict:
    raw = appl.get("water_heater", {})
    if not isinstance(raw, dict):
        raw = {}
    return {
        "present":                  bool(raw.get("present", True)),
        "rated_kw":                 float(raw.get("rated_kw", 2.0)),
        "bath_required_h":          float(raw.get("bath_required_h", 21.0)),
        "dr_adjustable":            bool(raw.get("dr_adjustable", True)),
        "pre_heat_window_start_h":  float(raw.get("pre_heat_window_start_h", 15.0)),
        "pre_heat_window_end_h":    float(raw.get("pre_heat_window_end_h", 18.0)),
    }


def _extract_ev(appl: dict) -> dict:
    raw = appl.get("ev", {})
    if not isinstance(raw, dict):
        raw = {}
    return {
        "present":         bool(raw.get("present", False)),
        "charger_kw":      float(raw.get("charger_kw", 7.0)),
        "capacity_kwh":    float(raw.get("capacity_kwh", 60.0)),
        "target_soc":      float(raw.get("target_soc", 0.80)),
        "min_soc":         float(raw.get("min_soc", 0.15)),
        "arrival_h":       float(raw.get("arrival_h", 18.0)),
        "departure_h":     float(raw.get("departure_h", 7.5)),
        "daily_drive_kwh": float(raw.get("daily_drive_kwh", 8.0)),
    }


def _extract_refrigerator(appl: dict) -> dict:
    raw = appl.get("refrigerator", {})
    if not isinstance(raw, dict):
        raw = {}
    return {
        "present":  bool(raw.get("present", True)),
        "power_kw": float(raw.get("power_kw", 0.15)),
    }


def to_legacy_dict(persona: dict) -> dict:
    """Convert JSON-format persona to the flat dict expected by family_runner.py.

    All appliance sub-dicts are extracted independently so each can be
    controlled separately in the simulation.
    """
    prefs    = persona["preferences"]
    appl     = persona.get("appliances", {})
    prompts  = persona["llm_prompts"]
    tags     = persona["tags"]

    tmin = prefs.get("temp_preferred_min", _OPTIONAL_PREFERENCE_DEFAULTS["temp_preferred_min"])
    tmax = prefs.get("temp_preferred_max", _OPTIONAL_PREFERENCE_DEFAULTS["temp_preferred_max"])
    sw   = prefs["scoring_weights"]

    # --- per-appliance extraction (independent, no cross-coupling) ---
    washer      = _extract_shiftable(appl, "washer",     {"present": True,
                      "earliest_h": 8.0, "latest_h": 22.0, "preferred_h": 14.0,
                      "duration_h": 2.0, "power_kw": 1.5})
    dishwasher  = _extract_shiftable(appl, "dishwasher", {"present": False,
                      "earliest_h": 19.0, "latest_h": 7.0, "preferred_h": 20.0,
                      "duration_h": 1.5, "power_kw": 1.2})
    dryer       = _extract_shiftable(appl, "dryer",      {"present": False,
                      "earliest_h": 8.0, "latest_h": 20.0, "preferred_h": 10.0,
                      "duration_h": 1.0, "power_kw": 2.5})
    water_heater  = _extract_water_heater(appl)
    ev            = _extract_ev(appl)
    refrigerator  = _extract_refrigerator(appl)

    return {
        "id": persona["id"],
        "display_name": persona["display_name"],
        "speaking_language": "en",
        "schedule_tag":    tags["schedule"],
        "comfort_tag":     tags["comfort"],
        "task_tag":        tags["task"],
        "price_tag":       tags["price"],
        "control_tag":     tags["control"],
        "grid_value_tag":  tags["grid_value"],
        "preferred_temp_min": tmin,
        "preferred_temp_max": tmax,
        "temp_tolerance":     prefs.get("temp_tolerance_c", _OPTIONAL_PREFERENCE_DEFAULTS["temp_tolerance_c"]),
        "scoring_weights":    sw,
        "vpp_override_prob":  prefs["vpp_override_prob"],
        # appliances — each independently accessible
        "washer":        washer,
        "dishwasher":    dishwasher,
        "dryer":         dryer,
        "water_heater":  water_heater,
        "ev":            ev,
        "refrigerator":  refrigerator,
        # raw appliances dict preserved for ApplianceSuite
        "appliances":    appl,
        "stable_preferences": {
            "comfort_priority": sw["comfort"],
            "cost_priority":    sw["energy"],
            "grid_priority":    sw["vpp"],
            "preferred_temp_min": tmin,
            "preferred_temp_max": tmax,
            "allow_pre_cooling": tags["comfort"] != "low_control_tolerance",
            "allow_temp_drift":  tags["comfort"] in ("temp_tolerant", "normal_comfort"),
        },
        "persona_prompt":       prompts["agent_context"],
        "roleplay_user_prompt": prompts["system_prompt"],
    }


def persona_user_pref_string(persona: dict) -> str:
    """Generate a static user_pref string for HVAC agent prompts.

    Accepts JSON-format or legacy flat dict.
    """
    p = to_legacy_dict(persona) if "preferences" in persona else persona
    tmin = p.get("preferred_temp_min", 23.0)
    tmax = p.get("preferred_temp_max", 26.0)
    w    = p.get("scoring_weights", {"comfort": 0.5, "energy": 0.3, "vpp": 0.2})
    name = p.get("display_name", p.get("id", "User"))
    return (
        f"[User: {name}] "
        f"Comfort priority={w['comfort']:.1f}, "
        f"Cost priority={w['energy']:.1f}, "
        f"Grid priority={w['vpp']:.1f}. "
        f"Preferred temp: {tmin}–{tmax}°C. "
        f"Respond in English."
    )
