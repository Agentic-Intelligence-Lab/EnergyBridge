"""Evidence-calibrated, observable-only household memory for EnergyBridge V2.

This module deliberately keeps the controller-side memory separate from a
role-play user's hidden persona.  The controller may learn only from the
onboarding conversation, calendar/home observations, events, and user
feedback.  Every learned belief therefore carries evidence and provenance,
and conflicting observations reduce confidence instead of being silently
averaged away.

The public functions return plain JSON-serializable dictionaries so the
benchmark harness can persist or inspect them without another dependency.
They do not mutate their inputs.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


MEMORY_VERSION = "energybridge_observable_profile_memory_v2"
EVENT_CONTEXT_VERSION = "energybridge_observable_event_context_v2"

_FORBIDDEN_KEYS = {
    "hidden",
    "hidden_persona",
    "latent_persona",
    "persona",
    "persona_config",
    "role_card",
    "roleplay_system_prompt",
    "system_prompt",
    "ground_truth",
    "scoring_weights",
    "vpp_override_prob",
    "override_probability",
    "tags",
}
_PROFILE_FIELDS = {
    "comfort_priority",
    "cost_grid_priority",
    "automation_preference",
    "thermostat_flexibility_c",
    "appliance_flexibility",
    "calendar_routine_sensitivity",
    "strategy_bias",
}
_EVENT_FIELDS = {
    "id",
    "event_id",
    "day",
    "date",
    "start_date",
    "type",
    "event_type",
    "signal_type",
    "trigger_h",
    "start_h",
    "end_h",
    "duration_h",
    "notice_hours",
    "price",
    "price_level",
    "urgency",
    "target_reduction_kw",
    "target_shed_kw",
    "target_kwh",
    "weather",
    "outdoor_temp_c",
    "outdoor_temperature_c",
}
_CALENDAR_FIELDS = {
    "available",
    "source",
    "date",
    "day",
    "weekday",
    "day_type",
    "occupied",
    "occupancy",
    "occupants_home",
    "working_from_home",
    "arrival_h",
    "departure_h",
    "returns_home_h",
    "summary",
    "events",
    "vpp_window_h",
    "vpp_conflicts",
    "appointments",
    "routines",
    "constraints",
    "appliance_deadlines",
    "protected_windows",
    "same_day_changes",
}
_HOME_FIELDS = {
    "indoor_temp_c",
    "temperature_c",
    "temp_c",
    "outdoor_temp_c",
    "humidity",
    "occupied",
    "occupancy",
    "facility_w",
    "load_kw",
    "appliance_state",
    "appliances",
    "battery_soc",
    "ev_soc",
    "water_heater_temp_c",
}
_PLAN_FIELDS = {
    "id",
    "strategy_id",
    "selected_strategy_id",
    "name",
    "mode",
    "action",
    "actions",
    "setpoint",
    "setpoint_c",
    "duration_minutes",
    "next_check_hour",
    "appliances",
    "appliance_actions",
    "reason",
    "strategy_explanation",
    "predicted_comfort",
    "predicted_cost",
    "predicted_grid_support",
    "selected_skill",
    "skill_selection",
    "control_source",
    "decision_basis",
    "memory_citations",
    "alternatives_considered",
    "uncertainty",
    "fallback_after_vpp_rejection",
    "objective_source",
}
_OUTCOME_FIELDS = {
    "accepted",
    "approved",
    "score",
    "overall_score",
    "comfort_score",
    "energy_score",
    "vpp_score",
    "target_achieved",
    "actual_kwh",
    "actual_shed_kwh",
    "comfort_violation_minutes",
    "feedback",
    "user_feedback",
    "controller_feedback",
    "member_feedback_summary",
    "comment",
    "complaint",
    "preference_observations",
    "belief_updates",
    "observed_preferences",
}

_NEGATIVE_TERMS = (
    "uncomfortable",
    "too hot",
    "too cold",
    "disrupt",
    "annoy",
    "complaint",
    "reject",
    "decline",
    "not accept",
    "without asking",
    "late",
    "missed",
    "不舒服",
    "太热",
    "太冷",
    "打扰",
    "干扰",
    "不同意",
    "拒绝",
    "没问",
    "未经确认",
    "延误",
)
_POSITIVE_TERMS = (
    "comfortable",
    "worked well",
    "satisfied",
    "acceptable",
    "good plan",
    "thank",
    "满意",
    "舒适",
    "可以接受",
    "效果很好",
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normal_key(value: object) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value).strip().lower()).strip("_")


def _is_forbidden_key(key: object) -> bool:
    normalized = _normal_key(key)
    if normalized == "persona_id":
        return False
    return (
        normalized in _FORBIDDEN_KEYS
        or normalized.startswith("hidden")
        or "persona" in normalized
        or "role_card" in normalized
        or "scoring_weight" in normalized
        or "system_prompt" in normalized
        or "ground_truth" in normalized
    )


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON-compatible copy while removing private fields."""
    if depth > 7:
        return "[depth limit]"
    if value is None or isinstance(value, (bool, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            return None
        return value
    if isinstance(value, str):
        return value[:2000]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, depth=depth + 1)
            for key, item in value.items()
            if not _is_forbidden_key(key)
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item, depth=depth + 1) for item in list(value)[:100]]
    return str(value)[:500]


def _pick(mapping: Mapping[str, Any] | None, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(mapping, Mapping):
        return {}
    return {
        str(key): _json_safe(value)
        for key, value in mapping.items()
        if _normal_key(key) in allowed and not _is_forbidden_key(key)
    }


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))


