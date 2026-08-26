"""Auditable household resumes for the EnergyBridge V2 evaluation harness.

The resume is a faithful, deterministic projection of a persona configuration.
It deliberately preserves biography, voice, routines, household composition,
service commitments, and relationship history instead of reducing a household
to one archetype label or one scoring weight.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Mapping, Sequence


RESUME_SCHEMA_VERSION = "energybridge.household_resume.v2"

_APPLIANCE_FACT_KEYS = (
    "present",
    "mode",
    "setpoint_preferred_min_c",
    "setpoint_preferred_max_c",
    "temp_tolerance_c",
    "earliest_h",
    "latest_h",
    "preferred_h",
    "duration_h",
    "shiftable",
    "dr_adjustable",
    "bath_required_h",
    "pre_heat_window_start_h",
    "pre_heat_window_end_h",
    "arrival_h",
    "departure_h",
    "target_soc",
    "min_soc",
)


def _compact_text(value: Any, limit: int = 2400) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _json_safe(value: Any) -> Any:
    """Return a detached JSON-compatible value with deterministic key order."""
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _fingerprint(value: Any) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _preferences(persona: Mapping[str, Any]) -> dict[str, Any]:
    nested = persona.get("preferences")
    if isinstance(nested, Mapping):
        return _json_safe(nested)
    out: dict[str, Any] = {}
    if isinstance(persona.get("scoring_weights"), Mapping):
        out["scoring_weights"] = _json_safe(persona["scoring_weights"])
    if persona.get("vpp_override_prob") is not None:
        out["vpp_override_prob"] = persona.get("vpp_override_prob")
    return out


def _prompt_material(persona: Mapping[str, Any]) -> tuple[str, list[str], str]:
    prompts = persona.get("llm_prompts") if isinstance(persona.get("llm_prompts"), Mapping) else {}
    narrative = _compact_text(
        prompts.get("system_prompt")
        or persona.get("roleplay_user_prompt")
        or persona.get("description")
        or ""
    )
    examples_raw = prompts.get("example_responses") or persona.get("example_responses") or []
    examples = [
        _compact_text(item, 500)
        for item in examples_raw
        if _compact_text(item, 500)
    ][:8] if isinstance(examples_raw, Sequence) and not isinstance(examples_raw, (str, bytes)) else []
    agent_context = _compact_text(prompts.get("agent_context") or persona.get("persona_prompt") or "")
    return narrative, examples, agent_context


def _calendar_resume(calendar: Any) -> dict[str, Any]:
    if not isinstance(calendar, Mapping):
        return {"available": False, "days": []}
    days_out: list[dict[str, Any]] = []
    raw_days = calendar.get("days") if isinstance(calendar.get("days"), Sequence) else []
    for raw_day in raw_days[:14]:
        if not isinstance(raw_day, Mapping):
            continue
        events: list[dict[str, Any]] = []
        raw_events = raw_day.get("events") if isinstance(raw_day.get("events"), Sequence) else []
        for event in raw_events[:16]:
            if not isinstance(event, Mapping):
                continue
            events.append({
                key: _json_safe(event.get(key))
                for key in ("title", "start_h", "end_h", "location", "member_id", "member_role")
                if event.get(key) is not None
            })
        day = {
            key: _json_safe(raw_day.get(key))
            for key in ("day", "weekday", "day_type", "summary")
            if raw_day.get(key) is not None
        }
        day["events"] = events
        if isinstance(raw_day.get("constraints"), Mapping):
            day["constraints"] = _json_safe(raw_day["constraints"])
        days_out.append(day)
    return {
        "available": bool(days_out),
        "source": _compact_text(calendar.get("source", ""), 300),
        "timezone": _compact_text(calendar.get("timezone", ""), 100),
        "description": _compact_text(calendar.get("description", ""), 600),
        "days": days_out,
    }


def _appliance_resume(appliances: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for name in sorted(str(key) for key in appliances):
        raw = appliances.get(name)
        if not isinstance(raw, Mapping):
            continue
        facts = {
            key: _json_safe(raw.get(key))
            for key in _APPLIANCE_FACT_KEYS
            if raw.get(key) is not None
        }
        facts.setdefault("present", bool(raw.get("present", False)))
        result.append({"device": name, **facts})
    return result


def _member_resume(persona: Mapping[str, Any]) -> list[dict[str, Any]]:
    members = persona.get("members") if isinstance(persona.get("members"), Sequence) else []
    profiles = persona.get("acceptance_profiles") if isinstance(persona.get("acceptance_profiles"), Sequence) else []
    profiles_by_id = {
        str(item.get("member_id") or item.get("persona_id")): item
        for item in profiles
        if isinstance(item, Mapping)
    }
    result: list[dict[str, Any]] = []
    for raw in members:
        if not isinstance(raw, Mapping):
            continue
        member_id = str(raw.get("member_id") or raw.get("persona_id") or "member")
        profile = profiles_by_id.get(member_id, {})
        item = {
            "member_id": member_id,
            "persona_id": raw.get("persona_id") or profile.get("persona_id"),
            "display_name": raw.get("display_name") or profile.get("display_name"),
            "household_role": raw.get("household_role") or profile.get("household_role"),
            "decision_weight": raw.get("decision_weight", profile.get("decision_weight")),
        }
        for key in ("tags", "preferences", "schedule"):
            if isinstance(profile.get(key), Mapping):
                item[key] = _json_safe(profile[key])
        if isinstance(profile.get("appliances"), Mapping):
            item["comfort_and_service"] = _appliance_resume(profile["appliances"])
        result.append({key: value for key, value in item.items() if value not in (None, "", {})})

    # Some integrations provide acceptance profiles without a separate members list.
    seen = {str(item.get("member_id")) for item in result}
    for raw in profiles:
        if not isinstance(raw, Mapping):
            continue
        member_id = str(raw.get("member_id") or raw.get("persona_id") or "member")
        if member_id in seen:
            continue
        result.append({
            key: _json_safe(raw.get(key))
            for key in (
                "member_id",
                "persona_id",
                "display_name",
                "household_role",
                "decision_weight",
                "tags",
                "preferences",
                "schedule",
            )
            if raw.get(key) not in (None, "", {})
        })
    return result


def _history_resume(past_events: Sequence[Any] | None) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for raw in list(past_events or [])[-8:]:
        if not isinstance(raw, Mapping):
            continue
        gate = raw.get("vpp_acceptance_gate") if isinstance(raw.get("vpp_acceptance_gate"), Mapping) else {}
        event = {
            "event_id": raw.get("id") or raw.get("event_id"),
            "day": raw.get("day"),
            "user_statement": _compact_text(raw.get("user_input", ""), 500),
            "decision": gate.get("decision") or ("accept" if gate.get("accepted") is True else "reject" if gate.get("accepted") is False else None),
            "satisfaction": raw.get("score"),
            "comfort_score": raw.get("comfort_score"),
            "energy_score": raw.get("energy_score"),
            "vpp_score": raw.get("vpp_score"),
            "feedback": _compact_text(
                raw.get("controller_feedback")
                or raw.get("member_feedback_summary")
                or raw.get("comment")
                or gate.get("energybridge_feedback")
                or "",
                800,
            ),
            "service_outcome": _json_safe(raw.get("appliance_summary") or {}),
            "target_achieved": raw.get("target_achieved"),
        }
        result.append({key: value for key, value in event.items() if value not in (None, "", {})})
    return result


def _explicit_constraints(
    persona: Mapping[str, Any],
    appliances: Mapping[str, Any],
) -> list[dict[str, Any]]:
    constraints: list[dict[str, Any]] = []
    scoring_policy = persona.get("scoring_policy") if isinstance(persona.get("scoring_policy"), Mapping) else {}
    for value in scoring_policy.get("hard_constraints") or []:
        if str(value).strip():
            constraints.append({"source": "scoring_policy.hard_constraints", "constraint": str(value).strip()})
    ac = appliances.get("ac") if isinstance(appliances.get("ac"), Mapping) else {}
    if ac and ac.get("present", True):
        bounds = {
            key: ac.get(key)
            for key in ("setpoint_preferred_min_c", "setpoint_preferred_max_c", "temp_tolerance_c")
            if ac.get(key) is not None
        }
        if bounds:
            constraints.append({"source": "appliances.ac", "constraint": "thermal_comfort_envelope", "facts": bounds})
    for name, raw in appliances.items():
        if not isinstance(raw, Mapping) or not bool(raw.get("present", False)):
            continue
        if raw.get("dr_adjustable") is False or raw.get("shiftable") is False:
            constraints.append({
                "source": f"appliances.{name}",
                "constraint": "fixed_or_non_dr_adjustable_service",
                "device": str(name),
            })
        if name == "ev" and raw.get("target_soc") is not None:
            constraints.append({
                "source": "appliances.ev",
                "constraint": "ev_readiness",
                "target_soc": raw.get("target_soc"),
                "departure_h": raw.get("departure_h"),
            })
        if name == "water_heater" and raw.get("bath_required_h") is not None:
            constraints.append({
                "source": "appliances.water_heater",
                "constraint": "hot_water_readiness",
                "required_h": raw.get("bath_required_h"),
            })
    schedule = persona.get("schedule") if isinstance(persona.get("schedule"), Mapping) else {}
    if schedule.get("vulnerable_members"):
        constraints.append({
            "source": "schedule.vulnerable_members",
            "constraint": "vulnerable_member_comfort_and_safety",
            "members": _json_safe(schedule.get("vulnerable_members")),
        })
    return constraints


def build_household_resume(
    persona_config: Mapping[str, Any] | None,
    appliance_config: Mapping[str, Any] | None = None,
    past_events: Sequence[Any] | None = None,
) -> dict[str, Any]:
    """Build a deterministic, source-auditable V2 household role-play resume.

    ``appliance_config`` is the physical source of truth when supplied. The
    returned ``profile_fingerprint`` covers stable household content, while
    ``resume_fingerprint`` also covers the bounded relationship history.
    """
    persona: dict[str, Any] = deepcopy(dict(persona_config or {}))
    effective_appliances = deepcopy(dict(
        appliance_config
        if appliance_config is not None
        else (persona.get("appliances") if isinstance(persona.get("appliances"), Mapping) else {})
    ))
    narrative, example_utterances, agent_context = _prompt_material(persona)
    preferences = _preferences(persona)
    scoring_weights = preferences.get("scoring_weights") if isinstance(preferences.get("scoring_weights"), Mapping) else {}
    tags = _json_safe(persona.get("tags") or {})
    schedule = _json_safe(persona.get("schedule") or {})
    calendar = _calendar_resume(persona.get("calendar"))
    members = _member_resume(persona)
    appliance_commitments = _appliance_resume(effective_appliances)
    history = _history_resume(past_events)

    stable_profile = {
        "household_id": str(persona.get("id") or persona.get("persona_id") or "unknown_household"),
        "display_name": _compact_text(persona.get("display_name") or persona.get("name") or persona.get("id") or "Unknown household", 300),
        "biography": {
            "description": _compact_text(persona.get("description") or persona.get("summary") or ""),
            "first_person_roleplay_source": narrative,
            "household_members": members,
        },
        "voice": {
            "example_utterances": example_utterances,
            "language": str(persona.get("speaking_language") or "en"),
        },
        "decision_profile": {
            "behavioral_dimensions": tags,
            "stable_priorities": _json_safe(scoring_weights),
            "other_preferences": {
                key: _json_safe(value)
                for key, value in preferences.items()
                if key != "scoring_weights"
            },
        },
        "daily_life": {
            "schedule": schedule,
            "calendar": calendar,
        },
        "comfort_and_service": {
            "appliance_commitments": appliance_commitments,
            "explicit_hard_constraints": _explicit_constraints(persona, effective_appliances),
        },
        "controller_context_source": agent_context,
    }
    profile_fingerprint = _fingerprint(stable_profile)
    resume_core = {**stable_profile, "relationship_history": history}
    resume_fingerprint = _fingerprint(resume_core)
    provenance = {
        "source_schema_version": str(persona.get("schema_version") or "unknown"),
        "appliance_source": "appliance_config_argument" if appliance_config is not None else "persona_config.appliances",
        "field_provenance": {
            "biography.description": ["persona_config.description", "persona_config.summary"],
            "biography.first_person_roleplay_source": [
                "persona_config.llm_prompts.system_prompt",
                "persona_config.roleplay_user_prompt",
            ],
            "voice.example_utterances": ["persona_config.llm_prompts.example_responses"],
            "decision_profile": ["persona_config.tags", "persona_config.preferences"],
            "daily_life": ["persona_config.schedule", "persona_config.calendar"],
            "comfort_and_service": [
                "appliance_config" if appliance_config is not None else "persona_config.appliances",
                "persona_config.scoring_policy.hard_constraints",
            ],
            "relationship_history": ["past_events"],
        },
        "source_fingerprint": _fingerprint({
            "persona_config": persona,
            "appliance_config": effective_appliances,
        }),
        "profile_fingerprint": profile_fingerprint,
        "resume_fingerprint": resume_fingerprint,
    }
    return {
        "schema_version": RESUME_SCHEMA_VERSION,
        "resume_id": f"{stable_profile['household_id']}:{resume_fingerprint[:12]}",
        **resume_core,
        "audit": provenance,
    }


__all__ = ["RESUME_SCHEMA_VERSION", "build_household_resume"]
