#!/usr/bin/env python3
"""Run fixed multi-user household scenarios across benchmark methods."""

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
from datetime import date
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_BENCH_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
DEFAULT_RESULTS_ROOT = _PROJECT_ROOT / "benchmark_results"
RUNNER = _BENCH_DIR / "run_multi_user_household.py"

load_dotenv(_PROJECT_ROOT / ".env")

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from energybridge.roleplay.households import list_household_ids  # noqa: E402
from energybridge.data.day_ahead import DEFAULT_TIANJIN_TOU_PRICE_CSV  # noqa: E402
from experiments.benchmark.run_baseline_matrix import (  # noqa: E402
    ENERGYBRIDGE_METHOD_ID,
    METHOD_CHOICES,
    _canonical_method,
    _first_metric,
    _metric,
    _read_json,
    _result_success,
    _write_summaries,
)


DEFAULT_METHODS = (ENERGYBRIDGE_METHOD_ID, "mpc_dynamic", "rule_milp", "rl_ppo_pref_v2", "hema_agent")


@dataclass(frozen=True)
class HouseholdJob:
    household_id: str
    method: str
    city: str
    mpc_horizon: int
    days: int
    start_date: str
    price_csv: str
    vpp_start_hour: float
    vpp_duration_hours: float
    vpp_events_json: str
    output_dir: Path
    log_file: Path


def _method_token(method: str, horizon: int) -> str:
    method = _canonical_method(method)
    if method == "mpc_dynamic":
        return f"{method}_H{int(horizon)}"
    return method


def _run_name(household_id: str, method: str, city: str, horizon: int, days: int) -> str:
    return f"{household_id}_{_method_token(method, horizon)}_{city.lower()}_{days}days"


def _price_metric(data: dict[str, Any], key: str, default: Any = "") -> Any:
    metrics = data.get("day_ahead_price_metrics")
    if isinstance(metrics, dict):
        value = metrics.get(key)
        if value is not None:
            if isinstance(value, float):
                return round(value, 6)
            return value
    return default


def _make_jobs(args: argparse.Namespace) -> list[HouseholdJob]:
    run_date = args.date
    date_dir = Path(args.results_root) / run_date
    days = args.days if args.days is not None else (7 if args.city.lower() == "germany" else 3)
    start_date = args.start_date or ("2025-06-01" if args.city.lower() == "germany" else "")
    log_dir = date_dir / "_batch_logs" / f"household_matrix_{args.city.lower()}_{days}days_H{args.mpc_horizon}"
    households = args.households or list_household_ids()
    methods = [_canonical_method(method) for method in (args.methods or list(DEFAULT_METHODS))]

    jobs: list[HouseholdJob] = []
    for household_id in households:
        for method in methods:
            run_name = _run_name(household_id, method, args.city, args.mpc_horizon, days)
            jobs.append(
                HouseholdJob(
                    household_id=household_id,
                    method=method,
                    city=args.city,
                    mpc_horizon=args.mpc_horizon,
                    days=days,
                    start_date=start_date,
                    price_csv=args.price_csv,
                    vpp_start_hour=float(args.vpp_start_hour) % 24.0,
                    vpp_duration_hours=float(args.vpp_duration_hours),
                    vpp_events_json=args.vpp_events_json,
                    output_dir=date_dir / run_name,
                    log_file=log_dir / f"{run_name}.log",
                )
            )
    return jobs


def _price_display(args: argparse.Namespace) -> str:
    if args.price_csv:
        return args.price_csv
    if args.city.lower() == "tianjin" and DEFAULT_TIANJIN_TOU_PRICE_CSV.exists():
        return f"{DEFAULT_TIANJIN_TOU_PRICE_CSV} (auto Tianjin TOU)"
    return "N/A"


def _command_for(job: HouseholdJob) -> list[str]:
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
        str(job.days),
        "--output",
        str(job.output_dir),
        "--vpp-start-hour",
        str(job.vpp_start_hour),
        "--vpp-duration-hours",
        str(job.vpp_duration_hours),
    ]
    if job.start_date:
        cmd += ["--start-date", job.start_date]
    if job.price_csv:
        cmd += ["--price-csv", job.price_csv]
    if job.vpp_events_json:
        cmd += ["--vpp-events-json", job.vpp_events_json]
    if job.method == "mpc_dynamic":
        cmd += ["--mpc-horizon", str(job.mpc_horizon)]
    return cmd


