"""Method-blind, portfolio planning primitives for the EnergyBridge harness.

The module is intentionally independent from the simulator and from any LLM
client.  It gives the caller a small set of pure functions for:

* presenting observable state, profile, memory, event, and anonymous advisor
  candidates to a base model;
* accepting either a V3 candidate portfolio or the previous single-plan JSON;
* validating every candidate against one common executable contract;
* recording every validator edit as an auditable JSON Patch operation; and
* comparing feasible candidates without collapsing different objectives into a
  method-specific scalar score.

Advisor plans are evidence, not fallbacks.  In particular, an invalid or
missing model selection produces ``replan_required``; an advisor candidate is
never silently substituted for the model's plan.

All public functions return JSON-serializable values and do not mutate their
inputs.  No function knows the desired acceptance rate or assigns a bonus based
on planner, model, or method identity.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from copy import deepcopy
from typing import Any, Mapping, Sequence

from .decision_evidence_v3 import build_decision_evidence_ledger


PLANNING_SCHEMA_VERSION = "energybridge.open_portfolio_planning.v3"
PLAN_LIFECYCLE_VERSION = "energybridge.plan_lifecycle.v3"

_PRIVATE_KEY_PARTS = (
    "hidden",
    "latent_persona",
    "roleplay",
    "role_card",
    "ground_truth",
    "system_prompt",
    "developer_prompt",
    "scoring_weight",
    "acceptance_probability",
    "target_acceptance",
    "evaluator",
    "api_key",
    "access_token",
    "authorization",
    "credential",
    "secret",
    "password",
    "auth_header",
    "bearer_token",
    "client_secret",
    "private_key",
    "developer_message",
    "developer_instruction",
)
_PRIVATE_EXACT_KEYS = {
    "persona",
    "persona_config",
    "vpp_override_prob",
    "override_probability",
    "evaluation_score",
    "target_score",
    "api_key",
    "access_token",
    "authorization",
    "credentials",
    "secret",
    "password",
}
_ADVISOR_IDENTITY_KEY_PARTS = (
    "method",
    "model",
    "provider",
    "vendor",
    "algorithm",
    "controller",
    "skill",
    "source",
)
_PLANNING_IDENTITY_KEYS = {
    "method",
    "method_name",
    "model",
    "model_name",
    "model_id",
    "provider",
    "vendor",
    "algorithm",
    "controller",
    "controller_name",
    "control_source",
    "objective_source",
    "selected_skill",
    "skill_selection",
    "api_base",
    "base_url",
    "endpoint_url",
    "llm_host",
}
_PLANNING_IDENTITY_KEY_TOKENS = {
    "method",
    "model",
    "provider",
    "vendor",
    "planner",
    "algorithm",
    "controller",
    "deployment",
    "backend",
    "endpoint",
    "host",
    "llm",
}
_PRIVATE_KEY_TOKENS = {
    "auth",
    "authorization",
    "bearer",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
    "developer",
}
_ADVISOR_IDENTITY_RE = re.compile(
    r"\b(?:synthetic[_-]*energybridge[-_.a-z0-9]*|EnergyBridge|OpenAI|Anthropic|xAI|DMXAPI|"
    r"MPC[-_.a-z0-9]*|HEMA[-_.a-z0-9]*|HAMA[-_.a-z0-9]*|rule[_+ -]?MILP|MILP|"
    r"RL[-_.a-z0-9]*|PPO[-_.a-z0-9]*|"
    r"(?:GPT|ChatGPT|Claude|Gemini|Llama|Qwen|DeepSeek|Mistral|Grok|o[134])[-_.a-z0-9]*)\b",
    flags=re.IGNORECASE,
)
_PEM_BLOCK_RE = re.compile(
    r"-----BEGIN\s+[A-Z0-9 _-]{1,80}-----.*?(?:-----END\s+[A-Z0-9 _-]{1,80}-----|$)",
    re.IGNORECASE | re.DOTALL,
)
_AUTH_HEADER_RE = re.compile(
    r"\b(?:authorization\s*:\s*)?(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{4,}",
    re.IGNORECASE,
)
_SK_CREDENTIAL_RE = re.compile(r"\bsk-[A-Za-z0-9_-]{6,}\b", re.IGNORECASE)
_SENSITIVE_ASSIGNMENT_RE = re.compile(
    r"\b(?:[A-Za-z0-9_.-]*(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|"
    r"auth(?:orization)?|credential|password|passwd|private[_ -]?key|secret|"
    r"endpoint|base[_ -]?url)[A-Za-z0-9_.-]*)\s*(?::|=|\bis\b)\s*"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;]+)",
    re.IGNORECASE,
)
_CREDENTIAL_PHRASE_RE = re.compile(
    r"\b(?:api[_ -]?key|access[_ -]?key|secret[_ -]?key|private[_ -]?key|"
    r"credential|password|passwd|secret)\b"
    r"(?:\s*(?::|=|\bis\b)\s*|\s+)"
    r"(?:\"[^\"]*\"|'[^']*'|[A-Za-z0-9._~+/=-]{4,})",
    re.IGNORECASE,
)
_TOKEN_VALUE_RE = re.compile(
    r"\b(?:access\s+token|refresh\s+token|token)\b"
    r"(?:\s*(?::|=|\bis\b)\s*|\s+)"
    r"[A-Za-z0-9._~+/=-]{4,}",
    re.IGNORECASE,
)
_GENERIC_KEY_VALUE_RE = re.compile(
    r"(?i:\bkey\b)(?:\s*(?::|=|\bis\b)\s*|\s+)"
    r"(?:\"[^\"]{4,}\"|'[^']{4,}'|[A-Za-z0-9._~+/=-]{16,}|"
    r"(?=[A-Za-z0-9._~+/=-]{6,})(?=[A-Za-z0-9._~+/=-]*[0-9._~+/=-])"
    r"[A-Za-z0-9._~+/=-]{6,}|[A-Z]{6,})"
)
_URL_ENDPOINT_RE = re.compile(
    r"\b(?:https?|wss?)://[^\s<>\"'\[\]{}()]+",
    re.IGNORECASE,
)
_BARE_HOST_ENDPOINT_RE = re.compile(
    # Dotted free text is ambiguous with a private DNS name.  Evidence
    # provenance therefore uses unambiguous JSON Pointers (``/memory/...``),
    # while every syntactically plausible bare DNS suffix is private.
    r"(?<![@\w.])(?:localhost|(?:[a-z0-9](?:[a-z0-9-]{0,62})\.)+[a-z]{2,63})"
    r"(?![a-z])(?::\d{2,5})?"
    r"(?:/[^\s<>\"'\[\]{}()]*)?",
    re.IGNORECASE,
)
_LABELLED_DOTTED_HOST_ENDPOINT_RE = re.compile(
    r"\b(?:endpoint|host|server|api[_ -]?base|base[_ -]?url)\s+"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,62})\.)+[a-z0-9-]{2,63}"
    r"(?::\d{2,5})?(?:/[^\s<>\"'\[\]{}()]*)?",
    re.IGNORECASE,
)
_SINGLE_LABEL_HOST_ENDPOINT_RE = re.compile(
    # A hostname may follow punctuation, but never the colon in a clock time.
    # Requiring a non-zero port also prevents ``15:00-18:00`` from being
    # misread as host ``00-18`` on port ``00``.
    r"(?<![@\w:])(?=[a-z0-9-]{2,64}:)(?=[a-z0-9-]*[a-z-])"
    r"(?:[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?):[1-9]\d{1,4}"
    r"(?:/[^\s<>\"'\[\]{}()]*)?",
    re.IGNORECASE,
)
_IP_ENDPOINT_RE = re.compile(
    r"\b(?:\d{1,3}\.){3}\d{1,3}(?::\d{2,5})?(?:/[^\s<>\"'\[\]{}()]*)?"
)
_IPV6_ENDPOINT_RE = re.compile(
    r"(?<![\w:])(?:\[[0-9a-f:]{2,}\]|(?:[0-9a-f]{1,4}:){2,}[0-9a-f:]*)"
    r"(?::\d{2,5})?(?:/[^\s<>\"'\[\]{}()]*)?",
    re.IGNORECASE,
)
_UNKNOWN_TECH_IDENTITY_RE = re.compile(
    r"\b[A-Z][A-Za-z0-9]*(?:[-_]?(?:Cloud|Solver|Planner|Optimizer|Controller|"
    r"Provider|Model|Agent|Algorithm|Backend|Deployment|LLM|API))[A-Za-z0-9_.-]*\b"
)
_LABELLED_TECH_IDENTITY_RE = re.compile(
    r"(?<!utility\s)(?<!energy\s)(?<!service\s)"
    r"\b(?:method|provider|model|planner|solver|optimizer|controller|algorithm|vendor|"
    r"backend|deployment|agent|llm)(?:\s+(?:name|id))?"
    r"\s*(?:(?::|=|\bis\b)\s*|\s+)"
    r"(?:\"[^\"]{1,120}\"|'[^']{1,120}'|[^\s,;]+)",
    re.IGNORECASE,
)
_PREFIXED_TECH_ROLE_RE = re.compile(
    r"\b(?!Utility\s+)(?:[A-Z][A-Za-z0-9_.-]{2,})\s+"
    r"(?:Cloud|Solver|Planner|Optimizer|Controller|Provider|Model|Agent|Algorithm|Backend|LLM|API)"
    r"(?:\s+V?\d+)?\b"
)
_PRODUCED_BY_RE = re.compile(
    r"\b(?:generated|produced|authored|selected|computed|planned)\s+by\s+"
    r"(?:\"[^\"]{1,120}\"|'[^']{1,120}'|[A-Za-z0-9_.-]{2,120})",
    re.IGNORECASE,
)
_SENSITIVE_TEXT_REPLACEMENT = "[sensitive value removed]"
_PRIVATE_ENDPOINT_REPLACEMENT = "[private endpoint]"
_TECH_IDENTITY_REPLACEMENT = "planning advisor"
_CANDIDATE_METADATA_KEYS = {
    "candidate_id",
    "id",
    "name",
    "label",
    "plan",
    "objective_estimates",
    "objectives",
    "uncertainty",
    "uncertainties",
    "counterfactuals",
    "evidence_citations",
    "memory_citations",
    "comparison",
    "tradeoff",
    "strategy_explanation",
    "explanation",
    "information_requests",
    "clarification_requests",
    "clarification_request",
}
_EXECUTABLE_KEYS = {
    "setpoint",
    "setpoint_c",
    "appliances",
    "appliance_actions",
    "actions",
    "commands",
}


def _normal_key(value: object) -> str:
    text = str(value).strip()
    text = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", text)
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", text)
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def _is_private_key(key: object) -> bool:
    raw_key = str(key)
    key_text_for_secret_scan = raw_key.replace("_", " ")
    if any(pattern.search(key_text_for_secret_scan) for pattern in (
        _PEM_BLOCK_RE,
        _AUTH_HEADER_RE,
        _SK_CREDENTIAL_RE,
        _SENSITIVE_ASSIGNMENT_RE,
        _CREDENTIAL_PHRASE_RE,
        _TOKEN_VALUE_RE,
        _GENERIC_KEY_VALUE_RE,
        _URL_ENDPOINT_RE,
        _LABELLED_DOTTED_HOST_ENDPOINT_RE,
        _BARE_HOST_ENDPOINT_RE,
        _SINGLE_LABEL_HOST_ENDPOINT_RE,
        _IP_ENDPOINT_RE,
        _IPV6_ENDPOINT_RE,
    )):
        return True
    normalized = _normal_key(key)
    tokens = set(normalized.split("_"))
    return (
        normalized in _PRIVATE_EXACT_KEYS
        or bool(tokens & _PRIVATE_KEY_TOKENS)
        or any(part in normalized for part in _PRIVATE_KEY_PARTS)
    )


def _is_advisor_identity_key(key: object) -> bool:
    normalized = _normal_key(key)
    return any(part in normalized for part in _ADVISOR_IDENTITY_KEY_PARTS)


def _is_planning_identity_key(key: object) -> bool:
    normalized = _normal_key(key)
    return (
        normalized in _PLANNING_IDENTITY_KEYS
        or bool(set(normalized.split("_")) & _PLANNING_IDENTITY_KEY_TOKENS)
    )


def _sanitize_model_visible_text(value: object, *, identity_blind: bool = True) -> str:
    """Redact credentials, endpoints, and technical planner identities.

    This is intentionally a content boundary rather than a key-name filter:
    free-form notes can otherwise smuggle the same private values into a prompt
    or persisted audit.  Ordinary household wording such as ``utility portal``
    is left intact.
    """
    text = str(value)
    for pattern in (
        _PEM_BLOCK_RE,
        _AUTH_HEADER_RE,
        _SK_CREDENTIAL_RE,
        _SENSITIVE_ASSIGNMENT_RE,
        _CREDENTIAL_PHRASE_RE,
        _TOKEN_VALUE_RE,
        _GENERIC_KEY_VALUE_RE,
    ):
        text = pattern.sub(_SENSITIVE_TEXT_REPLACEMENT, text)
    for pattern in (
        _URL_ENDPOINT_RE,
        _LABELLED_DOTTED_HOST_ENDPOINT_RE,
        _IPV6_ENDPOINT_RE,
        _IP_ENDPOINT_RE,
        _BARE_HOST_ENDPOINT_RE,
        _SINGLE_LABEL_HOST_ENDPOINT_RE,
    ):
        text = pattern.sub(_PRIVATE_ENDPOINT_REPLACEMENT, text)
    if identity_blind:
        for pattern in (
            _LABELLED_TECH_IDENTITY_RE,
            _PRODUCED_BY_RE,
            _PREFIXED_TECH_ROLE_RE,
            _UNKNOWN_TECH_IDENTITY_RE,
            _ADVISOR_IDENTITY_RE,
        ):
            text = pattern.sub(_TECH_IDENTITY_REPLACEMENT, text)
        text = re.sub(
            r"\b(?:planning advisor)(?:\s+planning advisor)+\b",
            _TECH_IDENTITY_REPLACEMENT,
            text,
            flags=re.IGNORECASE,
        )
    return text


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    """Return a bounded detached JSON value, omitting private evaluator data."""
    if depth > 10:
        return "[depth limit]"
    if value is None or isinstance(value, (bool, int, str)):
        return value if not isinstance(value, str) else _sanitize_model_visible_text(
            value, identity_blind=False
        )[:8000]
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item, depth=depth + 1)
            for key, item in value.items()
            if not _is_private_key(key)
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_json_safe(item, depth=depth + 1) for item in list(value)[:200]]
    return _sanitize_model_visible_text(value)[:1000]


def _sanitize_advisor_value(value: Any, *, depth: int = 0) -> Any:
    """Remove planner identity while retaining actionable candidate evidence."""
    if depth > 10:
        return "[depth limit]"
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_advisor_value(item, depth=depth + 1)
            for key, item in value.items()
            if not _is_private_key(key) and not _is_advisor_identity_key(key)
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_advisor_value(item, depth=depth + 1) for item in list(value)[:100]]
    if isinstance(value, str):
        return _sanitize_model_visible_text(value).replace(
            "planning advisor", "external advisor"
        )[:4000]
    return _json_safe(value, depth=depth)


def _sanitize_planning_input(value: Any, *, depth: int = 0) -> Any:
    """Apply the method/privacy boundary to every model-visible input."""
    if depth > 10:
        return "[depth limit]"
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_planning_input(item, depth=depth + 1)
            for key, item in value.items()
            if not _is_private_key(key) and not _is_planning_identity_key(key)
        }
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_sanitize_planning_input(item, depth=depth + 1) for item in list(value)[:200]]
    if isinstance(value, str):
        return _sanitize_model_visible_text(value)[:8000]
    return _json_safe(value, depth=depth)


def _canonical(value: Any) -> str:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _as_sequence(value: Any) -> list[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return list(value)
    return []


def anonymize_advisor_candidates(
    advisor_candidates: Sequence[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Return anonymous prompt candidates plus a non-identifying audit map.

    The map intentionally records only index and content fingerprint.  Upstream
    method/model names are not needed to reproduce what the planner saw.
    """
    presented: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for index, raw in enumerate(list(advisor_candidates or [])[:24], start=1):
        if not isinstance(raw, Mapping):
            continue
        advisor_ref = f"advisor_{index:02d}"
        safe = _sanitize_advisor_value(raw)
        if not isinstance(safe, Mapping):
            continue
        presented.append({"advisor_ref": advisor_ref, "candidate": safe})
        provenance.append(
            {
                "advisor_ref": advisor_ref,
                "input_index": index - 1,
                "presented_fingerprint": _fingerprint(safe),
            }
        )
    return {"presented_candidates": presented, "provenance": provenance}


