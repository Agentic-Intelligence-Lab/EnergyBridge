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
    r"\b(?:synthetic[_-]*energybridge[-_.a-z0-9]*|energybridge|agent|controller|algorithm|provider|openai|anthropic|xai|dmxapi|"
    r"mpc[-_.a-z0-9]*|hema[-_.a-z0-9]*|rule[_+ -]?milp|milp|"
    r"rl[-_.a-z0-9]*|ppo[-_.a-z0-9]*|"
    r"(?:gpt|chatgpt|claude|gemini|llama|qwen|deepseek|mistral|grok|o[134]|model)[-_.a-z0-9]*)\b",
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
    # Identity removal can turn natural phrases such as "the agent" into
    # "the the plan".  Keep the projected household narrative readable.
    text = re.sub(r"\bthe\s+the plan\b", "the household plan", text, flags=re.IGNORECASE)
    text = re.sub(
        r"\b(?:the plan|plan source omitted)(?:\s+(?:the plan|plan source omitted))+\b",
        "plan source omitted",
        text,
        flags=re.IGNORECASE,
    )
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


def _sanitize_household_resume(value: Any) -> Any:
    """Preserve household biography while removing source/model identity recursively."""
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for raw_key, raw_value in list(value.items())[:64]:
            key = str(raw_key)
            normalized = key.strip().lower().replace("-", "_").replace(" ", "_")
            if normalized in {"schema_version", "resume_id", "household_id"}:
                result[key] = (
                    "household_resume.v2"
                    if normalized == "schema_version"
                    else _json_safe(raw_value)
                )
                continue
            if normalized == "audit" and isinstance(raw_value, Mapping):
                result[key] = {
                    audit_key: _json_safe(raw_value.get(audit_key))
                    for audit_key in (
                        "profile_fingerprint",
                        "resume_fingerprint",
                        "roleplay_projection",
                    )
                    if raw_value.get(audit_key) not in (None, "")
                }
                continue
            parts = set(normalized.split("_"))
            if normalized in {
                "controller_context_source",
                "agent_context",
                "objective_source",
                "persona_prompt",
                "policy_source",
                "roleplay_prompt",
                "roleplay_user_prompt",
                "selected_skill",
                "source",
                "source_fingerprint",
                "system_prompt",
            } or parts & {
                "algorithm", "controller", "method", "model", "provider", "speaker",
            }:
                continue
            clean = _sanitize_household_resume(raw_value)
            if clean not in (None, "", [], {}):
                result[key] = clean
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        items = [_sanitize_household_resume(item) for item in list(value)[:32]]
        return [item for item in items if item not in (None, "", [], {})]
    if isinstance(value, str):
        return _sanitize_identity_text(value, 2000)
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def sanitize_household_resume_for_roleplay(resume: Mapping[str, Any] | None) -> dict[str, Any]:
    """Project a resume to household facts without controller/model provenance."""
    sanitized = _sanitize_household_resume(dict(resume or {}))
    return sanitized if isinstance(sanitized, dict) else {}


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
    visible = {
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
    if source.get("trigger_h") is not None and source.get("end_h") is not None:
        try:
            visible["trigger_hod"] = float(source["trigger_h"]) % 24.0
            visible["end_hod"] = float(source["end_h"]) % 24.0
        except (TypeError, ValueError):
            pass
        visible["window_semantics"] = (
            "Event trigger_h/end_h may be absolute simulation hours; appliance action times are local hour-of-day. "
            "Compare actions to [trigger_hod, end_hod). An action starting exactly at end_hod is outside the event, "
            "while an interval spanning any time before end_hod overlaps it."
        )
    return visible


def _visible_verified_plan_facts(facts: Mapping[str, Any] | None) -> dict[str, Any]:
    """Expose checked household impacts without controller identity or scores."""
    source = dict(facts or {})
    allowed = (
        "proposed_setpoint_c",
        "default_setpoint_c",
        "preferred_min_c",
        "preferred_max_c",
        "preference_tolerance_c",
        "changed_service_count",
        "changed_services",
        "fixed_services_modified",
        "skip_devices",
        "vpp_conflicts",
        "present_services",
        "specified_services",
        "unspecified_services",
    )
    visible = {
        key: _sanitize_visible_tree(source.get(key), text_limit=400)
        for key in allowed
        if source.get(key) not in (None, "", [], {})
        or isinstance(source.get(key), bool)
        or source.get(key) == 0
        or (
            key in {
                "present_services",
                "specified_services",
                "unspecified_services",
                "fixed_services_modified",
                "skip_devices",
                "vpp_conflicts",
            }
            and key in source
        )
    }
    if "vpp_conflicts" in source:
        visible["event_overlap_note"] = (
            "vpp_conflicts is the checked overlap result under the event's half-open interval. An empty list means "
            "none of the supplied effective appliance intervals overlaps this event."
        )
    if "changed_service_count" in source:
        visible["offer_change_note"] = (
            "changed_services contains only appliance services whose effective action differs from the ordinary plan. "
            "Other displayed actions are inherited context and must not receive offer-specific credit."
        )
    try:
        if source.get("proposed_setpoint_c") is not None and source.get("default_setpoint_c") is not None:
            visible["setpoint_change_c"] = round(
                float(source["proposed_setpoint_c"]) - float(source["default_setpoint_c"]),
                3,
            )
    except (TypeError, ValueError):
        pass
    if source.get("unspecified_services"):
        visible["action_coverage_note"] = (
            "The effective offered plan does not explicitly cover these present household services. Missing actions are "
            "not proof that service is protected; use the visible explanation and ordinary plan to decide whether the "
            "offer resolves that uncertainty."
        )
    return visible


def _effective_visible_offer(
    proposed_plan: Mapping[str, Any] | None,
    default_plan: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project the plan that will actually execute after sparse fields inherit defaults."""
    ordinary = _visible_plan(default_plan)
    proposed = _visible_plan(proposed_plan)
    # Only executable fields inherit. Explanations, projections, and claimed
    # benefits must come from this offer; inheriting them would manufacture
    # communication quality that the offer never supplied.
    effective: dict[str, Any] = {}
    if ordinary.get("setpoint_c") is not None:
        effective["setpoint_c"] = ordinary["setpoint_c"]
    if isinstance(ordinary.get("appliance_actions"), Mapping):
        effective["appliance_actions"] = deepcopy(dict(ordinary["appliance_actions"]))
    for key, value in proposed.items():
        if key == "appliance_actions" and isinstance(value, Mapping):
            inherited_actions = (
                dict(effective.get("appliance_actions") or {})
                if isinstance(effective.get("appliance_actions"), Mapping)
                else {}
            )
            inherited_actions.update(dict(value))
            effective[key] = inherited_actions
        else:
            effective[key] = value
    if ordinary:
        effective["plan_field_semantics"] = (
            "Only omitted executable setpoint/appliance fields inherit the ordinary household plan. The actions "
            "shown here are the merged actions that would execute; explanation and benefit claims never inherit."
        )
    return effective


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

    return 0.5, {
        "source": "neutral_uninformed_consent_prior",
        "formula": "0.5",
        "note": (
            "No explicit consent prior was supplied. Override propensity and satisfaction weights remain household "
            "evidence for the role-play model; they are not relabelled as acceptance probabilities."
        ),
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
    verified_plan_facts: Mapping[str, Any] | None = None,
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
    resume = sanitize_household_resume_for_roleplay(resume)
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
        "effective_plan_if_accepted": _effective_visible_offer(proposed_plan, default_plan),
        "verified_offer_facts": _visible_verified_plan_facts(verified_plan_facts),
        "live_household_statement": _sanitize_identity_text(user_preference_text, 1200),
        "persona_baseline_acceptance_probability": baseline,
        "persona_baseline_audit": baseline_audit,
        "hard_safety_veto": {
            "active": bool(veto_reasons),
            "reasons": veto_reasons,
        },
    }
    system_prompt = (
        "Speak as the household in the supplied resume and decide on this offer as written. Use its biography, routines, "
        "relationships, previous experiences, live statement, and today's facts as a person would; do not reduce it to a tag or "
        "weight. Never infer or judge which controller, algorithm, provider, or model made the offer. "
        "Start from the supplied consent prior and compare offered_vpp_plan with ordinary_household_plan. "
        "effective_plan_if_accepted shows what would actually run. Its inherited ordinary actions are context, not new benefits. "
        "Give positive weight only to an offer-specific change or explanation that genuinely helps this household or resolves a "
        "concern. Merely preserving ordinary comfort, safety, or service earns no extra credit. A useful explanation connects an exact "
        "change to this family's routine or constraint and a concrete benefit or tradeoff. When that link is specific, truthful, and "
        "gives the household meaningful control, let the explanation materially raise willingness instead of treating it as cosmetic; "
        "weigh its communication value separately from any correctable physical drawback. Generic claims, unanswered requests for "
        "benefit information, missing actions, unresolved 'if/only if/as long as' conditions, or claims contradicted by checked facts "
        "should lower willingness. A proposal without a household-specific reason is an incomplete request for consent, even when the "
        "household is generally cooperative. Read conditional statements literally: willingness to consider an offer if a condition "
        "is met is not positive evidence while that condition is still unmet. An acceptable thermostat, an empty conflict list, and "
        "unchanged ordinary chores are context_only unless this offer improves them or answers an earlier concern. Check times and "
        "claims against the event, both plans, and verified_offer_facts; an action "
        "at the exclusive event end is outside the event. Do not invent savings, guarantees, outcomes, failures, or personal details. "
        "The final probability is how often this household would accept the unchanged offer in 100 comparable situations. The prior "
        "is a starting point, not a floor. Use short signed adjustments only for facts that really change willingness—usually 2-4, "
        "or an empty list when nothing does. Baseline plus adjustments must equal the final probability; do not apply hidden clipping "
        "or canned deltas. Keep decision, probability, first-person reason, and feedback consistent. A counteroffer names the concrete "
        "change required and credits it only in the counterfactual. Cite 2-4 concise, non-duplicate evidence items by E1, E2, and so "
        "on. A hard safety veto cannot be waived. Return valid JSON only."
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
    normalized_ids = [str(item["id"]).strip().lower() for item in result]
    if len(normalized_ids) != len(set(normalized_ids)):
        raise RoleplayResponseError("evidence ids must be unique")
    return result


def _normalize_adjustments(raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)):
        raise RoleplayResponseError("adjustments must be a list")
    if not raw:
        return []
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
        raise RoleplayResponseError(
            "non-empty adjustments must contain at least one signed item"
        )
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
    evidence_by_id = {
        str(item.get("id") or "").strip().lower(): item
        for item in evidence
        if str(item.get("id") or "").strip()
    }
    for adjustment in adjustments:
        evidence_ref = str(adjustment.get("evidence") or "").strip().lower()
        cited = evidence_by_id.get(evidence_ref)
        if cited is None:
            raise RoleplayResponseError(
                f"adjustment evidence reference {adjustment.get('evidence')!r} was not defined"
            )
        effect = str(cited.get("effect") or "")
        delta = float(adjustment["delta"])
        if delta > 0.0 and effect != "supports_acceptance":
            raise RoleplayResponseError(
                "positive adjustments must cite evidence marked supports_acceptance"
            )
        if delta < 0.0 and effect not in {"supports_rejection", "requires_change"}:
            raise RoleplayResponseError(
                "negative adjustments must cite evidence marked supports_rejection or requires_change"
            )
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
