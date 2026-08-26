"""Pure-JSON orchestration facade for the EnergyBridge V3 harness.

This module composes the observable household profile, evidence memory, and
open portfolio planner into one explicit decision lifecycle.  It deliberately
contains no simulator, LLM client, persistence, evaluator, or method-specific
policy.  Callers own those concerns and exchange only JSON-compatible values
with this facade.

The lifecycle boundary is strict:

``prepare -> raw proposal -> validated -> consented -> executed -> outcome``

Resolving a planning response records only the raw and validated stages.
Consent never implies execution, and an outcome is attributed to physical
performance only when the caller supplies an explicit executed plan.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from datetime import date, datetime, timezone
from typing import Any, Mapping, Sequence

from .memory_v3 import (
    MEMORY_V3_VERSION,
    build_event_context_v3,
    compact_memory_context_v3,
    initialize_memory_v3,
    migrate_v2_memory,
    record_episode_stage,
    retract_episode_stage,
)
from .planning import (
    PLANNING_SCHEMA_VERSION,
    anonymize_advisor_candidates,
    build_planning_prompts,
    evaluate_planning_response,
)
from .profile_v3 import (
    HOUSEHOLD_MODEL_VERSION,
    build_profile_capsule,
    initialize_household_model,
    sanitize_observable_payload,
    update_household_model,
)


HARNESS_SESSION_VERSION = "energybridge.observable_harness_session.v3"
PREPARED_DECISION_VERSION = "energybridge.prepared_harness_decision.v3"
HARNESS_RESOLUTION_VERSION = "energybridge.harness_plan_resolution.v3"

__all__ = [
    "HARNESS_SESSION_VERSION",
    "PREPARED_DECISION_VERSION",
    "HARNESS_RESOLUTION_VERSION",
    "initialize_harness_session",
    "prepare_harness_decision",
    "resolve_harness_plan",
    "record_harness_outcome",
]


_IDENTITY_OR_PRIVATE_KEYS = {
    "method",
    "method_name",
    "controller_method",
    "model",
    "model_name",
    "model_id",
    "base_model",
    "llm_model",
    "provider",
    "provider_name",
    "vendor",
    "algorithm",
    "controller_name",
    "control_source",
    "selected_skill",
    "skill_selection",
    "persona",
    "persona_config",
    "role_card",
    "hidden_persona",
    "latent_persona",
    "evaluator",
    "evaluator_state",
    "ground_truth",
    "system_prompt",
    "developer_prompt",
    "developer",
    "roleplay_prompt",
    "roleplay_system_prompt",
    "acceptance_target",
    "target_acceptance",
    "target_rate",
    "acceptance_probability",
    "acceptance",
    "override",
    "override_probability",
    "override_prob",
    "vpp_override_prob",
    "scoring_weights",
    "api_key",
    "api_base",
    "api_endpoint",
    "api_url",
    "base_url",
    "endpoint",
    "access_token",
    "authorization",
    "credential",
    "credentials",
    "secret",
    "developer_message",
    "developer_instruction",
    "private",
    "private_data",
    "private_key",
    "endpoint_url",
    "llm_host",
}
_PRIVATE_KEY_FRAGMENTS = (
    "hidden_persona",
    "latent_persona",
    "role_card",
    "roleplay_prompt",
    "system_prompt",
    "developer_prompt",
    "ground_truth",
    "evaluator_state",
    "scoring_weight",
    "acceptance_target",
    "target_acceptance",
    "api_key",
    "access_token",
    "developer_message",
    "developer_instruction",
    "private_key",
    "endpoint_url",
    "llm_host",
    "api_base",
    "base_url",
    "acceptance_probability",
    "override_probability",
    "vpp_override_prob",
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
_EXECUTABLE_KEYS = {
    "setpoint",
    "setpoint_c",
    "appliances",
    "appliance_actions",
    "actions",
    "commands",
}
_AFFIRMATIVE_EXECUTION_STATUSES = {"executed", "applied", "succeeded", "completed"}
_NON_EXECUTION_STATUSES = {
    "failed",
    "rejected",
    "aborted",
    "skipped",
    "not_applied",
    "not_executed",
    "cancelled",
    "canceled",
    "queued",
    "pending",
}
_TRUE_CONSENT_VALUES = {
    "accept",
    "accepted",
    "approve",
    "approved",
    "consent",
    "consented",
    "yes",
    "y",
    "true",
    "同意",
    "接受",
    "是",
}
_FALSE_CONSENT_VALUES = {
    "reject",
    "rejected",
    "decline",
    "declined",
    "no",
    "n",
    "false",
    "not_accepted",
    "拒绝",
    "不同意",
    "否",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _timestamp(value: str | datetime | date | None) -> str:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value or "").strip()
    return text or _now_iso()


def _normal_key(value: Any) -> str:
    split = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(value).strip())
    return re.sub(r"[^a-z0-9]+", "_", split.lower()).strip("_")


def _private_key(value: Any) -> bool:
    raw_key = str(value)
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
    tokens = set(key.split("_"))
    identity_tokens = {
        "method", "model", "provider", "vendor", "planner", "algorithm",
        "deployment", "backend", "endpoint", "host", "llm",
    }
    identity_field = bool(tokens & identity_tokens) and key not in {
        "household_model",
    }
    return (
        key in _IDENTITY_OR_PRIVATE_KEYS
        or identity_field
        or bool(tokens & {
            "evaluator", "persona",
            "auth", "authorization", "bearer", "credential", "credentials",
            "password", "secret", "token", "developer",
        })
        or {"private", "key"}.issubset(tokens)
        or key.startswith(("hidden_", "secret_", "credential_"))
        or any(fragment in key for fragment in _PRIVATE_KEY_FRAGMENTS)
    )


def _redact_text(value: Any, limit: int = 8000) -> str:
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


def _observable_copy(value: Any, *, depth: int = 0) -> Any:
    """Create a bounded, method-blind, JSON-compatible detached value."""
    if depth > 18:
        return "[depth limit]"
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, item in list(value.items())[:300]:
            if _private_key(raw_key):
                continue
            result[str(raw_key)[:160]] = _observable_copy(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_observable_copy(item, depth=depth + 1) for item in list(value)[:300]]
    return _redact_text(value, 1000)


def _external_observable(value: Any) -> Any:
    # The profile boundary also removes contact-like secrets; the facade pass
    # adds the method/model boundary that applies across all three components.
    return _observable_copy(sanitize_observable_payload(value))


def _json_result(value: Any) -> Any:
    """Assert and return a detached, standards-compliant JSON value."""
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _safe_identifier(value: Any, fallback: str) -> str:
    source = _redact_text(value, 180).strip()
    if not source:
        return fallback
    ascii_prefix = re.sub(r"[^A-Za-z0-9_.:@/-]+", "-", source).strip("-")
    # Preserve already-safe identifiers for readable audit trails.  Whenever
    # projection changes the caller's identifier (notably Unicode-only names),
    # add an opaque digest so distinct households cannot collapse to the same
    # fallback namespace and share warm memory.
    if ascii_prefix and ascii_prefix == source:
        return ascii_prefix
    digest = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return f"{ascii_prefix or fallback}-{digest}"


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _retime_initial_memory(memory: Mapping[str, Any], observed_at: str) -> dict[str, Any]:
    """Make explicit initialization time apply to all onboarding evidence."""
    result = deepcopy(dict(memory))
    result["created_at"] = observed_at
    result["updated_at"] = observed_at
    for container_name in ("stable_beliefs", "contextual_beliefs"):
        for belief in (result.get(container_name) or {}).values():
            if not isinstance(belief, dict):
                continue
            belief["last_observed_at"] = observed_at
            belief["evaluated_at"] = observed_at
            for evidence in belief.get("evidence") or []:
                if isinstance(evidence, dict):
                    evidence["observed_at"] = observed_at
    for entry in result.get("belief_revision_ledger") or []:
        if isinstance(entry, dict):
            entry["observed_at"] = observed_at
    return result


def _validated_session_copy(session: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(session, Mapping):
        raise TypeError("session must be a mapping")
    result = _observable_copy(session)
    if not isinstance(result, dict) or result.get("schema_version") != HARNESS_SESSION_VERSION:
        raise ValueError(f"session must use schema {HARNESS_SESSION_VERSION!r}")
    profile = result.get("household_model")
    memory = result.get("memory")
    if not isinstance(profile, Mapping) or profile.get("schema_version") != HOUSEHOLD_MODEL_VERSION:
        raise ValueError("session household_model is not an observable V3 household model")
    if not isinstance(memory, Mapping) or memory.get("version") != MEMORY_V3_VERSION:
        raise ValueError("session memory is not observable V3 memory")
    return result


def _decision_index(session: Mapping[str, Any], episode_id: str) -> int | None:
    for index, item in enumerate(session.get("decisions") or []):
        if isinstance(item, Mapping) and str(item.get("episode_id")) == episode_id:
            return index
    return None


def _replace_decision(session: dict[str, Any], decision: Mapping[str, Any]) -> None:
    decisions = list(session.get("decisions") or [])
    episode_id = str(decision.get("episode_id") or "")
    index = _decision_index(session, episode_id)
    if index is None:
        decisions.append(deepcopy(dict(decision)))
    else:
        decisions[index] = deepcopy(dict(decision))
    session["decisions"] = decisions[-160:]


def _unique_episode_id(session: Mapping[str, Any], event_context: Mapping[str, Any]) -> str:
    base = _safe_identifier(event_context.get("event_id"), "episode")
    existing = {
        str(item.get("episode_id"))
        for item in list(session.get("decisions") or []) + list(session.get("memory", {}).get("episodes") or [])
        if isinstance(item, Mapping)
    }
    if base not in existing:
        return base
    suffix = int(session.get("revision", 0) or 0) + 1
    candidate = f"{base}:r{suffix}"
    while candidate in existing:
        suffix += 1
        candidate = f"{base}:r{suffix}"
    return candidate


def _anonymous_advisor_values(
    advisor_candidates: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    external = _external_observable(list(advisor_candidates or []))
    external = external if isinstance(external, list) else []
    candidates = [item for item in external if isinstance(item, Mapping)]
    anonymous = anonymize_advisor_candidates(candidates)
    return [
        deepcopy(dict(item["candidate"]))
        for item in anonymous.get("presented_candidates") or []
        if isinstance(item, Mapping) and isinstance(item.get("candidate"), Mapping)
    ]


def initialize_harness_session(
    onboarding: Mapping[str, Any] | None,
    household_id: str,
    *,
    calendar: Mapping[str, Any] | None = None,
    devices: Mapping[str, Any] | None = None,
    prior_memory: Mapping[str, Any] | None = None,
    observed_at: str | datetime | date | None = None,
) -> dict[str, Any]:
    """Initialize a self-contained observable profile-memory session.

    ``prior_memory`` may be V3 or the public observable V2 format.  V3 memory
    owned by another household is rejected to prevent accidental cross-home
    retrieval.  No persistence or network access occurs here.
    """
    timestamp = _timestamp(observed_at)
    safe_household_id = _safe_identifier(household_id, "household")
    safe_onboarding = _external_observable(onboarding or {})
    safe_calendar = _external_observable(calendar or {})
    safe_devices = _external_observable(devices or {})

    household_model = initialize_household_model(
        _mapping(safe_onboarding),
        household_id=safe_household_id,
        calendar=_mapping(safe_calendar),
        devices=_mapping(safe_devices),
        observed_at=timestamp,
    )
    if prior_memory is None:
        memory = initialize_memory_v3(_mapping(safe_onboarding), household_id=safe_household_id)
        memory = _retime_initial_memory(memory, timestamp)
    else:
        raw_version = str(prior_memory.get("version") or "")
        safe_prior = _observable_copy(prior_memory)
        if not isinstance(safe_prior, Mapping):
            raise TypeError("prior_memory must be a mapping")
        memory = migrate_v2_memory(safe_prior)
        if raw_version == MEMORY_V3_VERSION:
            owner = memory.get("owner") if isinstance(memory.get("owner"), Mapping) else {}
            prior_household = str(owner.get("household_id") or "")
            if prior_household and prior_household != safe_household_id:
                raise ValueError("prior_memory belongs to a different household")
        else:
            # V2 used a persona namespace rather than a household namespace.
            # The public caller supplies the observable household association.
            memory["owner"] = {"household_id": safe_household_id}

    session = {
        "schema_version": HARNESS_SESSION_VERSION,
        "household_id": safe_household_id,
        "created_at": timestamp,
        "updated_at": timestamp,
        "revision": 0,
        "privacy_boundary": {
            "scope": "controller_observable_only",
            "identity_blind": True,
            "evaluator_state_allowed": False,
        },
        "household_model": household_model,
        "memory": memory,
        "observable_sources": {
            "onboarding": deepcopy(memory.get("onboarding") or {}),
            "calendar": safe_calendar,
            "devices": safe_devices,
        },
        "decisions": [],
    }
    return _json_result(session)


def prepare_harness_decision(
    session: Mapping[str, Any],
    event: Mapping[str, Any],
    observable_state: Mapping[str, Any],
    *,
    advisor_candidates: Sequence[Mapping[str, Any]] | None = None,
    explicit_constraints: Sequence[Mapping[str, Any]] | None = None,
    profile_token_budget: int = 480,
    memory_episode_limit: int = 4,
    memory_char_budget: int = 6000,
    observed_at: str | datetime | date | None = None,
) -> dict[str, Any]:
    """Prepare grounded planning prompts and return the revised JSON session."""
    updated = _validated_session_copy(session)
    safe_event = _external_observable(event)
    safe_state = _external_observable(observable_state)
    if not isinstance(safe_event, Mapping) or not isinstance(safe_state, Mapping):
        raise TypeError("event and observable_state must be mappings")
    safe_event = dict(safe_event)
    safe_state = dict(safe_state)
    timestamp = _timestamp(observed_at or safe_event.get("observed_at"))

    sources = _mapping(updated.get("observable_sources"))
    calendar = safe_state.get("calendar")
    devices = safe_state.get("devices")
    calendar = calendar if isinstance(calendar, Mapping) else sources.get("calendar")
    devices = devices if isinstance(devices, Mapping) else sources.get("devices")
    calendar = _mapping(calendar)
    devices = _mapping(devices)

    profile_updates: dict[str, Any] = {}
    calendar_changed = _canonical(calendar) != _canonical(sources.get("calendar") or {})
    if isinstance(safe_state.get("calendar"), Mapping) and calendar_changed:
        profile_updates["calendar"] = calendar
        sources["calendar"] = calendar
    devices_changed = _canonical(devices) != _canonical(sources.get("devices") or {})
    if isinstance(safe_state.get("devices"), Mapping) and devices_changed:
        profile_updates["devices"] = devices
        sources["devices"] = devices
    if profile_updates:
        updated["household_model"] = update_household_model(
            updated["household_model"],
            observed_at=timestamp,
            **profile_updates,
        )
    updated["observable_sources"] = sources

    home_state = safe_state.get("home_state")
    home_state = home_state if isinstance(home_state, Mapping) else safe_state
    user_input = safe_state.get("user_input", safe_event.get("user_input", ""))
    event_context = build_event_context_v3(
        safe_event,
        calendar=calendar,
        home_state=_mapping(home_state),
        user_input=str(user_input or ""),
        observed_at=timestamp,
    )
    episode_id = _unique_episode_id(updated, event_context)
    event_context["event_id"] = episode_id

    profile_capsule = build_profile_capsule(
        updated["household_model"],
        context=event_context,
        token_budget=int(profile_token_budget),
    )
    memory_capsule = compact_memory_context_v3(
        updated["memory"],
        event_context,
        k=int(memory_episode_limit),
        max_chars=int(memory_char_budget),
    )
    safe_advisors = _anonymous_advisor_values(advisor_candidates)
    safe_constraints = _external_observable(list(explicit_constraints or []))
    safe_constraints = [item for item in safe_constraints if isinstance(item, Mapping)]
    system_prompt, user_prompt = build_planning_prompts(
        observable_state=safe_state,
        observable_profile=profile_capsule,
        memory=memory_capsule,
        event=safe_event,
        advisor_candidates=safe_advisors,
        explicit_constraints=safe_constraints,
    )

    updated["revision"] = int(updated.get("revision", 0) or 0) + 1
    updated["updated_at"] = timestamp
    decision = {
        "episode_id": episode_id,
        "status": "prepared",
        "prepared_at": timestamp,
        "event_context": event_context,
        "planning_input_fingerprint": _fingerprint(
            {"system_prompt": system_prompt, "user_prompt": user_prompt}
        ),
        "profile_evidence_refs": list(profile_capsule.get("evidence_refs") or []),
        "memory_context_signature": memory_capsule.get("current_context_signature"),
    }
    _replace_decision(updated, decision)

    prepared = {
        "schema_version": PREPARED_DECISION_VERSION,
        "episode_id": episode_id,
        "session": updated,
        "observable_state": safe_state,
        "event": safe_event,
        "event_context": event_context,
        "profile_capsule": profile_capsule,
        "memory_capsule": memory_capsule,
        "advisor_candidates": safe_advisors,
        "explicit_constraints": safe_constraints,
        "planning": {
            "schema_version": PLANNING_SCHEMA_VERSION,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
        },
    }
    return _json_result(prepared)


def _decode_and_sanitize_response(raw: Any) -> Any:
    if isinstance(raw, str):
        text = raw.strip()
        match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, flags=re.IGNORECASE | re.DOTALL)
        candidate = match.group(1).strip() if match else text
        try:
            raw = json.loads(candidate)
        except json.JSONDecodeError:
            return _external_observable(raw)
    return _external_observable(raw)


def _context_with_plans(
    base: Mapping[str, Any],
    *,
    raw_proposal: Mapping[str, Any] | None = None,
    validated_plan: Mapping[str, Any] | None = None,
    consented_plan: Mapping[str, Any] | None = None,
    executed_plan: Mapping[str, Any] | None = None,
    observations: Mapping[str, Any] | None = None,
    observed_at: str | None = None,
) -> dict[str, Any]:
    return build_event_context_v3(
        _mapping(base.get("event")),
        calendar=_mapping(base.get("calendar")),
        home_state=_mapping(base.get("home_state")),
        user_input=str(base.get("user_input") or ""),
        raw_proposal=raw_proposal,
        validated_plan=validated_plan,
        consented_plan=consented_plan,
        executed_plan=executed_plan,
        observations=observations,
        observed_at=observed_at or str(base.get("observed_at") or ""),
    )


def resolve_harness_plan(
    prepared: Mapping[str, Any],
    raw_model_response: Any,
    *,
    objective_specs: Mapping[str, Mapping[str, Any] | str] | None = None,
    observed_at: str | datetime | date | None = None,
) -> dict[str, Any]:
    """Validate a base-model portfolio and record raw/validated stages only."""
    if not isinstance(prepared, Mapping) or prepared.get("schema_version") != PREPARED_DECISION_VERSION:
        raise ValueError(f"prepared must use schema {PREPARED_DECISION_VERSION!r}")
    prepared_copy = _observable_copy(prepared)
    updated = _validated_session_copy(prepared_copy.get("session") or {})
    episode_id = str(prepared_copy.get("episode_id") or "")
    decision_index = _decision_index(updated, episode_id)
    if not episode_id or decision_index is None:
        raise ValueError("prepared decision is not registered in its session")

    safe_response = _decode_and_sanitize_response(raw_model_response)
    safe_specs = _external_observable(objective_specs or {})
    evaluation = evaluate_planning_response(
        safe_response,
        observable_state=_mapping(prepared_copy.get("observable_state")),
        observable_profile=_mapping(prepared_copy.get("profile_capsule")),
        memory=_mapping(prepared_copy.get("memory_capsule")),
        event=_mapping(prepared_copy.get("event")),
        advisor_candidates=[
            item for item in list(prepared_copy.get("advisor_candidates") or [])
            if isinstance(item, Mapping)
        ],
        explicit_constraints=[
            item for item in list(prepared_copy.get("explicit_constraints") or [])
            if isinstance(item, Mapping)
        ],
        objective_specs=_mapping(safe_specs),
    )
    timestamp = _timestamp(observed_at)
    base_context = _mapping(prepared_copy.get("event_context"))
    audit = _mapping(evaluation.get("portfolio_audit"))
    selection_audit = _mapping(audit.get("model_selection"))
    requested_id = selection_audit.get("requested_candidate_id")
    selected_lifecycle = next(
        (
            item
            for item in audit.get("candidate_lifecycles") or []
            if isinstance(item, Mapping)
            and item.get("origin") == "model"
            and str(item.get("candidate_id")) == str(requested_id)
        ),
        None,
    )

    raw_plan: dict[str, Any] = {}
    validated_plan: dict[str, Any] = {}
    event_context = base_context
    if isinstance(selected_lifecycle, Mapping):
        raw_plan = _mapping(selected_lifecycle.get("raw_snapshot"))
        validated_plan = _mapping(selected_lifecycle.get("validated_snapshot"))
        if raw_plan:
            event_context = _context_with_plans(
                base_context,
                raw_proposal=raw_plan,
                observed_at=timestamp,
            )
            event_context["event_id"] = episode_id
            updated["memory"] = record_episode_stage(
                updated["memory"],
                episode_id,
                "raw_proposal",
                {"plan": raw_plan, "status": "received"},
                event_context=event_context,
                observed_at=timestamp,
            )
        if validated_plan:
            event_context = _context_with_plans(
                base_context,
                raw_proposal=raw_plan,
                validated_plan=validated_plan,
                observed_at=timestamp,
            )
            event_context["event_id"] = episode_id
            updated["memory"] = record_episode_stage(
                updated["memory"],
                episode_id,
                "validated",
                {
                    "plan": validated_plan,
                    "status": selected_lifecycle.get("status"),
                    "checks": selected_lifecycle.get("constraints_checked") or [],
                    "patches": selected_lifecycle.get("json_patches") or [],
                    "reason": selection_audit.get("validator_reason", ""),
                },
                event_context=event_context,
                observed_at=timestamp,
            )

    decision = dict((updated.get("decisions") or [])[decision_index])
    decision.update(
        {
            "status": (
                "plan_validated"
                if evaluation.get("selection_status") == "selected"
                else "replan_required"
            ),
            "resolved_at": timestamp,
            "event_context": event_context,
            "selection": {
                "status": evaluation.get("selection_status"),
                "selected_candidate_id": evaluation.get("selected_candidate_id"),
                "planning_input_fingerprint": audit.get("planning_input_fingerprint"),
                "candidate_count": len(audit.get("candidate_lifecycles") or []),
                "advisor_override_allowed": False,
            },
        }
    )
    _replace_decision(updated, decision)
    updated["revision"] = int(updated.get("revision", 0) or 0) + 1
    updated["updated_at"] = timestamp

    result = {
        "schema_version": HARNESS_RESOLUTION_VERSION,
        "episode_id": episode_id,
        "session": updated,
        "planning_evaluation": evaluation,
        "selection_status": evaluation.get("selection_status"),
        "selected_candidate_id": evaluation.get("selected_candidate_id"),
        "selected_executable_plan": evaluation.get("selected_executable_plan"),
    }
    return _json_result(result)


def _find_episode(memory: Mapping[str, Any], episode_id: str) -> dict[str, Any]:
    for item in memory.get("episodes") or []:
        if isinstance(item, Mapping) and str(item.get("episode_id")) == episode_id:
            return deepcopy(dict(item))
    return {}


def _stage_plan(episode: Mapping[str, Any], stage: str) -> dict[str, Any]:
    stages = episode.get("stages") if isinstance(episode.get("stages"), Mapping) else {}
    record = stages.get(stage) if isinstance(stages.get(stage), Mapping) else {}
    return _mapping(record.get("plan"))


def _rebuild_household_profile(session: Mapping[str, Any]) -> dict[str, Any]:
    """Project the latest observable episode stages into a household model.

    Memory keeps ``stage_history`` for auditability, including superseded
    corrections.  The household profile is a current-state projection, so it
    is rebuilt from each episode's latest stage snapshot.  This prevents a
    corrected score or feedback item from remaining as contradictory evidence.
    """
    sources = _mapping(session.get("observable_sources"))
    memory = _mapping(session.get("memory"))
    onboarding = sources.get("onboarding")
    if not isinstance(onboarding, Mapping):
        onboarding = memory.get("onboarding")
    created_at = str(session.get("created_at") or _now_iso())
    model = initialize_household_model(
        _mapping(onboarding),
        household_id=str(session.get("household_id") or "household"),
        calendar=_mapping(sources.get("calendar")),
        devices=_mapping(sources.get("devices")),
        observed_at=created_at,
    )

    episodes = [
        item for item in memory.get("episodes") or []
        if isinstance(item, Mapping)
    ]
    episodes.sort(key=lambda item: (str(item.get("created_at") or ""), str(item.get("episode_id") or "")))
    for episode in episodes:
        stages = episode.get("stages") if isinstance(episode.get("stages"), Mapping) else {}
        consent = stages.get("consented") if isinstance(stages.get("consented"), Mapping) else {}
        executed = stages.get("executed") if isinstance(stages.get("executed"), Mapping) else {}
        outcome = stages.get("outcome") if isinstance(stages.get("outcome"), Mapping) else {}
        if not (consent or executed or outcome):
            continue

        attribution = (
            episode.get("causal_attribution")
            if isinstance(episode.get("causal_attribution"), Mapping)
            else {}
        )
        outcome_is_executed = (
            attribution.get("outcome_attribution") == "observational_executed_plan"
        )
        observations = (
            outcome.get("observations")
            if outcome_is_executed and isinstance(outcome.get("observations"), Mapping)
            else {}
        )
        # Scores and physical outcomes shape the household experience only
        # when an actuator-observed plan precedes them.  Consent itself and
        # interaction feedback remain valid preference evidence even when the
        # proposal was declined or never executed.
        feedback = deepcopy(dict(observations))
        decision = consent.get("decision")
        if isinstance(decision, bool) and "accepted" not in feedback:
            feedback["accepted"] = decision
        if consent.get("feedback") and "feedback" not in feedback:
            feedback["feedback"] = consent["feedback"]
        if outcome.get("feedback_text") and "feedback" not in feedback:
            feedback["feedback"] = outcome["feedback_text"]
        if feedback:
            feedback["event_id"] = str(episode.get("episode_id") or "episode")

        observed_at = str(
            outcome.get("recorded_at")
            or executed.get("recorded_at")
            or consent.get("recorded_at")
            or episode.get("updated_at")
            or created_at
        )
        kwargs: dict[str, Any] = {"feedback": feedback} if feedback else {}
        model = update_household_model(
            model,
            event_context=_mapping(episode.get("context")),
            observed_at=observed_at,
            **kwargs,
        )
    return model


def _consent_bool(value: Any, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        stripped = value.strip().lower()
        normalized = _normal_key(stripped)
        if stripped in _TRUE_CONSENT_VALUES or normalized in _TRUE_CONSENT_VALUES:
            return True
        if stripped in _FALSE_CONSENT_VALUES or normalized in _FALSE_CONSENT_VALUES:
            return False
    raise ValueError(f"consent field {field!r} must be an explicit accept/reject boolean or alias")


def _consent_payload(consented: bool | Mapping[str, Any], offered_plan: Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(consented, bool):
        payload: dict[str, Any] = {"decision": consented}
    elif isinstance(consented, Mapping):
        payload = dict(_external_observable(consented))
    else:
        raise TypeError("consented must be a boolean or mapping")
    decision_fields = [
        key for key in ("decision", "accepted", "approved")
        if key in payload
    ]
    if not decision_fields:
        raise ValueError("consented mapping requires decision, accepted, or approved")
    for key in decision_fields:
        payload[key] = _consent_bool(payload[key], key)
    decisions = {payload[key] for key in decision_fields}
    if len(decisions) > 1:
        raise ValueError("consent fields decision/accepted/approved disagree")
    if not isinstance(payload.get("plan"), Mapping) and offered_plan:
        payload["plan"] = deepcopy(dict(offered_plan))
    return payload


def _execution_payload(executed: bool | Mapping[str, Any]) -> dict[str, Any] | None:
    if executed is False:
        return None
    if executed is True:
        raise ValueError("executed=True has no plan; supply the actuator-observed plan explicitly")
    if not isinstance(executed, Mapping):
        raise TypeError("executed must be a boolean or mapping")
    safe = dict(_external_observable(executed))
    nested = safe.get("plan")
    has_nested_plan = isinstance(nested, Mapping) and bool(nested)
    has_direct_plan = any(key in safe for key in _EXECUTABLE_KEYS)
    has_plan = has_nested_plan or has_direct_plan
    executed_flag = safe.get("executed")
    if "executed" in safe and not isinstance(executed_flag, bool):
        raise ValueError("executed field must be boolean when supplied")
    status_supplied = safe.get("status") not in (None, "")
    status = _normal_key(safe.get("status")) if status_supplied else ""

    if status == "unknown":
        raise ValueError("unknown execution status cannot establish actuator execution")
    if status and status not in _AFFIRMATIVE_EXECUTION_STATUSES | _NON_EXECUTION_STATUSES:
        raise ValueError(f"unsupported execution status {status!r}")
    if status in _AFFIRMATIVE_EXECUTION_STATUSES and executed_flag is False:
        raise ValueError("affirmative execution status conflicts with executed=false")
    if status in _NON_EXECUTION_STATUSES and executed_flag is True:
        raise ValueError("non-execution status conflicts with executed=true")

    denied = executed_flag is False or status in _NON_EXECUTION_STATUSES
    if denied and has_plan:
        raise ValueError("non-execution status conflicts with the supplied executed plan")
    if denied:
        return None
    if not has_plan:
        raise ValueError("execution observation must contain an explicit actuator-observed plan")
    if has_nested_plan:
        return safe
    if has_direct_plan:
        return {"plan": safe, "status": "executed"}
    raise AssertionError("unreachable execution payload state")


def record_harness_outcome(
    session: Mapping[str, Any],
    episode_id: str,
    *,
    consented: bool | Mapping[str, Any] | None = None,
    executed: bool | Mapping[str, Any] | None = None,
    outcome: Mapping[str, Any] | None = None,
    calendar: Mapping[str, Any] | None = None,
    devices: Mapping[str, Any] | None = None,
    observed_at: str | datetime | date | None = None,
) -> dict[str, Any]:
    """Record independently observed consent, execution, and outcome stages.

    Passing ``executed=True`` is intentionally rejected: an affirmative flag
    cannot prove which controls reached the actuators.  Pass ``False`` for no
    execution or a mapping containing the actual plan for execution evidence.
    """
    updated = _validated_session_copy(session)
    episode_id = str(episode_id or "")
    decision_index = _decision_index(updated, episode_id)
    if not episode_id or decision_index is None:
        raise ValueError("episode_id is not registered in the session")
    timestamp = _timestamp(observed_at)
    decision = dict((updated.get("decisions") or [])[decision_index])
    base_context = _mapping(decision.get("event_context"))
    episode = _find_episode(updated["memory"], episode_id)
    raw_plan = _stage_plan(episode, "raw_proposal")
    validated_plan = _stage_plan(episode, "validated")
    offered_plan = validated_plan or raw_plan
    consented_plan = _stage_plan(episode, "consented")
    executed_plan = _stage_plan(episode, "executed")
    final_context = _context_with_plans(
        base_context,
        raw_proposal=raw_plan,
        validated_plan=validated_plan,
        consented_plan=consented_plan,
        executed_plan=executed_plan,
        observed_at=timestamp,
    )
    final_context["event_id"] = episode_id

    consent_payload: dict[str, Any] | None = None
    if consented is not None:
        consent_payload = _consent_payload(consented, offered_plan)
        consented_plan = _mapping(consent_payload.get("plan"))
        final_context = _context_with_plans(
            base_context,
            raw_proposal=raw_plan,
            validated_plan=validated_plan,
            consented_plan=consented_plan,
            executed_plan=executed_plan,
            observed_at=timestamp,
        )
        final_context["event_id"] = episode_id
        updated["memory"] = record_episode_stage(
            updated["memory"],
            episode_id,
            "consented",
            consent_payload,
            event_context=final_context,
            observed_at=timestamp,
        )

    execution_payload: dict[str, Any] | None = None
    if executed is not None:
        execution_payload = _execution_payload(executed)
        if execution_payload is not None:
            executed_plan = _mapping(execution_payload.get("plan"))
            final_context = _context_with_plans(
                base_context,
                raw_proposal=raw_plan,
                validated_plan=validated_plan,
                consented_plan=consented_plan,
                executed_plan=executed_plan,
                observed_at=timestamp,
            )
            final_context["event_id"] = episode_id
            updated["memory"] = record_episode_stage(
                updated["memory"],
                episode_id,
                "executed",
                execution_payload,
                event_context=final_context,
                observed_at=timestamp,
            )
        else:
            current_episode = _find_episode(updated["memory"], episode_id)
            current_stages = (
                current_episode.get("stages")
                if isinstance(current_episode.get("stages"), Mapping)
                else {}
            )
            # A failed/invalid planning response may register a session
            # decision without ever creating a memory episode.  Explicit
            # non-execution is then already the truth and needs no retraction;
            # a later outcome can create an unattributed episode normally.
            if isinstance(current_stages.get("executed"), Mapping):
                updated["memory"] = retract_episode_stage(
                    updated["memory"],
                    episode_id,
                    "executed",
                    reason="explicit_non_execution_correction",
                    observed_at=timestamp,
                )
            executed_plan = {}
            corrected_episode = _find_episode(updated["memory"], episode_id)
            corrected_stages = (
                corrected_episode.get("stages")
                if isinstance(corrected_episode.get("stages"), Mapping)
                else {}
            )
            corrected_outcome = (
                corrected_stages.get("outcome")
                if isinstance(corrected_stages.get("outcome"), Mapping)
                else {}
            )
            prior_observations = (
                corrected_outcome.get("observations")
                if isinstance(corrected_outcome.get("observations"), Mapping)
                else {}
            )
            final_context = _context_with_plans(
                base_context,
                raw_proposal=raw_plan,
                validated_plan=validated_plan,
                consented_plan=consented_plan,
                observations=prior_observations,
                observed_at=timestamp,
            )
            final_context["event_id"] = episode_id

    safe_outcome: dict[str, Any] | None = None
    if outcome is not None:
        if not isinstance(outcome, Mapping):
            raise TypeError("outcome must be a mapping")
        safe_outcome = dict(_external_observable(outcome))
        final_context = _context_with_plans(
            base_context,
            raw_proposal=raw_plan,
            validated_plan=validated_plan,
            consented_plan=consented_plan,
            executed_plan=executed_plan,
            observations=safe_outcome,
            observed_at=timestamp,
        )
        final_context["event_id"] = episode_id
        updated["memory"] = record_episode_stage(
            updated["memory"],
            episode_id,
            "outcome",
            safe_outcome,
            event_context=final_context,
            observed_at=timestamp,
        )

    safe_calendar = _external_observable(calendar) if calendar is not None else None
    safe_devices = _external_observable(devices) if devices is not None else None
    sources = _mapping(updated.get("observable_sources"))
    if isinstance(safe_calendar, Mapping):
        sources["calendar"] = safe_calendar
    if isinstance(safe_devices, Mapping):
        sources["devices"] = safe_devices
    updated["observable_sources"] = sources
    updated["household_model"] = _rebuild_household_profile(updated)

    latest_episode = _find_episode(updated["memory"], episode_id)
    stages = latest_episode.get("stages") if isinstance(latest_episode.get("stages"), Mapping) else {}
    if "outcome" in stages:
        status = "outcome_recorded"
    elif "executed" in stages:
        status = "executed"
    elif "consented" in stages:
        status = "consented"
    else:
        status = decision.get("status", "prepared")
    decision.update(
        {
            "status": status,
            "updated_at": timestamp,
            "event_context": final_context,
            "observed_stages": {
                stage: stage in stages
                for stage in ("raw_proposal", "validated", "consented", "executed", "outcome")
            },
        }
    )
    _replace_decision(updated, decision)
    updated["revision"] = int(updated.get("revision", 0) or 0) + 1
    updated["updated_at"] = timestamp
    return _json_result(updated)
