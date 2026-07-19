import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np
from scipy.special import expit

# ======================== CONFIGURATION ========================
BASE_DIR = Path(__file__).parent
JUNE_DATA = BASE_DIR / "data" / "clean_records_285_with_preload.json"
JULY_DATA = BASE_DIR / "data" / "july_events_31days.json"
WEATHER_CSV = "../experiments/real_data/germany_2025_weather.csv"
OUTPUT_DIR = BASE_DIR / "IS_result"

USE_CITY = "Germany"
VPP_HOUR = 18          # VPP start hour
PRE_HOUR = 17          # 1 hour before VPP
WEATHER_FEATURE_NAMES = ["t_mean_day", "t_max_day", "rh_mean_day", "ghi_sum_day", "cloud_cover_mean_day"]
# ==============================================================


# ============================================================
# 1. Data loading and cleaning
# ============================================================

def load_json_data(path):
    with open(path, "r") as f:
        return json.load(f)


def filter_city(records, city):
    return [r for r in records if r.get("city") == city]


def clean_records(records):
    """Remove records with missing key fields."""
    cleaned = []
    for r in records:
        if r.get("pre_event_load_kw") is None:
            continue
        if r.get("realized_delivery_kwh") is None:
            continue
        cleaned.append(r)
    return cleaned


# ============================================================
# 2. Weather data loading (daily + hourly)
# ============================================================

def load_weather_data(csv_path):
    """
    Load German weather CSV and return daily aggregated features.
    Assumes CSV has columns: date, temperature_2m, relative_humidity_2m,
    shortwave_radiation, cloud_cover.
    """
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
    """
    Load hourly temperature and return {date: {hour: temp}}.
    The CSV date column is assumed to be "YYYY-MM-DD HH:MM:SS".
    """
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
            temp = float(row["temperature_2m"])
            result[date_part][hour] = temp
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


# ============================================================
# 3. Feature construction
# ============================================================

def build_feature_matrix(records, use_weather=True, weather_keys=None, use_hourly_temp=False):
    """
    Build the feature matrix.

    - use_weather=True: include daily weather features
    - use_hourly_temp=True: include hourly temperature (pre-1h + VPP window)
    """
    if weather_keys is None:
        weather_keys = WEATHER_FEATURE_NAMES

    features = []
    feature_names = []

    # 1. Pre-event load (always included)
    pre_load = [r.get("pre_event_load_kw", 0) for r in records]
    features.append(pre_load)
    feature_names.append("pre_event_load_kw")

    # 2. Daily weather
    if use_weather:
        for key in weather_keys:
            vals = [r.get("weather", {}).get(key, 0) for r in records]
            features.append(vals)
            feature_names.append(key)

    # 3. Hourly temperature
    if use_hourly_temp:
        # Pre-1h temperature
        vals_pre = [r.get("temp_pre_hour", np.nan) for r in records]
        if any(np.isnan(v) for v in vals_pre):
            print("Warning: missing pre-1h temperature values, filling with 0")
            vals_pre = [0 if np.isnan(v) else v for v in vals_pre]
        features.append(vals_pre)
        feature_names.append("temp_pre_1h")

        # VPP window temperature
        vals_vpp = [r.get("temp_vpp_hour", np.nan) for r in records]
        if any(np.isnan(v) for v in vals_vpp):
            print("Warning: missing VPP window temperature values, filling with 0")
            vals_vpp = [0 if np.isnan(v) else v for v in vals_vpp]
        features.append(vals_vpp)
        feature_names.append("temp_vpp")

    X = np.array(features).T  # (n_samples, n_features)
    return X, feature_names


# ============================================================
# 4. Density ratio estimator
# ============================================================

class LogisticDensityRatio:
    def __init__(self, weight_decay=0.05, lr=0.05, steps=500, seed=0):
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


# ============================================================
# 5. Evaluation metrics
# ============================================================

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


# ============================================================
# 6. Training and evaluation
# ============================================================

