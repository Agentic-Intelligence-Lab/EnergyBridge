"""Historical time-slot baseline model for household P_base estimation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from math import sqrt
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List, Mapping, Sequence

EPS = 1e-9


@dataclass(frozen=True)
class BaselinePrediction:
    """Predicted counterfactual load distribution for one timestamp."""

    key: str
    n_samples: int
    p_base_mean_kw: float
    p_base_p05_kw: float
    p_base_p10_kw: float
    p_base_p30_kw: float
    p_base_p50_kw: float
    p_base_p70_kw: float
    p_base_p90_kw: float
    p_base_p95_kw: float
    std_kw: float
    baseline_confidence: float
    sample_score: float
    stability_score: float
    recent_error_score: float

    def as_dict(self) -> Dict[str, Any]:
        return {
            "baseline_key": self.key,
            "n_samples": self.n_samples,
            "p_base_mean_kw": round(self.p_base_mean_kw, 6),
            "p_base_p05_kw": round(self.p_base_p05_kw, 6),
            "p_base_p10_kw": round(self.p_base_p10_kw, 6),
            "p_base_p30_kw": round(self.p_base_p30_kw, 6),
            "p_base_p50_kw": round(self.p_base_p50_kw, 6),
            "p_base_p70_kw": round(self.p_base_p70_kw, 6),
            "p_base_p90_kw": round(self.p_base_p90_kw, 6),
            "p_base_p95_kw": round(self.p_base_p95_kw, 6),
            "std_kw": round(self.std_kw, 6),
            "baseline_confidence": round(self.baseline_confidence, 6),
            "sample_score": round(self.sample_score, 6),
            "stability_score": round(self.stability_score, 6),
            "recent_error_score": round(self.recent_error_score, 6),
        }


class TimeSlotBaselineModel:
    """Transparent P_base estimator using historical samples per time slot.

    The default key is ``day_type + minute_of_day``. This is intentionally more
    data-efficient than weekday-specific keys for the current 14-day training
    window: weekday slots have about 10 samples, weekend slots about 4 samples.
    """

    def __init__(self, required_samples: int = 7, fallback_required_samples: int = 14):
        self.required_samples = required_samples
        self.fallback_required_samples = fallback_required_samples
        self.samples_by_key: Dict[str, List[float]] = {}
        self.fallback_samples_by_minute: Dict[int, List[float]] = {}
        self.global_samples: List[float] = []
        self.train_error_by_key: Dict[str, float] = {}
        self.fallback_error_by_minute: Dict[int, float] = {}
        self.global_train_error = 0.0

    def fit(self, rows: Iterable[Mapping[str, Any]], power_key: str = "household_power_kw") -> "TimeSlotBaselineModel":
        self.samples_by_key.clear()
        self.fallback_samples_by_minute.clear()
        self.global_samples.clear()
        for row in rows:
            timestamp = _parse_timestamp(row["timestamp"])
            power_kw = _to_float(row.get(power_key, 0.0))
            key = self.key_for_timestamp(timestamp)
            minute = timestamp.hour * 60 + timestamp.minute
            self.samples_by_key.setdefault(key, []).append(power_kw)
            self.fallback_samples_by_minute.setdefault(minute, []).append(power_kw)
            self.global_samples.append(power_kw)
        self.train_error_by_key = {
            key: _leave_one_out_mae(samples) for key, samples in self.samples_by_key.items()
        }
        self.fallback_error_by_minute = {
            minute: _leave_one_out_mae(samples) for minute, samples in self.fallback_samples_by_minute.items()
        }
        self.global_train_error = _leave_one_out_mae(self.global_samples)
        return self

    def predict(self, timestamp: datetime | str) -> BaselinePrediction:
        ts = _parse_timestamp(timestamp)
        key = self.key_for_timestamp(ts)
        samples = self.samples_by_key.get(key)
        required = self.required_samples
        train_error = self.train_error_by_key.get(key, self.global_train_error)
        if not samples:
            minute = ts.hour * 60 + ts.minute
            key = f"all_days@{minute:04d}"
            samples = self.fallback_samples_by_minute.get(minute, self.global_samples)
            required = self.fallback_required_samples
            train_error = self.fallback_error_by_minute.get(minute, self.global_train_error)
        return _prediction_from_samples(key, samples or [0.0], required, train_error)

    @staticmethod
    def key_for_timestamp(timestamp: datetime) -> str:
        day_type = "weekend" if timestamp.weekday() >= 5 else "weekday"
        minute = timestamp.hour * 60 + timestamp.minute
        return f"{day_type}@{minute:04d}"


class DeviceAwareBaselineModel:
    """Train one time-slot baseline model per power column."""

    def __init__(self, power_keys: Sequence[str], required_samples: int = 7):
        self.power_keys = list(power_keys)
        self.models = {key: TimeSlotBaselineModel(required_samples=required_samples) for key in self.power_keys}

    def fit(self, rows: Iterable[Mapping[str, Any]]) -> "DeviceAwareBaselineModel":
        materialized = list(rows)
        for key, model in self.models.items():
            model.fit(materialized, power_key=key)
        return self

    def predict(self, timestamp: datetime | str) -> Dict[str, BaselinePrediction]:
        return {key: model.predict(timestamp) for key, model in self.models.items()}


def evaluate_predictions(
    rows: Iterable[Mapping[str, Any]],
    model: TimeSlotBaselineModel,
    power_key: str = "household_power_kw",
) -> Dict[str, Any]:
    evaluated: List[Dict[str, Any]] = []
    errors: List[float] = []
    squared_errors: List[float] = []
    ape_values: List[float] = []
    coverage_flags: List[int] = []
    confidences: List[float] = []
    for row in rows:
        timestamp = _parse_timestamp(row["timestamp"])
        actual = _to_float(row.get(power_key, 0.0))
        pred = model.predict(timestamp)
        error = actual - pred.p_base_mean_kw
        abs_error = abs(error)
        ape = abs_error / max(0.2, abs(actual))
        covered = int(pred.p_base_p10_kw <= actual <= pred.p_base_p90_kw)
        record = dict(row)
        record.update(pred.as_dict())
        record.update(
            {
                "actual_kw": round(actual, 6),
                "prediction_error_kw": round(error, 6),
                "absolute_error_kw": round(abs_error, 6),
                "absolute_percentage_error": round(ape, 6),
                "covered_by_p10_p90": covered,
            }
        )
        evaluated.append(record)
        errors.append(abs_error)
        squared_errors.append(error * error)
        ape_values.append(ape)
        coverage_flags.append(covered)
        confidences.append(pred.baseline_confidence)
    count = len(evaluated)
    high_confidence = [row for row in evaluated if _to_float(row.get("baseline_confidence", 0.0)) >= 0.7]
    high_conf_errors = [_to_float(row.get("absolute_error_kw", 0.0)) for row in high_confidence]
    return {
        "rows": evaluated,
        "summary": {
            "rows": count,
            "mae_kw": sum(errors) / count if count else 0.0,
            "rmse_kw": sqrt(sum(squared_errors) / count) if count else 0.0,
            "mape": sum(ape_values) / count if count else 0.0,
            "p10_p90_coverage": sum(coverage_flags) / count if count else 0.0,
            "mean_baseline_confidence": sum(confidences) / count if count else 0.0,
            "high_confidence_rows": len(high_confidence),
            "high_confidence_mae_kw": sum(high_conf_errors) / len(high_conf_errors) if high_conf_errors else 0.0,
        },
    }


def _prediction_from_samples(key: str, samples: Sequence[float], required_samples: int, train_error_mae: float) -> BaselinePrediction:
    values = sorted(float(v) for v in samples)
    n = len(values)
    avg = mean(values)
    std = pstdev(values) if n > 1 else 0.0
    sample_score = min(1.0, n / max(1, required_samples))
    cv = std / max(abs(avg), 0.2)
    stability_score = 1.0 / (1.0 + cv)
    normalized_error = train_error_mae / max(abs(avg), 0.2)
    recent_error_score = max(0.0, 1.0 - min(1.0, normalized_error))
    confidence = sample_score * stability_score * recent_error_score
    return BaselinePrediction(
        key=key,
        n_samples=n,
        p_base_mean_kw=avg,
        p_base_p05_kw=_quantile(values, 0.05),
        p_base_p10_kw=_quantile(values, 0.10),
        p_base_p30_kw=_quantile(values, 0.30),
        p_base_p50_kw=_quantile(values, 0.50),
        p_base_p70_kw=_quantile(values, 0.70),
        p_base_p90_kw=_quantile(values, 0.90),
        p_base_p95_kw=_quantile(values, 0.95),
        std_kw=std,
        baseline_confidence=confidence,
        sample_score=sample_score,
        stability_score=stability_score,
        recent_error_score=recent_error_score,
    )


def _leave_one_out_mae(samples: Sequence[float]) -> float:
    values = [float(v) for v in samples]
    n = len(values)
    if n <= 1:
        return 0.0
    total = sum(values)
    errors = []
    for value in values:
        pred = (total - value) / (n - 1)
        errors.append(abs(value - pred))
    return sum(errors) / len(errors)


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


def _parse_timestamp(value: datetime | str) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _to_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


class WeatherAdjustedTimeSlotBaselineModel:
    """Time-slot baseline with one global outdoor-temperature correction.

    Prediction formula:
        P_base = slot_mean_power + beta * (outdoor_temp - slot_mean_outdoor_temp)

    The quantile band is shifted by the same weather adjustment. This keeps the
    model transparent while handling adjacent-week temperature drift.
    """

    def __init__(
        self,
        required_samples: int = 7,
        power_key: str = "household_power_kw",
        weather_key: str = "outdoor_temperature_c",
    ):
        self.required_samples = required_samples
        self.power_key = power_key
        self.weather_key = weather_key
        self.slot_model = TimeSlotBaselineModel(required_samples=required_samples)
        self.weather_samples_by_key: Dict[str, List[float]] = {}
        self.global_weather_samples: List[float] = []
        self.beta_kw_per_c = 0.0

    def fit(self, rows: Iterable[Mapping[str, Any]]) -> "WeatherAdjustedTimeSlotBaselineModel":
        materialized = list(rows)
        self.slot_model.fit(materialized, power_key=self.power_key)
        self.weather_samples_by_key.clear()
        self.global_weather_samples.clear()
        for row in materialized:
            timestamp = _parse_timestamp(row["timestamp"])
            key = self.slot_model.key_for_timestamp(timestamp)
            outdoor = _to_float(row.get(self.weather_key, 0.0))
            self.weather_samples_by_key.setdefault(key, []).append(outdoor)
            self.global_weather_samples.append(outdoor)

        numerator = 0.0
        denominator = 0.0
        for row in materialized:
            timestamp = _parse_timestamp(row["timestamp"])
            key = self.slot_model.key_for_timestamp(timestamp)
            base_pred = self.slot_model.predict(timestamp)
            outdoor = _to_float(row.get(self.weather_key, 0.0))
            mean_outdoor = self._mean_outdoor(key)
            dx = outdoor - mean_outdoor
            residual = _to_float(row.get(self.power_key, 0.0)) - base_pred.p_base_mean_kw
            numerator += dx * residual
            denominator += dx * dx
        self.beta_kw_per_c = numerator / denominator if denominator > EPS else 0.0
        return self

    def predict(self, timestamp: datetime | str, outdoor_temperature_c: float | None = None) -> BaselinePrediction:
        ts = _parse_timestamp(timestamp)
        base = self.slot_model.predict(ts)
        if outdoor_temperature_c is None:
            adjustment = 0.0
            weather_score = 1.0
        else:
            mean_outdoor = self._mean_outdoor(base.key)
            delta_c = float(outdoor_temperature_c) - mean_outdoor
            adjustment = self.beta_kw_per_c * delta_c
            weather_score = 1.0 / (1.0 + abs(delta_c) / 5.0)
        return BaselinePrediction(
            key=f"weather_adjusted:{base.key}",
            n_samples=base.n_samples,
            p_base_mean_kw=max(0.0, base.p_base_mean_kw + adjustment),
            p_base_p05_kw=max(0.0, base.p_base_p05_kw + adjustment),
            p_base_p10_kw=max(0.0, base.p_base_p10_kw + adjustment),
            p_base_p30_kw=max(0.0, base.p_base_p30_kw + adjustment),
            p_base_p50_kw=max(0.0, base.p_base_p50_kw + adjustment),
            p_base_p70_kw=max(0.0, base.p_base_p70_kw + adjustment),
            p_base_p90_kw=max(0.0, base.p_base_p90_kw + adjustment),
            p_base_p95_kw=max(0.0, base.p_base_p95_kw + adjustment),
            std_kw=base.std_kw,
            baseline_confidence=base.baseline_confidence * weather_score,
            sample_score=base.sample_score,
            stability_score=base.stability_score,
            recent_error_score=base.recent_error_score * weather_score,
        )

    def _mean_outdoor(self, key: str) -> float:
        samples = self.weather_samples_by_key.get(key)
        if not samples and key.startswith("weather_adjusted:"):
            samples = self.weather_samples_by_key.get(key.split(":", 1)[1])
        if samples:
            return mean(samples)
        if self.global_weather_samples:
            return mean(self.global_weather_samples)
        return 0.0


def evaluate_weather_adjusted_predictions(
    rows: Iterable[Mapping[str, Any]],
    model: WeatherAdjustedTimeSlotBaselineModel,
    power_key: str = "household_power_kw",
    weather_key: str = "outdoor_temperature_c",
) -> Dict[str, Any]:
    evaluated: List[Dict[str, Any]] = []
    errors: List[float] = []
    squared_errors: List[float] = []
    ape_values: List[float] = []
    coverage_flags: List[int] = []
    confidences: List[float] = []
    for row in rows:
        timestamp = _parse_timestamp(row["timestamp"])
        actual = _to_float(row.get(power_key, 0.0))
        outdoor = _to_float(row.get(weather_key, 0.0))
        pred = model.predict(timestamp, outdoor_temperature_c=outdoor)
        error = actual - pred.p_base_mean_kw
        abs_error = abs(error)
        ape = abs_error / max(0.2, abs(actual))
        covered = int(pred.p_base_p10_kw <= actual <= pred.p_base_p90_kw)
        record = dict(row)
        record.update(pred.as_dict())
        record.update(
            {
                "actual_kw": round(actual, 6),
                "prediction_error_kw": round(error, 6),
                "absolute_error_kw": round(abs_error, 6),
                "absolute_percentage_error": round(ape, 6),
                "covered_by_p10_p90": covered,
                "weather_beta_kw_per_c": round(model.beta_kw_per_c, 6),
            }
        )
        evaluated.append(record)
        errors.append(abs_error)
        squared_errors.append(error * error)
        ape_values.append(ape)
        coverage_flags.append(covered)
        confidences.append(pred.baseline_confidence)
    count = len(evaluated)
    high_confidence = [row for row in evaluated if _to_float(row.get("baseline_confidence", 0.0)) >= 0.7]
    high_conf_errors = [_to_float(row.get("absolute_error_kw", 0.0)) for row in high_confidence]
    return {
        "rows": evaluated,
        "summary": {
            "rows": count,
            "mae_kw": sum(errors) / count if count else 0.0,
            "rmse_kw": sqrt(sum(squared_errors) / count) if count else 0.0,
            "mape": sum(ape_values) / count if count else 0.0,
            "p10_p90_coverage": sum(coverage_flags) / count if count else 0.0,
            "mean_baseline_confidence": sum(confidences) / count if count else 0.0,
            "high_confidence_rows": len(high_confidence),
            "high_confidence_mae_kw": sum(high_conf_errors) / len(high_conf_errors) if high_conf_errors else 0.0,
            "weather_beta_kw_per_c": model.beta_kw_per_c,
        },
    }
