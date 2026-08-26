"""Observable, evidence-backed household modelling for the V3 harness.

The household model in this module is deliberately *not* a projection of an
evaluation persona.  It can be built only from things a real home-energy agent
could observe: onboarding answers, calendars shared with the agent, device
capabilities, and feedback from previous interactions.  Every preference is a
distribution with confidence and provenance; contradictory evidence stays
visible instead of being collapsed into an archetype label.

The public API uses JSON-serialisable dictionaries so it can be persisted by
the existing harness without a database or a new runtime dependency.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence


HOUSEHOLD_MODEL_VERSION = "energybridge.observable_household_model.v3"
PROFILE_CAPSULE_VERSION = "energybridge.observable_profile_capsule.v3"

_MAX_EVIDENCE = 96
_MAX_PROVENANCE_PER_TRAIT = 24
_MIN_TOKEN_BUDGET = 96

_CATEGORICAL_TRAITS: dict[str, tuple[str, ...]] = {
    "comfort_protection": ("low", "moderate", "high"),
    "savings_interest": ("low", "moderate", "high"),
    "grid_support_interest": ("low", "moderate", "high"),
    "change_control": ("ask_first", "conditional_delegation", "delegated"),
    "routine_protection": ("low", "moderate", "high"),
    "explanation_need": ("brief", "concrete", "quantified_when_available"),
}
_NUMERIC_TRAITS = {"thermostat_change_tolerance_c"}

# These fields belong to an evaluator, a role card, or an implementation, not
# to the observable household.  Matching is normalised and recursive.
_FORBIDDEN_KEY_PARTS = (
    "hidden",
    "persona",
    "role_card",
    "system_prompt",
    "developer_prompt",
    "roleplay_user_prompt",
    "agent_context",
    "inferred_profile",
    "preference_rules",
    "scoring_weight",
    "ground_truth",
    "evaluation_target",
    "acceptance_target",
    "target_acceptance",
    "target_rate",
    "acceptance_probability",
    "override_probability",
    "vpp_override_prob",
    "api_key",
    "api_base",
    "base_url",
    "access_token",
    "secret_key",
    "developer_message",
    "developer_instruction",
    "private_key",
    "endpoint_url",
    "llm_host",
    "base_model",
    "model_name",
    "method_name",
)
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\b(?:bearer\s+)[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
    re.compile(r"\b(?:token\s+)[A-Za-z0-9._~+/=-]{12,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|authorization|password)"
        r"\s*[:=]\s*\S+",
        re.IGNORECASE,
    ),
    re.compile(
        r"-----BEGIN [^-\r\n]*PRIVATE KEY-----.*?"
        r"(?:-----END [^-\r\n]*PRIVATE KEY-----|$)",
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(r"\bSECRET(?:[_-][A-Za-z0-9]+)+\b", re.IGNORECASE),
    re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE),
    # Deliberately require a run of phone-like digits so ISO dates and event
    # time ranges remain usable evidence.
    re.compile(r"(?<![\d+])(?:\+\d{1,3}[ ()-]*)?\d{9,14}(?!\d)"),
    re.compile(
        r"\b(?:acceptance[ _-]?target|target[ _-]?acceptance|target[ _-]?rate)\b"
        r"(?:\s*(?:is|of|=|:)?\s*\d+(?:\.\d+)?%?)?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:scoring[ _-]?weights?|vpp[ _-]?override[ _-]?prob|hidden[ _-]?persona|"
        r"role[ _-]?card|system[ _-]?prompt|developer[ _-]?prompt|agent[ _-]?context|"
        r"inferred[ _-]?profile|base[ _-]?model|model[ _-]?name|method[ _-]?name|"
        r"acceptance[ _-]?target|target[ _-]?acceptance|target[ _-]?rate)\b",
        re.IGNORECASE,
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
    r"(?<![@\w.])(?:localhost|(?:[a-z0-9](?:[a-z0-9-]{0,62})\.)+[a-z]{2,63})"
    r"(?![a-z])(?::\d{2,5})?"
    r"(?:/[^\s<>\"'\[\]{}()]*)?",
    re.IGNORECASE,
)
_LABELLED_DOTTED_HOST_ENDPOINT_PATTERN = re.compile(
    r"\b(?:endpoint|host|server|api[_ -]?base|base[_ -]?url)\s+"
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

_CALENDAR_EVENT_FIELDS = {
    "title",
    "start_h",
    "end_h",
    "date",
    "day",
    "weekday",
    "day_type",
    "location_type",
    "member_role",
    "service",
    "constraint",
    "flexible",
}
_DEVICE_FIELDS = {
    "present",
    "mode",
    "shiftable",
    "dr_adjustable",
    "setpoint_preferred_min_c",
    "setpoint_preferred_max_c",
    "temp_tolerance_c",
    "earliest_h",
    "latest_h",
    "preferred_h",
    "duration_h",
    "arrival_h",
    "departure_h",
    "target_soc",
    "min_soc",
    "bath_required_h",
    "pre_heat_window_start_h",
    "pre_heat_window_end_h",
}
_CONTEXT_FIELDS = {
    "event_type",
    "type",
    "signal_type",
    "start_h",
    "trigger_h",
    "end_h",
    "day_type",
    "weekday",
    "occupied",
    "occupancy",
    "notice_hours",
    "price_level",
    "time_bucket",
    "duration_bucket",
    "affected_devices",
    "devices",
}

_TRAIT_QUESTIONS: dict[str, tuple[str, str]] = {
    "comfort_protection": (
        "comfort_tradeoff",
        "When an energy event overlaps a hot or cold period, what comfort changes would still feel reasonable?",
    ),
    "savings_interest": (
        "benefit_preference",
        "Which benefit matters most to you in these events: bill savings, supporting the grid, or avoiding disruption?",
    ),
    "grid_support_interest": (
        "grid_motivation",
        "How important is helping during a grid peak when the household impact is small?",
    ),
    "change_control": (
        "change_permission",
        "Which changes may happen automatically, and which ones should always be confirmed first?",
    ),
    "routine_protection": (
        "protected_routines",
        "Are there routines or service deadlines that should never be moved without checking with you?",
    ),
    "explanation_need": (
        "decision_information",
        "What information would you want before deciding on a proposed change?",
    ),
    "thermostat_change_tolerance_c": (
        "thermostat_range",
        "For a short event, roughly how much thermostat change would be comfortable?",
    ),
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normal_key(value: Any) -> str:
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value or "").strip())
    return re.sub(r"[^a-z0-9_]+", "_", split.lower()).strip("_")


def _forbidden_key(value: Any) -> bool:
    raw_key = str(value or "")
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
    key = _normal_key(value)
    compact = key.replace("_", "")
    tokens = set(key.split("_"))
    if tokens & {
        "method", "model", "provider", "vendor", "planner", "algorithm",
        "controller", "evaluator", "persona", "auth", "authorization",
        "bearer", "credential", "credentials", "password", "secret", "token",
        "developer", "endpoint", "host", "llm",
    }:
        return True
    if {"private", "key"}.issubset(tokens):
        return True
    return any(
        part in key or part.replace("_", "") in compact
        for part in _FORBIDDEN_KEY_PARTS
    )


def _redact_text(value: Any, limit: int = 800) -> tuple[str, int]:
    text = " ".join(str(value or "").split())
    if re.fullmatch(r"energybridge(?:[._][A-Za-z0-9_-]+)+", text, re.IGNORECASE):
        return text[:limit], 0
    redactions = 0
    for pattern in _SECRET_PATTERNS:
        text, count = pattern.subn("[redacted]", text)
        redactions += count
    for pattern in (
        _URL_ENDPOINT_PATTERN,
        _LABELLED_DOTTED_HOST_ENDPOINT_PATTERN,
        _IP_ENDPOINT_PATTERN,
        _IPV6_ENDPOINT_PATTERN,
        _BARE_HOST_ENDPOINT_PATTERN,
        _SINGLE_LABEL_HOST_ENDPOINT_PATTERN,
        _BARE_CREDENTIAL_PATTERN,
        _GENERIC_KEY_VALUE_PATTERN,
    ):
        text, count = pattern.subn("[redacted]", text)
        redactions += count
    text, count = _LABELLED_TECH_IDENTITY_PATTERN.subn("planning system", text)
    redactions += count
    text, count = _UNKNOWN_TECH_IDENTITY_PATTERN.subn("planning system", text)
    redactions += count
    if re.fullmatch(r"energybridge[._][A-Za-z0-9_.-]+", text, re.IGNORECASE) is None:
        text, count = _PLANNER_IDENTITY_PATTERN.subn("planning system", text)
        redactions += count
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text, redactions


def _sanitize(
    value: Any,
    *,
    depth: int = 0,
    audit: dict[str, int] | None = None,
) -> Any:
    """Recursively copy JSON-compatible observable data and drop private fields."""
    if audit is None:
        audit = {"discarded_fields": 0, "redacted_values": 0, "truncated_values": 0}
    if depth > 7:
        audit["truncated_values"] += 1
        return "[depth limit]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        text, redactions = _redact_text(value, 1200)
        audit["redacted_values"] += redactions
        return text
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:100]:
            if _forbidden_key(raw_key):
                audit["discarded_fields"] += 1
                continue
            result[str(raw_key)[:100]] = _sanitize(item, depth=depth + 1, audit=audit)
        if len(value) > 100:
            audit["truncated_values"] += len(value) - 100
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 100:
            audit["truncated_values"] += len(value) - 100
        return [_sanitize(item, depth=depth + 1, audit=audit) for item in list(value)[:100]]
    text, redactions = _redact_text(value, 400)
    audit["redacted_values"] += redactions
    return text


def sanitize_observable_payload(value: Any) -> Any:
    """Return a detached JSON-compatible copy with private/evaluator data removed."""
    return _sanitize(value)


def _safe_identifier(value: Any, fallback: str) -> str:
    source, _ = _redact_text(value, 160)
    source = source.strip()
    if not source:
        return fallback
    ascii_prefix = re.sub(r"[^A-Za-z0-9_.:@/-]+", "-", source).strip("-")
    if ascii_prefix and ascii_prefix == source:
        return ascii_prefix
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return f"{ascii_prefix or fallback}-{digest}"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _evidence_id(source: str, payload: Any) -> str:
    digest = hashlib.sha256(f"{source}:{_canonical(payload)}".encode("utf-8")).hexdigest()[:16]
    return f"ev-{digest}"


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))


def _float(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _blank_categorical_trait(labels: Sequence[str]) -> dict[str, Any]:
    prior = {label: 0.35 for label in labels}
    probability = round(1.0 / len(labels), 4)
    return {
        "kind": "categorical_distribution",
        "distribution": {label: probability for label in labels},
        "confidence": 0.0,
        "evidence_count": 0,
        "contradiction_count": 0,
        "weighted_support": prior,
        "provenance": [],
    }


def _blank_numeric_trait() -> dict[str, Any]:
    return {
        "kind": "numeric_distribution",
        "distribution": {
            "family": "unknown",
            "mean": None,
            "standard_deviation": None,
            "plausible_interval": [None, None],
            "unit": "celsius",
        },
        "confidence": 0.0,
        "evidence_count": 0,
        "contradiction_count": 0,
        "weighted_observations": [],
        "provenance": [],
    }


def _blank_traits() -> dict[str, dict[str, Any]]:
    traits = {
        key: _blank_categorical_trait(labels)
        for key, labels in _CATEGORICAL_TRAITS.items()
    }
    traits.update({key: _blank_numeric_trait() for key in _NUMERIC_TRAITS})
    return traits


def _entropy(distribution: Mapping[str, float]) -> float:
    values = [float(value) for value in distribution.values() if float(value) > 0]
    if len(values) <= 1:
        return 0.0
    raw = -sum(value * math.log(value) for value in values)
    return raw / math.log(len(distribution))


def _refresh_categorical(trait: dict[str, Any]) -> None:
    support = {str(key): max(0.0, float(value)) for key, value in trait["weighted_support"].items()}
    total = sum(support.values()) or 1.0
    distribution = {key: round(value / total, 4) for key, value in support.items()}
    # Correct rounding drift without changing the winning category.
    winner = max(distribution, key=lambda key: (distribution[key], key))
    distribution[winner] = round(distribution[winner] + (1.0 - sum(distribution.values())), 4)
    evidence_strength = 1.0 - math.exp(-0.7 * int(trait["evidence_count"]))
    concentration = 1.0 - _entropy(distribution)
    disagreement_penalty = min(0.35, 0.09 * int(trait["contradiction_count"]))
    trait["distribution"] = distribution
    trait["confidence"] = round(
        _clamp(evidence_strength * (0.58 + 0.42 * concentration) - disagreement_penalty),
        3,
    )


def _refresh_numeric(trait: dict[str, Any]) -> None:
    observations = list(trait.get("weighted_observations") or [])
    if not observations:
        return
    total_weight = sum(float(item["weight"]) for item in observations) or 1.0
    mean = sum(float(item["value"]) * float(item["weight"]) for item in observations) / total_weight
    variance = sum(
        float(item["weight"]) * ((float(item["value"]) - mean) ** 2)
        for item in observations
    ) / total_weight
    # A single answer is an estimate, not an exact physical boundary.
    standard_deviation = max(0.25, math.sqrt(max(0.0, variance)))
    low = max(0.0, mean - (1.65 * standard_deviation))
    high = min(6.0, mean + (1.65 * standard_deviation))
    evidence_strength = 1.0 - math.exp(-0.75 * len(observations))
    disagreement_penalty = min(0.35, 0.1 * int(trait["contradiction_count"]))
    trait["distribution"] = {
        "family": "empirical_normal",
        "mean": round(mean, 3),
        "standard_deviation": round(standard_deviation, 3),
        "plausible_interval": [round(low, 3), round(high, 3)],
        "unit": "celsius",
    }
    trait["confidence"] = round(_clamp((0.82 * evidence_strength) - disagreement_penalty), 3)


def _winning_label(trait: Mapping[str, Any]) -> tuple[str | None, float]:
    distribution = trait.get("distribution")
    if not isinstance(distribution, Mapping) or not distribution:
        return None, 0.0
    numeric = {
        str(key): float(value)
        for key, value in distribution.items()
        if isinstance(value, (int, float))
    }
    if not numeric:
        return None, 0.0
    winner = max(numeric, key=lambda key: (numeric[key], key))
    return winner, numeric[winner]


def _apply_signal(
    traits: dict[str, dict[str, Any]],
    *,
    trait_name: str,
    value: Any,
    reliability: float,
    evidence_id: str,
) -> bool:
    if trait_name not in traits:
        return False
    trait = traits[trait_name]
    before_label, _ = _winning_label(trait)
    before_count = int(trait.get("evidence_count", 0))
    reliability = _clamp(float(reliability), 0.1, 1.0)

    if trait_name in _CATEGORICAL_TRAITS:
        label = str(value)
        if label not in _CATEGORICAL_TRAITS[trait_name]:
            return False
        if before_count and before_label and before_label != label:
            trait["contradiction_count"] = int(trait.get("contradiction_count", 0)) + 1
        trait["weighted_support"][label] = round(
            float(trait["weighted_support"].get(label, 0.35)) + (2.0 * reliability),
            4,
        )
        trait["evidence_count"] = before_count + 1
        provenance = list(trait.get("provenance") or [])
        provenance.append({"evidence_id": evidence_id, "reliability": round(reliability, 3), "supports": label})
        trait["provenance"] = provenance[-_MAX_PROVENANCE_PER_TRAIT:]
        _refresh_categorical(trait)
        return True

    number = _float(value)
    if number is None or not 0.0 <= number <= 6.0:
        return False
    previous_mean = trait.get("distribution", {}).get("mean")
    if before_count and previous_mean is not None and abs(number - float(previous_mean)) > 0.75:
        trait["contradiction_count"] = int(trait.get("contradiction_count", 0)) + 1
    observations = list(trait.get("weighted_observations") or [])
    observations.append({"value": round(number, 3), "weight": round(reliability, 3), "evidence_id": evidence_id})
    trait["weighted_observations"] = observations[-_MAX_PROVENANCE_PER_TRAIT:]
    trait["evidence_count"] = before_count + 1
    provenance = list(trait.get("provenance") or [])
    provenance.append({"evidence_id": evidence_id, "reliability": round(reliability, 3), "supports": round(number, 3)})
    trait["provenance"] = provenance[-_MAX_PROVENANCE_PER_TRAIT:]
    _refresh_numeric(trait)
    return True


def _normalise_answers(onboarding: Mapping[str, Any] | None, audit: dict[str, int]) -> list[dict[str, Any]]:
    if not isinstance(onboarding, Mapping):
        return []
    raw_answers = onboarding.get("answers")
    if isinstance(raw_answers, Mapping):
        raw_answers = [{"id": key, "answer": value} for key, value in raw_answers.items()]
    if not isinstance(raw_answers, Sequence) or isinstance(raw_answers, (str, bytes)):
        return []
    answers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in list(raw_answers)[:24]:
        if not isinstance(raw, Mapping):
            continue
        if _forbidden_key(raw.get("id")):
            audit["discarded_fields"] += 1
            continue
        question_id = _safe_identifier(raw.get("id"), "question")
        if question_id in seen:
            continue
        seen.add(question_id)
        answer, redactions = _redact_text(raw.get("answer"), 600)
        audit["redacted_values"] += redactions
        options_raw = raw.get("selected_option_ids") or []
        if isinstance(options_raw, str):
            options_raw = [options_raw]
        options = [
            _safe_identifier(option, "option")
            for option in list(options_raw)[:6]
            if not _forbidden_key(option)
        ] if isinstance(options_raw, Sequence) else []
        item = {"id": question_id, "selected_option_ids": options}
        if answer:
            item["answer"] = answer
        answers.append(item)
    if len(raw_answers) > 24:
        audit["truncated_values"] += len(raw_answers) - 24
    return answers


def _answer_signals(answer: Mapping[str, Any]) -> list[tuple[str, Any, float]]:
    options = {_normal_key(item) for item in list(answer.get("selected_option_ids") or [])}
    text = str(answer.get("answer") or "").lower()
    signals: list[tuple[str, Any, float]] = []

    option_signals: dict[str, tuple[tuple[str, Any, float], ...]] = {
        "comfort_routine_first": (("comfort_protection", "high", 0.95), ("routine_protection", "high", 0.78)),
        "bill_savings_first": (("savings_interest", "high", 0.95),),
        "grid_support_first": (("grid_support_interest", "high", 0.95),),
        "balanced_tradeoff": (
            ("comfort_protection", "moderate", 0.72),
            ("savings_interest", "moderate", 0.72),
            ("grid_support_interest", "moderate", 0.58),
        ),
        "confirm_before_changes": (("change_control", "ask_first", 0.96),),
        "do_not_move_without_approval": (
            ("change_control", "ask_first", 0.98),
            ("routine_protection", "high", 0.86),
        ),
        "shift_1_2h_deadline_protected": (
            ("change_control", "conditional_delegation", 0.9),
            ("routine_protection", "high", 0.82),
        ),
        "shift_to_cheaper_periods": (
            ("change_control", "conditional_delegation", 0.82),
            ("savings_interest", "high", 0.82),
        ),
        "automatic_optimization_ok": (("change_control", "delegated", 0.96),),
        "irregular_confirm_same_day": (
            ("routine_protection", "high", 0.92),
            ("change_control", "ask_first", 0.8),
        ),
        "caregiving_sleep_work": (("routine_protection", "high", 0.92),),
        "meals_chores": (("routine_protection", "moderate", 0.72),),
        "shower_hot_water": (("routine_protection", "high", 0.84),),
        "arrival_comfort": (("comfort_protection", "high", 0.84),),
    }
    for option in options:
        signals.extend(option_signals.get(option, ()))

    thermostat_options = {
        "almost_none_0_5c": 0.5,
        "small_1c_short": 1.0,
        "moderate_1_2c_with_benefit": 1.5,
        "larger_when_unoccupied": 2.5,
    }
    for option, value in thermostat_options.items():
        if option in options:
            signals.append(("thermostat_change_tolerance_c", value, 0.96))
            break

    # Natural-language signals are intentionally weak and explicit.  They add
    # nuance without turning free text into a deterministic personality label.
    phrase_signals: tuple[tuple[tuple[str, ...], tuple[str, Any, float]], ...] = (
        (("ask me first", "ask before", "confirm first", "先问", "先确认"), ("change_control", "ask_first", 0.62)),
        (("automatic is fine", "automatically", "可以自动"), ("change_control", "delegated", 0.56)),
        (("comfortable", "comfort", "舒适"), ("comfort_protection", "high", 0.5)),
        (("saving", "cheaper", "bill", "省钱", "电费"), ("savings_interest", "high", 0.5)),
        (("grid", "peak demand", "电网", "削峰"), ("grid_support_interest", "high", 0.5)),
        (("routine", "deadline", "shower", "caregiving", "日程", "洗澡", "照护"), ("routine_protection", "high", 0.52)),
        (("how much", "rough saving", "具体节省", "多少钱"), ("explanation_need", "quantified_when_available", 0.62)),
        (("what benefit", "what do i gain", "what i get", "benefit for me", "有什么好处", "我能得到什么"), ("explanation_need", "concrete", 0.56)),
        (("explain why", "tell me why", "解释原因"), ("explanation_need", "concrete", 0.58)),
    )
    for phrases, signal in phrase_signals:
        if any(phrase in text for phrase in phrases):
            signals.append(signal)
    if any(
        trait == "explanation_need" and value == "quantified_when_available"
        for trait, value, _ in signals
    ):
        signals = [
            signal
            for signal in signals
            if not (signal[0] == "explanation_need" and signal[1] == "concrete")
        ]
    return signals


def _strongest_signal_per_trait(
    signals: Sequence[tuple[str, Any, float]],
) -> list[tuple[str, Any, float]]:
    """Avoid counting one utterance as several independent observations."""
    strongest: dict[str, tuple[str, Any, float]] = {}
    for signal in signals:
        trait_name = signal[0]
        current = strongest.get(trait_name)
        if current is None or signal[2] > current[2]:
            strongest[trait_name] = signal
    return list(strongest.values())


def _normalise_calendar(calendar: Mapping[str, Any] | None, audit: dict[str, int]) -> list[dict[str, Any]]:
    if not isinstance(calendar, Mapping):
        return []
    days_raw = calendar.get("days")
    if not isinstance(days_raw, Sequence) or isinstance(days_raw, (str, bytes)):
        days_raw = [calendar]
    commitments: list[dict[str, Any]] = []
    for day in list(days_raw)[:14]:
        if not isinstance(day, Mapping):
            continue
        day_facts = {
            key: _sanitize(value, audit=audit)
            for key, value in day.items()
            if _normal_key(key) in {"date", "day", "weekday", "day_type", "summary"}
        }
        events = day.get("events") or day.get("appointments") or []
        if not isinstance(events, Sequence) or isinstance(events, (str, bytes)):
            events = []
        for raw_event in list(events)[:20]:
            if not isinstance(raw_event, Mapping):
                continue
            event = dict(day_facts)
            for key, value in raw_event.items():
                if _normal_key(key) in _CALENDAR_EVENT_FIELDS and not _forbidden_key(key):
                    event[str(key)] = _sanitize(value, audit=audit)
            if event:
                commitments.append(event)
        constraints = day.get("constraints") or day.get("protected_windows") or []
        if isinstance(constraints, Sequence) and not isinstance(constraints, (str, bytes)):
            for constraint in list(constraints)[:10]:
                text, redactions = _redact_text(constraint, 300)
                audit["redacted_values"] += redactions
                if text:
                    commitments.append({**day_facts, "constraint": text})
    return commitments[:80]


def _normalise_devices(devices: Mapping[str, Any] | None, audit: dict[str, int]) -> list[dict[str, Any]]:
    if not isinstance(devices, Mapping):
        return []
    result: list[dict[str, Any]] = []
    for raw_name, raw in list(devices.items())[:40]:
        if _forbidden_key(raw_name) or not isinstance(raw, Mapping):
            if _forbidden_key(raw_name):
                audit["discarded_fields"] += 1
            continue
        facts = {
            str(key): _sanitize(value, audit=audit)
            for key, value in raw.items()
            if _normal_key(key) in _DEVICE_FIELDS and not _forbidden_key(key)
        }
        if facts:
            result.append({"device": _safe_identifier(raw_name, "device"), **facts})
    return sorted(result, key=lambda item: item["device"])


def _time_bucket(hour: Any) -> str | None:
    number = _float(hour)
    if number is None:
        return None
    number %= 24
    if 5 <= number < 11:
        return "morning"
    if 11 <= number < 16:
        return "midday"
    if 16 <= number < 22:
        return "evening"
    return "overnight"


def _normalise_context(context: Mapping[str, Any] | None, audit: dict[str, int] | None = None) -> dict[str, Any]:
    if not isinstance(context, Mapping):
        return {}
    audit = audit if audit is not None else {"discarded_fields": 0, "redacted_values": 0, "truncated_values": 0}
    result: dict[str, Any] = {}
    # Accept both a flat event dictionary and the existing V2 event-context
    # envelope.  Only the whitelisted observable feature projection survives.
    sources: list[Mapping[str, Any]] = [context]
    for nested_key in ("event", "features"):
        nested = context.get(nested_key)
        if isinstance(nested, Mapping):
            sources.append(nested)
    for source in sources:
        for key, value in source.items():
            normal = _normal_key(key)
            if normal not in _CONTEXT_FIELDS or _forbidden_key(key):
                continue
            if normal in {"start_h", "trigger_h"}:
                bucket = _time_bucket(value)
                if bucket:
                    result["time_bucket"] = bucket
            elif normal == "type":
                result["event_type"] = _safe_identifier(value, "event")
            elif normal == "occupancy":
                result["occupied"] = bool(value) if isinstance(value, bool) else _sanitize(value, audit=audit)
            elif normal in {"devices", "affected_devices"}:
                values = value if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) else [value]
                result["affected_devices"] = sorted({_safe_identifier(item, "device") for item in values if item is not None})[:12]
            elif normal not in {"start_h", "trigger_h", "end_h"}:
                result[normal] = _sanitize(value, audit=audit)
    return result


def _context_signature(context: Mapping[str, Any]) -> str:
    if not context:
        return "general"
    return "|".join(f"{key}={_canonical(value)}" for key, value in sorted(context.items()))


def _feedback_signals(feedback: Mapping[str, Any]) -> list[tuple[str, Any, float]]:
    text_parts = [
        str(feedback.get(key) or "")
        for key in ("feedback", "user_feedback", "comment", "complaint")
    ]
    text = " ".join(text_parts).lower()
    signals: list[tuple[str, Any, float]] = []
    if any(term in text for term in ("too hot", "too cold", "uncomfortable", "不舒服", "太热", "太冷")):
        signals.append(("comfort_protection", "high", 0.88))
    if any(term in text for term in ("ask me", "without asking", "confirm", "先问", "未经确认")):
        signals.append(("change_control", "ask_first", 0.9))
    if any(term in text for term in ("deadline", "routine", "shower", "dinner", "sleep", "work call", "日程", "洗澡", "晚饭", "睡眠")):
        signals.append(("routine_protection", "high", 0.82))
    if any(term in text for term in ("how much", "exact saving", "rough saving", "多少钱", "具体节省")):
        signals.append(("explanation_need", "quantified_when_available", 0.86))
    elif any(term in text for term in ("explain", "why this", "more specific", "解释", "具体说明")):
        signals.append(("explanation_need", "concrete", 0.78))
    if any(term in text for term in ("saved money", "lower bill", "省钱", "电费降低")):
        signals.append(("savings_interest", "high", 0.65))

    observations = feedback.get("preference_observations") or feedback.get("observed_preferences") or []
    if isinstance(observations, Mapping):
        observations = [{"key": key, "value": value} for key, value in observations.items()]
    aliases = {
        "comfort_priority": "comfort_protection",
        "comfort_protection": "comfort_protection",
        "savings_interest": "savings_interest",
        "cost_priority": "savings_interest",
        "grid_support_interest": "grid_support_interest",
        "automation_preference": "change_control",
        "change_control": "change_control",
        "routine_protection": "routine_protection",
        "calendar_routine_sensitivity": "routine_protection",
        "explanation_need": "explanation_need",
        "thermostat_flexibility_c": "thermostat_change_tolerance_c",
        "thermostat_change_tolerance_c": "thermostat_change_tolerance_c",
    }
    value_aliases = {
        "medium": "moderate",
        "ask_before_vpp_specific_changes": "ask_first",
        "confirm_required": "ask_first",
        "suggestion_first_with_deadline_protection": "conditional_delegation",
        "automatic_when_deadlines_protected": "delegated",
    }
    if isinstance(observations, Sequence) and not isinstance(observations, (str, bytes)):
        for raw in list(observations)[:16]:
            if not isinstance(raw, Mapping):
                continue
            trait = aliases.get(_normal_key(raw.get("key") or raw.get("trait")))
            if not trait:
                continue
            value: Any = raw.get("value")
            if isinstance(value, str):
                value = value_aliases.get(_normal_key(value), _normal_key(value))
            reliability = _float(raw.get("confidence") or raw.get("reliability"))
            signals.append((trait, value, _clamp(reliability if reliability is not None else 0.72, 0.2, 1.0)))
    return signals


def _experience_signal(feedback: Mapping[str, Any]) -> tuple[str, float] | None:
    score = _float(feedback.get("score") or feedback.get("overall_score") or feedback.get("satisfaction_score"))
    if score is not None:
        if score >= 4:
            return "positive", 0.75
        if score <= 2:
            return "negative", 0.82
        return "mixed", 0.55
    accepted = feedback.get("accepted")
    if accepted is True:
        return "positive", 0.5
    if accepted is False:
        return "negative", 0.58
    return None


def _new_context_preference(context: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "context_signature": _context_signature(context),
        "context": dict(context),
        "experience": _blank_categorical_trait(("negative", "mixed", "positive")),
        "traits": {},
        "evidence_refs": [],
        "last_observed_at": None,
    }


def _add_evidence(
    model: dict[str, Any],
    *,
    source: str,
    observed_at: str,
    fact: str,
    reliability: float,
    context: Mapping[str, Any] | None = None,
    source_ref: str | None = None,
) -> str:
    item = {
        "source": source,
        "observed_at": observed_at,
        "fact": _redact_text(fact, 420)[0],
        "reliability": round(_clamp(reliability, 0.1, 1.0), 3),
    }
    if context:
        item["context"] = dict(context)
    if source_ref:
        item["source_ref"] = _safe_identifier(source_ref, "source")
    evidence_id = _evidence_id(source, item)
    item["evidence_id"] = evidence_id
    index = list(model.get("evidence_index") or [])
    if not any(existing.get("evidence_id") == evidence_id for existing in index):
        index.append(item)
        model["evidence_index"] = index[-_MAX_EVIDENCE:]
    return evidence_id


def _apply_context_feedback(
    model: dict[str, Any],
    context: Mapping[str, Any],
    feedback: Mapping[str, Any],
    evidence_id: str,
    observed_at: str,
    signals: Sequence[tuple[str, Any, float]],
) -> None:
    signature = _context_signature(context)
    entries = list(model.get("contextual_preferences") or [])
    entry = next((item for item in entries if item.get("context_signature") == signature), None)
    if entry is None:
        entry = _new_context_preference(context)
        entries.append(entry)
    experience = _experience_signal(feedback)
    if experience:
        trait = entry["experience"]
        label, reliability = experience
        previous, _ = _winning_label(trait)
        if trait["evidence_count"] and previous != label:
            trait["contradiction_count"] += 1
        trait["weighted_support"][label] += 2.0 * reliability
        trait["evidence_count"] += 1
        trait["provenance"].append({"evidence_id": evidence_id, "reliability": reliability, "supports": label})
        trait["provenance"] = trait["provenance"][-_MAX_PROVENANCE_PER_TRAIT:]
        _refresh_categorical(trait)
    for trait_name, value, reliability in signals:
        if trait_name not in _CATEGORICAL_TRAITS and trait_name not in _NUMERIC_TRAITS:
            continue
        contextual_traits = entry.setdefault("traits", {})
        if trait_name not in contextual_traits:
            contextual_traits[trait_name] = (
                _blank_categorical_trait(_CATEGORICAL_TRAITS[trait_name])
                if trait_name in _CATEGORICAL_TRAITS
                else _blank_numeric_trait()
            )
        _apply_signal(
            contextual_traits,
            trait_name=trait_name,
            value=value,
            reliability=max(0.2, reliability * 0.9),
            evidence_id=evidence_id,
        )
    refs = list(entry.get("evidence_refs") or [])
    if evidence_id not in refs:
        refs.append(evidence_id)
    entry["evidence_refs"] = refs[-20:]
    entry["last_observed_at"] = observed_at
    model["contextual_preferences"] = entries[-32:]


def _derive_unknowns_and_questions(model: dict[str, Any]) -> None:
    unknowns: list[dict[str, Any]] = []
    for trait_name, trait in model["traits"].items():
        confidence = float(trait.get("confidence", 0.0))
        contradictions = int(trait.get("contradiction_count", 0))
        if int(trait.get("evidence_count", 0)) == 0:
            reason = "not_yet_observed"
            priority = "high" if trait_name in {"change_control", "routine_protection"} else "medium"
        elif contradictions:
            reason = "evidence_disagrees"
            priority = "high"
        elif confidence < 0.42:
            reason = "limited_evidence"
            priority = "medium"
        else:
            continue
        unknowns.append({"dimension": trait_name, "reason": reason, "priority": priority})
    unknowns.sort(key=lambda item: (item["priority"] != "high", item["dimension"]))
    model["unknowns"] = unknowns
    questions: list[dict[str, Any]] = []
    for item in unknowns[:4]:
        question_id, question = _TRAIT_QUESTIONS[item["dimension"]]
        questions.append({
            "question_id": question_id,
            "dimension": item["dimension"],
            "question": question,
            "reason": item["reason"],
        })
    model["active_questions"] = questions


def _trait_snapshot(trait: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "distribution": deepcopy(trait.get("distribution")),
        "confidence": trait.get("confidence"),
        "evidence_count": trait.get("evidence_count"),
        "contradiction_count": trait.get("contradiction_count"),
    }


def _changed_traits(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    changes: list[dict[str, Any]] = []
    for name in sorted(after):
        old = _trait_snapshot(before.get(name, {}))
        new = _trait_snapshot(after[name])
        if _canonical(old) != _canonical(new):
            changes.append({"path": f"traits.{name}", "before": old, "after": new})
    return changes


def _apply_feedback_record(
    model: dict[str, Any],
    feedback: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None,
    observed_at: str,
    source_ref: str | None = None,
) -> list[str]:
    safe_feedback = sanitize_observable_payload(feedback)
    if not isinstance(safe_feedback, Mapping):
        return []
    text = " ".join(
        str(safe_feedback.get(key) or "")
        for key in ("feedback", "user_feedback", "comment", "complaint")
    ).strip()
    experience = _experience_signal(safe_feedback)
    fact = text or (f"Household reported a {experience[0]} interaction." if experience else "Household feedback was recorded.")
    reliability = 0.82 if text else 0.55
    known_evidence = {
        str(item.get("evidence_id"))
        for item in model.get("evidence_index", [])
        if isinstance(item, Mapping)
    }
    evidence_id = _add_evidence(
        model,
        source="household_feedback",
        observed_at=observed_at,
        fact=fact,
        reliability=reliability,
        context=context,
        source_ref=source_ref,
    )
    if evidence_id in known_evidence:
        return []
    signals = _strongest_signal_per_trait(_feedback_signals(safe_feedback))
    for trait_name, value, signal_reliability in signals:
        _apply_signal(
            model["traits"],
            trait_name=trait_name,
            value=value,
            reliability=signal_reliability,
            evidence_id=evidence_id,
        )
    if context:
        _apply_context_feedback(model, context, safe_feedback, evidence_id, observed_at, signals)
    return [evidence_id]


def initialize_household_model(
    onboarding: Mapping[str, Any] | None,
    *,
    household_id: str,
    calendar: Mapping[str, Any] | None = None,
    devices: Mapping[str, Any] | None = None,
    feedback_history: Sequence[Mapping[str, Any]] | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Initialise a household model from controller-observable inputs only.

    The function intentionally ignores ``inferred_profile``, ``preference_rules``
    and any persona/evaluator fields supplied alongside a compatible V2
    onboarding dictionary.  Inferences are reproduced from visible answers.
    """
    timestamp = str(observed_at or _now_iso())
    audit = {"discarded_fields": 0, "redacted_values": 0, "truncated_values": 0}
    # Audit the complete envelopes before projecting whitelisted observable
    # fields.  The sanitised copies are intentionally discarded.
    _sanitize(onboarding, audit=audit)
    _sanitize(calendar, audit=audit)
    _sanitize(devices, audit=audit)
    _sanitize(feedback_history, audit=audit)
    answers = _normalise_answers(onboarding, audit)
    calendar_commitments = _normalise_calendar(calendar, audit)
    device_capabilities = _normalise_devices(devices, audit)
    model: dict[str, Any] = {
        "schema_version": HOUSEHOLD_MODEL_VERSION,
        "household_id": _safe_identifier(household_id, "household"),
        "created_at": timestamp,
        "updated_at": timestamp,
        "revision": 0,
        "privacy_boundary": {
            "scope": "controller_observable_only",
            "allowed_sources": [
                "onboarding_answer",
                "shared_calendar",
                "device_capability",
                "household_feedback",
                "executed_event_context",
            ],
            "profile_source": "observable_evidence_not_role_card",
            "sanitization": audit,
        },
        "traits": _blank_traits(),
        "contextual_preferences": [],
        "observed_commitments": {
            "calendar": calendar_commitments,
            "devices": device_capabilities,
        },
        "evidence_index": [],
        "unknowns": [],
        "active_questions": [],
        "revision_ledger": [],
    }
    evidence_refs: list[str] = []
    before = deepcopy(model["traits"])
    for answer in answers:
        answer_fact = str(answer.get("answer") or "A questionnaire option was selected.")
        evidence_id = _add_evidence(
            model,
            source="onboarding_answer",
            observed_at=timestamp,
            fact=answer_fact,
            reliability=0.88 if answer.get("selected_option_ids") else 0.62,
            source_ref=str(answer.get("id")),
        )
        evidence_refs.append(evidence_id)
        for trait_name, value, reliability in _strongest_signal_per_trait(_answer_signals(answer)):
            _apply_signal(
                model["traits"],
                trait_name=trait_name,
                value=value,
                reliability=reliability,
                evidence_id=evidence_id,
            )

    for index, commitment in enumerate(calendar_commitments):
        evidence_refs.append(_add_evidence(
            model,
            source="shared_calendar",
            observed_at=timestamp,
            fact=f"Shared calendar commitment: {_canonical(commitment)}",
            reliability=0.9,
            source_ref=f"calendar-{index}",
        ))
    for device in device_capabilities:
        evidence_refs.append(_add_evidence(
            model,
            source="device_capability",
            observed_at=timestamp,
            fact=f"Observed device facts: {_canonical(device)}",
            reliability=0.96,
            source_ref=str(device.get("device")),
        ))

    for index, history_item in enumerate(list(feedback_history or [])[-24:]):
        if not isinstance(history_item, Mapping):
            continue
        history_context = _normalise_context(
            history_item.get("event_context") if isinstance(history_item.get("event_context"), Mapping) else history_item,
            audit,
        )
        item_timestamp = str(history_item.get("observed_at") or history_item.get("timestamp") or timestamp)
        evidence_refs.extend(_apply_feedback_record(
            model,
            history_item,
            context=history_context,
            observed_at=item_timestamp,
            source_ref=str(history_item.get("event_id") or f"history-{index}"),
        ))

    _derive_unknowns_and_questions(model)
    model["privacy_boundary"]["sanitization"] = audit
    model["revision_ledger"].append({
        "revision": 0,
        "observed_at": timestamp,
        "reason": "initialized_from_observable_sources",
        "changed_traits": _changed_traits(before, model["traits"]),
        "evidence_refs": list(dict.fromkeys(evidence_refs))[-_MAX_EVIDENCE:],
    })
    return json.loads(json.dumps(model, ensure_ascii=False, allow_nan=False))


