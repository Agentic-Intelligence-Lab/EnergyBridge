"""Method-blind physical and tariff evidence for open household planning.

The helpers in this module deliberately do not rank candidates or choose a
plan.  They turn observable device capabilities, a tariff, an event window,
and actuator-facing controls into compact evidence cards that a base model can
deliberate over.  Exact values are emitted only for simple fixed-power loads;
thermal and state-dependent devices are explicitly labelled as bounds or
directional evidence.
"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math
import re
from typing import Any, Mapping, Sequence


ENERGY_IMPACT_SCHEMA_VERSION = "energybridge.candidate_impact.v3"
TARIFF_SNAPSHOT_VERSION = "energybridge.hourly_tariff.v1"
FLEXIBLE_LOAD_OPPORTUNITY_VERSION = "energybridge.flexible_load_opportunities.v1"
FLEXIBLE_LOAD_PROMPT_CAPSULE_VERSION = "energybridge.flexible_load_prompt_capsule.v1"
DECISION_EPOCH_SNAPSHOT_VERSION = "energybridge.decision_epochs.v1"

_SHIFTABLE_DEVICES = ("washer", "dishwasher", "dryer")
_ACTION_KEYS = {
    "washer": ("washer_start_h", "washer_skip"),
    "dishwasher": ("dishwasher_start_h", "dishwasher_skip"),
    "dryer": ("dryer_start_h", "dryer_skip"),
}


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _round(value: float | None, digits: int = 6) -> float | None:
    return None if value is None else round(float(value), digits)


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _safe_id(value: Any, fallback: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "")).strip("-._")
    return text[:96] or fallback


def build_hourly_tariff_snapshot(
    hourly_prices: Mapping[Any, Any] | Sequence[Any] | None,
    *,
    unit: str = "cost/kWh",
) -> dict[str, Any]:
    """Build a provider-neutral recurring 24-hour tariff snapshot.

    ``hourly_prices`` may be a mapping keyed by hour, a 24-value sequence, or
    a sequence of ``{"hour": ..., "price": ...}`` records.  Source paths and
    provider labels are intentionally not accepted into the model-visible
    result.
    """
    parsed: dict[int, float] = {}
    if isinstance(hourly_prices, Mapping):
        items = list(hourly_prices.items())
        for raw_hour, raw_price in items:
            hour = _finite(raw_hour)
            price = _finite(raw_price)
            if hour is None or price is None or not float(hour).is_integer():
                continue
            hour_i = int(hour)
            if 0 <= hour_i <= 23:
                parsed[hour_i] = price
    elif isinstance(hourly_prices, Sequence) and not isinstance(
        hourly_prices, (str, bytes, bytearray)
    ):
        for index, item in enumerate(hourly_prices):
            if isinstance(item, Mapping):
                hour = _finite(item.get("hour", item.get("hour_start")))
                price = _finite(item.get("price", item.get("value")))
            else:
                hour, price = float(index), _finite(item)
            if hour is None or price is None or not float(hour).is_integer():
                continue
            hour_i = int(hour)
            if 0 <= hour_i <= 23:
                parsed[hour_i] = price
    safe_unit = re.sub(r"[^A-Za-z0-9_ /().-]+", "", str(unit or "cost/kWh"))[:80]
    hours = [{"hour": hour, "price": _round(parsed[hour])} for hour in sorted(parsed)]
    return {
        "schema_version": TARIFF_SNAPSHOT_VERSION,
        "unit": safe_unit or "cost/kWh",
        "hours": hours,
        "coverage_hours": len(hours),
        "complete_day": len(hours) == 24,
    }


def _tariff_map(tariff: Mapping[str, Any] | None) -> tuple[dict[int, float], str]:
    tariff = tariff if isinstance(tariff, Mapping) else {}
    values: dict[int, float] = {}
    for item in list(tariff.get("hours") or []):
        if not isinstance(item, Mapping):
            continue
        hour, price = _finite(item.get("hour")), _finite(item.get("price"))
        if hour is not None and price is not None and float(hour).is_integer():
            hour_i = int(hour)
            if 0 <= hour_i <= 23:
                values[hour_i] = price
    return values, str(tariff.get("unit") or "cost/kWh")[:80]


def build_decision_epoch_snapshot(
    *,
    observable_state: Mapping[str, Any] | None,
    event: Mapping[str, Any] | None = None,
    ordinary_plan: Mapping[str, Any] | None = None,
    horizon_h: float = 24.0,
) -> dict[str, Any]:
    """Expose observable future state-change times without choosing a cadence.

    Event-triggered and receding-horizon controllers normally reconsider a
    plan when new information can arrive, a tariff interval changes, or a
    service boundary is reached.  This tool enumerates those times so the base
    model can decide whether and when another check is useful.  It never ranks
    epochs, inserts a mandatory callback, or selects a plan.
    """
    state = observable_state if isinstance(observable_state, Mapping) else {}
    event = event if isinstance(event, Mapping) else {}
    ordinary = ordinary_plan if isinstance(ordinary_plan, Mapping) else {}
    time_state = state.get("time")
    time_state = time_state if isinstance(time_state, Mapping) else {}
    current = _finite(time_state.get("simulation_hour"))
    if current is None:
        return {
            "schema_version": DECISION_EPOCH_SNAPSHOT_VERSION,
            "available": False,
            "epoch_columns": ["simulation_hour", "hour_of_day", "signals"],
            "epoch_rows": [],
            "selection_performed": False,
            "ranking_performed": False,
        }
    horizon = _finite(horizon_h)
    horizon = max(0.25, min(48.0, horizon if horizon is not None else 24.0))
    horizon_end = current + horizon
    epochs: dict[float, list[dict[str, Any]]] = {}

    def future_absolute(hour_of_day: Any) -> float | None:
        hod = _finite(hour_of_day)
        if hod is None:
            return None
        absolute = math.floor(current / 24.0) * 24.0 + (hod % 24.0)
        while absolute <= current + 1e-9:
            absolute += 24.0
        return absolute if absolute <= horizon_end + 1e-9 else None

    def add_epoch(absolute_h: Any, signal: dict[str, Any]) -> None:
        absolute = _finite(absolute_h)
        if (
            absolute is None
            or absolute <= current + 1e-9
            or absolute > horizon_end + 1e-9
        ):
            return
        key = round(absolute, 6)
        clean = json.loads(json.dumps(signal, ensure_ascii=False, allow_nan=False))
        if clean not in epochs.setdefault(key, []):
            epochs[key].append(clean)

    for kind, key in (("vpp_event_starts", "trigger_h"), ("vpp_event_ends", "end_h")):
        add_epoch(event.get(key), {
            "kind": kind,
            "evidence_path": f"/event/{key}",
        })

    tariff, tariff_unit = _tariff_map(state.get("hourly_tariff"))
    if tariff:
        for hour, price in sorted(tariff.items()):
            previous = tariff.get((hour - 1) % 24)
            if previous is None or abs(price - previous) <= 1e-12:
                continue
            add_epoch(future_absolute(hour), {
                "kind": "tariff_interval_changes",
                "from_price": _round(previous),
                "to_price": _round(price),
                "unit": tariff_unit,
                "evidence_path": f"/observable_state/hourly_tariff/hours/{hour}",
            })

    devices = state.get("device_capabilities")
    devices = devices if isinstance(devices, Mapping) else {}
    for name, raw in devices.items():
        if not isinstance(raw, Mapping) or raw.get("present") is False:
            continue
        for field, kind in (
            ("earliest_h", "service_window_opens"),
            ("latest_h", "service_window_closes"),
            ("arrival_h", "device_becomes_available"),
            ("departure_h", "service_deadline"),
            ("bath_required_h", "service_deadline"),
        ):
            absolute = future_absolute(raw.get(field))
            add_epoch(absolute, {
                "kind": kind,
                "device": str(name),
                "evidence_path": f"/observable_state/device_capabilities/{name}/{field}",
            })

    actions = ordinary.get("appliances")
    actions = actions if isinstance(actions, Mapping) else {}
    for name in _SHIFTABLE_DEVICES:
        start = future_absolute(actions.get(f"{name}_start_h"))
        if start is None or actions.get(f"{name}_skip") is True:
            continue
        add_epoch(start, {
            "kind": "ordinary_service_starts",
            "device": name,
            "evidence_path": f"/ordinary_plan/appliances/{name}_start_h",
        })
        duration = _finite((devices.get(name) or {}).get("duration_h"))
        if duration is not None and duration > 0:
            add_epoch(start + duration, {
                "kind": "ordinary_service_finishes",
                "device": name,
                "evidence_path": f"/observable_state/device_capabilities/{name}/duration_h",
            })
    for name, start_key, end_key in (
        ("water_heater", "water_heater_preheat_start_h", "water_heater_preheat_end_h"),
        ("ev", "ev_charge_start_h", "ev_charge_end_h"),
    ):
        start = future_absolute(actions.get(start_key))
        end = future_absolute(actions.get(end_key))
        if start is not None:
            add_epoch(start, {
                "kind": "ordinary_service_starts",
                "device": name,
                "evidence_path": f"/ordinary_plan/appliances/{start_key}",
            })
        if end is not None:
            if start is not None and end <= start:
                end += 24.0
            add_epoch(end, {
                "kind": "ordinary_service_finishes",
                "device": name,
                "evidence_path": f"/ordinary_plan/appliances/{end_key}",
            })

    rows = [
        [absolute, round(absolute % 24.0, 6), signals]
        for absolute, signals in sorted(epochs.items())
    ]
    return json.loads(json.dumps({
        "schema_version": DECISION_EPOCH_SNAPSHOT_VERSION,
        "available": bool(rows),
        "observed_at_simulation_hour": round(current, 6),
        "horizon_end_simulation_hour": round(horizon_end, 6),
        "epoch_columns": ["simulation_hour", "hour_of_day", "signals"],
        "epoch_rows": rows,
        "selection_performed": False,
        "ranking_performed": False,
        "interpretation": (
            "Rows are unranked opportunities for new observable evidence; "
            "the model may choose any future checkpoint or no checkpoint."
        ),
    }, ensure_ascii=False, allow_nan=False))


def _duration_interval(start: float, end: float) -> float:
    duration = end - start
    if duration < 0:
        duration += 24.0
    return max(0.0, min(24.0, duration))


def _segments(start: float, duration: float) -> list[tuple[float, float]]:
    duration = max(0.0, min(24.0, float(duration)))
    if duration <= 1e-9:
        return []
    start_hod = float(start) % 24.0
    end = start_hod + duration
    if end <= 24.0 + 1e-9:
        return [(start_hod, min(24.0, end))]
    return [(start_hod, 24.0), (0.0, end - 24.0)]


def _overlap_hours(
    start: float,
    duration: float,
    event_start: float | None,
    event_duration: float | None,
) -> float | None:
    if event_start is None or event_duration is None:
        return None
    total = 0.0
    for a_start, a_end in _segments(start, duration):
        for b_start, b_end in _segments(event_start, event_duration):
            total += max(0.0, min(a_end, b_end) - max(a_start, b_start))
    return min(duration, total)


def _interval_cost(
    start: float,
    duration: float,
    power_kw: float | None,
    tariff: Mapping[int, float],
) -> tuple[float | None, list[int]]:
    if power_kw is None or not tariff:
        return None, []
    total = 0.0
    missing: set[int] = set()
    for seg_start, seg_end in _segments(start, duration):
        cursor = seg_start
        while cursor < seg_end - 1e-9:
            hour = int(math.floor(cursor)) % 24
            boundary = min(seg_end, math.floor(cursor) + 1.0)
            span = boundary - cursor
            if hour not in tariff:
                missing.add(hour)
            else:
                total += power_kw * span * tariff[hour]
            cursor = boundary
    return (None if missing else total), sorted(missing)


def _window_start_options(
    earliest_h: float,
    latest_h: float,
    duration_h: float,
    *,
    preferred_h: float | None = None,
    ordinary_h: float | None = None,
) -> list[tuple[float, float]]:
    """Enumerate tariff-relevant starts without selecting one.

    The first tuple item is the local HOD exposed to the model; the second is
    an unwrapped hour used only for distance within an overnight service
    window. Hour boundaries are sufficient for an hourly tariff, while the
    declared endpoints, preferred start, and ordinary start preserve feasible
    fractional choices.
    """
    start_abs = float(earliest_h) % 24.0
    end_abs = float(latest_h) % 24.0
    if end_abs <= start_abs + 1e-9:
        end_abs += 24.0
    latest_start_abs = end_abs - float(duration_h)
    if latest_start_abs < start_abs - 1e-9:
        return []
    values = {start_abs, latest_start_abs}
    first_boundary = int(math.ceil(start_abs - 1e-9))
    last_boundary = int(math.floor(latest_start_abs + 1e-9))
    values.update(float(hour) for hour in range(first_boundary, last_boundary + 1))
    for raw in (preferred_h, ordinary_h):
        if raw is None:
            continue
        candidate = float(raw) % 24.0
        if candidate < start_abs - 1e-9:
            candidate += 24.0
        if start_abs - 1e-9 <= candidate <= latest_start_abs + 1e-9:
            values.add(candidate)
    return [
        (round(value % 24.0, 6), value)
        for value in sorted(values)
    ]


def build_flexible_load_opportunity_snapshot(
    *,
    observable_state: Mapping[str, Any] | None,
    event: Mapping[str, Any] | None,
    ordinary_plan: Mapping[str, Any] | None,
    tariff: Mapping[str, Any] | None,
    max_options_per_device: int = 32,
) -> dict[str, Any]:
    """Expose feasible fixed-load timing tradeoffs without choosing a plan.

    This is a traditional appliance-scheduling primitive: enumerate the
    service window, integrate the tariff for each relevant start, measure VPP
    overlap, and retain routine deviation. It deliberately performs no scalar
    weighting or overall ranking, so a base model still decides whether cost,
    event support, or routine fit matters most for this household.
    """
    devices = _device_capabilities(observable_state)
    ordinary = _physical_plan(ordinary_plan)
    ordinary_actions = (
        ordinary.get("appliances")
        if isinstance(ordinary.get("appliances"), Mapping)
        else {}
    )
    event_start, event_duration, event_card = _event_window(event)
    tariff_map, tariff_unit = _tariff_map(tariff)
    state = observable_state if isinstance(observable_state, Mapping) else {}
    time_card = state.get("time") if isinstance(state.get("time"), Mapping) else {}
    decision_hod = _finite(time_card.get("hour_of_day"))
    live_card = (
        state.get("realtime_device_state")
        if isinstance(state.get("realtime_device_state"), Mapping)
        else {}
    )
    live_services = (
        live_card.get("current_day_service_state")
        if isinstance(live_card.get("current_day_service_state"), Mapping)
        else {}
    )
    live_power = (
        live_card.get("current_power_kw")
        if isinstance(live_card.get("current_power_kw"), Mapping)
        else {}
    )
    device_rows: dict[str, dict[str, Any]] = {}
    for name in _SHIFTABLE_DEVICES:
        capability = devices.get(name) if isinstance(devices.get(name), Mapping) else {}
        if not bool(capability.get("present", False)):
            continue
        earliest = _finite(capability.get("earliest_h"))
        latest = _finite(capability.get("latest_h"))
        duration = _finite(capability.get("duration_h"))
        power = _finite(capability.get("power_kw", capability.get("rated_power_kw")))
        preferred = _finite(capability.get("preferred_h", capability.get("preferred_start_h")))
        ordinary_start = _finite(ordinary_actions.get(f"{name}_start_h"))
        row: dict[str, Any] = {
            "present": True,
            "service_required_today": bool(capability.get("service_required_today", True)),
            "earliest_hod": _round(earliest % 24.0, 3) if earliest is not None else None,
            "latest_finish_hod": _round(latest % 24.0, 3) if latest is not None else None,
            "duration_h": _round(duration, 3),
            "power_kw": _round(power),
            "preferred_start_hod": _round(preferred % 24.0, 3) if preferred is not None else None,
            "ordinary_start_hod": _round(ordinary_start % 24.0, 3) if ordinary_start is not None else None,
            "tariff_unit": tariff_unit,
            "selection_performed": False,
            "decision_hour_hod": _round(decision_hod % 24.0, 3)
            if decision_hod is not None else None,
            "options": [],
        }
        service_state = (
            live_services.get(name)
            if isinstance(live_services.get(name), Mapping)
            else {}
        )
        if bool(service_state.get("completed", False)):
            row["status"] = "service_already_completed"
            row["service_state_locked_before_decision"] = True
            device_rows[name] = row
            continue
        current_power = _finite(live_power.get(name))
        if current_power is not None and current_power > 1e-9:
            row["status"] = "service_already_running"
            row["service_state_locked_before_decision"] = True
            device_rows[name] = row
            continue
        if earliest is None or latest is None or duration is None or duration <= 0.0:
            row["status"] = "insufficient_service_window_evidence"
            device_rows[name] = row
            continue
        option_starts = _window_start_options(
            earliest,
            latest,
            duration,
            preferred_h=preferred,
            ordinary_h=ordinary_start,
        )
        excluded_past = 0
        if decision_hod is not None:
            current_unwrapped = decision_hod % 24.0
            filtered = []
            for start_hod, start_abs in option_starts:
                if start_abs + 1e-9 < current_unwrapped:
                    excluded_past += 1
                else:
                    filtered.append((start_hod, start_abs))
            option_starts = filtered
        option_starts = option_starts[: max(1, int(max_options_per_device))]
        row["excluded_past_start_count"] = excluded_past
        ordinary_cost, _ = (
            _interval_cost(ordinary_start, duration, power, tariff_map)
            if ordinary_start is not None
            else (None, [])
        )
        ordinary_overlap = (
            _overlap_hours(ordinary_start, duration, event_start, event_duration)
            if ordinary_start is not None
            else None
        )
        options = []
        for start_hod, start_abs in option_starts:
            cost, missing_hours = _interval_cost(start_hod, duration, power, tariff_map)
            overlap = _overlap_hours(start_hod, duration, event_start, event_duration)
            preferred_abs = None
            if preferred is not None:
                preferred_abs = preferred % 24.0
                if preferred_abs < (earliest % 24.0) - 1e-9:
                    preferred_abs += 24.0
            options.append({
                "start_hod": _round(start_hod, 3),
                "finish_hod": _round((start_hod + duration) % 24.0, 3),
                "scheduled_energy_kwh": _round(power * duration) if power is not None else None,
                "scheduled_cost": _round(cost),
                "cost_delta_vs_ordinary": _round(cost - ordinary_cost)
                if cost is not None and ordinary_cost is not None else None,
                "event_overlap_h": _round(overlap),
                "event_overlap_delta_vs_ordinary_h": _round(overlap - ordinary_overlap)
                if overlap is not None and ordinary_overlap is not None else None,
                "routine_shift_h": _round(abs(start_abs - preferred_abs), 3)
                if preferred_abs is not None else None,
                "missing_tariff_hours": missing_hours,
            })
        row["status"] = "enumerated" if options else "no_feasible_start"
        row["options"] = options
        cost_values = [item["scheduled_cost"] for item in options if item.get("scheduled_cost") is not None]
        overlap_values = [item["event_overlap_h"] for item in options if item.get("event_overlap_h") is not None]
        shift_values = [item["routine_shift_h"] for item in options if item.get("routine_shift_h") is not None]
        row["dimension_extrema"] = {
            "minimum_scheduled_cost": min(cost_values) if cost_values else None,
            "minimum_event_overlap_h": min(overlap_values) if overlap_values else None,
            "minimum_routine_shift_h": min(shift_values) if shift_values else None,
            "interpretation": "extrema are separate dimensions, not a selected or recommended start",
        }
        device_rows[name] = row
    return json.loads(json.dumps({
        "schema_version": FLEXIBLE_LOAD_OPPORTUNITY_VERSION,
        "event_window": event_card,
        "tariff_coverage_hours": len(tariff_map),
        "devices": device_rows,
        "selection_performed": False,
        "ranking_performed": False,
        "limitations": [
            "fixed-power arithmetic only; measured execution remains authoritative",
            "starts earlier than the observable decision clock are excluded",
            "routine_shift is exposed as a tradeoff and is not converted into a penalty",
            "the base model retains final portfolio and timing authority",
        ],
    }, ensure_ascii=False, allow_nan=False))


def compact_flexible_load_opportunities_for_prompt(
    snapshot: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return a lossless, columnar prompt view of flexible-start options.

    The full evidence artifact remains human-readable.  Repeating the same
    option keys dozens of times in an LLM prompt adds tokens but no planning
    information, so the prompt capsule carries one column declaration and all
    rows in their original order.  No option is ranked, pruned, or selected.
    """
    source = snapshot if isinstance(snapshot, Mapping) else {}
    columns = (
        "start_hod",
        "finish_hod",
        "scheduled_energy_kwh",
        "scheduled_cost",
        "cost_delta_vs_ordinary",
        "event_overlap_h",
        "event_overlap_delta_vs_ordinary_h",
        "routine_shift_h",
        "missing_tariff_hours",
    )
    devices: dict[str, Any] = {}
    for name, raw in (source.get("devices") or {}).items():
        if not isinstance(raw, Mapping):
            continue
        row = {
            str(key): deepcopy(value)
            for key, value in raw.items()
            if key != "options"
        }
        options = [item for item in list(raw.get("options") or []) if isinstance(item, Mapping)]
        row["option_columns"] = list(columns)
        row["option_rows"] = [
            [deepcopy(item.get(column)) for column in columns]
            for item in options
        ]
        row["option_count"] = len(options)
        row["option_semantics"] = (
            "Each option_rows item matches option_columns by position; rows remain unranked."
        )
        devices[str(name)] = row
    return json.loads(json.dumps({
        "schema_version": FLEXIBLE_LOAD_PROMPT_CAPSULE_VERSION,
        "source_schema_version": source.get("schema_version"),
        "event_window": deepcopy(source.get("event_window") or {}),
        "tariff_coverage_hours": source.get("tariff_coverage_hours"),
        "devices": devices,
        "selection_performed": False,
        "ranking_performed": False,
        "projection": "lossless_columnar_option_encoding",
        "limitations": deepcopy(list(source.get("limitations") or [])),
    }, ensure_ascii=False, allow_nan=False))


