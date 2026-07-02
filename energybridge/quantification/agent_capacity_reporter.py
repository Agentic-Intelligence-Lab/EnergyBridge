"""LLM-assisted capacity reporting from top-k historical DR memory.

The LLM does not invent capacity values.  It receives compact historical
distribution statistics plus deterministic capacity bands, then chooses a
reporting band and a strategy bias. Numeric guardrails are applied after
parsing the LLM output.
"""

from __future__ import annotations

import json
import re
from typing import Any, Mapping

from energybridge.llm.client import LLMClient
from energybridge.quantification.dr_event_memory import estimate_event_capacity_from_memory


CHOICE_MULTIPLIERS = {
    "conservative": 0.8,
    "calibrated": 1.0,
    "assertive": 1.1,
}
STRATEGY_BIASES = {"comfort_first", "balanced", "savings_first"}


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
            "choice": "calibrated",
            "strategy_bias": "balanced",
            "risk_level": "medium",
            "reason": "dry_run uses deterministic calibrated band without an LLM call",
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
    p25_kw = _float_or_none(distribution.get("p25_kw"))
    p50_kw = _float_or_none(distribution.get("median_kw"))
    p75_kw = _float_or_none(distribution.get("p75_kw"))
    if p50_kw is not None:
        conservative_kw = max(0.0, p25_kw if p25_kw is not None else p50_kw * CHOICE_MULTIPLIERS["conservative"])
        calibrated_kw = max(0.0, p50_kw)
        assertive_kw = max(0.0, p75_kw if p75_kw is not None else p50_kw * CHOICE_MULTIPLIERS["assertive"])
        band_basis = "topk_baseline_adjusted_delivery_distribution"
    else:
        calibrated_kwh = max(0.0, float(estimate.get("reported_capacity_kwh") or 0.0))
        conservative_kw = calibrated_kwh * CHOICE_MULTIPLIERS["conservative"] / duration_h
        calibrated_kw = calibrated_kwh / duration_h
        assertive_kw = calibrated_kwh * CHOICE_MULTIPLIERS["assertive"] / duration_h
        band_basis = "deterministic_memory_estimate_fallback"
    return {
        "conservative": {
            "reported_capacity_kwh": round(conservative_kw * duration_h, 6),
            "reported_capacity_kw": round(conservative_kw, 6),
            "distribution_position": "p25",
        },
        "calibrated": {
            "reported_capacity_kwh": round(calibrated_kw * duration_h, 6),
            "reported_capacity_kw": round(calibrated_kw, 6),
            "distribution_position": "p50",
        },
        "assertive": {
            "reported_capacity_kwh": round(assertive_kw * duration_h, 6),
            "reported_capacity_kw": round(assertive_kw, 6),
            "distribution_position": "p75",
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
        "capacity_bands": bands,
    }
    system_prompt = (
        "You are an EnergyBridge capacity-reporting agent. "
        "Choose one capacity band using only the provided top-k historical DR memory distribution. "
        "Do not invent numeric capacity. Return strict JSON only."
    )
    user_prompt = (
        "Select a capacity report and strategy bias for this VPP event.\n"
        "Rules:\n"
        "- choice must be one of conservative, calibrated, assertive.\n"
        "- conservative reports near P25 of the adjusted historical delivery distribution.\n"
        "- calibrated reports near P50 of the adjusted historical delivery distribution.\n"
        "- assertive reports near P75 and should be used only when similarity and user/VPP scores are strong.\n"
        "- strategy_bias must be one of comfort_first, balanced, savings_first.\n"
        "- Keep reason under 40 English words.\n\n"
        f"Profile:\n{json.dumps(profile, ensure_ascii=False)}\n\n"
        f"Event and top-k memory distribution:\n{json.dumps(event_context, ensure_ascii=False)}\n\n"
        "Return JSON with keys: choice, strategy_bias, risk_level, reason."
    )
    return system_prompt, user_prompt


def _validate_decision_json(text: str) -> str:
    data = _extract_json(text)
    choice = str(data.get("choice") or "").strip().lower()
    if choice not in CHOICE_MULTIPLIERS:
        raise ValueError(f"invalid choice: {choice}")
    strategy_bias = str(data.get("strategy_bias") or "").strip().lower()
    if strategy_bias not in STRATEGY_BIASES:
        raise ValueError(f"invalid strategy_bias: {strategy_bias}")
    out = {
        "choice": choice,
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
    choice = str(decision.get("choice") or "calibrated").lower()
    if choice not in CHOICE_MULTIPLIERS:
        choice = "calibrated"
    band = dict(bands.get(choice) or bands.get("calibrated") or {})
    duration_h = max(1e-9, float(bands.get("duration_h") or estimate.get("duration_h") or 1.0))
    reported_kwh = max(0.0, float(band.get("reported_capacity_kwh") or 0.0))
    reported_kw = reported_kwh / duration_h
    strategy_bias = str(decision.get("strategy_bias") or "balanced").lower()
    if strategy_bias not in STRATEGY_BIASES:
        strategy_bias = "balanced"
    return {
        "choice": choice,
        "strategy_bias": strategy_bias,
        "risk_level": str(decision.get("risk_level") or "medium").lower(),
        "reason": str(decision.get("reason") or "")[:240],
        "reported_capacity_kwh": round(reported_kwh, 6),
        "reported_capacity_kw": round(reported_kw, 6),
        "distribution_position": str(band.get("distribution_position") or ""),
        "duration_h": round(duration_h, 6),
        "guardrail": "capacity_from_precomputed_topk_distribution_band_not_freeform_llm_number",
    }


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
