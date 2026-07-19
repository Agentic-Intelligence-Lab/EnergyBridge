"""Optional weather features for DR-memory retrieval.

The capacity-memory retriever can use these features when a weather feature
library is available.  Benchmark controllers should still import and run when
that optional library is absent, so the default implementation is deliberately
conservative and returns no weather adjustment.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Mapping

FEATURE_NAMES = ("t_mean_day", "t_max_day", "rh_mean_day", "ghi_sum_day", "cloud_cover_mean_day")


def daily_features_for_city(city: str) -> dict[str, dict[str, float]]:
    """Return daily weather features keyed by ISO date.

    This fallback keeps the capacity retriever importable without bundled
    weather-derived feature tables.
    """

    return {}


def event_weather_features(
    city: str,
    start_date: str,
    *,
    daily_cache: Mapping[str, Mapping[str, float]] | None = None,
) -> dict[str, float] | None:
    """Return weather features for one event date, if available."""

    if not start_date:
        return None
    cache = daily_cache or {}
    values = cache.get(str(start_date))
    if not values:
        return None
    try:
        return {name: float(values[name]) for name in FEATURE_NAMES}
    except (KeyError, TypeError, ValueError):
        return None


features = SimpleNamespace(
    FEATURE_NAMES=FEATURE_NAMES,
    daily_features_for_city=daily_features_for_city,
    event_weather_features=event_weather_features,
)