def _canonical(value: Any) -> str:
    return json.dumps(_json_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _evidence(
    *,
    evidence_id: str,
    source: str,
    value: Any,
    observed_at: str,
    reliability: float,
    event_id: str | None = None,
    context_signature: str | None = None,
) -> dict[str, Any]:
    item = {
        "evidence_id": evidence_id,
        "source": source,
        "value": _json_safe(value),
        "observed_at": observed_at,
        "reliability": round(_clamp(reliability, 0.05, 1.0), 3),
    }
    if event_id:
        item["event_id"] = event_id
    if context_signature:
        item["context_signature"] = context_signature
    return item


def _new_belief(value: Any, evidence: dict[str, Any]) -> dict[str, Any]:
    reliability = float(evidence["reliability"])
    canonical = _canonical(value)
    return {
        "value": _json_safe(value),
        "confidence": round(_clamp(0.35 + (0.5 * reliability), 0.05, 0.95), 3),
        "evidence_count": 1,
        "support_count": 1,
        "contradiction_count": 0,
        "provenance": [evidence],
        "contradictions": [],
        "candidate_weights": {canonical: round(reliability, 3)},
        "candidate_values": {canonical: _json_safe(value)},
        "last_updated_at": evidence["observed_at"],
    }


def _values_support(previous: Any, incoming: Any) -> bool:
    old_number = _float(previous)
    new_number = _float(incoming)
    if old_number is not None and new_number is not None:
        tolerance = max(0.1, 0.12 * max(abs(old_number), abs(new_number), 1.0))
        return abs(old_number - new_number) <= tolerance
    return _canonical(previous) == _canonical(incoming)


def _merge_belief(
    current: Mapping[str, Any],
    incoming: Any,
    evidence: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Merge one observation and return ``(belief, contradicted)``."""
    belief = deepcopy(dict(current))
    previous = belief.get("value")
    reliability = float(evidence["reliability"])
    supports = _values_support(previous, incoming)
    contradicted = not supports

    weights = dict(belief.get("candidate_weights") or {})
    values = dict(belief.get("candidate_values") or {})
    incoming_key = _canonical(incoming)
    weights[incoming_key] = round(float(weights.get(incoming_key, 0.0)) + reliability, 4)
    values[incoming_key] = _json_safe(incoming)
    belief["candidate_weights"] = weights
    belief["candidate_values"] = values

    provenance = list(belief.get("provenance") or [])
    provenance.append(evidence)
    belief["provenance"] = provenance[-20:]
    belief["evidence_count"] = int(belief.get("evidence_count", 0) or 0) + 1
    if supports:
        belief["support_count"] = int(belief.get("support_count", 0) or 0) + 1
    else:
        belief["contradiction_count"] = int(belief.get("contradiction_count", 0) or 0) + 1
        contradictions = list(belief.get("contradictions") or [])
        contradictions.append(
            {
                "previous_value": _json_safe(previous),
                "incoming_value": _json_safe(incoming),
                "evidence_id": evidence["evidence_id"],
                "source": evidence["source"],
                "observed_at": evidence["observed_at"],
            }
        )
        belief["contradictions"] = contradictions[-10:]

    old_number = _float(previous)
    new_number = _float(incoming)
    if old_number is not None and new_number is not None:
        prior_weight = max(0.1, sum(weights.values()) - reliability)
        belief["value"] = round(
            ((old_number * prior_weight) + (new_number * reliability)) / (prior_weight + reliability),
            4,
        )
    else:
        winner = max(weights, key=lambda key: (weights[key], key))
        belief["value"] = values[winner]

    support = int(belief.get("support_count", 0) or 0)
    conflicts = int(belief.get("contradiction_count", 0) or 0)
    evidence_count = max(1, int(belief.get("evidence_count", 1) or 1))
    evidence_strength = 1.0 - math.exp(-0.55 * evidence_count)
    agreement = support / max(1, support + conflicts)
    confidence = 0.2 + (0.55 * evidence_strength) + (0.2 * agreement)
    if contradicted:
        confidence -= min(0.25, 0.08 + (0.08 * reliability))
    belief["confidence"] = round(_clamp(confidence, 0.05, 0.97), 3)
    belief["last_updated_at"] = evidence["observed_at"]
    return belief, contradicted


def _onboarding_view(questionnaire: Mapping[str, Any] | None) -> dict[str, Any]:
    questionnaire = questionnaire if isinstance(questionnaire, Mapping) else {}
    answers: list[dict[str, Any]] = []
    raw_answers = questionnaire.get("answers")
    if isinstance(raw_answers, Mapping):
        raw_answers = [
            {"id": question_id, "answer": answer}
            for question_id, answer in raw_answers.items()
        ]
    for item in list(raw_answers or [])[:20]:
        if not isinstance(item, Mapping):
            continue
        answer = {
            "id": str(item.get("id", ""))[:100],
            "answer": str(item.get("answer", ""))[:1000],
            "selected_option_ids": [
                str(option)[:100] for option in list(item.get("selected_option_ids") or [])[:10]
            ],
        }
        if item.get("question"):
            answer["question"] = str(item.get("question"))[:500]
        if answer["id"]:
            answers.append(answer)
    safe_profile = _derive_profile_from_answers(answers)
    # V2 intentionally ignores caller-supplied inferred_profile and
    # preference_rules.  Those fields may have been produced while a role-play
    # simulator could see a hidden persona.  Every stored inference below is a
    # reproducible projection of answer text/selected option IDs only.
    rules = [
        f"{item['id']}: {item['answer']}"[:500]
        for item in answers
        if str(item.get("answer", "")).strip()
    ][:12]
    return {
        "source": str(questionnaire.get("source", "onboarding_questionnaire"))[:100],
        "answers": answers,
        "inferred_profile": safe_profile,
        "preference_rules": rules,
    }


def _derive_profile_from_answers(answers: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """Infer a small observable profile only from interview answers."""
    by_id = {
        _normal_key(item.get("id")): item
        for item in answers
        if isinstance(item, Mapping) and str(item.get("id", "")).strip()
    }

    def signals(question_id: str) -> tuple[set[str], str]:
        item = by_id.get(question_id, {})
        options = {_normal_key(value) for value in list(item.get("selected_option_ids") or [])}
        text = str(item.get("answer", "") or "").strip().lower()
        return options, text

    profile: dict[str, Any] = {}
    vpp_options, vpp_text = signals("vpp_priority")
    if "bill_savings_first" in vpp_options or any(
        token in vpp_text for token in ("savings first", "grid support", "price first")
    ):
        profile.update({
            "cost_grid_priority": "high",
            "comfort_priority": "medium",
            "strategy_bias": "cost_grid_oriented",
        })
    elif vpp_options & {"comfort_routine_first", "confirm_before_changes"} or any(
        token in vpp_text for token in ("comfort first", "do not disrupt", "confirm before")
    ):
        profile.update({
            "cost_grid_priority": "medium",
            "comfort_priority": "high",
            "strategy_bias": "comfort_calendar_protective",
        })
    elif "balanced_tradeoff" in vpp_options or "balance" in vpp_text:
        profile.update({
            "cost_grid_priority": "medium",
            "comfort_priority": "medium",
            "strategy_bias": "balanced_middle",
        })

    thermostat_options, thermostat_text = signals("thermostat_flexibility")
    flex_by_option = {
        "almost_none_0_5c": 0.5,
        "small_1c_short": 1.0,
        "moderate_1_2c_with_benefit": 1.5,
        "larger_when_unoccupied": 2.5,
    }
    for option, value in flex_by_option.items():
        if option in thermostat_options:
            profile["thermostat_flexibility_c"] = value
            break
    if "thermostat_flexibility_c" not in profile:
        match = re.search(r"(?:change|limit|drift|flex(?:ibility)?)\D{0,24}(\d+(?:\.\d+)?)\s*°?\s*c", thermostat_text)
        if match:
            profile["thermostat_flexibility_c"] = min(5.0, max(0.0, float(match.group(1))))

    appliance_options, appliance_text = signals("appliance_shift_consent")
    if "do_not_move_without_approval" in appliance_options or any(
        token in appliance_text for token in ("ask before", "without approval", "explicitly approve")
    ):
        profile.update({
            "automation_preference": "ask_before_vpp_specific_changes",
            "appliance_flexibility": "limited_without_approval",
        })
    elif "automatic_optimization_ok" in appliance_options or "automatic" in appliance_text:
        profile.update({
            "automation_preference": "automatic_when_deadlines_protected",
            "appliance_flexibility": "flexible_if_deadlines_protected",
        })
    elif "shift_1_2h_deadline_protected" in appliance_options or "suggest" in appliance_text:
        profile.update({
            "automation_preference": "suggestion_first_with_clear_benefit",
            "appliance_flexibility": "flexible_if_deadlines_protected",
        })

    calendar_options, calendar_text = signals("calendar_routine_constraints")
    if "irregular_confirm_same_day" in calendar_options or any(
        token in calendar_text for token in ("same-day", "same day", "irregular", "plans may change")
    ):
        profile["calendar_routine_sensitivity"] = "high"
        if profile.get("strategy_bias") != "cost_grid_oriented":
            profile["strategy_bias"] = "comfort_calendar_protective"
    elif calendar_options or calendar_text:
        profile["calendar_routine_sensitivity"] = "medium"
    return _pick(profile, _PROFILE_FIELDS)


def initialize_memory(
    onboarding_questionnaire: Mapping[str, Any] | None,
    persona_id: str | None = None,
    method: str = "agent",
) -> dict[str, Any]:
    """Initialize controller-visible household memory from onboarding only.

    ``persona_id`` is an opaque namespace identifier; no persona document is
    accepted.  Unknown fields in the questionnaire are discarded, including
    common hidden-persona and evaluator-only fields.
    """
    created_at = _now_iso()
    onboarding = _onboarding_view(onboarding_questionnaire)
    memory: dict[str, Any] = {
        "version": MEMORY_VERSION,
        "owner": {
            "persona_id": str(persona_id or "unknown")[:200],
            "method": str(method or "agent")[:100],
        },
        "privacy_boundary": {
            "scope": "agent_observable_only",
            "allowed_sources": [
                "onboarding_questionnaire",
                "calendar",
                "home_observation",
                "event",
                "executed_outcome",
                "user_feedback",
            ],
            "excluded_sources": [
                "hidden_persona",
                "role_card",
                "persona_tags",
                "evaluator_weights",
                "roleplay_system_prompt",
            ],
        },
        "created_at": created_at,
        "updated_at": created_at,
        "revision": 0,
        "onboarding": onboarding,
        "beliefs": {},
        "contextual_beliefs": {},
        "profile_revision_ledger": [],
        "events": [],
    }

    observations: list[tuple[str, Any, float, str]] = []
    for answer in onboarding["answers"]:
        selected = list(answer.get("selected_option_ids") or [])
        value: Any = selected[0] if len(selected) == 1 else selected
        confidence = 0.78
        if not selected:
            value = answer.get("answer", "")
            confidence = 0.58
        observations.append(
            (str(answer["id"]), value, confidence, f"onboarding:{answer['id']}")
        )
    for key, value in onboarding["inferred_profile"].items():
        observations.append((str(key), value, 0.62, f"onboarding_profile:{key}"))

    for key, value, reliability, evidence_id in observations:
        evidence = _evidence(
            evidence_id=evidence_id,
            source="onboarding_questionnaire",
            value=value,
            observed_at=created_at,
            reliability=reliability,
        )
        belief = _new_belief(value, evidence)
        memory["beliefs"][key] = belief
        memory["profile_revision_ledger"].append(
            {
                "revision": 0,
                "belief_key": key,
                "previous_value": None,
                "new_value": _json_safe(value),
                "previous_confidence": 0.0,
                "new_confidence": belief["confidence"],
                "reason": "initialized_from_observable_onboarding",
                "evidence_id": evidence_id,
                "source": "onboarding_questionnaire",
                "contradiction": False,
                "observed_at": created_at,
            }
        )
    return memory


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _time_bucket(hour: Any) -> str | None:
    value = _float(hour)
    if value is None:
        return None
    hour_of_day = value % 24.0
    if hour_of_day < 6:
        return "overnight"
    if hour_of_day < 12:
        return "morning"
    if hour_of_day < 17:
        return "afternoon"
    if hour_of_day < 21:
        return "evening"
    return "late_evening"


def _duration_bucket(value: Any) -> str | None:
    duration = _float(value)
    if duration is None:
        return None
    if duration <= 1.0:
        return "short"
    if duration <= 2.5:
        return "medium"
    return "long"


def _temperature_bucket(value: Any) -> str | None:
    temperature = _float(value)
    if temperature is None:
        return None
    if temperature < 10:
        return "cold"
    if temperature < 24:
        return "mild"
    if temperature < 30:
        return "warm"
    return "hot"


def _occupancy_bucket(calendar: Mapping[str, Any], home: Mapping[str, Any]) -> str | None:
    value = _first(calendar, "occupied", "occupancy", "occupants_home")
    if value is None:
        value = _first(home, "occupied", "occupancy")
    if isinstance(value, bool):
        return "occupied" if value else "unoccupied"
    number = _float(value)
    if number is not None:
        return "occupied" if number > 0 else "unoccupied"
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"home", "occupied", "present", "yes"}:
        return "occupied"
    if text in {"away", "unoccupied", "absent", "no"}:
        return "unoccupied"
    return text[:60]


def _plan_family(plan: Mapping[str, Any]) -> str | None:
    explicit = _first(plan, "mode", "action", "strategy_id", "selected_strategy_id", "name")
    if explicit is not None:
        return _normal_key(explicit)[:80]
    actions = plan.get("appliance_actions") or plan.get("appliances") or plan.get("actions")
    if isinstance(actions, Mapping) and actions:
        return "+".join(sorted(_normal_key(key) for key in actions)[:5])
    if _first(plan, "setpoint", "setpoint_c") is not None:
        return "thermostat"
    return None


def _context_features(
    event: Mapping[str, Any],
    calendar: Mapping[str, Any],
    home: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    trigger = _first(event, "trigger_h", "start_h")
    duration = event.get("duration_h")
    if duration is None:
        start = _float(_first(event, "trigger_h", "start_h"))
        end = _float(event.get("end_h"))
        if start is not None and end is not None:
            duration = max(0.0, end - start)
    event_type = _first(event, "event_type", "type", "signal_type")
    day_type = _first(calendar, "day_type", "weekday")
    outdoor_temp = _first(event, "outdoor_temp_c", "outdoor_temperature_c")
    if outdoor_temp is None:
        outdoor_temp = home.get("outdoor_temp_c")
    constraints = calendar.get("constraints") or calendar.get("protected_windows") or []
    if isinstance(constraints, str):
        constraint_tokens = [_normal_key(constraints)[:80]]
    elif isinstance(constraints, Sequence):
        constraint_tokens = sorted({_normal_key(item)[:80] for item in constraints if str(item).strip()})[:8]
    elif isinstance(constraints, Mapping):
        constraint_tokens = sorted(_normal_key(key)[:80] for key in constraints)[:8]
    else:
        constraint_tokens = []
    return {
        "event_type": _normal_key(event_type)[:80] if event_type is not None else None,
        "time_bucket": _time_bucket(trigger),
        "day_type": _normal_key(day_type)[:60] if day_type is not None else None,
        "occupancy": _occupancy_bucket(calendar, home),
        "price_level": _normal_key(event.get("price_level"))[:60] if event.get("price_level") is not None else None,
        "duration_bucket": _duration_bucket(duration),
        "temperature_bucket": _temperature_bucket(outdoor_temp),
        "plan_family": _plan_family(plan),
        "calendar_constraints": constraint_tokens,
    }


def _signature(features: Mapping[str, Any]) -> str:
    keys = ("event_type", "time_bucket", "day_type", "occupancy", "price_level", "duration_bucket")
    parts = [f"{key}={features[key]}" for key in keys if features.get(key) not in (None, "", [])]
    return "|".join(parts) or "context=unspecified"


def build_event_context(
    event: Mapping[str, Any] | None = None,
    *,
    calendar: Mapping[str, Any] | None = None,
    home_state: Mapping[str, Any] | None = None,
    user_input: str | None = None,
    proposed_plan: Mapping[str, Any] | None = None,
    raw_model_plan: Mapping[str, Any] | None = None,
    validated_plan: Mapping[str, Any] | None = None,
    consented_plan: Mapping[str, Any] | None = None,
    executed_plan: Mapping[str, Any] | None = None,
    plan_lifecycle: Mapping[str, Any] | None = None,
    observed_at: str | datetime | None = None,
    observations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a retrieval context from controller-observable inputs.

    Each structured section uses an allowlist.  ``observations`` is available
    for additional observable sensors, but hidden-persona keys are recursively
    removed.
    """
    event_view = _pick(event, _EVENT_FIELDS)
    calendar_view = _pick(calendar, _CALENDAR_FIELDS)
    home_view = _pick(home_state, _HOME_FIELDS)
    proposed_view = _pick(proposed_plan, _PLAN_FIELDS)
    raw_model_view = _pick(raw_model_plan, _PLAN_FIELDS)
    validated_view = _pick(validated_plan, _PLAN_FIELDS)
    consented_view = _pick(consented_plan, _PLAN_FIELDS)
    executed_view = _pick(executed_plan, _PLAN_FIELDS)
    active_plan = executed_view or proposed_view
    features = _context_features(event_view, calendar_view, home_view, active_plan)
    if isinstance(observed_at, datetime):
        timestamp = observed_at.isoformat()
    elif observed_at:
        timestamp = str(observed_at)[:100]
    else:
        timestamp = _now_iso()
    return {
        "version": EVENT_CONTEXT_VERSION,
        "observed_at": timestamp,
        "event_id": str(_first(event_view, "event_id", "id") or "")[:200],
        "event": event_view,
        "calendar": calendar_view,
        "home_state": home_view,
        "user_input": str(user_input or "")[:2000],
        "raw_model_plan": raw_model_view,
        "validated_plan": validated_view,
        "proposed_plan": proposed_view,
        "consented_plan": consented_view,
        "executed_plan": executed_view,
        "plan_lifecycle": _safe_plan_lifecycle(plan_lifecycle),
        "observations": _json_safe(observations or {}),
        "features": features,
        "context_signature": _signature(features),
        "source_scope": "agent_observable_only",
    }


def _safe_plan_lifecycle(value: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    stages_out: dict[str, Any] = {}
    stages = value.get("stages") if isinstance(value.get("stages"), Mapping) else {}
    for name in (
        "raw_model_plan",
        "validated_plan",
        "proposed_plan",
        "consented_plan",
        "executed_plan",
    ):
        stage = stages.get(name)
        if not isinstance(stage, Mapping):
            continue
        stages_out[name] = {
            "plan": _pick(stage.get("plan"), _PLAN_FIELDS),
            "fingerprint": str(stage.get("fingerprint", ""))[:100],
            "status": str(stage.get("status", ""))[:120],
            "from_stage": str(stage.get("from_stage", ""))[:100],
            "reason": str(stage.get("reason", ""))[:500],
            "patches": _json_safe(list(stage.get("patches") or [])[:100]),
        }
    validators = []
    for item in list(value.get("validators") or [])[:30]:
        if not isinstance(item, Mapping):
            continue
        validators.append({
            "validator": str(item.get("validator", ""))[:120],
            "status": str(item.get("status", ""))[:120],
            "from_stage": str(item.get("from_stage", ""))[:100],
            "to_stage": str(item.get("to_stage", ""))[:100],
            "reason": str(item.get("reason", ""))[:500],
            "patches": _json_safe(list(item.get("patches") or [])[:100]),
        })
    return {
        "version": str(value.get("version", ""))[:120],
        "stage_order": [str(item)[:100] for item in list(value.get("stage_order") or [])[:10]],
        "stages": stages_out,
        "validators": validators,
    }


def _safe_outcome(outcome: Mapping[str, Any] | None) -> dict[str, Any]:
    return _pick(outcome, _OUTCOME_FIELDS)


def _feedback_text(outcome: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in ("feedback", "user_feedback", "controller_feedback", "member_feedback_summary", "comment", "complaint"):
        value = outcome.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
        elif isinstance(value, Mapping):
            for nested_key in ("comment", "feedback", "reason", "text"):
                nested = value.get(nested_key)
                if isinstance(nested, str) and nested.strip():
                    parts.append(nested.strip())
    return " | ".join(dict.fromkeys(parts))[:3000]


def _feedback_signal(outcome: Mapping[str, Any], text: str) -> tuple[str, float]:
    lowered = text.lower()
    negative_hits = sum(term in lowered for term in _NEGATIVE_TERMS)
    positive_hits = sum(term in lowered for term in _POSITIVE_TERMS)
    scores = [
        value
        for key in ("overall_score", "score", "comfort_score", "energy_score", "vpp_score")
        if (value := _float(outcome.get(key))) is not None
    ]
    mean_score = sum(scores) / len(scores) if scores else None
    accepted = _first(outcome, "accepted", "approved")
    negative_evidence = negative_hits
    positive_evidence = positive_hits
    severity = min(1.0, 0.25 * negative_hits)
    if accepted is False:
        negative_evidence += 2
        severity = max(severity, 0.8)
    elif accepted is True:
        positive_evidence += 1
    if mean_score is not None:
        if mean_score <= 2.5:
            negative_evidence += 2
            severity = max(severity, _clamp((3.5 - mean_score) / 2.5))
        elif mean_score >= 4.0:
            positive_evidence += 2
    if negative_evidence > positive_evidence:
        return "negative", round(max(0.35, severity), 3)
    if positive_evidence > negative_evidence:
        return "positive", 0.0
    return "mixed_or_unknown", round(severity, 3)


def _explicit_observations(outcome: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_items: list[Any] = []
    for key in ("preference_observations", "belief_updates", "observed_preferences"):
        raw = outcome.get(key)
        if isinstance(raw, Mapping):
            raw_items.extend({"key": item_key, "value": value} for item_key, value in raw.items())
        elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
            raw_items.extend(raw)
    user_feedback = outcome.get("user_feedback")
    if isinstance(user_feedback, Mapping):
        for key in ("preference_observations", "belief_updates", "observed_preferences", "preferences"):
            raw = user_feedback.get(key)
            if isinstance(raw, Mapping):
                raw_items.extend({"key": item_key, "value": value} for item_key, value in raw.items())
            elif isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
                raw_items.extend(raw)

    observations: list[dict[str, Any]] = []
    for item in raw_items[:30]:
        if not isinstance(item, Mapping):
            continue
        key = _normal_key(item.get("key", ""))[:100]
        if not key or _is_forbidden_key(key) or "persona" in key or "scoring_weight" in key:
            continue
        if "value" not in item:
            continue
        reliability = _float(item.get("confidence", item.get("reliability", 0.82)))
        observations.append(
            {
                "key": key,
                "value": _json_safe(item.get("value")),
                "reliability": _clamp(reliability if reliability is not None else 0.82, 0.2, 0.98),
            }
        )
    return observations


def _outcome_contextual_observations(
    outcome: Mapping[str, Any], context_signature: str
) -> list[dict[str, Any]]:
    observations: list[dict[str, Any]] = []
    accepted = _first(outcome, "accepted", "approved")
    if isinstance(accepted, bool):
        observations.append(
            {
                "key": f"acceptance::{context_signature}",
                "value": accepted,
                "reliability": 0.9,
            }
        )
    for source_key, target_key in (
        ("overall_score", "overall_satisfaction"),
        ("score", "overall_satisfaction"),
        ("comfort_score", "comfort_satisfaction"),
        ("energy_score", "energy_satisfaction"),
        ("vpp_score", "grid_support_satisfaction"),
    ):
        number = _float(outcome.get(source_key))
        if number is None:
            continue
        observations.append(
            {
                "key": f"{target_key}::{context_signature}",
                "value": round(number, 3),
                "reliability": 0.78,
            }
        )
        if target_key == "overall_satisfaction":
            break
    return observations


def _append_revision(
    memory: dict[str, Any],
    *,
    key: str,
    previous: Mapping[str, Any] | None,
    current: Mapping[str, Any],
    evidence: Mapping[str, Any],
    contradicted: bool,
    contextual: bool,
) -> None:
    ledger = list(memory.get("profile_revision_ledger") or [])
    ledger.append(
        {
            "revision": memory["revision"],
            "belief_key": key,
            "belief_scope": "contextual" if contextual else "stable",
            "previous_value": _json_safe(previous.get("value")) if previous else None,
            "new_value": _json_safe(current.get("value")),
            "previous_confidence": float(previous.get("confidence", 0.0)) if previous else 0.0,
            "new_confidence": float(current.get("confidence", 0.0)),
            "reason": "conflicting_observable_evidence" if contradicted else "supporting_observable_evidence",
            "evidence_id": evidence["evidence_id"],
            "source": evidence["source"],
            "contradiction": contradicted,
            "observed_at": evidence["observed_at"],
        }
    )
    memory["profile_revision_ledger"] = ledger[-500:]


def update_memory(
    memory: Mapping[str, Any],
    event_context: Mapping[str, Any],
    outcome: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return memory updated from one executed event and observable feedback."""
    updated = deepcopy(dict(memory))
    if updated.get("version") != MEMORY_VERSION:
        raise ValueError(f"Expected memory version {MEMORY_VERSION!r}")
    safe_context = _json_safe(event_context)
    if safe_context.get("version") != EVENT_CONTEXT_VERSION:
        raise ValueError("event_context must be created by build_event_context")
    safe_result = _safe_outcome(outcome)
    updated["revision"] = int(updated.get("revision", 0) or 0) + 1
    observed_at = str(safe_context.get("observed_at") or _now_iso())
    updated["updated_at"] = observed_at
    event_id = str(safe_context.get("event_id") or f"event-{updated['revision']}")[:200]
    context_signature = str(safe_context.get("context_signature") or "context=unspecified")
    text = _feedback_text(safe_result)
    signal, severity = _feedback_signal(safe_result, text)

    event_record = {
        "memory_event_id": f"{updated.get('owner', {}).get('persona_id', 'unknown')}:{event_id}:{updated['revision']}",
        "event_id": event_id,
        "sequence": updated["revision"],
        "observed_at": observed_at,
        "context_signature": context_signature,
        "features": deepcopy(safe_context.get("features") or {}),
        "event": deepcopy(safe_context.get("event") or {}),
        "calendar": deepcopy(safe_context.get("calendar") or {}),
        "home_state": deepcopy(safe_context.get("home_state") or {}),
        "user_input": str(safe_context.get("user_input") or "")[:1000],
        "raw_model_plan": deepcopy(safe_context.get("raw_model_plan") or {}),
        "validated_plan": deepcopy(safe_context.get("validated_plan") or {}),
        "proposed_plan": deepcopy(safe_context.get("proposed_plan") or {}),
        "consented_plan": deepcopy(safe_context.get("consented_plan") or {}),
        "executed_plan": deepcopy(safe_context.get("executed_plan") or {}),
        "plan_lifecycle": deepcopy(safe_context.get("plan_lifecycle") or {}),
        "outcome": safe_result,
        "feedback_text": text,
        "feedback_signal": signal,
        "negative_feedback": signal == "negative",
        "negative_severity": severity,
        "provenance": {
            "context": ["event", "calendar", "home_observation"],
            "outcome": ["executed_outcome", "user_feedback"],
        },
    }
    events = list(updated.get("events") or [])
    events.append(event_record)
    updated["events"] = events[-200:]

    for index, observation in enumerate(_explicit_observations(safe_result), start=1):
        key = observation["key"]
        evidence = _evidence(
            evidence_id=f"{event_id}:feedback:{index}",
            source="user_feedback",
            value=observation["value"],
            observed_at=observed_at,
            reliability=observation["reliability"],
            event_id=event_id,
            context_signature=context_signature,
        )
        previous = deepcopy(updated["beliefs"].get(key))
        if previous:
            current, contradicted = _merge_belief(previous, observation["value"], evidence)
        else:
            current, contradicted = _new_belief(observation["value"], evidence), False
        updated["beliefs"][key] = current
        _append_revision(
            updated,
            key=key,
            previous=previous,
            current=current,
            evidence=evidence,
            contradicted=contradicted,
            contextual=False,
        )

    for index, observation in enumerate(
        _outcome_contextual_observations(safe_result, context_signature), start=1
    ):
        key = observation["key"]
        evidence = _evidence(
            evidence_id=f"{event_id}:outcome:{index}",
            source="executed_outcome",
            value=observation["value"],
            observed_at=observed_at,
            reliability=observation["reliability"],
            event_id=event_id,
            context_signature=context_signature,
        )
        previous = deepcopy(updated["contextual_beliefs"].get(key))
        if previous:
            current, contradicted = _merge_belief(previous, observation["value"], evidence)
        else:
            current, contradicted = _new_belief(observation["value"], evidence), False
        updated["contextual_beliefs"][key] = current
        _append_revision(
            updated,
            key=key,
            previous=previous,
            current=current,
            evidence=evidence,
            contradicted=contradicted,
            contextual=True,
        )
    return updated


_FEATURE_WEIGHTS = {
    "event_type": 0.19,
    "time_bucket": 0.16,
    "day_type": 0.08,
    "occupancy": 0.15,
    "price_level": 0.12,
    "duration_bucket": 0.08,
    "temperature_bucket": 0.09,
    "plan_family": 0.08,
    "calendar_constraints": 0.05,
}


def _feature_similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> tuple[float, list[str]]:
    earned = 0.0
    available = 0.0
    matched: list[str] = []
    for key, weight in _FEATURE_WEIGHTS.items():
        old = left.get(key)
        new = right.get(key)
        if old in (None, "", []) or new in (None, "", []):
            continue
        available += weight
        if key == "calendar_constraints":
            old_set = set(old if isinstance(old, list) else [old])
            new_set = set(new if isinstance(new, list) else [new])
            union = old_set | new_set
            score = len(old_set & new_set) / len(union) if union else 0.0
        else:
            score = 1.0 if _canonical(old) == _canonical(new) else 0.0
        earned += weight * score
        if score >= 0.5:
            matched.append(key)
    return ((earned / available) if available else 0.0), matched


def retrieve_relevant_events(
    memory: Mapping[str, Any],
    current_context: Mapping[str, Any],
    k: int = 3,
) -> list[dict[str, Any]]:
    """Retrieve context-similar episodes, prioritizing relevant complaints.

    Common-rank ties are deterministic.  Negative feedback receives a bounded
    bonus but cannot make a completely unrelated event dominate a strongly
    matching one.
    """
    if k <= 0:
        return []
    current_features = current_context.get("features") if isinstance(current_context, Mapping) else {}
    if not isinstance(current_features, Mapping):
        current_features = {}
    events = [event for event in list(memory.get("events") or []) if isinstance(event, Mapping)]
    total = max(1, len(events))
    ranked: list[tuple[float, int, str, dict[str, Any]]] = []
    for position, event in enumerate(events):
        similarity, matched = _feature_similarity(event.get("features") or {}, current_features)
        severity = _clamp(float(event.get("negative_severity", 0.0) or 0.0))
        negative_bonus = severity * (0.35 + (0.65 * similarity))
        recency = (position + 1) / total
        score = (0.76 * similarity) + (0.16 * negative_bonus) + (0.08 * recency)
        record = deepcopy(dict(event))
        record["retrieval"] = {
            "score": round(score, 6),
            "context_similarity": round(similarity, 6),
            "negative_priority": round(negative_bonus, 6),
            "recency": round(recency, 6),
            "matched_features": matched,
        }
        ranked.append(
            (
                score,
                int(event.get("sequence", position + 1) or position + 1),
                str(event.get("memory_event_id", "")),
                record,
            )
        )
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [item[3] for item in ranked[: int(k)]]


def _compact_belief(key: str, belief: Mapping[str, Any]) -> dict[str, Any]:
    provenance = list(belief.get("provenance") or [])
    latest = provenance[-1] if provenance else {}
    return {
        "key": key,
        "value": _json_safe(belief.get("value")),
        "confidence": round(float(belief.get("confidence", 0.0) or 0.0), 3),
        "evidence_count": int(belief.get("evidence_count", 0) or 0),
        "contradiction_count": int(belief.get("contradiction_count", 0) or 0),
        "latest_source": latest.get("source"),
        "latest_evidence_id": latest.get("evidence_id"),
    }


def _compact_event(event: Mapping[str, Any]) -> dict[str, Any]:
    outcome = event.get("outcome") if isinstance(event.get("outcome"), Mapping) else {}
    plan = event.get("executed_plan") or event.get("proposed_plan") or {}
    compact_outcome = {
        key: outcome[key]
        for key in (
            "accepted",
            "approved",
            "score",
            "overall_score",
            "comfort_score",
            "energy_score",
            "vpp_score",
            "target_achieved",
        )
        if key in outcome
    }
    return {
        "event_id": event.get("event_id"),
        "context_signature": event.get("context_signature"),
        "similarity": (event.get("retrieval") or {}).get("context_similarity"),
        "feedback_signal": event.get("feedback_signal"),
        "negative_severity": event.get("negative_severity"),
        "executed_plan": _json_safe(plan),
        "outcome": compact_outcome,
        "feedback": str(event.get("feedback_text") or "")[:500],
        "evidence_ref": event.get("memory_event_id"),
    }


def compact_memory_context(
    memory: Mapping[str, Any],
    current_context: Mapping[str, Any],
    *,
    k: int = 3,
    max_chars: int = 6000,
) -> dict[str, Any]:
    """Compile a bounded, provenance-linked prompt capsule for the planner."""
    relevant = retrieve_relevant_events(memory, current_context, k=k)
    stable = [
        _compact_belief(str(key), belief)
        for key, belief in (memory.get("beliefs") or {}).items()
        if isinstance(belief, Mapping)
    ]
    stable.sort(key=lambda item: (-item["confidence"], item["key"]))
    signature = str(current_context.get("context_signature") or "context=unspecified")
    contextual = [
        _compact_belief(str(key), belief)
        for key, belief in (memory.get("contextual_beliefs") or {}).items()
        if isinstance(belief, Mapping) and str(key).endswith(f"::{signature}")
    ]
    contextual.sort(key=lambda item: (-item["confidence"], item["key"]))
    unresolved = [
        {
            "key": item["key"],
            "confidence": item["confidence"],
            "contradiction_count": item["contradiction_count"],
            "guidance": "ask or choose a reversible low-disruption action",
        }
        for item in stable + contextual
        if item["confidence"] < 0.58 or item["contradiction_count"] > 0
    ][:8]
    capsule: dict[str, Any] = {
        "memory_version": memory.get("version"),
        "privacy_scope": "agent_observable_only",
        "current_context_signature": signature,
        "profile_beliefs": stable[:14],
        "contextual_beliefs": contextual[:8],
        "relevant_events": [_compact_event(event) for event in relevant],
        "unresolved_or_conflicting_beliefs": unresolved,
        "planner_guidance": [
            "Treat high-confidence beliefs as preferences, not safety constraints.",
            "Avoid repeating relevant negative outcomes; cite evidence_ref when it changes the plan.",
            "When evidence conflicts or confidence is low, prefer reversible action or ask the user.",
        ],
    }

    budget = max(800, int(max_chars))

    def shrink_once() -> bool:
        if capsule["relevant_events"]:
            longest = max(
                capsule["relevant_events"],
                key=lambda item: len(str(item.get("feedback", ""))) + len(_canonical(item.get("executed_plan", {}))),
            )
            if len(str(longest.get("feedback", ""))) > 120:
                longest["feedback"] = str(longest["feedback"])[:117] + "..."
                longest["executed_plan"] = {
                    key: value
                    for key, value in (longest.get("executed_plan") or {}).items()
                    if key in {"id", "strategy_id", "mode", "action", "setpoint", "setpoint_c"}
                }
                return True
            capsule["relevant_events"].pop()
            return True
        if capsule["profile_beliefs"]:
            capsule["profile_beliefs"].pop()
            return True
        if capsule["contextual_beliefs"]:
            capsule["contextual_beliefs"].pop()
            return True
        if capsule["unresolved_or_conflicting_beliefs"]:
            capsule["unresolved_or_conflicting_beliefs"].pop()
            return True
        if capsule["planner_guidance"]:
            capsule["planner_guidance"].pop()
            return True
        return False

    while len(json.dumps(capsule, ensure_ascii=False, sort_keys=True)) > budget:
        if not shrink_once():
            break

    # Include the accounting field itself in the advertised size.  A few
    # passes converge because only the digit count can change.
    capsule["serialized_chars"] = 0
    for _ in range(4):
        serialized_size = len(json.dumps(capsule, ensure_ascii=False, sort_keys=True))
        capsule["serialized_chars"] = serialized_size
    while len(json.dumps(capsule, ensure_ascii=False, sort_keys=True)) > budget:
        if not shrink_once():
            break
        capsule["serialized_chars"] = len(json.dumps(capsule, ensure_ascii=False, sort_keys=True))
    capsule["serialized_chars"] = len(json.dumps(capsule, ensure_ascii=False, sort_keys=True))
    return capsule


__all__ = [
    "MEMORY_VERSION",
    "EVENT_CONTEXT_VERSION",
    "initialize_memory",
    "build_event_context",
    "update_memory",
    "retrieve_relevant_events",
    "compact_memory_context",
]