def train_and_evaluate(X_source, X_target, y_source, y_target_true, label, scheme_id):
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
    print(f"   MAE improvement: {(mae_baseline - mae_is):.4f} kWh")

    return {
        "scheme_id": scheme_id,
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
        "feature_names": None,  # will be filled by caller
    }


def save_weight_package(result, records, feature_names, output_path):
    """Save weight package for a given scheme result."""
    weights = result["weights"]
    weight_package = {
        "case": f"Germany June(source) -> July(target) | {result['label']}",
        "source_data": str(JUNE_DATA),
        "target_data": str(JULY_DATA),
        "estimator": "LogisticDensityRatio(weight_decay=0.05, lr=0.05, steps=500, seed=0)",
        "feature_names": feature_names,
        "n_source": len(records),
        "n_target": len(records),  # placeholder, actual target count from validation
        "ess_fraction": round(result["ess"], 4),
        "weight_min": round(weights.min(), 4),
        "weight_max": round(weights.max(), 4),
        "weight_mean": round(weights.mean(), 4),
        "per_event_weights": [
            {
                "memory_event_id": r["memory_event_id"],
                "date": r["start_date"],
                "household_id": r["household_id"],
                "pre_event_load_kw": r["pre_event_load_kw"],
                "realized_delivery_kwh": r["realized_delivery_kwh"],
                "raw_weight": round(w, 4),
            }
            for r, w in zip(records, weights)
        ],
        "validation": {
            "predicted_p50_kwh": round(result["pred_p50"], 4),
            "predicted_p25_kwh": round(result["pred_p25"], 4),
            "predicted_p75_kwh": round(result["pred_p75"], 4),
            "mae_is_kwh": round(result["mae_is"], 4),
            "mae_baseline_kwh": round(result["mae_baseline"], 4),
            "mae_improvement_pct": round(result["mae_improvement_pct"], 2),
            "rmse_is_kwh": round(result["rmse_is"], 4),
            "rmse_baseline_kwh": round(result["rmse_baseline"], 4),
            "rmse_improvement_pct": round(
                (result["rmse_baseline"] - result["rmse_is"]) / result["rmse_baseline"] * 100, 2
            ),
        }
    }

    with open(output_path, "w") as f:
        json.dump(weight_package, f, indent=2, ensure_ascii=False)
    print(f"\nWeight package saved: {output_path}")
    return output_path


# ============================================================
# 7. Main workflow
# ============================================================

