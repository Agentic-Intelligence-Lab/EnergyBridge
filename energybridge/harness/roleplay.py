"""Model-sensitive V2 role-play acceptance prompts and response validation.

The role-play model receives an auditable persona prior and explains concise
evidence-based adjustments to it. The normalizer preserves the model's final
probability exactly: it has no probability band, quality floor, or quality cap.
Only a caller-supplied hard safety veto may deterministically override the
role-play judgement.
"""

from __future__ import annotations

import json
import math
import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .profile import build_household_resume


ACCEPTANCE_SCHEMA_VERSION = "energybridge.roleplay_acceptance.v2"
VALID_DECISIONS = frozenset({"accept", "reject", "counteroffer"})
VALID_EVIDENCE_SOURCES = frozenset({
    "resume",
    "event",
    "live_preference",
    "plan",
    "ordinary_plan",
    "history",
    "hard_safety_veto",
    "other",
})
VALID_EVIDENCE_EFFECTS = frozenset({
    "supports_acceptance",
    "supports_rejection",
    "requires_change",
    "context_only",
})

# The response numbers are displayed decimals rather than hidden-precision
# calculations.  Half of one percentage point accommodates ordinary two-decimal
# rounding while still rejecting adjustments that do not explain the result.
PROBABILITY_ROUNDING_TOLERANCE = 0.005000001

_IDENTITY_KEY_PARTS = frozenset({
    "agent",
    "algorithm",
    "controller",
    "method",
    "model",
    "objective",
    "objective_source",
    "policy",
    "policy_source",
    "provider",
    "selected_skill",
    "skill",
    "speaker",
    "source",
})
_IDENTITY_LABEL_RE = re.compile(
    r"\b(?:agent|algorithm|controller|method|model|provider|objective(?:_source)?|policy_source)"
    r"\s*(?:name|id)?\s*[:=]\s*[^\s,;]+",
    re.IGNORECASE,
)
_IDENTITY_SENTINEL_RE = re.compile(
    r"\b(?:SECRET(?:[_-][A-Z0-9]+)+|[A-Z0-9_-]*(?:CONTROLLER|METHOD|MODEL|PROVIDER|ALGORITHM)"
    r"[A-Z0-9_-]*(?:SENTINEL|_ID)?)\b"
)
_KNOWN_IDENTITY_RE = re.compile(
    r"\b(?:energybridge|agent|controller|algorithm|provider|"
    r"mpc(?:_dynamic)?|hema(?:_agent)?|rule[_+ -]?milp|milp|"
    r"rl[_+ -]?ppo(?:_pref_v2)?|ppo|"
    r"(?:gpt|claude|gemini|llama|qwen|deepseek|model)[-_.a-z0-9]*)\b",
    re.IGNORECASE,
)
_PRODUCED_BY_RE = re.compile(
    r"\b(?:generated|produced|authored|selected|computed)\s+by\s+[-_.a-z0-9]+",
    re.IGNORECASE,
)

_VISIBLE_DEVICE_KEYS = frozenset({
    "ac",
    "hvac",
    "washer",
    "dishwasher",
    "dryer",
    "water_heater",
    "ev",
    "refrigerator",
    "core",
    "bottom",
    "middle",
    "top",
})
_VISIBLE_FACT_KEYS = frozenset({
    "action",
    "active",
    "amount",
    "available",
    "benefit",
    "change",
    "changes",
    "comfort",
    "completion_h",
    "condition",
    "constraints",
    "consequence",
    "cost",
    "count",
    "currency",
    "deadline_h",
    "description",
    "device",
    "duration_h",
    "effect",
    "enabled",
    "end_h",
    "energy",
    "energy_kwh",
    "expected_benefit",
    "fact",
    "humidity",
    "indoor_temp_c",
    "location",
    "member_id",
    "member_role",
    "mode",
    "name",
    "natural_language",
    "occupied",
    "occupancy",
    "on",
    "outdoor_temp_c",
    "power_kw",
    "present",
    "price",
    "projected_value",
    "protected_constraints",
    "reason",
    "recommended_actions",
    "required",
    "savings",
    "setpoint",
    "setpoint_c",
    "skip",
    "start_h",
    "status",
    "target",
    "target_soc",
    "temperature_c",
    "time_h",
    "title",
    "unit",
    "user_control",
    "value",
    "weather",
    "wind",
    "why_not",
    "why_request",
})
_VISIBLE_FACT_SUFFIXES = (
    "_h",
    "_c",
    "_kw",
    "_kwh",
    "_soc",
    "_count",
    "_duration",
    "_enabled",
    "_present",
    "_required",
    "_setpoint",
    "_skip",
    "_status",
)
_VISIBLE_DEVICE_PREFIXES = tuple(f"{name}_" for name in _VISIBLE_DEVICE_KEYS)