def _constraint_sources(
    observable_state: Mapping[str, Any] | None,
    observable_profile: Mapping[str, Any] | None,
    event: Mapping[str, Any] | None,
    explicit_constraints: Sequence[Mapping[str, Any]] | None,
) -> list[tuple[str, Any]]:
    sources: list[tuple[str, Any]] = []
    for label, value in (
        ("observable_state.planning_constraints", (observable_state or {}).get("planning_constraints")),
        ("observable_state.hard_constraints", (observable_state or {}).get("hard_constraints")),
        ("observable_profile.explicit_constraints", (observable_profile or {}).get("explicit_constraints")),
        ("observable_profile.hard_constraints", (observable_profile or {}).get("hard_constraints")),
        ("event.hard_constraints", (event or {}).get("hard_constraints")),
        ("caller.explicit_constraints", explicit_constraints),
    ):
        for item in _as_sequence(value):
            sources.append((label, item))
    return sources


def _pointer(path: Any) -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    raw_parts = text[1:].split("/") if text.startswith("/") else text.split(".")
    parts = [part.replace("~1", "/").replace("~0", "~") for part in raw_parts]
    if not parts or any(
        not re.fullmatch(r"(?:[A-Za-z_][A-Za-z0-9_-]*|\d+)", part)
        or part.lower() == "private"
        or _is_private_key(part)
        or _is_planning_identity_key(part)
        for part in parts
    ):
        return ""
    # Match executable-plan canonicalization exactly so constraints cannot
    # target a pre-move alias that disappears before validation.
    if parts[0] == "appliance_actions":
        parts[0] = "appliances"
    elif parts[0] == "setpoint_c":
        parts[0] = "setpoint"
    return "/" + "/".join(
        part.replace("~", "~0").replace("/", "~1") for part in parts
    )


