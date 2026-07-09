"""LLM-assisted capacity reporting from top-k historical DR memory.

The LLM does not invent capacity values. It receives compact historical
distribution statistics plus deterministic P50/P70/P90 capacity bands, then
recommends one band according to user preference and historical reliability.
Numeric guardrails are applied after parsing the LLM output.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from energybridge.llm.client import LLMClient
from energybridge.quantification.dr_event_memory import estimate_event_capacity_from_memory


QUANTILE_CHOICES = {"p50", "p70", "p90"}
LEGACY_CHOICE_TO_QUANTILE = {
    "conservative": "p50",
    "calibrated": "p70",
    "assertive": "p90",
}
FALLBACK_QUANTILE_MULTIPLIERS = {
    "p50": 1.0,
    "p70": 1.05,
    "p90": 1.1,
}
STRATEGY_BIASES = {"comfort_first", "balanced", "savings_first"}
PREFERENCE_PROFILES = {"comfort_sensitive", "balanced", "grid_cooperative", "price_sensitive", "uncertain"}


def apply_agent_capacity_reporting(
    result: Mapping[str, Any],
    memory: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
    client: LLMClient | None = None,
    top_k: int = 5,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Attach top-k distribution-based agent capacity reports to every VPP event."""
    out = dict(result)
    events = [dict(event) for event in out.get("vpp_event_log") or []]
    total_capacity_kwh = 0.0
    total_duration_h = 0.0
    reports = []
    for event in events:
        report = report_event_capacity_with_agent(
            event,
            memory,
            result=result,
            metadata=metadata,
            client=client,
            top_k=top_k,
            dry_run=dry_run,
        )
        event["agent_capacity_report"] = report
        reports.append(report)
        capacity = _float_or_none(report.get("reported_capacity_kwh"))
        duration = _float_or_none(report.get("duration_h"))
        if capacity is not None:
            total_capacity_kwh += max(0.0, capacity)
        if duration is not None:
            total_duration_h += max(0.0, duration)
    out["vpp_event_log"] = events
    out["agent_capacity_report_status"] = "estimated" if reports else "missing"
    out["agent_capacity_report_event_count"] = len(reports)
    out["agent_capacity_report_total_kwh"] = round(total_capacity_kwh, 6)
    out["agent_capacity_report_avg_kw"] = (
        round(total_capacity_kwh / total_duration_h, 6) if total_duration_h > 1e-9 else None
    )
    out["agent_capacity_report_basis"] = "topk_historical_memory_distribution_agent_band_choice"
    out.update(_summarize_report_choices(reports))
    return out


