"""User preference scorer using LLM roleplay evaluator (fallback: rule-based).

Scoring dimensions (M1):
  comfort_score  — thermal comfort satisfaction (1-5)
  energy_score   — energy usage / cost satisfaction (1-5)
  vpp_score      — VPP demand-response handling satisfaction (1-5)
  satisfaction_score — overall weighted sum (1-5, backward compat)

Enhancement (persona-aware):
  - Accepts persona dict from personas.py for differentiated scoring weights
  - Prompts written in English; persona dialogue style injected
  - Dialogue log written to logs/dialogue_{building}_{persona}_{city}_{method}.jsonl
  - VPP override: comfort_sensitive persona may express strong discomfort pre-event

M2 — zone_group_scores: office only, per-group comfort scores {Core, Bottom, Middle, Top}.
L3 — past_events included so Day 3 agent sees Day 1+2 feedback.
"""
from __future__ import annotations
import os, sys, json, random, datetime, re
from pathlib import Path
from energybridge.roleplay.calendar import calendar_brief_for_prompt, calendar_context_for_event

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BENCH_DIR = Path(__file__).parent
LOG_DIR   = BENCH_DIR / "logs" / "dialogue"
LOG_DIR.mkdir(parents=True, exist_ok=True)


def _adaptive_harness_v2() -> bool:
    value = str(os.getenv("ENERGYBRIDGE_HARNESS_PROFILE", "legacy_v1")).strip().lower()
    return value in {"v2", "adaptive", "adaptive_v2", "energybridge_v2"}

class StrategyPreference(str):
    """String preference text with attached candidate/selection trace metadata."""

    def __new__(cls, text: str, strategy_trace: dict | None = None):
        obj = str.__new__(cls, text)
        obj.strategy_trace = strategy_trace or {}
        return obj

# ---------------------------------------------------------------------------
# Legacy flat personas (backward compatibility when no persona dict supplied)
# ---------------------------------------------------------------------------
OFFICE_PERSONA = {
    "id": "office_default",
    "display_name": "Office User",
    "summary": "Office worker who values thermal comfort and willing to cooperate with grid demand.",
    "speaking_language": "en",
    "stable_preferences": {
        "comfort_priority": 0.6, "cost_priority": 0.3, "grid_priority": 0.1,
        "preferred_temp_min": 22.0, "preferred_temp_max": 26.0,
        "allow_pre_cooling": True, "allow_temp_drift": True,
    },
    "scoring_weights": {"comfort": 0.60, "energy": 0.30, "vpp": 0.10},
    "vpp_override_prob": 0.0,
}
FAMILY_PERSONA = {
    "id": "family_default",
    "display_name": "Home User",
    "summary": "Home resident who wants basic comfort and is willing to support grid peak-shaving.",
    "speaking_language": "en",
    "stable_preferences": {
        "comfort_priority": 0.5, "cost_priority": 0.2, "grid_priority": 0.3,
        "preferred_temp_min": 24.0, "preferred_temp_max": 26.0,
        "allow_pre_cooling": True, "allow_temp_drift": True,
    },
    "scoring_weights": {"comfort": 0.50, "energy": 0.20, "vpp": 0.30},
    "vpp_override_prob": 0.0,
}


# ---------------------------------------------------------------------------
# Dialogue logger
# ---------------------------------------------------------------------------
def _append_dialogue_log(log_path: Path, entry: dict) -> None:
    """Append one JSON-Lines entry to the dialogue log file."""
    try:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"  [DialogueLog] write error: {e}")


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------
def _int_score(val, default=3) -> int:
    try:
        v = int(round(float(val)))
        return max(1, min(5, v))
    except (TypeError, ValueError):
        return default


def _is_user_facing_controller_explanation(reason: str) -> bool:
    """Return True only for a real household-facing controller rationale."""
    text = str(reason or "").strip()
    if not text:
        return False
    lower = text.lower()
    objective_only_markers = (
        "mpc_pdf",
        "objective=",
        "objective =",
        "total=",
        "pulp_cbc",
        "solver",
        "cost=",
        "model=",
        "raw_policy_only",
    )
    if any(marker in lower for marker in objective_only_markers):
        user_words = (
            "comfort",
            "routine",
            "ev",
            "hot water",
            "water",
            "washer",
            "dryer",
            "dishwasher",
            "vpp",
            "price",
            "cost",
            "ready",
            "readiness",
        )
        # Objective traces sometimes include a metric word such as cost; require
        # multiple household-facing terms before treating them as explanations.
        return sum(1 for word in user_words if word in lower) >= 3
    return len(text) >= 24


_CONTROLLER_IDENTITY_RE = re.compile(
    r"\b(?:energybridge(?:\s*v\d+)?|hema(?:\s*agent)?|mpc(?:\s*dynamic)?|"
    r"rl(?:[-_\s]*ppo(?:[-_\s]*pref(?:[-_\s]*v\d+)?)?)?|rule[-+_\s]*milp|"
    r"openai|anthropic|xai|dmxapi|"
    r"(?:gpt|chatgpt|claude|gemini|qwen|deepseek|llama|mistral|grok|o[134])[-\w.]*)\b",
    re.IGNORECASE,
)


def _method_blind_observable_text(
    value: object,
    *,
    identities: list[str] | tuple[str, ...] = (),
    limit: int = 320,
) -> str:
    """Compact free text while removing controller/model identity tokens."""
    text = " ".join(str(value or "").split())
    for identity in sorted(
        {str(item).strip() for item in identities if str(item).strip()},
        key=len,
        reverse=True,
    ):
        # Short method aliases such as ``rl`` must not corrupt ordinary words
        # such as ``world``.  Match complete identifier tokens only.
        token_pattern = rf"(?<![\w]){re.escape(identity)}(?![\w])"
        text = re.sub(token_pattern, "controller", text, flags=re.IGNORECASE)
    text = _CONTROLLER_IDENTITY_RE.sub("controller", text)
    text = re.sub(r"\bcontroller(?:\s+controller)+\b", "controller", text, flags=re.IGNORECASE)
    return text[:limit].rstrip()


def _observable_acceptance_judgement(
    gate: dict | None,
    *,
    identities: list[str] | tuple[str, ...] = (),
) -> dict | None:
    """Allowlist method-blind gate evidence for the independent feedback LLM."""
    if not isinstance(gate, dict) or not gate:
        return None
    is_live_judgement = bool(
        str(gate.get("roleplay_source", "")) == "roleplay_llm"
        and not gate.get("fallback_source")
    )
    probability = _gate_acceptance_probability(gate) if is_live_judgement else None
    decision = gate.get("roleplay_decision", gate.get("decision"))
    reason = gate.get("roleplay_acceptance_reasoning") or gate.get("energybridge_feedback") or ""
    summary: dict = {
        "judgement_status": "live_household_judgement" if is_live_judgement else "unavailable",
        "acceptance_probability": probability,
        "roleplay_decision": (
            _method_blind_observable_text(decision, identities=identities, limit=40)
            if is_live_judgement
            else None
        ),
        "accepted": (
            bool(gate.get("accepted")) if is_live_judgement and "accepted" in gate else None
        ),
        "reason": (
            _method_blind_observable_text(reason, identities=identities)
            if is_live_judgement
            else "No live household judgement was available; do not infer satisfaction from a fallback prior.",
        ),
    }

    if not is_live_judgement:
        summary["evidence"] = []
        summary["probability_adjustments"] = []
        return summary

    evidence_items: list[dict] = []
    for item in list(gate.get("roleplay_evidence") or [])[:6]:
        if not isinstance(item, dict):
            continue
        evidence_items.append({
            "id": _method_blind_observable_text(item.get("id"), identities=identities, limit=24),
            "source": _method_blind_observable_text(item.get("source"), identities=identities, limit=40),
            "fact": _method_blind_observable_text(item.get("fact"), identities=identities, limit=180),
            "effect": _method_blind_observable_text(item.get("effect"), identities=identities, limit=60),
        })
    summary["evidence"] = evidence_items

    adjustment_items: list[dict] = []
    for item in list(gate.get("roleplay_probability_adjustments") or [])[:6]:
        if not isinstance(item, dict):
            continue
        try:
            delta = round(float(item.get("delta")), 4)
        except (TypeError, ValueError):
            delta = None
        adjustment_items.append({
            "dimension": _method_blind_observable_text(
                item.get("dimension"), identities=identities, limit=40
            ),
            "delta": delta,
            "evidence": _method_blind_observable_text(
                item.get("evidence"), identities=identities, limit=24
            ),
            "reason": _method_blind_observable_text(
                item.get("reason"), identities=identities, limit=160
            ),
        })
    summary["probability_adjustments"] = adjustment_items
    return summary


def normalize_persona(persona: dict) -> dict:
    """Accept both legacy scorer personas and current roleplay JSON personas."""
    normalized = dict(persona)
    preferences = persona.get("preferences", {})
    if "scoring_weights" not in normalized:
        normalized["scoring_weights"] = dict(preferences.get("scoring_weights", {}))
    if "vpp_override_prob" not in normalized:
        normalized["vpp_override_prob"] = float(preferences.get("vpp_override_prob", 0.0))
    if "stable_preferences" not in normalized:
        ac = persona.get("appliances", {}).get("ac", {})
        weights = normalized.get("scoring_weights", {})
        normalized["stable_preferences"] = {
            "comfort_priority": float(weights.get("comfort", 0.5)),
            "cost_priority": float(weights.get("energy", 0.3)),
            "grid_priority": float(weights.get("vpp", 0.2)),
            "preferred_temp_min": float(ac.get("setpoint_preferred_min_c", 24.0)),
            "preferred_temp_max": float(ac.get("setpoint_preferred_max_c", 26.0)),
            "allow_pre_cooling": True,
            "allow_temp_drift": float(ac.get("temp_tolerance_c", 1.0)) > 0.0,
        }
    normalized.setdefault(
        "temp_tolerance",
        float(persona.get("appliances", {}).get("ac", {}).get("temp_tolerance_c", 1.5)),
    )
    prompts = persona.get("llm_prompts", {})
    normalized.setdefault("roleplay_user_prompt", prompts.get("system_prompt", ""))
    normalized.setdefault("summary", persona.get("description", ""))
    return normalized


def _rule_score(persona: dict, mean_temp_c: float, pmv_ok_fraction: float,
                energy_kwh_per_day: float,
                zone_group_temps: dict | None = None,
                washer_completed: bool = True,
                washer_during_vpp: bool = False,
                skipped_task_count: int = 0,
                skipped_devices: list[str] | None = None) -> dict:
    """Rule-based fallback scorer using persona weights."""
    weights = persona.get("scoring_weights", {"comfort": 0.5, "energy": 0.3, "vpp": 0.2})
    sp = persona.get("stable_preferences", {})
    t_min = sp.get("preferred_temp_min", 23.0)
    t_max = sp.get("preferred_temp_max", 26.0)
    tolerance = persona.get("temp_tolerance", 1.5)

    # comfort_score: based on mean temp vs preferred range
    if t_min <= mean_temp_c <= t_max:
        comfort_score = 5
    elif mean_temp_c < t_min - tolerance or mean_temp_c > t_max + tolerance:
        comfort_score = 1
    elif mean_temp_c < t_min or mean_temp_c > t_max:
        overshoot = abs(mean_temp_c - (t_max if mean_temp_c > t_max else t_min))
        comfort_score = max(2, 5 - int(overshoot / 0.5))
    else:
        comfort_score = 3
    # boost from PMV ok fraction
    if pmv_ok_fraction >= 0.95:
        comfort_score = min(5, comfort_score + 1)

    # energy_score: reward lower consumption
    if energy_kwh_per_day < 20:
        energy_score = 5
    elif energy_kwh_per_day < 30:
        energy_score = 4
    elif energy_kwh_per_day < 50:
        energy_score = 3
    elif energy_kwh_per_day < 70:
        energy_score = 2
    else:
        energy_score = 1

    # vpp_score: based on setpoint compliance proxy
    if mean_temp_c >= 26.0:   # user tolerated warmth => VPP likely complied
        vpp_score = 4
    elif mean_temp_c >= 25.5:
        vpp_score = 3
    else:
        vpp_score = 2

    # washer penalty
    if not washer_completed:
        vpp_score = max(1, vpp_score - 1)
        comfort_score = max(1, comfort_score - 1)
    if washer_during_vpp:
        vpp_score = max(1, vpp_score - 1)   # ran washer added peak load

    if skipped_task_count > 0:
        skipped_names = ", ".join(skipped_devices or [])
        return {
            "score": 1,
            "comfort_score": max(1, comfort_score),
            "energy_score": max(1, energy_score),
            "vpp_score": 1,
            "label": "very_dissatisfied",
            "comment": f"rule_based: skipped required task(s): {skipped_names or skipped_task_count}",
            "zone_comfort_scores": None,
        }

    # weighted overall
    w_c = weights.get("comfort", 0.5)
    w_e = weights.get("energy", 0.3)
    w_v = weights.get("vpp", 0.2)
    total = w_c * comfort_score + w_e * energy_score + w_v * vpp_score
    overall = max(1, min(5, int(round(total))))

    comment = f"rule_based: temp={mean_temp_c:.1f}C pmv_ok={pmv_ok_fraction*100:.0f}%"
    return {
        "score": overall, "comfort_score": comfort_score,
        "energy_score": energy_score, "vpp_score": vpp_score,
        "label": ["very_dissatisfied","dissatisfied","neutral","satisfied","very_satisfied"][overall-1],
        "comment": comment, "zone_comfort_scores": None,
    }


_SCORE_LABELS = ["very_dissatisfied", "dissatisfied", "neutral", "satisfied", "very_satisfied"]


def _clamp_score(value: float) -> float:
    return max(1.0, min(5.0, float(value)))


def _score_label(value: float) -> str:
    idx = max(1, min(5, int(round(float(value))))) - 1
    return _SCORE_LABELS[idx]


def _record_v2_non_safety_factual_audit(
    result: dict,
    *,
    check: str,
    facts: dict,
) -> None:
    """Record a V2 factual disagreement without rewriting role-play judgement."""
    result.setdefault("non_safety_factual_audits", []).append({
        "check": str(check),
        "mode": "observation_only_adaptive_v2",
        "facts": dict(facts),
        "score_was_posthoc_remapped_by_guard": False,
        "label_was_posthoc_rewritten_by_guard": False,
        "comment_was_posthoc_rewritten_by_guard": False,
    })


def _apply_unserved_service_score_cap(result: dict, devices: list[str]) -> dict:
    """Apply the required-service failure cap after any scoring backend."""
    if not devices:
        return result
    result["score"] = min(result["score"], 2)
    result["energy_score"] = min(result["energy_score"], 2)
    result["vpp_score"] = min(result["vpp_score"], 2)
    result["label"] = "dissatisfied" if result["score"] == 2 else "very_dissatisfied"
    result["comment"] = (
        "Required appliance service target(s) were not met "
        f"({', '.join(devices)}); "
        + (result.get("comment", "") or "this violates the user's service rule.")
    )
    return result


def _persona_score_mode(persona: dict) -> str:
    persona_id = str(persona.get("id", "") or "").lower()
    tags = persona.get("tags", {}) or {}
    weights = persona.get("scoring_weights", {}) or {}
    comfort_w = float(weights.get("comfort", 0.5) or 0.5)
    energy_w = float(weights.get("energy", 0.3) or 0.3)
    vpp_w = float(weights.get("vpp", 0.2) or 0.2)
    if (
        "comfort_sensitive" in persona_id
        or tags.get("comfort") == "temp_sensitive"
        or comfort_w >= 0.56
    ):
        return "comfort"
    if (
        "price" in persona_id
        or tags.get("cost") in {"high", "price_sensitive", "price_cooperative"}
        or tags.get("grid") in {"cooperative", "high"}
        or energy_w + vpp_w >= 0.56
    ):
        return "price"
    if (
        "irregular" in persona_id
        or "cautious" in persona_id
        or tags.get("control") in {"confirm_required", "low_auto_accept", "privacy_sensitive"}
    ):
        return "cautious"
    return "balanced"


