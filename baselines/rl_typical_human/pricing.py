"""Simple time-of-use electricity price model."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, List


@dataclass(frozen=True)
class TimeOfUsePrice:
    """Electricity price for a timestamp."""

    period: str
    price_yuan_per_kwh: float


@dataclass(frozen=True)
class TimeOfUsePeriod:
    name: str
    start_hour: float
    end_hour: float
    price_yuan_per_kwh: float

    def contains(self, hour: float) -> bool:
        if self.start_hour <= self.end_hour:
            return self.start_hour <= hour < self.end_hour
        return hour >= self.start_hour or hour < self.end_hour


class TimeOfUsePricing:
    """A replaceable three-period TOU tariff.

    Current values are intentionally simple placeholders for controller and
    reward validation. They can be replaced by official Tianjin residential
    tariff data without changing the wrapper interface.
    """

    def __init__(self, periods: Iterable[TimeOfUsePeriod] | None = None) -> None:
        self.periods: List[TimeOfUsePeriod] = list(periods) if periods is not None else [
            TimeOfUsePeriod("valley", 0.0, 7.0, 0.30),
            TimeOfUsePeriod("flat", 7.0, 17.0, 0.55),
            TimeOfUsePeriod("peak", 17.0, 22.0, 0.85),
            TimeOfUsePeriod("flat", 22.0, 24.0, 0.55),
        ]

    @staticmethod
    def hour_decimal(timestamp: datetime) -> float:
        return timestamp.hour + timestamp.minute / 60.0 + timestamp.second / 3600.0

    def price_at(self, timestamp: datetime) -> TimeOfUsePrice:
        hour = self.hour_decimal(timestamp)
        for period in self.periods:
            if period.contains(hour):
                return TimeOfUsePrice(period.name, period.price_yuan_per_kwh)
        raise ValueError(f"No TOU period matched hour={hour}")
