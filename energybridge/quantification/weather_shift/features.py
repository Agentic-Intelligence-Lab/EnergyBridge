"""Daily weather feature extraction for the June->target weather-shift correction.

Builds a per-calendar-day feature vector (temperature/humidity/radiation
aggregates) from the raw hourly weather sources already used by the
simulator, so importance-sampling weights can be trained against any target
month/scenario without re-running any simulation.

Germany uses the real-2025 hourly CSV (`germany_2025_weather.csv`).
Tianjin uses the CSWD typical-year EPW (`CHN_TJ_Tianjin.545270_CSWD.epw`) --
the EPW's own "year" column is a synthetic TMY marker, not a real calendar
year, so lookups key off (month, day) only.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

FEATURE_NAMES = ["t_mean_day", "t_max_day", "rh_mean_day", "ghi_sum_day", "cloud_cover_mean_day"]

DEFAULT_GERMANY_WEATHER_CSV = Path("experiments/real_data/germany_2025_weather.csv")
DEFAULT_TIANJIN_EPW = Path("experiments/weather/epw/CHN_TJ_Tianjin.545270_CSWD.epw")

# Standard EPW column indices (0-based) for the hourly data rows.
_EPW_MONTH = 1
_EPW_DAY = 2
_EPW_HOUR = 3
_EPW_DRYBULB = 6
_EPW_RH = 8
_EPW_GHI = 13
_EPW_CLOUD = 22  # Total Sky Cover, tenths (0-10)


def _resolve(path: str | Path | None, default: Path) -> Path:
    if path is None:
        return default
    return Path(path)


def load_germany_daily_features(weather_csv: str | Path | None = None) -> dict[str, dict[str, float]]:
    """Return {"YYYY-MM-DD": {feature_name: value}} for every day in the CSV."""
    path = _resolve(weather_csv, DEFAULT_GERMANY_WEATHER_CSV)
    by_day: dict[str, list[tuple[float, float, float, float]]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            day_key = row["date"][:10]
            temp = float(row["temperature_2m"])
            rh = float(row["relative_humidity_2m"])
            ghi = float(row["shortwave_radiation"])
            cloud = float(row["cloud_cover"])
            by_day[day_key].append((temp, rh, ghi, cloud))
    out: dict[str, dict[str, float]] = {}
    for day_key, rows in by_day.items():
        temps = [r[0] for r in rows]
        rhs = [r[1] for r in rows]
        ghis = [r[2] for r in rows]
        clouds = [r[3] for r in rows]
        out[day_key] = {
            "t_mean_day": sum(temps) / len(temps),
            "t_max_day": max(temps),
            "rh_mean_day": sum(rhs) / len(rhs),
            "ghi_sum_day": sum(ghis),
            "cloud_cover_mean_day": sum(clouds) / len(clouds),
        }
    return out


def load_tianjin_daily_features(epw_path: str | Path | None = None) -> dict[str, dict[str, float]]:
    """Return {"MM-DD": {feature_name: value}} for every day in the EPW.

    Keyed by month-day only (no year) because the EPW is a synthetic
    typical-meteorological-year file, not a specific calendar year.
    """
    path = _resolve(epw_path, DEFAULT_TIANJIN_EPW)
    by_day: dict[str, list[tuple[float, float, float, float]]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.reader(fh)
        for _ in range(8):
            next(reader, None)  # skip the 8 EPW header lines
        for row in reader:
            if len(row) <= _EPW_CLOUD:
                continue
            month = int(row[_EPW_MONTH])
            day = int(row[_EPW_DAY])
            day_key = f"{month:02d}-{day:02d}"
            temp = float(row[_EPW_DRYBULB])
            rh = float(row[_EPW_RH])
            ghi = float(row[_EPW_GHI])
            cloud = float(row[_EPW_CLOUD])
            by_day[day_key].append((temp, rh, ghi, cloud))
    out: dict[str, dict[str, float]] = {}
    for day_key, rows in by_day.items():
        temps = [r[0] for r in rows]
        rhs = [r[1] for r in rows]
        ghis = [r[2] for r in rows]
        clouds = [r[3] for r in rows]
        out[day_key] = {
            "t_mean_day": sum(temps) / len(temps),
            "t_max_day": max(temps),
            "rh_mean_day": sum(rhs) / len(rhs),
            "ghi_sum_day": sum(ghis),
            "cloud_cover_mean_day": sum(clouds) / len(clouds),
        }
    return out


def daily_features_for_city(city: str, weather_path: str | Path | None = None) -> dict[str, dict[str, float]]:
    city_l = city.strip().lower()
    if city_l == "germany":
        return load_germany_daily_features(weather_path)
    if city_l == "tianjin":
        return load_tianjin_daily_features(weather_path)
    raise ValueError(f"Unsupported city for weather features: {city!r}")


def month_feature_matrix(
    city: str,
    year: int,
    month: int,
    weather_path: str | Path | None = None,
) -> tuple[list[str], list[list[float]]]:
    """Return (day_keys, x_rows) for every day of `month` (1-31) in `year`.

    `day_keys` are "YYYY-MM-DD" regardless of city (Tianjin's EPW is looked
    up by month/day and re-labelled with the requested `year` for reporting).
    """
    daily = daily_features_for_city(city, weather_path)
    city_l = city.strip().lower()
    day_keys: list[str] = []
    rows: list[list[float]] = []
    for day in range(1, 32):
        try:
            iso = f"{year:04d}-{month:02d}-{day:02d}"
            # Validate day-of-month without pulling in datetime edge cases.
            import datetime as _dt

            _dt.date(year, month, day)
        except ValueError:
            continue
        lookup_key = iso if city_l == "germany" else f"{month:02d}-{day:02d}"
        feats = daily.get(lookup_key)
        if feats is None:
            continue
        day_keys.append(iso)
        rows.append([feats[name] for name in FEATURE_NAMES])
    return day_keys, rows


def event_weather_features(
    city: str,
    date_str: str,
    daily_cache: dict[str, dict[str, float]] | None = None,
    weather_path: str | Path | None = None,
) -> dict[str, float] | None:
    """Look up the daily feature vector for one memory event's start_date."""
    daily = daily_cache if daily_cache is not None else daily_features_for_city(city, weather_path)
    city_l = city.strip().lower()
    if city_l == "germany":
        key = date_str[:10]
    else:
        key = date_str[5:10] if len(date_str) >= 10 else date_str
    return daily.get(key)


def attach_weather_to_memory(memory_path: str | Path, output_path: str | Path) -> dict[str, Any]:
    """Load a dr_event_memory JSON, attach `weather_features` to every event."""
    import json

    with open(memory_path, encoding="utf-8") as fh:
        memory = json.load(fh)

    caches: dict[str, dict[str, dict[str, float]]] = {}
    missing = 0
    for event in memory.get("events", []):
        city = str(event.get("city") or "")
        if city not in caches:
            caches[city] = daily_features_for_city(city)
        feats = event_weather_features(city, str(event.get("start_date") or ""), daily_cache=caches[city])
        if feats is None:
            missing += 1
            event["weather_features"] = None
        else:
            event["weather_features"] = feats

    memory.setdefault("summary", {})["weather_features_missing_count"] = missing
    with open(output_path, "w", encoding="utf-8") as fh:
        json.dump(memory, fh, indent=2)
    return {"total_events": len(memory.get("events", [])), "missing": missing, "output": str(output_path)}