def report_event_capacity_with_agent(
    event: Mapping[str, Any],
    memory: Mapping[str, Any],
    *,
    result: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    client: LLMClient | None = None,
    top_k: int = 5,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Return one agent capacity report with deterministic guardrails."""
    result = result or {}
    metadata = metadata or {}
    estimate = estimate_event_capacity_from_memory(
        event,
        memory,
        result=result,
        metadata=metadata,
        top_k=max(1, int(top_k)),
    )
    bands = _capacity_bands(estimate)
    if dry_run:
        decision = {
            "recommended_quantile": "p70",
            "preference_profile": "balanced",
            "strategy_bias": "balanced",
            "risk_level": "medium",
            "reason": "dry_run uses deterministic P70 recommendation without an LLM call",
        }
        metrics = {"used": False}
    else:
        llm = client or LLMClient()
        system_prompt, user_prompt = _build_prompt(event, result, metadata, estimate, bands)
        response = llm.chat_with_metrics(
            system_prompt,
            user_prompt,
            max_retries=2,
            retry_base_delay=1.0,
            validate_fn=_validate_decision_json,
        )
        decision = json.loads(response["text"])
        metrics = response.get("metrics", {})
    guarded = _guard_decision(decision, estimate, bands)
    return {
        **guarded,
        "deterministic_memory_estimate": estimate,
        "capacity_bands": bands,
        "llm_metrics": metrics,
        "basis": "agent_topk_memory_distribution_band_choice_with_guardrails",
    }


def _capacity_bands(estimate: Mapping[str, Any]) -> dict[str, Any]:
    duration_h = max(1e-9, float(estimate.get("duration_h") or 1.0))
    distribution = dict(estimate.get("delivery_distribution") or {})
    p50_kw = _float_or_none(distribution.get("median_kw"))
    p70_kw = _float_or_none(distribution.get("p70_kw"))
    p75_kw = _float_or_none(distribution.get("p75_kw"))
    p90_kw = _float_or_none(distribution.get("p90_kw"))
    if p50_kw is not None:
        p50_kw = max(0.0, p50_kw)
        p70_kw = max(0.0, p70_kw if p70_kw is not None else (p75_kw if p75_kw is not None else p50_kw * FALLBACK_QUANTILE_MULTIPLIERS["p70"]))
        p90_kw = max(0.0, p90_kw if p90_kw is not None else p50_kw * FALLBACK_QUANTILE_MULTIPLIERS["p90"])
        band_basis = "topk_baseline_adjusted_delivery_distribution"
    else:
        calibrated_kwh = max(0.0, float(estimate.get("reported_capacity_kwh") or 0.0))
        p50_kw = calibrated_kwh * FALLBACK_QUANTILE_MULTIPLIERS["p50"] / duration_h
        p70_kw = calibrated_kwh * FALLBACK_QUANTILE_MULTIPLIERS["p70"] / duration_h
        p90_kw = calibrated_kwh * FALLBACK_QUANTILE_MULTIPLIERS["p90"] / duration_h
        band_basis = "deterministic_memory_estimate_fallback"
    return {
        "p50": {
            "reported_capacity_kwh": round(p50_kw * duration_h, 6),
            "reported_capacity_kw": round(p50_kw, 6),
            "distribution_position": "p50",
        },
        "p70": {
            "reported_capacity_kwh": round(p70_kw * duration_h, 6),
            "reported_capacity_kw": round(p70_kw, 6),
            "distribution_position": "p70",
        },
        "p90": {
            "reported_capacity_kwh": round(p90_kw * duration_h, 6),
            "reported_capacity_kw": round(p90_kw, 6),
            "distribution_position": "p90",
        },
        "duration_h": round(duration_h, 6),
        "basis": band_basis,
        "delivery_distribution": distribution,
    }


def _build_prompt(
    event: Mapping[str, Any],
    result: Mapping[str, Any],
    metadata: Mapping[str, Any],
    estimate: Mapping[str, Any],
    bands: Mapping[str, Any],
) -> tuple[str, str]:
    retrieved = list(estimate.get("retrieved_events") or [])
    compact_memory = [
        {
            "similarity": item.get("similarity"),
            "source_day": item.get("memory_source_day"),
            "hour_of_day": item.get("hour_of_day"),
            "historical_baseline_kwh": item.get("no_dr_baseline_kwh"),
            "raw_delivery_kw": item.get("realized_delivery_kw"),
            "baseline_adjusted_delivery_kw": item.get("baseline_adjusted_delivery_kw"),
            "event_score": item.get("event_score"),
            "vpp_score": item.get("vpp_score"),
        }
        for item in retrieved
    ]
    profile = {
        "entity_id": metadata.get("household_id") or metadata.get("persona_id") or result.get("household_id") or result.get("persona_id"),
        "city": metadata.get("city") or result.get("city") or result.get("weather"),
        "method": metadata.get("method") or result.get("method"),
        "run_user_pref_score": result.get("user_pref_score"),
        "preference_evidence": _preference_evidence(result, metadata),
    }
    event_context = {
        "event_id": event.get("id"),
        "day": event.get("day"),
        "trigger_h": event.get("trigger_h"),
        "end_h": event.get("end_h"),
        "model_bid_kwh": estimate.get("model_bid_kwh"),
        "model_bid_kw": estimate.get("model_bid_kw"),
        "target_day": estimate.get("target_day"),
        "target_baseline_kwh": estimate.get("target_baseline_kwh"),
        "memory_confidence": estimate.get("confidence"),
        "topk_delivery_distribution": estimate.get("delivery_distribution"),
        "topk_memory_events": compact_memory,
        "vpp_capacity_options": _vpp_capacity_options(bands),
    }
    system_prompt = (
        "You are an EnergyBridge capacity-reporting agent. "
        "VPP will receive all P50/P70/P90 capacity options. "
        "Your job is to recommend one option based on user preference, historical reliability, and event context. "
        "Do not invent numeric capacity. Return strict JSON only."
    )
    user_prompt = (
        "Recommend one VPP capacity quantile and strategy bias for this event.\n"
        "Rules:\n"
        "- recommended_quantile must be one of p50, p70, p90.\n"
        "- VPP still receives all three options; recommended_quantile is the default bid suggestion.\n"
        "- Prefer p50 for comfort-sensitive, low-consent, low-confidence, or unstable-history users.\n"
        "- Prefer p70 for balanced users or when preference evidence is mixed.\n"
        "- Prefer p90 only for price-sensitive or grid-cooperative users with strong historical reliability.\n"
        "- strategy_bias must be one of comfort_first, balanced, savings_first.\n"
        "- preference_profile must be one of comfort_sensitive, balanced, grid_cooperative, price_sensitive, uncertain.\n"
        "- Keep reason under 40 English words.\n\n"
        f"Profile:\n{json.dumps(profile, ensure_ascii=False)}\n\n"
        f"Event and top-k memory distribution:\n{json.dumps(event_context, ensure_ascii=False)}\n\n"
        "Return JSON with keys: recommended_quantile, preference_profile, strategy_bias, risk_level, reason."
    )
    return system_prompt, user_prompt


def _validate_decision_json(text: str) -> str:
    data = _extract_json(text)
    quantile = _normalize_quantile(data.get("recommended_quantile") or data.get("choice"))
    if quantile not in QUANTILE_CHOICES:
        raise ValueError(f"invalid recommended_quantile: {quantile}")
    strategy_bias = str(data.get("strategy_bias") or "").strip().lower()
    if strategy_bias not in STRATEGY_BIASES:
        raise ValueError(f"invalid strategy_bias: {strategy_bias}")
    preference_profile = str(data.get("preference_profile") or "uncertain").strip().lower()
    if preference_profile not in PREFERENCE_PROFILES:
        preference_profile = "uncertain"
    out = {
        "recommended_quantile": quantile,
        "preference_profile": preference_profile,
        "strategy_bias": strategy_bias,
        "risk_level": str(data.get("risk_level") or "medium").strip().lower(),
        "reason": str(data.get("reason") or "").strip()[:240],
    }
    return json.dumps(out, ensure_ascii=False)


def _extract_json(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", text, flags=re.S)
    if not match:
        raise ValueError("response did not contain a JSON object")
    data = json.loads(match.group(0))
    if not isinstance(data, dict):
        raise ValueError("response JSON must be an object")
    return data


def _guard_decision(
    decision: Mapping[str, Any],
    estimate: Mapping[str, Any],
    bands: Mapping[str, Any],
) -> dict[str, Any]:
    quantile = _normalize_quantile(decision.get("recommended_quantile") or decision.get("choice"))
    if quantile not in QUANTILE_CHOICES:
        quantile = "p70"
    band = dict(bands.get(quantile) or bands.get("p70") or bands.get("p50") or {})
    duration_h = max(1e-9, float(bands.get("duration_h") or estimate.get("duration_h") or 1.0))
    reported_kwh = max(0.0, float(band.get("reported_capacity_kwh") or 0.0))
    reported_kw = reported_kwh / duration_h
    strategy_bias = str(decision.get("strategy_bias") or "balanced").lower()
    if strategy_bias not in STRATEGY_BIASES:
        strategy_bias = "balanced"
    preference_profile = str(decision.get("preference_profile") or "uncertain").lower()
    if preference_profile not in PREFERENCE_PROFILES:
        preference_profile = "uncertain"
    return {
        "recommended_quantile": quantile,
        "choice": quantile,
        "preference_profile": preference_profile,
        "strategy_bias": strategy_bias,
        "risk_level": str(decision.get("risk_level") or "medium").lower(),
        "reason": str(decision.get("reason") or "")[:240],
        "reported_capacity_kwh": round(reported_kwh, 6),
        "reported_capacity_kw": round(reported_kw, 6),
        "distribution_position": str(band.get("distribution_position") or ""),
        "duration_h": round(duration_h, 6),
        "vpp_capacity_options": _vpp_capacity_options(bands),
        "guardrail": "recommended_capacity_from_precomputed_p50_p70_p90_options_not_freeform_llm_number",
    }


def _summarize_report_choices(reports: list[Mapping[str, Any]]) -> dict[str, Any]:
    positions: list[str] = []
    choices: list[str] = []
    quantiles: list[str] = []
    for report in reports:
        position = str(report.get("distribution_position") or "").strip().lower()
        choice = str(report.get("choice") or "").strip().lower()
        quantile = str(report.get("recommended_quantile") or choice).strip().lower()
        if position:
            positions.append(position)
        if choice:
            choices.append(choice)
        if quantile:
            quantiles.append(quantile)
    position_counts = {position: positions.count(position) for position in ("p50", "p70", "p90")}
    choice_counts = {choice: choices.count(choice) for choice in ("p50", "p70", "p90")}
    quantile_counts = {quantile: quantiles.count(quantile) for quantile in ("p50", "p70", "p90")}
    return {
        "agent_capacity_report_distribution_positions": ",".join(positions),
        "agent_capacity_report_primary_distribution_position": _mode_label(positions),
        "agent_capacity_report_distribution_position_counts": ",".join(
            f"{key}={position_counts[key]}" for key in ("p50", "p70", "p90")
        ),
        "agent_capacity_report_choices": ",".join(choices),
        "agent_capacity_report_primary_choice": _mode_label(choices),
        "agent_capacity_report_choice_counts": ",".join(
            f"{key}={choice_counts[key]}" for key in ("p50", "p70", "p90")
        ),
        "agent_capacity_report_recommended_quantiles": ",".join(quantiles),
        "agent_capacity_report_primary_recommended_quantile": _mode_label(quantiles),
        "agent_capacity_report_recommended_quantile_counts": ",".join(
            f"{key}={quantile_counts[key]}" for key in ("p50", "p70", "p90")
        ),
    }


def _mode_label(values: list[str]) -> str:
    if not values:
        return ""
    counts = {value: values.count(value) for value in sorted(set(values))}
    return max(counts.items(), key=lambda item: (item[1], item[0]))[0]


def _normalize_quantile(value: Any) -> str:
    label = str(value or "").strip().lower()
    if label in QUANTILE_CHOICES:
        return label
    return LEGACY_CHOICE_TO_QUANTILE.get(label, label)


def _vpp_capacity_options(bands: Mapping[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for quantile in ("p50", "p70", "p90"):
        band = dict(bands.get(quantile) or {})
        options[quantile] = {
            "reported_capacity_kw": band.get("reported_capacity_kw"),
            "reported_capacity_kwh": band.get("reported_capacity_kwh"),
            "distribution_position": band.get("distribution_position") or quantile,
        }
    return {
        "options": options,
        "duration_h": bands.get("duration_h"),
        "basis": bands.get("basis"),
    }


def _preference_evidence(result: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    evidence: dict[str, Any] = {}
    for source_name, source in (("metadata", metadata), ("result", result)):
        for key in (
            "persona_id",
            "household_id",
            "user_label",
            "persona",
            "persona_config",
            "tags",
            "stable_preferences",
            "contextual_preferences",
            "memory_stable_preferences",
            "memory_contextual_preferences",
            "agent_preference_memory",
            "preference_memory",
            "user_preferences",
            "preference_summary",
            "user_input",
            "user_pref_score",
            "comfort_score",
            "energy_score",
            "vpp_score",
        ):
            if key in source and source.get(key) not in (None, ""):
                evidence[f"{source_name}.{key}"] = _compact_value(source.get(key), depth=2)
    return evidence


def _compact_value(value: Any, *, depth: int) -> Any:
    if depth <= 0:
        return str(value)[:240]
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 20:
                out["..."] = "truncated"
                break
            out[str(key)] = _compact_value(item, depth=depth - 1)
        return out
    if isinstance(value, list):
        return [_compact_value(item, depth=depth - 1) for item in value[:10]]
    if isinstance(value, str):
        return value[:500]
    return value


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