def _validated_model_copy(model: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(model, Mapping) or model.get("schema_version") != HOUSEHOLD_MODEL_VERSION:
        raise ValueError(f"household model must use schema {HOUSEHOLD_MODEL_VERSION}")
    # Explicit top-level projection prevents fields injected after construction
    # from crossing the privacy boundary during an update.
    result = {
        "schema_version": HOUSEHOLD_MODEL_VERSION,
        "household_id": _safe_identifier(model.get("household_id"), "household"),
        "created_at": str(model.get("created_at") or _now_iso()),
        "updated_at": str(model.get("updated_at") or model.get("created_at") or _now_iso()),
        "revision": int(model.get("revision", 0) or 0),
        "privacy_boundary": _sanitize(model.get("privacy_boundary") or {}),
        "traits": deepcopy(model.get("traits") or _blank_traits()),
        "contextual_preferences": _sanitize(list(model.get("contextual_preferences") or [])[-32:]),
        "observed_commitments": _sanitize(
            model.get("observed_commitments") or {"calendar": [], "devices": []}
        ),
        "evidence_index": _sanitize(list(model.get("evidence_index") or [])[-_MAX_EVIDENCE:]),
        "unknowns": [],
        "active_questions": [],
        "revision_ledger": _sanitize(list(model.get("revision_ledger") or [])[-48:]),
    }
    # Only known traits survive, with missing traits restored as unknown.
    safe_traits = _blank_traits()
    for name in safe_traits:
        if isinstance(result["traits"].get(name), Mapping):
            safe_traits[name] = _sanitize(result["traits"][name])
    result["traits"] = safe_traits
    return result


def update_household_model(
    model: Mapping[str, Any],
    *,
    event_context: Mapping[str, Any] | None = None,
    feedback: Mapping[str, Any] | None = None,
    calendar: Mapping[str, Any] | None = None,
    devices: Mapping[str, Any] | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    """Return a revised model after one observable interaction or data refresh."""
    updated = _validated_model_copy(model)
    timestamp = str(observed_at or _now_iso())
    revision = int(updated.get("revision", 0)) + 1
    audit = {"discarded_fields": 0, "redacted_values": 0, "truncated_values": 0}
    _sanitize(event_context, audit=audit)
    _sanitize(feedback, audit=audit)
    _sanitize(calendar, audit=audit)
    _sanitize(devices, audit=audit)
    before = deepcopy(updated["traits"])
    evidence_refs: list[str] = []

    if calendar is not None:
        commitments = _normalise_calendar(calendar, audit)
        updated["observed_commitments"]["calendar"] = commitments
        for index, commitment in enumerate(commitments):
            evidence_refs.append(_add_evidence(
                updated,
                source="shared_calendar",
                observed_at=timestamp,
                fact=f"Shared calendar commitment: {_canonical(commitment)}",
                reliability=0.9,
                source_ref=f"calendar-r{revision}-{index}",
            ))
    if devices is not None:
        capabilities = _normalise_devices(devices, audit)
        updated["observed_commitments"]["devices"] = capabilities
        for device in capabilities:
            evidence_refs.append(_add_evidence(
                updated,
                source="device_capability",
                observed_at=timestamp,
                fact=f"Observed device facts: {_canonical(device)}",
                reliability=0.96,
                source_ref=str(device.get("device")),
            ))
    context = _normalise_context(event_context, audit)
    if feedback is not None:
        evidence_refs.extend(_apply_feedback_record(
            updated,
            feedback,
            context=context,
            observed_at=timestamp,
            source_ref=str(feedback.get("event_id") or f"revision-{revision}"),
        ))
    elif context:
        evidence_refs.append(_add_evidence(
            updated,
            source="executed_event_context",
            observed_at=timestamp,
            fact=f"Observed event context: {_canonical(context)}",
            reliability=0.9,
            context=context,
            source_ref=f"revision-{revision}",
        ))

    updated["revision"] = revision
    updated["updated_at"] = timestamp
    _derive_unknowns_and_questions(updated)
    previous_audit = updated.get("privacy_boundary", {}).get("sanitization") or {}
    updated["privacy_boundary"]["sanitization"] = {
        key: int(previous_audit.get(key, 0) or 0) + value
        for key, value in audit.items()
    }
    updated["revision_ledger"].append({
        "revision": revision,
        "observed_at": timestamp,
        "reason": "revised_from_observable_interaction",
        "context_signature": _context_signature(context),
        "changed_traits": _changed_traits(before, updated["traits"]),
        "evidence_refs": list(dict.fromkeys(evidence_refs)),
    })
    updated["revision_ledger"] = updated["revision_ledger"][-48:]
    return json.loads(json.dumps(updated, ensure_ascii=False, allow_nan=False))


_TRAIT_NATURAL_NAMES = {
    "comfort_protection": "protecting comfort",
    "savings_interest": "seeing a meaningful bill benefit",
    "grid_support_interest": "helping during grid peaks",
    "change_control": "how changes are authorised",
    "routine_protection": "protecting routines and service deadlines",
    "explanation_need": "the detail needed in an explanation",
}
_LABEL_NATURAL = {
    "low": "a lower priority",
    "moderate": "something to balance with other needs",
    "high": "an important priority",
    "ask_first": "ask before material changes",
    "conditional_delegation": "automatic changes only within agreed conditions",
    "delegated": "delegation when service commitments stay protected",
    "brief": "a brief explanation",
    "concrete": "a concrete, household-specific explanation",
    "quantified_when_available": "numbers when they can be supported by the available facts",
}


def _confidence_words(confidence: float) -> str:
    if confidence >= 0.72:
        return "well supported"
    if confidence >= 0.48:
        return "moderately supported"
    return "tentative"


def _context_similarity(left: Mapping[str, Any], right: Mapping[str, Any]) -> float:
    if not left or not right:
        return 0.0
    shared = set(left) & set(right)
    if not shared:
        return 0.0
    matches = sum(_canonical(left[key]) == _canonical(right[key]) for key in shared)
    return matches / max(len(left), len(right))


def _capsule_sentences(model: Mapping[str, Any], context: Mapping[str, Any]) -> list[tuple[int, str, list[str]]]:
    sentences: list[tuple[int, str, list[str]]] = []
    for trait_name, trait in model.get("traits", {}).items():
        if int(trait.get("evidence_count", 0)) == 0:
            continue
        refs = [str(item.get("evidence_id")) for item in trait.get("provenance", [])[-3:]]
        confidence = float(trait.get("confidence", 0.0))
        if trait_name in _NUMERIC_TRAITS:
            distribution = trait.get("distribution", {})
            mean = distribution.get("mean")
            interval = distribution.get("plausible_interval") or []
            if mean is not None:
                sentence = (
                    f"The household's stated short-event thermostat tolerance is around {mean:g} C"
                    f" (plausibly {interval[0]:g}-{interval[1]:g} C); this is {_confidence_words(confidence)}."
                )
                sentences.append((86, sentence, refs))
            continue
        label, probability = _winning_label(trait)
        if label:
            alternatives = sorted(
                ((key, float(value)) for key, value in trait["distribution"].items() if key != label),
                key=lambda item: item[1],
                reverse=True,
            )
            nuance = ""
            if alternatives and alternatives[0][1] >= 0.25:
                nuance = f" The evidence still leaves room for {alternatives[0][0].replace('_', ' ')}."
            sentence = (
                f"For {_TRAIT_NATURAL_NAMES[trait_name]}, their evidence currently points to "
                f"{_LABEL_NATURAL[label]}; this is {_confidence_words(confidence)}.{nuance}"
            )
            priority = 92 if trait_name in {"change_control", "routine_protection"} else 76
            sentences.append((priority, sentence, refs))

    calendar = list(model.get("observed_commitments", {}).get("calendar") or [])
    if calendar:
        relevant = calendar[:3]
        facts = "; ".join(_canonical(item) for item in relevant)
        sentences.append((96, f"Shared calendar commitments that may matter now: {facts}.", []))
    devices = list(model.get("observed_commitments", {}).get("devices") or [])
    if devices:
        summaries: list[str] = []
        affected = set(context.get("affected_devices") or [])
        ordered = sorted(devices, key=lambda item: (item.get("device") not in affected, item.get("device")))
        for device in ordered[:4]:
            facts = ", ".join(f"{key}={value}" for key, value in device.items() if key != "device")
            summaries.append(f"{device.get('device')}: {facts}")
        sentences.append((88, "Observed device and service facts: " + "; ".join(summaries) + ".", []))

    contextual = []
    for item in model.get("contextual_preferences", []):
        similarity = _context_similarity(context, item.get("context") or {}) if context else 0.0
        contextual.append((similarity, item))
    for similarity, item in sorted(contextual, key=lambda pair: pair[0], reverse=True)[:2]:
        if context and similarity <= 0:
            continue
        label, _ = _winning_label(item.get("experience") or {})
        refs = list(item.get("evidence_refs") or [])[-3:]
        if label:
            context_text = ", ".join(f"{key}={value}" for key, value in item.get("context", {}).items())
            sentences.append((100, f"In a similar context ({context_text}), the recorded experience was {label}; treat this as contextual rather than universal.", refs))

    return sorted(sentences, key=lambda item: item[0], reverse=True)


def estimate_prompt_tokens(value: Any) -> int:
    """Conservative dependency-free token estimate for capsule budgeting."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True)
    total = 0
    for piece in re.findall(r"[A-Za-z0-9_]+|[^\x00-\x7F]|[^\w\s]", text):
        if re.fullmatch(r"[A-Za-z0-9_]+", piece):
            total += max(1, math.ceil(len(piece) / 4))
        else:
            total += 1
    return total


def build_profile_capsule(
    model: Mapping[str, Any],
    *,
    context: Mapping[str, Any] | None = None,
    token_budget: int = 480,
) -> dict[str, Any]:
    """Build a natural, evidence-cited prompt capsule within ``token_budget``.

    The capsule reports beliefs and uncertainty but contains no prescribed
    action or decision.  This leaves the downstream reasoning model room to use
    its own planning capability while grounding it in household-specific facts.
    """
    safe_model = _validated_model_copy(model)
    # Unknowns are derived state. Recompute them after the strict top-level
    # projection instead of trusting caller-supplied question metadata.
    _derive_unknowns_and_questions(safe_model)
    budget = int(token_budget)
    if budget < _MIN_TOKEN_BUDGET:
        raise ValueError(f"token_budget must be at least {_MIN_TOKEN_BUDGET}")
    safe_context = _normalise_context(context)
    candidates = _capsule_sentences(safe_model, safe_context)
    chosen: list[str] = []
    evidence_refs: list[str] = []
    decision_unknowns: list[dict[str, str]] = []
    omitted = 0

    def make_capsule() -> dict[str, Any]:
        text = "What this household has actually told or shown us: " + " ".join(chosen)
        if not chosen:
            text += "The available evidence is still too limited for a confident household-specific summary."
        return {
            "schema_version": PROFILE_CAPSULE_VERSION,
            "text": text,
            "evidence_refs": list(dict.fromkeys(evidence_refs)),
            "decision_unknowns": deepcopy(decision_unknowns),
            "token_budget": budget,
            "estimated_tokens": 0,
            "omitted_items": omitted,
            "privacy_scope": "controller_observable_only",
        }

    # Reserve room for the single highest-priority unresolved question. The
    # full model retains every active question; the prompt capsule surfaces one
    # rather than crowding out observed evidence with a questionnaire.
    for raw in list(safe_model.get("active_questions") or [])[:1]:
        if not isinstance(raw, Mapping):
            continue
        question = " ".join(str(raw.get("question") or "").split())[:320]
        if not question:
            continue
        item = {
            "question_id": _safe_identifier(raw.get("question_id"), "question"),
            "dimension": _safe_identifier(raw.get("dimension"), "unknown"),
            "question": question,
            "reason": _safe_identifier(raw.get("reason"), "limited_evidence"),
        }
        decision_unknowns.append(item)
        trial = make_capsule()
        trial["estimated_tokens"] = estimate_prompt_tokens(trial)
        if trial["estimated_tokens"] > budget or (
            candidates and budget - trial["estimated_tokens"] < 120
        ):
            decision_unknowns.pop()
            omitted += 1

    for _, sentence, refs in candidates:
        trial_sentences = chosen + [sentence]
        trial_refs = list(dict.fromkeys(evidence_refs + refs))
        trial = {
            "schema_version": PROFILE_CAPSULE_VERSION,
            "text": "What this household has actually told or shown us: " + " ".join(trial_sentences),
            "evidence_refs": trial_refs,
            "decision_unknowns": deepcopy(decision_unknowns),
            "token_budget": budget,
            "estimated_tokens": 9999,
            "omitted_items": 0,
            "privacy_scope": "controller_observable_only",
        }
        if estimate_prompt_tokens(trial) <= budget:
            chosen = trial_sentences
            evidence_refs = trial_refs
        else:
            omitted += 1

    capsule = make_capsule()
    capsule["omitted_items"] = omitted
    capsule["estimated_tokens"] = estimate_prompt_tokens(capsule)
    # The minimum-budget fallback can still be trimmed if a caller uses an
    # unusually long identifier in evidence references.
    while capsule["estimated_tokens"] > budget and capsule["evidence_refs"]:
        capsule["evidence_refs"].pop()
        capsule["estimated_tokens"] = estimate_prompt_tokens(capsule)
    while capsule["estimated_tokens"] > budget and chosen:
        chosen.pop()
        capsule = make_capsule()
        capsule["omitted_items"] = omitted + 1
        capsule["estimated_tokens"] = estimate_prompt_tokens(capsule)
    if capsule["estimated_tokens"] > budget:
        capsule["text"] = "Observable household evidence is limited; retain uncertainty."
        capsule["evidence_refs"] = []
        capsule["decision_unknowns"] = []
        capsule["estimated_tokens"] = estimate_prompt_tokens(capsule)
    return capsule


__all__ = [
    "HOUSEHOLD_MODEL_VERSION",
    "PROFILE_CAPSULE_VERSION",
    "initialize_household_model",
    "update_household_model",
    "build_profile_capsule",
    "estimate_prompt_tokens",
    "sanitize_observable_payload",
]
