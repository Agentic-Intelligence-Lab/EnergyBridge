"""Event-level baseline and shed estimation for VPP capacity evaluation.

The estimator separates two concepts that were previously easy to conflate:

* capacity/bid estimation before a VPP event; and
* actual shed estimation after an event using a defensible counterfactual load.

It supports two transparent baselines:

* historical same-time samples, optionally weather adjusted; and
* a short pre-event window from the same run as a low-confidence fallback.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from statistics import mean
from typing import Any, Iterable, Mapping, Sequence

EPS = 1e-9


@dataclass(frozen=True)
class EventBaselineConfig:
    """Tunable parameters for event baseline selection."""

    pre_event_hours: float = 1.0
    required_history_days: int = 3
    min_coverage_ratio: float = 0.8
    weather_sensitivity_per_c: float = 0.03
    weather_factor_min: float = 0.8
    weather_factor_max: float = 1.2


def estimate_event_baseline_and_shed(
    event: Mapping[str, Any],
    actual_rows: Iterable[Mapping[str, Any]],
    *,
    historical_rows: Iterable[Mapping[str, Any]] | None = None,
    actual_kwh: float | None = None,
    config: EventBaselineConfig | None = None,
) -> dict[str, Any]:
    """Estimate a VPP event baseline and realized shed.

    Args:
        event: Mapping with at least ``id``, ``trigger_h`` and ``end_h``.
        actual_rows: Current-run timestep rows. Expected fields are ``sim_h``,
            ``dt_h``, ``power_kw`` and optionally ``outdoor_temperature_c``.
        historical_rows: Normal-operation/no-DR rows from previous days or a
            no-DR counterfactual run. Rows use the same field names as
            ``actual_rows``; timestamp-only rows are also accepted.
        actual_kwh: Optional measured event-window energy, e.g. from the
            EnergyPlus meter. When omitted, it is integrated from rows.
        config: Optional estimator configuration.

    Returns:
        JSON-serializable diagnostics. ``actual_shed_kwh`` is only populated
        when a baseline has enough coverage.
    """
    cfg = config or EventBaselineConfig()
    actual_rows_list = list(actual_rows or [])
    historical_rows_list = list(historical_rows or [])
    start_h = _f(event.get("trigger_h"))
    end_h = _f(event.get("end_h"))
    duration_h = max(EPS, end_h - start_h)
    event_id = str(event.get("id", ""))

    event_actual = _window_energy(actual_rows_list, start_h, end_h)
    measured_actual_kwh = _optional_float(actual_kwh)
    if measured_actual_kwh is not None:
        event_actual["energy_kwh"] = max(0.0, measured_actual_kwh)
        event_actual["source"] = "measured_event_energy"
    event_outdoor_c = _window_weighted_average(actual_rows_list, start_h, end_h, "outdoor_temperature_c")

    candidates: list[dict[str, Any]] = []
    historical = _historical_same_time_baseline(
        historical_rows_list,
        event_start_h=start_h,
        event_end_h=end_h,
        event_outdoor_c=event_outdoor_c,
        cfg=cfg,
    )
    if historical:
        candidates.append(historical)

    pre_event = _pre_event_baseline(
        actual_rows_list,
        event_start_h=start_h,
        duration_h=duration_h,
        cfg=cfg,
    )
    if pre_event:
        candidates.append(pre_event)

    selected = _select_baseline(candidates, cfg)
    out: dict[str, Any] = {
        "event_id": event_id,
        "method": "event_baseline_historical_same_time_or_pre_event",
        "duration_hours": round(duration_h, 6),
        "actual_kwh": round(max(0.0, event_actual["energy_kwh"]), 6),
        "actual_energy_source": event_actual.get("source", "integrated_rows"),
        "actual_coverage_ratio": round(event_actual["coverage_ratio"], 6),
        "event_outdoor_temperature_c": _round_or_none(event_outdoor_c),
        "candidate_baselines": candidates,
    }
    if selected is None:
        out.update(
            {
                "status": "baseline_unavailable",
                "baseline_kwh": None,
                "baseline_source": None,
                "baseline_confidence": 0.0,
                "actual_shed_kwh": None,
                "actual_shed_avg_kw": None,
                "actual_shed_basis": "unavailable_without_valid_event_baseline",
            }
        )
        return out

    baseline_kwh = max(0.0, float(selected["baseline_kwh"]))
    actual_kwh_final = max(0.0, float(out["actual_kwh"]))
    shed_kwh = max(0.0, baseline_kwh - actual_kwh_final)
    out.update(
        {
            "status": "computed",
            "baseline_kwh": round(baseline_kwh, 6),
            "baseline_avg_kw": round(baseline_kwh / duration_h, 6),
            "baseline_source": selected["source"],
            "baseline_confidence": round(float(selected["confidence"]), 6),
            "actual_shed_kwh": round(shed_kwh, 6),
            "actual_shed_avg_kw": round(shed_kwh / duration_h, 6),
            "actual_shed_basis": f"{selected['source']}_baseline_minus_event_actual",
            "selected_baseline": selected,
        }
    )
    return out


def estimate_vpp_event_baselines(
    events: Sequence[Mapping[str, Any]],
    actual_rows: Iterable[Mapping[str, Any]],
    *,
    historical_rows: Iterable[Mapping[str, Any]] | None = None,
    actual_kwh_by_event: Mapping[str, float] | None = None,
    config: EventBaselineConfig | None = None,
) -> dict[str, dict[str, Any]]:
    """Estimate baselines for multiple VPP events."""
    actual_rows_list = list(actual_rows or [])
    historical_rows_list = list(historical_rows or [])
    measured = dict(actual_kwh_by_event or {})
    return {
        str(event.get("id", index + 1)): estimate_event_baseline_and_shed(
            event,
            actual_rows_list,
            historical_rows=historical_rows_list,
            actual_kwh=measured.get(str(event.get("id", ""))),
            config=config,
        )
        for index, event in enumerate(events)
    }


def _historical_same_time_baseline(
    rows: Sequence[Mapping[str, Any]],
    *,
    event_start_h: float,
    event_end_h: float,
    event_outdoor_c: float | None,
    cfg: EventBaselineConfig,
) -> dict[str, Any] | None:
    samples = _daily_window_samples(rows, event_start_h % 24.0, event_end_h % 24.0)
    energy_samples = [item["energy_kwh"] for item in samples if item["coverage_ratio"] >= cfg.min_coverage_ratio]
    if not energy_samples:
        return None
    energy_values = sorted(float(v) for v in energy_samples)
    outdoor_values = [
        item["outdoor_temperature_c"]
        for item in samples
        if item["coverage_ratio"] >= cfg.min_coverage_ratio and item["outdoor_temperature_c"] is not None
    ]
    p50 = _quantile(energy_values, 0.50)
    weather_factor = 1.0
    mean_history_outdoor = mean(outdoor_values) if outdoor_values else None
    if event_outdoor_c is not None and mean_history_outdoor is not None:
        delta_c = event_outdoor_c - mean_history_outdoor
        weather_factor = _clamp(
            1.0 + cfg.weather_sensitivity_per_c * delta_c,
            cfg.weather_factor_min,
            cfg.weather_factor_max,
        )
    adjusted = p50 * weather_factor
    sample_score = min(1.0, len(energy_values) / max(1, cfg.required_history_days))
    spread = _quantile(energy_values, 0.90) - _quantile(energy_values, 0.10)
    stability_score = 1.0 / (1.0 + spread / max(0.2, p50))
    confidence = sample_score * stability_score
    return {
        "source": "historical_same_time_weather_adjusted",
        "baseline_kwh": round(adjusted, 6),
        "raw_p50_kwh": round(p50, 6),
        "raw_mean_kwh": round(mean(energy_values), 6),
        "raw_p10_kwh": round(_quantile(energy_values, 0.10), 6),
        "raw_p90_kwh": round(_quantile(energy_values, 0.90), 6),
        "n_history_days": len(energy_values),
        "required_history_days": cfg.required_history_days,
        "weather_adjustment_factor": round(weather_factor, 6),
        "event_outdoor_temperature_c": _round_or_none(event_outdoor_c),
        "history_mean_outdoor_temperature_c": _round_or_none(mean_history_outdoor),
        "confidence": round(confidence, 6),
        "coverage_ratio": 1.0,
    }


def _pre_event_baseline(
    rows: Sequence[Mapping[str, Any]],
    *,
    event_start_h: float,
    duration_h: float,
    cfg: EventBaselineConfig,
) -> dict[str, Any] | None:
    lookback_h = max(EPS, cfg.pre_event_hours)
    pre = _window_energy(rows, event_start_h - lookback_h, event_start_h)
    if pre["coverage_ratio"] + EPS < cfg.min_coverage_ratio:
        return None
    baseline_kwh = pre["avg_kw"] * duration_h
    confidence = 0.45 * min(1.0, pre["coverage_ratio"])
    return {
        "source": "pre_event_short_window",
        "baseline_kwh": round(max(0.0, baseline_kwh), 6),
        "pre_event_avg_kw": round(max(0.0, pre["avg_kw"]), 6),
        "pre_event_hours": round(lookback_h, 6),
        "coverage_ratio": round(pre["coverage_ratio"], 6),
        "confidence": round(confidence, 6),
    }


def _select_baseline(candidates: Sequence[Mapping[str, Any]], cfg: EventBaselineConfig) -> dict[str, Any] | None:
    historical = [
        dict(item)
        for item in candidates
        if item.get("source") == "historical_same_time_weather_adjusted"
        and int(item.get("n_history_days", 0)) >= cfg.required_history_days
    ]
    if historical:
        return max(historical, key=lambda item: float(item.get("confidence", 0.0)))
    eligible = [dict(item) for item in candidates if float(item.get("coverage_ratio", 0.0)) >= cfg.min_coverage_ratio]
    if eligible:
        return max(eligible, key=lambda item: float(item.get("confidence", 0.0)))
    return None


def _daily_window_samples(rows: Sequence[Mapping[str, Any]], start_hod: float, end_hod: float) -> list[dict[str, Any]]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        if _row_is_dr_event(row):
            continue
        key = _day_key(row)
        if key is None:
            continue
        groups.setdefault(key, []).append(row)
    samples: list[dict[str, Any]] = []
    duration_h = _hod_window_duration(start_hod, end_hod)
    for day_key, day_rows in groups.items():
        energy = 0.0
        coverage = 0.0
        outdoor_weighted = 0.0
        outdoor_weight = 0.0
        for row in day_rows:
            start = _row_hod(row)
            if start is None:
                continue
            dt_h = _row_dt_h(row)
            power_kw = _row_power_kw(row)
            for offset in (0.0, 24.0):
                overlap = _interval_overlap(start + offset, start + offset + dt_h, start_hod, start_hod + duration_h)
                if overlap <= EPS:
                    continue
                energy += power_kw * overlap
                coverage += overlap
                outdoor = _row_outdoor_c(row)
                if outdoor is not None:
                    outdoor_weighted += outdoor * overlap
                    outdoor_weight += overlap
        if coverage > EPS:
            samples.append(
                {
                    "day_key": day_key,
                    "energy_kwh": energy,
                    "coverage_ratio": min(1.0, coverage / max(EPS, duration_h)),
                    "outdoor_temperature_c": (
                        outdoor_weighted / outdoor_weight if outdoor_weight > EPS else None
                    ),
                }
            )
    return samples


def _window_energy(rows: Sequence[Mapping[str, Any]], start_h: float, end_h: float) -> dict[str, Any]:
    duration_h = max(EPS, end_h - start_h)
    energy = 0.0
    coverage = 0.0
    for row in rows:
        row_start = _row_sim_h(row)
        if row_start is None:
            continue
        dt_h = _row_dt_h(row)
        overlap = _interval_overlap(row_start, row_start + dt_h, start_h, end_h)
        if overlap <= EPS:
            continue
        energy += _row_power_kw(row) * overlap
        coverage += overlap
    return {
        "energy_kwh": energy,
        "avg_kw": energy / duration_h,
        "coverage_ratio": min(1.0, coverage / duration_h),
        "source": "integrated_rows",
    }


def _window_weighted_average(
    rows: Sequence[Mapping[str, Any]],
    start_h: float,
    end_h: float,
    key: str,
) -> float | None:
    total = 0.0
    weight = 0.0
    for row in rows:
        row_start = _row_sim_h(row)
        if row_start is None:
            continue
        dt_h = _row_dt_h(row)
        overlap = _interval_overlap(row_start, row_start + dt_h, start_h, end_h)
        if overlap <= EPS:
            continue
        value = _optional_float(row.get(key))
        if value is None and key == "outdoor_temperature_c":
            value = _optional_float(row.get("outdoor_temp_c"))
        if value is None:
            continue
        total += value * overlap
        weight += overlap
    return total / weight if weight > EPS else None


def _row_sim_h(row: Mapping[str, Any]) -> float | None:
    value = _optional_float(row.get("sim_h"))
    if value is not None:
        return value
    ts = _parse_timestamp(row.get("timestamp"))
    if ts is None:
        return None
    return ts.weekday() * 24.0 + ts.hour + ts.minute / 60.0 + ts.second / 3600.0


def _row_hod(row: Mapping[str, Any]) -> float | None:
    value = _optional_float(row.get("hod"))
    if value is not None:
        return value % 24.0
    sim_h = _row_sim_h(row)
    return sim_h % 24.0 if sim_h is not None else None


def _row_dt_h(row: Mapping[str, Any]) -> float:
    value = _optional_float(row.get("dt_h"))
    if value is not None and value > EPS:
        return value
    minutes = _optional_float(row.get("time_step_minutes"))
    if minutes is not None and minutes > EPS:
        return minutes / 60.0
    return 10.0 / 60.0


def _row_power_kw(row: Mapping[str, Any]) -> float:
    for key in ("power_kw", "household_power_kw", "facility_power_kw"):
        value = _optional_float(row.get(key))
        if value is not None:
            return max(0.0, value)
    watts = _optional_float(row.get("facility_power_w"))
    if watts is not None:
        return max(0.0, watts / 1000.0)
    return 0.0


def _row_outdoor_c(row: Mapping[str, Any]) -> float | None:
    for key in ("outdoor_temperature_c", "outdoor_temp_c"):
        value = _optional_float(row.get(key))
        if value is not None:
            return value
    return None


def _row_is_dr_event(row: Mapping[str, Any]) -> bool:
    value = row.get("is_dr_event", row.get("vpp_active", False))
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "none"}
    return bool(value)


def _day_key(row: Mapping[str, Any]) -> str | None:
    if row.get("date") is not None:
        return str(row["date"])
    ts = _parse_timestamp(row.get("timestamp"))
    if ts is not None:
        return ts.date().isoformat()
    sim_h = _row_sim_h(row)
    if sim_h is None:
        return None
    return f"sim_day_{int(sim_h // 24)}"


def _parse_timestamp(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value
    if value in (None, ""):
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _hod_window_duration(start_hod: float, end_hod: float) -> float:
    start = start_hod % 24.0
    end = end_hod % 24.0
    if end <= start:
        end += 24.0
    return max(EPS, end - start)


def _interval_overlap(start_a: float, end_a: float, start_b: float, end_b: float) -> float:
    return max(0.0, min(end_a, end_b) - max(start_a, start_b))


def _quantile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    pos = q * (len(sorted_values) - 1)
    lower = int(pos)
    upper = min(lower + 1, len(sorted_values) - 1)
    weight = pos - lower
    return float(sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight)


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _f(value: Any) -> float:
    out = _optional_float(value)
    return out if out is not None else 0.0


def _round_or_none(value: float | None) -> float | None:
    return round(float(value), 6) if value is not None else None
