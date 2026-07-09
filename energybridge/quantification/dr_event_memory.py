"""Historical DR event memory for capacity reporting.

This module is intentionally separate from the no-DR counterfactual settlement
code.  The counterfactual baseline is an evaluation tool used after a run.  The
historical DR memory is a reporting tool used before or during future runs:
it learns how much the controller actually delivered in past events relative
to its model bid, then applies a correction factor to similar future events.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Iterable, Mapping

EPS = 1e-9


def build_dr_event_memory(
    result_items: Iterable[tuple[Mapping[str, Any], Mapping[str, Any], str | Path | None]],
    *,
    methods: Iterable[str] | None = None,
) -> dict[str, Any]:
    """Build a JSON-serializable historical DR memory from benchmark results.

    Args:
        result_items: Iterable of ``(benchmark_result, metadata, source_path)``.
            ``metadata`` is normally a row from a matrix summary.
        methods: Optional method filter.  Defaults to keeping every method in
            ``result_items``; for the current paper workflow this should be
            ``["eb_rule_milp"]``.
    """
    wanted = {str(method).lower() for method in methods or []}
    records: list[dict[str, Any]] = []
    for result, metadata, source_path in result_items:
        method = _method(metadata, result)
        if wanted and method.lower() not in wanted:
            continue
        for event in result.get("vpp_event_log") or []:
            record = extract_dr_event_record(
                result,
                event,
                metadata=metadata,
                source_path=source_path,
            )
            if record is not None:
                records.append(record)
    return {
        "schema_version": "historical_dr_event_memory_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": (
            "Historical DR events used to estimate future reported capacity. "
            "Each record stores the controller bid and the realized delivery "
            "against a no-DR counterfactual baseline."
        ),
        "recommended_use": (
            "Use this for capacity reporting / bid calibration. Keep no-DR "
            "counterfactual settlement separate for benchmark evaluation."
        ),
        "events": records,
        "summary": _memory_summary(records),
    }


def extract_dr_event_record(
    result: Mapping[str, Any],
    event: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
    source_path: str | Path | None = None,
) -> dict[str, Any] | None:
    """Extract one calibrated historical DR event record.

    Events without no-DR counterfactual delivery are skipped because they cannot
    calibrate the reportable-capacity estimate.
    """
    meta = metadata or {}
    baseline_kwh = _first_float(
        event.get("counterfactual_baseline_kwh"),
        event.get("counterfactual_capacity_upper_bound_kwh"),
    )
    delivered_kwh = _first_float(
        event.get("counterfactual_actual_shed_kwh"),
        event.get("actual_shed_kwh") if str(event.get("actual_shed_basis", "")).startswith("no_dr") else None,
    )
    actual_kwh = _first_float(event.get("counterfactual_actual_kwh"), event.get("actual_kwh"))
    if baseline_kwh is None or delivered_kwh is None or actual_kwh is None:
        return None
    trigger_h = _float_or_none(event.get("trigger_h"))
    end_h = _float_or_none(event.get("end_h"))
    if trigger_h is None or end_h is None or end_h <= trigger_h:
        return None
    duration_h = max(EPS, end_h - trigger_h)
    model_bid_kwh, model_bid_source = _event_model_bid_kwh(event, duration_h=duration_h)
    correction_factor = None
    if model_bid_kwh is not None and model_bid_kwh > EPS:
        correction_factor = delivered_kwh / model_bid_kwh
    entity_id = _entity_id(meta, result)
    city = _city(meta, result)
    method = _method(meta, result)
    event_id = str(event.get("id") or "")
    record = {
        "memory_event_id": "|".join(
            [
                entity_id or "unknown",
                city.lower() or "unknown_city",
                method,
                str(meta.get("start_date") or result.get("start_date") or ""),
                event_id or f"h{trigger_h:g}",
            ]
        ),
        "entity_id": entity_id,
        "entity_type": "household" if meta.get("household_id") or result.get("household_id") else "persona",
        "persona_id": str(meta.get("persona_id") or result.get("persona_id") or entity_id or ""),
        "household_id": str(meta.get("household_id") or result.get("household_id") or ""),
        "city": city,
        "method": method,
        "start_date": str(meta.get("start_date") or result.get("start_date") or ""),
        "days": _int_or_none(meta.get("days", result.get("sim_days"))),
        "memory_source_day": _memory_source_day(meta, event_id),
        "source_result_path": str(source_path or meta.get("output_dir") or result.get("output_dir") or ""),
        "event_id": event_id,
        "day": _int_or_none(event.get("day")),
        "trigger_h": round(trigger_h, 6),
        "end_h": round(end_h, 6),
        "hour_of_day": round(trigger_h % 24.0, 6),
        "duration_h": round(duration_h, 6),
        "actual_kwh": round(actual_kwh, 6),
        "no_dr_baseline_kwh": round(baseline_kwh, 6),
        "realized_delivery_kwh": round(delivered_kwh, 6),
        "realized_delivery_kw": round(delivered_kwh / duration_h, 6),
        "delivery_ratio_vs_no_dr": round(delivered_kwh / baseline_kwh, 6) if baseline_kwh > EPS else None,
        "model_bid_kwh": _round_or_none(model_bid_kwh),
        "model_bid_kw": _round_or_none(model_bid_kwh / duration_h if model_bid_kwh is not None else None),
        "model_bid_source": model_bid_source,
        "delivery_correction_factor_vs_model_bid": _round_or_none(correction_factor),
        "demand_target_shed_kwh": _round_or_none(_first_float(event.get("demand_target_shed_kwh"))),
        "capacity_recommended_bid_kw": _round_or_none(_capacity_assessment_value(event, "recommended_bid_kw")),
        "capacity_success_probability": _round_or_none(_capacity_assessment_value(event, "success_probability")),
        "capacity_committable_kw": _round_or_none(_capacity_assessment_value(event, "committable_kw")),
        "event_score": _round_or_none(_first_float(event.get("score"))),
        "comfort_score": _round_or_none(_first_float(event.get("comfort_score"))),
        "energy_score": _round_or_none(_first_float(event.get("energy_score"))),
        "vpp_score": _round_or_none(_first_float(event.get("vpp_score"))),
        "run_user_pref_score": _round_or_none(_first_float(result.get("user_pref_score"))),
        "vpp_appliance_avoidance_success": event.get("vpp_appliance_avoidance_success"),
        "non_ac_appliances_during_event": list(event.get("vpp_non_ac_appliances_during_event") or []),
        "selected_strategy_id": _selected_strategy_id(event),
        "selected_strategy_reason": _selected_strategy_reason(event),
        "basis": "historical_controller_delivery_vs_no_dr_counterfactual",
    }
    return record


def apply_dr_memory_capacity_estimate(
    result: Mapping[str, Any],
    memory: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
    top_k: int = 5,
    factor_cap: float = 2.0,
) -> dict[str, Any]:
    """Attach historical-memory capacity estimates to a benchmark result."""
    out = dict(result)
    events = [dict(event) for event in out.get("vpp_event_log") or []]
    estimates: list[dict[str, Any]] = []
    total_kwh = 0.0
    total_duration_h = 0.0
    for event in events:
        estimate = estimate_event_capacity_from_memory(
            event,
            memory,
            result=result,
            metadata=metadata,
            top_k=top_k,
            factor_cap=factor_cap,
        )
        event["historical_dr_memory_capacity_estimate"] = estimate
        estimates.append(estimate)
        cap = _float_or_none(estimate.get("reported_capacity_kwh"))
        duration = _float_or_none(estimate.get("duration_h"))
        if cap is not None:
            total_kwh += max(0.0, cap)
        if duration is not None:
            total_duration_h += max(0.0, duration)
    out["vpp_event_log"] = events
    out["historical_dr_memory_status"] = "estimated" if estimates else "missing"
    out["historical_dr_memory_event_count"] = len(estimates)
    out["historical_dr_memory_reported_capacity_total_kwh"] = round(total_kwh, 6)
    out["historical_dr_memory_reported_capacity_avg_kw"] = (
        round(total_kwh / total_duration_h, 6) if total_duration_h > EPS else None
    )
    out["historical_dr_memory_basis"] = "similar_historical_eb_rule_milp_delivery_correction"
    return out


def estimate_event_capacity_from_memory(
    event: Mapping[str, Any],
    memory: Mapping[str, Any],
    *,
    result: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    top_k: int = 5,
    factor_cap: float = 2.0,
) -> dict[str, Any]:
    """Estimate reportable capacity for one future event using kNN history."""
    result = result or {}
    metadata = metadata or {}
    trigger_h = _float_or_none(event.get("trigger_h"))
    end_h = _float_or_none(event.get("end_h"))
    if trigger_h is None or end_h is None or end_h <= trigger_h:
        return {"status": "missing_event_window", "reported_capacity_kwh": 0.0}
    duration_h = max(EPS, end_h - trigger_h)
    model_bid_kwh, model_bid_source = _event_model_bid_kwh(event, duration_h=duration_h)
    target_day = _event_day(event, trigger_h)
    target_baseline_kwh = _event_baseline_kwh(event)
    entity_id = _entity_id(metadata, result)
    if not entity_id:
        return {
            "status": "missing_entity_id",
            "reported_capacity_kwh": 0.0,
            "reported_capacity_kw": 0.0,
            "duration_h": round(duration_h, 6),
            "basis": "entity_id_required_for_role_scoped_memory_lookup",
            "retrieval_count": 0,
            "retrieved_events": [],
            "delivery_distribution": {"count": 0},
        }
    city = _city(metadata, result)
    method = _method(metadata, result)
    scored = []
    for record in memory.get("events") or []:
        if str(record.get("entity_id") or "") != entity_id:
            continue
        score = _similarity(
            record,
            entity_id=entity_id,
            city=city,
            method=method,
            trigger_h=trigger_h,
            duration_h=duration_h,
            target_day=target_day,
            target_baseline_kwh=target_baseline_kwh,
        )
        if score <= 0:
            continue
        scored.append((score, record))
    same_hour_scored = [
        item for item in scored
        if _hour_distance(_float_or_none(item[1].get("hour_of_day")), trigger_h) <= 0.5
    ]
    if len(same_hour_scored) >= min(max(1, int(top_k)), 3):
        scored = same_hour_scored
    scored.sort(key=lambda item: item[0], reverse=True)
    neighbors = scored[: max(1, int(top_k))]
    factors = []
    delivered_kw = []
    adjusted_delivery_samples = []
    for _, record in neighbors:
        factor = _float_or_none(record.get("delivery_correction_factor_vs_model_bid"))
        if factor is not None:
            factors.append(max(0.0, min(float(factor_cap), factor)))
        kw = _float_or_none(record.get("realized_delivery_kw"))
        if kw is not None:
            delivered_kw.append(max(0.0, kw))
            adjusted_kw, adjustment_factor, adjustment_basis = _baseline_adjusted_delivery_kw(
                record,
                target_baseline_kwh=target_baseline_kwh,
            )
            adjusted_delivery_samples.append(
                {
                    "raw_delivery_kw": round(max(0.0, kw), 6),
                    "adjusted_delivery_kw": round(max(0.0, adjusted_kw), 6),
                    "baseline_adjustment_factor": _round_or_none(adjustment_factor),
                    "baseline_adjustment_basis": adjustment_basis,
                }
            )
    adjusted_delivered_kw = (
        [sample["adjusted_delivery_kw"] for sample in adjusted_delivery_samples]
        if target_baseline_kwh is not None
        else []
    )
    if adjusted_delivered_kw:
        correction = median(factors) if factors else None
        capacity_kwh = max(0.0, median(adjusted_delivered_kw) * duration_h)
        basis = "median_topk_baseline_adjusted_historical_delivered_kw"
    elif model_bid_kwh is not None and factors:
        correction = median(factors)
        capacity_kwh = max(0.0, model_bid_kwh * correction)
        basis = "model_bid_times_historical_delivery_correction"
    elif delivered_kw:
        correction = None
        capacity_kwh = max(0.0, median(delivered_kw) * duration_h)
        basis = "median_historical_delivered_kw"
    else:
        correction = None
        capacity_kwh = max(0.0, model_bid_kwh or 0.0)
        basis = "model_bid_no_matching_history"
    confidence = _confidence(neighbors)
    return {
        "status": "estimated" if neighbors else "no_matching_history",
        "reported_capacity_kwh": round(capacity_kwh, 6),
        "reported_capacity_kw": round(capacity_kwh / duration_h, 6),
        "duration_h": round(duration_h, 6),
        "model_bid_kwh": _round_or_none(model_bid_kwh),
        "model_bid_kw": _round_or_none(model_bid_kwh / duration_h if model_bid_kwh is not None else None),
        "model_bid_source": model_bid_source,
        "target_day": _round_or_none(target_day),
        "target_baseline_kwh": _round_or_none(target_baseline_kwh),
        "correction_factor": _round_or_none(correction),
        "factor_cap": round(float(factor_cap), 6),
        "confidence": confidence,
        "retrieval_count": len(neighbors),
        "basis": basis,
        "delivery_distribution": _distribution(adjusted_delivered_kw or delivered_kw),
        "retrieved_events": [
            {
                "similarity": round(score, 6),
                "memory_event_id": record.get("memory_event_id"),
                "entity_id": record.get("entity_id"),
                "city": record.get("city"),
                "method": record.get("method"),
                "hour_of_day": record.get("hour_of_day"),
                "memory_source_day": record.get("memory_source_day"),
                "no_dr_baseline_kwh": record.get("no_dr_baseline_kwh"),
                "delivery_ratio_vs_no_dr": record.get("delivery_ratio_vs_no_dr"),
                **_retrieved_adjustment_fields(
                    record,
                    target_baseline_kwh=target_baseline_kwh,
                ),
                "model_bid_kwh": record.get("model_bid_kwh"),
                "realized_delivery_kwh": record.get("realized_delivery_kwh"),
                "realized_delivery_kw": record.get("realized_delivery_kw"),
                "correction_factor": record.get("delivery_correction_factor_vs_model_bid"),
                "event_score": record.get("event_score"),
                "energy_score": record.get("energy_score"),
                "vpp_score": record.get("vpp_score"),
                "selected_strategy_id": record.get("selected_strategy_id"),
            }
            for score, record in neighbors
        ],
    }


def _memory_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    factors = [
        float(record["delivery_correction_factor_vs_model_bid"])
        for record in records
        if _float_or_none(record.get("delivery_correction_factor_vs_model_bid")) is not None
    ]
    delivered_kw = [
        float(record["realized_delivery_kw"])
        for record in records
        if _float_or_none(record.get("realized_delivery_kw")) is not None
    ]
    return {
        "event_count": len(records),
        "entities": sorted({str(record.get("entity_id") or "") for record in records if record.get("entity_id")}),
        "cities": sorted({str(record.get("city") or "") for record in records if record.get("city")}),
        "methods": sorted({str(record.get("method") or "") for record in records if record.get("method")}),
        "avg_realized_delivery_kw": _round_or_none(sum(delivered_kw) / len(delivered_kw) if delivered_kw else None),
        "median_realized_delivery_kw": _round_or_none(median(delivered_kw) if delivered_kw else None),
        "median_delivery_correction_factor_vs_model_bid": _round_or_none(median(factors) if factors else None),
        "basis": "historical_controller_delivery_vs_no_dr_counterfactual",
    }


def _similarity(
    record: Mapping[str, Any],
    *,
    entity_id: str,
    city: str,
    method: str,
    trigger_h: float,
    duration_h: float,
    target_day: int | None = None,
    target_baseline_kwh: float | None = None,
) -> float:
    score = 0.0
    if entity_id and str(record.get("entity_id") or "") == entity_id:
        score += 4.0
    if city and str(record.get("city") or "").lower() == city.lower():
        score += 2.0
    if method and str(record.get("method") or "").lower() == method.lower():
        score += 1.0
    rec_hour = _float_or_none(record.get("hour_of_day"))
    if rec_hour is not None:
        diff = _hour_distance(rec_hour, trigger_h)
        score += 3.0 * max(0.0, 1.0 - diff / 12.0)
    rec_duration = _float_or_none(record.get("duration_h"))
    if rec_duration is not None:
        score += max(0.0, 1.0 - abs(duration_h - rec_duration) / max(duration_h, rec_duration, 1.0))
    rec_baseline = _float_or_none(record.get("no_dr_baseline_kwh"))
    if target_baseline_kwh is not None and rec_baseline is not None and max(target_baseline_kwh, rec_baseline) > EPS:
        ratio = min(target_baseline_kwh, rec_baseline) / max(target_baseline_kwh, rec_baseline)
        score += 1.0 * max(0.0, ratio)
    rec_day = _int_or_none(record.get("memory_source_day") or record.get("day"))
    if target_day is not None and rec_day is not None:
        day_diff = abs(int(target_day) - int(rec_day))
        score += 0.5 * max(0.0, 1.0 - min(day_diff, 7) / 7.0)
    return score


def _hour_distance(hour_a: float | None, hour_b: float | None) -> float:
    if hour_a is None or hour_b is None:
        return 24.0
    diff = abs((hour_a % 24.0) - (hour_b % 24.0))
    return min(diff, 24.0 - diff)


def _baseline_adjusted_delivery_kw(
    record: Mapping[str, Any],
    *,
    target_baseline_kwh: float | None,
) -> tuple[float, float | None, str]:
    raw_kw = max(0.0, _float_or_none(record.get("realized_delivery_kw")) or 0.0)
    historical_baseline = _float_or_none(record.get("no_dr_baseline_kwh"))
    if target_baseline_kwh is None or historical_baseline is None or historical_baseline <= EPS:
        return raw_kw, None, "raw_historical_delivery_no_target_baseline"
    factor = target_baseline_kwh / historical_baseline
    factor = max(0.8, min(1.25, factor))
    return raw_kw * factor, factor, "target_no_dr_baseline_over_historical_no_dr_baseline_clamped"


def _retrieved_adjustment_fields(
    record: Mapping[str, Any],
    *,
    target_baseline_kwh: float | None,
) -> dict[str, Any]:
    adjusted_kw, adjustment_factor, adjustment_basis = _baseline_adjusted_delivery_kw(
        record,
        target_baseline_kwh=target_baseline_kwh,
    )
    duration_h = max(EPS, _float_or_none(record.get("duration_h")) or 1.0)
    return {
        "baseline_adjusted_delivery_kw": round(adjusted_kw, 6),
        "baseline_adjusted_delivery_kwh": round(adjusted_kw * duration_h, 6),
        "baseline_adjustment_factor": _round_or_none(adjustment_factor),
        "baseline_adjustment_basis": adjustment_basis,
    }


def _distribution(values: list[float]) -> dict[str, Any]:
    cleaned = sorted(max(0.0, float(value)) for value in values if _float_or_none(value) is not None)
    if not cleaned:
        return {"count": 0}
    return {
        "count": len(cleaned),
        "min_kw": round(cleaned[0], 6),
        "p10_kw": round(_percentile(cleaned, 0.10), 6),
        "p25_kw": round(_percentile(cleaned, 0.25), 6),
        "median_kw": round(_percentile(cleaned, 0.50), 6),
        "p70_kw": round(_percentile(cleaned, 0.70), 6),
        "p75_kw": round(_percentile(cleaned, 0.75), 6),
        "p90_kw": round(_percentile(cleaned, 0.90), 6),
        "max_kw": round(cleaned[-1], 6),
        "mean_kw": round(sum(cleaned) / len(cleaned), 6),
        "basis": "baseline_adjusted_historical_realized_delivery_kw",
    }


def _percentile(sorted_values: list[float], quantile: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = max(0.0, min(1.0, quantile)) * (len(sorted_values) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1.0 - frac) + sorted_values[hi] * frac


def _confidence(neighbors: list[tuple[float, Mapping[str, Any]]]) -> float:
    if not neighbors:
        return 0.0
    best = max(score for score, _ in neighbors)
    mean_score = sum(score for score, _ in neighbors) / len(neighbors)
    return round(min(1.0, 0.5 * min(1.0, best / 8.0) + 0.5 * min(1.0, mean_score / 8.0)), 6)


def _event_model_bid_kwh(event: Mapping[str, Any], *, duration_h: float) -> tuple[float | None, str]:
    summary = event.get("capacity_window_summary") or {}
    value = _float_or_none(summary.get("recommended_bid_energy_kwh"))
    if value is not None:
        return max(0.0, value), "capacity_window_summary.recommended_bid_energy_kwh"
    assessment = ((event.get("capacity_assessment") or {}).get("assessment") or {})
    bid_kw = _float_or_none(assessment.get("recommended_bid_kw"))
    if bid_kw is not None:
        return max(0.0, bid_kw * duration_h), "capacity_assessment.recommended_bid_kw"
    target = _float_or_none(event.get("demand_target_shed_kwh"))
    if target is not None:
        return max(0.0, target), "event.demand_target_shed_kwh"
    return None, "missing"


def _event_baseline_kwh(event: Mapping[str, Any]) -> float | None:
    return _first_float(
        event.get("counterfactual_baseline_kwh"),
        event.get("counterfactual_capacity_upper_bound_kwh"),
        event.get("demand_baseline_kwh"),
        event.get("estimated_baseline_kwh"),
    )


def _event_day(event: Mapping[str, Any], trigger_h: float) -> int | None:
    day = _int_or_none(event.get("day"))
    if day is not None:
        return day
    if trigger_h >= 0:
        return int(trigger_h // 24.0) + 1
    return None


def _memory_source_day(metadata: Mapping[str, Any], event_id: str) -> int | None:
    for key in ("memory_source_day", "source_day", "day"):
        parsed = _int_or_none(metadata.get(key))
        if parsed is not None:
            return parsed
    sample_id = str(metadata.get("memory_sample_id") or event_id or "")
    for part in sample_id.replace("-", "_").split("_"):
        if len(part) >= 2 and part[0].lower() == "d":
            parsed = _int_or_none(part[1:])
            if parsed is not None:
                return parsed
    return None


def _capacity_assessment_value(event: Mapping[str, Any], key: str) -> float | None:
    assessment = ((event.get("capacity_assessment") or {}).get("assessment") or {})
    return _float_or_none(assessment.get(key))


def _selected_strategy_id(event: Mapping[str, Any]) -> str:
    selected = event.get("selected_strategy") or {}
    if isinstance(selected, Mapping):
        return str(selected.get("id") or selected.get("label") or "")
    return ""


def _selected_strategy_reason(event: Mapping[str, Any]) -> str:
    selected = event.get("selected_strategy") or {}
    if isinstance(selected, Mapping):
        reason = selected.get("reason") or selected.get("rationale") or ""
        return str(reason)[:500]
    return ""


def _entity_id(metadata: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    return str(
        metadata.get("household_id")
        or metadata.get("persona_id")
        or result.get("household_id")
        or result.get("persona_id")
        or result.get("user_label")
        or ""
    )


def _city(metadata: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    return str(metadata.get("city") or result.get("city") or result.get("weather") or "")


def _method(metadata: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    return str(metadata.get("method") or result.get("method") or "")


def _first_float(*values: Any) -> float | None:
    for value in values:
        parsed = _float_or_none(value)
        if parsed is not None:
            return parsed
    return None


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _round_or_none(value: float | None) -> float | None:
    return round(float(value), 6) if value is not None else None