def derive_planning_constraints(
    *,
    observable_state: Mapping[str, Any] | None = None,
    observable_profile: Mapping[str, Any] | None = None,
    event: Mapping[str, Any] | None = None,
    explicit_constraints: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Compile only explicit, machine-readable constraints.

    Preferences and uncertain memories are deliberately *not* promoted to hard
    constraints.  Physical ``control_limits`` are the sole inferred constraints
    and their repairs remain visible in the JSON Patch ledger.
    """
    compiled: list[dict[str, Any]] = []
    limits = (observable_state or {}).get("control_limits")
    if not isinstance(limits, Mapping):
        limits = (observable_state or {}).get("action_space")
    if isinstance(limits, Mapping):
        setpoint = limits.get("setpoint") or limits.get("setpoint_c")
        if isinstance(setpoint, Mapping) and (
            setpoint.get("min") is not None or setpoint.get("max") is not None
        ):
            item = {
                "constraint_id": "physical_setpoint_range",
                "kind": "range",
                "path": "/setpoint",
                "severity": "hard",
                "repair": "clamp",
                "evidence_paths": ["/observable_state/control_limits"],
            }
            if setpoint.get("min") is not None:
                item["min"] = _json_safe(setpoint.get("min"))
            if setpoint.get("max") is not None:
                item["max"] = _json_safe(setpoint.get("max"))
            compiled.append(item)

    for source, raw in _constraint_sources(
        observable_state, observable_profile, event, explicit_constraints
    ):
        if not isinstance(raw, Mapping):
            continue
        kind = _normal_key(raw.get("kind") or raw.get("type"))
        aliases = {
            "number_range": "range",
            "numeric_range": "range",
            "one_of": "enum",
            "allowed_values": "enum",
            "equal": "equals",
            "immutable": "equals",
            "disjoint": "disjoint_interval",
            "interval_disjoint": "disjoint_interval",
            "within": "within_interval",
            "interval_within": "within_interval",
        }
        kind = aliases.get(kind, kind)
        if kind not in {
            "required",
            "range",
            "enum",
            "equals",
            "disjoint_interval",
            "disjoint_interval_duration",
            "within_interval",
        }:
            continue
        # Constraint metadata is model-visible and later persisted in the
        # validation ledger. Apply the same privacy/identity boundary as every
        # other planning input rather than treating arbitrary metadata as part
        # of the executable constraint schema.
        # Structural paths are not prose. Canonicalize and validate them before
        # the free-text DNS sanitizer runs, then restore only the safe JSON
        # Pointer form. This keeps legacy dotted constraint paths executable
        # without creating a general dotted-text privacy exception.
        trusted_paths = {
            key: _pointer(raw.get(key))
            for key in ("path", "start_path", "end_path")
            if raw.get(key) is not None
        }
        trusted_evidence_paths = [
            pointer
            for pointer in (_pointer(value) for value in _as_sequence(raw.get("evidence_paths")))
            if pointer
        ]
        item = _sanitize_planning_input(raw)
        assert isinstance(item, dict)
        item["kind"] = kind
        for key in ("path", "start_path", "end_path"):
            pointer = trusted_paths.get(key)
            if pointer:
                item[key] = pointer
            else:
                item.pop(key, None)
        item.setdefault("severity", "hard")
        item.setdefault("constraint_id", f"constraint_{len(compiled) + 1:02d}")
        evidence = trusted_evidence_paths
        source_pointer = _pointer(source)
        if source_pointer not in evidence:
            evidence.append(source_pointer)
        item["evidence_paths"] = evidence
        compiled.append(item)

    # Deterministic de-duplication makes repeated caller/profile constraints
    # harmless without changing their semantics.
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in compiled:
        signature = _canonical(item)
        if signature not in seen:
            seen.add(signature)
            unique.append(item)
    return unique


def build_planning_prompts(
    *,
    observable_state: Mapping[str, Any] | None,
    observable_profile: Mapping[str, Any] | None,
    memory: Mapping[str, Any] | None,
    event: Mapping[str, Any] | None,
    advisor_candidates: Sequence[Mapping[str, Any]] | None = None,
    explicit_constraints: Sequence[Mapping[str, Any]] | None = None,
) -> tuple[str, str]:
    """Build the open portfolio-planning system and user prompts.

    The protocol specifies an auditable envelope, not a fixed planning recipe:
    candidate count, action composition, tradeoffs, and final choice remain base
    model judgments.
    """
    anonymous = anonymize_advisor_candidates(advisor_candidates)
    constraints = derive_planning_constraints(
        observable_state=observable_state,
        observable_profile=observable_profile,
        event=event,
        explicit_constraints=explicit_constraints,
    )
    payload = {
        "schema_version": PLANNING_SCHEMA_VERSION,
        "observable_state": _sanitize_planning_input(observable_state or {}),
        "observable_profile": _sanitize_planning_input(observable_profile or {}),
        "relevant_memory": _sanitize_planning_input(memory or {}),
        "event": _sanitize_planning_input(event or {}),
        "decision_evidence_ledger": build_decision_evidence_ledger(
            observable_profile=observable_profile,
            memory=memory,
            event=event,
        ),
        "hard_constraints": _sanitize_planning_input(constraints),
        "anonymous_advisor_candidates": anonymous["presented_candidates"],
    }
    system_prompt = """You are the household's planning reasoner. Work only from the observable evidence in the payload.

Produce one or more executable candidates and choose how many are useful. A single strong candidate is valid when the evidence does not support a consequential alternative; add alternatives only when they expose a real tradeoff or uncertainty. Do not fill a canned strategy grid or force differences that have no evidential basis. Keep the JSON compact by citing facts instead of repeating them. Treat profile and memory beliefs according to their confidence and provenance. Use the decision evidence ledger to distinguish a direct current household statement from inferred profile beliefs and older observations. A current statement governs the same topic for this event, but its conditions still have to be verified; it is not blanket permission. Hard constraints are invariants; preferences and objectives remain tradeoffs unless explicitly marked hard.

Anonymous advisor candidates are optional evidence. You may adapt, combine, or reject them. They have no authority and may not replace your own final judgment. Do not infer their author, method, model, or vendor.

For each candidate, keep the executable control in `plan`. Add evidence citations as JSON Pointers into the supplied payload (for example `/observable_state/device_capabilities/washer/earliest_h`), objective estimates with units/direction/confidence when supportable, important uncertainty, and counterfactual conditions that could change the choice. Use null or omit an estimate when evidence is insufficient; do not invent precision. Professional calibration memory reports prior forecast/result agreement; use it to calibrate confidence, never as a reward, ranking, policy, or action recommendation. Compare objectives directly instead of hiding tradeoffs in one unexplained score. Do not predict or optimize an acceptance probability.

The observable profile may contain `decision_unknowns`. They are possible information gaps, not mandatory questions or implicit preferences. When an unanswered household fact could materially reverse the choice, you may include natural `information_requests` describing what to ask, why the answer matters, and which supplied gap or evidence it concerns. Phrase questions neutrally: do not presuppose an energy, comfort, cost, or service effect that the supplied evidence does not establish. Decide whether any request is worthwhile; do not ask for facts that cannot change the plan, do not use questions to postpone an otherwise safe reversible decision, and do not turn this into a checklist. Still select an executable plan for the evidence currently available.

Return JSON only with `candidate_plans` (a list), `selected_candidate_id`, a concise `selection_reason`, and optional `information_requests`. Candidate IDs only need to be unique. The plan itself is open-ended: use the executable fields supported by the payload rather than a fixed action template. The selected ID must name one of your candidate plans, not an anonymous advisor reference."""
    user_prompt = (
        "Develop an executable portfolio, compare its real tradeoffs, and select the plan you judge best "
        "for this observed household and event.\n\n[PLANNING PAYLOAD]\n"
        + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    )
    return system_prompt, user_prompt


def _strip_json_fence(text: str) -> str:
    stripped = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1).strip() if match else stripped


def _response_mapping(raw: Any) -> tuple[dict[str, Any] | None, str | None]:
    if isinstance(raw, Mapping):
        return deepcopy(dict(raw)), None
    if isinstance(raw, str):
        try:
            parsed = json.loads(_strip_json_fence(raw))
        except json.JSONDecodeError as exc:
            return None, f"invalid_json:{exc.msg}"
        if isinstance(parsed, Mapping):
            return dict(parsed), None
        if isinstance(parsed, Sequence) and not isinstance(parsed, (str, bytes, bytearray)):
            return {"candidate_plans": list(parsed)}, None
        return None, "response_must_be_object_or_candidate_list"
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)):
        return {"candidate_plans": deepcopy(list(raw))}, None
    return None, "response_must_be_json_object_string_or_candidate_list"


def _looks_executable(plan: Mapping[str, Any]) -> bool:
    if plan.get("setpoint") is not None or plan.get("setpoint_c") is not None:
        return True
    for key in ("appliances", "appliance_actions"):
        value = plan.get(key)
        if isinstance(value, Mapping) and bool(value):
            return True
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and bool(value):
            return True
    return False


def normalize_information_requests(raw: Any) -> list[dict[str, Any]]:
    """Normalize optional model-owned questions without prescribing their count.

    This is an audit projection, not a question-ranking policy.  A request may
    be a natural string or a structured object; unsupported metadata is simply
    omitted from the model-visible/persisted boundary.
    """
    if isinstance(raw, Mapping) and any(
        key in raw for key in ("information_requests", "clarification_requests", "clarification_request")
    ):
        raw = raw.get(
            "information_requests",
            raw.get("clarification_requests", raw.get("clarification_request")),
        )
    if raw is None:
        return []
    supplied = list(raw) if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes, bytearray)) else [raw]
    result: list[dict[str, Any]] = []
    for index, value in enumerate(supplied[:16], start=1):
        if isinstance(value, str):
            question = _sanitize_model_visible_text(value).strip()[:600]
            item: Mapping[str, Any] = {}
        elif isinstance(value, Mapping):
            safe = _sanitize_planning_input(value)
            if not isinstance(safe, Mapping):
                continue
            item = safe
            question = _sanitize_model_visible_text(
                item.get("question", item.get("ask", item.get("information_needed", "")))
            ).strip()[:600]
        else:
            continue
        if not question:
            continue
        citations = []
        raw_citations = item.get(
            "evidence_gap_citations",
            item.get("evidence_citations", item.get("evidence_gap", [])),
        )
        citation_values = (
            [raw_citations]
            if isinstance(raw_citations, str)
            else _as_sequence(raw_citations)
        )
        for citation in citation_values[:12]:
            text = _sanitize_model_visible_text(citation).strip()[:500]
            if text.startswith("/"):
                citations.append(text)
        result.append({
            "request_id": f"information_request_{index:02d}",
            "question": question,
            "decision_relevance": _sanitize_model_visible_text(
                item.get("decision_relevance", item.get("why_it_matters", item.get("impact", "")))
            ).strip()[:1000],
            "linked_question_id": _sanitize_model_visible_text(
                item.get("question_id", item.get("linked_question_id", ""))
            ).strip()[:160],
            "evidence_gap_citations": list(dict.fromkeys(citations)),
        })
    return result


def _information_acquisition_audit(
    requests: Sequence[Mapping[str, Any]],
    observable_profile: Mapping[str, Any] | None,
) -> dict[str, Any]:
    unknowns = (
        list((observable_profile or {}).get("decision_unknowns") or [])
        if isinstance(observable_profile, Mapping)
        else []
    )
    question_ids = {
        str(item.get("question_id"))
        for item in unknowns
        if isinstance(item, Mapping) and item.get("question_id")
    }
    reviewed: list[dict[str, Any]] = []
    for request in requests:
        citations = list(request.get("evidence_gap_citations") or [])
        linked = str(request.get("linked_question_id") or "")
        grounded = bool(
            (linked and linked in question_ids)
            or any(
                citation.startswith("/observable_profile/decision_unknowns/")
                for citation in citations
            )
        )
        reviewed.append({
            **deepcopy(dict(request)),
            "grounded_in_supplied_unknown": grounded,
            "decision_relevance_stated": bool(str(request.get("decision_relevance") or "").strip()),
        })
    return {
        "policy": "model_owned_nonblocking_information_value_audit",
        "available_unknown_count": len(unknowns),
        "requested_count": len(reviewed),
        "requests": reviewed,
        "questions_ranked_or_scored_by_harness": False,
        "plan_selection_changed_by_harness": False,
    }


def _candidate_plan(raw: Mapping[str, Any]) -> dict[str, Any]:
    nested = raw.get("plan")
    if isinstance(nested, Mapping):
        return deepcopy(dict(nested))
    return {
        str(key): deepcopy(value)
        for key, value in raw.items()
        if str(key) not in _CANDIDATE_METADATA_KEYS
    }


def parse_planning_response(raw: Any) -> dict[str, Any]:
    """Parse a V3 portfolio or a legacy single plan without validating it."""
    response, error = _response_mapping(raw)
    if response is None:
        return {
            "schema_version": PLANNING_SCHEMA_VERSION,
            "candidate_plans": [],
            "selected_candidate_id": None,
            "selection_reason": "",
            "legacy_single_plan": False,
            "parse_errors": [error or "unknown_parse_error"],
        }
    sanitized_response = _sanitize_planning_input(response)
    if not isinstance(sanitized_response, Mapping):
        return {
            "schema_version": PLANNING_SCHEMA_VERSION,
            "candidate_plans": [],
            "selected_candidate_id": None,
            "selection_reason": "",
            "legacy_single_plan": False,
            "parse_errors": ["response_failed_privacy_projection"],
        }
    response = dict(sanitized_response)

    supplied = response.get("candidate_plans")
    if supplied is None:
        supplied = response.get("candidates")
    legacy = supplied is None and (
        any(key in response for key in _EXECUTABLE_KEYS)
        or isinstance(response.get("plan"), Mapping)
    )
    if legacy:
        supplied_items: list[Any] = [response]
    elif isinstance(supplied, Mapping):
        supplied_items = [supplied]
    else:
        supplied_items = _as_sequence(supplied)

    candidates: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    id_aliases: dict[str, str] = {}
    errors: list[str] = []
    for index, item in enumerate(supplied_items[:32], start=1):
        if not isinstance(item, Mapping):
            errors.append(f"candidate_{index:02d}_must_be_object")
            continue
        raw_id = str(item.get("candidate_id") or item.get("id") or f"model_candidate_{index:02d}").strip()
        candidate_id = raw_id or f"model_candidate_{index:02d}"
        if candidate_id in used_ids:
            suffix = 2
            while f"{candidate_id}_{suffix}" in used_ids:
                suffix += 1
            candidate_id = f"{candidate_id}_{suffix}"
            errors.append(f"duplicate_candidate_id_renamed:{raw_id}->{candidate_id}")
        used_ids.add(candidate_id)
        id_aliases.setdefault(raw_id, candidate_id)
        candidate = {
            "candidate_id": candidate_id,
            "plan": _candidate_plan(item),
            "objective_estimates": _json_safe(
                item.get("objective_estimates", item.get("objectives", {}))
            ),
            "uncertainty": _json_safe(item.get("uncertainty", item.get("uncertainties", []))),
            "counterfactuals": _json_safe(item.get("counterfactuals", [])),
            "evidence_citations": _json_safe(
                item.get("evidence_citations", item.get("memory_citations", []))
            ),
            "comparison": _json_safe(item.get("comparison", item.get("tradeoff", ""))),
            "strategy_explanation": _json_safe(
                item.get("strategy_explanation", item.get("explanation"))
            ),
            "origin": "model",
        }
        candidates.append(candidate)

    requested = response.get("selected_candidate_id", response.get("selected_id"))
    selection_inference = None
    if requested is None and len(candidates) == 1:
        # Returning exactly one model-authored candidate is an unambiguous
        # implicit selection. This repairs only the envelope, never the plan or
        # the model's tradeoff judgment.
        requested = candidates[0]["candidate_id"]
        selection_inference = (
            "legacy_single_plan" if legacy else "single_candidate_unambiguous"
        )
    selected_id = id_aliases.get(str(requested), str(requested)) if requested is not None else None
    return {
        "schema_version": PLANNING_SCHEMA_VERSION,
        "candidate_plans": candidates,
        "selected_candidate_id": selected_id,
        "selection_reason": str(response.get("selection_reason", response.get("reason", "")))[:4000],
        "information_requests": normalize_information_requests(response),
        "legacy_single_plan": legacy,
        "selection_inference": selection_inference,
        "parse_errors": errors,
    }


_MISSING = object()


def _pointer_parts(path: str) -> list[str]:
    if path == "":
        return []
    if not path.startswith("/"):
        raise ValueError("JSON pointer must start with '/'")
    return [part.replace("~1", "/").replace("~0", "~") for part in path[1:].split("/")]


def _get_pointer(document: Any, path: str) -> Any:
    current = document
    try:
        for part in _pointer_parts(path):
            if isinstance(current, Mapping):
                if part not in current:
                    return _MISSING
                current = current[part]
            elif isinstance(current, list):
                current = current[int(part)]
            else:
                return _MISSING
    except (IndexError, KeyError, TypeError, ValueError):
        return _MISSING
    return current


def _set_pointer(document: dict[str, Any], path: str, value: Any) -> None:
    parts = _pointer_parts(path)
    if not parts:
        raise ValueError("cannot replace document root")
    current: Any = document
    for part in parts[:-1]:
        if not isinstance(current, dict):
            raise ValueError(f"non-object parent at {path}")
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    if not isinstance(current, dict):
        raise ValueError(f"non-object parent at {path}")
    current[parts[-1]] = deepcopy(value)


def _remove_pointer(document: dict[str, Any], path: str) -> None:
    parts = _pointer_parts(path)
    if not parts:
        raise ValueError("cannot remove document root")
    current: Any = document
    for part in parts[:-1]:
        if not isinstance(current, dict) or part not in current:
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _patch(
    *,
    op: str,
    path: str,
    value: Any = _MISSING,
    from_path: str | None = None,
    rule_id: str,
    reason: str,
    evidence_paths: Sequence[Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {"op": op, "path": path}
    if value is not _MISSING:
        item["value"] = _json_safe(value)
    if from_path is not None:
        item["from"] = from_path
    item["provenance"] = {
        "rule_id": rule_id,
        "reason": reason,
        "evidence_paths": [str(value) for value in list(evidence_paths or [])],
    }
    return item


def _canonicalize_plan(plan: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    validated = deepcopy(plan)
    patches: list[dict[str, Any]] = []
    warnings: list[str] = []
    aliases = (
        ("/setpoint_c", "/setpoint"),
        ("/appliance_actions", "/appliances"),
    )
    for source, target in aliases:
        source_value = _get_pointer(validated, source)
        target_value = _get_pointer(validated, target)
        if source_value is _MISSING:
            continue
        if target_value is _MISSING:
            _set_pointer(validated, target, source_value)
            _remove_pointer(validated, source)
            patches.append(
                _patch(
                    op="move",
                    from_path=source,
                    path=target,
                    rule_id="canonical_executable_alias",
                    reason="normalize an accepted alias to the runtime field name",
                )
            )
        elif _canonical(source_value) == _canonical(target_value):
            _remove_pointer(validated, source)
            patches.append(
                _patch(
                    op="remove",
                    path=source,
                    rule_id="remove_duplicate_alias",
                    reason=f"duplicate of {target}",
                )
            )
        else:
            warnings.append(f"conflicting_aliases:{source}:{target}")
    return validated, patches, warnings


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _violation(
    constraint: Mapping[str, Any],
    *,
    path: str,
    actual: Any,
    message: str,
    repaired: bool = False,
) -> dict[str, Any]:
    return {
        "constraint_id": str(constraint.get("constraint_id", "constraint")),
        "kind": str(constraint.get("kind", "unknown")),
        "path": path,
        "severity": str(constraint.get("severity", "hard")),
        "actual": None if actual is _MISSING else _json_safe(actual),
        "message": message,
        "repaired": bool(repaired),
        "evidence_paths": [str(value) for value in _as_sequence(constraint.get("evidence_paths"))],
    }


def _interval(value: Any) -> tuple[float, float] | None:
    if isinstance(value, Mapping):
        start = _finite_number(value.get("start", value.get("start_h")))
        end = _finite_number(value.get("end", value.get("end_h")))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and len(value) >= 2:
        start, end = _finite_number(value[0]), _finite_number(value[1])
    else:
        return None
    if start is None or end is None or end <= start:
        return None
    return start, end


def _validate_constraint(
    document: dict[str, Any],
    constraint: Mapping[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    violations: list[dict[str, Any]] = []
    patches: list[dict[str, Any]] = []
    kind = str(constraint.get("kind", ""))
    path = str(constraint.get("path") or "")
    value = _get_pointer(document, path) if path else _MISSING
    repair = str(constraint.get("repair") or "none").lower()
    evidence = _as_sequence(constraint.get("evidence_paths"))
    rule_id = str(constraint.get("constraint_id", "constraint"))

    if kind == "required":
        if value is _MISSING or value is None:
            default = constraint.get("default", _MISSING)
            if repair in {"default", "replace"} and default is not _MISSING and path:
                _set_pointer(document, path, default)
                patches.append(
                    _patch(
                        op="add" if value is _MISSING else "replace",
                        path=path,
                        value=default,
                        rule_id=rule_id,
                        reason="apply the explicitly declared required-field default",
                        evidence_paths=evidence,
                    )
                )
                violations.append(
                    _violation(constraint, path=path, actual=value, message="required value was missing", repaired=True)
                )
            else:
                violations.append(
                    _violation(constraint, path=path, actual=value, message="required value is missing")
                )
        return violations, patches

    if value is None and bool(constraint.get("nullable", False)):
        return violations, patches

    if value is _MISSING and kind not in {"disjoint_interval", "within_interval"}:
        # Optional paths are not errors.  Pair `required` with another rule when
        # the field must be present.
        return violations, patches

    if kind == "range":
        number = _finite_number(value)
        low = _finite_number(constraint.get("min"))
        high = _finite_number(constraint.get("max"))
        invalid = number is None or (low is not None and number < low) or (high is not None and number > high)
        if invalid:
            if number is not None and repair == "clamp" and path:
                replacement = max(low, number) if low is not None else number
                replacement = min(high, replacement) if high is not None else replacement
                if isinstance(value, int) and not isinstance(value, bool) and float(replacement).is_integer():
                    replacement = int(replacement)
                _set_pointer(document, path, replacement)
                patches.append(
                    _patch(
                        op="replace",
                        path=path,
                        value=replacement,
                        rule_id=rule_id,
                        reason="clamp to an explicit executable control limit",
                        evidence_paths=evidence,
                    )
                )
                violations.append(
                    _violation(constraint, path=path, actual=value, message="numeric value outside range", repaired=True)
                )
            else:
                violations.append(
                    _violation(constraint, path=path, actual=value, message="value is non-numeric or outside range")
                )
    elif kind == "enum":
        allowed = list(_as_sequence(constraint.get("values", constraint.get("allowed", []))))
        if value not in allowed:
            violations.append(
                _violation(constraint, path=path, actual=value, message="value is not in the allowed set")
            )
    elif kind == "equals":
        expected = constraint.get("value", constraint.get("expected", _MISSING))
        if expected is not _MISSING and _canonical(value) != _canonical(expected):
            if repair == "replace" and path:
                _set_pointer(document, path, expected)
                patches.append(
                    _patch(
                        op="replace",
                        path=path,
                        value=expected,
                        rule_id=rule_id,
                        reason="restore an explicitly immutable executable value",
                        evidence_paths=evidence,
                    )
                )
                violations.append(
                    _violation(constraint, path=path, actual=value, message="value differed from invariant", repaired=True)
                )
            else:
                violations.append(
                    _violation(constraint, path=path, actual=value, message="value differs from invariant")
                )
    elif kind == "disjoint_interval_duration":
        start = _finite_number(value)
        duration = _finite_number(constraint.get("duration_h"))
        reference = _interval(
            constraint.get("interval", constraint.get("window", constraint.get("forbidden_window")))
        )
        if start is None or duration is None or duration <= 0.0 or reference is None:
            violations.append(
                _violation(
                    constraint,
                    path=path,
                    actual={"start": start, "duration_h": duration},
                    message="start, positive duration, and forbidden interval must be finite",
                )
            )
        else:
            end = start + duration
            if max(start, reference[0]) < min(end, reference[1]):
                violations.append(
                    _violation(
                        constraint,
                        path=path,
                        actual={"start": start, "end": end, "duration_h": duration},
                        message="duration-derived half-open interval overlaps forbidden window",
                    )
                )
    elif kind in {"disjoint_interval", "within_interval"}:
        start_path = str(constraint.get("start_path") or "")
        end_path = str(constraint.get("end_path") or "")
        start = _finite_number(_get_pointer(document, start_path)) if start_path else None
        end = _finite_number(_get_pointer(document, end_path)) if end_path else None
        if start is None or end is None or end <= start:
            violations.append(
                _violation(
                    constraint,
                    path=f"{start_path},{end_path}",
                    actual={"start": start, "end": end},
                    message="interval endpoints must be finite with end greater than start",
                )
            )
        else:
            reference = _interval(
                constraint.get("interval", constraint.get("window", constraint.get("forbidden_window")))
            )
            if reference is None:
                violations.append(
                    _violation(
                        constraint,
                        path=f"{start_path},{end_path}",
                        actual={"start": start, "end": end},
                        message="constraint reference interval is invalid",
                    )
                )
            elif kind == "disjoint_interval" and max(start, reference[0]) < min(end, reference[1]):
                violations.append(
                    _violation(
                        constraint,
                        path=f"{start_path},{end_path}",
                        actual={"start": start, "end": end},
                        message="half-open intervals overlap",
                    )
                )
            elif kind == "within_interval" and not (start >= reference[0] and end <= reference[1]):
                violations.append(
                    _violation(
                        constraint,
                        path=f"{start_path},{end_path}",
                        actual={"start": start, "end": end},
                        message="interval is outside the allowed window",
                    )
                )
    return violations, patches


def _intrinsic_plan_issues(plan: Any) -> list[dict[str, Any]]:
    base = {
        "constraint_id": "executable_contract",
        "kind": "schema",
        "severity": "hard",
        "repaired": False,
        "evidence_paths": [],
    }
    if not isinstance(plan, Mapping):
        return [{**base, "path": "", "actual": _json_safe(plan), "message": "plan must be an object"}]
    issues: list[dict[str, Any]] = []
    for unsupported in ("actions", "commands"):
        if plan.get(unsupported):
            issues.append({
                **base,
                "constraint_id": "unsupported_executable_envelope",
                "path": f"/{unsupported}",
                "actual": _json_safe(plan.get(unsupported)),
                "message": (
                    f"{unsupported} is not an actuator-facing plan field; "
                    "use setpoint and/or appliances"
                ),
            })
    if not _looks_executable(plan):
        issues.append(
            {**base, "path": "", "actual": None, "message": "plan has no executable action field"}
        )
    if "setpoint" in plan and _finite_number(plan.get("setpoint")) is None:
        issues.append(
            {**base, "path": "/setpoint", "actual": _json_safe(plan.get("setpoint")), "message": "setpoint must be finite and numeric"}
        )
    appliances = plan.get("appliances")
    if appliances is not None and not isinstance(appliances, Mapping):
        issues.append(
            {**base, "path": "/appliances", "actual": _json_safe(appliances), "message": "appliances must be an object"}
        )
    stack: list[tuple[str, Any]] = [("", plan)]
    while stack:
        path, value = stack.pop()
        if isinstance(value, float) and not math.isfinite(value):
            issues.append(
                {**base, "path": path, "actual": None, "message": "executable values must be finite"}
            )
        elif isinstance(value, Mapping):
            for key, child in value.items():
                token = str(key).replace("~", "~0").replace("/", "~1")
                stack.append((f"{path}/{token}", child))
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
            for index, child in enumerate(value):
                stack.append((f"{path}/{index}", child))
    return issues


def validate_plan_candidate(
    candidate: Mapping[str, Any] | Any,
    *,
    observable_state: Mapping[str, Any] | None = None,
    observable_profile: Mapping[str, Any] | None = None,
    event: Mapping[str, Any] | None = None,
    explicit_constraints: Sequence[Mapping[str, Any]] | None = None,
    candidate_id: str | None = None,
    origin: str = "model",
) -> dict[str, Any]:
    """Create an immutable raw-to-validated lifecycle for one plan candidate."""
    wrapper = candidate if isinstance(candidate, Mapping) else {}
    if isinstance(wrapper.get("plan"), Mapping):
        raw_plan: Any = _sanitize_planning_input(wrapper["plan"])
    else:
        raw_plan = _sanitize_planning_input(candidate)
    safe_wrapper = _sanitize_planning_input(wrapper)
    if not isinstance(safe_wrapper, Mapping):
        safe_wrapper = {}
    raw_candidate_id = (
        candidate_id
        if candidate_id is not None
        else safe_wrapper.get("candidate_id", "candidate")
    )
    cid = _sanitize_model_visible_text(raw_candidate_id).strip()[:240] or "candidate"
    safe_origin = _sanitize_model_visible_text(origin).strip()[:120] or "unspecified"
    raw_snapshot = _json_safe(raw_plan)
    if not isinstance(raw_plan, Mapping):
        intrinsic = _intrinsic_plan_issues(raw_plan)
        return {
            "schema_version": PLAN_LIFECYCLE_VERSION,
            "candidate_id": cid,
            "origin": safe_origin,
            "raw_snapshot": raw_snapshot,
            "validated_snapshot": None,
            "json_patches": [],
            "violations": intrinsic,
            "warnings": [],
            "feasible": False,
            "status": "invalid",
            "objective_estimates": _json_safe(safe_wrapper.get("objective_estimates", {})),
            "uncertainty": _json_safe(safe_wrapper.get("uncertainty", [])),
            "counterfactuals": _json_safe(safe_wrapper.get("counterfactuals", [])),
            "evidence_citations": _json_safe(safe_wrapper.get("evidence_citations", [])),
            "strategy_explanation": _json_safe(
                safe_wrapper.get("strategy_explanation", safe_wrapper.get("explanation"))
            ),
        }

    validated, patches, warnings = _canonicalize_plan(dict(raw_plan))
    violations = _intrinsic_plan_issues(validated)
    for warning in warnings:
        if warning.startswith("conflicting_aliases:"):
            _, source, target = warning.split(":", 2)
            violations.append(
                {
                    "constraint_id": "canonical_executable_alias",
                    "kind": "schema",
                    "path": f"{source},{target}",
                    "severity": "hard",
                    "actual": {
                        source: _json_safe(_get_pointer(validated, source)),
                        target: _json_safe(_get_pointer(validated, target)),
                    },
                    "message": "conflicting executable aliases are ambiguous",
                    "repaired": False,
                    "evidence_paths": [],
                }
            )
    constraints = derive_planning_constraints(
        observable_state=observable_state,
        observable_profile=observable_profile,
        event=event,
        explicit_constraints=explicit_constraints,
    )
    for constraint in constraints:
        found, new_patches = _validate_constraint(validated, constraint)
        violations.extend(found)
        patches.extend(new_patches)

    hard_unrepaired = [
        item
        for item in violations
        if str(item.get("severity", "hard")).lower() == "hard" and not item.get("repaired")
    ]
    feasible = not hard_unrepaired
    status = "invalid" if not feasible else "repaired" if patches else "valid"
    return {
        "schema_version": PLAN_LIFECYCLE_VERSION,
        "candidate_id": cid,
        "origin": safe_origin,
        "raw_snapshot": raw_snapshot,
        "validated_snapshot": _json_safe(validated),
        "json_patches": patches,
        "constraints_checked": constraints,
        "violations": violations,
        "warnings": warnings,
        "feasible": feasible,
        "status": status,
        "objective_estimates": _json_safe(safe_wrapper.get("objective_estimates", safe_wrapper.get("objectives", {}))),
        "uncertainty": _json_safe(safe_wrapper.get("uncertainty", safe_wrapper.get("uncertainties", []))),
        "counterfactuals": _json_safe(safe_wrapper.get("counterfactuals", [])),
        "evidence_citations": _json_safe(safe_wrapper.get("evidence_citations", safe_wrapper.get("memory_citations", []))),
        "comparison": _json_safe(safe_wrapper.get("comparison", safe_wrapper.get("tradeoff", ""))),
        "strategy_explanation": _json_safe(
            safe_wrapper.get("strategy_explanation", safe_wrapper.get("explanation"))
        ),
    }


def _objective_item(value: Any, spec: Mapping[str, Any] | None = None) -> dict[str, Any] | None:
    spec = spec or {}
    if isinstance(value, Mapping):
        point = _finite_number(value.get("value", value.get("estimate")))
        lower = _finite_number(value.get("lower", value.get("low")))
        upper = _finite_number(value.get("upper", value.get("high")))
        direction = str(value.get("direction", spec.get("direction", ""))).lower()
        unit = value.get("unit", spec.get("unit"))
        confidence = _finite_number(value.get("confidence"))
    else:
        point = _finite_number(value)
        lower = upper = None
        direction = str(spec.get("direction", "")).lower()
        unit = spec.get("unit")
        confidence = None
    if point is None or direction not in {"min", "max", "minimize", "maximize"}:
        return None
    direction = "min" if direction.startswith("min") else "max"
    if lower is None:
        lower = point
    if upper is None:
        upper = point
    if lower > upper:
        lower, upper = upper, lower
    out = {
        "value": point,
        "lower": lower,
        "upper": upper,
        "direction": direction,
    }
    if unit is not None:
        out["unit"] = _json_safe(unit)
    if confidence is not None:
        out["confidence"] = max(0.0, min(1.0, confidence))
    return out


def _objective_specs(
    lifecycles: Sequence[Mapping[str, Any]],
    objective_specs: Mapping[str, Mapping[str, Any] | str] | None,
) -> dict[str, dict[str, Any]]:
    specs: dict[str, dict[str, Any]] = {}
    for name, raw in (objective_specs or {}).items():
        if isinstance(raw, str):
            specs[str(name)] = {"direction": raw}
        elif isinstance(raw, Mapping):
            specs[str(name)] = dict(raw)
    if specs:
        return specs

    # Without caller specs, use only directions explicitly supplied by the
    # candidates.  Objective names never imply a preferred direction here.
    directions: dict[str, set[str]] = {}
    for lifecycle in lifecycles:
        estimates = lifecycle.get("objective_estimates")
        if not isinstance(estimates, Mapping):
            continue
        for name, raw in estimates.items():
            if not isinstance(raw, Mapping):
                continue
            direction = str(raw.get("direction", "")).lower()
            if direction.startswith("min"):
                directions.setdefault(str(name), set()).add("min")
            elif direction.startswith("max"):
                directions.setdefault(str(name), set()).add("max")
    for name, values in directions.items():
        if len(values) == 1:
            specs[name] = {"direction": next(iter(values))}
    return specs


def _dominates(
    first: Mapping[str, dict[str, Any]],
    second: Mapping[str, dict[str, Any]],
    names: Sequence[str],
    *,
    robust: bool,
    specs: Mapping[str, Mapping[str, Any]],
) -> bool:
    if not names or any(name not in first or name not in second for name in names):
        return False
    no_worse = True
    strictly_better = False
    for name in names:
        a, b = first[name], second[name]
        epsilon = _finite_number(specs.get(name, {}).get("epsilon")) or 0.0
        if a["direction"] == "min":
            av = a["upper"] if robust else a["value"]
            bv = b["lower"] if robust else b["value"]
            no_worse = no_worse and av <= bv + epsilon
            strictly_better = strictly_better or av < bv - epsilon
        else:
            av = a["lower"] if robust else a["value"]
            bv = b["upper"] if robust else b["value"]
            no_worse = no_worse and av >= bv - epsilon
            strictly_better = strictly_better or av > bv + epsilon
    return no_worse and strictly_better


def analyze_pareto(
    candidate_lifecycles: Sequence[Mapping[str, Any]],
    *,
    objective_specs: Mapping[str, Mapping[str, Any] | str] | None = None,
) -> dict[str, Any]:
    """Return point and uncertainty-robust Pareto audits without scalarization."""
    lifecycles = [dict(item) for item in candidate_lifecycles if isinstance(item, Mapping)]
    specs = _objective_specs(lifecycles, objective_specs)
    names = sorted(specs)
    vectors: dict[str, dict[str, dict[str, Any]]] = {}
    incomplete: list[str] = []
    for item in lifecycles:
        if not item.get("feasible"):
            continue
        cid = str(item.get("candidate_id"))
        raw_estimates = item.get("objective_estimates")
        raw_estimates = raw_estimates if isinstance(raw_estimates, Mapping) else {}
        vector: dict[str, dict[str, Any]] = {}
        for name in names:
            parsed = _objective_item(raw_estimates.get(name), specs.get(name))
            if parsed is not None:
                vector[name] = parsed
        vectors[cid] = vector
        if any(name not in vector for name in names):
            incomplete.append(cid)

    comparable = sorted(cid for cid, vector in vectors.items() if names and all(name in vector for name in names))
    point_edges: list[dict[str, str]] = []
    robust_edges: list[dict[str, str]] = []
    for first in comparable:
        for second in comparable:
            if first == second:
                continue
            if _dominates(vectors[first], vectors[second], names, robust=False, specs=specs):
                point_edges.append({"dominant": first, "dominated": second})
            if _dominates(vectors[first], vectors[second], names, robust=True, specs=specs):
                robust_edges.append({"dominant": first, "dominated": second})
    point_dominated = {edge["dominated"] for edge in point_edges}
    robust_dominated = {edge["dominated"] for edge in robust_edges}
    return {
        "comparison": "pareto_no_scalarization",
        "objective_specs": _json_safe(specs),
        "objective_names": names,
        "vectors": _json_safe(vectors),
        "comparable_candidate_ids": comparable,
        "incomplete_candidate_ids": sorted(set(incomplete)),
        "point_frontier": [cid for cid in comparable if cid not in point_dominated],
        "robust_frontier": [cid for cid in comparable if cid not in robust_dominated],
        "point_dominance": point_edges,
        "robust_dominance": robust_edges,
        "weights_used": False,
    }


def _advisor_wrappers(
    advisor_candidates: Sequence[Mapping[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    anonymous = anonymize_advisor_candidates(advisor_candidates)
    wrappers: list[dict[str, Any]] = []
    for item in anonymous["presented_candidates"]:
        candidate = item.get("candidate")
        if not isinstance(candidate, Mapping):
            continue
        if isinstance(candidate.get("plan"), Mapping):
            plan = candidate.get("plan")
        else:
            plan = {
                key: value
                for key, value in candidate.items()
                if key not in _CANDIDATE_METADATA_KEYS
            }
        wrappers.append(
            {
                "candidate_id": item["advisor_ref"],
                "plan": plan,
                "objective_estimates": candidate.get(
                    "objective_estimates", candidate.get("objectives", {})
                ),
                "uncertainty": candidate.get("uncertainty", []),
                "counterfactuals": candidate.get("counterfactuals", []),
                "evidence_citations": [],
                "origin": "advisor",
            }
        )
    return wrappers, anonymous["provenance"]


def evaluate_planning_response(
    raw: Any,
    *,
    observable_state: Mapping[str, Any] | None,
    observable_profile: Mapping[str, Any] | None,
    memory: Mapping[str, Any] | None,
    event: Mapping[str, Any] | None,
    advisor_candidates: Sequence[Mapping[str, Any]] | None = None,
    explicit_constraints: Sequence[Mapping[str, Any]] | None = None,
    objective_specs: Mapping[str, Mapping[str, Any] | str] | None = None,
) -> dict[str, Any]:
    """Validate a complete model response and return plan plus portfolio audit.

    ``selected_executable_plan`` is populated only when the base model selected
    one of its own feasible candidates (or returned one valid legacy plan).
    Advisors participate in the comparison audit but are never execution
    fallbacks.
    """
    parsed = parse_planning_response(raw)
    raw_mapping, raw_mapping_error = _response_mapping(raw)
    raw_response_snapshot = (
        _sanitize_planning_input(raw_mapping)
        if isinstance(raw_mapping, Mapping)
        else {"parse_error": raw_mapping_error or "unavailable"}
    )
    model_lifecycles = [
        validate_plan_candidate(
            candidate,
            observable_state=observable_state,
            observable_profile=observable_profile,
            event=event,
            explicit_constraints=explicit_constraints,
            candidate_id=str(candidate.get("candidate_id")),
            origin="model",
        )
        for candidate in parsed["candidate_plans"]
    ]
    advisors, advisor_provenance = _advisor_wrappers(advisor_candidates)
    model_ids = {str(item.get("candidate_id")) for item in model_lifecycles}
    for advisor in advisors:
        original_id = str(advisor.get("candidate_id"))
        if original_id in model_ids:
            advisor["candidate_id"] = f"{original_id}__advisor_evidence"
            for item in advisor_provenance:
                if item.get("advisor_ref") == original_id:
                    item["audit_candidate_id"] = advisor["candidate_id"]
    advisor_lifecycles = [
        validate_plan_candidate(
            candidate,
            observable_state=observable_state,
            observable_profile=observable_profile,
            event=event,
            explicit_constraints=explicit_constraints,
            candidate_id=str(candidate.get("candidate_id")),
            origin="advisor",
        )
        for candidate in advisors
    ]
    lifecycles = model_lifecycles + advisor_lifecycles
    pareto = analyze_pareto(lifecycles, objective_specs=objective_specs)

    requested = parsed.get("selected_candidate_id")
    selected = next(
        (
            item
            for item in model_lifecycles
            if str(item.get("candidate_id")) == str(requested)
        ),
        None,
    )
    if requested is None:
        selection_status = "replan_required"
        selection_reason = "base model did not select a candidate"
    elif selected is None:
        selection_status = "replan_required"
        selection_reason = "selected ID is not a base-model candidate"
    elif not selected.get("feasible"):
        selection_status = "replan_required"
        selection_reason = "base model selected a candidate with unresolved hard violations"
    else:
        selection_status = "selected"
        selection_reason = "base model selection passed the common executable validator"

    selected_plan = (
        deepcopy(selected.get("validated_snapshot"))
        if selection_status == "selected" and selected is not None
        else None
    )
    if selected_plan is not None and selected is not None:
        explanation = selected.get("strategy_explanation")
        if isinstance(explanation, Mapping) and explanation:
            selected_plan["strategy_explanation"] = deepcopy(dict(explanation))
        elif isinstance(explanation, str) and explanation.strip():
            selected_plan["strategy_explanation"] = explanation.strip()
    planning_inputs = {
        "observable_state": _sanitize_planning_input(observable_state or {}),
        "observable_profile": _sanitize_planning_input(observable_profile or {}),
        "memory": _sanitize_planning_input(memory or {}),
        "event": _sanitize_planning_input(event or {}),
        "constraints": derive_planning_constraints(
            observable_state=observable_state,
            observable_profile=observable_profile,
            event=event,
            explicit_constraints=explicit_constraints,
        ),
        "advisor_presented_fingerprints": advisor_provenance,
    }
    audit = {
        "schema_version": PLANNING_SCHEMA_VERSION,
        "planning_input_fingerprint": _fingerprint(planning_inputs),
        "raw_response_snapshot": _json_safe(raw_response_snapshot),
        "legacy_single_plan": parsed["legacy_single_plan"],
        "selection_inference": parsed.get("selection_inference"),
        "parse_errors": parsed["parse_errors"],
        "model_selection": {
            "requested_candidate_id": requested,
            "selection_reason": parsed.get("selection_reason", ""),
            "status": selection_status,
            "validator_reason": selection_reason,
            "advisor_override_allowed": False,
        },
        "candidate_lifecycles": lifecycles,
        "pareto": pareto,
        "advisor_provenance": advisor_provenance,
        "information_acquisition": _information_acquisition_audit(
            parsed.get("information_requests") or [],
            observable_profile,
        ),
    }
    return {
        "schema_version": PLANNING_SCHEMA_VERSION,
        "selected_executable_plan": selected_plan,
        "selection_status": selection_status,
        "selected_candidate_id": requested if selected_plan is not None else None,
        "portfolio_audit": audit,
    }


__all__ = [
    "PLANNING_SCHEMA_VERSION",
    "PLAN_LIFECYCLE_VERSION",
    "anonymize_advisor_candidates",
    "derive_planning_constraints",
    "build_planning_prompts",
    "normalize_information_requests",
    "parse_planning_response",
    "validate_plan_candidate",
    "analyze_pareto",
    "evaluate_planning_response",
]
