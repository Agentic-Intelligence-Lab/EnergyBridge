"""Simple JSON memory store for EnergyBridge local demo."""

from __future__ import annotations

import json
from pathlib import Path


def _default_memory() -> dict:
    return {
        "hard_constraints": {},
        "stable_preferences": {},
        "contextual_preferences": {},
        "session_summary": {
            "summary_text": "",
            "current_round_summary": {},
            "rolling_round_summaries": [],
            "recent_preferences": {},
            "recent_grid_demand": {},
            "recent_control_plan": {},
            "recent_safety_report": {},
            "episode_count": 0,
        },
        "episodic_logs": [],
    }


def _running_average(previous_value: float | None, current_value: float, count: int) -> float:
    if previous_value is None:
        return round(current_value, 3)
    return round(((previous_value * count) + current_value) / (count + 1), 3)


def _compact_text(value: object, limit: int = 120) -> str:
    text = "" if value is None else str(value)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _build_round_summary(episode: dict, round_index: int) -> dict:
    user_preferences = dict(episode.get("user_preferences", {}) or {})
    grid_demand = dict(episode.get("grid_demand", {}) or {})
    control_plan = dict(episode.get("control_plan", {}) or {})
    safety_report = dict(episode.get("safety_report", {}) or {})

    parts = []
    if episode.get("user_input"):
        parts.append(f"User intent: {_compact_text(episode.get('user_input'), 80)}")

    if user_preferences:
        preference_bits = []
        for field in ["comfort_priority", "cost_priority", "grid_priority"]:
            if field in user_preferences:
                preference_bits.append(f"{field}={user_preferences[field]}")
        if preference_bits:
            parts.append("Preferences: " + ", ".join(preference_bits))

    if grid_demand:
        parts.append(
            "Grid signal: "
            + ", ".join(
                [
                    f"type={grid_demand.get('type', 'unknown')}",
                    f"price={grid_demand.get('price_level', 'unknown')}",
                    f"target={grid_demand.get('target_reduction_kw', 'n/a')}kW",
                ]
            )
        )

    if control_plan:
        parts.append(
            "Control: "
            + ", ".join(
                [
                    f"action={control_plan.get('action', 'unknown')}",
                    f"setpoint={control_plan.get('setpoint', 'n/a')}",
                    f"duration={control_plan.get('duration_minutes', 'n/a')}min",
                ]
            )
        )

    if safety_report:
        parts.append(f"Safety: {'passed' if safety_report.get('safe', False) else 'rejected'}")

    if episode.get("final_response"):
        parts.append(f"Conclusion: {_compact_text(episode.get('final_response'), 100)}")

    return {
        "round_index": round_index,
        "summary_text": " | ".join(parts),
        "user_input": _compact_text(episode.get("user_input", ""), 160),
        "user_preferences": user_preferences,
        "grid_demand": grid_demand,
        "control_plan": control_plan,
        "safety_report": safety_report,
        "final_response": _compact_text(episode.get("final_response", ""), 160),
    }


def _build_session_summary(memory: dict) -> dict:
    episodic_logs = list(memory.get("episodic_logs", []) or [])
    stable_preferences = dict(memory.get("stable_preferences", {}) or {})

    if not episodic_logs:
        return _default_memory()["session_summary"]

    current_round_summary = _build_round_summary(episodic_logs[-1], len(episodic_logs))
    rolling_round_summaries = [
        _build_round_summary(episode, index)
        for index, episode in enumerate(episodic_logs[-4:-1], start=max(1, len(episodic_logs) - 3))
    ]

    parts = [f"Current round: {current_round_summary.get('summary_text', '')}"]
    if rolling_round_summaries:
        parts.append(
            "Recent 3 rounds: "
            + " || ".join(
                f"round {summary.get('round_index', '?')} {summary.get('summary_text', '')}"
                for summary in rolling_round_summaries
            )
        )

    if stable_preferences:
        stable_bits = []
        for field in ["comfort_priority", "cost_priority", "grid_priority", "preferred_temp_min", "preferred_temp_max"]:
            if field in stable_preferences:
                stable_bits.append(f"{field}={stable_preferences[field]}")
        if stable_bits:
            parts.append("Stable profile: " + ", ".join(stable_bits))

    return {
        "summary_text": " | ".join(parts),
        "current_round_summary": current_round_summary,
        "rolling_round_summaries": rolling_round_summaries,
        "recent_preferences": current_round_summary.get("user_preferences", {}),
        "recent_grid_demand": current_round_summary.get("grid_demand", {}),
        "recent_control_plan": current_round_summary.get("control_plan", {}),
        "recent_safety_report": current_round_summary.get("safety_report", {}),
        "episode_count": len(episodic_logs),
    }


def load_memory(path: str = "logs/memory.json") -> dict:
    memory_path = Path(path)
    if not memory_path.exists():
        return _default_memory()

    with memory_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if not isinstance(data, dict):
        return _default_memory()
    data.setdefault("hard_constraints", {})
    data.setdefault("stable_preferences", {})
    data.setdefault("contextual_preferences", {})
    data.setdefault("session_summary", _default_memory()["session_summary"])
    data.setdefault("episodic_logs", [])
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
    updated.setdefault("session_summary", _default_memory()["session_summary"])
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
    updated["session_summary"] = _build_session_summary(updated)
    return updated