def compact_portfolio_impacts_for_review(
    report: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Project post-proposal impact cards to decision-relevant review facts.

    The full report remains in the lifecycle audit.  A second model pass needs
    the checked measurements and limitations, not thousands of repeated
    evidence-path strings already available in the initial payload.  This
    projection removes no candidate and performs no ranking or selection.
    """
    source = report if isinstance(report, Mapping) else {}

    def without_repeated_paths(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {
                str(key): without_repeated_paths(item)
                for key, item in value.items()
                if str(key) != "evidence_paths"
            }
        if isinstance(value, (list, tuple)):
            return [without_repeated_paths(item) for item in value]
        return deepcopy(value)

    raw_impacts = [
        raw
        for raw in list(source.get("candidate_impacts") or [])[:24]
        if isinstance(raw, Mapping)
    ]
    impacts: list[dict[str, Any]] = []
    preferred_device_fields = (
        "present",
        "service_required_today",
        "scheduled",
        "explicitly_skipped",
        "start_hod",
        "finish_hod",
        "within_service_window",
        "ready_by_declared_deadline",
        "energy_requirement_feasible",
        "task_completed",
        "scheduled_energy_kwh",
        "energy_upper_bound_kwh",
        "vpp_overlap_h",
        "vpp_overlap_energy_kwh",
        "vpp_overlap_energy_upper_bound_kwh",
        "scheduled_cost",
        "cost_upper_bound",
        "cost_unit",
        "estimate_class",
        "uncertainty",
    )
    preferred_hvac_fields = (
        "mode",
        "candidate_setpoint_c",
        "ordinary_setpoint_c",
        "setpoint_delta_c",
        "expected_demand_direction",
        "energy_kwh_estimate",
        "energy_delta_vs_ordinary_kwh",
        "vpp_energy_delta_vs_ordinary_kwh",
        "horizon_cost_delta_vs_ordinary",
        "cost_unit",
        "predicted_final_temp_c",
        "comfort_violation_c",
        "estimate_class",
        "rollout_uncertainty",
    )
    observed_device_fields = {
        str(key)
        for raw in raw_impacts
        for card in (raw.get("device_impacts") or {}).values()
        if isinstance(card, Mapping)
        for key in card
        if str(key) not in {"device", "evidence_paths"}
    }
    observed_hvac_fields = {
        str(key)
        for raw in raw_impacts
        if isinstance(raw.get("hvac_impact"), Mapping)
        for key in raw["hvac_impact"]
        if str(key) != "evidence_paths"
    }
    device_fields = tuple(
        key for key in preferred_device_fields if key in observed_device_fields
    ) + tuple(sorted(observed_device_fields - set(preferred_device_fields)))
    hvac_fields = tuple(
        key for key in preferred_hvac_fields if key in observed_hvac_fields
    ) + tuple(sorted(observed_hvac_fields - set(preferred_hvac_fields)))
    common_event_window: dict[str, Any] = {}
    common_limitations: list[Any] = []
    for raw in raw_impacts:
        if not common_event_window:
            common_event_window = deepcopy(raw.get("event_window") or {})
        for limitation in without_repeated_paths(list(raw.get("limitations") or [])):
            if limitation not in common_limitations:
                common_limitations.append(limitation)
        devices = [
            [str(name), *[deepcopy(card.get(key)) for key in device_fields]]
            for name, card in (raw.get("device_impacts") or {}).items()
            if isinstance(card, Mapping)
        ]
        hvac = raw.get("hvac_impact") if isinstance(raw.get("hvac_impact"), Mapping) else {}
        comparison = (
            raw.get("offer_specific_comparison")
            if isinstance(raw.get("offer_specific_comparison"), Mapping)
            else {}
        )
        impacts.append({
            "candidate_id": raw.get("candidate_id"),
            "plan_fingerprint": raw.get("plan_fingerprint"),
            "offer_specific_changed_paths": deepcopy(
                list(raw.get("offer_specific_changed_paths") or [])
            ),
            "device_impact_rows": devices,
            "hvac_impact_values": [deepcopy(hvac.get(key)) for key in hvac_fields],
            "aggregate": deepcopy(raw.get("aggregate") or {}),
            "findings": without_repeated_paths(list(raw.get("findings") or [])),
            "offer_specific_comparison": without_repeated_paths({
                key: comparison.get(key)
                for key in (
                    "changed_path_count",
                    "candidate_minus_ordinary",
                    "hvac_energy_delta_vs_ordinary_kwh",
                    "hvac_vpp_energy_delta_vs_ordinary_kwh",
                    "hvac_cost_delta_vs_ordinary",
                    "hvac_cost_unit",
                    "offer_materiality",
                    "supported_benefit_claims",
                    "benefit_claim_status",
                )
                if key in comparison
            }),
        })
    return json.loads(json.dumps({
        "schema_version": "energybridge.impact_review_capsule.v1",
        "source_schema_version": source.get("schema_version"),
        "candidate_impacts": impacts,
        "candidate_count": len(impacts),
        "event_window": common_event_window,
        "device_impact_columns": ["device", *device_fields],
        "hvac_impact_columns": list(hvac_fields),
        "row_semantics": (
            "Each device_impact_rows and hvac_impact_values item matches the "
            "corresponding columns by position; no candidate or evidence dimension is ranked."
        ),
        "limitations": common_limitations,
        "ranking_performed": False,
        "selected_candidate_id": None,
        "projection": "decision_relevant_post_proposal_evidence",
    }, ensure_ascii=False, allow_nan=False))


def _event_window(event: Mapping[str, Any] | None) -> tuple[float | None, float | None, dict[str, Any]]:
    event = event if isinstance(event, Mapping) else {}
    start = _finite(event.get("trigger_hod", event.get("start_hod")))
    end = _finite(event.get("end_hod"))
    absolute_start = _finite(event.get("trigger_h", event.get("start_h")))
    absolute_end = _finite(event.get("end_h"))
    if start is None and absolute_start is not None:
        start = absolute_start % 24.0
    if start is None:
        return None, None, {"available": False}
    if absolute_start is not None and absolute_end is not None:
        duration = max(0.0, min(24.0, absolute_end - absolute_start))
    elif end is not None:
        duration = _duration_interval(start, end)
    else:
        duration = _finite(event.get("duration_h"))
        duration = max(0.0, min(24.0, duration)) if duration is not None else None
    return start % 24.0, duration, {
        "available": duration is not None,
        "start_hod": _round(start % 24.0, 3),
        "end_hod": _round((start + duration) % 24.0, 3) if duration is not None else None,
        "duration_h": _round(duration, 3),
        "interval_semantics": "half_open_[start,end)",
    }


def _physical_plan(value: Mapping[str, Any] | None) -> dict[str, Any]:
    value = value if isinstance(value, Mapping) else {}
    if isinstance(value.get("validated_snapshot"), Mapping):
        value = value["validated_snapshot"]
    elif isinstance(value.get("plan"), Mapping):
        value = value["plan"]
    appliances = value.get("appliances", value.get("appliance_actions", {}))
    appliances = appliances if isinstance(appliances, Mapping) else {}
    out: dict[str, Any] = {"appliances": deepcopy(dict(appliances))}
    setpoint = value.get("setpoint", value.get("setpoint_c"))
    if setpoint is not None:
        out["setpoint"] = deepcopy(setpoint)
    return out


def _flatten(value: Any, prefix: str = "") -> dict[str, Any]:
    if isinstance(value, Mapping):
        out: dict[str, Any] = {}
        for key in sorted(value, key=str):
            path = f"{prefix}/{str(key).replace('~', '~0').replace('/', '~1')}"
            out.update(_flatten(value[key], path))
        return out
    return {prefix or "/": value}


def _changed_paths(plan: Mapping[str, Any], ordinary: Mapping[str, Any]) -> list[str]:
    first, second = _flatten(plan), _flatten(ordinary)
    return sorted(path for path in set(first) | set(second) if first.get(path) != second.get(path))


def _device_capabilities(observable_state: Mapping[str, Any] | None) -> Mapping[str, Any]:
    state = observable_state if isinstance(observable_state, Mapping) else {}
    devices = state.get("device_capabilities")
    return devices if isinstance(devices, Mapping) else {}


def _fixed_device_card(
    name: str,
    actions: Mapping[str, Any],
    capability: Mapping[str, Any],
    service_state: Mapping[str, Any] | None,
    *,
    event_start: float | None,
    event_duration: float | None,
    tariff: Mapping[int, float],
    tariff_unit: str,
) -> dict[str, Any]:
    start_key, skip_key = _ACTION_KEYS[name]
    skipped = actions.get(skip_key)
    start = _finite(actions.get(start_key))
    duration = _finite(capability.get("duration_h"))
    power = _finite(capability.get("power_kw", capability.get("rated_power_kw")))
    card: dict[str, Any] = {
        "present": bool(capability.get("present", False)),
        "service_required_today": bool(capability.get("service_required_today", True)),
        "scheduled": start is not None and skipped is not True,
        "explicitly_skipped": skipped is True,
        "start_hod": _round(start % 24.0, 3) if start is not None else None,
        "duration_h": _round(duration, 3),
        "power_kw": _round(power, 3),
        "estimate_class": "fixed_power_exact_when_capability_complete",
        "evidence_paths": [
            f"/observable_state/device_capabilities/{name}",
            f"/candidate_plan/appliances/{start_key}",
        ],
    }
    service_state = service_state if isinstance(service_state, Mapping) else {}
    completed_before_decision = bool(service_state.get("completed"))
    skipped_before_decision = bool(service_state.get("skipped"))
    running_before_decision = bool(service_state.get("running"))
    if completed_before_decision or skipped_before_decision or running_before_decision:
        observed_start = _finite(
            service_state.get("actual_start_h", service_state.get("start_h"))
        )
        card.update({
            "scheduled": False,
            "service_state_locked_before_proposal": True,
            "observed_service_status": (
                "completed" if completed_before_decision
                else "skipped" if skipped_before_decision
                else "running"
            ),
            "observed_start_hod": _round(observed_start % 24.0, 3)
            if observed_start is not None else None,
            "task_completed": completed_before_decision,
            "incremental_scheduled_energy_kwh": 0.0,
            "scheduled_energy_kwh": 0.0,
            "vpp_overlap_h": 0.0 if completed_before_decision else None,
            "vpp_overlap_energy_kwh": 0.0 if completed_before_decision else None,
            "scheduled_cost": 0.0,
            "action_effect": "no_incremental_schedule_effect_service_already_locked",
            "evidence_paths": [
                *card["evidence_paths"],
                f"/observable_state/realtime_device_state/current_day_service_state/{name}",
            ],
        })
        return card
    if skipped is True:
        card.update({"task_completed": False, "scheduled_energy_kwh": 0.0, "vpp_overlap_h": 0.0, "vpp_overlap_energy_kwh": 0.0})
        return card
    if start is None or duration is None or duration <= 0:
        card["task_completed"] = None
        card["uncertainty"] = ["start or duration is unavailable"]
        return card
    finish = start + duration
    earliest = _finite(capability.get("earliest_h"))
    latest = _finite(capability.get("latest_h"))
    within_window = None
    if earliest is not None and latest is not None:
        latest_abs = latest + (24.0 if latest < earliest else 0.0)
        start_abs = start + (24.0 if start < earliest and latest_abs > 24.0 else 0.0)
        within_window = start_abs >= earliest - 1e-9 and start_abs + duration <= latest_abs + 1e-9
    overlap = _overlap_hours(start, duration, event_start, event_duration)
    cost, missing = _interval_cost(start, duration, power, tariff)
    card.update({
        "finish_hod": _round(finish % 24.0, 3),
        "within_service_window": within_window,
        "task_completed": within_window if within_window is not None else True,
        "scheduled_energy_kwh": _round(power * duration) if power is not None else None,
        "vpp_overlap_h": _round(overlap),
        "vpp_overlap_energy_kwh": _round(power * overlap) if power is not None and overlap is not None else None,
        "scheduled_cost": _round(cost),
        "cost_unit": tariff_unit if cost is not None else None,
    })
    if missing:
        card["tariff_missing_hours"] = missing
    return card


def _water_heater_card(
    actions: Mapping[str, Any],
    capability: Mapping[str, Any],
    *,
    event_start: float | None,
    event_duration: float | None,
    tariff: Mapping[int, float],
    tariff_unit: str,
) -> dict[str, Any]:
    enabled = actions.get("water_heater_preheat")
    start = _finite(actions.get("water_heater_preheat_start_h"))
    end = _finite(actions.get("water_heater_preheat_end_h"))
    power = _finite(capability.get("rated_kw", capability.get("rated_power_kw", capability.get("power_kw"))))
    card: dict[str, Any] = {
        "present": bool(capability.get("present", False)),
        "enabled": enabled is True,
        "start_hod": _round(start % 24.0, 3) if start is not None else None,
        "end_hod": _round(end % 24.0, 3) if end is not None else None,
        "rated_power_kw": _round(power, 3),
        "estimate_class": "rated_power_upper_bound_duty_cycle_unknown",
        "evidence_paths": [
            "/observable_state/device_capabilities/water_heater",
            "/candidate_plan/appliances/water_heater_preheat",
        ],
    }
    if enabled is False:
        card.update({"scheduled": False, "energy_upper_bound_kwh": 0.0, "vpp_overlap_h": 0.0, "vpp_overlap_energy_upper_bound_kwh": 0.0})
        return card
    if enabled is not True or start is None or end is None:
        card.update({"scheduled": False, "uncertainty": ["explicit enable flag and complete preheat interval are required"]})
        return card
    duration = _duration_interval(start, end)
    overlap = _overlap_hours(start, duration, event_start, event_duration)
    cost, missing = _interval_cost(start, duration, power, tariff)
    deadline = _finite(capability.get("bath_required_h"))
    service_ready = None if deadline is None else ((end - deadline) % 24.0 >= 23.999999 or end <= deadline + 1e-9)
    # For the benchmark's same-day windows, a direct comparison is clearer;
    # wrapped intervals retain an explicitly unknown readiness judgment.
    if deadline is not None and end >= start:
        service_ready = end <= deadline + 1e-9
    card.update({
        "scheduled": duration > 0,
        "duration_h": _round(duration, 3),
        "ready_by_declared_deadline": service_ready,
        "energy_upper_bound_kwh": _round(power * duration) if power is not None else None,
        "vpp_overlap_h": _round(overlap),
        "vpp_overlap_energy_upper_bound_kwh": _round(power * overlap) if power is not None and overlap is not None else None,
        "cost_upper_bound": _round(cost),
        "cost_unit": tariff_unit if cost is not None else None,
        "uncertainty": ["tank thermostat duty cycle and thermal losses are not modeled by this accounting bound"],
    })
    if missing:
        card["tariff_missing_hours"] = missing
    return card


def _ev_card(
    actions: Mapping[str, Any],
    capability: Mapping[str, Any],
    realtime: Mapping[str, Any],
    *,
    event_start: float | None,
    event_duration: float | None,
    tariff: Mapping[int, float],
    tariff_unit: str,
) -> dict[str, Any]:
    mode = str(actions.get("ev_mode") or "").strip().lower()
    start = _finite(actions.get("ev_charge_start_h"))
    end = _finite(actions.get("ev_charge_end_h"))
    charger = _finite(capability.get("charger_kw", capability.get("power_kw")))
    efficiency = _finite(capability.get("efficiency"))
    efficiency = efficiency if efficiency is not None and 0 < efficiency <= 1 else None
    card: dict[str, Any] = {
        "present": bool(capability.get("present", False)),
        "mode": mode or None,
        "start_hod": _round(start % 24.0, 3) if start is not None else None,
        "end_hod": _round(end % 24.0, 3) if end is not None else None,
        "charger_kw": _round(charger, 3),
        "efficiency": _round(efficiency, 4),
        "estimate_class": "charger_power_upper_bound_with_observable_energy_requirement",
        "evidence_paths": [
            "/observable_state/device_capabilities/ev",
            "/observable_state/realtime_device_state",
            "/candidate_plan/appliances/ev_mode",
        ],
    }
    if start is None or end is None:
        card["scheduled"] = False
        card["uncertainty"] = ["complete charge interval is unavailable"]
        return card
    duration = _duration_interval(start, end)
    overlap = _overlap_hours(start, duration, event_start, event_duration)
    cost, missing = _interval_cost(start, duration, charger, tariff)
    deliverable = charger * duration * efficiency if charger is not None and efficiency is not None else None
    daily_drive = _finite(capability.get("daily_drive_kwh"))
    current_soc = _finite(realtime.get("ev_soc", realtime.get("soc")))
    target_soc = _finite(capability.get("target_soc"))
    capacity = _finite(capability.get("capacity_kwh"))
    required = daily_drive
    required_basis = "daily_drive_kwh" if daily_drive is not None else None
    if current_soc is not None and target_soc is not None and capacity is not None:
        soc_required = max(0.0, target_soc - current_soc) * capacity
        if required is None or soc_required > required:
            required, required_basis = soc_required, "target_soc_minus_observed_soc"
    card.update({
        "scheduled": duration > 0,
        "duration_h": _round(duration, 3),
        "deliverable_energy_upper_bound_kwh": _round(deliverable),
        "required_battery_energy_kwh": _round(required),
        "required_energy_basis": required_basis,
        "energy_requirement_feasible": (deliverable + 1e-9 >= required) if deliverable is not None and required is not None else None,
        "vpp_overlap_h": _round(overlap),
        "vpp_overlap_grid_energy_upper_bound_kwh": _round(charger * overlap) if charger is not None and overlap is not None else None,
        "grid_cost_upper_bound": _round(cost),
        "cost_unit": tariff_unit if cost is not None else None,
        "uncertainty": ["actual charger modulation and battery state trajectory are not modeled by this upper bound"],
    })
    if missing:
        card["tariff_missing_hours"] = missing
    return card


def _hvac_card(
    plan: Mapping[str, Any],
    ordinary: Mapping[str, Any],
    devices: Mapping[str, Any],
    rollout: Mapping[str, Any] | None = None,
    tariff: Mapping[int, float] | None = None,
    tariff_unit: str = "cost/kWh",
) -> dict[str, Any]:
    proposed = _finite(plan.get("setpoint"))
    baseline = _finite(ordinary.get("setpoint"))
    ac = devices.get("ac") if isinstance(devices.get("ac"), Mapping) else {}
    mode = str(ac.get("mode") or "unknown").strip().lower()
    delta = proposed - baseline if proposed is not None and baseline is not None else None
    direction = "unknown_without_reference_setpoint"
    if delta is not None and abs(delta) <= 1e-9:
        direction = "unchanged"
    elif delta is not None and mode == "cooling":
        direction = "lower_cooling_demand" if delta > 0 else "higher_cooling_demand"
    elif delta is not None and mode == "heating":
        direction = "higher_heating_demand" if delta > 0 else "lower_heating_demand"
    card = {
        "mode": mode,
        "candidate_setpoint_c": _round(proposed, 3),
        "ordinary_setpoint_c": _round(baseline, 3),
        "setpoint_delta_c": _round(delta, 3),
        "expected_demand_direction": direction,
        "energy_kwh_estimate": None,
        "estimate_class": "directional_only_requires_thermal_model_for_energy",
        "evidence_paths": [
            "/candidate_plan/setpoint",
            "/ordinary_plan/setpoint",
            "/observable_state/device_capabilities/ac/mode",
        ],
    }
    rollout = rollout if isinstance(rollout, Mapping) else {}
    rows = [item for item in list(rollout.get("candidate_setpoints") or []) if isinstance(item, Mapping)]

    def row_for(setpoint: float | None) -> Mapping[str, Any] | None:
        if setpoint is None:
            return None
        return next(
            (
                item for item in rows
                if _finite(item.get("setpoint_c")) is not None
                and abs(float(item.get("setpoint_c")) - setpoint) <= 1e-6
            ),
            None,
        )

    candidate_row, ordinary_row = row_for(proposed), row_for(baseline)
    if candidate_row is not None:
        candidate_energy = _finite(candidate_row.get("hvac_energy_kwh"))
        ordinary_energy = _finite(ordinary_row.get("hvac_energy_kwh")) if ordinary_row else None
        candidate_vpp = _finite(candidate_row.get("vpp_hvac_energy_kwh"))
        ordinary_vpp = _finite(ordinary_row.get("vpp_hvac_energy_kwh")) if ordinary_row else None
        horizon_h = _finite(rollout.get("horizon_h"))
        rollout_start = _finite(rollout.get("start_hod"))
        unit_cost = None
        if horizon_h is not None and horizon_h > 0 and rollout_start is not None:
            integrated, missing = _interval_cost(
                rollout_start,
                horizon_h,
                1.0,
                tariff or {},
            )
            if integrated is not None and not missing:
                unit_cost = integrated / horizon_h
        candidate_cost = candidate_energy * unit_cost if candidate_energy is not None and unit_cost is not None else None
        ordinary_cost = ordinary_energy * unit_cost if ordinary_energy is not None and unit_cost is not None else None
        card.update({
            "energy_kwh_estimate": _round(candidate_energy),
            "ordinary_energy_kwh_estimate": _round(ordinary_energy),
            "energy_delta_vs_ordinary_kwh": _round(candidate_energy - ordinary_energy)
            if candidate_energy is not None and ordinary_energy is not None else None,
            "vpp_energy_kwh_estimate": _round(candidate_vpp),
            "ordinary_vpp_energy_kwh_estimate": _round(ordinary_vpp),
            "vpp_energy_delta_vs_ordinary_kwh": _round(candidate_vpp - ordinary_vpp)
            if candidate_vpp is not None and ordinary_vpp is not None else None,
            "horizon_cost_estimate": _round(candidate_cost),
            "ordinary_horizon_cost_estimate": _round(ordinary_cost),
            "horizon_cost_delta_vs_ordinary": _round(candidate_cost - ordinary_cost)
            if candidate_cost is not None and ordinary_cost is not None else None,
            "cost_unit": tariff_unit if candidate_cost is not None else None,
            "cost_estimate_class": (
                "thermal_rollout_energy_times_horizon_average_tariff"
                if candidate_cost is not None else None
            ),
            "predicted_final_temp_c": _round(_finite(candidate_row.get("predicted_final_temp_c")), 3),
            "comfort_violation_c": _round(_finite(candidate_row.get("comfort_violation_c"))),
            "estimate_class": "observable_regional_thermal_rollout",
            "rollout_horizon_h": _round(_finite(rollout.get("horizon_h")), 3),
            "rollout_uncertainty": str(
                rollout.get("uncertainty")
                or "model estimate; compare with measured execution before treating as calibrated"
            )[:300],
        })
    return card


def _aggregate_cards(cards: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    exact_energy = 0.0
    exact_energy_seen = False
    bound_energy = 0.0
    bound_energy_seen = False
    exact_overlap = 0.0
    exact_overlap_seen = False
    bound_overlap = 0.0
    bound_overlap_seen = False
    exact_cost = 0.0
    exact_cost_seen = False
    bound_cost = 0.0
    bound_cost_seen = False
    for card in cards.values():
        for key, total_name in (
            ("scheduled_energy_kwh", "exact_energy"),
            ("energy_upper_bound_kwh", "bound_energy"),
            ("deliverable_energy_upper_bound_kwh", "bound_energy"),
            ("vpp_overlap_energy_kwh", "exact_overlap"),
            ("vpp_overlap_energy_upper_bound_kwh", "bound_overlap"),
            ("vpp_overlap_grid_energy_upper_bound_kwh", "bound_overlap"),
            ("scheduled_cost", "exact_cost"),
            ("cost_upper_bound", "bound_cost"),
            ("grid_cost_upper_bound", "bound_cost"),
        ):
            value = _finite(card.get(key))
            if value is None:
                continue
            if total_name == "exact_energy":
                exact_energy, exact_energy_seen = exact_energy + value, True
            elif total_name == "bound_energy":
                bound_energy, bound_energy_seen = bound_energy + value, True
            elif total_name == "exact_overlap":
                exact_overlap, exact_overlap_seen = exact_overlap + value, True
            elif total_name == "bound_overlap":
                bound_overlap, bound_overlap_seen = bound_overlap + value, True
            elif total_name == "exact_cost":
                exact_cost, exact_cost_seen = exact_cost + value, True
            else:
                bound_cost, bound_cost_seen = bound_cost + value, True
    return {
        "fixed_load_scheduled_energy_kwh": _round(exact_energy) if exact_energy_seen else None,
        "state_dependent_energy_upper_bound_kwh": _round(bound_energy) if bound_energy_seen else None,
        "fixed_load_vpp_overlap_energy_kwh": _round(exact_overlap) if exact_overlap_seen else None,
        "state_dependent_vpp_overlap_energy_upper_bound_kwh": _round(bound_overlap) if bound_overlap_seen else None,
        "fixed_load_scheduled_cost": _round(exact_cost) if exact_cost_seen else None,
        "state_dependent_cost_upper_bound": _round(bound_cost) if bound_cost_seen else None,
        "whole_home_energy_claimed": False,
        "scalar_utility_score": None,
    }


def evaluate_candidate_impact(
    candidate: Mapping[str, Any] | None,
    *,
    observable_state: Mapping[str, Any] | None,
    event: Mapping[str, Any] | None,
    ordinary_plan: Mapping[str, Any] | None = None,
    tariff: Mapping[str, Any] | None = None,
    candidate_id: str | None = None,
) -> dict[str, Any]:
    """Return an evidence card for one candidate without ranking it."""
    plan = _physical_plan(candidate)
    ordinary = _physical_plan(ordinary_plan)
    devices = _device_capabilities(observable_state)
    state = observable_state if isinstance(observable_state, Mapping) else {}
    realtime = state.get("realtime_device_state")
    realtime = realtime if isinstance(realtime, Mapping) else {}
    service_states = realtime.get("current_day_service_state")
    service_states = service_states if isinstance(service_states, Mapping) else {}
    actions = plan.get("appliances") if isinstance(plan.get("appliances"), Mapping) else {}
    event_start, event_duration, event_card = _event_window(event)
    tariff_map, tariff_unit = _tariff_map(tariff)
    cards: dict[str, dict[str, Any]] = {}
    for name in _SHIFTABLE_DEVICES:
        capability = devices.get(name) if isinstance(devices.get(name), Mapping) else {}
        if capability.get("present") or any(key in actions for key in _ACTION_KEYS[name]):
            cards[name] = _fixed_device_card(
                name,
                actions,
                capability,
                service_states.get(name)
                if isinstance(service_states.get(name), Mapping)
                else {},
                event_start=event_start,
                event_duration=event_duration,
                tariff=tariff_map,
                tariff_unit=tariff_unit,
            )
    wh = devices.get("water_heater") if isinstance(devices.get("water_heater"), Mapping) else {}
    if wh.get("present") or any(str(key).startswith("water_heater_") for key in actions):
        cards["water_heater"] = _water_heater_card(
            actions,
            wh,
            event_start=event_start,
            event_duration=event_duration,
            tariff=tariff_map,
            tariff_unit=tariff_unit,
        )
    ev = devices.get("ev") if isinstance(devices.get("ev"), Mapping) else {}
    if ev.get("present") or any(str(key).startswith("ev_") for key in actions):
        cards["ev"] = _ev_card(
            actions,
            ev,
            realtime,
            event_start=event_start,
            event_duration=event_duration,
            tariff=tariff_map,
            tariff_unit=tariff_unit,
        )
    changed = _changed_paths(plan, ordinary) if ordinary_plan is not None else []
    findings: list[dict[str, Any]] = []
    for name, card in cards.items():
        overlap = _finite(card.get("vpp_overlap_h"))
        if overlap is not None and overlap > 1e-9:
            findings.append({
                "code": "scheduled_load_overlaps_event",
                "device": name,
                "severity": "material_tradeoff",
                "overlap_h": _round(overlap),
                "evidence_paths": card.get("evidence_paths", []),
            })
        if card.get("within_service_window") is False or card.get("ready_by_declared_deadline") is False or card.get("energy_requirement_feasible") is False:
            findings.append({
                "code": "declared_service_requirement_not_met",
                "device": name,
                "severity": "hard_service_risk",
                "evidence_paths": card.get("evidence_paths", []),
            })
        if (
            name in _SHIFTABLE_DEVICES
            and card.get("service_required_today") is True
            and card.get("task_completed") is False
        ):
            findings.append({
                "code": "required_daily_service_cancelled",
                "device": name,
                "severity": "hard_service_risk",
                "evidence_paths": card.get("evidence_paths", []),
            })
    result = {
        "schema_version": ENERGY_IMPACT_SCHEMA_VERSION,
        "candidate_id": _safe_id(candidate_id or (candidate or {}).get("candidate_id"), "candidate"),
        "plan_fingerprint": _fingerprint(plan),
        "selection_authority": "base_model",
        "ranking_performed": False,
        "event_window": event_card,
        "offer_specific_changed_paths": changed,
        "device_impacts": cards,
        "hvac_impact": _hvac_card(
            plan,
            ordinary,
            devices,
            state.get("professional_hvac_rollout")
            if isinstance(state.get("professional_hvac_rollout"), Mapping)
            else {},
            tariff_map,
            tariff_unit,
        ),
        "aggregate": _aggregate_cards(cards),
        "findings": findings,
        "tariff_coverage_hours": len(tariff_map),
        "limitations": [
            "fixed-power appliance arithmetic is not a whole-home simulation",
            "water-heater and EV values are bounds when duty cycle or state trajectory is unavailable",
            "HVAC energy is directional until a thermal model supplies a trace",
        ],
    }
    if ordinary_plan is not None:
        ordinary_card = evaluate_candidate_impact(
            ordinary_plan,
            observable_state=observable_state,
            event=event,
            ordinary_plan=None,
            tariff=tariff,
            candidate_id="ordinary_plan_reference",
        )
        candidate_aggregate = result["aggregate"]
        ordinary_aggregate = ordinary_card["aggregate"]
        deltas: dict[str, float | None] = {}
        for key in (
            "fixed_load_scheduled_energy_kwh",
            "state_dependent_energy_upper_bound_kwh",
            "fixed_load_vpp_overlap_energy_kwh",
            "state_dependent_vpp_overlap_energy_upper_bound_kwh",
            "fixed_load_scheduled_cost",
            "state_dependent_cost_upper_bound",
        ):
            candidate_value = _finite(candidate_aggregate.get(key))
            ordinary_value = _finite(ordinary_aggregate.get(key))
            deltas[f"{key}_delta_vs_ordinary"] = (
                _round(candidate_value - ordinary_value)
                if candidate_value is not None and ordinary_value is not None
                else None
            )
        result["offer_specific_comparison"] = {
            "changed_path_count": len(changed),
            "candidate_minus_ordinary": deltas,
            "hvac_energy_delta_vs_ordinary_kwh": result["hvac_impact"].get(
                "energy_delta_vs_ordinary_kwh"
            ),
            "hvac_vpp_energy_delta_vs_ordinary_kwh": result["hvac_impact"].get(
                "vpp_energy_delta_vs_ordinary_kwh"
            ),
            "hvac_cost_delta_vs_ordinary": result["hvac_impact"].get(
                "horizon_cost_delta_vs_ordinary"
            ),
            "hvac_cost_unit": result["hvac_impact"].get("cost_unit"),
            "ordinary_plan_fingerprint": ordinary_card["plan_fingerprint"],
            "interpretation": (
                "negative deltas indicate less modeled energy, event overlap, or cost than the ordinary plan"
            ),
        }
        comparison = result["offer_specific_comparison"]
        supported_claims: list[dict[str, Any]] = []
        fixed_cost_delta = deltas.get("fixed_load_scheduled_cost_delta_vs_ordinary")
        if fixed_cost_delta is not None and fixed_cost_delta < -1e-9:
            supported_claims.append({
                "kind": "normalized_fixed_load_cost_reduction",
                "amount": _round(-fixed_cost_delta),
                "unit": tariff_unit,
                "estimate_class": "exact_fixed_power_tariff_integration",
                "evidence_paths": [
                    "/offer_specific_comparison/candidate_minus_ordinary/fixed_load_scheduled_cost_delta_vs_ordinary"
                ],
            })
        fixed_overlap_delta = deltas.get(
            "fixed_load_vpp_overlap_energy_kwh_delta_vs_ordinary"
        )
        if fixed_overlap_delta is not None and fixed_overlap_delta < -1e-9:
            supported_claims.append({
                "kind": "fixed_load_event_overlap_energy_reduction",
                "amount_kwh": _round(-fixed_overlap_delta),
                "estimate_class": "exact_fixed_power_interval_overlap",
                "evidence_paths": [
                    "/offer_specific_comparison/candidate_minus_ordinary/fixed_load_vpp_overlap_energy_kwh_delta_vs_ordinary"
                ],
            })
        hvac_cost_delta = comparison.get("hvac_cost_delta_vs_ordinary")
        if hvac_cost_delta is not None and hvac_cost_delta < -1e-9:
            supported_claims.append({
                "kind": "modeled_hvac_cost_reduction",
                "amount": _round(-hvac_cost_delta),
                "unit": comparison.get("hvac_cost_unit"),
                "estimate_class": "regional_thermal_rollout_estimate",
                "evidence_paths": ["/offer_specific_comparison/hvac_cost_delta_vs_ordinary"],
            })
        hvac_overlap_delta = comparison.get("hvac_vpp_energy_delta_vs_ordinary_kwh")
        if hvac_overlap_delta is not None and hvac_overlap_delta < -1e-9:
            supported_claims.append({
                "kind": "modeled_hvac_event_energy_reduction",
                "amount_kwh": _round(-hvac_overlap_delta),
                "estimate_class": "regional_thermal_rollout_estimate",
                "evidence_paths": [
                    "/offer_specific_comparison/hvac_vpp_energy_delta_vs_ordinary_kwh"
                ],
            })
        comparison["offer_materiality"] = (
            "no_observable_physical_change"
            if not changed
            else "observable_physical_change"
        )
        comparison["supported_benefit_claims"] = supported_claims
        comparison["benefit_claim_status"] = (
            "no_offer_specific_claim_supported"
            if not supported_claims
            else "offer_specific_claims_available"
        )
        if not changed:
            result["findings"].append({
                "code": "no_observable_offer_change",
                "severity": "offer_readiness",
                "evidence_paths": ["/offer_specific_changed_paths"],
                "interpretation": (
                    "the physical candidate is the ordinary plan and cannot support an incremental offer claim"
                ),
            })
    return json.loads(json.dumps(result, ensure_ascii=False, allow_nan=False))


def evaluate_portfolio_impacts(
    candidates: Sequence[Mapping[str, Any]] | None,
    *,
    observable_state: Mapping[str, Any] | None,
    event: Mapping[str, Any] | None,
    ordinary_plan: Mapping[str, Any] | None = None,
    tariff: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate every candidate independently and preserve model ownership."""
    cards = [
        evaluate_candidate_impact(
            candidate,
            observable_state=observable_state,
            event=event,
            ordinary_plan=ordinary_plan,
            tariff=tariff,
            candidate_id=str(candidate.get("candidate_id") or f"candidate_{index:02d}"),
        )
        for index, candidate in enumerate(list(candidates or [])[:24], start=1)
        if isinstance(candidate, Mapping)
    ]
    return {
        "schema_version": ENERGY_IMPACT_SCHEMA_VERSION,
        "candidate_impacts": cards,
        "candidate_count": len(cards),
        "ranking_performed": False,
        "selected_candidate_id": None,
        "selection_authority": "base_model",
    }


__all__ = [
    "DECISION_EPOCH_SNAPSHOT_VERSION",
    "ENERGY_IMPACT_SCHEMA_VERSION",
    "FLEXIBLE_LOAD_OPPORTUNITY_VERSION",
    "FLEXIBLE_LOAD_PROMPT_CAPSULE_VERSION",
    "TARIFF_SNAPSHOT_VERSION",
    "build_flexible_load_opportunity_snapshot",
    "build_decision_epoch_snapshot",
    "compact_flexible_load_opportunities_for_prompt",
    "compact_portfolio_impacts_for_review",
    "build_hourly_tariff_snapshot",
    "evaluate_candidate_impact",
    "evaluate_portfolio_impacts",
]
