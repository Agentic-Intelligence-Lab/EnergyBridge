"""Generate a reproducible typical human schedule for June 1-7."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List

import csv
import json
import random

START_DATE = date(2026, 6, 1)
END_DATE = date(2026, 6, 7)
DEFAULT_SEED = 20260601
STEP_MINUTES = 10


@dataclass
class HotWaterEvent:
    timestamp: str
    volume_l: float
    label: str


@dataclass
class TaskEvent:
    device: str
    earliest_start: str
    latest_finish: str
    duration_minutes: int
    rated_power_kw: float
    label: str


@dataclass
class DaySchedule:
    date: str
    weekday: str
    day_type: str
    wake_time: str
    sleep_time: str
    ev_departure: str | None
    ev_arrival: str | None
    ev_drive_kwh: float
    hot_water_events: List[HotWaterEvent]
    task_events: List[TaskEvent]
    weekend_short_trip: bool = False


def _round_to_step(dt: datetime, step_minutes: int = STEP_MINUTES) -> datetime:
    day_start = datetime.combine(dt.date(), time(0, 0))
    minutes = (dt - day_start).total_seconds() / 60.0
    rounded = int(round(minutes / step_minutes) * step_minutes)
    return day_start + timedelta(minutes=rounded)


def _at(day: date, hhmm: str) -> datetime:
    hour, minute = [int(part) for part in hhmm.split(":")]
    return datetime.combine(day, time(hour, minute))


def _jitter(rng: random.Random, day: date, base_hhmm: str, low_min: int, high_min: int) -> datetime:
    return _round_to_step(_at(day, base_hhmm) + timedelta(minutes=rng.randint(low_min, high_min)))


def _trunc_gauss(rng: random.Random, mean: float, std: float, low: float, high: float) -> float:
    for _ in range(100):
        value = rng.gauss(mean, std)
        if low <= value <= high:
            return value
    return min(high, max(low, mean))


def _fmt(dt: datetime | None) -> str | None:
    return None if dt is None else dt.isoformat(sep=" ")


def _task(device: str, earliest: datetime, latest: datetime, duration: int, rated_kw: float, label: str) -> TaskEvent:
    return TaskEvent(
        device=device,
        earliest_start=_fmt(earliest),
        latest_finish=_fmt(latest),
        duration_minutes=duration,
        rated_power_kw=rated_kw,
        label=label,
    )


def generate_typical_week(seed: int = DEFAULT_SEED) -> List[DaySchedule]:
    rng = random.Random(seed)
    schedules: List[DaySchedule] = []
    laundry_days = {date(2026, 6, 2), date(2026, 6, 6)}
    shifted_laundry_days: set[date] = set()
    for d in laundry_days:
        roll = rng.random()
        if roll < 0.2 and d > START_DATE:
            shifted_laundry_days.add(d - timedelta(days=1))
        elif roll > 0.8 and d < END_DATE:
            shifted_laundry_days.add(d + timedelta(days=1))
        else:
            shifted_laundry_days.add(d)

    current = START_DATE
    while current <= END_DATE:
        weekday = current.strftime("%a")
        is_weekend = current.weekday() >= 5
        day_type = "weekend" if is_weekend else "weekday"
        task_events: List[TaskEvent] = []
        hot_water_events: List[HotWaterEvent] = []

        if is_weekend:
            wake = _jitter(rng, current, "08:00", -30, 40)
            sleep = _jitter(rng, current, "23:30", -30, 40)
            morning_hw = _jitter(rng, current, "08:40", -20, 30)
            evening_hw = _jitter(rng, current, "21:30", -30, 30)
            weekend_short_trip = rng.random() < 0.6 and current.weekday() == 5
            if weekend_short_trip:
                ev_departure = _jitter(rng, current, "10:30", -45, 60)
                ev_arrival = _jitter(rng, current, "12:30", -30, 60)
                drive_kwh = round(_trunc_gauss(rng, 4.0, 1.2, 1.0, 7.0), 2)
            else:
                ev_departure = None
                ev_arrival = None
                drive_kwh = 0.0
            morning_l = round(1.15 * _trunc_gauss(rng, 35.0, 8.0, 20.0, 55.0), 1)
            evening_l = round(_trunc_gauss(rng, 45.0, 10.0, 25.0, 70.0), 1)
            dinner_end = _jitter(rng, current, "19:30", -30, 45)
        else:
            wake = _jitter(rng, current, "06:30", -20, 20)
            sleep = _jitter(rng, current, "23:00", -30, 30)
            morning_hw = _jitter(rng, current, "06:50", -10, 20)
            evening_hw = _jitter(rng, current, "21:00", -20, 30)
            ev_departure = _jitter(rng, current, "08:00", -20, 20)
            ev_arrival = _jitter(rng, current, "18:00", -30, 45)
            drive_kwh = round(_trunc_gauss(rng, 9.0, 2.0, 5.0, 14.0), 2)
            morning_l = round(_trunc_gauss(rng, 35.0, 8.0, 20.0, 55.0), 1)
            evening_l = round(_trunc_gauss(rng, 45.0, 10.0, 25.0, 70.0), 1)
            dinner_end = _jitter(rng, current, "19:30", -20, 30)
            weekend_short_trip = False

        hot_water_events.append(HotWaterEvent(_fmt(morning_hw), morning_l, "morning"))
        hot_water_events.append(HotWaterEvent(_fmt(evening_hw), evening_l, "evening"))

        if rng.random() < 0.85:
            task_events.append(_task("dishwasher", dinner_end, _at(current, "23:00"), 90, 1.2, "after_dinner"))

        if current in shifted_laundry_days:
            if is_weekend:
                washer_start = _jitter(rng, current, "14:00", -50, 60)
                washer_latest = _at(current, "18:00")
                dryer_start = washer_start + timedelta(minutes=70)
                dryer_latest = _at(current, "19:30")
            else:
                washer_start = _jitter(rng, current, "19:30", -20, 20)
                washer_latest = _at(current, "22:00")
                dryer_start = washer_start + timedelta(minutes=70)
                dryer_latest = _at(current, "23:00")
            task_events.append(_task("clothes_washer", washer_start, washer_latest, 60, 0.5, "laundry"))
            task_events.append(_task("clothes_dryer", _round_to_step(dryer_start), dryer_latest, 60, 3.0, "laundry_after_washer"))

        schedules.append(
            DaySchedule(
                date=current.isoformat(),
                weekday=weekday,
                day_type=day_type,
                wake_time=_fmt(wake),
                sleep_time=_fmt(sleep),
                ev_departure=_fmt(ev_departure),
                ev_arrival=_fmt(ev_arrival),
                ev_drive_kwh=drive_kwh,
                hot_water_events=hot_water_events,
                task_events=task_events,
                weekend_short_trip=weekend_short_trip,
            )
        )
        current += timedelta(days=1)
    return schedules


def schedule_to_dict(schedules: List[DaySchedule]) -> Dict[str, Any]:
    return {
        "seed": DEFAULT_SEED,
        "start_datetime": "2026-06-01 00:00:00",
        "end_datetime": "2026-06-08 00:00:00",
        "time_step_minutes": STEP_MINUTES,
        "days": [asdict(day) for day in schedules],
    }


def write_schedule_outputs(schedules: List[DaySchedule], output_dir: Path, seed: int = DEFAULT_SEED) -> Dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    data = schedule_to_dict(schedules)
    data["seed"] = seed
    json_path = output_dir / f"typical_human_schedule_seed{seed}.json"
    csv_path = output_dir / f"typical_human_schedule_seed{seed}.csv"
    json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    rows: List[Dict[str, Any]] = []
    for day in schedules:
        base = asdict(day)
        hot_water = "; ".join(f"{e.timestamp} {e.volume_l}L {e.label}" for e in day.hot_water_events)
        tasks = "; ".join(f"{t.device} {t.earliest_start}->{t.latest_finish}" for t in day.task_events)
        rows.append(
            {
                "date": base["date"],
                "weekday": base["weekday"],
                "day_type": base["day_type"],
                "wake_time": base["wake_time"],
                "sleep_time": base["sleep_time"],
                "ev_departure": base["ev_departure"],
                "ev_arrival": base["ev_arrival"],
                "ev_drive_kwh": base["ev_drive_kwh"],
                "hot_water_events": hot_water,
                "task_events": tasks,
            }
        )
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return {"json": str(json_path), "csv": str(csv_path)}


if __name__ == "__main__":
    out = Path(__file__).resolve().parent / "outputs"
    schedules = generate_typical_week(DEFAULT_SEED)
    print(json.dumps(write_schedule_outputs(schedules, out, DEFAULT_SEED), indent=2, ensure_ascii=False))
