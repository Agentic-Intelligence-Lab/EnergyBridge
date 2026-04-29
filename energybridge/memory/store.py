"""Simple JSON memory store for EnergyBridge local demo."""

from __future__ import annotations

import json
from pathlib import Path


def _default_memory() -> dict:
    return {
        "hard_constraints": {},
        "stable_preferences": {},
        "contextual_preferences": {},
        "episodic_logs": [],
    }


def _running_average(previous_value: float | None, current_value: float, count: int) -> float:
    if previous_value is None:
        return round(current_value, 3)
    return round(((previous_value * count) + current_value) / (count + 1), 3)


def load_memory(path: str = "logs/memory.json") -> dict:
    memory_path = Path(path)
    if not memory_path.exists():
        return _default_memory()

    with memory_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        return _default_memory()
    return data


def save_memory(memory: dict, path: str = "logs/memory.json") -> None:
    memory_path = Path(path)
    memory_path.parent.mkdir(parents=True, exist_ok=True)
    with memory_path.open("w", encoding="utf-8") as f:
        json.dump(memory, f, ensure_ascii=False, indent=2)


def update_memory(memory: dict, episode: dict) -> dict:
    updated = dict(memory or _default_memory())
    updated.setdefault("hard_constraints", {})
    updated.setdefault("stable_preferences", {})
    updated.setdefault("contextual_preferences", {})
    updated.setdefault("episodic_logs", [])

    user_preferences = episode.get("user_preferences", {})
    if user_preferences:
        stable_preferences = updated["stable_preferences"]
        observation_count = int(stable_preferences.get("observation_count", 0) or 0)

        for field in [
            "comfort_priority",
            "cost_priority",
            "grid_priority",
            "preferred_temp_min",
            "preferred_temp_max",
        ]:
            current_value = user_preferences.get(field)
            if current_value is None:
                continue
            stable_preferences[field] = _running_average(
                stable_preferences.get(field),
                float(current_value),
                observation_count,
            )

        for field in ["allow_pre_cooling", "allow_temp_drift"]:
            current_value = bool(user_preferences.get(field, False))
            true_count_key = f"{field}_true_count"
            true_count = int(stable_preferences.get(true_count_key, 0) or 0)
            if current_value:
                true_count += 1
            stable_preferences[true_count_key] = true_count
            stable_preferences[field] = true_count >= ((observation_count + 1) / 2)

        stable_preferences["observation_count"] = observation_count + 1

    updated["episodic_logs"].append(episode)
    updated["episodic_logs"] = updated["episodic_logs"][-50:]
    return updated
