"""Day-ahead price and real-weather helpers for benchmark runs."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone as dt_timezone
from pathlib import Path
from typing import Any, Iterable


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REAL_DATA_DIR = PROJECT_ROOT / "experiments" / "real_data"
DEFAULT_GERMANY_PRICE_CSV = DEFAULT_REAL_DATA_DIR / "germany_2025_price.csv"
DEFAULT_GERMANY_WEATHER_CSV = DEFAULT_REAL_DATA_DIR / "germany_2025_weather.csv"
DEFAULT_GERMANY_EPW = PROJECT_ROOT / "experiments" / "weather" / "epw" / "DEU_Germany_2025_real.epw"


@dataclass(frozen=True)
class PricePoint:
    local_time: datetime
    price_eur_per_kwh: float


class DayAheadPriceProfile:
    """Hourly day-ahead price profile indexed by local time."""

    def __init__(self, points: Iterable[PricePoint], *, source: str = "") -> None:
        self.source = source
        self.points = sorted(points, key=lambda item: item.local_time)
        self._by_hour = {
            point.local_time.replace(minute=0, second=0, microsecond=0): point
            for point in self.points
        }

    @classmethod
    def from_csv(cls, path: Path, *, standard_timezone_hours: float | None = None) -> "DayAheadPriceProfile":
        points: list[PricePoint] = []
        with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                raw_time = row.get("Datetime (Local)") or row.get("datetime") or row.get("time")
                raw_utc_time = row.get("Datetime (UTC)")
                raw_price = row.get("Price (EUR/MWhe)") or row.get("price_eur_mwh")
                if not raw_time or raw_price in (None, ""):
                    continue
                if standard_timezone_hours is not None and raw_utc_time:
                    local_time = _to_standard_time(raw_utc_time, standard_timezone_hours)
                else:
                    local_time = datetime.fromisoformat(str(raw_time).strip()).replace(tzinfo=None)
                # ENTSO-E style wholesale prices are EUR/MWh. Convert to EUR/kWh.
                price_eur_per_kwh = float(raw_price) / 1000.0
                points.append(PricePoint(local_time=local_time, price_eur_per_kwh=price_eur_per_kwh))
        if not points:
            raise ValueError(f"No day-ahead prices found in {path}")
        return cls(points, source=str(path))

    def price_at(self, local_time: datetime) -> float | None:
        key = local_time.replace(minute=0, second=0, microsecond=0, tzinfo=None)
        point = self._by_hour.get(key)
        return point.price_eur_per_kwh if point else None

    def points_for_day(self, day: date) -> list[PricePoint]:
        start = datetime.combine(day, datetime.min.time())
        end = start + timedelta(days=1)
        return [point for point in self.points if start <= point.local_time < end]

    def prompt_context_for_day(self, day: date) -> str:
        """Compact price context for a daily 00:00 planning prompt."""
        points = self.points_for_day(day)
        if not points:
            return f"Day-ahead prices for {day.isoformat()}: unavailable."
        prices = [p.price_eur_per_kwh for p in points]
        cheapest = sorted(points, key=lambda p: p.price_eur_per_kwh)[:4]
        expensive = sorted(points, key=lambda p: p.price_eur_per_kwh, reverse=True)[:4]
        cheap_text = ", ".join(f"{p.local_time.hour:02d}:00={p.price_eur_per_kwh:.4f}" for p in cheapest)
        expensive_text = ", ".join(f"{p.local_time.hour:02d}:00={p.price_eur_per_kwh:.4f}" for p in expensive)
        negative = [p for p in points if p.price_eur_per_kwh < 0]
        negative_text = (
            "; negative-price hours: "
            + ", ".join(f"{p.local_time.hour:02d}:00" for p in negative[:8])
            if negative else ""
        )
        return (
            f"Day-ahead prices for {day.isoformat()} are known at this 00:00 planning step. "
            f"Use them as a secondary objective after comfort, service deadlines, and VPP constraints. "
            f"Price unit=EUR/kWh. avg={sum(prices)/len(prices):.4f}, "
            f"min={min(prices):.4f}, max={max(prices):.4f}. "
            f"Cheapest hours: {cheap_text}. Most expensive hours: {expensive_text}{negative_text}. "
            "Prefer flexible appliances and EV charging in low-price hours when this does not reduce user score."
        )


def maybe_load_price_profile(
    path: Path | None,
    *,
    standard_timezone_hours: float | None = None,
) -> DayAheadPriceProfile | None:
    if path is None:
        return None
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Price CSV not found: {path}")
    return DayAheadPriceProfile.from_csv(path, standard_timezone_hours=standard_timezone_hours)


def generate_epw_from_openmeteo_csv(
    csv_path: Path,
    epw_path: Path,
    *,
    location_name: str = "GermanyReal2025",
    latitude: float = 52.52,
    longitude: float = 13.405,
    timezone: float = 1.0,
    elevation_m: float = 34.0,
) -> Path:
    """Generate a simple hourly EPW from the Germany real-weather CSV."""
    csv_path = Path(csv_path)
    epw_path = Path(epw_path)
    epw_path.parent.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            ts = _to_standard_time(row["date"], timezone)
            rows.append({"ts": ts, **row})
    if not rows:
        raise ValueError(f"No weather rows found in {csv_path}")
    rows.sort(key=lambda item: item["ts"])
    rows = _regularize_hourly_rows(rows)

    header = [
        f"LOCATION,{location_name},Germany,DEU,RealData,000000,{latitude},{longitude},{timezone},{elevation_m}",
        "DESIGN CONDITIONS,0",
        "TYPICAL/EXTREME PERIODS,0",
        "GROUND TEMPERATURES,0",
        "HOLIDAYS/DAYLIGHT SAVINGS,No,0,0,0",
        f"COMMENTS 1,Generated from {csv_path}",
        "COMMENTS 2,Hourly real weather CSV converted by EnergyBridge.",
        "DATA PERIODS,1,1,Data,Sunday, 1/ 1,12/31",
    ]
    with epw_path.open("w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(header) + "\n")
        for item in rows:
            ts: datetime = item["ts"]
            dry = _float(item.get("temperature_2m"), 20.0)
            dew = _float(item.get("dew_point_2m"), dry - 5.0)
            rh = int(round(_float(item.get("relative_humidity_2m"), 50.0)))
            pressure_pa = int(round(_float(item.get("surface_pressure"), 1013.25) * 100.0))
            cloud_tenths = max(0, min(10, int(round(_float(item.get("cloud_cover"), 0.0) / 10.0))))
            wind_speed = _float(item.get("wind_speed_10m"), 0.0)
            ghi = max(0.0, _float(item.get("shortwave_radiation"), 0.0))
            dni = max(0.0, _float(item.get("direct_radiation"), 0.0))
            dhi = max(0.0, _float(item.get("diffuse_radiation"), 0.0))
            # EPW hour uses 1-24 for the interval ending at that clock hour.
            epw_hour = ts.hour + 1
            fields = [
                ts.year, ts.month, ts.day, epw_hour, 0,
                "?9?9?9?9E0?9?9?9*9*9?9*9*9?9*9*9?9?9*9*9*9*9C9*9*9",
                round(dry, 2), round(dew, 2), rh, pressure_pa,
                0, 0, 315,  # extraterrestrial fields + horizontal infrared placeholder
                round(ghi, 2), round(dni, 2), round(dhi, 2),
                0, 0, 0, 0,  # illuminance placeholders
                180, round(wind_speed, 2), cloud_tenths, cloud_tenths,
                999.0, 99999, 9, 999999999,
                0, 0.085, 0, 0, 0.2,
                round(_float(item.get("precipitation"), 0.0), 3), 1 if _float(item.get("precipitation"), 0.0) > 0 else 0,
            ]
            handle.write(",".join(str(value) for value in fields) + "\n")
    return epw_path


def generate_runperiod_idf(
    template_idf: Path,
    output_dir: Path,
    *,
    start_date: date,
    days: int,
) -> Path:
    """Create a run-specific IDF with the requested date range."""
    template_idf = Path(template_idf)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    end_date = start_date + timedelta(days=max(1, int(days)) - 1)
    target = output_dir / f"{template_idf.stem}_{start_date.isoformat()}_{days}days.idf"
    lines = template_idf.read_text(encoding="utf-8", errors="ignore").splitlines()
    for idx, line in enumerate(lines):
        if line.strip().lower() == "runperiod,":
            lines[idx + 2] = _idf_field(start_date.month, "Begin Month")
            lines[idx + 3] = _idf_field(start_date.day, "Begin Day of Month")
            lines[idx + 5] = _idf_field(end_date.month, "End Month")
            lines[idx + 6] = _idf_field(end_date.day, "End Day of Month")
            lines[idx + 8] = _idf_field(start_date.strftime("%A"), "Day of Week for Start Day")
            break
    else:
        raise ValueError(f"No RunPeriod object found in {template_idf}")
    target.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return target


def read_facility_meter_steps(out_dir: Path) -> list[dict[str, float]]:
    """Read EnergyPlus Electricity:Facility timestep energy from eplusout.mtr."""
    path = Path(out_dir) / "eplusout.mtr"
    if not path.exists():
        return []
    steps: list[dict[str, float]] = []
    current_window: tuple[float, float] | None = None
    in_data = False
    with path.open(errors="ignore") as handle:
        for raw in handle:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("End of Data Dictionary"):
                in_data = True
                continue
            if not in_data:
                continue
            parts = [part.strip() for part in line.split(",")]
            if not parts or not parts[0].isdigit():
                continue
            code = int(parts[0])
            if code == 2 and len(parts) >= 8:
                day = int(parts[1])
                hour = int(parts[5])
                start_min = float(parts[6])
                end_min = float(parts[7])
                start_h = (day - 1) * 24.0 + (hour - 1) + start_min / 60.0
                end_h = (day - 1) * 24.0 + (hour - 1) + end_min / 60.0
                current_window = (start_h, end_h)
                continue
            if code != 9 or current_window is None or len(parts) < 2:
                continue
            try:
                kwh = float(parts[1]) / 3_600_000.0
            except ValueError:
                continue
            steps.append({
                "start_h": current_window[0],
                "end_h": current_window[1],
                "kwh": kwh,
            })
    return steps


def compute_price_metrics(
    out_dir: Path,
    *,
    price_profile: DayAheadPriceProfile | None,
    start_date: date | None,
    sim_days: int,
) -> dict[str, Any]:
    if price_profile is None or start_date is None:
        return _nan_price_metrics(sim_days)
    steps = read_facility_meter_steps(out_dir)
    if not steps:
        return _nan_price_metrics(sim_days)
    total_cost = 0.0
    priced_kwh = 0.0
    missing_steps = 0
    per_day: list[dict[str, Any]] = []
    for day_idx in range(max(1, int(sim_days))):
        per_day.append({
            "day": day_idx + 1,
            "date": (start_date + timedelta(days=day_idx)).isoformat(),
            "energy_kwh": 0.0,
            "cost_eur": 0.0,
            "weighted_price_eur_per_kwh": math.nan,
        })

    for step in steps:
        local_time = datetime.combine(start_date, datetime.min.time()) + timedelta(hours=step["start_h"])
        price = price_profile.price_at(local_time)
        if price is None:
            missing_steps += 1
            continue
        kwh = float(step["kwh"])
        cost = kwh * price
        total_cost += cost
        priced_kwh += kwh
        day_idx = int(step["start_h"] // 24)
        if 0 <= day_idx < len(per_day):
            per_day[day_idx]["energy_kwh"] += kwh
            per_day[day_idx]["cost_eur"] += cost

    for item in per_day:
        if item["energy_kwh"] > 0:
            item["weighted_price_eur_per_kwh"] = round(item["cost_eur"] / item["energy_kwh"], 6)
        item["energy_kwh"] = round(item["energy_kwh"], 6)
        item["cost_eur"] = round(item["cost_eur"], 6)

    return {
        "available": priced_kwh > 0,
        "source": price_profile.source,
        "total_cost_eur": round(total_cost, 6),
        "priced_energy_kwh": round(priced_kwh, 6),
        "weighted_price_eur_per_kwh": round(total_cost / priced_kwh, 6) if priced_kwh > 0 else math.nan,
        "missing_price_steps": missing_steps,
        "per_day": per_day,
    }


def _nan_price_metrics(sim_days: int) -> dict[str, Any]:
    return {
        "available": False,
        "source": "",
        "total_cost_eur": math.nan,
        "priced_energy_kwh": math.nan,
        "weighted_price_eur_per_kwh": math.nan,
        "missing_price_steps": 0,
        "per_day": [
            {
                "day": idx + 1,
                "date": "",
                "energy_kwh": math.nan,
                "cost_eur": math.nan,
                "weighted_price_eur_per_kwh": math.nan,
            }
            for idx in range(max(1, int(sim_days)))
        ],
    }


def _idf_field(value: Any, label: str) -> str:
    return f"    {value},                       !- {label}"


def _to_standard_time(raw_time: Any, standard_timezone_hours: float) -> datetime:
    """Convert a real timestamp to the fixed standard time used by EnergyPlus."""
    ts = datetime.fromisoformat(str(raw_time).strip())
    if ts.tzinfo is None:
        return ts.replace(minute=0, second=0, microsecond=0)
    utc_ts = ts.astimezone(dt_timezone.utc).replace(tzinfo=None)
    return (utc_ts + timedelta(hours=standard_timezone_hours)).replace(minute=0, second=0, microsecond=0)


def _regularize_hourly_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep generated EPW data strictly continuous despite DST transitions."""
    if not rows:
        return []
    by_ts: dict[datetime, list[dict[str, Any]]] = {}
    for row in rows:
        by_ts.setdefault(row["ts"], []).append(row)

    start = min(by_ts)
    end = max(by_ts)
    current = start
    previous: dict[str, Any] | None = None
    regularized: list[dict[str, Any]] = []
    while current <= end:
        candidates = by_ts.get(current)
        if candidates:
            row = candidates[-1].copy()
        elif previous is not None:
            # DST spring-forward creates one missing civil hour. Repeating the
            # last weather sample avoids shifting all later EPW rows.
            row = previous.copy()
            row["date"] = current.isoformat(sep=" ")
        else:
            current += timedelta(hours=1)
            continue
        row["ts"] = current
        regularized.append(row)
        previous = row
        current += timedelta(hours=1)
    return regularized


def _float(value: Any, default: float) -> float:
    try:
        number = float(value)
        if math.isnan(number):
            return default
        return number
    except (TypeError, ValueError):
        return default