def _normalised_scoring_weights(persona: dict, mode: str) -> dict[str, float]:
    weights = dict(persona.get("scoring_weights", {}) or {})
    defaults = {
        "comfort": {"comfort": 0.72, "energy": 0.16, "vpp": 0.12},
        "price": {"comfort": 0.34, "energy": 0.36, "vpp": 0.30},
        "cautious": {"comfort": 0.54, "energy": 0.18, "vpp": 0.28},
        "balanced": {"comfort": 0.50, "energy": 0.25, "vpp": 0.25},
    }[mode]
    merged = {
        "comfort": float(weights.get("comfort", defaults["comfort"]) or defaults["comfort"]),
        "energy": float(weights.get("energy", defaults["energy"]) or defaults["energy"]),
        "vpp": float(weights.get("vpp", defaults["vpp"]) or defaults["vpp"]),
    }
    total = sum(max(0.0, v) for v in merged.values()) or 1.0
    return {k: max(0.0, v) / total for k, v in merged.items()}


def _hard_comfort_component(
    mean_temp_c: float,
    pmv_ok_fraction: float,
    pref_min: float,
    pref_max: float,
    pref_tol: float,
    gate_intrusion: dict | None,
    mode: str,
) -> float:
    tol = max(0.2, float(pref_tol))
    if pref_min <= mean_temp_c <= pref_max and pmv_ok_fraction >= 0.85:
        score = 5.0
    elif pref_min - tol <= mean_temp_c <= pref_max + tol and pmv_ok_fraction >= 0.65:
        score = 4.0
    else:
        over = max(pref_min - mean_temp_c, mean_temp_c - pref_max, 0.0)
        score = 3.0 - min(2.0, over / max(0.5, tol * 0.6))
        if pmv_ok_fraction < 0.45:
            score -= 0.8
    intrusion = gate_intrusion or {}
    try:
        proposed_excess = float(intrusion.get("comfort_excess_c", 0.0) or 0.0)
    except (TypeError, ValueError):
        proposed_excess = 0.0
    if bool(intrusion.get("hvac_off")):
        score -= 1.0 if mode == "price" else 1.8
    if proposed_excess > 0:
        score -= min(1.6 if mode != "price" else 0.7, proposed_excess * (0.55 if mode != "price" else 0.20))
    return _clamp_score(score)


def _hard_energy_component(energy_kwh_per_day: float, mode: str) -> float:
    energy = float(energy_kwh_per_day or 0.0)
    if mode == "price":
        thresholds = (22.0, 28.0, 35.0, 45.0)
    elif mode == "comfort":
        thresholds = (24.0, 34.0, 46.0, 62.0)
    else:
        thresholds = (23.0, 31.0, 42.0, 56.0)
    if energy <= thresholds[0]:
        return 5.0
    if energy <= thresholds[1]:
        return 4.0
    if energy <= thresholds[2]:
        return 3.0
    if energy <= thresholds[3]:
        return 2.0
    return 1.0


def _gate_acceptance_probability(gate: dict | None) -> float | None:
    """Return the role-play willingness probability carried by an event gate.

    Keep this deliberately independent of controller identity.  The gate is the
    household's pre-event judgement of the *observed proposal*; satisfaction can
    therefore share it as evidence without learning a method-specific offset.
    """
    if not isinstance(gate, dict):
        return None
    for key in ("acceptance_probability", "final_acceptance_probability"):
        value = gate.get(key)
        if value is None:
            continue
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            continue
    return None


def _hard_vpp_component(vpp_result_context: dict | None, gate: dict | None, mode: str) -> float:
    ctx = vpp_result_context or {}
    gate = gate or {}
    achieved = ctx.get("achieved")
    if achieved is True:
        score = 5.0 if mode == "price" else 4.0
    elif achieved is False:
        score = 2.0
    else:
        score = 3.0
    if gate and not bool(gate.get("accepted", True)):
        score -= 1.0 if mode in {"comfort", "cautious"} else 0.35
    return _clamp_score(score)


def _nested_float(data: dict | None, path: tuple[str, ...], default: float = 0.5) -> float:
    cur = data or {}
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    try:
        return float(cur)
    except (TypeError, ValueError):
        return default


def _is_rl_method(method: str, policy_control_context: dict | None = None) -> bool:
    source = ""
    if isinstance(policy_control_context, dict):
        source = str(policy_control_context.get("objective_source", "") or "")
    return str(method).startswith("rl") or source.startswith("rl_")


def _rl_raw_policy_appliance_failure(policy_control_context: dict | None) -> bool:
    if not isinstance(policy_control_context, dict):
        return False
    gate = policy_control_context.get("vpp_acceptance_gate")
    proposed = gate.get("proposed_plan", {}) if isinstance(gate, dict) else {}
    reason = str(proposed.get("reason") or "").lower()
    actions = proposed.get("appliance_actions")
    raw_missing_text = (
        "raw policy" in reason
        and (
            "not emitted" in reason
            or "no fallback appliance commands" in reason
            or "appliance commands were added" in reason
        )
    )
    empty_policy_actions = isinstance(actions, dict) and not actions
    emitted = set(policy_control_context.get("emitted_services") or [])
    action_space = set(policy_control_context.get("action_space_services") or [])
    weak_coverage = bool(action_space) and len(emitted & action_space) <= max(
        1, int(0.35 * len(action_space))
    )
    return bool(raw_missing_text or (empty_policy_actions and weak_coverage))


def _calibrate_roleplay_score(
    result: dict,
    *,
    persona: dict,
    method: str,
    mean_temp_c: float,
    pmv_ok_fraction: float,
    energy_kwh_per_day: float,
    pref_min: float,
    pref_max: float,
    pref_tol: float,
    explanation_is_user_facing: bool,
    vpp_result_context: dict | None,
    policy_control_context: dict | None,
    severe_service_issue: bool,
) -> dict:
    """Make satisfaction consistent with willingness using method-blind evidence.

    The acceptance probability is a shared *proposal perception* signal, not the
    final rating. Realised comfort, energy and VPP service remain independent
    evidence. V2 preserves the feedback LLM's coherent score/comment tuple and
    audits disagreement; legacy retains its frozen calibration. No method name,
    controller family, or target baseline rate enters the V2 judgement.
    """
    adaptive_v2 = _adaptive_harness_v2()
    policy_guard = result.get("policy_service_guard")
    v2_missing_service_evidence = bool(
        adaptive_v2
        and isinstance(policy_guard, dict)
        and policy_guard.get("missing_policy_services")
    )
    if severe_service_issue and not v2_missing_service_evidence:
        return result
    gate = {}
    if isinstance(policy_control_context, dict):
        maybe_gate = policy_control_context.get("vpp_acceptance_gate")
        if isinstance(maybe_gate, dict):
            gate = maybe_gate
    intrusion = gate.get("intrusion", {}) if isinstance(gate.get("intrusion"), dict) else {}

    if adaptive_v2:
        # V2 consistency is prompt-owned.  Preserve the LLM-authored score and
        # comment as one coherent judgement instead of deterministically
        # remapping the score from acceptance probability.  The audit makes any
        # disagreement observable for evals without changing either value.
        authored_score = round(_clamp_score(float(result.get("score", 3.0) or 3.0)), 2)
        result["score"] = authored_score
        result["comfort_score"] = round(
            _clamp_score(float(result.get("comfort_score", 3.0) or 3.0)), 2
        )
        result["energy_score"] = round(
            _clamp_score(float(result.get("energy_score", 3.0) or 3.0)), 2
        )
        result["vpp_score"] = round(
            _clamp_score(float(result.get("vpp_score", 3.0) or 3.0)), 2
        )
        result["label"] = _score_label(authored_score)
        is_live_judgement = bool(
            gate
            and str(gate.get("roleplay_source", "")) == "roleplay_llm"
            and not gate.get("fallback_source")
        )
        probability = _gate_acceptance_probability(gate) if is_live_judgement else None
        rating_willingness = (authored_score - 1.0) / 4.0
        result["score_consistency_audit"] = {
            "version": "prompt_owned_acceptance_satisfaction_v2",
            "method_blind": True,
            "score_was_posthoc_remapped": False,
            "non_safety_factual_audit_count": len(
                result.get("non_safety_factual_audits") or []
            ),
            "non_safety_factual_checks": [
                str(item.get("check", ""))
                for item in (result.get("non_safety_factual_audits") or [])
                if isinstance(item, dict) and item.get("check")
            ],
            "live_acceptance_judgement": is_live_judgement,
            "acceptance_probability": probability,
            "normalized_authored_rating": round(rating_willingness, 6),
            "signed_rating_minus_acceptance": (
                round(rating_willingness - probability, 6)
                if probability is not None
                else None
            ),
        }
        return result

    mode = _persona_score_mode(persona)
    weights = _normalised_scoring_weights(persona, mode)
    accepted = bool(gate.get("accepted", True)) if gate else True
    comfort_hard = _hard_comfort_component(
        mean_temp_c, pmv_ok_fraction, pref_min, pref_max, pref_tol, intrusion, mode
    )
    energy_hard = _hard_energy_component(energy_kwh_per_day, mode)
    vpp_hard = _hard_vpp_component(vpp_result_context, gate, mode)
    hard_weighted = (
        weights["comfort"] * comfort_hard
        + weights["energy"] * energy_hard
        + weights["vpp"] * vpp_hard
    )
    llm_score = _clamp_score(float(result.get("score", 3.0) or 3.0))
    calibrated = 0.56 * llm_score + 0.44 * hard_weighted

    quality = _nested_float(gate, ("strategy_quality", "strategy_quality_score"), 0.5)
    calendar_fit = _nested_float(
        gate, ("adaptability_diagnostics", "calendar_fit", "calendar_fit_score"), 0.5
    )
    alignment = _nested_float(
        gate,
        ("adaptability_diagnostics", "roleplay_preference_alignment", "alignment_score"),
        0.5,
    )
    similarity = _nested_float(
        gate, ("adaptability_diagnostics", "rule_milp_similarity", "similarity_score"), 0.5
    )
    no_explanation = bool(gate) and not bool(
        intrusion.get("has_user_facing_explanation", explanation_is_user_facing)
    )
    rl_method = _is_rl_method(method, policy_control_context)
    rl_policy_failure = rl_method and _rl_raw_policy_appliance_failure(policy_control_context)
    if rl_policy_failure:
        no_explanation = True
    try:
        proposed_excess = float(intrusion.get("comfort_excess_c", 0.0) or 0.0)
    except (TypeError, ValueError):
        proposed_excess = 0.0
    hvac_off = bool(intrusion.get("hvac_off"))

    if mode == "comfort":
        calibrated += 0.55 * (quality - 0.5) + 0.35 * (calendar_fit - 0.5) + 0.25 * (alignment - 0.5)
        calibrated += 0.55 if accepted else -0.55
        if no_explanation:
            calibrated -= 0.25
        if hvac_off:
            calibrated -= 0.75
        calibrated -= min(0.9, proposed_excess * 0.18)
    elif mode == "price":
        calibrated += 0.30 * (quality - 0.5) + 0.20 * (calendar_fit - 0.5)
        calibrated += 0.22 if accepted else -0.18
        if str(method) == "rule_milp":
            calibrated += 0.45 * max(0.0, similarity - 0.45)
        if vpp_result_context and vpp_result_context.get("achieved") is True:
            calibrated += 0.25
        if hvac_off and proposed_excess > 2.0:
            calibrated -= 0.35
        if no_explanation:
            calibrated -= 0.12
    elif mode == "cautious":
        calibrated += 0.45 * (quality - 0.5) + 0.45 * (calendar_fit - 0.5) + 0.30 * (alignment - 0.5)
        calibrated += 0.35 if accepted else -0.45
        if no_explanation:
            calibrated -= 0.35
        if hvac_off:
            calibrated -= 0.55
        calibrated -= min(0.6, proposed_excess * 0.12)
    else:
        calibrated += 0.30 * (quality - 0.5) + (0.25 if accepted else -0.25)

    if rl_policy_failure:
        if mode == "price":
            calibrated -= 1.10
            calibrated = min(calibrated, 2.60 if accepted else 2.25)
        elif mode == "cautious":
            calibrated -= 1.35
            calibrated = min(calibrated, 2.05 if accepted else 1.80)
        elif mode == "comfort":
            calibrated -= 1.25
            calibrated = min(calibrated, 1.35 if accepted else 1.15)
        else:
            calibrated -= 1.10
            calibrated = min(calibrated, 2.20)

    if gate and not accepted:
        if mode == "comfort" and (hvac_off or proposed_excess >= 1.0):
            calibrated = min(calibrated, 2.35)
        elif mode == "comfort":
            calibrated = min(calibrated, 3.25)
        elif mode == "cautious" and (hvac_off or no_explanation or proposed_excess >= 1.0):
            calibrated = min(calibrated, 3.20)
        elif mode == "price" and hvac_off and proposed_excess >= 3.0:
            calibrated = min(calibrated, 4.05)

    ctx = vpp_result_context or {}
    non_ac_during_vpp = list(ctx.get("non_ac_appliances_during_vpp") or [])
    nonfatal_rejected_rebound = bool(
        gate
        and not accepted
        and non_ac_during_vpp
        and not severe_service_issue
        and not rl_policy_failure
        and not bool(intrusion.get("raw_policy_only"))
    )
    if nonfatal_rejected_rebound:
        rebound_floor = 2.15
        if comfort_hard >= 4.0:
            rebound_floor += 0.25
        if vpp_hard >= 2.0:
            rebound_floor += 0.10
        if mode == "price":
            rebound_floor += 0.15
        elif mode == "comfort":
            rebound_floor -= 0.05
        if not hvac_off and proposed_excess <= 0.5:
            rebound_floor += 0.45
        elif hvac_off or proposed_excess >= 2.0:
            rebound_floor -= 0.20
        if no_explanation:
            rebound_floor -= 0.05
        if len(non_ac_during_vpp) >= 3:
            rebound_floor -= 0.10
        calibrated = max(calibrated, rebound_floor)
        rebound_cap = 3.10 if not no_explanation else 2.95
        if hvac_off or proposed_excess >= 2.0:
            rebound_cap = min(rebound_cap, 2.45)
        calibrated = min(calibrated, rebound_cap)

    clean_accepted_event = bool(
        gate
        and accepted
        and ctx.get("achieved") is True
        and not severe_service_issue
        and not non_ac_during_vpp
        and not rl_policy_failure
    )
    if clean_accepted_event and explanation_is_user_facing:
        accepted_floor = 3.75
        if comfort_hard >= 4.0:
            accepted_floor += 0.15
        if calendar_fit >= 0.65 or alignment >= 0.65:
            accepted_floor += 0.10
        calibrated = max(calibrated, min(4.25, accepted_floor))

    calibrated = round(_clamp_score(calibrated), 2)
    old_score = result.get("score", calibrated)
    result["score"] = calibrated
    result["comfort_score"] = round(
        _clamp_score(0.50 * float(result.get("comfort_score", 3) or 3) + 0.50 * comfort_hard), 2
    )
    result["energy_score"] = round(
        _clamp_score(0.45 * float(result.get("energy_score", 3) or 3) + 0.55 * energy_hard), 2
    )
    result["vpp_score"] = round(
        _clamp_score(0.45 * float(result.get("vpp_score", 3) or 3) + 0.55 * vpp_hard), 2
    )
    result["label"] = _score_label(calibrated)
    if abs(float(old_score or calibrated) - calibrated) >= 0.25:
        result["original_roleplay_score"] = old_score
        result["score_calibration"] = {
            "mode": mode,
            "weights": weights,
            "hard_components": {
                "comfort": round(comfort_hard, 3),
                "energy": round(energy_hard, 3),
                "vpp": round(vpp_hard, 3),
            },
            "gate_accepted": accepted,
            "strategy_quality": round(quality, 3),
            "calendar_fit": round(calendar_fit, 3),
            "roleplay_alignment": round(alignment, 3),
            "rule_milp_similarity": round(similarity, 3),
            "hvac_off": hvac_off,
            "comfort_excess_c": round(proposed_excess, 3),
            "no_user_facing_explanation": no_explanation,
            "rl_raw_policy_appliance_failure": rl_policy_failure,
        }
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
_APPLIANCE_KEYS = ("washer", "dishwasher", "dryer", "water_heater", "ev")


