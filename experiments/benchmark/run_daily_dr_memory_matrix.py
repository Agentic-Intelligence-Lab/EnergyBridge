#!/usr/bin/env python3
"""Build DR memory data with one independent simulation per historical event.

This runner is for capacity-reporting data generation.  It deliberately avoids
feeding a 30-day sequence to the controller.  Each historical VPP event becomes
one standalone 1-day run with its own start date, output directory, role-play
context, and EnergyPlus simulation.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_BENCH_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
DEFAULT_RESULTS_ROOT = _PROJECT_ROOT / "benchmark_results"
RUNNER = _BENCH_DIR / "run_multi_user_household.py"
DEFAULT_EVENTS_JSON = _BENCH_DIR / "configs" / "vpp_events_june_memory.json"
DEFAULT_GERMANY_PRICE_CSV = _PROJECT_ROOT / "experiments" / "real_data" / "germany_2025_price.csv"

load_dotenv(_PROJECT_ROOT / ".env")

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from energybridge.data.day_ahead import DEFAULT_TIANJIN_TOU_PRICE_CSV  # noqa: E402
from energybridge.data.vpp_events import load_vpp_events_config  # noqa: E402
from energybridge.quantification.counterfactual_baseline import (  # noqa: E402
    apply_counterfactual_baseline,
    build_counterfactual_library,
    find_matching_baseline,
)
from energybridge.quantification.dr_event_memory import build_dr_event_memory  # noqa: E402
from energybridge.roleplay.households import list_household_ids  # noqa: E402
from experiments.benchmark.run_baseline_matrix import (  # noqa: E402
    METHOD_CHOICES,
    _canonical_method,
    _first_metric,
    _metric,
    _read_json,
    _result_success,
)


DEFAULT_METHODS = ("no_dr", "eb_rule_milp")
DEFAULT_CITIES = ("Germany", "Tianjin")


@dataclass(frozen=True)
class DailyMemoryJob:
    household_id: str
    method: str
    city: str
    sample_id: str
    source_event_id: str
    source_day: int
    start_date: str
    event_start_h: float
    event_duration_h: float
    price_csv: str
    output_dir: Path
    log_file: Path
    event_json: Path


def _price_csv_for_city(city: str, args: argparse.Namespace) -> str:
    if city.lower() == "germany":
        return args.germany_price_csv
    if city.lower() == "tianjin":
        return args.tianjin_price_csv
    return ""


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in value).strip("_").lower()


def _event_duration_h(event: dict[str, Any]) -> float:
    return max(1e-9, float(event["end_h"]) - float(event["trigger_h"]))


def _make_daily_event_json(path: Path, event: dict[str, Any], *, source: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    start_h = float(event["trigger_h"]) % 24.0
    payload = {
        "description": f"Single-day split from {source}",
        "events": [
            {
                "id": str(event.get("id") or "vpp1"),
                "day": 1,
                "start_h": round(start_h, 6),
                "duration_h": round(_event_duration_h(event), 6),
                "label": str(event.get("label") or event.get("id") or "historical event"),
                "source": f"daily_split:{source}",
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_jobs(args: argparse.Namespace) -> list[DailyMemoryJob]:
    date_dir = Path(args.results_root) / args.date
    batch_dir = date_dir / "_batch_logs"
    start = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    events = load_vpp_events_config(args.vpp_events_json, sim_days=args.days)
    if args.max_samples:
        events = events[: args.max_samples]
    households = args.households or list_household_ids()
    methods = [_canonical_method(method) for method in args.methods]
    cities = list(args.cities)
    jobs: list[DailyMemoryJob] = []
    for event in events:
        source_day = int(event.get("day") or int(float(event["trigger_h"]) // 24.0) + 1)
        sample_date = start + timedelta(days=source_day - 1)
        source_event_id = str(event.get("id") or f"day{source_day}")
        sample_id = f"d{source_day:02d}_{_slug(source_event_id)}"
        event_json = batch_dir / "daily_event_configs" / f"{sample_id}.json"
        _make_daily_event_json(event_json, event, source=args.vpp_events_json)
        for city in cities:
            price_csv = _price_csv_for_city(city, args)
            log_dir = batch_dir / f"daily_dr_memory_{city.lower()}_1day"
            for household_id in households:
                for method in methods:
                    run_name = (
                        f"{household_id}_{method}_{city.lower()}_{sample_id}_"
                        f"{sample_date.isoformat()}_1day"
                    )
                    jobs.append(
                        DailyMemoryJob(
                            household_id=household_id,
                            method=method,
                            city=city,
                            sample_id=sample_id,
                            source_event_id=source_event_id,
                            source_day=source_day,
                            start_date=sample_date.isoformat(),
                            event_start_h=float(event["trigger_h"]) % 24.0,
                            event_duration_h=_event_duration_h(event),
                            price_csv=price_csv,
                            output_dir=date_dir / run_name,
                            log_file=log_dir / f"{run_name}.log",
                            event_json=event_json,
                        )
                    )
    return jobs


def _command_for(job: DailyMemoryJob) -> list[str]:
    cmd = [
        sys.executable,
        "-u",
        str(RUNNER),
        "--household",
        job.household_id,
        "--method",
        job.method,
        "--city",
        job.city,
        "--days",
        "1",
        "--start-date",
        job.start_date,
        "--output",
        str(job.output_dir),
        "--vpp-start-hour",
        str(job.event_start_h),
        "--vpp-duration-hours",
        str(job.event_duration_h),
        "--vpp-events-json",
        str(job.event_json),
    ]
    if job.price_csv:
        cmd += ["--price-csv", job.price_csv]
    return cmd


def _price_metric(data: dict[str, Any], key: str, default: Any = "") -> Any:
    metrics = data.get("day_ahead_price_metrics")
    if isinstance(metrics, dict):
        value = metrics.get(key)
        if value is not None:
            if isinstance(value, float):
                return round(value, 6)
            return value
    return default


def _summarize_job(job: DailyMemoryJob, status: str, return_code: int | None, elapsed_s: float = 0.0) -> dict[str, Any]:
    data = _read_json(job.output_dir / "benchmark_result.json")
    return {
        "persona_id": job.household_id,
        "household_id": job.household_id,
        "method": job.method,
        "city": job.city,
        "days": 1,
        "start_date": job.start_date,
        "memory_sample_id": job.sample_id,
        "memory_source_event_id": job.source_event_id,
        "memory_source_day": job.source_day,
        "price_csv": job.price_csv,
        "vpp_start_hour": round(job.event_start_h, 6),
        "vpp_duration_hours": round(job.event_duration_h, 6),
        "vpp_events_json": str(job.event_json),
        "status": status,
        "return_code": return_code,
        "elapsed_s": round(elapsed_s, 1),
        "output_dir": str(job.output_dir),
        "log_file": str(job.log_file),
        "run_summary_path": str(job.output_dir / "run_summary.txt"),
        "exit_code": _metric(data, "exit_code"),
        "energy_kwh": _first_metric(data, ("energy_kwh", "energy_kwh_total")),
        "energy_kwh_per_day": _metric(data, "energy_kwh_per_day"),
        "day_ahead_total_cost_eur": _price_metric(data, "total_cost_eur"),
        "day_ahead_weighted_price_eur_per_kwh": _price_metric(data, "weighted_price_eur_per_kwh"),
        "day_ahead_price_unit": _price_metric(data, "price_unit"),
        "day_ahead_price_source": _price_metric(data, "source"),
        "vpp_window_energy_kwh": _metric(data, "vpp_window_energy_kwh"),
        "vpp_window_energy_avg_per_hour_kwh": _metric(data, "vpp_window_energy_avg_per_hour_kwh"),
        "vpp_demand_achievement_ratio": _metric(data, "vpp_demand_achievement_ratio"),
        "vpp_appliance_avoidance_success_rate": _metric(data, "vpp_appliance_avoidance_success_rate"),
        "user_pref_score": _metric(data, "user_pref_score"),
        "appliance_task_completion_rate": _metric(data, "appliance_task_completion_rate"),
        "physical_appliance_task_completion_rate": _metric(data, "physical_appliance_task_completion_rate"),
        "policy_output_covered_appliance_services": _metric(data, "policy_output_covered_appliance_services"),
        "policy_output_uncovered_appliance_services": _metric(data, "policy_output_uncovered_appliance_services"),
        "policy_output_absent_appliance_services": _metric(data, "policy_output_absent_appliance_services"),
    }


def _run_job(job: DailyMemoryJob, *, resume: bool) -> dict[str, Any]:
    if resume and _result_success(job.output_dir):
        print(f"[SKIP] {job.output_dir.name} already has exit_code=0", flush=True)
        return _summarize_job(job, "skipped_done", 0, 0.0)
    if job.output_dir.exists():
        shutil.rmtree(job.output_dir)
    job.output_dir.parent.mkdir(parents=True, exist_ok=True)
    job.log_file.parent.mkdir(parents=True, exist_ok=True)
    cmd = _command_for(job)
    print("\n" + "=" * 88, flush=True)
    print(
        f"[RUN] sample={job.sample_id} household={job.household_id} "
        f"method={job.method} city={job.city}",
        flush=True,
    )
    print(f"      output={job.output_dir}", flush=True)
    print(f"      log={job.log_file}", flush=True)
    print("      cmd=" + " ".join(cmd), flush=True)
    print("=" * 88, flush=True)
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    start = time.time()
    with job.log_file.open("w", encoding="utf-8", errors="replace", buffering=1) as log_fh:
        proc = subprocess.Popen(
            cmd,
            cwd=str(_PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
        )
        assert proc.stdout is not None
        for raw_line in proc.stdout:
            line = raw_line.decode("utf-8", errors="replace")
            log_fh.write(line)
            print(line, end="", flush=True)
        return_code = proc.wait()
    elapsed = time.time() - start
    status = "completed" if return_code == 0 and _result_success(job.output_dir) else "failed"
    print(f"[{status.upper()}] {job.output_dir.name} return_code={return_code} elapsed={elapsed:.1f}s", flush=True)
    return _summarize_job(job, status, return_code, elapsed)


def _write_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(key)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fields})


def _result_path_from_row(row: dict[str, Any]) -> Path | None:
    output_dir = row.get("output_dir")
    if not output_dir:
        return None
    path = Path(str(output_dir)) / "benchmark_result.json"
    return path if path.exists() else None


def _copy_counterfactual_metrics(row: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    for key in (
        "counterfactual_baseline_status",
        "counterfactual_baseline_id",
        "counterfactual_baseline_source",
        "counterfactual_baseline_basis",
        "counterfactual_baseline_event_count",
        "counterfactual_baseline_vpp_window_kwh",
        "counterfactual_actual_vpp_window_kwh",
        "counterfactual_capacity_upper_bound_total_kwh",
        "counterfactual_capacity_upper_bound_avg_per_hour_kwh",
        "counterfactual_actual_shed_total_kwh",
        "counterfactual_actual_shed_avg_per_hour_kwh",
        "counterfactual_delivery_ratio_vs_baseline_upper_bound_total",
        "vpp_energy_reduction_total_kwh",
        "vpp_energy_reduction_avg_per_hour_kwh",
        "vpp_energy_reduction_basis",
    ):
        if key in result:
            out[key] = result[key]
    return out


def _postprocess(rows: list[dict[str, Any]], summary_dir: Path) -> None:
    no_dr_items: list[tuple[dict[str, Any], dict[str, Any], Path]] = []
    for row in rows:
        if str(row.get("method") or "").lower() != "no_dr":
            continue
        result_path = _result_path_from_row(row)
        if result_path is None:
            continue
        no_dr_items.append((_read_json(result_path), row, result_path))
    library = build_counterfactual_library(no_dr_items)
    library_path = summary_dir / "daily_dr_memory_no_dr_counterfactual_library.json"
    library_path.write_text(json.dumps(library, ensure_ascii=False, indent=2), encoding="utf-8")
    applied_rows: list[dict[str, Any]] = []
    memory_items: list[tuple[dict[str, Any], dict[str, Any], Path]] = []
    for row in rows:
        result_path = _result_path_from_row(row)
        if result_path is None:
            applied_rows.append(row)
            continue
        result = _read_json(result_path)
        baseline = find_matching_baseline(library, result, metadata=row)
        if baseline is not None:
            result = apply_counterfactual_baseline(result, baseline, metadata=row)
            result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            row = _copy_counterfactual_metrics(row, result)
        applied_rows.append(row)
        if str(row.get("method") or "").lower() == "eb_rule_milp":
            memory_items.append((result, row, result_path))
    applied_json = summary_dir / "daily_dr_memory_summary_with_counterfactual.json"
    applied_csv = summary_dir / "daily_dr_memory_summary_with_counterfactual.csv"
    _write_rows(applied_json, applied_rows)
    _write_csv(applied_csv, applied_rows)
    memory = build_dr_event_memory(memory_items, methods=["eb_rule_milp"])
    memory_path = summary_dir / "eb_rule_milp_daily_dr_memory.json"
    memory_path.write_text(json.dumps(memory, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[POST] no-DR library: {library_path} baselines={len(library.get('baselines') or [])}", flush=True)
    print(f"[POST] applied summary: {applied_json}", flush=True)
    print(f"[POST] daily memory  : {memory_path} events={len(memory.get('events') or [])}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--households", nargs="+", default=None, help="Defaults to all fixed households.")
    parser.add_argument("--cities", nargs="+", default=list(DEFAULT_CITIES), choices=["Germany", "Tianjin", "Beijing", "Shanghai"])
    parser.add_argument("--methods", nargs="+", default=list(DEFAULT_METHODS), choices=list(METHOD_CHOICES))
    parser.add_argument("--days", type=int, default=30, help="Number of historical source days.")
    parser.add_argument("--start-date", default="2025-06-01", help="First historical source date.")
    parser.add_argument("--vpp-events-json", default=str(DEFAULT_EVENTS_JSON))
    parser.add_argument("--germany-price-csv", default=str(DEFAULT_GERMANY_PRICE_CSV))
    parser.add_argument("--tianjin-price-csv", default=str(DEFAULT_TIANJIN_TOU_PRICE_CSV))
    parser.add_argument("--results-root", default=str(DEFAULT_RESULTS_ROOT))
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--workers", type=int, default=5)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--max-samples", type=int, default=0, help="Limit number of historical event days.")
    parser.add_argument("--max-runs", type=int, default=0, help="Limit total jobs after expansion.")
    parser.add_argument("--no-postprocess", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    datetime.strptime(args.start_date, "%Y-%m-%d")
    jobs = _make_jobs(args)
    if args.max_runs:
        jobs = jobs[: args.max_runs]
    summary_dir = Path(args.results_root) / args.date / "_batch_logs"
    raw_json = summary_dir / "daily_dr_memory_summary_raw.json"
    raw_csv = summary_dir / "daily_dr_memory_summary_raw.csv"
    print("=" * 88)
    print("EnergyBridge daily-split DR memory matrix")
    print(f"  source events: {args.vpp_events_json}")
    print(f"  jobs         : {len(jobs)}")
    print(f"  cities       : {', '.join(args.cities)}")
    print(f"  methods      : {', '.join(args.methods)}")
    print(f"  start        : {args.start_date}")
    print(f"  summary      : {raw_json}")
    print("=" * 88)
    if args.dry_run:
        for idx, job in enumerate(jobs, start=1):
            print(f"[{idx:04d}/{len(jobs):04d}] {' '.join(_command_for(job))}")
        return
    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        future_to_job = {executor.submit(_run_job, job, resume=args.resume): job for job in jobs}
        for idx, future in enumerate(as_completed(future_to_job), start=1):
            job = future_to_job[future]
            try:
                row = future.result()
            except Exception as exc:
                row = {
                    "household_id": job.household_id,
                    "method": job.method,
                    "city": job.city,
                    "memory_sample_id": job.sample_id,
                    "start_date": job.start_date,
                    "status": "failed",
                    "return_code": "",
                    "output_dir": str(job.output_dir),
                    "log_file": str(job.log_file),
                    "error": str(exc),
                }
            print(
                f"\n>>> Daily memory completed: {idx}/{len(jobs)} "
                f"{job.output_dir.name} status={row.get('status')}",
                flush=True,
            )
            rows.append(row)
            _write_rows(raw_json, rows)
            _write_csv(raw_csv, rows)
            if args.fail_fast and row.get("status") == "failed":
                raise SystemExit(f"Stopping after failed job: {job.output_dir}")
    failed = sum(1 for row in rows if row.get("status") == "failed")
    print("=" * 88)
    print("DAILY DR MEMORY MATRIX DONE")
    print(f"  completed/skipped: {len(rows) - failed}")
    print(f"  failed           : {failed}")
    print(f"  raw summary      : {raw_json}")
    print("=" * 88)
    if failed:
        raise SystemExit(1)
    if not args.no_postprocess:
        _postprocess(rows, summary_dir)


if __name__ == "__main__":
    main()
