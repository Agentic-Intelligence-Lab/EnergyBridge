#!/usr/bin/env python3
"""IS weight training for June(source) -> July(target) density ratio estimation.

Supports Germany and Tianjin via USE_CITY switch.
Scheme 5 only (pre_event_load_kw + temp_pre_1h + temp_vpp, 3D hourly).

Usage:
  Set USE_CITY = "Germany" or "Tianjin" below, then run:
    python importance_sampling/IS_weather_and_preload.py
"""
import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.special import expit

# ======================== CONFIGURATION ========================
BASE_DIR = Path(__file__).parent

USE_CITY = "Germany"   # switch: "Germany" or "Tianjin"

if USE_CITY == "Germany":
    JUNE_DATA = BASE_DIR / "data" / "june_2025_germany_with_preload.json"
    JULY_DATA = BASE_DIR / "data" / "july_2025_germany_with_preload.json"
    WEATHER_CSV = BASE_DIR.parent / "experiments" / "real_data" / "germany_2025_weather.csv"
else:  # Tianjin
    JUNE_DATA = BASE_DIR / "data" / "june_2025_tianjin_with_preload.json"
    JULY_DATA = BASE_DIR / "data" / "july_2025_tianjin_with_preload.json"
    WEATHER_CSV = BASE_DIR.parent / "experiments" / "weather" / "tianjin.csv"

OUTPUT_DIR = BASE_DIR / "IS_result"

VPP_HOUR = 18   # VPP start hour
PRE_HOUR = 17   # 1 hour before VPP

WEATHER_FEATURE_NAMES = ["t_mean_day", "t_max_day", "rh_mean_day", "ghi_sum_day", "cloud_cover_mean_day"]
# ==============================================================


def load_json_data(path):
    with open(path, "r") as f:
        return json.load(f)


def filter_city(records, city):
    return [r for r in records if r.get("city") == city]


def clean_records(records):
    cleaned = []
    for r in records:
        if r.get("pre_event_load_kw") is None:
            continue
        if r.get("realized_delivery_kwh") is None:
            continue
        cleaned.append(r)
    return cleaned


# ── Weather loaders ──────────────────────────────────────────────────────────

def load_weather_data(csv_path):
    """Germany daily-aggregated weather: date col + temperature_2m/relative_humidity_2m/
    shortwave_radiation/cloud_cover columns."""
    by_day = defaultdict(list)
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            day_key = row["date"][:10]
            temp = float(row["temperature_2m"])
            rh = float(row["relative_humidity_2m"])
            ghi = float(row["shortwave_radiation"])
            cloud = float(row["cloud_cover"])
            by_day[day_key].append((temp, rh, ghi, cloud))

    out = {}
    for day_key, rows in by_day.items():
        temps = [r[0] for r in rows]
        rhs = [r[1] for r in rows]
        ghis = [r[2] for r in rows]
        clouds = [r[3] for r in rows]
        out[day_key] = {
            "t_mean_day": np.mean(temps),
            "t_max_day": np.max(temps),
            "rh_mean_day": np.mean(rhs),
            "ghi_sum_day": np.sum(ghis),
            "cloud_cover_mean_day": np.mean(clouds),
        }
    return out


def load_hourly_temperature(csv_path):
    """Germany hourly temp: date col format "YYYY-MM-DD HH:MM:SS". Returns {date: {hour: temp}}."""
    result = defaultdict(dict)
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            date_str = row["date"]
            if " " in date_str:
                date_part, time_part = date_str.split(" ")
                hour = int(time_part.split(":")[0])
            else:
                continue
            result[date_part][hour] = float(row["temperature_2m"])
    return result