def _service_from_action_key(key: str) -> str | None:
    key = str(key)
    if key.startswith("washer_"):
        return "washer"
    if key.startswith("dishwasher_"):
        return "dishwasher"
    if key.startswith("dryer_"):
        return "dryer"
    if key.startswith("water_heater_"):
        return "water_heater"
    if key.startswith("ev_"):
        return "ev"
    return None


def _services_from_actions(actions: dict | None) -> set[str]:
    services: set[str] = set()
    if not isinstance(actions, dict):
        return services
    for key, value in actions.items():
        if value is None:
            continue
        service = _service_from_action_key(str(key))
        if service:
            services.add(service)
    return services


def _iter_policy_action_dicts(policy_control_context: dict | None):
    if not isinstance(policy_control_context, dict):
        return
    actions = policy_control_context.get("vpp_trigger_actions")
    if isinstance(actions, dict):
        yield actions
    for decision in policy_control_context.get("day_decisions") or []:
        if not isinstance(decision, dict):
            continue
        for key in ("actions", "raw_appliance_actions"):
            actions = decision.get(key)
            if isinstance(actions, dict):
                yield actions


def _invalid_ev_policy_windows(policy_control_context: dict | None, persona: dict | None) -> list[str]:
    """Find EV charge windows that cannot serve the current day's post-arrival need."""
    ev_cfg = ((persona or {}).get("appliances") or {}).get("ev", {}) or {}
    if not bool(ev_cfg.get("present")):
        return []
    try:
        arrival_h = float(ev_cfg.get("arrival_h", 18.0)) % 24.0
        departure_h = float(ev_cfg.get("departure_h", 7.5)) % 24.0
    except (TypeError, ValueError):
        return []
    invalid: list[str] = []
    for actions in _iter_policy_action_dicts(policy_control_context):
        if actions.get("ev_charge_start_h") is None or actions.get("ev_charge_end_h") is None:
            continue
        try:
            start_h = float(actions.get("ev_charge_start_h")) % 24.0
            end_h = float(actions.get("ev_charge_end_h")) % 24.0
        except (TypeError, ValueError):
            invalid.append(f"{actions.get('ev_charge_start_h')}-{actions.get('ev_charge_end_h')}")
            continue
        if arrival_h > departure_h:
            # For an arrival-day policy, a same-day early-morning window such as
            # 04:30-07:30 happens before the evening arrival and cannot recharge
            # the commute energy consumed at departure.  Use an evening start or
            # an overnight window (start > end) instead.
            feasible = start_h >= arrival_h or start_h > end_h
        else:
            feasible = start_h < end_h and start_h < departure_h and end_h > arrival_h
        if not feasible:
            invalid.append(f"{_fmt_hour_for_window(start_h)}-{_fmt_hour_for_window(end_h)}")
    return invalid


def _fmt_hour_for_window(hour: float) -> str:
    h = float(hour) % 24.0
    hh = int(h)
    mm = int(round((h - hh) * 60))
    if mm >= 60:
        hh = (hh + 1) % 24
        mm = 0
    return f"{hh:02d}:{mm:02d}"


def _vpp_window_from_context(vpp_context: dict | None) -> tuple[float, float, str]:
    ctx = vpp_context or {}
    start = float(ctx.get("trigger_h", ctx.get("hour", 18.0))) % 24.0
    if "end_h" in ctx:
        end = float(ctx.get("end_h", start + float(ctx.get("duration_h", 1.0)))) % 24.0
    else:
        end = (start + float(ctx.get("duration_h", 1.0))) % 24.0
    return start, end, f"{_fmt_hour_for_window(start)}-{_fmt_hour_for_window(end)}"


def _get_appliance_presence(vpp_context: dict) -> dict:
    raw = (vpp_context or {}).get("appliances", {}) if isinstance(vpp_context, dict) else {}
    return {k: bool((raw.get(k, False) if isinstance(raw, dict) else False)) for k in _APPLIANCE_KEYS}


def _appliance_control_profile(persona: dict | None, vpp_context: dict | None) -> dict:
    """Return present/controllable/fixed status for each appliance.

    VPP runtime context only tells the role-play layer whether a device exists.
    The persona JSON carries the critical second bit: whether the agent is
    allowed to shift/control that device. Strategy text must not promise control
    over fixed routines, or low-DR users will reasonably punish the agent.
    """
    presence = _get_appliance_presence(vpp_context or {})
    app_cfg = (persona or {}).get("appliances", {}) or {}
    profile: dict = {}
    for name in _APPLIANCE_KEYS:
        cfg = app_cfg.get(name, {}) or {}
        present = bool(presence.get(name, False) or cfg.get("present", False))
        if name in {"washer", "dishwasher", "dryer"}:
            controllable = present and bool(cfg.get("shiftable", True)) and bool(cfg.get("dr_adjustable", True))
        elif name == "water_heater":
            controllable = present and bool(cfg.get("dr_adjustable", True))
        elif name == "ev":
            controllable = present and cfg.get("dr_adjustable") is not False
        else:
            controllable = present
        profile[name] = {
            "present": present,
            "controllable": bool(controllable),
            "fixed": bool(present and not controllable),
        }
    return profile


def _profile_present(profile: dict, name: str) -> bool:
    raw = profile.get(name, False)
    if isinstance(raw, dict):
        return bool(raw.get("present", False))
    return bool(raw)


def _profile_controllable(profile: dict, name: str) -> bool:
    raw = profile.get(name, False)
    if isinstance(raw, dict):
        return bool(raw.get("present", False) and raw.get("controllable", False))
    return bool(raw)


def _profile_fixed_names(profile: dict) -> list[str]:
    names: list[str] = []
    for name in _APPLIANCE_KEYS:
        raw = profile.get(name, False)
        if isinstance(raw, dict) and raw.get("present") and raw.get("fixed"):
            names.append(name)
    return names


def _profile_controllable_names(profile: dict) -> list[str]:
    return [name for name in _APPLIANCE_KEYS if _profile_controllable(profile, name)]


def _fixed_appliance_constraints(persona: dict | None) -> list[str]:
    """Return present devices that the controller cannot shift for DR."""
    app = (persona or {}).get("appliances", {}) or {}
    fixed: list[str] = []
    for name in ("washer", "dishwasher", "dryer"):
        cfg = app.get(name, {}) or {}
        if bool(cfg.get("present")) and (
            not bool(cfg.get("shiftable", True)) or not bool(cfg.get("dr_adjustable", True))
        ):
            fixed.append(name)
    wh = app.get("water_heater", {}) or {}
    if bool(wh.get("present")) and not bool(wh.get("dr_adjustable", True)):
        fixed.append("water_heater")
    ev = app.get("ev", {}) or {}
    if bool(ev.get("present")) and ev.get("dr_adjustable") is False:
        fixed.append("ev")
    return fixed


def _low_disruption_strategy_language(persona: dict | None) -> bool:
    """True when strategy text should avoid cost/savings/DR-push framing."""
    tags = (persona or {}).get("tags", {}) or {}
    return (
        tags.get("price") in {"low_incentive", "price_indifferent"}
        or tags.get("grid_value") in {"low_value", "uncertain_flex"}
        or tags.get("control") in {"confirm_required", "low_auto_accept", "privacy_sensitive"}
        or tags.get("task") in {"rigid", "semi_rigid"}
    )


def build_vpp_preference_memory_notes(past_events: list | None, persona: dict | None = None) -> list[str]:
    """Extract compact, reusable strategy-generation rules from past feedback."""
    events = list(past_events or [])
    if not events:
        return []

    notes: list[str] = []
    persona = normalize_persona(persona or FAMILY_PERSONA)
    tags = persona.get("tags", {}) or {}
    fixed_appliances = set(_fixed_appliance_constraints(persona))
    comments = " ".join(str(e.get("comment", "")) for e in events).lower()
    user_inputs = " ".join(str(e.get("user_input", "")) for e in events).lower()
    low_scores = [e for e in events if int(e.get("score") or 3) <= 3]
    low_score_text = " ".join(
        (str(e.get("comment", "")) + " " + str(e.get("user_input", ""))).lower()
        for e in low_scores
    )
    recent = events[-3:]

    def _score(event: dict, key: str, default: int = 3) -> int:
        try:
            return max(1, min(5, int(round(float(event.get(key, default))))))
        except (TypeError, ValueError):
            return default

    def _has_controllable_service_issue(event: dict) -> bool:
        summary = event.get("appliance_summary") or {}
        if not isinstance(summary, dict):
            return False
        for name, info in summary.items():
            if not isinstance(info, dict) or not bool(info.get("present")):
                continue
            if name in {"washer", "dishwasher", "dryer"} and bool(info.get("skipped")):
                return True
            if name not in fixed_appliances and bool(info.get("ran_during_vpp")):
                return True
        return False

    def _has_fixed_vpp_overlap(event: dict) -> bool:
        summary = event.get("appliance_summary") or {}
        if not isinstance(summary, dict):
            return False
        for name, info in summary.items():
            if (
                name in fixed_appliances
                and isinstance(info, dict)
                and bool(info.get("present"))
                and bool(info.get("ran_during_vpp"))
            ):
                return True
        return False

    recent_positive = (
        len(recent) >= 2
        and all(_score(e, "score") >= 4 for e in recent[-2:])
        and all(_score(e, "comfort_score") >= 4 for e in recent[-2:])
    )
    recent_clean = recent_positive and not any(_has_controllable_service_issue(e) for e in recent[-2:])
    any_too_warm = any(
        word in comments + " " + user_inputs
        for word in ("too warm", "above 26", "26.5", "hot", "uncomfortable", "temperature drift")
    )
    normal_comfort = tags.get("comfort") in {None, "", "normal_comfort"}
    exploration_allowed = (
        recent_clean
        and not low_scores
        and normal_comfort
        and tags.get("control") not in {"confirm_required", "low_auto_accept", "privacy_sensitive"}
        and not _low_disruption_strategy_language(persona)
        and not any_too_warm
    )

    if _low_disruption_strategy_language(persona) or any(
        word in comments for word in ("cost talk", "saving", "savings", "money", "irrelevant", "annoying")
    ):
        notes.append(
            "Keep user-facing explanations centered on comfort preservation, routine stability, and low-risk event support."
        )
    if any(word in low_score_text for word in ("pointless", "weak", "limited", "not very useful", "partial")):
        notes.append(
            "Set expectations that fixed routines can limit VPP impact; do not overpromise grid response."
        )
    if (
        any(word in low_score_text for word in ("return home", "home arrival", "commute home", "arrive home"))
        and any(word in low_score_text for word in ("warm", "26.5", "above 26", "temperature drift"))
    ):
        notes.append(
            "For events near return-home or dinner time, avoid the warm edge of the comfort range and restore comfort immediately after the event."
        )
    if any(word in low_score_text for word in ("above 26", "26.5", "too warm")):
        notes.append(
            "Treat 26C as the practical comfort ceiling unless the user explicitly accepts a warmer setting for that event."
        )
    if any(word in comments for word in ("fixed load", "fixed loads", "fixed hot-water", "routine unchanged")):
        notes.append(
            "Treat fixed/non-DR-adjustable appliances as constraints, not controllable levers."
        )
    if any(_has_fixed_vpp_overlap(e) for e in recent):
        notes.append(
            "When fixed appliances overlap the event, acknowledge the constraint and focus future actions on controllable devices and comfort-safe HVAC only."
        )
    if any(word in user_inputs + comments for word in ("confirm", "only", "comfort-safe", "tiny adjustment")):
        notes.append(
            "Keep each event within the selected confirmation boundary; avoid stronger actions than the user approved."
        )
    if any(word in comments for word in ("comfort stayed", "comfort kept", "within range", "acceptable")):
        notes.append(
            "Repeat the proven pattern: keep AC inside the preferred range and mention that comfort/routine are protected."
        )
    if exploration_allowed:
        notes.append(
            "Recent events scored well with comfort preserved; cautiously improve energy by using the warm edge of the preferred AC range during VPP, then restore without overcooling."
        )
        notes.append(
            "For future daily planning, avoid unnecessary cooling below the user's comfortable mid-to-warm range unless safety or explicit feedback asks for colder air."
        )
    elif recent_positive and any_too_warm:
        notes.append(
            "Do not escalate energy-saving temperature exploration because prior feedback mentioned warmth; keep the next event closer to the normal comfort target."
        )
    if any(e.get("target_achieved") is False for e in recent) and recent_clean and not _low_disruption_strategy_language(persona):
        notes.append(
            "If a future VPP event has non-AC appliance activity inside the VPP window without comfort complaints, strengthen controllable load shifting and use the warmest still-comfortable AC setting inside the preferred range."
        )

    deduped: list[str] = []
    for note in notes:
        if note not in deduped:
            deduped.append(note)
    return deduped[:5]


def _strategy_appliance_plan_text(
    strategy_id: str,
    presence: dict,
    vpp_context: dict | None = None,
    persona: dict | None = None,
) -> str:
    if not presence:
        return ""
    start_h, _, window_text = _vpp_window_from_context(vpp_context)
    low_disruption = (
        False if _adaptive_harness_v2() else _low_disruption_strategy_language(persona)
    )
    parts = []
    if _profile_controllable(presence, "washer"):
        parts.append("shift washer away from VPP")
    if _profile_controllable(presence, "dishwasher"):
        parts.append("shift dishwasher away from VPP")
    if _profile_controllable(presence, "dryer"):
        parts.append("shift dryer away from VPP")
    if _profile_controllable(presence, "water_heater"):
        parts.append(f"finish water-heater preheat before {_fmt_hour_for_window(start_h)}")
    if _profile_controllable(presence, "ev"):
        parts.append(f"keep EV charging out of {window_text}")
    fixed = _profile_fixed_names(presence)
    if not parts and not fixed:
        return ""

    if low_disruption:
        if strategy_id == "A":
            head = "Protect comfort and routine first"
        elif strategy_id == "B":
            head = "Use only low-disruption support"
        else:
            head = "Support the event without disrupting routine"
    elif strategy_id == "A":
        head = "Prioritize comfort with gentle appliance shifting"
    elif strategy_id == "B":
        head = "Balance comfort and peak reduction with active shifting"
    else:
        head = "Prioritize peak reduction and move controllable loads"
    text = f"{head}: " + (", ".join(parts) if parts else "adjust only controllable HVAC/load")
    if fixed:
        text += "; keep fixed appliances unchanged (" + ", ".join(fixed) + ")"
    return text


def _strategy_appliance_plan_cn(
    strategy_id: str,
    presence: dict,
    vpp_context: dict | None = None,
    persona: dict | None = None,
) -> str:
    """Backward-compatible alias; returns English text despite the old name."""
    return _strategy_appliance_plan_text(strategy_id, presence, vpp_context, persona)


