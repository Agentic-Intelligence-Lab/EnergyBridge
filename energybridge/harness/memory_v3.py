"""Evidence-linked, stage-aware household memory for the EnergyBridge V3 harness.

The memory is deliberately controller-observable and model-agnostic.  It is
not a persona mirror and it does not contain method/model identities or
evaluator state.  Instead, it records an auditable chain of what was proposed,
validated, consented to, executed, and observed.  Stable and contextual
beliefs remain hypotheses with provenance, decay, and contradiction state.

All public APIs return JSON-serializable dictionaries and never mutate their
inputs.  Persistence is opt-in, integrity checked, atomic, and private by
default; it is not encryption and callers should still choose a trusted path.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import stat
import tempfile
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence


MEMORY_V3_VERSION = "energybridge_evidence_memory_v3"
EVENT_CONTEXT_V3_VERSION = "energybridge_observable_event_context_v3"
PERSISTENCE_V3_FORMAT = "energybridge_memory_v3_envelope_v1"

__all__ = [
    "MEMORY_V3_VERSION",
    "EVENT_CONTEXT_V3_VERSION",
    "PERSISTENCE_V3_FORMAT",
    "EPISODE_STAGES",
    "initialize_memory_v3",
    "build_event_context_v3",
    "record_episode_stage",
    "retract_episode_stage",
    "update_memory_v3",
    "observe_belief_v3",
    "refresh_beliefs_v3",
    "migrate_v2_memory",
    "retrieve_relevant_episodes",
    "retrieve_relevant_events_v3",
    "compact_memory_context_v3",
    "save_memory_v3",
    "load_memory_v3",
]

EPISODE_STAGES = (
    "raw_proposal",
    "validated",
    "consented",
    "executed",
    "outcome",
)
_AFFIRMATIVE_EXECUTION_STATUSES = {"executed", "applied", "succeeded", "completed"}

_STABLE_SOURCES = {
    "onboarding_questionnaire",
    "user_statement",
    "user_feedback",
    "user_correction",
}
_CONTEXTUAL_SOURCES = _STABLE_SOURCES | {
    "calendar",
    "home_observation",
    "event",
    "consent_decision",
    "executed_outcome",
}
_FORBIDDEN_EXACT_KEYS = {
    "method",
    "model",
    "model_name",
    "model_id",
    "provider",
    "provider_name",
    "api_key",
    "access_token",
    "authorization",
    "credential",
    "credentials",
    "secret",
    "hidden",
    "hidden_persona",
    "latent_persona",
    "persona",
    "persona_config",
    "persona_id",
    "role_card",
    "roleplay_system_prompt",
    "system_prompt",
    "ground_truth",
    "scoring_weights",
    "evaluator",
    "evaluator_state",
    "acceptance_target",
    "target_acceptance",
    "acceptance_probability",
    "override_probability",
    "vpp_override_prob",
    "api_base",
    "base_url",
}
_FORBIDDEN_KEY_FRAGMENTS = (
    "hidden_persona",
    "latent_persona",
    "role_card",
    "roleplay_prompt",
    "system_prompt",
    "scoring_weight",
    "ground_truth",
    "evaluator_",
    "api_key",
    "access_token",
    "developer_message",
    "developer_instruction",
    "private_key",
    "endpoint_url",
    "llm_host",
    "api_base",
    "base_url",
)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"(?i)\bToken\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(
        r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
        r"authorization|password)\s*[:=]\s*\S+"
    ),
    re.compile(
        r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?"
        r"(?:-----END [^-\r\n]*PRIVATE KEY-----|$)",
        re.IGNORECASE | re.DOTALL,
    ),
)
_PLANNER_IDENTITY_PATTERN = re.compile(
    r"\b(?:MPC|RL|PPO|HEMA|HAMA|EnergyBridge|OpenAI|GPT(?:-[\w.]+)?|"
    r"Claude|Gemini|Llama|Qwen|DeepSeek)\b",
    re.IGNORECASE,
)
_URL_ENDPOINT_PATTERN = re.compile(
    r"\b(?:https?|wss?)://[^\s<>\"'\[\]{}()]+", re.IGNORECASE
)
_BARE_HOST_ENDPOINT_PATTERN = re.compile(
    r"(?<![@\w.])(?:localhost|"
    r"(?:[a-z0-9_](?:[a-z0-9_-]{0,62})\.)+[a-z][a-z0-9_-]{1,62})"
    r"(?![a-z])(?::\d{2,5})?"
    r"(?:/[^\s<>\"'\[\]{}()]*)?",
    re.IGNORECASE,
)
_LABELLED_DOTTED_HOST_ENDPOINT_PATTERN = re.compile(
    r"\b(?:endpoint|host|server|api[_ -]?base|base[_ -]?url)\s*"
    r"(?:(?:[:=]|\bis\b)\s*)?"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,62})\.)+[a-z0-9-]{2,63}"
    r"(?::\d{2,5})?(?:/[^\s<>\"'\[\]{}()]*)?",
    re.IGNORECASE,
)
_SINGLE_LABEL_HOST_ENDPOINT_PATTERN = re.compile(
    r"\b(?:endpoint|host|server)\s+(?=[a-z0-9-]{2,64}:)(?=[a-z0-9-]*[a-z-])"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?):\d{2,5}"
    r"(?:/[^\s<>\"'\[\]{}()]*)?",
    re.IGNORECASE,
)
_IP_ENDPOINT_PATTERN = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?(?:/[^\s<>\"'\[\]{}()]*)?"
)
_IPV6_ENDPOINT_PATTERN = re.compile(
    r"\b(?:endpoint|host|server)\s+\[[0-9a-f:]{2,}\]"
    r"(?::\d{2,5})?(?:/[^\s<>\"'\[\]{}()]*)?",
    re.IGNORECASE,
)
_BARE_CREDENTIAL_PATTERN = re.compile(
    r"\b(?:api[_ -]?key|access[_ -]?key|secret[_ -]?key|credential|password|"
    r"passwd|secret|token)\b(?:\s*(?::|=|\bis\b)\s*|\s+)"
    r"(?:\"[^\"]{4,}\"|'[^']{4,}'|[A-Za-z0-9._~+/=-]{6,})",
    re.IGNORECASE,
)
_GENERIC_KEY_VALUE_PATTERN = re.compile(
    r"(?i:\bkey\b)(?:\s*(?::|=|\bis\b)\s*|\s+)"
    r"(?:\"[^\"]{4,}\"|'[^']{4,}'|[A-Za-z0-9._~+/=-]{16,}|"
    r"(?=[A-Za-z0-9._~+/=-]{6,})(?=[A-Za-z0-9._~+/=-]*[0-9._~+/=-])"
    r"[A-Za-z0-9._~+/=-]{6,}|[A-Z]{6,})"
)
_UNKNOWN_TECH_IDENTITY_PATTERN = re.compile(
    r"\b[A-Z][A-Za-z0-9]*(?:[-_]?(?:Cloud|Solver|Planner|Optimizer|Controller|"
    r"Provider|Model|Agent|Algorithm|LLM|API))(?:V?\d+)?\b"
)
_LABELLED_TECH_IDENTITY_PATTERN = re.compile(
    r"(?<!utility\s)(?<!energy\s)(?<!service\s)"
    r"\b(?:method|provider|model|planner|solver|optimizer|controller|algorithm|"
    r"vendor|backend|deployment|agent|llm)(?:\s+(?:name|id))?"
    r"\s*(?:(?::|=|\bis\b)\s*|\s+)"
    r"(?!(?:identity|behavior|behaviour|configuration|settings|context|preference|"
    r"choices|charges|service|information|data|facts?|unknown|unspecified|omitted|system)\b)"
    r"(?:\"[^\"]{1,120}\"|'[^']{1,120}'|[^\s,;]+)",
    re.IGNORECASE,
)

_EVENT_FIELDS = {
    "id",
    "event_id",
    "date",
    "day",
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
    "expected_benefit",
    "predicted_comfort",
    "predicted_cost",
    "predicted_grid_support",
    "memory_citations",
    "alternatives_considered",
    "uncertainty",
    "fallback_after_vpp_rejection",
    # Ordered actuator-facing dispatches that jointly produced one event
    # outcome.  The latest top-level setpoint/actions remain available for
    # similarity features while the full causal exposure is retained.
    "execution_exposures",
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
    "measurements",
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
    "missed",
    "不舒服",
    "太热",
    "太冷",
    "打扰",
    "干扰",
    "不同意",
    "拒绝",
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
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value).strip())
    return re.sub(r"[^a-z0-9_]+", "_", split.lower()).strip("_")


def _forbidden_key(key: object) -> bool:
    raw_key = str(key)
    key_text_for_secret_scan = raw_key.replace("_", " ")
    if any(pattern.search(key_text_for_secret_scan) for pattern in (
        *_SECRET_PATTERNS,
        _URL_ENDPOINT_PATTERN,
        _LABELLED_DOTTED_HOST_ENDPOINT_PATTERN,
        _BARE_HOST_ENDPOINT_PATTERN,
        _SINGLE_LABEL_HOST_ENDPOINT_PATTERN,
        _IP_ENDPOINT_PATTERN,
        _IPV6_ENDPOINT_PATTERN,
        _BARE_CREDENTIAL_PATTERN,
        _GENERIC_KEY_VALUE_PATTERN,
    )):
        return True
    normalized = _normal_key(key)
    if normalized in _FORBIDDEN_EXACT_KEYS:
        return True
    if normalized.startswith(("hidden_", "secret_", "credential_")):
        return True
    tokens = set(normalized.split("_"))
    if tokens & {
        "method", "model", "provider", "vendor", "planner", "algorithm",
        "evaluator", "persona", "auth", "authorization",
        "bearer", "credential", "credentials", "password", "secret", "token",
        "developer", "private", "endpoint", "host", "llm", "api",
        "acceptance", "override",
    }:
        return True
    if {"private", "key"}.issubset(tokens):
        return True
    return any(fragment in normalized for fragment in _FORBIDDEN_KEY_FRAGMENTS)


def _redact_text(value: object, limit: int = 2000) -> str:
    text = str(value)
    if re.fullmatch(r"energybridge(?:[._][A-Za-z0-9_-]+)+", text, re.IGNORECASE):
        return text[:limit]
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted credential]", text)
    for pattern in (
        _URL_ENDPOINT_PATTERN,
        _IP_ENDPOINT_PATTERN,
        _IPV6_ENDPOINT_PATTERN,
        _LABELLED_DOTTED_HOST_ENDPOINT_PATTERN,
        _BARE_HOST_ENDPOINT_PATTERN,
        _SINGLE_LABEL_HOST_ENDPOINT_PATTERN,
    ):
        text = pattern.sub("[private endpoint]", text)
    text = _BARE_CREDENTIAL_PATTERN.sub("[redacted credential]", text)
    text = _GENERIC_KEY_VALUE_PATTERN.sub("[redacted credential]", text)
    text = _LABELLED_TECH_IDENTITY_PATTERN.sub("planning system", text)
    text = _UNKNOWN_TECH_IDENTITY_PATTERN.sub("planning system", text)
    if re.fullmatch(r"energybridge[._][A-Za-z0-9_.-]+", text, re.IGNORECASE) is None:
        text = _PLANNER_IDENTITY_PATTERN.sub("planning system", text)
    return text[:limit]


def _safe(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded JSON-compatible copy without inspecting banned values."""
    if depth > 8:
        return "[depth limit]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Path):
        return _redact_text(value)
    if isinstance(value, Mapping):
        # Preference/belief observations commonly encode their destination as
        # data (``{"key": ..., "value": ...}``) rather than a mapping key.
        # Reject the whole leaf before inspecting/copying its private value.
        for semantic_field in ("key", "belief_key", "field", "attribute"):
            semantic_key = value.get(semantic_field)
            if isinstance(semantic_key, str) and _forbidden_key(semantic_key):
                return {}
        output: dict[str, Any] = {}
        for key, item in value.items():
            if _forbidden_key(key):
                continue
            output[str(key)[:160]] = _safe(item, depth=depth + 1)
        return output
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_safe(item, depth=depth + 1) for item in list(value)[:120]]
    return _redact_text(value, 500)


