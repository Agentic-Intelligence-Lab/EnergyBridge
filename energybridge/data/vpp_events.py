"""VPP event schedule helpers for benchmark runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence


def make_daily_vpp_events(
    sim_days: int,
    *,
    start_h: float = 18.0,
    duration_h: float = 1.0,
) -> list[dict[str, Any]]:
    """Create one same-time VPP event per simulated day."""
    start_h = float(start_h) % 24.0
    duration_h = float(duration_h)
    if duration_h <= 0.0:
        raise ValueError("duration_h must be > 0")
    if start_h + duration_h > 24.0:
        raise ValueError("VPP windows crossing midnight are not supported yet")
    return [
        {
            "id": f"vpp{day_idx + 1}",
            "trigger_h": day_idx * 24.0 + start_h,
            "end_h": day_idx * 24.0 + start_h + duration_h,
            "day": day_idx + 1,
            "source": "daily_default",
        }
        for day_idx in range(max(1, int(sim_days)))
    ]


def load_vpp_events_config(
    path: str | Path,
    *,
    sim_days: int,
    default_start_h: float = 18.0,
    default_duration_h: float = 1.0,
) -> list[dict[str, Any]]:
    """Load a JSON VPP event schedule and return normalized absolute events.

    Supported JSON shapes:
      [{"day": 1, "start_h": 18, "duration_h": 1}, ...]
      {"events": [...], "default_start_h": 18, "default_duration_h": 1}

    Event fields:
      day: 1-based simulation day
      start_h/hour/trigger_h: hour-of-day when day is present
      end_h/end_hour OR duration_h/duration_hours/duration_minutes
      id: optional stable event id

    Absolute trigger_h/end_h values are also accepted when day is omitted.
    """
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    return parse_vpp_events_config(
        data,
        sim_days=sim_days,
        default_start_h=default_start_h,
        default_duration_h=default_duration_h,
        source=str(path),
    )


def parse_vpp_events_config(
    data: Any,
    *,
    sim_days: int,
    default_start_h: float = 18.0,
    default_duration_h: float = 1.0,
    source: str = "inline",
) -> list[dict[str, Any]]:
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        events_raw = list(data)
        defaults: Mapping[str, Any] = {}
    elif isinstance(data, Mapping):
        defaults = data
        events_raw = list(data.get("events") or data.get("vpp_events") or [])
        if not events_raw:
            return make_daily_vpp_events(
                sim_days,
                start_h=_first(defaults, ("start_h", "start_hour", "hour", "default_start_h"), default_start_h),
                duration_h=_duration_from(defaults, default_duration_h),
            )
    else:
        raise ValueError("VPP event config must be a JSON object or list")

    normalized: list[dict[str, Any]] = []
    for index, event in enumerate(events_raw, start=1):
        if not isinstance(event, Mapping):
            raise ValueError(f"VPP event #{index} must be an object")
        normalized.append(
            _normalize_event(
                event,
                index=index,
                sim_days=sim_days,
                default_start_h=_first(defaults, ("default_start_h", "start_h", "start_hour", "hour"), default_start_h),
                default_duration_h=_duration_from(defaults, default_duration_h),
                source=source,
            )
        )
    normalized.sort(key=lambda item: (float(item["trigger_h"]), str(item.get("id", ""))))
    used_ids: set[str] = set()
    for index, event in enumerate(normalized, start=1):
        event_id = str(event.get("id") or f"vpp{index}")
        if event_id in used_ids:
            raise ValueError(f"Duplicate VPP event id: {event_id}")
        used_ids.add(event_id)
        event["id"] = event_id
    _validate_no_overlap(normalized)
    return normalized


def describe_vpp_events(events: Sequence[Mapping[str, Any]]) -> str:
    if not events:
        return "no VPP events"
    starts = {round(float(event["trigger_h"]) % 24.0, 6) for event in events}
    durations = {round(float(event["end_h"]) - float(event["trigger_h"]), 6) for event in events}
    days = {int(event.get("day", int(float(event["trigger_h"]) // 24) + 1)) for event in events}
    if len(events) == len(days) and len(starts) == 1 and len(durations) == 1:
        start = next(iter(starts))
        duration = next(iter(durations))
        return f"daily {start:.2f}h for {duration:.2f}h"
    return f"{len(events)} configured event(s) across {len(days)} day(s)"


def _normalize_event(
    event: Mapping[str, Any],
    *,
    index: int,
    sim_days: int,
    default_start_h: float,
    default_duration_h: float,
    source: str,
) -> dict[str, Any]:
    has_day = event.get("day") is not None
    if has_day:
        day = int(event["day"])
        if day < 1 or day > int(sim_days):
            raise ValueError(f"VPP event #{index} day must be within 1..{sim_days}: {day}")
        start_hod = float(_first(event, ("start_h", "start_hour", "hour", "trigger_hour", "trigger_h"), default_start_h)) % 24.0
        trigger_h = (day - 1) * 24.0 + start_hod
    else:
        trigger_h = float(_first(event, ("trigger_h", "start_h", "start_hour", "hour"), default_start_h))
        if trigger_h < 0.0:
            raise ValueError(f"VPP event #{index} trigger_h must be >= 0")
        day = int(trigger_h // 24.0) + 1
        if day < 1 or day > int(sim_days):
            raise ValueError(f"VPP event #{index} trigger_h is outside the {sim_days}-day simulation")
        start_hod = trigger_h % 24.0

    if event.get("end_h") is not None or event.get("end_hour") is not None:
        raw_end = float(_first(event, ("end_h", "end_hour"), start_hod + default_duration_h))
        if has_day and raw_end <= 24.0:
            duration_h = raw_end - start_hod
        else:
            duration_h = raw_end - trigger_h
    else:
        duration_h = _duration_from(event, default_duration_h)

    if duration_h <= 0.0:
        raise ValueError(f"VPP event #{index} duration must be > 0")
    if start_hod + duration_h > 24.0:
        raise ValueError(f"VPP event #{index} crosses midnight; this is not supported yet")
    end_h = trigger_h + duration_h
    if end_h > int(sim_days) * 24.0 + 1e-9:
        raise ValueError(f"VPP event #{index} ends outside the {sim_days}-day simulation")

    out = dict(event)
    out.update({
        "id": str(event.get("id") or ""),
        "trigger_h": round(trigger_h, 6),
        "end_h": round(end_h, 6),
        "day": day,
        "source": str(event.get("source") or source),
    })
    return out


def _duration_from(data: Mapping[str, Any], default_duration_h: float) -> float:
    if data.get("duration_h") is not None:
        return float(data["duration_h"])
    if data.get("duration_hours") is not None:
        return float(data["duration_hours"])
    if data.get("duration_minutes") is not None:
        return float(data["duration_minutes"]) / 60.0
    return float(default_duration_h)


def _first(data: Mapping[str, Any], keys: Sequence[str], default: Any) -> Any:
    for key in keys:
        if data.get(key) is not None:
            return data[key]
    return default


def _validate_no_overlap(events: Sequence[Mapping[str, Any]]) -> None:
    previous: Mapping[str, Any] | None = None
    for event in events:
        if previous is not None and float(event["trigger_h"]) < float(previous["end_h"]) - 1e-9:
            raise ValueError(
                f"VPP events overlap: {previous.get('id', '?')} and {event.get('id', '?')}"
            )
        previous = event
