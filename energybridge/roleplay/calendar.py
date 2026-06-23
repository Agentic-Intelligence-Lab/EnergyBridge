"""Synthetic calendar support for role-play persona evaluation.

EnergyBridge uses local synthetic calendar events as context for deadline
inference, such as EV charging before a commute.  This keeps role-play
evaluation reproducible while still reflecting user routines.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


CALENDAR_DIRNAME = "calendars"
AWAY_LOCATIONS = {"outside", "office", "work", "transit", "commute", "school"}
OCCUPANCY_THRESHOLD = 0.05


def calendar_path_for_persona(persona_id: str, personas_dir: Path) -> Path:
    return personas_dir / CALENDAR_DIRNAME / persona_id / "calendar_7day.json"


def calendar_candidate_paths(persona_id: str, personas_dir: Path) -> list[Path]:
    base = personas_dir / CALENDAR_DIRNAME / persona_id
    return [
        base / "calendar_7day.json",
        base / "calendar_3day.json",
    ]


def load_calendar_for_persona(persona_id: str, personas_dir: Path) -> dict[str, Any] | None:
    path = next((candidate for candidate in calendar_candidate_paths(persona_id, personas_dir)
                 if candidate.exists()), None)
    if path is None:
        return None
    with path.open(encoding="utf-8") as fh:
        data = json.load(fh)
    if data.get("persona_id") not in (None, persona_id):
        raise ValueError(f"Calendar persona_id mismatch in {path}")
    return data


def attach_calendar(persona: dict[str, Any], personas_dir: Path) -> dict[str, Any]:
    """Return a shallow persona copy with matching calendar data if available."""
    out = dict(persona)
    calendar = load_calendar_for_persona(str(persona.get("id", "")), personas_dir)
    if calendar is not None:
        out["calendar"] = calendar
    return out


def calendar_context_for_event(
    persona: dict[str, Any],
    event_index: int,
    vpp_context: dict | None = None,
) -> dict[str, Any]:
    """Build compact calendar context for one VPP event.

    This is intentionally prompt-friendly: it exposes daily events, conflicts
    with the 18:00-19:00 VPP window, and inferred appliance deadlines.
    """
    calendar = persona.get("calendar") or {}
    days = calendar.get("days") or []
    vpp = vpp_context or {}
    calendar_day_index = int(vpp.get("day", event_index) or event_index)
    day = next((item for item in days if int(item.get("day", -1)) == calendar_day_index), None)
    if day is None and days:
        # For runs longer than the calendar horizon, cycle weekly while keeping
        # Day1 aligned to Sunday.
        weekly_day = ((calendar_day_index - 1) % len(days)) + 1
        day = next((item for item in days if int(item.get("day", -1)) == weekly_day), None)
    if day is None:
        return {
            "available": False,
            "event_index": event_index,
            "note": "No paired calendar found for this persona/event.",
        }

    vpp_start = float(vpp.get("trigger_h", vpp.get("hour", 18.0)) % 24.0)
    duration_h = float(vpp.get("duration_h", 1.0) or 1.0)
    vpp_end = vpp_start + duration_h

    conflicts = []
    for event in day.get("events", []):
        start_h = float(event.get("start_h", 0.0))
        end_h = float(event.get("end_h", start_h))
        if _overlaps(start_h, end_h, vpp_start, vpp_end):
            conflicts.append({
                "title": event.get("title", ""),
                "start_h": start_h,
                "end_h": end_h,
                "location": event.get("location", ""),
                "energy_relevance": event.get("energy_relevance", ""),
            })

    constraints = dict(day.get("constraints") or {})
    appliance_deadlines = dict(constraints.get("appliance_deadlines") or {})
    if constraints.get("next_departure_h") is not None:
        appliance_deadlines.setdefault(
            "ev",
            f"finish charging before next departure at {_fmt_h(constraints['next_departure_h'])}",
        )
    if constraints.get("bath_shower_h") is not None:
        appliance_deadlines.setdefault(
            "water_heater",
            f"hot water ready before {_fmt_h(constraints['bath_shower_h'])}",
        )

    return {
        "available": True,
        "source": calendar.get("source", "persona_calendar"),
        "event_index": event_index,
        "day": calendar_day_index,
        "weekday": day.get("weekday", ""),
        "day_type": day.get("day_type", "weekday"),
        "summary": day.get("summary", ""),
        "events": day.get("events", []),
        "vpp_window_h": [vpp_start, vpp_end],
        "vpp_conflicts": conflicts,
        "constraints": constraints,
        "appliance_deadlines": appliance_deadlines,
        "roleplay_guidance": (
            "Use calendar context as user-side constraints: prefer strategies "
            "that preserve required appointments, task deadlines, return-home "
            "comfort, bath hot-water needs, and EV departure readiness."
        ),
    }


def calendar_brief_for_prompt(context: dict[str, Any]) -> str:
    if not context.get("available"):
        return "Calendar: unavailable."
    events = context.get("events") or []
    event_text = "; ".join(
        f"{_fmt_h(e.get('start_h'))}-{_fmt_h(e.get('end_h'))} {e.get('title', '')}"
        for e in events[:6]
    )
    conflicts = context.get("vpp_conflicts") or []
    conflict_text = "; ".join(
        f"{_fmt_h(e.get('start_h'))}-{_fmt_h(e.get('end_h'))} {e.get('title', '')}"
        for e in conflicts
    ) or "none"
    deadlines = context.get("appliance_deadlines") or {}
    return (
        f"Calendar day summary: {context.get('summary', '')}. "
        f"Events: {event_text or 'none'}. "
        f"VPP-window conflicts: {conflict_text}. "
        f"Appliance deadlines: {json.dumps(deadlines, ensure_ascii=False)}."
    )


def hourly_occupancy_from_persona(persona: dict[str, Any], days: int) -> list[list[float]] | None:
    """Convert a persona role-play calendar into hourly home occupancy fractions.

    The synthetic calendars describe user-side routines.  We treat home as the
    default for a household, then subtract explicit away intervals such as
    office, transit, outside, or school.  This avoids confusing a home appliance
    task with human presence while still using the role-play schedule as the
    single source of truth.
    """
    calendar = persona.get("calendar") or {}
    calendar_days = calendar.get("days") or []
    precomputed = calendar.get("household_occupancy_hourly") or calendar.get("occupancy_hourly")
    if precomputed:
        out: list[list[float]] = []
        rows = list(precomputed)
        for day_idx in range(max(1, int(days))):
            src = rows[day_idx % len(rows)] if rows else [1.0] * 24
            values = list(src)[:24]
            while len(values) < 24:
                values.append(values[-1] if values else 1.0)
            out.append([round(max(0.0, min(1.0, float(value))), 4) for value in values])
        return out
    if not calendar_days:
        return None

    out: list[list[float]] = []
    for day_idx in range(max(1, int(days))):
        day = _calendar_day_for_index(calendar_days, day_idx + 1)
        if day is None:
            out.append([1.0] * 24)
            continue
        hourly = [1.0] * 24
        events = day.get("events") or []
        for event in events:
            location = str(event.get("location", "")).strip().lower()
            if location not in AWAY_LOCATIONS:
                continue
            try:
                start_h = float(event.get("start_h", 0.0))
                end_h = float(event.get("end_h", start_h))
            except (TypeError, ValueError):
                continue
            _subtract_hourly_interval(hourly, start_h, end_h)
        out.append([round(max(0.0, min(1.0, value)), 4) for value in hourly])
    return out


def occupancy_fraction_at_sim_hour(
    persona: dict[str, Any] | None,
    sim_h: float,
    *,
    default: float = 1.0,
) -> float:
    """Return role-play-calendar occupancy fraction for a simulation hour."""
    if not persona:
        return default
    day_idx = max(0, int(float(sim_h) // 24.0))
    profile = hourly_occupancy_from_persona(persona, day_idx + 1)
    if not profile:
        return default
    hour_idx = int(float(sim_h) % 24.0)
    try:
        return float(profile[day_idx][hour_idx])
    except (IndexError, TypeError, ValueError):
        return default


def occupied_at_sim_hour(persona: dict[str, Any] | None, sim_h: float, *, default: bool = True) -> bool:
    """Boolean convenience wrapper for calendar-derived occupancy."""
    fraction = occupancy_fraction_at_sim_hour(persona, sim_h, default=1.0 if default else 0.0)
    return fraction > OCCUPANCY_THRESHOLD


def _overlaps(start_a: float, end_a: float, start_b: float, end_b: float) -> bool:
    return max(start_a, start_b) < min(end_a, end_b)


def _calendar_day_for_index(days: list[dict[str, Any]], day_index_1based: int) -> dict[str, Any] | None:
    day = next((item for item in days if int(item.get("day", -1)) == day_index_1based), None)
    if day is not None or not days:
        return day
    weekly_day = ((day_index_1based - 1) % len(days)) + 1
    return next((item for item in days if int(item.get("day", -1)) == weekly_day), None)


def _subtract_hourly_interval(hourly: list[float], start_h: float, end_h: float) -> None:
    """Subtract interval overlap from 24 hourly occupancy fractions in place."""
    start = float(start_h)
    end = float(end_h)
    if end < start:
        _subtract_hourly_interval(hourly, start, 24.0)
        _subtract_hourly_interval(hourly, 0.0, end)
        return
    start = max(0.0, min(24.0, start))
    end = max(0.0, min(24.0, end))
    if end <= start:
        return
    for hour in range(24):
        overlap = max(0.0, min(end, hour + 1.0) - max(start, float(hour)))
        if overlap > 0:
            hourly[hour] = max(0.0, hourly[hour] - overlap)


def _fmt_h(value: Any) -> str:
    try:
        hour = float(value)
    except (TypeError, ValueError):
        return "?"
    h = int(hour) % 24
    m = int(round((hour % 1.0) * 60.0)) % 60
    return f"{h:02d}:{m:02d}"
