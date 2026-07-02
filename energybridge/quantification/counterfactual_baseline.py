"""Counterfactual no-DR baselines for VPP event settlement.

The benchmark has two different questions:

* Did a controller keep controllable appliances out of the VPP window?
* How much energy did it actually reduce against a no-DR counterfactual?

This module handles the second question.  It stores event-window electricity
from a no-DR/counterfactual run and applies it back to controller runs with the
same user, city, duration, and event schedule.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

EPS = 1e-9


def build_counterfactual_library(
    baseline_items: Iterable[tuple[Mapping[str, Any], Mapping[str, Any], str | Path | None]],
) -> dict[str, Any]:
    """Build a JSON-serializable library from benchmark results.

    Args:
        baseline_items: Iterable of ``(result_json, metadata, source_path)``.
            ``metadata`` is typically a row from ``run_baseline_matrix.py`` or
            ``run_household_matrix.py`` so it contains stable persona/household
            identifiers that are not always present in ``benchmark_result.json``.
    """
    baselines = []
    for result, metadata, source_path in baseline_items:
        record = extract_counterfactual_baseline(
            result,
            metadata=metadata,
            source_path=source_path,
        )
        if record.get("event_baselines"):
            baselines.append(record)
    return {
        "schema_version": "counterfactual_vpp_baseline_library_v1",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "description": (
            "No-DR/counterfactual event-window electricity used to compute "
            "realized VPP delivery as baseline_kWh - actual_kWh."
        ),
        "baselines": baselines,
    }


def extract_counterfactual_baseline(
    result: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
    source_path: str | Path | None = None,
) -> dict[str, Any]:
    """Extract event-window electricity from a no-DR result."""
    meta = metadata or {}
    events = []
    for event in result.get("vpp_event_log") or []:
        actual_kwh = _float_or_none(event.get("actual_kwh"))
        if actual_kwh is None:
            continue
        trigger_h = _float_or_none(event.get("trigger_h"))
        end_h = _float_or_none(event.get("end_h"))
        if trigger_h is None or end_h is None or end_h <= trigger_h:
            continue
        duration_h = max(EPS, end_h - trigger_h)
        events.append(
            {
                "id": str(event.get("id") or f"event_{len(events) + 1}"),
                "day": _int_or_none(event.get("day")),
                "trigger_h": round(trigger_h, 6),
                "end_h": round(end_h, 6),
                "duration_h": round(duration_h, 6),
                "baseline_kwh": round(max(0.0, actual_kwh), 6),
                "baseline_avg_kw": round(max(0.0, actual_kwh) / duration_h, 6),
                "source_event_actual_kwh": round(max(0.0, actual_kwh), 6),
            }
        )
    total_duration_h = sum(float(event["duration_h"]) for event in events)
    total_kwh = sum(float(event["baseline_kwh"]) for event in events)
    entity_id = _entity_id(meta, result)
    city = _city(meta, result)
    return {
        "baseline_id": _baseline_id(entity_id, city, meta, result),
        "entity_id": entity_id,
        "entity_type": "household" if meta.get("household_id") else "persona",
        "persona_id": str(meta.get("persona_id") or entity_id or ""),
        "household_id": str(meta.get("household_id") or ""),
        "city": city,
        "days": _int_or_none(meta.get("days", result.get("sim_days"))),
        "start_date": str(meta.get("start_date") or result.get("start_date") or ""),
        "baseline_method": str(meta.get("method") or result.get("method") or "no_dr"),
        "method_scope": str(meta.get("method_scope") or "all_methods"),
        "vpp_schedule_source": str(result.get("vpp_schedule_source") or meta.get("vpp_events_json") or ""),
        "source_result_path": str(source_path or meta.get("output_dir") or ""),
        "event_baselines": events,
        "total_baseline_kwh": round(total_kwh, 6),
        "avg_baseline_kw": round(total_kwh / total_duration_h, 6) if total_duration_h > EPS else None,
        "total_duration_h": round(total_duration_h, 6),
        "basis": "no_dr_counterfactual_event_window_energy",
    }


def apply_counterfactual_baseline(
    result: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Return a copy of ``result`` with no-DR baseline delivery fields attached."""
    out = dict(result)
    events = [dict(event) for event in out.get("vpp_event_log") or []]
    baseline_by_id = {str(event.get("id")): event for event in baseline.get("event_baselines") or []}
    baseline_by_window = {
        _event_window_key(event): event
        for event in baseline.get("event_baselines") or []
        if _event_window_key(event) is not None
    }

    shed_values: list[float] = []
    target_ratios: list[float] = []
    bid_ratios: list[float] = []
    baseline_bound_ratios: list[float] = []
    baseline_total = 0.0
    actual_total = 0.0
    duration_total = 0.0
    matched = 0

    for event in events:
        baseline_event = baseline_by_id.get(str(event.get("id")))
        if baseline_event is None:
            key = _event_window_key(event)
            baseline_event = baseline_by_window.get(key) if key is not None else None
        if baseline_event is None:
            event["counterfactual_baseline_status"] = "missing_event_baseline"
            continue
        baseline_kwh = _float_or_none(baseline_event.get("baseline_kwh"))
        actual_kwh = _float_or_none(event.get("actual_kwh"))
        if baseline_kwh is None or actual_kwh is None:
            event["counterfactual_baseline_status"] = "missing_energy"
            continue
        trigger_h = _float_or_none(event.get("trigger_h"))
        end_h = _float_or_none(event.get("end_h"))
        duration_h = max(EPS, (end_h - trigger_h) if trigger_h is not None and end_h is not None else _float_or_none(baseline_event.get("duration_h")) or 1.0)
        shed_kwh = baseline_kwh - actual_kwh
        baseline_total += baseline_kwh
        actual_total += actual_kwh
        duration_total += duration_h
        shed_values.append(shed_kwh)
        matched += 1

        target_kwh = _first_float(
            event.get("demand_target_shed_kwh"),
            event.get("target_shed_kwh"),
            event.get("demand_target_kwh"),
        )
        bid_kwh = _event_bid_energy_kwh(event)
        target_ratio = shed_kwh / target_kwh if target_kwh and target_kwh > EPS else None
        bid_ratio = shed_kwh / bid_kwh if bid_kwh and bid_kwh > EPS else None
        baseline_bound_ratio = shed_kwh / baseline_kwh if baseline_kwh > EPS else None
        if target_ratio is not None:
            target_ratios.append(target_ratio)
        if bid_ratio is not None:
            bid_ratios.append(bid_ratio)
        if baseline_bound_ratio is not None:
            baseline_bound_ratios.append(baseline_bound_ratio)

        event.update(
            {
                "counterfactual_baseline_status": "matched",
                "counterfactual_baseline_id": baseline.get("baseline_id"),
                "counterfactual_baseline_kwh": round(baseline_kwh, 6),
                "counterfactual_baseline_avg_kw": round(baseline_kwh / duration_h, 6),
                "counterfactual_capacity_upper_bound_kwh": round(baseline_kwh, 6),
                "counterfactual_capacity_upper_bound_avg_kw": round(baseline_kwh / duration_h, 6),
                "counterfactual_actual_kwh": round(actual_kwh, 6),
                "counterfactual_actual_shed_kwh": round(shed_kwh, 6),
                "counterfactual_actual_shed_avg_kw": round(shed_kwh / duration_h, 6),
                "counterfactual_actual_shed_nonnegative_kwh": round(max(0.0, shed_kwh), 6),
                "counterfactual_delivery_ratio_vs_target": _round_or_none(target_ratio),
                "counterfactual_delivery_ratio_vs_recommended_bid": _round_or_none(bid_ratio),
                "counterfactual_delivery_ratio_vs_baseline_upper_bound": _round_or_none(baseline_bound_ratio),
                "counterfactual_baseline_basis": baseline.get("basis", "no_dr_counterfactual_event_window_energy"),
                "counterfactual_capacity_upper_bound_basis": "no_dr_counterfactual_event_window_energy",
                # Override the legacy field so downstream summaries use the
                # explicit counterfactual, not an event-prebaseline heuristic.
                "actual_shed_kwh": round(shed_kwh, 6),
                "actual_shed_avg_kw": round(shed_kwh / duration_h, 6),
                "actual_shed_basis": "no_dr_counterfactual_baseline_minus_event_actual",
            }
        )

    out["vpp_event_log"] = events
    event_count = len(events)
    if matched <= 0:
        status = "missing"
    elif matched < event_count:
        status = "partial"
    else:
        status = "matched"
    out["counterfactual_baseline_status"] = status
    out["counterfactual_baseline_id"] = baseline.get("baseline_id")
    out["counterfactual_baseline_source"] = baseline.get("source_result_path")
    out["counterfactual_baseline_basis"] = baseline.get("basis")
    out["counterfactual_baseline_event_count"] = matched
    out["counterfactual_baseline_missing_event_count"] = max(0, event_count - matched)
    if matched:
        shed_total = sum(shed_values)
        out.update(
            {
                "counterfactual_baseline_vpp_window_kwh": round(baseline_total, 6),
                "counterfactual_actual_vpp_window_kwh": round(actual_total, 6),
                "counterfactual_capacity_upper_bound_total_kwh": round(baseline_total, 6),
                "counterfactual_capacity_upper_bound_avg_per_hour_kwh": (
                    round(baseline_total / duration_total, 6) if duration_total > EPS else None
                ),
                "counterfactual_capacity_upper_bound_basis": "no_dr_counterfactual_event_window_energy",
                "counterfactual_baseline_vpp_window_avg_per_hour_kwh": (
                    round(baseline_total / duration_total, 6) if duration_total > EPS else None
                ),
                "counterfactual_actual_vpp_window_avg_per_hour_kwh": (
                    round(actual_total / duration_total, 6) if duration_total > EPS else None
                ),
                "counterfactual_actual_shed_total_kwh": round(shed_total, 6),
                "counterfactual_actual_shed_avg_per_event_kwh": round(shed_total / matched, 6),
                "counterfactual_actual_shed_avg_per_hour_kwh": (
                    round(shed_total / duration_total, 6) if duration_total > EPS else None
                ),
                "counterfactual_delivery_ratio_vs_target_avg": _mean_or_none(target_ratios),
                "counterfactual_delivery_ratio_vs_recommended_bid_avg": _mean_or_none(bid_ratios),
                "counterfactual_delivery_ratio_vs_baseline_upper_bound_avg": _mean_or_none(baseline_bound_ratios),
                "counterfactual_delivery_ratio_vs_baseline_upper_bound_total": (
                    round(shed_total / baseline_total, 6) if baseline_total > EPS else None
                ),
                "vpp_energy_reduction_total_kwh": round(shed_total, 6),
                "vpp_energy_reduction_avg_per_event_kwh": round(shed_total / matched, 6),
                "vpp_energy_reduction_avg_per_hour_kwh": (
                    round(shed_total / duration_total, 6) if duration_total > EPS else None
                ),
                "vpp_energy_reduction_kwh": (
                    round(shed_total / duration_total, 6) if duration_total > EPS else None
                ),
                "vpp_actual_shed_kwh": (
                    round(shed_total / duration_total, 6) if duration_total > EPS else None
                ),
                "vpp_energy_reduction_basis": "no_dr_counterfactual_baseline",
            }
        )
    return out