def _contains_forbidden_key(value: Any, *, depth: int = 0) -> bool:
    if depth > 12:
        return False
    if isinstance(value, Mapping):
        for key, item in value.items():
            if _forbidden_key(key) or _contains_forbidden_key(item, depth=depth + 1):
                return True
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return any(_contains_forbidden_key(item, depth=depth + 1) for item in value)
    return False


def _pick(mapping: Mapping[str, Any] | None, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(mapping, Mapping):
        return {}
    return {
        str(key): _safe(value)
        for key, value in mapping.items()
        if _normal_key(key) in allowed and not _forbidden_key(key)
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
    return json.dumps(_safe(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _values_support(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return isinstance(left, bool) and isinstance(right, bool) and left is right
    left_number = _float(left)
    right_number = _float(right)
    if left_number is not None and right_number is not None:
        tolerance = max(0.1, 0.1 * max(abs(left_number), abs(right_number), 1.0))
        return abs(left_number - right_number) <= tolerance
    return _canonical(left) == _canonical(right)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()[:20]


def _parse_time(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    elif value:
        text = str(value).strip().replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _timestamp(value: Any = None) -> str:
    parsed = _parse_time(value)
    return parsed.isoformat() if parsed else (_redact_text(value, 100) if value else _now_iso())


def _age_days(observed_at: Any, reference_at: Any) -> float:
    observed = _parse_time(observed_at)
    reference = _parse_time(reference_at)
    if observed is None or reference is None:
        return 0.0
    return max(0.0, (reference - observed).total_seconds() / 86400.0)


def _onboarding_view(questionnaire: Mapping[str, Any] | None) -> dict[str, Any]:
    questionnaire = questionnaire if isinstance(questionnaire, Mapping) else {}
    answers: list[dict[str, Any]] = []
    raw_answers = questionnaire.get("answers")
    if isinstance(raw_answers, Mapping):
        raw_answers = [
            {"id": question_id, "answer": answer}
            for question_id, answer in raw_answers.items()
        ]
    for item in list(raw_answers or [])[:30]:
        if not isinstance(item, Mapping):
            continue
        question_id = _normal_key(item.get("id", ""))[:100]
        if not question_id or _forbidden_key(question_id):
            continue
        answers.append(
            {
                "id": question_id,
                "question": _redact_text(item.get("question", ""), 500),
                "answer": _redact_text(item.get("answer", ""), 1000),
                "selected_option_ids": [
                    _redact_text(option, 100)
                    for option in list(item.get("selected_option_ids") or [])[:12]
                ],
            }
        )
    return {
        "source": "onboarding_questionnaire",
        "answers": answers,
    }


def _new_memory(household_id: str, created_at: str, onboarding: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "version": MEMORY_V3_VERSION,
        "owner": {"household_id": _redact_text(household_id or "unknown", 200)},
        "privacy_boundary": {
            "scope": "controller_observable_only",
            "identity_blind": True,
            "excluded": [
                "hidden persona or role card",
                "evaluator state or target score",
                "method, model, or provider identity",
                "credentials and private prompts",
            ],
        },
        "created_at": created_at,
        "updated_at": created_at,
        "revision": 0,
        "onboarding": _safe(onboarding),
        "stable_beliefs": {},
        "contextual_beliefs": {},
        "belief_revision_ledger": [],
        "episodes": [],
        "migration": None,
    }


def initialize_memory_v3(
    onboarding_questionnaire: Mapping[str, Any] | None = None,
    household_id: str | None = None,
    **legacy_ignored: Any,
) -> dict[str, Any]:
    """Initialize observable-only V3 memory.

    Extra legacy keywords are accepted so V2 call sites can migrate without
    plumbing changes.  They are intentionally neither inspected nor stored.
    In particular, controller method/model identity cannot enter V3 memory.
    """
    del legacy_ignored
    created_at = _now_iso()
    onboarding = _onboarding_view(onboarding_questionnaire)
    memory = _new_memory(str(household_id or "unknown"), created_at, onboarding)
    for index, answer in enumerate(onboarding["answers"], start=1):
        selected = list(answer.get("selected_option_ids") or [])
        value: Any = selected[0] if len(selected) == 1 else selected
        reliability = 0.82
        if not selected:
            value = answer.get("answer", "")
            reliability = 0.62
        if value in (None, "", []):
            continue
        memory = observe_belief_v3(
            memory,
            key=str(answer["id"]),
            value=value,
            source="onboarding_questionnaire",
            evidence_id=f"onboarding:{index}:{answer['id']}",
            reliability=reliability,
            observed_at=created_at,
            scope="stable",
        )
    return memory


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def _time_bucket(hour: Any) -> str | None:
    number = _float(hour)
    if number is None:
        return None
    hour_of_day = number % 24.0
    if hour_of_day < 6:
        return "overnight"
    if hour_of_day < 12:
        return "morning"
    if hour_of_day < 17:
        return "afternoon"
    if hour_of_day < 21:
        return "evening"
    return "late_evening"


def _duration_bucket(event: Mapping[str, Any]) -> str | None:
    duration = _float(event.get("duration_h"))
    if duration is None:
        start = _float(_first(event, "trigger_h", "start_h"))
        end = _float(event.get("end_h"))
        if start is not None and end is not None:
            duration = max(0.0, end - start)
    if duration is None:
        return None
    if duration <= 1:
        return "short"
    if duration <= 2.5:
        return "medium"
    return "long"


def _temperature_bucket(event: Mapping[str, Any], home: Mapping[str, Any]) -> str | None:
    value = _first(event, "outdoor_temp_c", "outdoor_temperature_c")
    if value is None:
        value = home.get("outdoor_temp_c")
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


def _occupancy(calendar: Mapping[str, Any], home: Mapping[str, Any]) -> str | None:
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
    normalized = _normal_key(value)
    if normalized in {"home", "occupied", "present", "yes"}:
        return "occupied"
    if normalized in {"away", "unoccupied", "absent", "no"}:
        return "unoccupied"
    return normalized[:60]


def _constraint_tokens(calendar: Mapping[str, Any]) -> list[str]:
    raw = calendar.get("constraints") or calendar.get("protected_windows") or []
    if isinstance(raw, Mapping):
        values = list(raw.keys())
    elif isinstance(raw, str):
        values = [raw]
    elif isinstance(raw, Sequence):
        values = list(raw)
    else:
        values = []
    return sorted({_normal_key(value)[:80] for value in values if str(value).strip()})[:12]


def _action_tokens(plan: Mapping[str, Any]) -> list[str]:
    tokens: set[str] = set()
    for key in ("mode", "action", "strategy_id", "name"):
        if plan.get(key) not in (None, ""):
            tokens.add(_normal_key(plan[key])[:80])
    for key in ("actions", "appliance_actions", "appliances"):
        actions = plan.get(key)
        if isinstance(actions, Mapping):
            tokens.update(_normal_key(name)[:80] for name in actions)
        elif isinstance(actions, Sequence) and not isinstance(actions, (str, bytes, bytearray)):
            for action in actions[:20]:
                if isinstance(action, Mapping):
                    name = _first(action, "device", "appliance", "name", "action")
                    if name:
                        tokens.add(_normal_key(name)[:80])
                elif str(action).strip():
                    tokens.add(_normal_key(action)[:80])
    if _first(plan, "setpoint", "setpoint_c") is not None:
        tokens.add("thermostat")
    return sorted(token for token in tokens if token)[:16]


def _features(
    event: Mapping[str, Any],
    calendar: Mapping[str, Any],
    home: Mapping[str, Any],
    plan: Mapping[str, Any],
) -> dict[str, Any]:
    event_type = _first(event, "event_type", "type", "signal_type")
    day_type = _first(calendar, "day_type", "weekday")
    return {
        "event_type": _normal_key(event_type)[:80] if event_type is not None else None,
        "time_bucket": _time_bucket(_first(event, "trigger_h", "start_h")),
        "day_type": _normal_key(day_type)[:60] if day_type is not None else None,
        "occupancy": _occupancy(calendar, home),
        "price_level": _normal_key(event.get("price_level"))[:60]
        if event.get("price_level") is not None
        else None,
        "duration_bucket": _duration_bucket(event),
        "temperature_bucket": _temperature_bucket(event, home),
        "calendar_constraints": _constraint_tokens(calendar),
        "action_tokens": _action_tokens(plan),
    }


def _context_signature(features: Mapping[str, Any]) -> str:
    keys = ("event_type", "time_bucket", "day_type", "occupancy", "price_level", "duration_bucket")
    parts = [f"{key}={features[key]}" for key in keys if features.get(key) not in (None, "", [])]
    return "|".join(parts) or "context=unspecified"


def build_event_context_v3(
    event: Mapping[str, Any] | None = None,
    *,
    calendar: Mapping[str, Any] | None = None,
    home_state: Mapping[str, Any] | None = None,
    user_input: str | None = None,
    raw_proposal: Mapping[str, Any] | None = None,
    raw_model_plan: Mapping[str, Any] | None = None,
    validated_plan: Mapping[str, Any] | None = None,
    proposed_plan: Mapping[str, Any] | None = None,
    consented_plan: Mapping[str, Any] | None = None,
    executed_plan: Mapping[str, Any] | None = None,
    observed_at: str | datetime | None = None,
    observations: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a V3 context using only controller-observable allowlisted data.

    ``raw_model_plan`` is accepted only as a V2 compatibility spelling.  The
    resulting schema deliberately calls it ``raw_proposal`` and stores no
    generator identity.
    """
    event_view = _pick(event, _EVENT_FIELDS)
    calendar_view = _pick(calendar, _CALENDAR_FIELDS)
    home_view = _pick(home_state, _HOME_FIELDS)
    raw_view = _pick(raw_proposal if raw_proposal is not None else raw_model_plan, _PLAN_FIELDS)
    validated_view = _pick(validated_plan, _PLAN_FIELDS)
    proposed_view = _pick(proposed_plan, _PLAN_FIELDS)
    consented_view = _pick(consented_plan, _PLAN_FIELDS)
    executed_view = _pick(executed_plan, _PLAN_FIELDS)
    active = executed_view or consented_view or proposed_view or validated_view or raw_view
    features = _features(event_view, calendar_view, home_view, active)
    return {
        "version": EVENT_CONTEXT_V3_VERSION,
        "observed_at": _timestamp(observed_at),
        "event_id": _redact_text(_first(event_view, "event_id", "id") or "", 200),
        "event": event_view,
        "calendar": calendar_view,
        "home_state": home_view,
        "user_input": _redact_text(user_input or "", 2000),
        "raw_proposal": raw_view,
        "validated_plan": validated_view,
        "proposed_plan": proposed_view,
        "consented_plan": consented_view,
        "executed_plan": executed_view,
        "observations": _safe(observations or {}),
        "features": features,
        "context_signature": _context_signature(features),
        "source_scope": "controller_observable_only",
    }


def _evidence_item(
    *,
    evidence_id: str,
    value: Any,
    source: str,
    reliability: float,
    observed_at: str,
    episode_id: str | None,
    context_signature: str | None,
) -> dict[str, Any]:
    item = {
        "evidence_id": _redact_text(evidence_id, 240),
        "source": source,
        "value": _safe(value),
        "reliability": round(_clamp(reliability, 0.05, 1.0), 4),
        "observed_at": _timestamp(observed_at),
    }
    if episode_id:
        item["episode_id"] = _redact_text(episode_id, 200)
    if context_signature:
        item["context_signature"] = _redact_text(context_signature, 600)
    return item


def _recompute_belief(
    belief: Mapping[str, Any],
    *,
    reference_at: str,
) -> dict[str, Any]:
    result = deepcopy(dict(belief))
    half_life_days = max(1.0, float(result.get("half_life_days", 90.0) or 90.0))
    evidence = [item for item in list(result.get("evidence") or []) if isinstance(item, Mapping)]
    candidates: dict[str, dict[str, Any]] = {}
    for item in evidence:
        value = _safe(item.get("value"))
        matching = next(
            (
                candidate
                for candidate in candidates.values()
                if _values_support(candidate.get("value"), value)
            ),
            None,
        )
        candidate_id = str(matching.get("candidate_id")) if matching else _fingerprint(value)
        age = _age_days(item.get("observed_at"), reference_at)
        decay = 0.5 ** (age / half_life_days)
        weight = float(item.get("reliability", 0.5) or 0.5) * decay
        candidate = candidates.setdefault(
            candidate_id,
            {"candidate_id": candidate_id, "value": value, "weight": 0.0, "latest_at": ""},
        )
        candidate["weight"] += weight
        timestamp = str(item.get("observed_at") or "")
        if timestamp > str(candidate.get("latest_at") or ""):
            candidate["latest_at"] = timestamp
    ordered = sorted(
        candidates.values(),
        key=lambda item: (-float(item["weight"]), str(item["candidate_id"])),
    )
    total_weight = sum(float(item["weight"]) for item in ordered)
    winner_weight = float(ordered[0]["weight"]) if ordered else 0.0
    winner_share = winner_weight / total_weight if total_weight else 0.0
    strength = 1.0 - math.exp(-total_weight)
    confidence = strength * (0.42 + (0.58 * winner_share))
    winner_value = ordered[0]["value"] if ordered else None
    contradiction_count = sum(
        1 for item in evidence if ordered and not _values_support(winner_value, item.get("value"))
    )
    newest = max((str(item.get("observed_at") or "") for item in evidence), default="")
    newest_age = _age_days(newest, reference_at)
    if not ordered:
        status = "empty"
        value = None
    else:
        value = ordered[0]["value"]
        if newest_age > 2.0 * half_life_days:
            status = "stale"
        elif contradiction_count and winner_share < 0.72:
            status = "conflicted"
        elif confidence >= 0.62:
            status = "supported"
        else:
            status = "provisional"
    result.update(
        {
            "value": value,
            "confidence": round(_clamp(confidence, 0.0, 0.99), 4),
            "status": status,
            "evidence_count": len(evidence),
            "contradiction_count": contradiction_count,
            "effective_evidence_weight": round(total_weight, 4),
            "candidate_distribution": [
                {
                    **item,
                    "weight": round(float(item["weight"]), 4),
                    "share": round(float(item["weight"]) / total_weight, 4) if total_weight else 0.0,
                }
                for item in ordered[:8]
            ],
            "last_observed_at": newest or None,
            "evaluated_at": reference_at,
        }
    )
    return result


def observe_belief_v3(
    memory: Mapping[str, Any],
    *,
    key: str,
    value: Any,
    source: str,
    evidence_id: str,
    reliability: float = 0.8,
    observed_at: str | datetime | None = None,
    scope: str = "stable",
    context_signature: str | None = None,
    episode_id: str | None = None,
    half_life_days: float | None = None,
) -> dict[str, Any]:
    """Add one provenance-linked observation to a stable/contextual belief."""
    if str(memory.get("version")) != MEMORY_V3_VERSION:
        raise ValueError(f"Expected memory version {MEMORY_V3_VERSION!r}")
    normalized_key = _normal_key(key)[:120]
    if not normalized_key or _forbidden_key(normalized_key):
        raise ValueError("belief key is empty or outside the observable memory boundary")
    if scope not in {"stable", "contextual"}:
        raise ValueError("scope must be 'stable' or 'contextual'")
    allowed = _STABLE_SOURCES if scope == "stable" else _CONTEXTUAL_SOURCES
    if source not in allowed:
        raise ValueError(f"source {source!r} cannot update {scope} beliefs")
    if scope == "contextual" and not context_signature:
        raise ValueError("contextual beliefs require context_signature")
    if not str(evidence_id).strip():
        raise ValueError("evidence_id is required")

    updated = deepcopy(dict(memory))
    timestamp = _timestamp(observed_at)
    container_key = "stable_beliefs" if scope == "stable" else "contextual_beliefs"
    belief_id = normalized_key if scope == "stable" else f"{normalized_key}::{context_signature}"
    container = dict(updated.get(container_key) or {})
    previous = deepcopy(container.get(belief_id))
    belief = deepcopy(previous) if isinstance(previous, Mapping) else {
        "belief_id": belief_id,
        "key": normalized_key,
        "scope": scope,
        "context_signature": context_signature if scope == "contextual" else None,
        "half_life_days": max(1.0, float(half_life_days or (120.0 if scope == "stable" else 35.0))),
        "evidence": [],
    }
    evidence = list(belief.get("evidence") or [])
    if any(str(item.get("evidence_id")) == str(evidence_id) for item in evidence if isinstance(item, Mapping)):
        return deepcopy(dict(memory))
    updated["revision"] = int(updated.get("revision", 0) or 0) + 1
    updated["updated_at"] = timestamp
    item = _evidence_item(
        evidence_id=evidence_id,
        value=value,
        source=source,
        reliability=float(reliability),
        observed_at=timestamp,
        episode_id=episode_id,
        context_signature=context_signature,
    )
    evidence.append(item)
    belief["evidence"] = evidence[-60:]
    belief = _recompute_belief(belief, reference_at=timestamp)
    container[belief_id] = belief
    updated[container_key] = container
    ledger = list(updated.get("belief_revision_ledger") or [])
    ledger.append(
        {
            "revision": updated["revision"],
            "belief_id": belief_id,
            "scope": scope,
            "previous_value": _safe(previous.get("value")) if isinstance(previous, Mapping) else None,
            "new_value": _safe(belief.get("value")),
            "previous_confidence": float(previous.get("confidence", 0.0)) if isinstance(previous, Mapping) else 0.0,
            "new_confidence": float(belief.get("confidence", 0.0)),
            "status": belief.get("status"),
            "evidence_id": item["evidence_id"],
            "source": source,
            "observed_at": timestamp,
        }
    )
    updated["belief_revision_ledger"] = ledger[-800:]
    return updated


def refresh_beliefs_v3(
    memory: Mapping[str, Any], reference_at: str | datetime | None = None
) -> dict[str, Any]:
    """Return a copy with time-decayed belief confidence recomputed."""
    if str(memory.get("version")) != MEMORY_V3_VERSION:
        raise ValueError(f"Expected memory version {MEMORY_V3_VERSION!r}")
    updated = deepcopy(dict(memory))
    timestamp = _timestamp(reference_at)
    for container_key in ("stable_beliefs", "contextual_beliefs"):
        updated[container_key] = {
            str(key): _recompute_belief(belief, reference_at=timestamp)
            for key, belief in (updated.get(container_key) or {}).items()
            if isinstance(belief, Mapping) and not _forbidden_key(key)
        }
    return updated


def _feedback_text(outcome: Mapping[str, Any]) -> str:
    parts: list[str] = []
    for key in (
        "feedback",
        "user_feedback",
        "controller_feedback",
        "member_feedback_summary",
        "comment",
        "complaint",
    ):
        value = outcome.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(_redact_text(value.strip(), 1200))
        elif isinstance(value, Mapping):
            for nested_key in ("comment", "feedback", "reason", "text"):
                nested = value.get(nested_key)
                if isinstance(nested, str) and nested.strip():
                    parts.append(_redact_text(nested.strip(), 1200))
    return " | ".join(dict.fromkeys(parts))[:3000]


def _feedback_signal(outcome: Mapping[str, Any], text: str) -> tuple[str, float]:
    lowered = text.lower()
    negative_hits = sum(term in lowered for term in _NEGATIVE_TERMS)
    positive_hits = sum(term in lowered for term in _POSITIVE_TERMS)
    scores = [
        number
        for key in ("overall_score", "score", "comfort_score", "energy_score", "vpp_score")
        if (number := _float(outcome.get(key))) is not None
    ]
    mean_score = sum(scores) / len(scores) if scores else None
    accepted = _first(outcome, "accepted", "approved")
    severity = min(1.0, 0.25 * negative_hits)
    if accepted is False:
        negative_hits += 2
        severity = max(severity, 0.8)
    elif accepted is True:
        positive_hits += 1
    if mean_score is not None:
        if mean_score <= 2.5:
            negative_hits += 2
            severity = max(severity, _clamp((3.5 - mean_score) / 2.5))
        elif mean_score >= 4.0:
            positive_hits += 2
    if negative_hits > positive_hits:
        return "negative", round(max(0.35, severity), 3)
    if positive_hits > negative_hits:
        return "positive", 0.0
    return "mixed_or_unknown", round(severity, 3)


def _explicit_preference_observations(outcome: Mapping[str, Any]) -> list[dict[str, Any]]:
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

    result: list[dict[str, Any]] = []
    for item in raw_items[:40]:
        if not isinstance(item, Mapping) or "value" not in item:
            continue
        key = _normal_key(item.get("key", ""))[:120]
        if not key or _forbidden_key(key):
            continue
        reliability = _float(item.get("confidence", item.get("reliability", 0.82)))
        result.append(
            {
                "key": key,
                "value": _safe(item.get("value")),
                "reliability": _clamp(reliability if reliability is not None else 0.82, 0.2, 0.98),
            }
        )
    return result


def _plan_from_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    candidate = payload.get("plan") if isinstance(payload.get("plan"), Mapping) else payload
    return _pick(candidate, _PLAN_FIELDS)


def _changed_plan_fields(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    keys = sorted(set(before) | set(after))
    return [key for key in keys if _canonical(before.get(key)) != _canonical(after.get(key))][:40]


def _coerce_event_context(context: Mapping[str, Any]) -> dict[str, Any]:
    version = str(context.get("version") or "")
    if version == EVENT_CONTEXT_V3_VERSION:
        return build_event_context_v3(
            context.get("event"),
            calendar=context.get("calendar"),
            home_state=context.get("home_state"),
            user_input=str(context.get("user_input") or ""),
            raw_proposal=context.get("raw_proposal"),
            validated_plan=context.get("validated_plan"),
            proposed_plan=context.get("proposed_plan"),
            consented_plan=context.get("consented_plan"),
            executed_plan=context.get("executed_plan"),
            observed_at=context.get("observed_at"),
            observations=context.get("observations"),
        )
    if version == "energybridge_observable_event_context_v2":
        return build_event_context_v3(
            context.get("event"),
            calendar=context.get("calendar"),
            home_state=context.get("home_state"),
            user_input=str(context.get("user_input") or ""),
            raw_model_plan=context.get("raw_model_plan"),
            validated_plan=context.get("validated_plan"),
            proposed_plan=context.get("proposed_plan"),
            consented_plan=context.get("consented_plan"),
            executed_plan=context.get("executed_plan"),
            observed_at=context.get("observed_at"),
            observations=context.get("observations"),
        )
    raise ValueError("event_context must be created by build_event_context_v3 or the V2 builder")


def _episode_context(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: deepcopy(context.get(key))
        for key in (
            "event_id",
            "observed_at",
            "event",
            "calendar",
            "home_state",
            "user_input",
            "observations",
            "features",
            "context_signature",
        )
    }


def _stage_record(
    stage: str,
    payload: Mapping[str, Any],
    *,
    episode_id: str,
    revision: int,
    observed_at: str,
) -> dict[str, Any]:
    plan = _plan_from_payload(payload) if stage != "outcome" else {}
    record: dict[str, Any] = {
        "stage": stage,
        "recorded_at": observed_at,
        "evidence_id": f"{episode_id}:{stage}:{revision}",
    }
    if plan:
        record["plan"] = plan
        record["plan_fingerprint"] = _fingerprint(plan)
    if stage == "validated":
        record["status"] = _redact_text(payload.get("status", "validated"), 120)
        record["checks"] = _safe(list(payload.get("checks") or payload.get("validators") or [])[:30])
        record["patches"] = _safe(list(payload.get("patches") or [])[:60])
        record["reason"] = _redact_text(payload.get("reason", ""), 800)
    elif stage == "consented":
        decision = _first(payload, "accepted", "approved", "decision")
        record["decision"] = _safe(decision)
        record["requested_changes"] = _safe(payload.get("requested_changes") or [])
        record["feedback"] = _redact_text(payload.get("feedback", ""), 1200)
    elif stage == "executed":
        record["status"] = _redact_text(payload.get("status", "executed"), 120)
        record["execution_window"] = _safe(payload.get("execution_window") or {})
        record["execution_notes"] = _redact_text(payload.get("execution_notes", ""), 1000)
    elif stage == "outcome":
        safe_outcome = _pick(payload, _OUTCOME_FIELDS)
        text = _feedback_text(safe_outcome)
        signal, severity = _feedback_signal(safe_outcome, text)
        record.update(
            {
                "observations": safe_outcome,
                "feedback_text": text,
                "feedback_signal": signal,
                "negative_severity": severity,
            }
        )
    else:
        record["status"] = _redact_text(payload.get("status", stage), 120)
    return record


def _rebuild_causal_chain(episode: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    stages = episode.get("stages") if isinstance(episode.get("stages"), Mapping) else {}
    chain: list[dict[str, Any]] = []
    previous_name: str | None = None
    previous_plan: Mapping[str, Any] = {}
    for name in EPISODE_STAGES:
        stage = stages.get(name)
        if not isinstance(stage, Mapping):
            continue
        plan = stage.get("plan") if isinstance(stage.get("plan"), Mapping) else {}
        edge = {
            "stage": name,
            "evidence_id": stage.get("evidence_id"),
            "plan_fingerprint": stage.get("plan_fingerprint"),
        }
        if previous_name is not None:
            edge["from_stage"] = previous_name
            if plan and previous_plan:
                edge["changed_fields"] = _changed_plan_fields(previous_plan, plan)
        chain.append(edge)
        previous_name = name
        if plan:
            previous_plan = plan

    raw = stages.get("raw_proposal") if isinstance(stages.get("raw_proposal"), Mapping) else {}
    validated = stages.get("validated") if isinstance(stages.get("validated"), Mapping) else {}
    consented = stages.get("consented") if isinstance(stages.get("consented"), Mapping) else {}
    executed = stages.get("executed") if isinstance(stages.get("executed"), Mapping) else {}
    outcome = stages.get("outcome") if isinstance(stages.get("outcome"), Mapping) else {}
    decision_exposure = consented.get("plan_fingerprint") or validated.get("plan_fingerprint") or raw.get("plan_fingerprint")
    executed_exposure = executed.get("plan_fingerprint")
    executed_at = _parse_time(executed.get("recorded_at")) if executed_exposure else None
    outcome_at = _parse_time(outcome.get("recorded_at")) if outcome else None
    if not outcome:
        outcome_attribution = "not_observed"
    elif not executed_exposure:
        outcome_attribution = "unattributed_no_execution_evidence"
    elif executed_at is None or outcome_at is None:
        outcome_attribution = "unattributed_temporal_order_unknown"
    elif executed_at > outcome_at:
        outcome_attribution = "unattributed_temporal_order_invalid"
    else:
        outcome_attribution = "observational_executed_plan"
    attribution = {
        "decision_exposure_fingerprint": decision_exposure,
        "executed_exposure_fingerprint": executed_exposure,
        "consent_observed": "decision" in consented,
        "execution_observed": bool(executed_exposure),
        "outcome_observed": bool(outcome),
        "outcome_attribution": outcome_attribution,
        "causal_claim": "none_single_episode_observational_only",
    }
    if validated.get("plan") and executed.get("plan"):
        attribution["validation_to_execution_changed_fields"] = _changed_plan_fields(
            validated["plan"], executed["plan"]
        )
    return chain, attribution


def _recompute_integrity_flags(episode: Mapping[str, Any]) -> list[str]:
    """Derive integrity flags from the complete latest-stage projection."""
    stages = episode.get("stages") if isinstance(episode.get("stages"), Mapping) else {}
    flags: list[str] = []
    for stage_position, stage_name in enumerate(EPISODE_STAGES):
        if not isinstance(stages.get(stage_name), Mapping):
            continue
        for predecessor in EPISODE_STAGES[:stage_position]:
            if not isinstance(stages.get(predecessor), Mapping):
                flags.append(f"missing_observation:{predecessor}")

    outcome = stages.get("outcome") if isinstance(stages.get("outcome"), Mapping) else {}
    executed = stages.get("executed") if isinstance(stages.get("executed"), Mapping) else {}
    attribution = (
        episode.get("causal_attribution")
        if isinstance(episode.get("causal_attribution"), Mapping)
        else {}
    )
    attribution_status = str(attribution.get("outcome_attribution") or "not_observed")
    if outcome and attribution_status != "observational_executed_plan":
        flags.append("outcome_not_attributed_to_execution")
    if outcome and executed and not executed.get("plan_fingerprint"):
        flags.append("invalid_observation:executed_plan_missing")
    if attribution_status == "unattributed_temporal_order_invalid":
        flags.append("outcome_precedes_executed_observation")
    elif attribution_status == "unattributed_temporal_order_unknown":
        flags.append("execution_outcome_temporal_order_unknown")
    return list(dict.fromkeys(flags))


def _find_episode(episodes: Sequence[Any], episode_id: str) -> tuple[int | None, dict[str, Any]]:
    for index, item in enumerate(episodes):
        if isinstance(item, Mapping) and str(item.get("episode_id")) == episode_id:
            return index, deepcopy(dict(item))
    return None, {
        "episode_id": episode_id,
        "created_at": None,
        "updated_at": None,
        "context": {},
        "stages": {},
        "stage_history": [],
        "integrity_flags": [],
    }


def _apply_stage_beliefs(memory: dict[str, Any], episode: Mapping[str, Any], stage: str) -> dict[str, Any]:
    stages = episode.get("stages") if isinstance(episode.get("stages"), Mapping) else {}
    stage_record = stages.get(stage) if isinstance(stages.get(stage), Mapping) else {}
    context = episode.get("context") if isinstance(episode.get("context"), Mapping) else {}
    signature = str(context.get("context_signature") or "context=unspecified")
    episode_id = str(episode.get("episode_id") or "episode")
    timestamp = str(stage_record.get("recorded_at") or memory.get("updated_at") or _now_iso())

    if stage == "consented" and isinstance(stage_record.get("decision"), bool):
        memory = observe_belief_v3(
            memory,
            key="consent_response",
            value=stage_record["decision"],
            source="consent_decision",
            evidence_id=str(stage_record["evidence_id"]),
            reliability=0.9,
            observed_at=timestamp,
            scope="contextual",
            context_signature=signature,
            episode_id=episode_id,
        )
    if stage != "outcome":
        return memory

    observations = stage_record.get("observations") if isinstance(stage_record.get("observations"), Mapping) else {}
    for index, item in enumerate(_explicit_preference_observations(observations), start=1):
        memory = observe_belief_v3(
            memory,
            key=item["key"],
            value=item["value"],
            source="user_feedback",
            evidence_id=f"{stage_record['evidence_id']}:preference:{index}",
            reliability=item["reliability"],
            observed_at=timestamp,
            scope="stable",
            episode_id=episode_id,
        )

    # Scores and physical results describe the executed exposure only.  A
    # rejected/unexecuted proposal may update consent memory, never execution
    # performance beliefs.
    executed = stages.get("executed") if isinstance(stages.get("executed"), Mapping) else {}
    attribution = (
        episode.get("causal_attribution")
        if isinstance(episode.get("causal_attribution"), Mapping)
        else {}
    )
    if (
        not executed.get("plan_fingerprint")
        or attribution.get("outcome_attribution") != "observational_executed_plan"
    ):
        return memory
    for source_key, belief_key in (
        ("overall_score", "overall_satisfaction"),
        ("score", "overall_satisfaction"),
        ("comfort_score", "comfort_satisfaction"),
        ("energy_score", "energy_satisfaction"),
        ("vpp_score", "grid_support_satisfaction"),
        ("target_achieved", "target_achievement"),
    ):
        value = observations.get(source_key)
        if value is None:
            continue
        memory = observe_belief_v3(
            memory,
            key=belief_key,
            value=value,
            source="executed_outcome",
            evidence_id=f"{stage_record['evidence_id']}:{source_key}",
            reliability=0.8,
            observed_at=timestamp,
            scope="contextual",
            context_signature=signature,
            episode_id=episode_id,
        )
        if belief_key == "overall_satisfaction":
            break
    return memory


def _supersede_stage_beliefs(
    memory: dict[str, Any],
    *,
    evidence_id: str,
    episode_id: str,
    stage: str,
    reference_at: str,
    source: str | None = None,
) -> dict[str, Any]:
    """Remove derived belief evidence for a corrected latest stage.

    The immutable ``stage_history`` remains the audit record. Belief containers
    are a projection of the latest stage view, so an overwritten consent or
    outcome must not survive as a second contradictory observation.
    """
    prefix = f"{evidence_id}:"
    changed_ids: list[str] = []
    for container_key in ("stable_beliefs", "contextual_beliefs"):
        rebuilt: dict[str, Any] = {}
        for belief_id, raw_belief in (memory.get(container_key) or {}).items():
            if not isinstance(raw_belief, Mapping):
                continue
            belief = deepcopy(dict(raw_belief))
            original = list(belief.get("evidence") or [])
            retained = [
                item
                for item in original
                if not (
                    isinstance(item, Mapping)
                    and (
                        str(item.get("evidence_id")) == evidence_id
                        or str(item.get("evidence_id", "")).startswith(prefix)
                    )
                    and (source is None or str(item.get("source")) == source)
                )
            ]
            if len(retained) != len(original):
                changed_ids.append(str(belief_id))
            if retained:
                belief["evidence"] = retained
                rebuilt[str(belief_id)] = _recompute_belief(
                    belief,
                    reference_at=reference_at,
                )
        memory[container_key] = rebuilt
    if changed_ids:
        ledger = list(memory.get("belief_revision_ledger") or [])
        ledger.append({
            "revision": memory.get("revision"),
            "status": "superseded_stage_correction",
            "episode_id": episode_id,
            "stage": stage,
            "superseded_evidence_id": evidence_id,
            "source_filter": source,
            "affected_belief_ids": sorted(set(changed_ids)),
            "observed_at": reference_at,
        })
        memory["belief_revision_ledger"] = ledger[-800:]
    return memory


def _episode_belief_projection(episode: Mapping[str, Any]) -> dict[str, Any]:
    """Describe the belief evidence implied by the latest episode stages."""
    stages = episode.get("stages") if isinstance(episode.get("stages"), Mapping) else {}
    context = episode.get("context") if isinstance(episode.get("context"), Mapping) else {}
    signature = str(context.get("context_signature") or "context=unspecified")
    consented = stages.get("consented") if isinstance(stages.get("consented"), Mapping) else {}
    executed = stages.get("executed") if isinstance(stages.get("executed"), Mapping) else {}
    outcome = stages.get("outcome") if isinstance(stages.get("outcome"), Mapping) else {}
    attribution = (
        episode.get("causal_attribution")
        if isinstance(episode.get("causal_attribution"), Mapping)
        else {}
    )
    consent_projection = None
    if isinstance(consented.get("decision"), bool):
        consent_projection = {
            "evidence_id": consented.get("evidence_id"),
            "context_signature": signature,
            "decision": consented.get("decision"),
            "plan_fingerprint": consented.get("plan_fingerprint"),
        }
    outcome_projection = None
    if outcome:
        outcome_projection = {
            "evidence_id": outcome.get("evidence_id"),
            "context_signature": signature,
        }
    execution_projection = None
    if outcome and attribution.get("outcome_attribution") == "observational_executed_plan":
        execution_projection = {
            "outcome_evidence_id": outcome.get("evidence_id"),
            "execution_evidence_id": executed.get("evidence_id"),
            "executed_plan_fingerprint": executed.get("plan_fingerprint"),
            "context_signature": signature,
        }
    return {
        "consent": consent_projection,
        "outcome": outcome_projection,
        "execution_outcome": execution_projection,
    }


def _reconcile_episode_beliefs(
    memory: dict[str, Any],
    *,
    previous_episode: Mapping[str, Any],
    current_episode: Mapping[str, Any],
    reference_at: str,
) -> dict[str, Any]:
    """Make derived beliefs an idempotent projection of current lifecycle state."""
    previous = _episode_belief_projection(previous_episode)
    current = _episode_belief_projection(current_episode)
    episode_id = str(current_episode.get("episode_id") or previous_episode.get("episode_id") or "episode")

    if _canonical(previous.get("consent")) != _canonical(current.get("consent")):
        old = previous.get("consent")
        if isinstance(old, Mapping) and old.get("evidence_id"):
            memory = _supersede_stage_beliefs(
                memory,
                evidence_id=str(old["evidence_id"]),
                episode_id=episode_id,
                stage="consented",
                reference_at=reference_at,
                source="consent_decision",
            )
        if current.get("consent") is not None:
            memory = _apply_stage_beliefs(memory, current_episode, "consented")

    outcome_changed = _canonical(previous.get("outcome")) != _canonical(current.get("outcome"))
    execution_changed = _canonical(previous.get("execution_outcome")) != _canonical(
        current.get("execution_outcome")
    )
    if outcome_changed:
        old = previous.get("outcome")
        if isinstance(old, Mapping) and old.get("evidence_id"):
            memory = _supersede_stage_beliefs(
                memory,
                evidence_id=str(old["evidence_id"]),
                episode_id=episode_id,
                stage="outcome",
                reference_at=reference_at,
            )
        if current.get("outcome") is not None:
            memory = _apply_stage_beliefs(memory, current_episode, "outcome")
    elif execution_changed:
        old_outcome = previous.get("outcome")
        if isinstance(old_outcome, Mapping) and old_outcome.get("evidence_id"):
            memory = _supersede_stage_beliefs(
                memory,
                evidence_id=str(old_outcome["evidence_id"]),
                episode_id=episode_id,
                stage="outcome_execution_attribution",
                reference_at=reference_at,
                source="executed_outcome",
            )
        if current.get("execution_outcome") is not None:
            # Explicit preference evidence is already present and deduplicates;
            # only newly valid executed-outcome evidence is added here.
            memory = _apply_stage_beliefs(memory, current_episode, "outcome")
    return memory


def record_episode_stage(
    memory: Mapping[str, Any],
    episode_id: str,
    stage: str,
    payload: Mapping[str, Any] | None,
    *,
    event_context: Mapping[str, Any] | None = None,
    observed_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Record one immutable lifecycle observation and update its latest view.

    Stages may arrive late or be corrected.  Missing predecessors are flagged
    rather than fabricated; a later stage never implies that an earlier plan
    was executed unchanged.
    """
    if str(memory.get("version")) != MEMORY_V3_VERSION:
        raise ValueError(f"Expected memory version {MEMORY_V3_VERSION!r}")
    if stage not in EPISODE_STAGES:
        raise ValueError(f"stage must be one of {EPISODE_STAGES!r}")
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping")
    if stage == "executed":
        execution_plan = _plan_from_payload(payload)
        if not execution_plan:
            raise ValueError(
                "executed stage requires an explicit actuator-observed plan; "
                "use retract_episode_stage for a non-execution correction"
            )
        raw_status = payload.get("status")
        if raw_status is not None:
            if not isinstance(raw_status, str):
                raise ValueError("executed stage status must be a string when supplied")
            normalized_status = _normal_key(raw_status)
            if normalized_status not in _AFFIRMATIVE_EXECUTION_STATUSES:
                raise ValueError(
                    "executed stage status must confirm physical execution "
                    f"({', '.join(sorted(_AFFIRMATIVE_EXECUTION_STATUSES))})"
                )
    episode_id = _redact_text(episode_id or "", 200)
    if not episode_id:
        raise ValueError("episode_id is required")
    safe_payload = _safe(payload)
    if not isinstance(safe_payload, Mapping):
        raise TypeError("payload must be a mapping")

    updated = deepcopy(dict(memory))
    updated["revision"] = int(updated.get("revision", 0) or 0) + 1
    timestamp = _timestamp(observed_at or (event_context or {}).get("observed_at"))
    updated["updated_at"] = timestamp
    episodes = list(updated.get("episodes") or [])
    index, episode = _find_episode(episodes, episode_id)
    previous_episode = deepcopy(episode)
    if event_context is not None:
        context = _coerce_event_context(event_context)
        episode["context"] = _episode_context(context)
    episode["created_at"] = episode.get("created_at") or timestamp
    episode["updated_at"] = timestamp
    stage_record = _stage_record(
        stage,
        safe_payload,
        episode_id=episode_id,
        revision=updated["revision"],
        observed_at=timestamp,
    )
    stages = dict(episode.get("stages") or {})
    stages[stage] = stage_record
    episode["stages"] = stages
    history = list(episode.get("stage_history") or [])
    history.append(deepcopy(stage_record))
    episode["stage_history"] = history[-40:]

    episode["causal_chain"], episode["causal_attribution"] = _rebuild_causal_chain(episode)
    episode["integrity_flags"] = _recompute_integrity_flags(episode)
    if index is None:
        episodes.append(episode)
    else:
        episodes[index] = episode
    updated["episodes"] = episodes[-300:]
    return _reconcile_episode_beliefs(
        updated,
        previous_episode=previous_episode,
        current_episode=episode,
        reference_at=timestamp,
    )


def retract_episode_stage(
    memory: Mapping[str, Any],
    episode_id: str,
    stage: str,
    *,
    reason: str = "explicit_observation_correction",
    observed_at: str | datetime | None = None,
) -> dict[str, Any]:
    """Retract a latest lifecycle stage while preserving an audit tombstone.

    Retraction is reserved for explicit observation corrections.  It removes
    the stage from the episode's latest-state projection, never substitutes an
    empty or inferred plan, and reconciles causal attribution, integrity flags,
    and all beliefs derived from the superseded stage.  The original stage and
    a retraction record remain in ``stage_history``.
    """
    if str(memory.get("version")) != MEMORY_V3_VERSION:
        raise ValueError(f"Expected memory version {MEMORY_V3_VERSION!r}")
    if stage not in EPISODE_STAGES:
        raise ValueError(f"stage must be one of {EPISODE_STAGES!r}")
    safe_episode_id = _redact_text(episode_id or "", 200)
    if not safe_episode_id:
        raise ValueError("episode_id is required")

    updated = deepcopy(dict(memory))
    episodes = list(updated.get("episodes") or [])
    index, episode = _find_episode(episodes, safe_episode_id)
    if index is None:
        raise ValueError("episode_id is not present in memory")
    stages = dict(episode.get("stages") or {})
    previous_stage = stages.get(stage)
    if not isinstance(previous_stage, Mapping):
        return deepcopy(dict(memory))

    previous_episode = deepcopy(episode)
    updated["revision"] = int(updated.get("revision", 0) or 0) + 1
    timestamp = _timestamp(observed_at)
    updated["updated_at"] = timestamp
    stages.pop(stage, None)
    episode["stages"] = stages
    episode["updated_at"] = timestamp
    retraction = {
        "stage": stage,
        "record_type": "retraction",
        "status": "retracted_stage_correction",
        "recorded_at": timestamp,
        "evidence_id": f"{safe_episode_id}:{stage}:retraction:{updated['revision']}",
        "retracted_evidence_id": previous_stage.get("evidence_id"),
        "retracted_plan_fingerprint": previous_stage.get("plan_fingerprint"),
        "reason": _redact_text(reason or "explicit_observation_correction", 800),
    }
    history = list(episode.get("stage_history") or [])
    history.append(retraction)
    episode["stage_history"] = history[-40:]

    def stage_plan(name: str) -> dict[str, Any]:
        record = stages.get(name) if isinstance(stages.get(name), Mapping) else {}
        return deepcopy(dict(record.get("plan"))) if isinstance(record.get("plan"), Mapping) else {}

    prior_context = episode.get("context") if isinstance(episode.get("context"), Mapping) else {}
    outcome_stage = stages.get("outcome") if isinstance(stages.get("outcome"), Mapping) else {}
    observations = (
        outcome_stage.get("observations")
        if isinstance(outcome_stage.get("observations"), Mapping)
        else {}
    )
    rebuilt_context = build_event_context_v3(
        prior_context.get("event"),
        calendar=prior_context.get("calendar"),
        home_state=prior_context.get("home_state"),
        user_input=str(prior_context.get("user_input") or ""),
        raw_proposal=stage_plan("raw_proposal"),
        validated_plan=stage_plan("validated"),
        consented_plan=stage_plan("consented"),
        executed_plan=stage_plan("executed"),
        observations=observations,
        observed_at=timestamp,
    )
    rebuilt_context["event_id"] = safe_episode_id
    episode["context"] = _episode_context(rebuilt_context)
    episode["causal_chain"], episode["causal_attribution"] = _rebuild_causal_chain(episode)
    episode["integrity_flags"] = _recompute_integrity_flags(episode)
    episodes[index] = episode
    updated["episodes"] = episodes[-300:]
    return _reconcile_episode_beliefs(
        updated,
        previous_episode=previous_episode,
        current_episode=episode,
        reference_at=timestamp,
    )


def _hashed_legacy_household(memory: Mapping[str, Any]) -> str:
    owner = memory.get("owner") if isinstance(memory.get("owner"), Mapping) else {}
    # V2's persona_id is documented as an opaque namespace.  It is hashed and
    # never copied into V3; method/model metadata is not accessed at all.
    opaque_namespace = str(owner.get("persona_id") or "unknown")
    digest = hashlib.sha256(opaque_namespace.encode("utf-8")).hexdigest()[:12]
    return f"migrated-household-{digest}"


def migrate_v2_memory(memory: Mapping[str, Any]) -> dict[str, Any]:
    """Migrate trusted V2 observable memory without importing identity metadata.

    Only V2 evidence whose source is valid for the corresponding V3 belief
    scope is retained.  Lifecycle records are reconstructed conservatively:
    absence of an executed plan stays absent and outcomes remain unattributed.
    """
    if str(memory.get("version")) == MEMORY_V3_VERSION:
        return deepcopy(dict(memory))
    if str(memory.get("version")) != "energybridge_observable_profile_memory_v2":
        raise ValueError("migrate_v2_memory requires observable profile memory V2")

    onboarding = memory.get("onboarding") if isinstance(memory.get("onboarding"), Mapping) else {}
    migrated = initialize_memory_v3(
        {"answers": list(onboarding.get("answers") or [])},
        household_id=_hashed_legacy_household(memory),
    )
    skipped_evidence = 0

    # Stable V2 beliefs have explicit onboarding/user-feedback provenance.
    # Contextual V2 beliefs are deliberately rebuilt from lifecycle episodes
    # below because the V2 schema could label a score ``executed_outcome`` even
    # when no executed-plan observation existed.
    for container_name, scope in (("beliefs", "stable"),):
        source_container = memory.get(container_name)
        if not isinstance(source_container, Mapping):
            continue
        for old_key, old_belief in source_container.items():
            if not isinstance(old_belief, Mapping):
                continue
            normalized = _normal_key(old_key)
            if not normalized or _forbidden_key(normalized):
                skipped_evidence += 1
                continue
            if scope == "contextual" and "::" in str(old_key):
                belief_key, signature = str(old_key).split("::", 1)
            elif scope == "contextual":
                belief_key = str(old_belief.get("key") or normalized)
                signature = str(old_belief.get("context_signature") or "context=unspecified")
            else:
                belief_key, signature = normalized, None
            provenance = [
                item for item in list(old_belief.get("provenance") or []) if isinstance(item, Mapping)
            ]
            for index, item in enumerate(provenance[-30:], start=1):
                source = str(item.get("source") or "")
                allowed = _STABLE_SOURCES if scope == "stable" else _CONTEXTUAL_SOURCES
                if source not in allowed:
                    skipped_evidence += 1
                    continue
                if (
                    scope == "stable"
                    and source == "onboarding_questionnaire"
                    and belief_key in (migrated.get("stable_beliefs") or {})
                ):
                    continue
                value = item.get("value", old_belief.get("value"))
                reliability = _float(item.get("reliability"))
                migrated = observe_belief_v3(
                    migrated,
                    key=belief_key,
                    value=value,
                    source=source,
                    evidence_id=f"migrated:{_fingerprint([old_key, item.get('evidence_id'), index])}",
                    reliability=reliability if reliability is not None else 0.65,
                    observed_at=item.get("observed_at") or memory.get("updated_at"),
                    scope=scope,
                    context_signature=signature,
                    episode_id=str(item.get("event_id") or "") or None,
                )

    for position, old_episode in enumerate(list(memory.get("events") or [])[-300:], start=1):
        if not isinstance(old_episode, Mapping):
            continue
        event_id = _redact_text(old_episode.get("event_id") or f"migrated-{position}", 160)
        episode_id = f"legacy:{event_id}:{position}"
        context = build_event_context_v3(
            old_episode.get("event"),
            calendar=old_episode.get("calendar"),
            home_state=old_episode.get("home_state"),
            user_input=str(old_episode.get("user_input") or ""),
            raw_model_plan=old_episode.get("raw_model_plan"),
            validated_plan=old_episode.get("validated_plan"),
            proposed_plan=old_episode.get("proposed_plan"),
            consented_plan=old_episode.get("consented_plan"),
            executed_plan=old_episode.get("executed_plan"),
            observed_at=old_episode.get("observed_at"),
        )
        raw = context.get("raw_proposal") or {}
        validated = context.get("validated_plan") or context.get("proposed_plan") or {}
        proposed = context.get("proposed_plan") or validated
        consented_plan = context.get("consented_plan") or proposed
        executed = context.get("executed_plan") or {}
        outcome = _pick(old_episode.get("outcome"), _OUTCOME_FIELDS)
        decision = _first(outcome, "accepted", "approved")
        if raw:
            migrated = record_episode_stage(
                migrated, episode_id, "raw_proposal", {"plan": raw},
                event_context=context, observed_at=context["observed_at"],
            )
        if validated:
            migrated = record_episode_stage(
                migrated, episode_id, "validated", {"plan": validated, "status": "migrated_observed"},
                event_context=context, observed_at=context["observed_at"],
            )
        if isinstance(decision, bool) or context.get("consented_plan"):
            migrated = record_episode_stage(
                migrated,
                episode_id,
                "consented",
                {"plan": consented_plan, "decision": decision},
                event_context=context,
                observed_at=context["observed_at"],
            )
        if executed:
            migrated = record_episode_stage(
                migrated, episode_id, "executed", {"plan": executed},
                event_context=context, observed_at=context["observed_at"],
            )
        if outcome:
            migrated = record_episode_stage(
                migrated, episode_id, "outcome", outcome,
                event_context=context, observed_at=context["observed_at"],
            )

    migrated["migration"] = {
        "source_version": "energybridge_observable_profile_memory_v2",
        "migrated_at": _now_iso(),
        "policy": "observable_evidence_only_conservative_attribution",
        "skipped_evidence_count": skipped_evidence,
    }
    return migrated


def _unique_episode_id(memory: Mapping[str, Any], base: str) -> str:
    existing = {
        str(item.get("episode_id"))
        for item in list(memory.get("episodes") or [])
        if isinstance(item, Mapping)
    }
    if base not in existing:
        return base
    suffix = int(memory.get("revision", 0) or 0) + 1
    candidate = f"{base}:{suffix}"
    while candidate in existing:
        suffix += 1
        candidate = f"{base}:{suffix}"
    return candidate


def update_memory_v3(
    memory: Mapping[str, Any],
    event_context: Mapping[str, Any],
    outcome: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Compatibility update that materializes the complete observed lifecycle.

    V2 memory/context are accepted.  Crucially, a proposed or consented plan is
    never copied into the executed stage; execution must be explicitly present.
    """
    updated = migrate_v2_memory(memory) if str(memory.get("version")) != MEMORY_V3_VERSION else deepcopy(dict(memory))
    context = _coerce_event_context(event_context)
    base = _redact_text(context.get("event_id") or f"event-{int(updated.get('revision', 0)) + 1}", 180)
    episode_id = _unique_episode_id(updated, base)
    timestamp = context["observed_at"]
    outcome_observed = outcome is not None
    safe_outcome = _pick(outcome, _OUTCOME_FIELDS)

    raw = context.get("raw_proposal") or {}
    validated = context.get("validated_plan") or context.get("proposed_plan") or {}
    proposed = context.get("proposed_plan") or validated
    consented = context.get("consented_plan") or proposed
    executed = context.get("executed_plan") or {}
    decision = _first(safe_outcome, "accepted", "approved")
    if raw:
        updated = record_episode_stage(
            updated, episode_id, "raw_proposal", {"plan": raw},
            event_context=context, observed_at=timestamp,
        )
    if validated:
        updated = record_episode_stage(
            updated, episode_id, "validated", {"plan": validated},
            event_context=context, observed_at=timestamp,
        )
    if isinstance(decision, bool) or context.get("consented_plan"):
        updated = record_episode_stage(
            updated,
            episode_id,
            "consented",
            {"plan": consented, "decision": decision},
            event_context=context,
            observed_at=timestamp,
        )
    if executed:
        updated = record_episode_stage(
            updated, episode_id, "executed", {"plan": executed},
            event_context=context, observed_at=timestamp,
        )
    if outcome_observed:
        updated = record_episode_stage(
            updated, episode_id, "outcome", safe_outcome,
            event_context=context, observed_at=timestamp,
        )
    return updated


_FEATURE_WEIGHTS = {
    "event_type": 0.17,
    "time_bucket": 0.13,
    "day_type": 0.07,
    "occupancy": 0.13,
    "price_level": 0.1,
    "duration_bucket": 0.08,
    "temperature_bucket": 0.08,
    "calendar_constraints": 0.12,
    "action_tokens": 0.12,
}


def _set_similarity(left: Any, right: Any) -> float:
    left_set = set(left if isinstance(left, list) else [left])
    right_set = set(right if isinstance(right, list) else [right])
    union = left_set | right_set
    return len(left_set & right_set) / len(union) if union else 0.0


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
        score = _set_similarity(old, new) if key in {"calendar_constraints", "action_tokens"} else (
            1.0 if _canonical(old) == _canonical(new) else 0.0
        )
        earned += weight * score
        if score >= 0.5:
            matched.append(key)
    return ((earned / available) if available else 0.0), matched


def retrieve_relevant_episodes(
    memory: Mapping[str, Any],
    current_context: Mapping[str, Any],
    k: int = 4,
) -> list[dict[str, Any]]:
    """Retrieve structurally similar, provenance-rich episodes.

    Ranking is independent of controller identity.  Relevant negative outcomes
    receive a bounded reminder bonus, while execution evidence and freshness
    affect trust rather than defining a preferred strategy.
    """
    if k <= 0:
        return []
    v3 = migrate_v2_memory(memory) if str(memory.get("version")) != MEMORY_V3_VERSION else memory
    context = _coerce_event_context(current_context)
    current_features = context.get("features") if isinstance(context.get("features"), Mapping) else {}
    reference_at = context.get("observed_at") or _now_iso()
    ranked: list[tuple[float, int, str, dict[str, Any]]] = []
    for position, item in enumerate(list(v3.get("episodes") or [])):
        if not isinstance(item, Mapping):
            continue
        episode = deepcopy(dict(item))
        old_context = episode.get("context") if isinstance(episode.get("context"), Mapping) else {}
        old_features = old_context.get("features") if isinstance(old_context.get("features"), Mapping) else {}
        similarity, matched = _feature_similarity(old_features, current_features)
        stages = episode.get("stages") if isinstance(episode.get("stages"), Mapping) else {}
        outcome = stages.get("outcome") if isinstance(stages.get("outcome"), Mapping) else {}
        severity = _clamp(float(outcome.get("negative_severity", 0.0) or 0.0))
        risk_reminder = severity * similarity
        attribution = episode.get("causal_attribution") if isinstance(episode.get("causal_attribution"), Mapping) else {}
        evidence_quality = 1.0 if attribution.get("execution_observed") and attribution.get("outcome_observed") else (
            0.72 if attribution.get("consent_observed") else 0.35
        )
        age = _age_days(episode.get("updated_at"), reference_at)
        freshness = 0.5 ** (age / 120.0)
        score = (0.68 * similarity) + (0.12 * evidence_quality) + (0.1 * freshness) + (0.1 * risk_reminder)
        episode["retrieval"] = {
            "score": round(score, 6),
            "context_similarity": round(similarity, 6),
            "evidence_quality": round(evidence_quality, 4),
            "freshness": round(freshness, 4),
            "negative_reminder": round(risk_reminder, 4),
            "matched_features": matched,
        }
        ranked.append((score, position, str(episode.get("episode_id") or ""), episode))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    return [item[3] for item in ranked[: int(k)]]


def _compact_belief(belief: Mapping[str, Any]) -> dict[str, Any]:
    evidence = [item for item in list(belief.get("evidence") or []) if isinstance(item, Mapping)]
    return {
        "belief_id": belief.get("belief_id"),
        "key": belief.get("key"),
        "value": _safe(belief.get("value")),
        "confidence": round(float(belief.get("confidence", 0.0) or 0.0), 4),
        "status": belief.get("status"),
        "evidence_count": int(belief.get("evidence_count", 0) or 0),
        "contradiction_count": int(belief.get("contradiction_count", 0) or 0),
        "evidence_refs": [str(item.get("evidence_id")) for item in evidence[-4:]],
        "last_observed_at": belief.get("last_observed_at"),
    }


def _compact_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    keep = {
        "id",
        "strategy_id",
        "name",
        "mode",
        "action",
        "actions",
        "setpoint",
        "setpoint_c",
        "appliances",
        "appliance_actions",
        "expected_benefit",
        "uncertainty",
    }
    compact = {key: _safe(value) for key, value in plan.items() if key in keep}
    exposures = [
        item
        for item in list(plan.get("execution_exposures") or [])
        if isinstance(item, Mapping) and isinstance(item.get("plan"), Mapping)
    ]
    if exposures:
        compact["execution_exposure_count"] = len(exposures)
        compact["execution_exposures"] = [
            {
                "simulation_hour": item.get("simulation_hour"),
                "plan": _compact_plan(item.get("plan") or {}),
                "fingerprint": item.get("fingerprint"),
            }
            for item in exposures[-8:]
        ]
    return compact


def _compact_episode(episode: Mapping[str, Any]) -> dict[str, Any]:
    stages = episode.get("stages") if isinstance(episode.get("stages"), Mapping) else {}
    validated = stages.get("validated") if isinstance(stages.get("validated"), Mapping) else {}
    consented = stages.get("consented") if isinstance(stages.get("consented"), Mapping) else {}
    executed = stages.get("executed") if isinstance(stages.get("executed"), Mapping) else {}
    outcome = stages.get("outcome") if isinstance(stages.get("outcome"), Mapping) else {}
    observations = outcome.get("observations") if isinstance(outcome.get("observations"), Mapping) else {}
    compact_outcome = {
        key: _safe(observations[key])
        for key in (
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
        )
        if key in observations
    }
    return {
        "episode_id": episode.get("episode_id"),
        "context_signature": (episode.get("context") or {}).get("context_signature"),
        "similarity": (episode.get("retrieval") or {}).get("context_similarity"),
        "matched_features": (episode.get("retrieval") or {}).get("matched_features"),
        "decision": consented.get("decision"),
        "validated_plan": _compact_plan(validated.get("plan") or {}),
        "executed_plan": _compact_plan(executed.get("plan") or {}),
        "changed_before_execution": (episode.get("causal_attribution") or {}).get(
            "validation_to_execution_changed_fields", []
        ),
        "outcome": compact_outcome,
        "feedback_signal": outcome.get("feedback_signal"),
        "feedback": _redact_text(outcome.get("feedback_text", ""), 600),
        "attribution": _safe(episode.get("causal_attribution") or {}),
        "evidence_refs": [
            str(stage.get("evidence_id"))
            for stage in stages.values()
            if isinstance(stage, Mapping) and stage.get("evidence_id")
        ],
    }


def compact_memory_context_v3(
    memory: Mapping[str, Any],
    current_context: Mapping[str, Any],
    *,
    k: int = 4,
    max_chars: int = 6000,
) -> dict[str, Any]:
    """Build a bounded prompt capsule that preserves evidence/attribution."""
    v3 = migrate_v2_memory(memory) if str(memory.get("version")) != MEMORY_V3_VERSION else deepcopy(dict(memory))
    context = _coerce_event_context(current_context)
    v3 = refresh_beliefs_v3(v3, context.get("observed_at"))
    relevant = retrieve_relevant_episodes(v3, context, k=k)
    stable = [
        _compact_belief(belief)
        for belief in (v3.get("stable_beliefs") or {}).values()
        if isinstance(belief, Mapping)
    ]
    stable.sort(key=lambda item: (-item["confidence"], str(item["belief_id"])))
    signature = str(context.get("context_signature") or "context=unspecified")
    contextual = [
        _compact_belief(belief)
        for belief in (v3.get("contextual_beliefs") or {}).values()
        if isinstance(belief, Mapping) and str(belief.get("context_signature")) == signature
    ]
    contextual.sort(key=lambda item: (-item["confidence"], str(item["belief_id"])))
    unresolved = [
        item
        for item in stable + contextual
        if item["status"] in {"conflicted", "provisional", "stale"}
        or item["contradiction_count"] > 0
    ]
    capsule: dict[str, Any] = {
        "memory_version": MEMORY_V3_VERSION,
        "privacy_scope": "controller_observable_only",
        "current_context_signature": signature,
        "epistemic_contract": [
            "Beliefs are evidence-linked hypotheses, not commands or hard constraints.",
            "Consent evidence describes the offered plan; physical outcomes describe only the executed plan.",
            "Conflicted, stale, or weak beliefs should remain uncertain.",
        ],
        "stable_beliefs": stable[:18],
        "contextual_beliefs": contextual[:10],
        "unresolved_beliefs": unresolved[:10],
        "relevant_episodes": [_compact_episode(episode) for episode in relevant],
    }
    budget = max(900, int(max_chars))

    def shrink_once() -> bool:
        episodes = capsule["relevant_episodes"]
        for episode in reversed(episodes):
            if len(str(episode.get("feedback", ""))) > 160:
                episode["feedback"] = str(episode["feedback"])[:157] + "..."
                return True
            for plan_key in ("validated_plan", "executed_plan"):
                plan = episode.get(plan_key)
                if isinstance(plan, Mapping) and plan:
                    reduced = {
                        key: value
                        for key, value in plan.items()
                        if key in {"id", "strategy_id", "mode", "action", "setpoint", "setpoint_c"}
                    }
                    if _canonical(reduced) != _canonical(plan):
                        episode[plan_key] = reduced
                        return True
        if episodes:
            episodes.pop()
            return True
        if capsule["contextual_beliefs"]:
            capsule["contextual_beliefs"].pop()
            return True
        if capsule["stable_beliefs"]:
            capsule["stable_beliefs"].pop()
            return True
        if capsule["unresolved_beliefs"]:
            capsule["unresolved_beliefs"].pop()
            return True
        if capsule["epistemic_contract"]:
            capsule["epistemic_contract"].pop()
            return True
        return False

    capsule["serialized_chars"] = 0
    for _ in range(1000):
        capsule["serialized_chars"] = len(json.dumps(capsule, ensure_ascii=False, sort_keys=True))
        actual = len(json.dumps(capsule, ensure_ascii=False, sort_keys=True))
        if actual <= budget:
            break
        if not shrink_once():
            break
    for _ in range(5):
        capsule["serialized_chars"] = len(json.dumps(capsule, ensure_ascii=False, sort_keys=True))
    return capsule


def _validate_v3_memory(memory: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(memory, Mapping) or str(memory.get("version")) != MEMORY_V3_VERSION:
        raise ValueError(f"Expected memory version {MEMORY_V3_VERSION!r}")
    if _contains_forbidden_key(memory):
        raise ValueError("memory contains fields outside the V3 privacy boundary")
    owner = memory.get("owner") if isinstance(memory.get("owner"), Mapping) else {}
    if not str(owner.get("household_id") or ""):
        raise ValueError("memory owner.household_id is required")
    safe = _safe(memory)
    if not isinstance(safe, dict):
        raise ValueError("memory must be a JSON object")
    return safe


def _assert_no_symlink_components(path: str | Path) -> Path:
    """Reject an existing symlink at any component of a persistence path."""
    target = Path(os.path.abspath(os.fspath(Path(path).expanduser())))
    current = Path(target.anchor)
    for part in target.parts[1:]:
        current = current / part
        try:
            metadata = os.lstat(current)
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(metadata.st_mode):
            raise ValueError("persistence path contains a symlink component")
    return target


def _assert_private_persistence_parent(target: Path) -> Path:
    """Require the direct storage directory to resist peer replacement."""
    parent = target.parent
    try:
        metadata = os.stat(parent, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise ValueError("persistence parent must be an existing non-symlink directory") from exc
    if not stat.S_ISDIR(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise ValueError("persistence parent must be an existing non-symlink directory")
    if metadata.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise PermissionError("persistence parent directory must not be group/world writable")
    return parent


def save_memory_v3(
    memory: Mapping[str, Any],
    path: str | Path,
    *,
    allow_persistence: bool = False,
    max_bytes: int = 8_000_000,
) -> Path:
    """Atomically persist V3 memory after explicit opt-in.

    The envelope hash detects corruption/tampering and mode ``0600`` protects
    the local file from other users.  This is intentionally not encryption.
    """
    if not allow_persistence:
        raise PermissionError("set allow_persistence=True to persist household memory")
    safe_memory = _validate_v3_memory(memory)
    canonical = _canonical(safe_memory)
    envelope = {
        "format": PERSISTENCE_V3_FORMAT,
        "sha256": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        "memory": safe_memory,
    }
    encoded = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > int(max_bytes):
        raise ValueError("serialized memory exceeds max_bytes")
    target = _assert_no_symlink_components(path)
    if target.exists() and (target.is_symlink() or not target.is_file()):
        raise ValueError("persistence target must be a regular non-symlink file")
    parent = _assert_private_persistence_parent(target)

    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(parent))
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, target)
        os.chmod(target, 0o600)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
    return target


def load_memory_v3(
    path: str | Path,
    *,
    allow_persistence: bool = False,
    expected_household_id: str | None = None,
    max_bytes: int = 8_000_000,
) -> dict[str, Any]:
    """Load and verify an opt-in V3 persistence envelope."""
    if not allow_persistence:
        raise PermissionError("set allow_persistence=True to load household memory")
    target = _assert_no_symlink_components(path)
    _assert_private_persistence_parent(target)
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(target, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise ValueError("memory path is not a regular file")
        if metadata.st_mode & 0o077:
            raise PermissionError("memory file permissions must not grant group/other access")
        if metadata.st_size > int(max_bytes):
            raise ValueError("memory file exceeds max_bytes")
        chunks: list[bytes] = []
        remaining = int(max_bytes) + 1
        while remaining > 0:
            chunk = os.read(descriptor, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
    finally:
        os.close(descriptor)
    raw = b"".join(chunks)
    if len(raw) > int(max_bytes):
        raise ValueError("memory file exceeds max_bytes")
    try:
        envelope = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("memory persistence envelope is not valid UTF-8 JSON") from exc
    if not isinstance(envelope, Mapping) or envelope.get("format") != PERSISTENCE_V3_FORMAT:
        raise ValueError("unsupported memory persistence envelope")
    payload = envelope.get("memory")
    if not isinstance(payload, Mapping):
        raise ValueError("persistence envelope has no memory object")
    expected_hash = hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()
    if str(envelope.get("sha256") or "") != expected_hash:
        raise ValueError("memory persistence integrity check failed")
    memory = _validate_v3_memory(payload)
    household_id = str((memory.get("owner") or {}).get("household_id") or "")
    if expected_household_id is not None and household_id != str(expected_household_id):
        raise ValueError("memory household identity does not match expected_household_id")
    return memory


# Compact compatibility spellings for harness code that selects a memory
# version at import time.  V2's module remains untouched.
retrieve_relevant_events_v3 = retrieve_relevant_episodes