def _summarize_job(job: HouseholdJob, status: str, return_code: int | None, elapsed_s: float = 0.0) -> dict[str, Any]:
    data = _read_json(job.output_dir / "benchmark_result.json")
    return {
        "persona_id": job.household_id,
        "household_id": job.household_id,
        "method": job.method,
        "city": job.city,
        "days": job.days,
        "start_date": job.start_date,
        "price_csv": job.price_csv,
        "vpp_start_hour": round(job.vpp_start_hour, 6),
        "vpp_duration_hours": round(job.vpp_duration_hours, 6),
        "vpp_events_json": job.vpp_events_json,
        "mpc_horizon": job.mpc_horizon if job.method == "mpc_dynamic" else "",
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
        "vpp_actual_shed_kwh": _metric(data, "vpp_actual_shed_kwh"),
        "vpp_energy_reduction_kwh": _metric(data, "vpp_energy_reduction_kwh"),
        "vpp_energy_reduction_total_kwh": _metric(data, "vpp_energy_reduction_total_kwh"),
        "vpp_energy_reduction_avg_per_event_kwh": _metric(data, "vpp_energy_reduction_avg_per_event_kwh"),
        "vpp_energy_reduction_avg_per_hour_kwh": _metric(data, "vpp_energy_reduction_avg_per_hour_kwh"),
        "vpp_energy_reduction_basis": _metric(data, "vpp_energy_reduction_basis"),
        "counterfactual_baseline_status": _metric(data, "counterfactual_baseline_status"),
        "counterfactual_baseline_id": _metric(data, "counterfactual_baseline_id"),
        "counterfactual_baseline_vpp_window_kwh": _metric(data, "counterfactual_baseline_vpp_window_kwh"),
        "counterfactual_baseline_vpp_window_avg_per_hour_kwh": _metric(
            data, "counterfactual_baseline_vpp_window_avg_per_hour_kwh"
        ),
        "counterfactual_capacity_upper_bound_total_kwh": _metric(
            data, "counterfactual_capacity_upper_bound_total_kwh"
        ),
        "counterfactual_capacity_upper_bound_avg_per_hour_kwh": _metric(
            data, "counterfactual_capacity_upper_bound_avg_per_hour_kwh"
        ),
        "counterfactual_actual_shed_total_kwh": _metric(data, "counterfactual_actual_shed_total_kwh"),
        "counterfactual_actual_shed_avg_per_hour_kwh": _metric(
            data, "counterfactual_actual_shed_avg_per_hour_kwh"
        ),
        "counterfactual_delivery_ratio_vs_target_avg": _metric(
            data, "counterfactual_delivery_ratio_vs_target_avg"
        ),
        "counterfactual_delivery_ratio_vs_recommended_bid_avg": _metric(
            data, "counterfactual_delivery_ratio_vs_recommended_bid_avg"
        ),
        "counterfactual_delivery_ratio_vs_baseline_upper_bound_avg": _metric(
            data, "counterfactual_delivery_ratio_vs_baseline_upper_bound_avg"
        ),
        "counterfactual_delivery_ratio_vs_baseline_upper_bound_total": _metric(
            data, "counterfactual_delivery_ratio_vs_baseline_upper_bound_total"
        ),
        "vpp_demand_achievement_ratio": _metric(data, "vpp_demand_achievement_ratio"),
        "vpp_appliance_avoidance_success_rate": _metric(data, "vpp_appliance_avoidance_success_rate"),
        "user_pref_score": _metric(data, "user_pref_score"),
        "appliance_task_completion_rate": _metric(data, "appliance_task_completion_rate"),
        "physical_appliance_task_completion_rate": _metric(data, "physical_appliance_task_completion_rate"),
        "policy_output_covered_appliance_services": _metric(data, "policy_output_covered_appliance_services"),
        "policy_output_uncovered_appliance_services": _metric(data, "policy_output_uncovered_appliance_services"),
        "policy_output_absent_appliance_services": _metric(data, "policy_output_absent_appliance_services"),
        "completed_vpp_avoidance_rate": _first_metric(
            data,
            ("completed_vpp_avoidance_rate", "appliance_vpp_avoidance_rate", "appliance_shift_success_rate"),
        ),
    }