def find_matching_baseline(
    library: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Find the best counterfactual baseline for a result."""
    meta = metadata or {}
    entity_id = _entity_id(meta, result)
    city = _city(meta, result).lower()
    days = _int_or_none(meta.get("days", result.get("sim_days")))
    start_date = str(meta.get("start_date") or result.get("start_date") or "")
    candidates = []
    for baseline in library.get("baselines") or []:
        if str(baseline.get("entity_id") or "") != entity_id:
            continue
        if str(baseline.get("city") or "").lower() != city:
            continue
        if days is not None and _int_or_none(baseline.get("days")) != days:
            continue
        schedule_match = _schedule_match_count(result, baseline)
        result_event_count = len(result.get("vpp_event_log") or [])
        if result_event_count and schedule_match < result_event_count:
            continue
        score = 0
        if start_date and str(baseline.get("start_date") or "") == start_date:
            score += 2
        elif not str(baseline.get("start_date") or ""):
            score += 1
        score += schedule_match or len(baseline.get("event_baselines") or [])
        candidates.append((score, baseline))
    if not candidates:
        return None
    return dict(max(candidates, key=lambda item: item[0])[1])


def _schedule_match_count(result: Mapping[str, Any], baseline: Mapping[str, Any]) -> int:
    result_events = list(result.get("vpp_event_log") or [])
    if not result_events:
        return 0
    baseline_by_id = {str(event.get("id")): event for event in baseline.get("event_baselines") or []}
    baseline_by_window = {
        _event_window_key(event): event
        for event in baseline.get("event_baselines") or []
        if _event_window_key(event) is not None
    }
    matched = 0
    for event in result_events:
        if str(event.get("id")) in baseline_by_id:
            matched += 1
            continue
        key = _event_window_key(event)
        if key is not None and key in baseline_by_window:
            matched += 1
    return matched


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


def _baseline_id(entity_id: str, city: str, metadata: Mapping[str, Any], result: Mapping[str, Any]) -> str:
    days = _int_or_none(metadata.get("days", result.get("sim_days")))
    start = str(metadata.get("start_date") or result.get("start_date") or "")
    method = str(metadata.get("method") or result.get("method") or "no_dr")
    parts = [entity_id or "unknown", city.lower() or "unknown_city", f"{days or 'x'}days", start or "no_start", method]
    return "|".join(parts)


def _event_window_key(event: Mapping[str, Any]) -> tuple[int | None, float, float] | None:
    trigger_h = _float_or_none(event.get("trigger_h"))
    end_h = _float_or_none(event.get("end_h"))
    if trigger_h is None or end_h is None:
        return None
    return (_int_or_none(event.get("day")), round(trigger_h, 6), round(end_h, 6))


def _event_bid_energy_kwh(event: Mapping[str, Any]) -> float | None:
    summary = event.get("capacity_window_summary") or {}
    value = _float_or_none(summary.get("recommended_bid_energy_kwh"))
    if value is not None:
        return max(0.0, value)
    assessment = ((event.get("capacity_assessment") or {}).get("assessment") or {})
    bid_kw = _float_or_none(assessment.get("recommended_bid_kw"))
    if bid_kw is None:
        return None
    trigger_h = _float_or_none(event.get("trigger_h"))
    end_h = _float_or_none(event.get("end_h"))
    duration_h = max(EPS, (end_h - trigger_h) if trigger_h is not None and end_h is not None else 1.0)
    return max(0.0, bid_kw * duration_h)


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


def _mean_or_none(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 6) if values else None
