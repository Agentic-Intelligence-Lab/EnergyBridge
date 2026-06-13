#!/usr/bin/env python3
"""Generate deterministic 7-day synthetic calendars for approved personas.

Day 1 is Sunday so the default 3-day benchmark maps to Sunday, Monday, Tuesday.
The generated calendars are tag-aware: commute/home/caregiver/irregular
patterns, appliance rigidity, EV constraints, and comfort/control tags all
shape the daily events and deadlines.
"""
from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
OUT_ROOT = ROOT / "calendars"
DAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")


def main() -> None:
    written = []
    for persona_path in sorted(ROOT.glob("*.json")):
        persona = json.loads(persona_path.read_text(encoding="utf-8"))
        if not persona.get("meta", {}).get("approved", False):
            continue
        data = build_calendar(persona)
        out_dir = OUT_ROOT / persona["id"]
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "calendar_7day.json"
        out_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        written.append(out_path)
    print(f"wrote {len(written)} calendars")
    for path in written:
        print(path.relative_to(ROOT))


def build_calendar(persona: dict) -> dict:
    tags = persona.get("tags", {})
    schedule = persona.get("schedule", {})
    appliances = persona.get("appliances", {})
    persona_id = persona["id"]
    return {
        "schema_version": "calendar_v1",
        "persona_id": persona_id,
        "source": "synthetic_energybridge_calendar_v2_tag_aware",
        "timezone": "Asia/Shanghai",
        "week_start": "Sunday",
        "description": (
            "Tag-aware 7-day synthetic calendar. Day1 is Sunday; the default "
            "3-day benchmark therefore evaluates Sunday, Monday, and Tuesday."
        ),
        "persona_tags": tags,
        "days": [
            build_day(day_idx, tags, schedule, appliances)
            for day_idx in range(1, 8)
        ],
    }


def build_day(day_idx: int, tags: dict, schedule: dict, appliances: dict) -> dict:
    day_name = DAY_NAMES[day_idx - 1]
    is_weekend = day_idx in (1, 7)
    schedule_tag = tags.get("schedule", "regular_commuter")
    comfort_tag = tags.get("comfort", "normal_comfort")
    control_tag = tags.get("control", "suggestion_first")
    task_tag = tags.get("task", "semi_rigid")

    wake_h = float(schedule.get("wake_h", 7.0))
    bath_h = float(schedule.get("bath_shower_h", 21.0))
    sleep_h = float(schedule.get("sleep_h", 23.0))
    events = [{"title": "Wake and morning routine", "start_h": wake_h, "end_h": wake_h + 0.5,
               "location": "home", "energy_relevance": "morning comfort baseline"}]

    if schedule_tag == "stay_at_home":
        events += stay_home_events(day_name, comfort_tag)
        home_arrival_h = wake_h
        next_departure_h = None
        summary = f"{day_name}: mostly at home; comfort constraints apply through the day."
    elif schedule_tag == "caregiver":
        events += caregiver_events(day_name)
        home_arrival_h = wake_h
        next_departure_h = None
        summary = f"{day_name}: caregiving schedule with vulnerable household member at home."
    elif schedule_tag == "irregular":
        events += irregular_events(day_idx, day_name, is_weekend)
        home_arrival_h = 19.0 if day_idx % 2 else 16.0
        next_departure_h = None
        summary = f"{day_name}: irregular plan; user dislikes assumptions without explanation."
    elif schedule_tag == "night_owl":
        events += night_owl_events(day_name, is_weekend)
        home_arrival_h = 20.5
        next_departure_h = None
        summary = f"{day_name}: late schedule; evening and night comfort matter."
    else:
        if is_weekend:
            events += weekend_commuter_events(day_name)
            home_arrival_h = 17.0
            next_departure_h = next_work_departure(day_idx, schedule)
            summary = f"{day_name}: non-workday with errands and evening home time."
        else:
            leave_h = float(schedule.get("leaves_home_h", 8.5))
            return_h = float(schedule.get("returns_home_h", 18.5))
            events += commuter_events(day_name, leave_h, return_h)
            home_arrival_h = return_h
            next_departure_h = day_idx * 24.0 + float(schedule.get("leaves_home_h", 8.5))
            summary = f"{day_name}: regular workday commute; return-home comfort is relevant."

    events += appliance_events(appliances, day_idx, task_tag, bath_h)
    events.append({"title": "Bath / shower", "start_h": bath_h, "end_h": bath_h + 0.5,
                   "location": "home", "energy_relevance": "hot water deadline"})
    events.append({"title": "Sleep", "start_h": sleep_h, "end_h": min(24.0, sleep_h + 0.5),
                   "location": "home", "energy_relevance": "quiet comfort"})

    if comfort_tag in ("temp_sensitive", "low_control_tolerance"):
        summary += " Temperature deviations are very noticeable."
    if control_tag in ("confirm_required", "low_auto_accept", "privacy_sensitive"):
        summary += " User expects explicit consent or careful explanation."

    return {
        "day": day_idx,
        "weekday": day_name,
        "day_type": "weekend" if is_weekend else "weekday",
        "summary": summary,
        "events": sorted(events, key=lambda item: float(item.get("start_h", 0.0))),
        "constraints": {
            "home_arrival_h": home_arrival_h,
            "next_departure_h": next_departure_h,
            "bath_shower_h": bath_h,
            "vulnerable_member_home": schedule_tag == "caregiver",
            "appliance_deadlines": appliance_deadlines(appliances, task_tag, bath_h, next_departure_h),
        },
    }