def load_hourly_temperature_tianjin(csv_path):
    """Open-Meteo hourly export: 2 metadata lines then header starting with "time,".
    time format "YYYY-MM-DDTHH:MM", column name has unit suffix e.g. "temperature_2m (°C)".
    Returns {date: {hour: temp}}."""
    with open(csv_path, newline="", encoding="utf-8") as f:
        lines = f.readlines()
    header_idx = next(i for i, l in enumerate(lines) if l.startswith("time,"))
    reader = csv.DictReader(lines[header_idx:])
    temp_key = next(k for k in reader.fieldnames if k.startswith("temperature_2m"))

    result = defaultdict(dict)
    for row in reader:
        ts = row["time"]
        if "T" not in ts:
            continue
        date_part, time_part = ts.split("T")
        hour = int(time_part.split(":")[0])
        result[date_part][hour] = float(row[temp_key])
    return result


def attach_weather(records, weather_lookup):
    for rec in records:
        date_key = rec.get("start_date", "")[:10]
        rec["weather"] = weather_lookup.get(date_key, {})


def attach_hourly_temp(records, hourly_temp, pre_hour=PRE_HOUR, vpp_hour=VPP_HOUR):
    for rec in records:
        date_key = rec.get("start_date", "")[:10]
        rec["temp_pre_hour"] = hourly_temp.get(date_key, {}).get(pre_hour, np.nan)
        rec["temp_vpp_hour"] = hourly_temp.get(date_key, {}).get(vpp_hour, np.nan)


# ── Feature construction ─────────────────────────────────────────────────────

def build_feature_matrix(records, use_weather=True, weather_keys=None, use_hourly_temp=False):
    """Build feature matrix.
    use_weather=True: daily weather features; use_hourly_temp=True: pre-1h + VPP-window temp."""
    if weather_keys is None:
        weather_keys = WEATHER_FEATURE_NAMES

    features = []
    feature_names = []

    pre_load = [r.get("pre_event_load_kw", 0) for r in records]
    features.append(pre_load)
    feature_names.append("pre_event_load_kw")

    if use_weather:
        for key in weather_keys:
            vals = [r.get("weather", {}).get(key, 0) for r in records]
            features.append(vals)
            feature_names.append(key)

    if use_hourly_temp:
        vals_pre = [r.get("temp_pre_hour", np.nan) for r in records]
        if any(np.isnan(v) for v in vals_pre):
            print("Warning: missing pre-1h temperature values, filling with 0")
            vals_pre = [0 if np.isnan(v) else v for v in vals_pre]
        features.append(vals_pre)
        feature_names.append("temp_pre_1h")

        vals_vpp = [r.get("temp_vpp_hour", np.nan) for r in records]
        if any(np.isnan(v) for v in vals_vpp):
            print("Warning: missing VPP window temperature values, filling with 0")
            vals_vpp = [0 if np.isnan(v) else v for v in vals_vpp]
        features.append(vals_vpp)
        feature_names.append("temp_vpp")

    X = np.array(features).T
    return X, feature_names


# ── Density ratio estimator ──────────────────────────────────────────────────

class LogisticDensityRatio:
    def __init__(self, weight_decay=0.05, lr=0.05, steps=1000, seed=0):
        self.weight_decay = weight_decay
        self.lr = lr
        self.steps = steps
        self.seed = seed
        self._mean = None
        self._std = None
        self._w = None
        self._b = 0.0
        self._n_source = 0
        self._n_target = 0

    def fit(self, X_source, X_target):
        X_source = np.asarray(X_source, dtype=np.float64)
        X_target = np.asarray(X_target, dtype=np.float64)
        self._n_source = X_source.shape[0]
        self._n_target = X_target.shape[0]

        combined = np.concatenate([X_source, X_target], axis=0)
        self._mean = combined.mean(axis=0)
        self._std = combined.std(axis=0)
        self._std[self._std < 1e-9] = 1.0

        Xs = (X_source - self._mean) / self._std
        Xt = (X_target - self._mean) / self._std
        X = np.concatenate([Xs, Xt], axis=0)
        y = np.concatenate([np.zeros(Xs.shape[0]), np.ones(Xt.shape[0])])

        n_samples, n_features = X.shape
        np.random.seed(self.seed)
        self._w = np.random.randn(n_features) * 0.01
        self._b = 0.0

        for _ in range(self.steps):
            logits = X @ self._w + self._b
            probs = expit(logits)
            grad_w = (X.T @ (probs - y)) / n_samples + self.weight_decay * self._w
            grad_b = np.mean(probs - y)
            self._w -= self.lr * grad_w
            self._b -= self.lr * grad_b
        return self

    def weights(self, X_query):
        X_query = np.asarray(X_query, dtype=np.float64)
        Xq = (X_query - self._mean) / self._std
        logits = Xq @ self._w + self._b
        p_target = expit(logits)
        p_target = np.clip(p_target, 1e-6, 1 - 1e-6)
        odds = p_target / (1 - p_target)
        return odds * (self._n_source / max(1, self._n_target))


