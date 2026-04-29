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
        updated["stable_preferences"].update(
            {
                "preferred_temp_min": user_preferences.get("preferred_temp_min"),
                "preferred_temp_max": user_preferences.get("preferred_temp_max"),
                "allow_temp_drift": user_preferences.get("allow_temp_drift"),
            }
        )

    updated["episodic_logs"].append(episode)
    updated["episodic_logs"] = updated["episodic_logs"][-50:]
    return updated