def _run_job(job: HouseholdJob, *, resume: bool) -> dict[str, Any]:
    if resume and _result_success(job.output_dir):
        print(f"[SKIP] {job.output_dir.name} already has exit_code=0", flush=True)
        return _summarize_job(job, "skipped_done", 0, 0.0)
    if job.output_dir.exists():
        shutil.rmtree(job.output_dir)

    job.log_file.parent.mkdir(parents=True, exist_ok=True)
    job.output_dir.parent.mkdir(parents=True, exist_ok=True)
    cmd = _command_for(job)
    print("\n" + "=" * 88, flush=True)
    print(f"[RUN] household={job.household_id} method={job.method} city={job.city}", flush=True)
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run fixed multi-user households across baseline methods.")
    parser.add_argument("--households", nargs="+", default=None, help="Household IDs. Defaults to all fixed households.")
    parser.add_argument("--methods", nargs="+", choices=list(METHOD_CHOICES), default=None, help="Methods to run.")
    parser.add_argument("--city", default="Tianjin", choices=["Tianjin", "Beijing", "Shanghai", "Germany"])
    parser.add_argument("--days", type=int, default=None, help="Simulation length. Defaults to 3, or 7 for Germany.")
    parser.add_argument("--start-date", default="", help="RunPeriod start date YYYY-MM-DD.")
    parser.add_argument("--price-csv", default="", help="Optional day-ahead price CSV.")
    parser.add_argument("--vpp-start-hour", type=float, default=18.0)
    parser.add_argument("--vpp-duration-hours", type=float, default=1.0)
    parser.add_argument("--vpp-events-json", default="")
    parser.add_argument("--mpc-horizon", type=int, default=6)
    parser.add_argument("--results-root", default=str(DEFAULT_RESULTS_ROOT))
    parser.add_argument("--date", default=date.today().isoformat())
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--max-runs", type=int, default=0)
    parser.add_argument("--workers", type=int, default=1, help="Number of jobs to run in parallel. Default: 1.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mpc_horizon < 1:
        raise SystemExit("--mpc-horizon must be >= 1")
    if args.days is not None and args.days < 1:
        raise SystemExit("--days must be >= 1")
    if args.vpp_duration_hours <= 0:
        raise SystemExit("--vpp-duration-hours must be > 0")
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    if not args.vpp_events_json and (float(args.vpp_start_hour) % 24.0) + float(args.vpp_duration_hours) > 24.0:
        raise SystemExit("VPP windows crossing midnight are not supported yet; choose start+duration <= 24")

    days = args.days if args.days is not None else (7 if args.city.lower() == "germany" else 3)
    start_date = args.start_date or ("2025-06-01" if args.city.lower() == "germany" else "")
    jobs = _make_jobs(args)
    if args.max_runs:
        jobs = jobs[: args.max_runs]

    summary_dir = Path(args.results_root) / args.date / "_batch_logs"
    suffix = f"{args.city.lower()}_{days}days_H{args.mpc_horizon}"
    summary_json = summary_dir / f"household_matrix_summary_{suffix}.json"
    summary_csv = summary_dir / f"household_matrix_summary_{suffix}.csv"

    print("=" * 88)
    print("EnergyBridge household matrix")
    print(f"  households: {len(set(j.household_id for j in jobs))}")
    print(f"  methods   : {', '.join(dict.fromkeys(j.method for j in jobs))}")
    print(f"  jobs      : {len(jobs)}")
    print(f"  city      : {args.city}")
    print(f"  days      : {days}")
    print(f"  start     : {start_date or '(template IDF)'}")
    print(f"  price     : {_price_display(args)}")
    print(f"  summary   : {summary_json}")
    print("=" * 88)

    if args.dry_run:
        for idx, job in enumerate(jobs, start=1):
            print(f"[{idx:02d}/{len(jobs):02d}] {' '.join(_command_for(job))}")
        return

    rows: list[dict[str, Any]] = []
    if args.workers == 1:
        for idx, job in enumerate(jobs, start=1):
            print(f"\n>>> Household matrix progress: {idx}/{len(jobs)}", flush=True)
            row = _run_job(job, resume=args.resume)
            rows.append(row)
            _write_summaries(rows, summary_json, summary_csv)
            if args.fail_fast and row["status"] == "failed":
                raise SystemExit(f"Stopping after failed job: {job.output_dir}")
    else:
        print(f"\n>>> Running household matrix with {args.workers} parallel workers", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_job = {executor.submit(_run_job, job, resume=args.resume): job for job in jobs}
            for idx, future in enumerate(as_completed(future_to_job), start=1):
                job = future_to_job[future]
                try:
                    row = future.result()
                except Exception as exc:
                    row = _summarize_job(job, "failed", None, 0.0)
                    row["error"] = str(exc)
                print(
                    f"\n>>> Household matrix completed: {idx}/{len(jobs)} "
                    f"{job.output_dir.name} status={row['status']}",
                    flush=True,
                )
                rows.append(row)
                _write_summaries(rows, summary_json, summary_csv)
                if args.fail_fast and row["status"] == "failed":
                    for pending in future_to_job:
                        pending.cancel()
                    raise SystemExit(f"Stopping after failed job: {job.output_dir}")

    n_failed = sum(1 for row in rows if row["status"] == "failed")
    print("\n" + "=" * 88)
    print("HOUSEHOLD MATRIX DONE")
    print(f"  completed: {sum(1 for row in rows if row['status'] == 'completed')}")
    print(f"  skipped  : {sum(1 for row in rows if row['status'] == 'skipped_done')}")
    print(f"  failed   : {n_failed}")
    print(f"  summary  : {summary_json}")
    print(f"  csv      : {summary_csv}")
    print("=" * 88)
    if n_failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