def commuter_events(day_name: str, leave_h: float, return_h: float) -> list[dict]:
    return [
        {"title": "Commute to office", "start_h": leave_h - 0.5, "end_h": leave_h,
         "location": "transit", "energy_relevance": "home becomes unoccupied"},
        {"title": "Working hours - in office", "start_h": leave_h, "end_h": max(leave_h, return_h - 0.5),
         "location": "office", "energy_relevance": "home away period"},
        {"title": "Commute home", "start_h": return_h - 0.5, "end_h": return_h,
         "location": "transit", "energy_relevance": "VPP may overlap return-home preparation"},
        {"title": f"{day_name} dinner at home", "start_h": max(19.0, return_h + 0.2), "end_h": 20.0,
         "location": "home", "energy_relevance": "post-VPP comfort"},
    ]


def weekend_commuter_events(day_name: str) -> list[dict]:
    return [
        {"title": f"{day_name} home morning", "start_h": 9.0, "end_h": 11.0,
         "location": "home", "energy_relevance": "occupied comfort"},
        {"title": f"{day_name} errands", "start_h": 14.0, "end_h": 17.0,
         "location": "outside", "energy_relevance": "away from home before VPP"},
        {"title": f"{day_name} evening at home", "start_h": 18.0, "end_h": 21.0,
         "location": "home", "energy_relevance": "VPP directly overlaps home activity"},
    ]


def stay_home_events(day_name: str, comfort_tag: str) -> list[dict]:
    relevance = "direct VPP conflict; comfort sensitive" if comfort_tag == "temp_sensitive" else "occupied comfort"
    return [
        {"title": f"{day_name} remote/home activities", "start_h": 9.0, "end_h": 17.5,
         "location": "home", "energy_relevance": "occupied comfort"},
        {"title": f"{day_name} dinner preparation", "start_h": 18.0, "end_h": 19.0,
         "location": "home", "energy_relevance": relevance},
    ]


def caregiver_events(day_name: str) -> list[dict]:
    return [
        {"title": "Medication routine", "start_h": 8.0, "end_h": 8.5,
         "location": "home", "energy_relevance": "safety routine"},
        {"title": "Nap/rest period", "start_h": 14.0, "end_h": 16.0,
         "location": "home", "energy_relevance": "avoid noise and discomfort"},
        {"title": f"{day_name} dinner care", "start_h": 18.0, "end_h": 19.0,
         "location": "home", "energy_relevance": "VPP conflict with vulnerable member at home"},
    ]


def irregular_events(day_idx: int, day_name: str, is_weekend: bool) -> list[dict]:
    if is_weekend:
        return [
            {"title": f"{day_name} flexible outing", "start_h": 13.0, "end_h": 18.5,
             "location": "outside", "energy_relevance": "uncertain return near VPP"},
            {"title": f"{day_name} evening recovery", "start_h": 19.0, "end_h": 21.0,
             "location": "home", "energy_relevance": "post-VPP comfort"},
        ]
    if day_idx % 2:
        return [
            {"title": "Client visit", "start_h": 10.0, "end_h": 13.0,
             "location": "outside", "energy_relevance": "away"},
            {"title": "Uncertain return window", "start_h": 17.5, "end_h": 19.2,
             "location": "transit", "energy_relevance": "VPP overlaps uncertain arrival"},
        ]
    return [
        {"title": "Home admin block", "start_h": 16.0, "end_h": 19.0,
         "location": "home", "energy_relevance": "VPP conflict with occupancy"},
    ]


