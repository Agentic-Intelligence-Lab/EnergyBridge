#!/usr/bin/env python3
"""Small, leakage-free study of richer importance-sampling weights.

The density-ratio models only see covariates available before the VPP event:
the one-hour pre-event load, forecast weather, calendar, and household ID.  July
realized delivery is used only after fitting to score event-level RAG reports.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from energybridge.quantification.dr_event_memory import estimate_event_capacity_from_memory  # noqa: E402


EPS = 1e-9


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _clean_city(records: list[dict[str, Any]], city: str) -> list[dict[str, Any]]:
    return [
        row
        for row in records
        if str(row.get("city") or "").lower() == city.lower()
        and row.get("pre_event_load_kw") is not None
        and row.get("realized_delivery_kwh") is not None
        and "|no_dr|" not in str(row.get("memory_event_id") or "")
    ]


def _weather_lookup(path: Path) -> tuple[dict[str, dict[int, dict[str, float]]], dict[str, dict[str, float]]]:
    hourly: dict[str, dict[int, dict[str, float]]] = defaultdict(dict)
    daily_rows: dict[str, list[dict[str, float]]] = defaultdict(list)
    lines = path.read_text(encoding="utf-8").splitlines()
    header_index = next(
        (index for index, line in enumerate(lines) if line.startswith("date,") or line.startswith("time,")),
        None,
    )
    if header_index is None:
        raise ValueError(f"weather CSV has no date/time header: {path}")
    reader = csv.DictReader(lines[header_index:])
    assert reader.fieldnames is not None

    def field(prefix: str) -> str:
        return next(name for name in reader.fieldnames or [] if name.startswith(prefix))

    time_key = "date" if "date" in reader.fieldnames else "time"
    temp_key = field("temperature_2m")
    rh_key = field("relative_humidity_2m")
    solar_key = field("shortwave_radiation")
    cloud_key = field("cloud_cover")
    wind_key = field("wind_speed_10m")
    for row in reader:
            timestamp = str(row[time_key])
            timestamp = timestamp.replace("T", " ")
            date, time = timestamp.split(" ", 1)
            hour = int(time.split(":", 1)[0])
            values = {
                "temp": float(row[temp_key]),
                "rh": float(row[rh_key]),
                "solar": float(row[solar_key]),
                "cloud": float(row[cloud_key]),
                "wind": float(row[wind_key]),
            }
            hourly[date][hour] = values
            daily_rows[date].append(values)
    daily: dict[str, dict[str, float]] = {}
    for date, rows in daily_rows.items():
        daily[date] = {
            "temp_mean": float(np.mean([r["temp"] for r in rows])),
            "temp_max": float(np.max([r["temp"] for r in rows])),
            "rh_mean": float(np.mean([r["rh"] for r in rows])),
            "solar_sum": float(np.sum([r["solar"] for r in rows])),
            "cloud_mean": float(np.mean([r["cloud"] for r in rows])),
        }
    return dict(hourly), daily


def _target_records_from_results(root: Path, city: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    pattern = f"*EnergyBridge_{city.lower()}_*/*benchmark_result.json"
    for path in sorted(root.glob(pattern)):
        result = _load(path)
        for event in result.get("vpp_event_log") or []:
            selected = (event.get("event_baseline_estimate") or {}).get("selected_baseline") or {}
            pre_event_load = selected.get("pre_event_avg_kw")
            realized = event.get("counterfactual_actual_shed_kwh")
            if realized is None:
                realized = event.get("actual_shed_kwh")
            if pre_event_load is None or realized is None:
                continue
            records.append(
                {
                    "memory_event_id": "target|" + path.parent.name,
                    "city": city,
                    "household_id": path.parent.name.split("_EnergyBridge_", 1)[0],
                    "start_date": str(result.get("start_date") or "")[:10],
                    "pre_event_load_kw": float(pre_event_load),
                    "realized_delivery_kwh": float(realized),
                }
            )
    return records


def _base_covariates(
    row: dict[str, Any],
    hourly: dict[str, dict[int, dict[str, float]]],
    daily: dict[str, dict[str, float]],
) -> dict[str, float]:
    date = str(row["start_date"])[:10]
    h17 = hourly[date][17]
    h18 = hourly[date][18]
    weekday = __import__("datetime").date.fromisoformat(date).weekday()
    angle = 2.0 * math.pi * weekday / 7.0
    return {
        "pre_event_load_kw": float(row["pre_event_load_kw"]),
        "temp_pre_1h": h17["temp"],
        "temp_vpp": h18["temp"],
        "rh_vpp": h18["rh"],
        "solar_pre_1h": h17["solar"],
        "cloud_vpp": h18["cloud"],
        "wind_vpp": h18["wind"],
        "temp_mean_day": daily[date]["temp_mean"],
        "temp_max_day": daily[date]["temp_max"],
        "rh_mean_day": daily[date]["rh_mean"],
        "solar_sum_day": daily[date]["solar_sum"],
        "cloud_mean_day": daily[date]["cloud_mean"],
        "weekday_sin": math.sin(angle),
        "weekday_cos": math.cos(angle),
    }


def _feature_matrix(
    records: list[dict[str, Any]],
    hourly: dict[str, dict[int, dict[str, float]]],
    daily: dict[str, dict[str, float]],
    scheme: str,
    households: list[str],
) -> tuple[np.ndarray, list[str]]:
    current = ["pre_event_load_kw", "temp_pre_1h", "temp_vpp"]
    weather = current + [
        "rh_vpp",
        "solar_pre_1h",
        "cloud_vpp",
        "wind_vpp",
        "temp_mean_day",
        "temp_max_day",
        "rh_mean_day",
        "solar_sum_day",
        "cloud_mean_day",
        "weekday_sin",
        "weekday_cos",
    ]
    rows = []
    names = current if scheme == "current" else weather
    for record in records:
        values = _base_covariates(record, hourly, daily)
        vector = [values[name] for name in names]
        if scheme == "nonlinear":
            continuous = [values[name] for name in weather[:12]]
            vector.extend(value * value for value in continuous)
            vector.extend(
                [
                    values["pre_event_load_kw"] * values["temp_vpp"],
                    values["temp_vpp"] * values["rh_vpp"],
                    values["solar_pre_1h"] * values["cloud_vpp"],
                ]
            )
        elif scheme == "household_shift":
            for household in households:
                active = 1.0 if record.get("household_id") == household else 0.0
                vector.extend(
                    [
                        active,
                        active * values["pre_event_load_kw"],
                        active * values["temp_pre_1h"],
                        active * values["temp_vpp"],
                    ]
                )
        rows.append(vector)
    if scheme == "nonlinear":
        names = weather + [f"{name}^2" for name in weather[:12]] + ["load*temp", "temp*rh", "solar*cloud"]
    elif scheme == "household_shift":
        names = weather + [f"{household}:{suffix}" for household in households for suffix in ("bias", "load", "temp17", "temp18")]
    return np.asarray(rows, dtype=np.float64), names


class LogisticDensityRatio:
    def __init__(self, *, weight_decay: float, steps: int = 2500, lr: float = 0.03) -> None:
        self.weight_decay = float(weight_decay)
        self.steps = int(steps)
        self.lr = float(lr)
        self.mean: np.ndarray | None = None
        self.std: np.ndarray | None = None
        self.coef: np.ndarray | None = None
        self.intercept = 0.0
        self.n_source = 0
        self.n_target = 0

    @staticmethod
    def _sigmoid(values: np.ndarray) -> np.ndarray:
        values = np.clip(values, -35.0, 35.0)
        return 1.0 / (1.0 + np.exp(-values))

    def fit(self, source: np.ndarray, target: np.ndarray) -> "LogisticDensityRatio":
        self.n_source, self.n_target = len(source), len(target)
        combined = np.vstack([source, target])
        self.mean = combined.mean(axis=0)
        self.std = combined.std(axis=0)
        self.std[self.std < EPS] = 1.0
        xs = (source - self.mean) / self.std
        xt = (target - self.mean) / self.std
        features = np.vstack([xs, xt])
        labels = np.concatenate([np.zeros(len(xs)), np.ones(len(xt))])
        self.coef = np.zeros(features.shape[1], dtype=np.float64)
        self.intercept = 0.0
        for _ in range(self.steps):
            probabilities = self._sigmoid(features @ self.coef + self.intercept)
            residual = probabilities - labels
            self.coef -= self.lr * ((features.T @ residual) / len(features) + self.weight_decay * self.coef)
            self.intercept -= self.lr * float(np.mean(residual))
        return self

    def weights(self, source: np.ndarray) -> np.ndarray:
        assert self.mean is not None and self.std is not None and self.coef is not None
        probability = self._sigmoid(((source - self.mean) / self.std) @ self.coef + self.intercept)
        probability = np.clip(probability, 1e-5, 1.0 - 1e-5)
        weights = probability / (1.0 - probability) * self.n_source / self.n_target
        weights = np.clip(weights, 0.2, 5.0)
        return weights / max(EPS, float(np.mean(weights)))


def _ess(weights: np.ndarray) -> float:
    return float(weights.sum() ** 2 / (np.square(weights).sum() + EPS) / len(weights))


def _balance_error(source: np.ndarray, target: np.ndarray, weights: np.ndarray) -> float:
    combined_std = np.vstack([source, target]).std(axis=0)
    valid = combined_std > EPS
    weighted_mean = np.average(source, axis=0, weights=weights)
    smd = np.abs(weighted_mean - target.mean(axis=0))[valid] / combined_std[valid]
    return float(np.mean(smd)) if len(smd) else 0.0


def _metrics(rows: list[dict[str, Any]], prediction_key: str) -> dict[str, Any]:
    valid = [row for row in rows if row.get(prediction_key) is not None and float(row.get("actual_shed_kwh") or 0) > EPS]
    ratios = np.asarray([row[prediction_key] / row["actual_shed_kwh"] for row in valid], dtype=np.float64)
    errors = np.asarray([row[prediction_key] - row["actual_shed_kwh"] for row in valid], dtype=np.float64)
    passed = np.logical_and(ratios >= 0.8, ratios <= 1.2)
    return {
        "n": len(valid),
        "pass_count": int(passed.sum()),
        "pass_pct": round(float(passed.mean() * 100.0), 2),
        "mean_ratio": round(float(ratios.mean()), 4),
        "mean_abs_ratio_error": round(float(np.mean(np.abs(ratios - 1.0))), 4),
        "mae_kwh": round(float(np.mean(np.abs(errors))), 4),
        "rmse_kwh": round(float(np.sqrt(np.mean(np.square(errors)))), 4),
    }


def _retrieval_rows(args: argparse.Namespace, memory: dict[str, Any]) -> list[dict[str, Any]]:
    target_payload = _load(args.target_events_json)
    target_events = target_payload.get("events", target_payload)
    rows: list[dict[str, Any]] = []
    for record in target_events:
        if str(record.get("method") or "") != args.method or str(record.get("city") or "").lower() != args.city.lower():
            continue
        event = {
            "id": record.get("event_id") or record.get("memory_event_id"),
            "day": record.get("day"),
            "trigger_h": record.get("trigger_h"),
            "end_h": record.get("end_h"),
            "hour_of_day": record.get("hour_of_day"),
            "duration_h": record.get("duration_h"),
            "counterfactual_baseline_kwh": record.get("no_dr_baseline_kwh"),
            "actual_shed_kwh": record.get("realized_delivery_kwh"),
            "actual_shed_basis": "self_contained_memory_record_replay",
        }
        event = {key: value for key, value in event.items() if value is not None}
        metadata = {
            "household_id": record.get("household_id") or record.get("entity_id"),
            "persona_id": record.get("persona_id") or record.get("household_id"),
            "city": record.get("city"),
            "method": record.get("method"),
            "start_date": record.get("start_date"),
        }
        estimate = estimate_event_capacity_from_memory(
            event,
            memory,
            result={},
            metadata=metadata,
            top_k=args.top_k,
        )
        neighbors = estimate.get("retrieved_events") or []
        deliveries = [
            float(item["realized_delivery_kw"])
            for item in neighbors
            if item.get("realized_delivery_kw") is not None
        ]
        ids = [
            str(item["memory_event_id"])
            for item in neighbors
            if item.get("realized_delivery_kw") is not None
        ]
        rows.append(
            {
                "household_id": metadata["household_id"],
                "start_date": metadata["start_date"],
                "accepted": bool(record.get("vpp_appliance_avoidance_success")),
                "actual_shed_kwh": (
                    float(record["realized_delivery_kwh"])
                    if record.get("realized_delivery_kwh") is not None
                    else None
                ),
                "neighbor_ids": ids,
                "neighbor_delivery_kw": deliveries,
                "duration_h": float(record.get("duration_h") or 1.0),
            }
        )
    return rows


def _apply_weights(rows: list[dict[str, Any]], name: str, weight_map: dict[str, float]) -> None:
    key = f"reported_{name}_kwh"
    for row in rows:
        values = row["neighbor_delivery_kw"]
        weights = [weight_map.get(event_id, 1.0) for event_id in row["neighbor_ids"]]
        if not values:
            row[key] = None
            continue
        row[key] = float(np.average(values, weights=weights) * row["duration_h"])


def _apply_global_shift(rows: list[dict[str, Any]], name: str, shift_factor: float) -> None:
    key = f"reported_{name}_global_kwh"
    for row in rows:
        baseline = row.get("reported_uniform_kwh")
        row[key] = None if baseline is None else float(baseline * shift_factor)


def _apply_household_shift(rows: list[dict[str, Any]], name: str, shift_factors: dict[str, float]) -> None:
    key = f"reported_{name}_household_kwh"
    for row in rows:
        baseline = row.get("reported_uniform_kwh")
        factor = shift_factors.get(str(row.get("household_id") or ""), 1.0)
        row[key] = None if baseline is None else float(baseline * factor)


def _shift_sweep(
    rows: list[dict[str, Any]],
    name: str,
    *,
    global_factor: float,
    household_factors: dict[str, float],
) -> dict[str, list[dict[str, Any]]]:
    accepted = [row for row in rows if row["accepted"]]
    output: dict[str, list[dict[str, Any]]] = {"global": [], "household": []}
    for alpha in (0.25, 0.5, 0.75, 1.0):
        shrunk_global = 1.0 + alpha * (global_factor - 1.0)
        global_label = f"{name}_global_a{alpha:g}".replace(".", "p")
        _apply_global_shift(rows, global_label, shrunk_global)
        global_key = f"reported_{global_label}_global_kwh"
        output["global"].append({"alpha": alpha, "factor": shrunk_global, "metrics": _metrics(accepted, global_key)})

        shrunk_household = {
            household: 1.0 + alpha * (factor - 1.0)
            for household, factor in household_factors.items()
        }
        household_label = f"{name}_house_a{alpha:g}".replace(".", "p")
        _apply_household_shift(rows, household_label, shrunk_household)
        household_key = f"reported_{household_label}_household_kwh"
        output["household"].append(
            {"alpha": alpha, "factors": shrunk_household, "metrics": _metrics(accepted, household_key)}
        )
    return output


def _write_report(path: Path, payload: dict[str, Any]) -> None:
    lines = [
        "# IS Weight Improvement Pilot",
        "",
        "July realized delivery is used only for held-out scoring. Weight fitting uses pre-event load, forecast weather, calendar, and household identity.",
        "",
        "| scheme | features | decay | ESS | balance error | local pass | local MAE | global pass | global MAE | household pass | household MAE |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for item in payload["results"]:
        metrics = item["accepted_only"]
        global_metrics = item["accepted_only_global"]
        household_metrics = item["accepted_only_household"]
        balance = item["balance_error"]
        balance_text = "-" if balance is None else f"{balance:.3f}"
        lines.append(
            f"| {item['scheme']} | {item['feature_count']} | {item['weight_decay']:.3g} | {item['ess_fraction']:.3f} | "
            f"{balance_text} | {metrics['pass_pct']:.2f}% | {metrics['mae_kwh']:.4f} | "
            f"{global_metrics['pass_pct']:.2f}% | {global_metrics['mae_kwh']:.4f} | "
            f"{household_metrics['pass_pct']:.2f}% | {household_metrics['mae_kwh']:.4f} |"
        )
    lines.extend(
        [
            "",
            "`balance error` is the mean absolute standardized difference between weighted June and July covariate means; lower is better.",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-json", type=Path, default=_ROOT / "importance_sampling/data/june_2025_germany_with_preload.json")
    parser.add_argument("--target-json", type=Path, default=_ROOT / "importance_sampling/data/july_2025_germany_with_preload.json")
    parser.add_argument("--weather-csv", type=Path, default=_ROOT / "experiments/real_data/germany_2025_weather.csv")
    parser.add_argument(
        "--target-results-root",
        type=Path,
        default=None,
        help="Optional original July result root used to recover event pre-load for the selected city.",
    )
    parser.add_argument("--memory-json", type=Path, default=_ROOT / "dr_capacity_memory_toolkit/june_2025_daily_eb/data/energybridge_daily_dr_memory_rag_v2_weather.json")
    parser.add_argument("--target-events-json", type=Path, default=_ROOT / "dr_capacity_memory_toolkit/july_2025_daily_eb/data/energybridge_daily_dr_memory.json")
    parser.add_argument("--city", default="Germany")
    parser.add_argument("--method", default="EnergyBridge")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output-json", type=Path, required=True)
    parser.add_argument("--output-md", type=Path, required=True)
    args = parser.parse_args()

    source_records = _clean_city(_load(args.source_json), args.city)
    if args.target_results_root is not None:
        target_records = _target_records_from_results(args.target_results_root, args.city)
    else:
        target_records = _clean_city(_load(args.target_json), args.city)
    hourly, daily = _weather_lookup(args.weather_csv)
    households = sorted({str(row["household_id"]) for row in source_records})
    memory_payload = _load(args.memory_json)
    memory = {"events": memory_payload.get("events", memory_payload)}
    retrieval_rows = _retrieval_rows(args, memory)

    _apply_weights(retrieval_rows, "uniform", {})
    _apply_global_shift(retrieval_rows, "uniform", 1.0)
    _apply_household_shift(retrieval_rows, "uniform", {})
    results: list[dict[str, Any]] = [
        {
            "scheme": "uniform_rag",
            "feature_count": 0,
            "weight_decay": 0.0,
            "ess_fraction": 1.0,
            "weight_min": 1.0,
            "weight_max": 1.0,
            "balance_error": None,
            "global_shift_factor": 1.0,
            "household_shift_factors": {},
            "accepted_only": _metrics([row for row in retrieval_rows if row["accepted"]], "reported_uniform_kwh"),
            "accepted_only_global": _metrics([row for row in retrieval_rows if row["accepted"]], "reported_uniform_global_kwh"),
            "accepted_only_household": _metrics([row for row in retrieval_rows if row["accepted"]], "reported_uniform_household_kwh"),
            "all_events": _metrics(retrieval_rows, "reported_uniform_kwh"),
            "all_events_global": _metrics(retrieval_rows, "reported_uniform_global_kwh"),
            "all_events_household": _metrics(retrieval_rows, "reported_uniform_household_kwh"),
        }
    ]
    common_source, _ = _feature_matrix(source_records, hourly, daily, "weather", households)
    common_target, _ = _feature_matrix(target_records, hourly, daily, "weather", households)

    for scheme in ("current", "weather", "nonlinear", "household_shift"):
        source_x, feature_names = _feature_matrix(source_records, hourly, daily, scheme, households)
        target_x, _ = _feature_matrix(target_records, hourly, daily, scheme, households)
        for decay in (0.01, 0.05, 0.2):
            estimator = LogisticDensityRatio(weight_decay=decay).fit(source_x, target_x)
            weights = estimator.weights(source_x)
            weight_map = {str(row["memory_event_id"]): float(weight) for row, weight in zip(source_records, weights)}
            label = f"{scheme}_l2_{decay:g}".replace(".", "p")
            _apply_weights(retrieval_rows, label, weight_map)
            source_delivery = np.asarray([float(row["realized_delivery_kwh"]) for row in source_records])
            global_shift = float(np.average(source_delivery, weights=weights) / np.mean(source_delivery))
            _apply_global_shift(retrieval_rows, label, global_shift)
            household_factors: dict[str, float] = {}
            for household in households:
                indices = [index for index, row in enumerate(source_records) if row["household_id"] == household]
                values = source_delivery[indices]
                household_weights = weights[indices]
                household_factors[household] = float(np.average(values, weights=household_weights) / np.mean(values))
            _apply_household_shift(retrieval_rows, label, household_factors)
            shrinkage = _shift_sweep(
                retrieval_rows,
                label,
                global_factor=global_shift,
                household_factors=household_factors,
            )
            results.append(
                {
                    "scheme": label,
                    "feature_count": len(feature_names),
                    "feature_names": feature_names,
                    "weight_decay": decay,
                    "ess_fraction": round(_ess(weights), 6),
                    "weight_min": round(float(weights.min()), 6),
                    "weight_max": round(float(weights.max()), 6),
                    "balance_error": round(_balance_error(common_source, common_target, weights), 6),
                    "global_shift_factor": round(global_shift, 6),
                    "household_shift_factors": {key: round(value, 6) for key, value in household_factors.items()},
                    "shrinkage_sweep": shrinkage,
                    "accepted_only": _metrics([row for row in retrieval_rows if row["accepted"]], f"reported_{label}_kwh"),
                    "accepted_only_global": _metrics([row for row in retrieval_rows if row["accepted"]], f"reported_{label}_global_kwh"),
                    "accepted_only_household": _metrics([row for row in retrieval_rows if row["accepted"]], f"reported_{label}_household_kwh"),
                    "all_events": _metrics(retrieval_rows, f"reported_{label}_kwh"),
                    "all_events_global": _metrics(retrieval_rows, f"reported_{label}_global_kwh"),
                    "all_events_household": _metrics(retrieval_rows, f"reported_{label}_household_kwh"),
                }
            )

    payload = {
        "scope": {
            "city": args.city,
            "source_events": len(source_records),
            "target_covariate_events": len(target_records),
            "held_out_events": len(retrieval_rows),
            "accepted_held_out_events": sum(row["accepted"] for row in retrieval_rows),
            "top_k": args.top_k,
            "leakage_control": "July realized delivery excluded from density-ratio fitting",
        },
        "results": results,
    }
    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(args.output_md, payload)
    print(f"wrote {args.output_json}")
    print(f"wrote {args.output_md}")
    for item in results:
        metric = item["accepted_only"]
        global_metric = item["accepted_only_global"]
        household_metric = item["accepted_only_household"]
        print(
            f"{item['scheme']:<28} ESS={item['ess_fraction']!s:<8} "
            f"local={metric['pass_pct']:>6.2f}%/{metric['mae_kwh']:.4f} "
            f"global={global_metric['pass_pct']:>6.2f}%/{global_metric['mae_kwh']:.4f} "
            f"house={household_metric['pass_pct']:>6.2f}%/{household_metric['mae_kwh']:.4f}"
        )


if __name__ == "__main__":
    main()
