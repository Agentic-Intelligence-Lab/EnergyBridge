"""Deterministic parser that converts user text into preference weights and bounds."""

from __future__ import annotations


def parse_user_preference(user_input: str) -> dict:
    text = (user_input or "").lower()

    comfort_keywords = ["comfort", "comfortable"]
    cost_keywords = ["save", "savings", "cheap"]
    grid_keywords = ["grid", "demand response", "peak shaving"]
    pre_cool_keywords = ["pre-cool", "pre cool", "pre_cooling", "pre-cooling"]
    drift_keywords = ["drift", "float"]

    comfort_priority = 0.5
    cost_priority = 0.3
    grid_priority = 0.2

    if any(k in text for k in comfort_keywords):
        comfort_priority += 0.2
    if any(k in text for k in cost_keywords):
        cost_priority += 0.2
    if any(k in text for k in grid_keywords):
        grid_priority += 0.25

    total = comfort_priority + cost_priority + grid_priority
    comfort_priority /= total
    cost_priority /= total
    grid_priority /= total

    allow_pre_cooling = any(k in text for k in pre_cool_keywords) or ("briefly" in text)
    allow_temp_drift = any(k in text for k in drift_keywords) or ("briefly" in text)

    preferred_temp_min = 24.0
    preferred_temp_max = 26.0
    strong_comfort_mentioned = "very comfortable" in text
    if strong_comfort_mentioned:
        preferred_temp_min = 23.5
        preferred_temp_max = 25.5

    return {
        "comfort_priority": round(comfort_priority, 3),
        "cost_priority": round(cost_priority, 3),
        "grid_priority": round(grid_priority, 3),
        "preferred_temp_min": preferred_temp_min,
        "preferred_temp_max": preferred_temp_max,
        "allow_pre_cooling": allow_pre_cooling,
        "allow_temp_drift": allow_temp_drift,
        "mentioned_preferences": {
            "comfort_priority": any(k in text for k in comfort_keywords),
            "cost_priority": any(k in text for k in cost_keywords),
            "grid_priority": any(k in text for k in grid_keywords),
            "allow_pre_cooling": any(k in text for k in pre_cool_keywords) or ("briefly" in text),
            "allow_temp_drift": any(k in text for k in drift_keywords) or ("briefly" in text),
            "preferred_temp_bounds": strong_comfort_mentioned,
        },
    }


def merge_preferences_with_memory(parsed_preferences: dict, memory: dict | None) -> dict:
    stable_preferences = (memory or {}).get("stable_preferences", {})
    session_summary = (memory or {}).get("session_summary", {})
    recent_preferences = session_summary.get("recent_preferences", {}) if isinstance(session_summary, dict) else {}
    mentioned = parsed_preferences.get("mentioned_preferences", {})
    merged = dict(parsed_preferences)

    for field in ["comfort_priority", "cost_priority", "grid_priority"]:
        if not mentioned.get(field, False) and field in recent_preferences:
            merged[field] = recent_preferences[field]
        elif not mentioned.get(field, False) and field in stable_preferences:
            merged[field] = stable_preferences[field]

    for field in ["allow_pre_cooling", "allow_temp_drift"]:
        if not mentioned.get(field, False) and field in recent_preferences:
            merged[field] = recent_preferences[field]
        elif not mentioned.get(field, False) and field in stable_preferences:
            merged[field] = stable_preferences[field]

    if not mentioned.get("preferred_temp_bounds", False):
        if "preferred_temp_min" in recent_preferences:
            merged["preferred_temp_min"] = recent_preferences["preferred_temp_min"]
        if "preferred_temp_max" in recent_preferences:
            merged["preferred_temp_max"] = recent_preferences["preferred_temp_max"]
        if "preferred_temp_min" in stable_preferences:
            merged["preferred_temp_min"] = stable_preferences["preferred_temp_min"]
        if "preferred_temp_max" in stable_preferences:
            merged["preferred_temp_max"] = stable_preferences["preferred_temp_max"]

    return merged