def night_owl_events(day_name: str, is_weekend: bool) -> list[dict]:
    return [
        {"title": f"{day_name} late return", "start_h": 19.5 if not is_weekend else 18.5, "end_h": 20.5,
         "location": "transit", "energy_relevance": "late arrival"},
        {"title": f"{day_name} night activity", "start_h": 22.0, "end_h": 23.8,
         "location": "home", "energy_relevance": "night comfort and appliance use"},
    ]


def appliance_events(appliances: dict, day_idx: int, task_tag: str, bath_h: float) -> list[dict]:
    events = []
    washer = appliances.get("washer", {})
    if washer.get("present", False):
        preferred = float(washer.get("preferred_h", 19.0))
        rigid = task_tag == "rigid" or not washer.get("shiftable", True)
        events.append({
            "title": "Laundry task" if not rigid else "Fixed laundry routine",
            "start_h": preferred,
            "end_h": preferred + float(washer.get("duration_h", 2.0)),
            "location": "home",
            "energy_relevance": "rigid task timing" if rigid else "shiftable chore deadline",
        })
    dishwasher = appliances.get("dishwasher", {})
    if dishwasher.get("present", False):
        preferred = float(dishwasher.get("preferred_h", 21.0))
        events.append({
            "title": "Dishwasher load",
            "start_h": preferred,
            "end_h": preferred + float(dishwasher.get("duration_h", 1.5)),
            "location": "home",
            "energy_relevance": "dishwasher task deadline",
        })
    ev = appliances.get("ev", {})
    if ev.get("present", False):
        arrival = float(ev.get("arrival_h", 18.5))
        departure = float(ev.get("departure_h", 7.5))
        events.append({
            "title": "EV arrives home",
            "start_h": max(0.0, arrival - 0.2),
            "end_h": arrival,
            "location": "driveway",
            "energy_relevance": "EV charging can start after arrival",
        })
        events.append({
            "title": "Next EV departure deadline",
            "start_h": 24.0 if departure == 0 else min(23.9, departure),
            "end_h": min(24.0, departure + 0.3) if departure < 23.7 else 24.0,
            "location": "driveway",
            "energy_relevance": "EV must reach target SOC before departure",
        })
    return events


def appliance_deadlines(appliances: dict, task_tag: str, bath_h: float, next_departure_h: float | None) -> dict:
    deadlines = {}
    washer = appliances.get("washer", {})
    if washer.get("present", False):
        if task_tag == "rigid" or not washer.get("shiftable", True):
            deadlines["washer"] = f"fixed around {fmt_h(washer.get('preferred_h', 19.0))}; do not skip"
        else:
            deadlines["washer"] = "complete today while avoiding 18:00-19:00 if possible"
    dishwasher = appliances.get("dishwasher", {})
    if dishwasher.get("present", False):
        deadlines["dishwasher"] = "finish before sleep; avoid VPP window if possible"
    wh = appliances.get("water_heater", {})
    if wh.get("present", True):
        deadlines["water_heater"] = f"hot water ready before {fmt_h(bath_h)}"
    ev = appliances.get("ev", {})
    if ev.get("present", False):
        if next_departure_h is not None:
            deadlines["ev"] = f"charge before next departure at absolute hour {next_departure_h:.1f}"
        else:
            deadlines["ev"] = "maintain enough SOC for next trip"
    return deadlines


def next_work_departure(day_idx: int, schedule: dict) -> float | None:
    if day_idx == 1:
        return 24.0 + float(schedule.get("leaves_home_h", 8.5))
    if day_idx == 7:
        return 48.0 + float(schedule.get("leaves_home_h", 8.5))
    return None


def fmt_h(value) -> str:
    hour = float(value)
    h = int(hour) % 24
    m = int(round((hour % 1) * 60)) % 60
    return f"{h:02d}:{m:02d}"


if __name__ == "__main__":
    main()