class RoleplayResponseError(ValueError):
    """Raised when an acceptance response cannot satisfy the V2 contract."""


def _compact_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str))


def _sanitize_identity_text(value: Any, limit: int) -> str:
    """Remove controller/model identity while retaining household-visible facts."""
    text = _compact_text(value, max(limit * 2, limit))
    text = _IDENTITY_LABEL_RE.sub("plan source omitted", text)
    text = _PRODUCED_BY_RE.sub("produced for the household", text)
    text = _IDENTITY_SENTINEL_RE.sub("plan source omitted", text)
    text = _KNOWN_IDENTITY_RE.sub("the plan", text)
    return _compact_text(text, limit)


def _is_visible_fact_key(value: Any) -> bool:
    key = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if not key:
        return False
    key_parts = set(key.split("_"))
    if key in _IDENTITY_KEY_PARTS or key_parts & _IDENTITY_KEY_PARTS or any(
        part in key
        for part in (
            "controller_id",
            "controller_name",
            "method_id",
            "method_name",
            "model_id",
            "model_name",
            "objective_source",
            "policy_source",
            "provider_id",
            "provider_name",
            "selected_skill",
        )
    ):
        return False
    return bool(
        key in _VISIBLE_DEVICE_KEYS
        or key in _VISIBLE_FACT_KEYS
        or key.startswith(_VISIBLE_DEVICE_PREFIXES)
        or key.endswith(_VISIBLE_FACT_SUFFIXES)
    )