# ── Metrics ──────────────────────────────────────────────────────────────────

def compute_ess(weights):
    w = np.asarray(weights, dtype=np.float64)
    if w.size == 0:
        return 0.0
    return float((w.sum() ** 2) / (np.square(w).sum() + 1e-12) / w.size)


def weighted_quantile(values, weights, q):
    values = np.asarray(values, dtype=np.float64)
    weights = np.asarray(weights, dtype=np.float64)
    if values.size == 0:
        return 0.0
    if values.size == 1:
        return float(values[0])
    order = np.argsort(values)
    v = values[order]
    w = weights[order]
    cum_w = np.cumsum(w) - 0.5 * w
    cum_w = cum_w / w.sum()
    q = max(0.0, min(1.0, q))
    if q <= cum_w[0]:
        return float(v[0])
    if q >= cum_w[-1]:
        return float(v[-1])
    idx = int(np.searchsorted(cum_w, q))
    lo, hi = idx - 1, idx
    frac = (q - cum_w[lo]) / (cum_w[hi] - cum_w[lo])
    return float(v[lo] * (1.0 - frac) + v[hi] * frac)


# ── Training ─────────────────────────────────────────────────────────────────

def train_and_evaluate(X_source, X_target, y_source, y_target_true, label):
    print(f"\n{'=' * 70}")
    print(f"Training {label}")
    print(f"{'=' * 70}")

    est = LogisticDensityRatio(weight_decay=0.05, lr=0.05, steps=1000, seed=0)
    est.fit(X_source, X_target)
    weights = est.weights(X_source)

    ess = compute_ess(weights)
    pred_p50 = weighted_quantile(y_source, weights, 0.5)
    pred_p25 = weighted_quantile(y_source, weights, 0.25)
    pred_p75 = weighted_quantile(y_source, weights, 0.75)

    baseline_pred = np.mean(y_source)
    mae_is = np.mean(np.abs(y_target_true - pred_p50))
    mae_baseline = np.mean(np.abs(y_target_true - baseline_pred))
    rmse_is = np.sqrt(np.mean((y_target_true - pred_p50) ** 2))
    rmse_baseline = np.sqrt(np.mean((y_target_true - baseline_pred) ** 2))

    print(f"   ESS: {ess:.4f}")
    print(f"   Weight range: [{weights.min():.4f}, {weights.max():.4f}]")
    print(f"   Weight mean: {weights.mean():.4f}")
    print(f"   P50 (IS):   {pred_p50:.4f} kWh")
    print(f"   Baseline (mean): {baseline_pred:.4f} kWh")
    print(f"   True mean:   {np.mean(y_target_true):.4f} kWh")
    print(f"   MAE (IS):    {mae_is:.4f} kWh")
    print(f"   MAE (baseline): {mae_baseline:.4f} kWh")
    print(f"   MAE improvement: {(mae_baseline - mae_is):.4f} kWh ({(mae_baseline - mae_is) / mae_baseline * 100:.2f}%)")

    return {
        "label": label,
        "ess": ess,
        "weights": weights,
        "pred_p50": pred_p50,
        "pred_p25": pred_p25,
        "pred_p75": pred_p75,
        "mae_is": mae_is,
        "mae_baseline": mae_baseline,
        "mae_improvement_pct": (mae_baseline - mae_is) / mae_baseline * 100,
        "rmse_is": rmse_is,
        "rmse_baseline": rmse_baseline,
        "est": est,
    }