def _strategy_appliance_pref_en(
    strategy_id: str,
    presence: dict,
    vpp_context: dict | None = None,
    persona: dict | None = None,
) -> str:
    if not presence:
        return ""
    start_h, _, window_text = _vpp_window_from_context(vpp_context)
    low_disruption = (
        False if _adaptive_harness_v2() else _low_disruption_strategy_language(persona)
    )
    actions = []
    if _profile_controllable(presence, "washer"):
        actions.append(f"shift washer away from {window_text}")
    if _profile_controllable(presence, "dishwasher"):
        actions.append(f"shift dishwasher away from {window_text}")
    if _profile_controllable(presence, "dryer"):
        actions.append(f"shift dryer away from {window_text}")
    if _profile_controllable(presence, "water_heater"):
        actions.append(f"finish water-heater preheat before {_fmt_hour_for_window(start_h)}")
    if _profile_controllable(presence, "ev"):
        actions.append(f"keep EV charging out of {window_text}")
    fixed = _profile_fixed_names(presence)
    if not actions and not fixed:
        return ""

    if low_disruption:
        if strategy_id == "A":
            prefix = "Protect comfort and routine while"
        elif strategy_id == "B":
            prefix = "Make only low-disruption event support by"
        else:
            prefix = "Use the strongest still-routine-safe support by"
    elif strategy_id == "A":
        prefix = "Keep comfort as priority while"
    elif strategy_id == "B":
        prefix = "Balance comfort and demand response by"
    else:
        prefix = "Prioritize peak reduction by"
    if actions:
        text = f"{prefix} " + ", ".join(actions) + "."
    else:
        text = "Use only comfort-safe controllable actions."
    if fixed:
        text += " Keep fixed appliance routines unchanged: " + ", ".join(fixed) + "."
    if low_disruption:
        text += " Keep the explanation centered on comfort and routine only."
    return text


def _compose_pref_with_appliances(selected: dict, presence: dict, vpp_context: dict | None = None) -> str:
    base = selected.get("user_pref", selected.get("description", "")).strip()
    if selected.get("_profile_aligned"):
        return base
    sid = str(selected.get("id", "")).upper()
    tail = _strategy_appliance_pref_en(sid, presence, vpp_context).strip()
    if not tail:
        return base
    if not base:
        return tail
    return f"{base} {tail}"


def _candidate_trace_items(
    candidates: list[dict],
    presence: dict,
    vpp_context: dict | None,
    persona: dict | None,
) -> list[dict]:
    items = []
    for candidate in candidates:
        sid = str(candidate.get("id", "")).upper()
        items.append({
            "id": sid,
            "label": candidate.get("label", ""),
            "description": candidate.get("description", ""),
            "tradeoff": candidate.get("tradeoff", ""),
            "user_pref": candidate.get("user_pref", ""),
            "appliance_plan": _strategy_appliance_plan_text(sid, presence, vpp_context, persona),
            "appliance_plan_cn": _strategy_appliance_plan_text(sid, presence, vpp_context, persona),
            "appliance_pref_en": _strategy_appliance_pref_en(sid, presence, vpp_context, persona),
        })
    return items


def _selected_trace_item(
    selected: dict,
    *,
    source: str,
    preference_text: str,
    selection_meta: dict | None = None,
    human_input: str = "",
) -> dict:
    return {
        "id": selected.get("id", ""),
        "label": selected.get("label", ""),
        "description": selected.get("description", ""),
        "tradeoff": selected.get("tradeoff", ""),
        "source": source,
        "human_input": human_input,
        "preference_text": preference_text,
        "selection_meta": selection_meta or {},
    }


def _strategy_user_pref_for_profile(
    candidate: dict,
    profile: dict,
    vpp_context: dict | None,
    persona: dict | None,
) -> str:
    """Build a concise user preference that only promises controllable actions."""
    sid = str(candidate.get("id", "")).upper()
    sp = (persona or {}).get("stable_preferences", {}) or {}
    pref_min = float(sp.get("preferred_temp_min", 24.0))
    pref_max = float(sp.get("preferred_temp_max", 26.0))
    low_disruption = (
        False if _adaptive_harness_v2() else _low_disruption_strategy_language(persona)
    )
    if low_disruption:
        if sid == "A":
            base = f"Comfort first: keep AC within {pref_min:.1f}-{pref_max:.1f}°C and protect routine."
        elif sid == "B":
            base = f"Low-disruption support: allow only a tiny AC adjustment within {pref_min:.1f}-{pref_max:.1f}°C."
        else:
            base = f"Routine-safe support: use the warmest comfortable AC setting within {pref_min:.1f}-{pref_max:.1f}°C."
    elif sid == "A":
        base = f"Comfort first: keep AC within {pref_min:.1f}-{pref_max:.1f}°C."
    elif sid == "B":
        base = f"Balanced: allow only a brief AC adjustment within {pref_min:.1f}-{pref_max:.1f}°C."
    else:
        base = f"Energy-aware: use the warmest still-comfortable AC setting within {pref_min:.1f}-{pref_max:.1f}°C."
    tail = _strategy_appliance_pref_en(sid, profile, vpp_context, persona)
    return f"{base} {tail}".strip()


def _align_candidates_to_appliance_profile(
    candidates: list[dict],
    profile: dict,
    vpp_context: dict | None,
    persona: dict | None,
) -> list[dict]:
    """Make strategy candidates consistent with appliance controllability."""
    fixed = _profile_fixed_names(profile)
    controllable = _profile_controllable_names(profile)
    low_disruption = (
        False if _adaptive_harness_v2() else _low_disruption_strategy_language(persona)
    )
    aligned: list[dict] = []
    for raw in candidates:
        c = dict(raw)
        if _adaptive_harness_v2():
            # V1 rewrote every model-generated option into the same A/B/C
            # sentence templates. V2 retains the simulated household's actual
            # wording and only appends non-negotiable controllability facts.
            pref = str(c.get("user_pref", c.get("description", "")) or "").strip()
            constraint_bits: list[str] = []
            if fixed:
                constraint_bits.append("Keep fixed routines unchanged: " + ", ".join(fixed) + ".")
            if not controllable and fixed:
                constraint_bits.append("Only comfort-safe AC changes are controllable.")
            suffix = " ".join(constraint_bits)
            if suffix and suffix.lower() not in pref.lower():
                pref = f"{pref} {suffix}".strip()
            c["user_pref"] = pref[:600]
            c["description"] = str(c.get("description", ""))[:240]
            c["tradeoff"] = str(c.get("tradeoff", ""))[:180]
            c["_profile_aligned"] = "adaptive_v2_constraints_only"
            aligned.append(c)
            continue
        c["user_pref"] = _strategy_user_pref_for_profile(c, profile, vpp_context, persona)
        if low_disruption:
            if str(c.get("id", "")).upper() == "A":
                c["label"] = "Comfort first"
                c["tradeoff"] = "Least disruption, limited support"
            elif str(c.get("id", "")).upper() == "B":
                c["label"] = "Gentle support"
                c["tradeoff"] = "Small adjustment, routine unchanged"
            else:
                c["label"] = "Low-disruption support"
                c["tradeoff"] = "Stronger support while low disruption"
        desc = str(c.get("description", "")).strip()
        if fixed:
            fixed_note = "fixed appliances unchanged"
            desc = f"{desc}; {fixed_note}" if desc else fixed_note
        if not controllable and fixed:
            desc = "Only comfort-safe AC adjustment; fixed appliances unchanged"
        if low_disruption and str(c.get("id", "")).upper() == "C":
            desc = "Gentle support within comfort range; fixed routine unchanged"
        c["description"] = desc[:64]
        c["_profile_aligned"] = True
        aligned.append(c)
    return aligned