def _sanitize_visible_tree(value: Any, *, text_limit: int = 500) -> Any:
    """Project nested content through a physical/household-fact allowlist."""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:48]:
            if not _is_visible_fact_key(raw_key):
                continue
            clean_value = _sanitize_visible_tree(raw_value, text_limit=text_limit)
            if clean_value not in (None, "", [], {}):
                result[str(raw_key)] = clean_value
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        result = [
            _sanitize_visible_tree(item, text_limit=text_limit)
            for item in list(value)[:24]
        ]
        return [item for item in result if item not in (None, "", [], {})]
    if isinstance(value, str):
        return _sanitize_identity_text(value, text_limit)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def _visible_relationship_history(resume: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_history = resume.get("relationship_history")
    if not isinstance(raw_history, Sequence) or isinstance(raw_history, (str, bytes)):
        return []
    result: list[dict[str, Any]] = []
    text_fields = {"user_statement", "decision", "feedback"}
    scalar_fields = {
        "event_id",
        "day",
        "satisfaction",
        "comfort_score",
        "energy_score",
        "vpp_score",
        "target_achieved",
    }
    for raw in list(raw_history)[-8:]:
        if not isinstance(raw, Mapping):
            continue
        item: dict[str, Any] = {}
        for key in scalar_fields:
            value = raw.get(key)
            if value is not None:
                item[key] = (
                    _sanitize_identity_text(value, 300)
                    if isinstance(value, str)
                    else _json_safe(value)
                )
        for key in text_fields:
            value = _sanitize_identity_text(raw.get(key, ""), 700)
            if value:
                item[key] = value
        service = _sanitize_visible_tree(raw.get("service_outcome") or {}, text_limit=300)
        if service:
            item["service_outcome"] = service
        if item:
            result.append(item)
    return result


def _visible_plan(plan: Mapping[str, Any] | None) -> dict[str, Any]:
    """Expose household-visible proposal facts and omit controller identity."""
    source = dict(plan or {})
    actions = source.get("appliance_actions")
    if not isinstance(actions, Mapping):
        actions = source.get("appliances") if isinstance(source.get("appliances"), Mapping) else {}
    visible = {
        "setpoint_c": _sanitize_visible_tree(source.get("setpoint"), text_limit=100),
        "next_check_hour": _sanitize_visible_tree(source.get("next_check_hour"), text_limit=100),
        "reason_shown_to_household": _sanitize_identity_text(source.get("reason", ""), 600),
        "appliance_actions": _sanitize_visible_tree(actions, text_limit=300),
    }
    explanation = source.get("strategy_explanation")
    if isinstance(explanation, Mapping):
        visible["strategy_explanation"] = {
            key: _sanitize_visible_tree(explanation.get(key), text_limit=400)
            for key in (
                "natural_language",
                "why_request",
                "recommended_actions",
                "protected_constraints",
                "user_control",
                "expected_benefit",
                "alternatives",
                "personalization_notes",
            )
            if explanation.get(key) not in (None, "", [], {})
        }
    for key in ("projected_comfort", "projected_service_outcomes", "projected_cost", "projected_energy"):
        if source.get(key) not in (None, "", [], {}):
            visible[key] = _sanitize_visible_tree(source.get(key), text_limit=300)
    return {key: value for key, value in visible.items() if value not in (None, "", [], {})}


def _visible_event(event: Mapping[str, Any] | None) -> dict[str, Any]:
    source = dict(event or {})
    return {
        key: _sanitize_visible_tree(source.get(key), text_limit=300)
        for key in (
            "id",
            "day",
            "trigger_h",
            "end_h",
            "duration_h",
            "current_hod",
            "weather",
            "outdoor_temp_c",
            "indoor_temp_c",
            "occupancy",
            "price_context",
            "grid_request",
        )
        if _sanitize_visible_tree(source.get(key), text_limit=300) not in (None, "", [], {})
    }


def _persona_baseline_probability(
    persona_config: Mapping[str, Any] | None,
    supplied: float | None,
) -> tuple[float, dict[str, Any]]:
    """Return a transparent persona prior, never a post-judgement bound."""
    persona = dict(persona_config or {})
    preferences = persona.get("preferences") if isinstance(persona.get("preferences"), Mapping) else {}
    candidates = (
        ("caller.baseline_acceptance_probability", supplied),
        ("persona_config.baseline_acceptance_probability", persona.get("baseline_acceptance_probability")),
        ("persona_config.preferences.baseline_acceptance_probability", preferences.get("baseline_acceptance_probability")),
        ("persona_config.preferences.vpp_acceptance_baseline_probability", preferences.get("vpp_acceptance_baseline_probability")),
    )
    for source, value in candidates:
        if value is None:
            continue
        return float(_unit_interval(value, "baseline_acceptance_probability")), {
            "source": source,
            "formula": "explicit_persona_prior",
        }

    override = preferences.get("vpp_override_prob", persona.get("vpp_override_prob"))
    if override is not None:
        override_probability = float(_unit_interval(override, "vpp_override_prob"))
        return 1.0 - override_probability, {
            "source": "persona_config.preferences.vpp_override_prob",
            "formula": "1 - vpp_override_prob",
            "input": override_probability,
        }

    weights = preferences.get("scoring_weights")
    if not isinstance(weights, Mapping):
        weights = persona.get("scoring_weights") if isinstance(persona.get("scoring_weights"), Mapping) else {}
    try:
        comfort = max(0.0, float(weights.get("comfort", 0.0) or 0.0))
        energy = max(0.0, float(weights.get("energy", 0.0) or 0.0))
        vpp = max(0.0, float(weights.get("vpp", 0.0) or 0.0))
    except (TypeError, ValueError):
        comfort = energy = vpp = 0.0
    total = comfort + energy + vpp
    if total > 0.0:
        return (energy + vpp) / total, {
            "source": "persona_config.preferences.scoring_weights",
            "formula": "(energy + vpp) / (comfort + energy + vpp)",
            "inputs": {"comfort": comfort, "energy": energy, "vpp": vpp},
        }
    return 0.5, {
        "source": "neutral_missing_persona_prior",
        "formula": "neutral prior used only because the persona has no acceptance signal",
    }


def build_roleplay_acceptance_prompts(
    *,
    persona_config: Mapping[str, Any] | None,
    appliance_config: Mapping[str, Any] | None = None,
    event: Mapping[str, Any] | None = None,
    proposed_plan: Mapping[str, Any] | None = None,
    default_plan: Mapping[str, Any] | None = None,
    past_events: Sequence[Any] | None = None,
    user_preference_text: str = "",
    hard_veto_reasons: Sequence[str] | None = None,
    baseline_acceptance_probability: float | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Build method-blind V2 acceptance prompts and their auditable payload."""
    resume = build_household_resume(
        persona_config,
        appliance_config=appliance_config,
        past_events=past_events,
    )
    # The source resume remains fully auditable, while its event history is
    # projected again at the role-play boundary so free-form past feedback
    # cannot reveal which controller/model produced an earlier plan.
    resume["relationship_history"] = _visible_relationship_history(resume)
    resume.setdefault("audit", {})["roleplay_projection"] = (
        "household_visible_method_identity_sanitized_v2"
    )
    baseline, baseline_audit = _persona_baseline_probability(
        persona_config,
        baseline_acceptance_probability,
    )
    veto_reasons = [
        _sanitize_identity_text(reason, 400)
        for reason in (hard_veto_reasons or [])
        if str(reason).strip()
    ]
    payload = {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "household_resume": resume,
        "event": _visible_event(event),
        "ordinary_household_plan": _visible_plan(default_plan),
        "offered_vpp_plan": _visible_plan(proposed_plan),
        "live_household_statement": _sanitize_identity_text(user_preference_text, 1200),
        "persona_baseline_acceptance_probability": baseline,
        "persona_baseline_audit": baseline_audit,
        "hard_safety_veto": {
            "active": bool(veto_reasons),
            "reasons": veto_reasons,
        },
    }
    system_prompt = (
        "You are simulating the residential user or household described in the supplied resume. "
        "Make this event decision in that household's own voice and from its lived circumstances. "
        "Treat biography, routines, household relationships, prior experiences, current context, and the "
        "specific offered actions as evidence; do not collapse the person into a tag or scoring weight. "
        "Do not infer, name, reward, or punish the controller, algorithm, provider, or model that produced the plan. "
        "Repeat the supplied persona baseline exactly, then usually give 2-4 short, non-duplicate signed adjustments for "
        "facts that actually matter now. Their sum plus the baseline must equal the final probability, allowing only normal "
        "decimal rounding; never use hidden clipping, floors, caps, bands, or canned deltas. Distinguish accepting the plan as written "
        "from making a counteroffer. A counteroffer must state the concrete change needed. "
        "Use 2-4 non-duplicate evidence items and refer to them by E1, E2, and so on from adjustments instead of repeating facts. "
        "A listed hard safety veto cannot be waived by the role-play user. Return valid JSON only."
    )
    user_prompt = (
        "Decide whether this household accepts the offered VPP plan as written. Keep the whole answer compact.\n\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        + "\n\nReturn exactly one JSON object in this single response shape:\n"
        "{\n"
        '  "decision": "accept|reject|counteroffer",\n'
        '  "baseline_acceptance_probability": number,\n'
        '  "adjustments": [{"dimension": "...", "delta": signed_number, "evidence": "E1", "reason": "one short clause"}],\n'
        '  "final_acceptance_probability": number,\n'
        '  "confidence": number,\n'
        '  "evidence": [{"id": "E1", "source": "resume|event|live_preference|plan|ordinary_plan|history|other", "fact": "one short fact", "effect": "supports_acceptance|supports_rejection|requires_change|context_only"}],\n'
        '  "counterfactual": {"changes": ["one minimal concrete change"], "decision_if_changed": "accept|reject|counteroffer|uncertain", "acceptance_probability_if_changed": number_or_null, "reason": "one short clause"},\n'
        '  "reason": "first-person reasoning, at most two short sentences",\n'
        '  "user_feedback": "exactly one short first-person sentence"\n'
        "}"
    )
    return system_prompt, user_prompt, payload


def _extract_json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, Mapping):
        return deepcopy(dict(raw))
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = "\n".join(
            line for line in text.splitlines()
            if not line.strip().startswith("```")
        ).strip()
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise RoleplayResponseError("role-play response did not contain a JSON object")
    try:
        data = json.loads(text[start : end + 1])
    except json.JSONDecodeError as exc:
        raise RoleplayResponseError(f"invalid role-play JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RoleplayResponseError("role-play JSON must be an object")
    return data


def _unit_interval(value: Any, field: str, *, nullable: bool = False) -> float | None:
    if value is None and nullable:
        return None
    if isinstance(value, bool):
        raise RoleplayResponseError(f"{field} must be a number, not a boolean")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RoleplayResponseError(f"{field} must be numeric") from exc
    if not math.isfinite(number) or not 0.0 <= number <= 1.0:
        raise RoleplayResponseError(f"{field} must be a finite number from 0 to 1")
    return number


def _normalize_decision(value: Any, *, counterfactual: bool = False) -> str:
    decision = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "accepted": "accept",
        "approve": "accept",
        "approved": "accept",
        "rejected": "reject",
        "decline": "reject",
        "declined": "reject",
        "counter_offer": "counteroffer",
        "conditional_accept": "counteroffer",
        "accept_with_changes": "counteroffer",
    }
    decision = aliases.get(decision, decision)
    allowed = set(VALID_DECISIONS)
    if counterfactual:
        allowed.add("uncertain")
    if decision not in allowed:
        raise RoleplayResponseError(f"invalid decision: {value!r}")
    return decision


def _normalize_evidence(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise RoleplayResponseError("evidence must be a non-empty list")
    result: list[dict[str, str]] = []
    for index, item in enumerate(raw[:12], start=1):
        if isinstance(item, str):
            evidence_id, source, fact, effect = f"E{index}", "other", item, "context_only"
        elif isinstance(item, Mapping):
            evidence_id = _compact_text(item.get("id") or f"E{index}", 24)
            source = str(item.get("source") or "other").strip().lower()
            fact = item.get("fact") or item.get("observation") or item.get("evidence")
            effect = str(item.get("effect") or "context_only").strip().lower()
        else:
            continue
        source = source if source in VALID_EVIDENCE_SOURCES else "other"
        effect = effect if effect in VALID_EVIDENCE_EFFECTS else "context_only"
        fact_text = _compact_text(fact, 500)
        if fact_text:
            result.append({
                "id": evidence_id or f"E{index}",
                "source": source,
                "fact": fact_text,
                "effect": effect,
            })
    if not result:
        raise RoleplayResponseError("evidence must contain at least one factual item")
    return result


def _normalize_adjustments(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise RoleplayResponseError("adjustments must be a list")
    result: list[dict[str, Any]] = []
    for item in raw[:12]:
        if not isinstance(item, Mapping):
            continue
        try:
            delta = float(item.get("delta"))
        except (TypeError, ValueError) as exc:
            raise RoleplayResponseError("adjustments[].delta must be numeric") from exc
        if not math.isfinite(delta):
            raise RoleplayResponseError("adjustments[].delta must be finite")
        if delta == 0.0:
            raise RoleplayResponseError("adjustments[].delta must be a non-zero signed adjustment")
        dimension = _compact_text(item.get("dimension") or item.get("factor"), 120)
        evidence = _compact_text(item.get("evidence") or item.get("fact"), 500)
        reason = _compact_text(item.get("reason"), 500)
        if not dimension or not evidence or not reason:
            raise RoleplayResponseError(
                "each adjustment requires dimension, evidence, and reason"
            )
        result.append({
            "dimension": dimension,
            "delta": delta,
            "evidence": evidence,
            "reason": reason,
        })
    if not result:
        raise RoleplayResponseError("adjustments must contain at least one signed item")
    return result


def _normalize_counterfactual(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise RoleplayResponseError("counterfactual must be an object")
    changes_raw = raw.get("changes") or []
    if isinstance(changes_raw, str):
        changes_raw = [changes_raw]
    if not isinstance(changes_raw, Sequence):
        raise RoleplayResponseError("counterfactual.changes must be a list")
    changes = [_compact_text(item, 400) for item in changes_raw[:8] if _compact_text(item, 400)]
    decision_if_changed = _normalize_decision(
        raw.get("decision_if_changed") or ("uncertain" if not changes else "counteroffer"),
        counterfactual=True,
    )
    probability = _unit_interval(
        raw.get("acceptance_probability_if_changed"),
        "counterfactual.acceptance_probability_if_changed",
        nullable=True,
    )
    return {
        "changes": changes,
        "decision_if_changed": decision_if_changed,
        "acceptance_probability_if_changed": probability,
        "reason": _compact_text(raw.get("reason", ""), 700),
    }


def _normalize_valid_response(
    data: Mapping[str, Any],
    *,
    expected_baseline: float | None = None,
) -> dict[str, Any]:
    decision = _normalize_decision(data.get("decision"))
    baseline = _unit_interval(
        data.get("baseline_acceptance_probability"),
        "baseline_acceptance_probability",
    )
    if expected_baseline is not None and not math.isclose(
        float(baseline),
        float(expected_baseline),
        rel_tol=0.0,
        abs_tol=PROBABILITY_ROUNDING_TOLERANCE,
    ):
        raise RoleplayResponseError(
            "baseline_acceptance_probability does not repeat the supplied persona baseline "
            f"within rounding tolerance ({baseline!r} != {expected_baseline!r})"
        )
    adjustments = _normalize_adjustments(data.get("adjustments"))
    probability_value = data.get("final_acceptance_probability")
    if probability_value is None:
        probability_value = data.get("acceptance_probability")
    if probability_value is None:
        probability_value = data.get("probability")
    probability = _unit_interval(probability_value, "final_acceptance_probability")
    confidence = _unit_interval(data.get("confidence"), "confidence")
    evidence = _normalize_evidence(data.get("evidence"))
    counterfactual = _normalize_counterfactual(data.get("counterfactual"))
    reasoning = _compact_text(
        data.get("reason") or data.get("reasoning") or data.get("acceptance_reasoning"),
        1200,
    )
    user_feedback = _compact_text(data.get("user_feedback") or data.get("energybridge_feedback"), 700)
    if not reasoning:
        raise RoleplayResponseError("reason is required")
    if not user_feedback:
        raise RoleplayResponseError("user_feedback is required")
    adjustment_sum = math.fsum(float(item["delta"]) for item in adjustments)
    arithmetic_residual = float(probability) - float(baseline) - adjustment_sum
    if abs(arithmetic_residual) > PROBABILITY_ROUNDING_TOLERANCE:
        raise RoleplayResponseError(
            "final_acceptance_probability must equal baseline plus signed adjustments "
            f"within rounding tolerance (residual={arithmetic_residual:.9g})"
        )
    response = {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "decision": decision,
        "baseline_acceptance_probability": baseline,
        "adjustments": adjustments,
        "final_acceptance_probability": probability,
        "acceptance_probability": probability,
        "confidence": confidence,
        "evidence": evidence,
        "counterfactual": counterfactual,
        "reason": reasoning,
        "user_feedback": user_feedback,
        "hard_veto_applied": False,
        "hard_veto_reasons": [],
        "normalization": {
            "source": "roleplay_response",
            "rounding_tolerance": PROBABILITY_ROUNDING_TOLERANCE,
        },
    }
    if expected_baseline is not None:
        response["normalization"]["expected_baseline"] = expected_baseline
    response["normalization"]["adjustment_sum"] = adjustment_sum
    response["normalization"]["arithmetic_residual"] = arithmetic_residual
    return response


def _hard_veto_only_response(reasons: Sequence[str], error: Exception | None = None) -> dict[str, Any]:
    clean_reasons = [_compact_text(reason, 400) for reason in reasons if str(reason).strip()]
    response = {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "decision": "reject",
        "baseline_acceptance_probability": None,
        "adjustments": [],
        "final_acceptance_probability": 0.0,
        "acceptance_probability": 0.0,
        "confidence": 1.0,
        "evidence": [
            {"source": "hard_safety_veto", "fact": reason, "effect": "supports_rejection"}
            for reason in clean_reasons
        ],
        "counterfactual": {
            "changes": [f"Resolve hard safety issue: {reason}" for reason in clean_reasons],
            "decision_if_changed": "uncertain",
            "acceptance_probability_if_changed": None,
            "reason": "The household can reconsider only after every hard safety issue is resolved.",
        },
        "reason": "I cannot accept a plan that fails a non-waivable safety or service constraint.",
        "user_feedback": "Resolve the listed safety or service issue before asking me to participate.",
        "hard_veto_applied": True,
        "hard_veto_reasons": clean_reasons,
        "normalization": {"source": "hard_safety_veto"},
    }
    if error is not None:
        response["normalization"]["roleplay_error"] = _compact_text(error, 300)
    return response


def _apply_hard_veto(response: dict[str, Any], reasons: Sequence[str]) -> dict[str, Any]:
    clean_reasons = [_compact_text(reason, 400) for reason in reasons if str(reason).strip()]
    if not clean_reasons:
        return response
    out = deepcopy(response)
    out["roleplay_decision_before_veto"] = out.get("decision")
    out["roleplay_acceptance_probability_before_veto"] = out.get("acceptance_probability")
    out["roleplay_final_acceptance_probability_before_veto"] = out.get("final_acceptance_probability")
    out["decision"] = "reject"
    out["final_acceptance_probability"] = 0.0
    out["acceptance_probability"] = 0.0
    out["hard_veto_applied"] = True
    out["hard_veto_reasons"] = clean_reasons
    out["evidence"] = list(out.get("evidence") or []) + [
        {"source": "hard_safety_veto", "fact": reason, "effect": "supports_rejection"}
        for reason in clean_reasons
    ]
    out["normalization"] = {
        **dict(out.get("normalization") or {}),
        "hard_veto_override": True,
    }
    return out


def normalize_roleplay_acceptance_response(
    raw: Any,
    fallback: Any = None,
    hard_veto_reasons: Sequence[str] | None = None,
    expected_baseline: float | None = None,
) -> dict[str, Any]:
    """Validate a V2 response without inventing or reshaping its probability.

    Invalid model output raises ``RoleplayResponseError`` unless the caller
    explicitly supplies ``fallback``. Hard veto reasons are the sole exception:
    they produce a deterministic rejection and retain any valid pre-veto model
    judgement for audit.
    """
    expected = (
        float(_unit_interval(expected_baseline, "expected_baseline"))
        if expected_baseline is not None
        else None
    )
    veto_reasons = [str(reason) for reason in (hard_veto_reasons or []) if str(reason).strip()]
    try:
        response = _normalize_valid_response(
            _extract_json_object(raw),
            expected_baseline=expected,
        )
    except Exception as exc:
        error = exc if isinstance(exc, RoleplayResponseError) else RoleplayResponseError(str(exc))
        if veto_reasons:
            return _hard_veto_only_response(veto_reasons, error=error)
        if fallback is None:
            raise error
        try:
            response = _normalize_valid_response(
                _extract_json_object(fallback),
                expected_baseline=expected,
            )
        except Exception as fallback_exc:
            normalized_error = (
                fallback_exc
                if isinstance(fallback_exc, RoleplayResponseError)
                else RoleplayResponseError(str(fallback_exc))
            )
            raise RoleplayResponseError(
                f"role-play response invalid ({error}); caller fallback invalid ({normalized_error})"
            ) from fallback_exc
        response["normalization"] = {
            **dict(response.get("normalization") or {}),
            "source": "caller_fallback",
            "roleplay_error": _compact_text(error, 300),
        }
    return _apply_hard_veto(response, veto_reasons)


__all__ = [
    "ACCEPTANCE_SCHEMA_VERSION",
    "PROBABILITY_ROUNDING_TOLERANCE",
    "RoleplayResponseError",
    "VALID_DECISIONS",
    "build_roleplay_acceptance_prompts",
    "normalize_roleplay_acceptance_response",
]