def save_weight_package(result, records, feature_names, output_path, city):
    weights = result["weights"]
    weight_package = {
        "case": f"{city} June(source) -> July(target) | {result['label']}",
        "source_data": str(JUNE_DATA),
        "target_data": str(JULY_DATA),
        "weather_source": str(WEATHER_CSV),
        "estimator": "LogisticDensityRatio(weight_decay=0.05, lr=0.05, steps=1000, seed=0)",
        "feature_names": feature_names,
        "n_source": len(records),
        "n_target": int(result["est"]._n_target),
        "ess_fraction": round(result["ess"], 4),
        "weight_min": round(float(weights.min()), 4),
        "weight_max": round(float(weights.max()), 4),
        "weight_mean": round(float(weights.mean()), 4),
        "per_event_weights": [
            {
                "memory_event_id": r["memory_event_id"],
                "date": r["start_date"],
                "household_id": r["household_id"],
                "pre_event_load_kw": r["pre_event_load_kw"],
                "realized_delivery_kwh": r["realized_delivery_kwh"],
                "raw_weight": round(float(w), 4),
            }
            for r, w in zip(records, weights)
        ],
        "validation": {
            "predicted_p50_kwh": round(float(result["pred_p50"]), 4),
            "predicted_p25_kwh": round(float(result["pred_p25"]), 4),
            "predicted_p75_kwh": round(float(result["pred_p75"]), 4),
            "mae_is_kwh": round(float(result["mae_is"]), 4),
            "mae_baseline_kwh": round(float(result["mae_baseline"]), 4),
            "mae_improvement_pct": round(float(result["mae_improvement_pct"]), 2),
            "rmse_is_kwh": round(float(result["rmse_is"]), 4),
            "rmse_baseline_kwh": round(float(result["rmse_baseline"]), 4),
            "rmse_improvement_pct": round(
                (result["rmse_baseline"] - result["rmse_is"]) / result["rmse_baseline"] * 100, 2
            ),
        },
    }
    with open(output_path, "w") as f:
        json.dump(weight_package, f, indent=2, ensure_ascii=False)
    print(f"\nWeight package saved: {output_path}")
    return output_path


def main():
    print("=" * 70)
    print(f"IS Weight Training ({USE_CITY}): Pre-event Load + Hourly Temperature")
    print("=" * 70)

    june_raw = load_json_data(JUNE_DATA)
    july_raw = load_json_data(JULY_DATA)
    june_records = clean_records(filter_city(june_raw, USE_CITY))
    july_records = clean_records(filter_city(july_raw, USE_CITY))
    print(f"\nJune {USE_CITY} data: {len(june_records)} records")
    print(f"July {USE_CITY} data: {len(july_records)} records")

    if USE_CITY == "Germany":
        hourly_temp = load_hourly_temperature(WEATHER_CSV)
    else:  # Tianjin
        hourly_temp = load_hourly_temperature_tianjin(WEATHER_CSV)

    attach_hourly_temp(june_records, hourly_temp)
    attach_hourly_temp(july_records, hourly_temp)

    y_source = np.array([r["realized_delivery_kwh"] for r in june_records])
    y_target = np.array([r["realized_delivery_kwh"] for r in july_records])

    X_src, fnames = build_feature_matrix(june_records, use_weather=False, use_hourly_temp=True)
    X_tgt, _ = build_feature_matrix(july_records, use_weather=False, use_hourly_temp=True)

    label = "Scheme 5: Pre-event load + pre-1h temp + VPP-window temp (3D, hourly)"
    result = train_and_evaluate(X_src, X_tgt, y_source, y_target, label)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    city_lower = USE_CITY.lower()
    out_path = OUTPUT_DIR / f"weights_package_{city_lower}_6to7.json"
    save_weight_package(result, june_records, fnames, out_path, USE_CITY)

    print("\n" + "=" * 70)
    print("Feature importance")
    print("=" * 70)
    est = result["est"]
    for name, coef in sorted(zip(fnames, np.abs(est._w)), key=lambda x: x[1], reverse=True):
        print(f"   {name}: {coef:.4f}")


if __name__ == "__main__":
    main()