_ADAPTIVE_VISIBLE_EVENT_FIELDS = (
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

_ADAPTIVE_HOUSEHOLD_STATEMENT_SYSTEM_PROMPT = (
    "Speak as the household described in the supplied resume before this demand-response event. "
    "Write one natural, concise first-person household statement that the controller can act on. "
    "The household may approve an adjustment, agree only under concrete conditions, ask for a different "
    "approach, or decline; do not presume cooperation or optimize for acceptance. Use only the visible "
    "household and event facts supplied here, preserve stated routines and service commitments, and do not "
    "invent savings, outcomes, personal details, scores, probabilities, or technical system identities. "
    "Do not offer a menu or label alternatives. Return only one JSON object with the single string field "
    '"statement".'
)

_ADAPTIVE_EVALUATOR_DISCLOSURE_RE = re.compile(
    r"\bevaluator(?:\s+(?:name|id|model))?\s*"
    r"(?:(?::|=|\bis\b)\s*|\s+)"
    r"(?:\"[^\"]{1,160}\"|'[^']{1,160}'|[^\s,;]+)",
    re.IGNORECASE,
)


def _protect_adaptive_household_language(value: object) -> object:
    """Protect harmless wording that credential regexes can otherwise overmatch."""
    if isinstance(value, dict):
        return {
            key: _protect_adaptive_household_language(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_protect_adaptive_household_language(item) for item in value]
    if isinstance(value, str):
        return re.sub(
            r"\bkey\s+routine\b",
            "key household routine",
            value,
            flags=re.IGNORECASE,
        )
    return value


def _restore_adaptive_household_language(value: object) -> object:
    """Restore only the harmless phrase protected at the privacy boundary."""
    if isinstance(value, dict):
        return {
            key: _restore_adaptive_household_language(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_restore_adaptive_household_language(item) for item in value]
    if isinstance(value, str):
        return re.sub(
            r"\bkey\s+household\s+routine\b",
            "key routine",
            value,
            flags=re.IGNORECASE,
        )
    return value


def _adaptive_roleplay_observable(value: object) -> object:
    """Apply the shared role-play privacy projection to any adaptive payload."""
    from energybridge.harness.roleplay import sanitize_household_resume_for_roleplay

    protected = _protect_adaptive_household_language(value)
    projected = sanitize_household_resume_for_roleplay(
        protected if isinstance(protected, dict) else {"visible_value": protected}
    )
    restored = _restore_adaptive_household_language(projected)
    if isinstance(value, dict):
        return restored if isinstance(restored, dict) else {}
    return (restored or {}).get("visible_value") if isinstance(restored, dict) else None


def _adaptive_roleplay_payload(
    persona: dict,
    vpp_context: dict,
    past_events: list,
) -> dict:
    """Build the same sanitized household resume used at the acceptance boundary."""
    from energybridge.harness.profile import build_household_resume
    appliances = persona.get("appliances")
    source_resume = build_household_resume(
        persona,
        appliance_config=appliances if isinstance(appliances, dict) else None,
        past_events=past_events,
    )
    resume = _adaptive_roleplay_observable(source_resume)
    if not isinstance(resume, dict):
        resume = {}
    # Event identifiers are run bookkeeping rather than lived household facts.
    for item in list(resume.get("relationship_history") or []):
        if isinstance(item, dict):
            item.pop("event_id", None)
    event_source = dict(vpp_context or {})
    visible_event = {
        key: event_source.get(key)
        for key in _ADAPTIVE_VISIBLE_EVENT_FIELDS
        if event_source.get(key) not in (None, "", [], {})
        or isinstance(event_source.get(key), bool)
        or event_source.get(key) == 0
    }
    if event_source.get("trigger_h") is not None and event_source.get("end_h") is not None:
        try:
            visible_event["trigger_hod"] = float(event_source["trigger_h"]) % 24.0
            visible_event["end_hod"] = float(event_source["end_h"]) % 24.0
        except (TypeError, ValueError):
            pass
        visible_event["window_semantics"] = (
            "Event trigger_h/end_h may be absolute simulation hours; household schedules use local "
            "hour-of-day. The event window is half-open, so an action beginning exactly at end_hod is "
            "outside the event."
        )
    projected_event = _adaptive_roleplay_observable({"event": visible_event})
    visible_event = (
        projected_event.get("event", {})
        if isinstance(projected_event, dict)
        else {}
    )
    return {
        "schema_version": "energybridge.household_statement.v2",
        "household_resume": resume,
        "event": visible_event,
    }


def _sanitize_adaptive_household_statement(value: object, *, limit: int = 800) -> str:
    """Sanitize model-authored free text without discarding ordinary household language."""
    from energybridge.harness.roleplay import sanitize_household_resume_for_roleplay

    text = " ".join(str(value or "").split())
    # A trailing period can make the generic credential detector interpret
    # "key routine." as a token-like value. Protect this harmless phrase while
    # applying the shared role-play sanitizer, then restore its natural wording.
    text = str(_protect_adaptive_household_language(text))
    text = _ADAPTIVE_EVALUATOR_DISCLOSURE_RE.sub("plan source omitted", text)
    projected = sanitize_household_resume_for_roleplay(
        {"biography": {"description": text}}
    )
    clean = str((projected.get("biography") or {}).get("description") or "")
    clean = _ADAPTIVE_EVALUATOR_DISCLOSURE_RE.sub("plan source omitted", clean)
    clean = str(_restore_adaptive_household_language(clean))
    clean = " ".join(clean.split())
    return clean[:limit].rstrip()


def _adaptive_statement_from_response(raw: object) -> str:
    """Extract and sanitize one statement from a role-play model response."""
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = "\n".join(
            line for line in text.splitlines()
            if not line.strip().startswith("```")
        ).strip()
    if text.startswith("["):
        raise ValueError("household statement response must not be a list")
    if "{" in text and "}" in text:
        start, end = text.find("{"), text.rfind("}")
        parsed = json.loads(text[start:end + 1])
        if not isinstance(parsed, dict):
            raise ValueError("household statement response must be an object")
        text = str(
            parsed.get("statement")
            or parsed.get("household_statement")
            or parsed.get("user_statement")
            or ""
        ).strip()
    statement = _sanitize_adaptive_household_statement(text)
    if not statement:
        raise ValueError("household statement response was empty after sanitization")
    return statement


def _sanitize_adaptive_strategy_candidates(raw: object) -> list[dict]:
    """Validate and sanitize the three model-authored human-menu options."""
    if not isinstance(raw, list) or len(raw) != 3:
        raise ValueError("adaptive strategy response must contain three options")
    expected_ids = ("A", "B", "C")
    result: list[dict] = []
    for expected_id, item in zip(expected_ids, raw):
        if not isinstance(item, dict) or str(item.get("id", "")).strip().upper() != expected_id:
            raise ValueError("adaptive strategy response must use A, B, C in order")
        clean = {"id": expected_id}
        for key, limit in (
            ("label", 120),
            ("description", 400),
            ("tradeoff", 300),
            ("user_pref", 800),
        ):
            text = _sanitize_adaptive_household_statement(item.get(key, ""), limit=limit)
            if not text:
                raise ValueError("adaptive strategy option was empty after privacy filtering")
            clean[key] = text
        result.append(clean)
    return result


def _adaptive_feedback_persona_from_resume(resume: dict) -> dict:
    """Re-express a sanitized resume in the persona shape used by roleplay_user."""
    biography = resume.get("biography") if isinstance(resume.get("biography"), dict) else {}
    daily_life = resume.get("daily_life") if isinstance(resume.get("daily_life"), dict) else {}
    service = (
        resume.get("comfort_and_service")
        if isinstance(resume.get("comfort_and_service"), dict)
        else {}
    )
    appliances: dict[str, dict] = {}
    for item in list(service.get("appliance_commitments") or []):
        if not isinstance(item, dict) or not item.get("device"):
            continue
        device = str(item.get("device"))
        appliances[device] = {
            str(key): value for key, value in item.items() if key != "device"
        }
    projected = _adaptive_roleplay_observable({
        "description": biography.get("description", ""),
        "schedule": daily_life.get("schedule", {}),
        "calendar": daily_life.get("calendar", {}),
        "appliances": appliances,
        "members": biography.get("household_members", []),
    })
    return projected if isinstance(projected, dict) else {}


def _sanitize_adaptive_feedback_result(result: dict) -> dict:
    """Sanitize all adaptive model-authored feedback text before persistence."""
    out = dict(result or {})
    out["comment"] = _sanitize_adaptive_household_statement(
        out.get("comment", ""), limit=1000
    )
    label = str(out.get("label", "neutral") or "neutral").strip().lower()
    out["label"] = label if label in _SCORE_LABELS else "neutral"
    projected = _adaptive_roleplay_observable({
        "zone_comfort_scores": out.get("zone_comfort_scores")
    })
    out["zone_comfort_scores"] = (
        projected.get("zone_comfort_scores")
        if isinstance(projected, dict)
        else None
    )
    return out


def _adaptive_statement_fallback(payload: dict) -> str:
    """Return one neutral conditional statement derived only from visible facts."""
    resume = payload.get("household_resume") or {}
    service = resume.get("comfort_and_service") or {}
    commitments = service.get("appliance_commitments") or []
    devices = [
        str(item.get("device", "")).replace("_", " ")
        for item in commitments
        if isinstance(item, dict) and item.get("present") and item.get("device")
    ]
    if devices:
        device_text = " and ".join(devices[:2])
        text = (
            "I would consider a brief, reversible event adjustment only if it protects our comfort "
            f"and keeps the timing of our {device_text} commitments intact."
        )
    else:
        text = (
            "I would consider a brief, reversible event adjustment only if it protects our comfort "
            "and keeps our ordinary household routine intact."
        )
    return _sanitize_adaptive_household_statement(text)


def _adaptive_calendar_trace(
    persona: dict,
    event_index: int,
    vpp_context: dict,
) -> dict:
    """Keep household-visible calendar facts in adaptive trace metadata."""
    raw_context = calendar_context_for_event(persona, event_index, vpp_context)
    projected = _adaptive_roleplay_observable({"calendar_context": raw_context})
    value = projected.get("calendar_context") if isinstance(projected, dict) else None
    return value if isinstance(value, dict) else {}


def _generate_adaptive_household_statement(
    persona: dict,
    vpp_context: dict,
    past_events: list,
) -> tuple[str, str]:
    """Ask the role-play model for one unconstrained household statement."""
    payload = _adaptive_roleplay_payload(persona, vpp_context, past_events)
    user_prompt = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    try:
        from energybridge.utils.config import load_llm_config
        roleplay_config = load_llm_config(
            prefix="ROLEPLAY_LLM",
            use_key="ROLEPLAY_USE_LLM",
            fallback_prefix="LLM",
        )
        if not roleplay_config.use_llm:
            raise RuntimeError("LLM off")
        from energybridge.llm.client import LLMClient

        response = LLMClient(config=roleplay_config).chat_with_metrics(
            _ADAPTIVE_HOUSEHOLD_STATEMENT_SYSTEM_PROMPT,
            user_prompt,
            max_retries=3,
            retry_base_delay=1.0,
        )
        return _adaptive_statement_from_response(response.get("text", "")), "roleplay_llm"
    except Exception as exc:
        print(
            "  [Household Statement] role-play unavailable; using visible-fact fallback "
            f"({type(exc).__name__})"
        )
        return _adaptive_statement_fallback(payload), "roleplay_visible_fact_fallback"


def get_user_preference_input(
    building: str,
    event_index: int,
    vpp_context: dict,
    past_events: list,
    persona: dict | None = None,
    log_path: Path | None = None,
    human_mode: bool = False,
) -> StrategyPreference:
    """Get user preference statement BEFORE agent acts on a VPP event.

    Adaptive automated runs ask the role-play household for one natural statement.
    Legacy runs and explicit ``human_mode`` retain the A/B/C menu contract.
    """
    if persona is None:
        persona = OFFICE_PERSONA if building == "office" else FAMILY_PERSONA
    else:
        persona = normalize_persona(persona)

    if _adaptive_harness_v2() and not human_mode:
        statement, source = _generate_adaptive_household_statement(
            persona,
            vpp_context,
            past_events,
        )
        calendar_context = _adaptive_calendar_trace(persona, event_index, vpp_context)
        selected_strategy = {
            "id": "household_statement",
            "label": "Household statement",
            "source": source,
            "preference_text": statement,
            "selection_meta": {
                "source": source,
                "single_statement": True,
                "privacy_sanitized": True,
            },
        }
        trace = {
            "event_index": event_index,
            "source": source,
            "candidates": [],
            "selected_strategy": selected_strategy,
            "calendar_context": calendar_context,
            "returned_user_pref": statement,
        }
        print(
            f"  [Household Statement | event={event_index}] "
            f"({source}) {statement}"
        )
        _log_and_return(
            log_path,
            {"id": "roleplay_household"},
            event_index,
            source,
            statement,
            extra={"selected_strategy": selected_strategy},
        )
        return StrategyPreference(statement, trace)

    # VPP override: comfort_sensitive persona may bypass strategy menu
    override_prob = persona.get("vpp_override_prob", 0.0)
    if override_prob > 0 and not _adaptive_harness_v2() and random.random() < override_prob:
        tags = persona.get("tags", {}) or {}
        ac_cfg = (persona.get("appliances", {}) or {}).get("ac", {}) or {}
        pref_max = float(ac_cfg.get("setpoint_preferred_max_c", persona.get("preferred_temp_max", 26.0)))
        _, _, window_text = _vpp_window_from_context(vpp_context)
        if _low_disruption_strategy_language(persona):
            override_msg = (
                "For this brief event, I only approve comfort-safe, routine-preserving actions: "
                f"keep AC at or below {pref_max:.1f}°C, do not change fixed appliance routines, "
                f"and keep the explanation about comfort and routine. If support is limited, just keep life smooth."
            )
        elif tags.get("control") == "confirm_required":
            override_msg = (
                "For this event, I confirm only a comfort-first or tiny adjustment plan: "
                f"keep the AC at or below {pref_max:.1f}°C, avoid noticeable temperature drift, "
                f"finish chores outside {window_text} if possible, and do not make any larger automatic changes."
            )
        else:
            override_msg = (
                "I really don't want the temperature to go above 26°C during this VPP event. "
                "My comfort is the top priority — please keep it below 26°C if at all possible."
            )
        _log_and_return(log_path, persona, event_index, "override_prefill", override_msg)
        return StrategyPreference(
            override_msg,
            {
                "event_index": event_index,
                "source": "override_prefill",
                "candidates": [],
                "selected_strategy": {
                    "id": "override",
                    "label": "override_prefill",
                    "source": "override_prefill",
                    "preference_text": override_msg,
                },
                "calendar_context": calendar_context_for_event(persona, event_index, vpp_context),
                "returned_user_pref": override_msg,
            },
        )

    # Step 1: Generate 3 candidate strategies
    calendar_context = (
        _adaptive_calendar_trace(persona, event_index, vpp_context)
        if _adaptive_harness_v2()
        else calendar_context_for_event(persona, event_index, vpp_context)
    )
    candidates = generate_vpp_strategy_candidates(
        building, event_index, vpp_context, past_events, persona,
        calendar_context=calendar_context,
    )

    # Step 2: Display all strategies
    appliance_presence = _appliance_control_profile(persona, vpp_context)
    menu_persona = None if _adaptive_harness_v2() else persona
    candidate_trace = _candidate_trace_items(
        candidates, appliance_presence, vpp_context, menu_persona
    )
    print(f"  ┌─[Strategy Candidates | VPP event {event_index}]{'─'*30}")
    for c in candidates:
        print(f"  │  [{c['id']}] {c['label']}  —  {c['description']}  ({c['tradeoff']})")
        plan_cn = _strategy_appliance_plan_cn(
            str(c.get('id', '')).upper(), appliance_presence, vpp_context, menu_persona
        )
        if plan_cn:
            print(f"  │      Appliance control: {plan_cn}")
    print(f"  └{'─'*56}")

    # Step 3: Select strategy
    rule_selected = (
        candidates[1]
        if _adaptive_harness_v2() and len(candidates) >= 2
        else _auto_select_strategy(candidates, persona)
    )
    selected = rule_selected
    roleplay_selection_meta = {}

    if human_mode:
        # --- Human-in-the-loop: prompt for selection ---
        auto_id = selected["id"]
        print(f"  ┌─[Choose Strategy | VPP event {event_index}]{'─'*28}")
        print(f"  │  Enter A / B / C to choose a strategy, or type a custom preference.")
        print(f"  │  Press Enter = use the auto recommendation [{auto_id}] {selected['label']}")
        print(f"  └{'─'*56}")
        try:
            raw_choice = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            raw_choice = ""

        if raw_choice.upper() in ("A", "B", "C"):
            override = next((c for c in candidates if c["id"] == raw_choice.upper()), None)
            if override:
                selected = override
                print(f"  [Strategy Selected  | event={event_index}] → [{selected['id']}] {selected['label']} (human)")
                pref = _compose_pref_with_appliances(selected, appliance_presence, vpp_context)
                trace = {
                    "event_index": event_index,
                    "source": "human",
                    "candidates": candidate_trace,
                    "selected_strategy": _selected_trace_item(
                        selected,
                        source="human",
                        preference_text=pref,
                        human_input=raw_choice,
                    ),
                    "calendar_context": calendar_context,
                    "returned_user_pref": pref,
                }
                _log_and_return(log_path, persona, event_index, "strategy_human",
                                pref, extra={"selected_id": selected["id"], "human_input": raw_choice,
                                             "candidates": candidate_trace,
                                             "selected_strategy": trace["selected_strategy"]})
                return StrategyPreference(pref, trace)
        elif raw_choice:
            # custom free-text preference
            if _adaptive_harness_v2():
                raw_choice = _sanitize_adaptive_household_statement(raw_choice, limit=800)
            print(f"  [Strategy Selected  | event={event_index}] → [custom] (human)")
            trace = {
                "event_index": event_index,
                "source": "human_custom",
                "candidates": candidate_trace,
                "selected_strategy": {
                    "id": "custom",
                    "label": "custom",
                    "source": "human_custom",
                    "human_input": raw_choice,
                    "preference_text": raw_choice,
                },
                "calendar_context": calendar_context,
                "returned_user_pref": raw_choice,
            }
            _log_and_return(log_path, persona, event_index, "strategy_human_custom",
                            raw_choice, extra={"human_input": raw_choice,
                                               "candidates": candidate_trace,
                                               "selected_strategy": trace["selected_strategy"]})
            return StrategyPreference(raw_choice, trace)
        else:
            print(f"  [Strategy Selected  | event={event_index}] → [{selected['id']}] {selected['label']} (human→auto)")

    else:
        try:
            selected, roleplay_reason, roleplay_selection_meta = _roleplay_select_strategy(
                candidates,
                persona,
                building=building,
                event_index=event_index,
                vpp_context=vpp_context,
                past_events=past_events,
                appliance_presence=appliance_presence,
                calendar_context=calendar_context,
            )
            reason_suffix = f" | {roleplay_reason}" if roleplay_reason else ""
            print(
                f"  [Strategy Selected  | event={event_index}] → "
                f"[{selected['id']}] {selected['label']} (auto roleplay_llm){reason_suffix}"
            )
        except Exception as exc:
            selected = rule_selected
            roleplay_selection_meta = {"source": "rule_fallback", "error": str(exc)[:120]}
            print(
                f"  [Strategy Selected  | event={event_index}] → "
                f"[{selected['id']}] {selected['label']} (auto rule_fallback: {str(exc)[:60]})"
            )

    pref = _compose_pref_with_appliances(selected, appliance_presence, vpp_context)
    mode_tag = "human_auto" if human_mode else roleplay_selection_meta.get("source", "auto")
    trace = {
        "event_index": event_index,
        "source": mode_tag,
        "candidates": candidate_trace,
        "selected_strategy": _selected_trace_item(
            selected,
            source=mode_tag,
            preference_text=pref,
            selection_meta=roleplay_selection_meta,
        ),
        "calendar_context": calendar_context,
        "returned_user_pref": pref,
    }
    _log_and_return(log_path, persona, event_index, f"strategy_{mode_tag}",
                    pref, extra={"selected_id": selected["id"],
                                 "selection_meta": roleplay_selection_meta,
                                 "calendar_context": calendar_context,
                                 "candidates": candidate_trace,
                                 "selected_strategy": trace["selected_strategy"]})
    return StrategyPreference(pref, trace)


# ---------------------------------------------------------------------------
# VPP Strategy Candidate Generation  (Feature 1)
# ---------------------------------------------------------------------------
_STRATEGY_DEFAULTS = [
    {
        "id": "A", "label": "Comfort first",
        "description": "Keep the setpoint near 25°C and accept higher energy use.",
        "tradeoff": "Best comfort, higher energy",
        "user_pref": (
            "Comfort first: please keep the temperature at 25°C. "
            "I'm willing to use more energy to stay comfortable during this VPP event."
        ),
    },
    {
        "id": "B", "label": "Balanced",
        "description": "Raise to 26°C and finish or defer shiftable appliances.",
        "tradeoff": "Small drift, about 15% savings",
        "user_pref": (
            "Balanced: I'm okay with a brief setpoint rise to 26°C and shifting "
            "shiftable appliances away from the VPP window to reduce peak load."
        ),
    },
    {
        "id": "C", "label": "Energy first",
        "description": "Raise to 27°C and delay all shiftable appliances until after VPP.",
        "tradeoff": "Noticeable drift, about 30% savings",
        "user_pref": (
            "Energy saving first: please raise the setpoint to 27°C and delay all "
            "shiftable appliances past the VPP window — I can tolerate the warmth."
        ),
    },
]


def generate_vpp_strategy_candidates(
    building: str,
    event_index: int,
    vpp_context: dict,
    past_events: list,
    persona: dict | None = None,
    calendar_context: dict | None = None,
) -> list[dict]:
    """Generate 3 VPP response strategy candidates (A=comfort, B=balanced, C=savings).

    Tries LLM first; falls back to hardcoded defaults.
    Each item: {id, label, description, tradeoff, user_pref}.
    'user_pref' is the English preference statement injected into the AC agent prompt.
    """
    if persona is None:
        persona = OFFICE_PERSONA if building == "office" else FAMILY_PERSONA
    else:
        persona = normalize_persona(persona)
    adaptive_v2 = _adaptive_harness_v2()

    try:
        from energybridge.utils.config import load_llm_config
        candidate_llm_config = (
            load_llm_config(
                prefix="ROLEPLAY_LLM",
                use_key="ROLEPLAY_USE_LLM",
                fallback_prefix="LLM",
            )
            if adaptive_v2
            else load_llm_config(use_key="USE_LLM")
        )
        if not candidate_llm_config.use_llm:
            raise RuntimeError("LLM off")
        from energybridge.llm.client import LLMClient

        _ap = _appliance_control_profile(persona, vpp_context)
        _present_appliances = [k for k in _APPLIANCE_KEYS if _profile_present(_ap, k)]
        _controllable_appliances = _profile_controllable_names(_ap)
        _fixed_appliances = _profile_fixed_names(_ap)
        _active_appliances_text = (
            "present=" + (",".join(_present_appliances) if _present_appliances else "none")
            + "; controllable=" + (",".join(_controllable_appliances) if _controllable_appliances else "none")
            + "; fixed=" + (",".join(_fixed_appliances) if _fixed_appliances else "none")
        )
        if _adaptive_harness_v2():
            roleplay_payload = _adaptive_roleplay_payload(
                persona, vpp_context, past_events
            )
            household_resume = roleplay_payload.get("household_resume") or {}
            relationship_history = list(
                household_resume.get("relationship_history") or []
            )[-8:]
            sys_prompt = (
                "You are simulating this particular household before a demand-response event. "
                "Draft three genuinely plausible commitments the household might make after considering its "
                "current day, prior experience, autonomy expectations, comfort, service deadlines, and grid/cost value. "
                "Do not force the options into comfort/balanced/savings archetypes and do not reuse stock phrasing. "
                "Options may include conditional approval, a narrow alternative, or declining discretionary action. "
                "Only physical safety, fixed routines, and explicit deadlines are hard constraints. Keep the household's "
                "voice and make each tradeoff concrete enough for a controller to act on. "
                'Return ONLY a JSON array of exactly 3 objects with ids "A", "B", and "C" and fields '
                '"id", "label", "description", "tradeoff", and "user_pref".'
            )
            user_msg = json.dumps(
                {
                    "household_resume": household_resume,
                    "event_index": event_index,
                    "event_context": roleplay_payload.get("event") or {},
                    "calendar_context": (
                        _adaptive_calendar_trace(persona, event_index, vpp_context)
                    ),
                    "appliance_control_boundary": _active_appliances_text,
                    "recent_events": relationship_history,
                },
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            )
            prompt_client = LLMClient(
                config_prefix="ROLEPLAY_LLM",
                use_key="ROLEPLAY_USE_LLM",
                fallback_prefix="LLM",
            )
        else:
            sp = persona.get("stable_preferences", {})
            past_summary = [
                {"event": e["id"], "score": e.get("score"), "comment": e.get("comment", "")[:50]}
                for e in (past_events or [])
            ]
            _cal = calendar_context or calendar_context_for_event(persona, event_index, vpp_context)
            _cal_brief = calendar_brief_for_prompt(_cal)
            _tags = persona.get("tags", {}) or {}
            _style_notes = []
            if _tags.get("price") in {"low_incentive", "price_indifferent"}:
                _style_notes.append("Use comfort, routine preservation, and low-risk support as the only user-facing rationale.")
                _style_notes.append("Write user_pref without financial or market language unless the user explicitly asks for it.")
            if _tags.get("control") == "confirm_required":
                _style_notes.append("Write each user_pref as an explicit event-level confirmation boundary, not as open-ended permission.")
            if _tags.get("comfort") == "temp_sensitive":
                _style_notes.append("Keep AC recommendations inside the preferred comfort range unless the user explicitly accepts a tiny drift.")
            _style_text = " ".join(_style_notes) if _style_notes else "Use the persona's normal communication style."
            _learned_notes = build_vpp_preference_memory_notes(past_events, persona)
            _learned_text = " ".join(f"- {note}" for note in _learned_notes) if _learned_notes else "No learned feedback rules yet."
            sys_prompt = (
                "You are an energy management strategy advisor for a smart home VPP demand-response system. "
                "Generate 3 distinct response strategies for the upcoming peak-shaving event. "
                "Strategy A = comfort-first, B = balanced, C = energy-saving. "
                "Tailor them to the user persona and explicitly include appliance control. "
                "Use the user's calendar as a hard preference context: do not propose schedules that would miss "
                "appointments, return-home comfort needs, EV departure readiness, hot-water deadlines, or required chores. "
                "If appliances are available, mention how to handle washer, dishwasher, dryer, water heater, and EV in strategy text. "
                "For fixed/non-DR-adjustable appliances, do not imply the controller can move them; describe them as fixed constraints. "
                'Return ONLY a JSON array of exactly 3 objects, each with keys: '
                '"id" ("A"/"B"/"C"), '
                '"label" (short English label), '
                '"description" (English action summary ≤80 chars), '
                '"tradeoff" (English tradeoff ≤60 chars), '
                '"user_pref" (English preference statement ≤140 chars, will be injected into AC agent prompt).'
            )
            user_msg = (
                f"Building={building}. VPP event #{event_index}. "
                f"Context: {json.dumps(vpp_context, ensure_ascii=False)}. "
                f"Active appliances: {_active_appliances_text}. "
                f"{_cal_brief} "
                f"Style/control notes: {_style_text} "
                f"Learned feedback rules from past events: {_learned_text} "
                f"Persona: comfort_priority={sp.get('comfort_priority', 0.5)}, "
                f"preferred_range={sp.get('preferred_temp_min', 24)}-{sp.get('preferred_temp_max', 26)}°C. "
                f"Past events: {json.dumps(past_summary, ensure_ascii=False)}."
            )
            prompt_client = LLMClient()
        resp = prompt_client.chat_with_metrics(
            sys_prompt, user_msg, max_retries=3, retry_base_delay=1.0
        )
        raw = resp["text"].strip()
        if raw.startswith("```"):
            raw = "\n".join(ln for ln in raw.splitlines() if not ln.startswith("```"))
        candidates = json.loads(raw)
        if isinstance(candidates, list) and len(candidates) == 3:
            required = ("id", "label", "description", "tradeoff", "user_pref")
            for c in candidates:
                if not all(k in c for k in required):
                    raise ValueError("strategy candidate is missing required fields")
            if _adaptive_harness_v2():
                candidates = _sanitize_adaptive_strategy_candidates(candidates)
            return _align_candidates_to_appliance_profile(candidates, _ap, vpp_context, persona)
        raise ValueError(f"unexpected shape: {type(candidates)}")
    except Exception as e:
        if _adaptive_harness_v2():
            safe_error = _sanitize_adaptive_household_statement(str(e), limit=120)
            print(
                "  [StrategyGen] role-play generation failed "
                f"({type(e).__name__}: {safe_error or 'details omitted'}), using defaults"
            )
        else:
            print(f"  [StrategyGen] LLM failed ({e}), using defaults")
        _ap = _appliance_control_profile(persona, vpp_context)
        return _align_candidates_to_appliance_profile(
            list(_STRATEGY_DEFAULTS), _ap, vpp_context, persona
        )  # copy so mutations don't affect template


def _auto_select_strategy(candidates: list[dict], persona: dict) -> dict:
    """Select strategy based on persona comfort_priority (A≥0.65 / B 0.40-0.65 / C<0.40)."""
    sp = persona.get("stable_preferences", {})
    cp = float(sp.get("comfort_priority", 0.5))
    if cp >= 0.65:
        sel_id = "A"
    elif cp >= 0.40:
        sel_id = "B"
    else:
        sel_id = "C"
    return next((c for c in candidates if c["id"] == sel_id), candidates[1])


def _roleplay_select_strategy(
    candidates: list[dict],
    persona: dict,
    *,
    building: str,
    event_index: int,
    vpp_context: dict,
    past_events: list,
    appliance_presence: dict,
    calendar_context: dict | None = None,
) -> tuple[dict, str, dict]:
    """Ask the role-play user LLM to choose one candidate strategy."""
    from energybridge.llm.roleplay_user import RoleplayUserSimulator

    strategy_options = []
    for idx, candidate in enumerate(candidates, start=1):
        strategy_options.append({
            "index": idx,
            "id": candidate.get("id"),
            "label": candidate.get("label"),
            "description": candidate.get("description"),
            "tradeoff": candidate.get("tradeoff"),
            "user_pref": candidate.get("user_pref"),
            "appliance_plan_cn": _strategy_appliance_plan_cn(
                str(candidate.get("id", "")).upper(), appliance_presence, vpp_context, persona
            ),
            "appliance_pref_en": _strategy_appliance_pref_en(
                str(candidate.get("id", "")).upper(), appliance_presence, vpp_context, persona
            ),
        })
    scenario = {
        "building": building,
        "event_index": event_index,
        "vpp_context": vpp_context,
        "calendar_context": calendar_context or calendar_context_for_event(persona, event_index, vpp_context),
        "active_appliances": [k for k in _APPLIANCE_KEYS if _profile_present(appliance_presence, k)],
        "controllable_appliances": _profile_controllable_names(appliance_presence),
        "fixed_non_dr_adjustable_appliances": _profile_fixed_names(appliance_presence),
        "past_events": [
            {
                "id": e.get("id"),
                "score": e.get("score"),
                "label": e.get("label"),
                "comment": e.get("comment", ""),
                "user_input": e.get("user_input", ""),
            }
            for e in (past_events or [])
        ],
        "learned_preference_rules": build_vpp_preference_memory_notes(past_events, persona),
        "instruction": (
            "Choose the option this home user would approve before the VPP event. "
            "The returned choice will be injected into the home agent prompt as the user's live preference."
        ),
    }
    result = RoleplayUserSimulator().choose_strategy(
        persona=persona,
        turn_index=event_index,
        scenario=scenario,
        strategy_options=strategy_options,
    )
    data = result.get("data", {})
    selected_index = int(data.get("selected_index", 0))
    if not 1 <= selected_index <= len(candidates):
        raise ValueError(f"invalid selected_index={selected_index}")
    selected = candidates[selected_index - 1]
    reason = str(data.get("reason", "")).strip()
    return selected, reason, {
        "selected_index": selected_index,
        "approved": data.get("approved"),
        "reason": reason,
        "metrics": result.get("metrics", {}),
        "source": "roleplay_llm",
    }


def _log_and_return(log_path, persona, event_index, source, text, extra: dict | None = None):
    if log_path:
        persona_id = (
            "roleplay_household"
            if _adaptive_harness_v2()
            else persona.get("id", "?")
        )
        entry = {
            "ts": datetime.datetime.utcnow().isoformat(),
            "persona": persona_id,
            "event_index": event_index,
            "type": "user_input",
            "source": source,
            "text": text,
        }
        if extra:
            entry.update(extra)
        _append_dialogue_log(log_path, entry)


def score_user_preference(
    building: str,
    method: str,
    mean_temp_c: float,
    pmv_ok_fraction: float,
    energy_kwh_per_day: float,
    agent_setpoint_c=None,
    event_index: int = 1,
    user_preference_text: str = "",
    agent_reason: str = "",
    zone_group_temps: dict | None = None,
    persona: dict | None = None,
    washer_completed: bool = True,
    washer_during_vpp: bool = False,
    appliance_summary: dict | None = None,
    log_path: Path | None = None,
    human_mode: bool = False,
    vpp_context: dict | None = None,
    vpp_result_context: dict | None = None,
    policy_control_context: dict | None = None,
):
    """Score user satisfaction using roleplay LLM (fallback: rule-based).

    Returns dict with: satisfaction_score, comfort_score, energy_score, vpp_score,
    satisfaction_label, comment, source.
    M2: zone_group_temps = {group: mean_temp} for office zone-aware scoring.
    Persona-aware: uses persona's scoring_weights for weighted overall.
    """
    if persona is None:
        persona = OFFICE_PERSONA if building == "office" else FAMILY_PERSONA
    else:
        persona = normalize_persona(persona)
    adaptive_v2 = _adaptive_harness_v2()
    raw_calendar_context = calendar_context_for_event(
        persona,
        event_index,
        vpp_context or {"hour": 18.0, "duration_h": 1.0},
    )
    if adaptive_v2:
        projected_calendar = _adaptive_roleplay_observable({
            "calendar_context": raw_calendar_context
        })
        calendar_context = (
            projected_calendar.get("calendar_context", {})
            if isinstance(projected_calendar, dict)
            else {}
        )
    else:
        calendar_context = raw_calendar_context
    calendar_brief = calendar_brief_for_prompt(calendar_context)
    tags = persona.get("tags", {}) or {}
    schedule = persona.get("schedule", {}) or {}
    appliance_cfg = persona.get("appliances", {}) or {}
    fixed_appliances = _fixed_appliance_constraints(persona)
    ac_cfg = appliance_cfg.get("ac", {}) or {}
    pref_min = float(ac_cfg.get("setpoint_preferred_min_c", persona.get("preferred_temp_min", 24.0)))
    pref_max = float(ac_cfg.get("setpoint_preferred_max_c", persona.get("preferred_temp_max", 26.0)))
    pref_tol = float(ac_cfg.get("temp_tolerance_c", persona.get("temp_tolerance", 1.0)))
    protective_user = bool(
        not adaptive_v2
        and (
            tags.get("schedule") == "caregiver"
            or tags.get("comfort") == "temp_sensitive"
            or tags.get("control") in {"confirm_required", "low_auto_accept", "privacy_sensitive"}
            or bool(schedule.get("vulnerable_members"))
        )
    )

    # Human-in-the-loop scoring: print event summary and ask for terminal input
    if human_mode:
        sp_str = f"{agent_setpoint_c:.1f}°C" if agent_setpoint_c else f"{mean_temp_c:.1f}°C"
        print(f"  ╔═[VPP Event {event_index} Satisfaction Score]{'═'*32}")
        print(f"  ║  VPP-window mean indoor temp: {mean_temp_c:.1f}°C   setpoint: {sp_str}")
        print(f"  ║  Today's energy: {energy_kwh_per_day:.2f} kWh   comfort pass rate: {pmv_ok_fraction*100:.0f}%")
        if calendar_context.get("available"):
            print(f"  ║  Calendar constraints: {calendar_context.get('summary', '')[:80]}")
        if agent_reason:
            print(f"  ║  Controller explanation: {agent_reason[:160]}")
        print(f"  ╚{'═'*52}")
        print("  Rate this VPP handling (1=very dissatisfied / 5=very satisfied), press Enter=3:")
        try:
            raw_score = input("  > ").strip()
            score = max(1, min(5, int(raw_score))) if raw_score.isdigit() else 3
        except (EOFError, KeyboardInterrupt):
            score = 3
        print("  Optional: leave brief feedback, or press Enter to skip:")
        try:
            comment = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            comment = ""
        if adaptive_v2:
            comment = _sanitize_adaptive_household_statement(comment, limit=1000)
        print(f"  [Human Score Selected | event={event_index}] → {score}/5 | {comment or '—'}")
        return {
            "score": score, "comfort_score": score, "energy_score": score,
            "vpp_score": score, "label": "human", "comment": comment or "—",
            "zone_comfort_scores": None, "source": "human",
        }

    policy_control_context = dict(policy_control_context or {})
    acceptance_gate = policy_control_context.get("vpp_acceptance_gate")
    identity_tokens = [
        str(method or ""),
        str(policy_control_context.get("method") or ""),
        str(policy_control_context.get("objective_source") or ""),
    ]
    if isinstance(acceptance_gate, dict):
        prompt_audit = (
            acceptance_gate.get("prompt_audit")
            if isinstance(acceptance_gate.get("prompt_audit"), dict)
            else {}
        )
        prompt_metrics = (
            acceptance_gate.get("prompt_gate_metrics")
            if isinstance(acceptance_gate.get("prompt_gate_metrics"), dict)
            else {}
        )
        identity_tokens.extend([
            str(acceptance_gate.get("method") or ""),
            str(prompt_audit.get("roleplay_model") or ""),
            str(prompt_metrics.get("model") or ""),
            str(prompt_metrics.get("provider") or ""),
        ])

    method_blind_agent_reason = _method_blind_observable_text(
        agent_reason,
        identities=identity_tokens,
        limit=1200,
    )
    explanation_is_user_facing = _is_user_facing_controller_explanation(
        method_blind_agent_reason if adaptive_v2 else agent_reason
    )
    judgement_context_key = (
        "household_judgement_context" if adaptive_v2 else "roleplay_scoring_contract"
    )
    home_state = {
        "indoor_temp": round(mean_temp_c, 1),
        "hvac_setpoint": agent_setpoint_c or round(mean_temp_c, 1),
        "energy_per_day_kwh": round(energy_kwh_per_day, 2),
        "washer_completed": washer_completed,
        "washer_during_vpp": washer_during_vpp,
        "calendar_context": calendar_context,
        "fixed_non_dr_adjustable_appliances": fixed_appliances,
        "event_preference_counts_as_confirmation": bool(user_preference_text),
        judgement_context_key: {
            "price_sensitivity": (
                "Treat price/cost-aware scheduling as a real satisfaction factor. "
                "No-disruption cost savings can raise energy_score and may raise overall score "
                "when comfort, consent, and required services are preserved."
            ),
            "explanation_credit": (
                "A concrete, truthful controller explanation may increase satisfaction when it "
                "connects comfort, appliance completion, EV/hot-water readiness, VPP-window "
                "avoidance, and cost/price benefit. It must not excuse hard service or comfort failures. "
                "Do not give explanation credit when the controller explanation is empty, a solver/objective "
                "trace, or a code-like metric string."
            ),
            "learning_feedback": (
                "Write detailed feedback. If a later event fixes the exact complaint, score more "
                "favorably; if the same issue repeats, score more harshly."
            ),
        },
        (
            "household_facing_explanation_available"
            if adaptive_v2
            else "controller_explanation_is_user_facing"
        ): bool(explanation_is_user_facing),
    }
    if not adaptive_v2:
        home_state["protective_user_mode"] = protective_user
    if adaptive_v2:
        home_state[judgement_context_key]["acceptance_satisfaction_consistency"] = (
            "The pre-event role-play judgement concerns the offered proposal. If that offer was accepted and "
            "executed, overall satisfaction should normally move in the same direction as its continuous "
            "willingness. If it was rejected, realised comfort, energy, appliance service, and VPP outcomes come "
            "from the ordinary fallback instead; they may change satisfaction, but the comment must distinguish "
            "the rejected offer from the fallback experience. Do not mechanically convert probability into a "
            "rating. If realised outcomes make the rating materially depart from the earlier willingness, explain "
            "the new evidence and direction naturally."
        )
    if agent_reason:
        if adaptive_v2 and explanation_is_user_facing:
            home_state["household_facing_explanation"] = method_blind_agent_reason
        elif not adaptive_v2:
            home_state["controller_explanation_excerpt"] = str(agent_reason)[:1200]
    if agent_reason and not explanation_is_user_facing:
        home_state["controller_explanation_note"] = (
            "No concrete household-facing explanation was supplied. A technical trace existed, but its controller "
            "identity and implementation details are hidden and must not earn explanation/communication credit."
            if adaptive_v2
            else "The provided controller explanation is not household-facing. It may be used as a technical trace, "
            "but it must not earn explanation/communication credit."
        )
    if vpp_result_context:
        home_state["vpp_result"] = vpp_result_context
    if adaptive_v2 and user_preference_text:
        home_state["event_user_statement"] = user_preference_text
    appliance_summary = appliance_summary or {}
    observable_acceptance = _observable_acceptance_judgement(
        acceptance_gate,
        identities=identity_tokens,
    )
    if adaptive_v2 and observable_acceptance is not None:
        home_state["pre_event_offer_judgement"] = observable_acceptance
        live_offer_accepted = observable_acceptance.get("accepted")
        home_state["offer_execution_status"] = {
            "offered_plan_accepted": live_offer_accepted,
            "realised_outcomes_plan": (
                "offered_plan"
                if live_offer_accepted is True
                else "ordinary_fallback_plan"
                if live_offer_accepted is False
                else "unknown_because_household_judgement_was_unavailable"
            ),
        }
    skipped_devices = [
        name for name, info in appliance_summary.items()
        if name in {"washer", "dishwasher", "dryer"} and bool(info.get("present")) and bool(info.get("skipped"))
    ]
    skipped_task_count = len(skipped_devices)
    if skipped_devices:
        home_state["skipped_devices"] = skipped_devices
        home_state["service_rule_violated"] = True

    wh_info = appliance_summary.get("water_heater", {}) if appliance_summary else {}
    water_heater_during_vpp = bool(wh_info.get("present") and wh_info.get("ran_during_vpp"))
    water_heater_not_ready = bool(
        wh_info.get("present")
        and (wh_info.get("bath_check_done") is True or "ready_at_bath" in wh_info)
        and wh_info.get("ready_at_bath") is False
    )
    ev_info = appliance_summary.get("ev", {}) if appliance_summary else {}
    ev_target_missed = bool(
        ev_info.get("present")
        and "target_reached" in ev_info
        and ev_info.get("target_reached") is False
    )
    ev_target_reached = bool(
        ev_info.get("present")
        and "target_reached" in ev_info
        and ev_info.get("target_reached") is True
    )
    if water_heater_during_vpp:
        home_state["water_heater_during_vpp"] = True
    if water_heater_not_ready:
        home_state["water_heater_not_ready_at_bath"] = True
        home_state["service_rule_violated"] = True
    if ev_target_missed:
        home_state["ev_target_not_reached"] = True
        home_state["service_rule_violated"] = True
    invalid_ev_windows = _invalid_ev_policy_windows(policy_control_context, persona)
    invalid_ev_windows_hard_failure = bool(invalid_ev_windows and not ev_target_reached)
    if invalid_ev_windows:
        home_state["invalid_ev_policy_windows"] = invalid_ev_windows
        if invalid_ev_windows_hard_failure:
            home_state["service_rule_violated"] = True
        else:
            home_state["invalid_ev_policy_windows_repaired_by_later_policy"] = True
    unserved_service_devices = []
    if water_heater_not_ready:
        unserved_service_devices.append("water_heater")
    if ev_target_missed:
        unserved_service_devices.append("ev")
    if invalid_ev_windows_hard_failure:
        unserved_service_devices.append("ev")
    unserved_service_devices = list(dict.fromkeys(unserved_service_devices))
    nonfixed_appliances_during_vpp = [
        name
        for name, info in appliance_summary.items()
        if name in {"washer", "dishwasher", "dryer", "water_heater", "ev"}
        and bool(info.get("present"))
        and bool(info.get("ran_during_vpp"))
        and name not in fixed_appliances
    ]
    policy_action_space = set(policy_control_context.get("action_space_services") or [])
    emitted_policy_services = set(policy_control_context.get("emitted_services") or [])
    if not emitted_policy_services:
        emitted_policy_services |= _services_from_actions(policy_control_context.get("vpp_trigger_actions"))
        for decision in policy_control_context.get("day_decisions") or []:
            if isinstance(decision, dict):
                emitted_policy_services |= _services_from_actions(decision.get("actions"))
                emitted_policy_services |= _services_from_actions(decision.get("raw_appliance_actions"))
    present_required_services: set[str] = set()
    for name, info in appliance_summary.items():
        if name not in _APPLIANCE_KEYS or not isinstance(info, dict) or not bool(info.get("present")):
            continue
        if name in {"washer", "dishwasher", "dryer"}:
            if (
                bool(info.get("skipped"))
                or bool(info.get("ran_during_vpp"))
                or info.get("completed") is False
            ):
                present_required_services.add(name)
        elif name == "water_heater":
            if bool(info.get("ran_during_vpp")) or (
                (info.get("bath_check_done") is True or "ready_at_bath" in info)
                and info.get("ready_at_bath") is False
            ):
                present_required_services.add(name)
        elif name == "ev":
            if bool(info.get("ran_during_vpp")) or (
                "target_reached" in info and info.get("target_reached") is False
            ):
                present_required_services.add(name)
    if policy_action_space:
        present_required_services |= {
            name for name, info in appliance_summary.items()
            if name in policy_action_space
            and isinstance(info, dict)
            and bool(info.get("present"))
        }
    unsupported_policy_services = sorted(
        present_required_services - policy_action_space
    ) if policy_action_space else []
    missing_policy_services = sorted(
        present_required_services - emitted_policy_services
    ) if policy_control_context else []
    ev_policy_explicit = bool(
        ev_info.get("present")
        and ev_target_reached
        and "ev" in emitted_policy_services
    )
    if policy_control_context:
        action_evidence_key = (
            "observed_action_evidence" if adaptive_v2 else "policy_control_context"
        )
        home_state[action_evidence_key] = {
            "action_space_services": sorted(policy_action_space),
            "emitted_services": sorted(emitted_policy_services),
            "present_required_services": sorted(present_required_services),
            (
                "unsupported_services" if adaptive_v2 else "unsupported_policy_services"
            ): unsupported_policy_services,
            (
                "missing_services" if adaptive_v2 else "missing_policy_services"
            ): missing_policy_services,
            "vpp_trigger_actions": policy_control_context.get("vpp_trigger_actions", {}),
            "occupancy_decisions": policy_control_context.get("occupancy_decisions", []),
        }
        if not adaptive_v2:
            home_state[action_evidence_key].update({
                "method": policy_control_context.get("method", method),
                "objective_source": policy_control_context.get("objective_source", ""),
            })

    policy_scored_method = (
        method in (
            "agent", "eb_rule_milp", "agent_pmv", "EnergyBridge", "hema_agent",
            "rl", "rl_ppo_pref_v2", "mpc", "mpc_dynamic", "rule_milp",
        )
        or str(method).startswith("rl_")
    )
    if (adaptive_v2 and agent_setpoint_c is not None) or (
        not adaptive_v2 and policy_scored_method and agent_setpoint_c
    ):
        if adaptive_v2:
            # The V2 resident judges observed facts, never an algorithm brand.
            controller = "Household controller"
        elif method == "rl" or str(method).startswith("rl_"):
            controller = "RL baseline"
        elif method == "hema_agent":
            controller = "HEMA Agent baseline"
        elif method in ("mpc", "mpc_dynamic"):
            controller = "MPC baseline"
        elif method == "rule_milp":
            controller = "Rule+MILP baseline"
        elif method == "eb_rule_milp":
            controller = "EnergyBridge agent"
        else:
            controller = "LLM agent"
        within_preferred = pref_min - pref_tol <= float(agent_setpoint_c) <= pref_max + pref_tol
        if protective_user and within_preferred:
            action_name = "hold_or_minimal_hvac"
            rationale = (
                f"{controller} kept the cooling setpoint at {agent_setpoint_c}°C, within this "
                f"protective user's preferred range ({pref_min:.1f}-{pref_max:.1f}°C). "
                "Do not treat this status/setpoint log as an aggressive DR temperature raise; "
                "score based on actual comfort, whether the event-specific user preference/confirmation was followed, "
                "consent sensitivity, and preserved routines."
            )
        else:
            action_name = "set_hvac_temperature"
            rationale = (
                f"{controller} set cooling setpoint to {agent_setpoint_c}°C during VPP DR event."
            )
        if adaptive_v2 and agent_reason and explanation_is_user_facing:
            rationale += f" | Household-facing explanation: {method_blind_agent_reason[:400]}"
        elif adaptive_v2 and agent_reason:
            rationale += (
                " | No concrete household-facing explanation was supplied; a hidden technical trace must not earn "
                "clarity, reassurance, consent-handling, or price-explanation credit."
            )
        elif agent_reason:
            rationale += f" Controller explanation: {agent_reason[:100]}"
            if not explanation_is_user_facing:
                rationale += (
                    " | The controller explanation is only a technical objective/solver trace, not a user-facing "
                    "explanation; do not praise clarity, reassurance, consent handling, or price explanation."
                )
        if calendar_context.get("available"):
            rationale += f" | User calendar context: {calendar_brief[:240]}"
        if user_preference_text:
            rationale += (
                f" | User had expressed/confirmed for this event: {user_preference_text[:120]}. "
                "Treat this as the event-specific confirmation boundary for confirm-required personas."
            )
        if fixed_appliances:
            rationale += (
                " | Fixed/non-DR-adjustable appliances that the controller cannot move: "
                + ", ".join(fixed_appliances)
                + ". Do not blame the controller for their fixed operation; evaluate whether controllable actions stayed within the user's consent and comfort."
            )
        if not adaptive_v2 and _low_disruption_strategy_language(persona):
            rationale += (
                " | This is a low-disruption user: evaluate mainly comfort, consent, routine smoothness, and whether the explanation stayed low-pressure. "
                "Do not invent a financial or market pitch if the controller explanation did not contain one. "
                "weak VPP contribution from fixed loads is not by itself a communication failure."
            )
        if vpp_result_context:
            ratio = vpp_result_context.get("achievement_ratio")
            achieved = vpp_result_context.get("achieved")
            achieved_text = "unknown" if achieved is None else str(bool(achieved))
            target_mode = vpp_result_context.get("target_mode", "unknown")
            success_text = vpp_result_context.get("success_text", "")
            achievement_text = vpp_result_context.get("achievement_text", "")
            if target_mode == "non_ac_appliance_avoidance":
                non_ac = vpp_result_context.get("non_ac_appliances_during_vpp") or []
                rationale += (
                    f" | Event-level VPP result: mode={target_mode}, achieved={achieved_text}, "
                    f"non_ac_appliances_during_vpp={non_ac or 'none'}, "
                    f"actual_window_kwh={vpp_result_context.get('actual_kwh', 'n/a')}kWh diagnostic only; "
                    f"{success_text}. {achievement_text}. "
                    "Do not compare against shed/cap targets when judging VPP success."
                )
            elif ratio is not None:
                rationale += (
                    f" | Event-level VPP result: legacy mode={target_mode}, achieved={achieved_text}, "
                    f"legacy_metric={ratio}; {success_text}. {achievement_text}. "
                    "For current benchmarks, do not compare shed/cap targets; use non-AC appliance avoidance."
                )
            else:
                rationale += (
                    f" | Event-level VPP result: mode={target_mode}, achieved={achieved_text}, "
                    f"actual_window_kwh={vpp_result_context.get('actual_kwh', 'n/a')}kWh diagnostic only; "
                    f"{success_text}. {achievement_text}. "
                    "For current benchmarks, VPP success is non-AC appliance avoidance, not shed/cap target comparison."
                )
        if not washer_completed:
            rationale += " | NOTE: washing machine task was NOT completed today."
        if washer_during_vpp:
            rationale += " | NOTE: washing machine ran DURING VPP window (added peak load)."
        if water_heater_during_vpp:
            if "water_heater" in fixed_appliances:
                rationale += (
                    " | NOTE: water heater operated during the VPP window because this persona's "
                    "bath routine/device is fixed/non-DR-adjustable; this preserves service but weakens grid response. "
                    "This is a fixed-load limitation, not an Agent scheduling violation."
                )
            else:
                rationale += " | NOTE: water heater ran DURING VPP window (added peak load)."
        if water_heater_not_ready:
            rationale += (
                " | CRITICAL SERVICE VIOLATION: water heater was present but was not ready at the required bath/shower time. "
                "The user should strongly penalize this service failure even if VPP appliance avoidance succeeded."
            )
        if invalid_ev_windows_hard_failure:
            rationale += (
                " | CRITICAL SERVICE VIOLATION: EV was present but the emitted charge window is infeasible for the "
                "arrival-day charging need "
                f"(invalid windows: {', '.join(invalid_ev_windows)}). "
                "The user, especially an EV owner/commuter, should strongly penalize this policy failure even if the VPP window was clean."
            )
        elif invalid_ev_windows:
            rationale += (
                " | NOTE: an earlier EV charge window was infeasible for the arrival-day charging need "
                f"(invalid windows: {', '.join(invalid_ev_windows)}), but final EV target SOC was reached; "
                "treat this as policy clarity/robustness weakness rather than an unmet service target."
            )
        if ev_target_missed:
            rationale += (
                " | CRITICAL SERVICE VIOLATION: EV was present but did not reach the required target SOC by the departure/check time. "
                "The user, especially an EV owner/commuter, should strongly penalize this service failure even if the VPP window was clean."
            )
        if skipped_devices:
            rationale += (
                " | CRITICAL SERVICE VIOLATION: agent skipped required appliance task(s): "
                + ", ".join(skipped_devices)
                + ". User should be very dissatisfied; this should score at the lowest level."
            )
        if policy_control_context:
            policy_evidence_label = (
                "Observable policy action evidence"
                if adaptive_v2
                else "Policy action evidence for this method"
            )
            policy_attribution = (
                "controller-controlled"
                if adaptive_v2
                else "method-controlled"
            )
            rationale += (
                f" | {policy_evidence_label}: "
                f"action_space={sorted(policy_action_space)}, "
                f"emitted_services={sorted(emitted_policy_services)}, "
                f"present_required_services={sorted(present_required_services)}, "
                f"vpp_trigger_actions={policy_control_context.get('vpp_trigger_actions', {})}, "
                f"occupancy_ac_modes={policy_control_context.get('occupancy_decisions', [])}. "
                f"Only count appliance service as {policy_attribution} when it appears in emitted policy actions; "
                "do not credit baseline routines or simulator default completion as policy success."
            )
            if ev_policy_explicit:
                ev_attribution = "controller" if adaptive_v2 else "method"
                rationale += (
                    f" EV factual evidence: the {ev_attribution} emitted an explicit EV charging action/window "
                    "and the simulator reached the EV target SOC; do not describe the EV schedule as "
                    "missing, absent, or not explicit."
                )
            if missing_policy_services:
                if adaptive_v2:
                    rationale += (
                        " | Observable appliance-strategy gap: these present controllable services had no "
                        "emitted controller action: "
                        + ", ".join(missing_policy_services)
                        + ". Treat the missing action as evidence when judging whether the controller addressed "
                        "this household's needs. Do not automatically convert it into a missed service outcome "
                        "when the supplied outcome facts show completion, and do not credit ordinary/default "
                        "completion as a controller contribution."
                    )
                else:
                    rationale += (
                        " | CRITICAL APPLIANCE STRATEGY FAILURE: required present controllable appliances "
                        "have no emitted policy strategy/action: "
                        + ", ".join(missing_policy_services)
                        + ". This is a service failure. The role-play user must assign a punitive low overall score, normally 1/5, even if comfort or VPP energy target looks acceptable."
                    )
        if not adaptive_v2 and (
            str(method).startswith("rl")
            or str(policy_control_context.get("objective_source", "")).startswith("rl_")
        ):
            rationale += (
                " | CRITICAL RL SCORING RULE: this benchmark evaluates the raw RL policy only. "
                "Do not give RL credit for fallback/default appliance behavior or routine completion that was not "
                "emitted by the RL action. If present required appliances are outside the RL action space or missing "
                "from emitted RL actions, treat the task as incomplete and score it as a punitive service failure "
                "even if the simulator service outcome later looks completed."
            )
            if unsupported_policy_services:
                rationale += " Unsupported-by-RL required services: " + ", ".join(unsupported_policy_services) + "."
            if missing_policy_services:
                rationale += " Required services with no emitted RL control action: " + ", ".join(missing_policy_services) + "."
        control_plan = {
            "action": action_name,
            "setpoint": agent_setpoint_c,
            "rationale": rationale,
        }
    else:
        control_plan = {
            "action": "set_hvac_temperature",
            "setpoint": round(mean_temp_c, 1),
            "rationale": (
                f"PMV reactive controller: mean_temp={mean_temp_c:.1f}°C, "
                f"pmv_comfort_ok={pmv_ok_fraction*100:.0f}%. No VPP adaptation."
            ),
        }
        if calendar_context.get("available"):
            control_plan["rationale"] += f" | User calendar context: {calendar_brief[:240]}"

    safety = {"status": "approved", "reason": "Within safe operation bounds."}

    if missing_policy_services and not adaptive_v2:
        result = {
            "score": 1,
            "comfort_score": 2,
            "energy_score": 1,
            "vpp_score": 1,
            "label": "very_dissatisfied",
            "comment": (
                "Required present controllable appliance(s) had no emitted policy strategy/action "
                f"(missing: {', '.join(missing_policy_services)}; "
                f"unsupported_by_action_space: {', '.join(unsupported_policy_services) or 'none'}). "
                "Baseline routine/default completion was not credited as method success."
            ),
            "zone_comfort_scores": None,
            "source": "roleplay_llm",
            "policy_service_guard": {
                "missing_policy_services": missing_policy_services,
                "unsupported_policy_services": unsupported_policy_services,
                "emitted_policy_services": sorted(emitted_policy_services),
                "present_required_services": sorted(present_required_services),
            },
        }
        if not adaptive_v2 and (
            str(method).startswith("rl")
            or str(policy_control_context.get("objective_source", "")).startswith("rl_")
        ):
            result["rl_policy_service_guard"] = result["policy_service_guard"]
        if log_path:
            _append_dialogue_log(log_path, {
                "ts": datetime.datetime.utcnow().isoformat(),
                "persona": persona.get("id", "?"),
                "event_index": event_index,
                "type": "feedback",
                "method": method,
                "scores": {
                    "overall": result["score"],
                    "comfort": result["comfort_score"],
                    "energy": result["energy_score"],
                    "vpp": result["vpp_score"],
                },
                "comment": result.get("comment", ""),
                "washer_completed": washer_completed,
                "washer_during_vpp": washer_during_vpp,
                "skipped_devices": skipped_devices,
                "source": result.get("source", "?"),
            })
        return result

    # M2: build zone group context for office
    zone_ctx = None
    if zone_group_temps and building == "office":
        zone_ctx = {g: {"mean_temp_c": round(t, 1)} for g, t in zone_group_temps.items()}

    # Build the feedback boundary. Adaptive runs expose one sanitized resume
    # projection plus sanitized observable experience; legacy remains frozen.
    if adaptive_v2:
        resume = (_adaptive_roleplay_payload(
            persona, vpp_context or {}, []
        ).get("household_resume") or {})
        rp_persona = _adaptive_feedback_persona_from_resume(resume)
        visible_feedback_payload = _adaptive_roleplay_observable({
            "selected_strategy": home_state,
            "experienced_plan": control_plan,
            "safety_report": safety,
            "zone_context": zone_ctx or {},
        })
        visible_feedback_payload = (
            visible_feedback_payload
            if isinstance(visible_feedback_payload, dict)
            else {}
        )
        model_home_state = visible_feedback_payload.get("selected_strategy") or {}
        model_control_plan = visible_feedback_payload.get("experienced_plan") or {}
        model_safety = visible_feedback_payload.get("safety_report") or {}
        model_zone_ctx = visible_feedback_payload.get("zone_context") or None
    else:
        rp_persona = dict(persona)
        if "roleplay_user_prompt" in persona:
            rp_persona["summary"] = persona["roleplay_user_prompt"]
        model_home_state = home_state
        model_control_plan = control_plan
        model_safety = safety
        model_zone_ctx = zone_ctx

    try:
        from energybridge.utils.config import load_llm_config
        feedback_llm_config = (
            load_llm_config(
                prefix="ROLEPLAY_LLM",
                use_key="ROLEPLAY_USE_LLM",
                fallback_prefix="LLM",
            )
            if adaptive_v2
            else load_llm_config(use_key="USE_LLM")
        )
        if not feedback_llm_config.use_llm:
            raise RuntimeError("LLM off")
        from energybridge.llm.roleplay_user import RoleplayUserSimulator
        r = RoleplayUserSimulator().generate_feedback(
            persona=rp_persona,
            turn_index=event_index,
            selected_strategy=model_home_state,
            projected_control_plan=model_control_plan,
            projected_safety_report=model_safety,
            zone_group_context=model_zone_ctx,
        )
        fb = r.get("data", {})
        result = {
            "score":           _int_score(fb.get("satisfaction_score"), 3),
            "comfort_score":   _int_score(fb.get("comfort_score"), 3),
            "energy_score":    _int_score(fb.get("energy_score"), 3),
            "vpp_score":       _int_score(fb.get("vpp_score"), 3),
            "label":           fb.get("satisfaction_label", "neutral"),
            "comment":         fb.get("comment", ""),
            "zone_comfort_scores": fb.get("zone_comfort_scores"),
            "source": "roleplay_llm",
        }
        if adaptive_v2:
            result = _sanitize_adaptive_feedback_result(result)
        if vpp_result_context and vpp_result_context.get("achieved") is True:
            comment_lower = str(result.get("comment", "")).lower()
            misleading_miss = any(
                token in comment_lower
                for token in ("missed", "failed", "not met", "not achieved")
            )
            severe_service_issue = bool(
                skipped_task_count
                or nonfixed_appliances_during_vpp
                or missing_policy_services
                or unserved_service_devices
            )
            if (misleading_miss or result["vpp_score"] <= 2) and not severe_service_issue:
                if adaptive_v2:
                    _record_v2_non_safety_factual_audit(
                        result,
                        check="achieved_vpp_authored_judgement_disagreement",
                        facts={
                            "vpp_achieved": True,
                            "comment_claimed_vpp_miss": bool(misleading_miss),
                            "authored_vpp_score": result["vpp_score"],
                        },
                    )
                else:
                    result["vpp_score"] = max(result["vpp_score"], 4)
                    result["energy_score"] = max(result["energy_score"], 3)
                    if result["comfort_score"] >= 4 and result["score"] < 4:
                        result["score"] = 4
                        result["label"] = "satisfied"
                    if misleading_miss:
                        result["comment"] = "VPP appliance criterion achieved; comfort/routine were preserved."
                    result["factual_consistency_guard"] = "corrected_achieved_vpp_missed_label"
        if ev_policy_explicit:
            comment_lower = str(result.get("comment", "")).lower()
            false_ev_missing = (
                "ev" in comment_lower
                and any(
                    token in comment_lower
                    for token in (
                        "missing",
                        "not explicit",
                        "not scheduled",
                        "no ev",
                        "absent",
                    )
                )
            )
            severe_service_issue = bool(
                skipped_task_count
                or nonfixed_appliances_during_vpp
                or missing_policy_services
                or unserved_service_devices
            )
            if false_ev_missing and not severe_service_issue:
                if adaptive_v2:
                    _record_v2_non_safety_factual_audit(
                        result,
                        check="explicit_ev_action_authored_comment_disagreement",
                        facts={
                            "ev_policy_explicit": True,
                            "ev_target_reached": bool(ev_target_reached),
                            "comment_claimed_ev_missing": True,
                        },
                    )
                else:
                    result["energy_score"] = max(result["energy_score"], 3)
                    if vpp_result_context and vpp_result_context.get("achieved") is True:
                        result["vpp_score"] = max(result["vpp_score"], 4)
                    result["score"] = max(result["score"], 3)
                    if result["comfort_score"] >= 4 and result["vpp_score"] >= 4:
                        result["score"] = max(result["score"], 4)
                    result["label"] = [
                        "very_dissatisfied",
                        "dissatisfied",
                        "neutral",
                        "satisfied",
                        "very_satisfied",
                    ][max(1, min(5, int(result["score"]))) - 1]
                    result["comment"] = (
                        "EV charging schedule was emitted and target SOC was reached; "
                        "remaining concerns are comfort/routine only."
                    )
                    result["factual_consistency_guard"] = "corrected_false_ev_missing_label"
        if skipped_task_count > 0:
            skipped_names = ", ".join(skipped_devices)
            result.update({
                "score": 1,
                "comfort_score": min(result["comfort_score"], 2),
                "energy_score": min(result["energy_score"], 2),
                "vpp_score": 1,
                "label": "very_dissatisfied",
                "comment": (
                    f"Required appliance task(s) were skipped ({skipped_names}); "
                    "this violates the user's service rule."
                ),
            })
        if (
            not adaptive_v2
            and _low_disruption_strategy_language(persona)
            and fixed_appliances
            and (not adaptive_v2 or not missing_policy_services)
            and result["score"] < 4
            and result["comfort_score"] >= 4
            and agent_setpoint_c is not None
            and pref_min - pref_tol <= float(agent_setpoint_c) <= pref_max + pref_tol
            and vpp_result_context
            and vpp_result_context.get("achieved") is False
            and not nonfixed_appliances_during_vpp
        ):
            if adaptive_v2:
                _record_v2_non_safety_factual_audit(
                    result,
                    check="fixed_load_limited_vpp_authored_judgement",
                    facts={
                        "fixed_appliances": list(fixed_appliances),
                        "vpp_achieved": False,
                        "comfort_score_at_least_four": True,
                        "setpoint_within_preference_tolerance": True,
                        "nonfixed_appliances_during_vpp": [],
                    },
                )
            else:
                result["score"] = 4
                result["label"] = "satisfied"
                result["comment"] = (
                    "Comfort/consent preserved; fixed loads limited VPP."
                )
                result["fixed_constraint_satisfaction_guard"] = (
                    "overall_user_satisfaction_not_penalized_for_fixed_non_dr_loads"
                )
    except Exception as e:
        result = _rule_score(persona, mean_temp_c, pmv_ok_fraction, energy_kwh_per_day,
                             zone_group_temps, washer_completed, washer_during_vpp,
                             skipped_task_count, skipped_devices)
        result["source"] = "rule_based_fallback"

    if missing_policy_services:
        if not adaptive_v2:
            result["score"] = 1
            result["comfort_score"] = min(result["comfort_score"], 2)
            result["energy_score"] = 1
            result["vpp_score"] = 1
            result["label"] = "very_dissatisfied"
            result["comment"] = (
                "Required present controllable appliance(s) had no emitted policy strategy/action "
                f"(missing: {', '.join(missing_policy_services)}; "
                f"unsupported_by_action_space: {', '.join(unsupported_policy_services) or 'none'}). "
                "Baseline routine/default completion was not credited as method success."
            )
        result["policy_service_guard"] = {
            "missing_policy_services": missing_policy_services,
            "unsupported_policy_services": unsupported_policy_services,
            "emitted_policy_services": sorted(emitted_policy_services),
            "present_required_services": sorted(present_required_services),
        }
        if not adaptive_v2 and (
            str(method).startswith("rl")
            or str(policy_control_context.get("objective_source", "")).startswith("rl_")
        ):
            result["rl_policy_service_guard"] = result["policy_service_guard"]

    result = _calibrate_roleplay_score(
        result,
        persona=persona,
        method=method,
        mean_temp_c=mean_temp_c,
        pmv_ok_fraction=pmv_ok_fraction,
        energy_kwh_per_day=energy_kwh_per_day,
        pref_min=pref_min,
        pref_max=pref_max,
        pref_tol=pref_tol,
        explanation_is_user_facing=explanation_is_user_facing,
        vpp_result_context=vpp_result_context,
        policy_control_context=policy_control_context,
        severe_service_issue=bool(
            missing_policy_services
            or skipped_task_count
            or unserved_service_devices
            or invalid_ev_windows_hard_failure
        ),
    )
    result = _apply_unserved_service_score_cap(result, unserved_service_devices)
    if adaptive_v2:
        result = _sanitize_adaptive_feedback_result(result)

    # Dialogue log
    if log_path:
        _append_dialogue_log(log_path, {
            "ts": datetime.datetime.utcnow().isoformat(),
            "persona": (
                "roleplay_household" if adaptive_v2 else persona.get("id", "?")
            ),
            "event_index": event_index,
            "type": "feedback",
            "method": "household_controller" if adaptive_v2 else method,
            "scores": {
                "overall": result["score"],
                "comfort": result["comfort_score"],
                "energy":  result["energy_score"],
                "vpp":     result["vpp_score"],
            },
            "comment": result.get("comment", ""),
            "washer_completed": washer_completed,
            "washer_during_vpp": washer_during_vpp,
            "skipped_devices": skipped_devices,
            "source": result.get("source", "?"),
        })

    return result