def main():
    print("=" * 70)
    print("IS Weight Training: Pre-event Load + Temperature Features")
    print("(Daily / Hourly) - Auto-select Best Scheme")
    print("=" * 70)

    # 1. Load data
    june_raw = load_json_data(JUNE_DATA)
    july_raw = load_json_data(JULY_DATA)
    june_records = clean_records(filter_city(june_raw, USE_CITY))
    july_records = clean_records(filter_city(july_raw, USE_CITY))
    print(f"\nJune {USE_CITY} data: {len(june_records)} records")
    print(f"July {USE_CITY} data: {len(july_records)} records")

    # 2. Load weather data
    weather_lookup = load_weather_data(WEATHER_CSV)
    hourly_temp = load_hourly_temperature(WEATHER_CSV)

    # 3. Attach features
    attach_weather(june_records, weather_lookup)
    attach_weather(july_records, weather_lookup)
    attach_hourly_temp(june_records, hourly_temp)
    attach_hourly_temp(july_records, hourly_temp)

    # 4. Prepare target variables
    y_source = np.array([r["realized_delivery_kwh"] for r in june_records])
    y_target = np.array([r["realized_delivery_kwh"] for r in july_records])

    results = {}

    # Define schemes to evaluate
    schemes = [
        # {
        #     "id": "pre_load",
        #     "label": "Scheme 1: Pure pre-event load (1D)",
        #     "use_weather": False,
        #     "weather_keys": None,
        #     "use_hourly_temp": False,
        # },
        # {
        #     "id": "weather",
        #     "label": "Scheme 2: Pure daily weather (5D)",
        #     "use_weather": True,
        #     "weather_keys": WEATHER_FEATURE_NAMES,
        #     "use_hourly_temp": False,
        # },
        # {
        #     "id": "combined",
        #     "label": "Scheme 3: Daily weather + pre-event load (6D)",
        #     "use_weather": True,
        #     "weather_keys": WEATHER_FEATURE_NAMES,
        #     "use_hourly_temp": False,
        # },
        # {
        #     "id": "combined_partial",
        #     "label": "Scheme 4: Pre-event load + t_max + t_mean (3D)",
        #     "use_weather": True,
        #     "weather_keys": ["t_max_day", "t_mean_day"],
        #     "use_hourly_temp": False,
        # },
        {
            "id": "hourly_temp",
            "label": "Scheme 5: Pre-event load + pre-1h temp + VPP-window temp (3D, hourly)",
            "use_weather": False,
            "weather_keys": None,
            "use_hourly_temp": True,
        },
    ]

    for scheme in schemes:
        X_src, fnames = build_feature_matrix(
            june_records,
            use_weather=scheme["use_weather"],
            weather_keys=scheme["weather_keys"],
            use_hourly_temp=scheme["use_hourly_temp"],
        )
        X_tgt, _ = build_feature_matrix(
            july_records,
            use_weather=scheme["use_weather"],
            weather_keys=scheme["weather_keys"],
            use_hourly_temp=scheme["use_hourly_temp"],
        )

        result = train_and_evaluate(X_src, X_tgt, y_source, y_target, scheme["label"], scheme["id"])
        result["feature_names"] = fnames
        results[scheme["id"]] = result

    # Summary
    print("\n" + "=" * 70)
    print("Summary Comparison")
    print("=" * 70)
    print(f"\n{'Scheme':<45} {'ESS':>10} {'MAE (IS)':>12} {'MAE (baseline)':>12} {'Improvement':>10}")
    print("-" * 91)
    for key, r in results.items():
        label = r["label"].split(":")[0]
        print(
            f"{label:<45} {r['ess']:>10.4f} {r['mae_is']:>12.4f} {r['mae_baseline']:>12.4f} {r['mae_improvement_pct']:>9.2f}%"
        )

    # Best scheme (by improvement percentage)
    best_key = max(results, key=lambda k: results[k]["mae_improvement_pct"])
    best = results[best_key]
    print(f"\nBest scheme: {best['label']}")
    print(f"   ESS: {best['ess']:.4f}, MAE improvement: {best['mae_improvement_pct']:.2f}%")

    # Save best scheme's weight package
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    out_path = OUTPUT_DIR / f"weights_package_germany_6to7.json"

    # Rebuild feature matrix for best scheme to get source records with correct features
    # Find the scheme config that produced this result
    best_scheme_config = next((s for s in schemes if s["id"] == best_key), None)
    if best_scheme_config:
        _, fnames = build_feature_matrix(
            june_records,
            use_weather=best_scheme_config["use_weather"],
            weather_keys=best_scheme_config["weather_keys"],
            use_hourly_temp=best_scheme_config["use_hourly_temp"],
        )
        best["feature_names"] = fnames

    save_weight_package(best, june_records, best["feature_names"], out_path)

    print("\n" + "=" * 70)
    print(f"Feature importance (Best Scheme: {best_key})")
    print("=" * 70)
    est = best["est"]
    for name, coef in sorted(zip(best["feature_names"], np.abs(est._w)), key=lambda x: x[1], reverse=True):
        print(f"   {name}: {coef:.4f}")

    # Also save a summary of all schemes
    summary_path = OUTPUT_DIR / "all_schemes_comparison.json"
    summary = {
        "best_scheme": best_key,
        "best_scheme_label": best["label"],
        "results": {
            key: {
                "label": r["label"],
                "ess": r["ess"],
                "mae_is": r["mae_is"],
                "mae_baseline": r["mae_baseline"],
                "mae_improvement_pct": r["mae_improvement_pct"],
                "feature_names": r.get("feature_names", []),
            }
            for key, r in results.items()
        }
    }
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, ensure_ascii=False)
    print(f"\nComparison summary saved: {summary_path}")


if __name__ == "__main__":
    main()