"""Fixed multi-user household scenario loading for EnergyBridge."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .calendar import attach_calendar, hourly_occupancy_from_persona


ROLEPLAY_DIR = Path(__file__).resolve().parent
PERSONA_DIR = ROLEPLAY_DIR / "personas"
HOUSEHOLD_DIR = ROLEPLAY_DIR / "households"


def household_candidate_paths(household_arg: str, household_dir: Path = HOUSEHOLD_DIR) -> list[Path]:
    raw = Path(household_arg)
    candidates = [raw]
    if raw.suffix != ".json":
        candidates.extend([
            household_dir / f"{household_arg}.json",
            household_dir / household_arg / "household.json",
        ])
    return candidates


def load_household_config(household_arg: str, household_dir: Path = HOUSEHOLD_DIR) -> dict[str, Any]:
    for candidate in household_candidate_paths(household_arg, household_dir):
        if candidate.exists() and candidate.is_file():
            with candidate.open(encoding="utf-8") as fh:
                data = json.load(fh)
            data.setdefault("_source_path", str(candidate))
            return data
    checked = ", ".join(str(path) for path in household_candidate_paths(household_arg, household_dir))
    raise FileNotFoundError(f"Household '{household_arg}' not found. Checked: {checked}")


def list_household_ids(household_dir: Path = HOUSEHOLD_DIR) -> list[str]:
    return sorted(path.stem for path in household_dir.glob("*.json"))


def load_household_member_personas(
    household: dict[str, Any],
    personas_dir: Path = PERSONA_DIR,
) -> list[dict[str, Any]]:
    members: list[dict[str, Any]] = []
    for member in household.get("members") or []:
        persona_id = str(member.get("persona_id", "")).strip()
        if not persona_id:
            raise ValueError(f"Household {household.get('id')} has a member without persona_id")
        path = personas_dir / f"{persona_id}.json"
        if not path.exists():
            raise FileNotFoundError(f"Persona '{persona_id}' for household {household.get('id')} not found at {path}")
        persona = attach_calendar(json.loads(path.read_text(encoding="utf-8")), personas_dir)
        persona = dict(persona)
        persona["household_member"] = {
            "member_id": member.get("member_id", persona_id),
            "household_role": member.get("household_role", ""),
            "persona_id": persona_id,
            "decision_weight": float(member.get("decision_weight", 1.0) or 1.0),
        }
        members.append(persona)
    if len(members) < 2:
        raise ValueError(f"Household {household.get('id')} must contain at least two members")
    return members


def build_household_persona(
    household: dict[str, Any],
    member_personas: list[dict[str, Any]],
    *,
    days: int = 7,
) -> dict[str, Any]:
    """Return an aggregate persona-like dict for FamilyRunner.

    The runner and scorer already understand a single persona object with
    appliances, preferences, prompts, and calendar.  A household is therefore a
    reproducible "large user" whose calendar occupancy is the max overlay of its
    member calendars.
    """
    preferences = dict(household.get("preferences") or {})
    preferences.setdefault("scoring_weights", _aggregate_scoring_weights(member_personas))

    prompt = str(
        ((household.get("llm_prompts") or {}).get("system_prompt"))
        or household.get("household_prompt")
        or _default_household_prompt(household, member_personas)
    )
    appliance_config = dict(household.get("appliances") or {})
    if not appliance_config:
        appliance_config = _merge_member_appliances(member_personas)

    out = {
        "schema_version": "household_persona_v1",
        "id": household["id"],
        "display_name": household.get("display_name", household["id"]),
        "description": household.get("description", ""),
        "tags": dict(household.get("tags") or {"schedule": "multi_user_overlay"}),
        "preferences": preferences,
        "schedule": {
            "occupancy_pattern": "multi_user_overlay",
            "calendar_merge_policy": "occupied_if_any_member_home",
            "member_count": len(member_personas),
        },
        "appliances": appliance_config,
        "members": [
            {
                "member_id": p.get("household_member", {}).get("member_id", p.get("id")),
                "household_role": p.get("household_member", {}).get("household_role", ""),
                "persona_id": p.get("id"),
                "display_name": p.get("display_name", p.get("id")),
            }
            for p in member_personas
        ],
        "llm_prompts": {
            "system_prompt": prompt,
            "agent_context": household.get("agent_context", prompt),
        },
        "calendar": merge_member_calendars(household, member_personas, days=days),
        "meta": {
            "persona_type": "multi_user_household",
            "household_source_path": household.get("_source_path", ""),
            "calendar_merge_policy": "union_home_occupancy_max",
            "appliance_config_policy": household.get("appliance_config_policy", "maximal_shared_device_set"),
            "scoring_policy": household.get("scoring_policy", {}),
        },
    }
    return out


def merge_member_calendars(
    household: dict[str, Any],
    member_personas: list[dict[str, Any]],
    *,
    days: int = 7,
) -> dict[str, Any]:
    days = max(1, int(days))
    member_profiles: list[list[list[float]]] = []
    for persona in member_personas:
        profile = hourly_occupancy_from_persona(persona, days)
        if not profile:
            profile = [[1.0] * 24 for _ in range(days)]
        member_profiles.append(profile)

    household_occupancy: list[list[float]] = []
    member_occupancy: dict[str, list[list[float]]] = {}
    for persona, profile in zip(member_personas, member_profiles):
        member_id = str(persona.get("household_member", {}).get("member_id", persona.get("id")))
        member_occupancy[member_id] = profile
    for day_idx in range(days):
        row: list[float] = []
        for hour in range(24):
            row.append(round(max(profile[day_idx][hour] for profile in member_profiles), 4))
        household_occupancy.append(row)

    day_records: list[dict[str, Any]] = []
    for day_idx in range(1, min(days, 7) + 1):
        events: list[dict[str, Any]] = []
        member_constraints: dict[str, Any] = {}
        appliance_deadlines: dict[str, list[str]] = {}
        vulnerable_home = False
        weekday = ""
        day_type = ""
        for persona in member_personas:
            member = persona.get("household_member", {})
            member_id = str(member.get("member_id", persona.get("id")))
            day = _calendar_day(persona, day_idx)
            if not day:
                continue
            weekday = weekday or str(day.get("weekday", ""))
            day_type = day_type or str(day.get("day_type", ""))
            for event in day.get("events") or []:
                item = dict(event)
                item["member_id"] = member_id
                item["persona_id"] = persona.get("id")
                item["member_role"] = member.get("household_role", "")
                item["title"] = f"{member_id}: {item.get('title', '')}"
                events.append(item)
            constraints = dict(day.get("constraints") or {})
            member_constraints[member_id] = constraints
            vulnerable_home = vulnerable_home or bool(constraints.get("vulnerable_member_home"))
            for appliance, deadline in dict(constraints.get("appliance_deadlines") or {}).items():
                appliance_deadlines.setdefault(str(appliance), []).append(f"{member_id}: {deadline}")
        events.sort(key=lambda item: (float(item.get("start_h", 0.0) or 0.0), str(item.get("member_id", ""))))
        day_records.append({
            "day": day_idx,
            "weekday": weekday,
            "day_type": day_type or "weekday",
            "summary": (
                f"{household.get('display_name', household.get('id'))}: overlay of "
                f"{len(member_personas)} member calendars; home is occupied when any member is home."
            ),
            "events": events,
            "constraints": {
                "home_occupancy_policy": "occupied_if_any_member_home",
                "member_constraints": member_constraints,
                "appliance_deadlines": appliance_deadlines,
                "vulnerable_member_home": vulnerable_home,
            },
        })

    return {
        "schema_version": "household_calendar_v1",
        "household_id": household.get("id"),
        "source": "fixed_household_member_calendar_overlay",
        "timezone": "Asia/Shanghai",
        "week_start": "Sunday",
        "description": "Deterministic overlay of member persona calendars. Occupancy is max over members.",
        "member_persona_ids": [p.get("id") for p in member_personas],
        "household_occupancy_hourly": household_occupancy,
        "member_occupancy_hourly": member_occupancy,
        "days": day_records,
    }


def _calendar_day(persona: dict[str, Any], day_idx: int) -> dict[str, Any] | None:
    days = ((persona.get("calendar") or {}).get("days") or [])
    if not days:
        return None
    day = next((item for item in days if int(item.get("day", -1)) == day_idx), None)
    if day is not None:
        return day
    weekly_day = ((day_idx - 1) % len(days)) + 1
    return next((item for item in days if int(item.get("day", -1)) == weekly_day), None)


def _aggregate_scoring_weights(member_personas: list[dict[str, Any]]) -> dict[str, float]:
    totals = {"comfort": 0.0, "energy": 0.0, "vpp": 0.0}
    total_weight = 0.0
    for persona in member_personas:
        member_weight = float(persona.get("household_member", {}).get("decision_weight", 1.0) or 1.0)
        weights = ((persona.get("preferences") or {}).get("scoring_weights") or {})
        for key in totals:
            totals[key] += member_weight * float(weights.get(key, 0.0) or 0.0)
        total_weight += member_weight
    if total_weight <= 0:
        return {"comfort": 0.5, "energy": 0.3, "vpp": 0.2}
    return {key: round(value / total_weight, 3) for key, value in totals.items()}


def _merge_member_appliances(member_personas: list[dict[str, Any]]) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for persona in member_personas:
        for name, cfg in (persona.get("appliances") or {}).items():
            if not isinstance(cfg, dict):
                continue
            if name not in merged:
                merged[name] = dict(cfg)
            elif cfg.get("present"):
                merged[name]["present"] = True
    return merged


def _default_household_prompt(household: dict[str, Any], member_personas: list[dict[str, Any]]) -> str:
    names = ", ".join(p.get("display_name", p.get("id", "")) for p in member_personas)
    return (
        f"This is a multi-user household ({household.get('id')}) with members: {names}. "
        "Treat satisfaction as a household compromise. Protect comfort and safety, complete every present "
        "appliance service, and support VPP only when those constraints remain satisfied."
    )
