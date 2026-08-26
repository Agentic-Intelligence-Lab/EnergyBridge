#!/usr/bin/env python3
"""Run the 10-persona baseline matrix for EnergyBridge.

Default matrix:
  approved personas x {EnergyBridge, mpc_dynamic, rule_milp, rl_ppo_pref_v2, hema_agent}

Each job delegates to run_persona_json.py so that calendar loading, output
directory naming, run_summary generation, and MPC horizon handling stay aligned
with the single-run benchmark path.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import re
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
RUNNER = _BENCH_DIR / "run_persona_json.py"

load_dotenv(_PROJECT_ROOT / ".env")

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from energybridge.roleplay.loader import list_personas  # noqa: E402
from energybridge.data.day_ahead import DEFAULT_TIANJIN_TOU_PRICE_CSV  # noqa: E402
from energybridge.benchmark.run_manifest import (  # noqa: E402
    build_run_manifest,
    result_manifest_fingerprint,
    result_matches_manifest,
)


ENERGYBRIDGE_METHOD_ID = "EnergyBridge"
DEFAULT_METHODS = (ENERGYBRIDGE_METHOD_ID, "mpc_dynamic", "rule_milp", "rl_ppo_pref_v2", "hema_agent")
METHOD_CHOICES = (
    ENERGYBRIDGE_METHOD_ID,
    "agent",
    "mpc_dynamic",
    "mpc",
    "rl",
    "rl_ppo",
    "rl_ppo_pref_v2",
    "rl_pref_v2",
    "rule_milp",
    "rule+milp",
    "pmv_milp",
    "no_dr",
    "none",
    "baseline",
    "hema_agent"
)


@dataclass(frozen=True)
class Job:
    persona_id: str
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


def _slug_label(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value or "").strip("_").lower()


def _canonical_method(method: str) -> str:
    raw = (method or ENERGYBRIDGE_METHOD_ID).strip()
    key = raw.lower()
    aliases = {
        "agent": ENERGYBRIDGE_METHOD_ID,
        "energybridge": ENERGYBRIDGE_METHOD_ID,
        "mpc": "mpc_dynamic",
        "rl": "rl_ppo_pref_v2",
        "rl_ppo": "rl_ppo_pref_v2",
        "rl_ppo_pref_v2": "rl_ppo_pref_v2",
        "rl_pref_v2": "rl_ppo_pref_v2",
        "rule_milp": "rule_milp",
        "rule+milp": "rule_milp",
        "pmv_milp": "rule_milp",
        "eb_rule_milp": ENERGYBRIDGE_METHOD_ID,
        "eb+rule+milp": ENERGYBRIDGE_METHOD_ID,
        "energybridge_rule_milp": ENERGYBRIDGE_METHOD_ID,
        "agent_milp": ENERGYBRIDGE_METHOD_ID,
        "agent+milp": ENERGYBRIDGE_METHOD_ID,
        "no_dr": "no_dr",
        "none": "no_dr",
        "baseline": "no_dr",
    }
    return aliases.get(key, key)


def _persona_run_label(persona_id: str) -> str:
    match = re.match(r"^basic_role_([a-z])(?:_|$)", persona_id)
    if match:
        return f"role_{match.group(1)}"
    return _slug_label(persona_id)


def _method_token(method: str, horizon: int) -> str:
    method = _canonical_method(method)
    if method == "mpc_dynamic":
        return f"{method}_H{int(horizon)}"
    return method


def _run_name(persona_id: str, method: str, city: str, horizon: int, days: int = 3) -> str:
    return (
        f"{_persona_run_label(persona_id)}_"
        f"{_method_token(method, horizon)}_"
        f"{city.lower()}_{days}days"
    )


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _result_success(
    output_dir: Path,
    expected_manifest_or_fingerprint: dict[str, Any] | str | None = None,
) -> bool:
    """Check result success, optionally requiring an exact V2 manifest.

    Omitting ``expected_manifest_or_fingerprint`` preserves the historical V1
    helper behavior for callers that only need to inspect ``exit_code``. Matrix
    resume paths always provide an expected V2 manifest.
    """
    data = _read_json(output_dir / "benchmark_result.json")
    if not data or data.get("exit_code") != 0:
        return False
    if expected_manifest_or_fingerprint is None:
        return True
    return result_matches_manifest(data, expected_manifest_or_fingerprint)


def _manifest_for_job(job: Job) -> dict[str, Any]:
    return build_run_manifest(
        runner="run_persona_json",
        subject_kind="persona",
        subject_id=job.persona_id,
        subject_reference=job.persona_id,
        method=job.method,
        city=job.city,
        days=job.days,
        start_date=job.start_date,
        price_csv=job.price_csv,
        vpp_start_hour=job.vpp_start_hour,
        vpp_duration_hours=job.vpp_duration_hours,
        vpp_events_json=job.vpp_events_json,
        mpc_horizon=job.mpc_horizon,
    )


def _metric(data: dict[str, Any], key: str, default: Any = "") -> Any:
    value = data.get(key, default)
    if isinstance(value, float):
        return round(value, 6)
    return value


def _first_metric(data: dict[str, Any], keys: tuple[str, ...], default: Any = "") -> Any:
    for key in keys:
        value = data.get(key)
        if value is not None:
            if isinstance(value, float):
                return round(value, 6)
            return value
    return default


def _price_metric(data: dict[str, Any], key: str, default: Any = "") -> Any:
    metrics = data.get("day_ahead_price_metrics")
    if isinstance(metrics, dict):
        value = metrics.get(key)
        if value is not None:
            if isinstance(value, float):
                return round(value, 6)
            return value
    return default


def _make_jobs(args: argparse.Namespace) -> list[Job]:
    run_date = args.date
    date_dir = Path(args.results_root) / run_date
    days = args.days if args.days is not None else (7 if args.city.lower() == "germany" else 3)
    start_date = args.start_date or ("2025-06-01" if args.city.lower() == "germany" else "")
    log_dir = date_dir / "_batch_logs" / f"baseline_matrix_{args.city.lower()}_{days}days_H{args.mpc_horizon}"
    personas = args.personas or list_personas(approved_only=True)
    methods = [_canonical_method(method) for method in (args.methods or list(DEFAULT_METHODS))]

    jobs: list[Job] = []
    for persona_id in personas:
        for method in methods:
            run_name = _run_name(persona_id, method, args.city, args.mpc_horizon, days=days)
            output_dir = date_dir / run_name
            log_file = log_dir / f"{run_name}.log"
            jobs.append(
                Job(
                    persona_id=persona_id,
                    method=method,
                    city=args.city,
                    mpc_horizon=args.mpc_horizon,
                    days=days,
                    start_date=start_date,
                    price_csv=args.price_csv,
                    vpp_start_hour=float(args.vpp_start_hour) % 24.0,
                    vpp_duration_hours=float(args.vpp_duration_hours),
                    vpp_events_json=args.vpp_events_json,
                    output_dir=output_dir,
                    log_file=log_file,
                )
            )
    return jobs


def _price_display(args: argparse.Namespace) -> str:
    if args.price_csv:
        return args.price_csv
    if args.city.lower() == "tianjin" and DEFAULT_TIANJIN_TOU_PRICE_CSV.exists():
        return f"{DEFAULT_TIANJIN_TOU_PRICE_CSV} (auto Tianjin TOU)"
    return "N/A"


def _command_for(job: Job) -> list[str]:
    cmd = [
        sys.executable,
        "-u",
        str(RUNNER),
        job.persona_id,
        "--method",
        job.method,
        "--city",
        job.city,
        "--days",
        str(job.days),
        "--output",
        str(job.output_dir),
    ]
    if job.start_date:
        cmd += ["--start-date", job.start_date]
    if job.price_csv:
        cmd += ["--price-csv", job.price_csv]
    cmd += [
        "--vpp-start-hour",
        str(job.vpp_start_hour),
        "--vpp-duration-hours",
        str(job.vpp_duration_hours),
    ]
    if job.vpp_events_json:
        cmd += ["--vpp-events-json", job.vpp_events_json]
    if job.method in {ENERGYBRIDGE_METHOD_ID, "mpc_dynamic"}:
        cmd += ["--mpc-horizon", str(job.mpc_horizon)]
    return cmd


def _summarize_job(job: Job, status: str, return_code: int | None, elapsed_s: float = 0.0) -> dict[str, Any]:
    data = _read_json(job.output_dir / "benchmark_result.json")
    manifest = data.get("run_manifest") if isinstance(data.get("run_manifest"), dict) else {}
    llm = manifest.get("llm") if isinstance(manifest.get("llm"), dict) else {}
    controller_llm = llm.get("controller") if isinstance(llm.get("controller"), dict) else {}
    roleplay_llm = llm.get("roleplay") if isinstance(llm.get("roleplay"), dict) else {}
    return {
        "persona_id": job.persona_id,
        "method": job.method,
        "city": job.city,
        "days": job.days,
        "start_date": job.start_date,
        "price_csv": job.price_csv,
        "vpp_start_hour": round(job.vpp_start_hour, 6),
        "vpp_duration_hours": round(job.vpp_duration_hours, 6),
        "vpp_events_json": job.vpp_events_json,
        "mpc_horizon": job.mpc_horizon if job.method in {ENERGYBRIDGE_METHOD_ID, "mpc_dynamic"} else "",
        "status": status,
        "return_code": return_code,
        "elapsed_s": round(elapsed_s, 1),
        "output_dir": str(job.output_dir),
        "log_file": str(job.log_file),
        "run_fingerprint": result_manifest_fingerprint(data),
        "harness_profile": manifest.get("harness_profile", ""),
        "controller_llm_model": controller_llm.get("model", ""),
        "roleplay_llm_model": roleplay_llm.get("model", ""),
        "exit_code": _metric(data, "exit_code"),
        "energy_kwh": _first_metric(data, ("energy_kwh", "energy_kwh_total")),
        "energy_kwh_per_day": _metric(data, "energy_kwh_per_day"),
        "day_ahead_total_cost_eur": _price_metric(data, "total_cost_eur"),
        "day_ahead_weighted_price_eur_per_kwh": _price_metric(data, "weighted_price_eur_per_kwh"),
        "day_ahead_price_unit": _price_metric(data, "price_unit"),
        "day_ahead_price_source": _price_metric(data, "source"),
        "vpp_window_energy_kwh": _metric(data, "vpp_window_energy_kwh"),
        "vpp_window_energy_avg_per_hour_kwh": _metric(data, "vpp_window_energy_avg_per_hour_kwh"),
        "accepted_effective_vpp_window_energy_kwh": _metric(data, "accepted_effective_vpp_window_energy_kwh"),
        "accepted_effective_vpp_window_energy_avg_per_hour_kwh": _metric(
            data, "accepted_effective_vpp_window_energy_avg_per_hour_kwh"
        ),
        "accepted_effective_service_miss_penalty_kwh": _metric(
            data, "accepted_effective_service_miss_penalty_kwh"
        ),
        "accepted_effective_service_miss_count": _metric(data, "accepted_effective_service_miss_count"),
        "accepted_effective_vpp_success_rate": _metric(data, "accepted_effective_vpp_success_rate"),
        "accepted_effective_vpp_basis": _metric(data, "accepted_effective_vpp_basis"),
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
        "vpp_plan_acceptance_rate": _metric(data, "vpp_plan_acceptance_rate"),
        "vpp_plan_acceptance_probability_avg": _metric(data, "vpp_plan_acceptance_probability_avg"),
        "vpp_plan_rejected_count": _metric(data, "vpp_plan_rejected_count"),
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


def _write_summaries(summary_rows: list[dict[str, Any]], summary_json: Path, summary_csv: Path) -> None:
    summary_json.parent.mkdir(parents=True, exist_ok=True)
    summary_json.write_text(json.dumps(summary_rows, indent=2, ensure_ascii=False), encoding="utf-8")

    if not summary_rows:
        summary_csv.write_text("", encoding="utf-8")
        return
    fieldnames = list(summary_rows[0].keys())
    with summary_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(summary_rows)


def _run_job(job: Job, *, resume: bool) -> dict[str, Any]:
    expected_manifest = _manifest_for_job(job)
    if resume:
        if _result_success(job.output_dir, expected_manifest):
            print(
                f"[SKIP] {job.output_dir.name} has matching successful manifest "
                f"{expected_manifest['fingerprint'][:12]}",
                flush=True,
            )
            return _summarize_job(job, "skipped_done", 0, 0.0)
        if _result_success(job.output_dir):
            print(
                f"[RERUN] {job.output_dir.name} is successful but its manifest is missing or stale",
                flush=True,
            )

    if job.output_dir.exists():
        shutil.rmtree(job.output_dir)

    job.log_file.parent.mkdir(parents=True, exist_ok=True)
    job.output_dir.parent.mkdir(parents=True, exist_ok=True)
    cmd = _command_for(job)

    print("\n" + "=" * 88, flush=True)
    print(f"[RUN] persona={job.persona_id} method={job.method} city={job.city}", flush=True)
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
            try:
                print(line, end="", flush=True)
            except UnicodeEncodeError:
                safe = line.encode(sys.stdout.encoding or "utf-8", errors="replace").decode(
                    sys.stdout.encoding or "utf-8", errors="replace"
                )
                print(safe, end="", flush=True)
        return_code = proc.wait()

    elapsed = time.time() - start
    status = (
        "completed"
        if return_code == 0 and _result_success(job.output_dir, expected_manifest)
        else "failed"
    )
    print(f"[{status.upper()}] {job.output_dir.name} return_code={return_code} elapsed={elapsed:.1f}s", flush=True)
    return _summarize_job(job, status, return_code, elapsed)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run approved personas across EnergyBridge and baseline methods."
    )
    parser.add_argument(
        "--personas",
        nargs="+",
        default=None,
        help="Persona IDs to run. Defaults to all approved personas in energybridge/roleplay/personas.",
    )
    parser.add_argument(
        "--methods",
        nargs="+",
        choices=list(METHOD_CHOICES),
        default=None,
        help="Methods to run. Defaults to EnergyBridge mpc_dynamic rule_milp rl_ppo_pref_v2 hema_agent. 'agent' is a deprecated alias.",
    )
    parser.add_argument(
        "--city",
        default="Tianjin",
        choices=["Tianjin", "Beijing", "Shanghai", "Germany"],
        help="Weather city label. Default: Tianjin.",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Simulation length in days. Defaults to 3, or 7 for --city Germany.",
    )
    parser.add_argument(
        "--start-date",
        default="",
        help="RunPeriod start date YYYY-MM-DD. Defaults to 2025-06-01 for Germany.",
    )
    parser.add_argument(
        "--price-csv",
        default="",
        help="Optional day-ahead price CSV. If omitted, price-aware planning is disabled.",
    )
    parser.add_argument(
        "--vpp-start-hour",
        type=float,
        default=18.0,
        help="Daily VPP event start hour-of-day. Default: 18.0.",
    )
    parser.add_argument(
        "--vpp-duration-hours",
        type=float,
        default=1.0,
        help="Daily VPP event duration in hours. Default: 1.0.",
    )
    parser.add_argument(
        "--vpp-events-json",
        default="",
        help="Optional JSON file defining VPP events. Overrides the daily start/duration schedule.",
    )
    parser.add_argument(
        "--mpc-horizon",
        type=int,
        default=6,
        help="MPC horizon in 10-minute steps for EnergyBridge advisory and mpc_dynamic. Default: 6.",
    )
    parser.add_argument(
        "--results-root",
        default=str(DEFAULT_RESULTS_ROOT),
        help="Root output directory. Default: benchmark_results.",
    )
    parser.add_argument(
        "--date",
        default=date.today().isoformat(),
        help="Date folder under results-root. Default: today.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip only successful jobs whose V2 run-manifest fingerprint exactly matches current inputs/config.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the planned matrix and commands without running simulations.",
    )
    parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Stop after the first failed job.",
    )
    parser.add_argument(
        "--max-runs",
        type=int,
        default=0,
        help="Limit number of jobs, useful for a quick smoke check. Default: no limit.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Number of jobs to run in parallel. Default: 1.",
    )
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
    summary_json = summary_dir / f"baseline_matrix_summary_{suffix}.json"
    summary_csv = summary_dir / f"baseline_matrix_summary_{suffix}.csv"

    print("=" * 88)
    print("EnergyBridge baseline matrix")
    print(f"  personas : {len(set(j.persona_id for j in jobs))}")
    print(f"  methods  : {', '.join(dict.fromkeys(j.method for j in jobs))}")
    print(f"  jobs     : {len(jobs)}")
    print(f"  city     : {args.city}")
    print(f"  days     : {days}")
    print(f"  start    : {start_date or '(template IDF)'}")
    print(f"  price    : {_price_display(args)}")
    print(f"  vpp      : {args.vpp_events_json or f'daily {float(args.vpp_start_hour) % 24.0:.2f}h for {float(args.vpp_duration_hours):.2f}h'}")
    print(f"  horizon  : H={args.mpc_horizon}")
    print(f"  summary  : {summary_json}")
    print("=" * 88)

    if args.dry_run:
        for idx, job in enumerate(jobs, start=1):
            print(f"[{idx:02d}/{len(jobs):02d}] {' '.join(_command_for(job))}")
        return

    summary_rows: list[dict[str, Any]] = []
    if args.workers == 1:
        for idx, job in enumerate(jobs, start=1):
            print(f"\n>>> Matrix progress: {idx}/{len(jobs)}", flush=True)
            row = _run_job(job, resume=args.resume)
            summary_rows.append(row)
            _write_summaries(summary_rows, summary_json, summary_csv)
            if args.fail_fast and row["status"] == "failed":
                raise SystemExit(f"Stopping after failed job: {job.output_dir}")
    else:
        print(f"\n>>> Running with {args.workers} parallel workers", flush=True)
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            future_to_job = {executor.submit(_run_job, job, resume=args.resume): job for job in jobs}
            for idx, future in enumerate(as_completed(future_to_job), start=1):
                job = future_to_job[future]
                try:
                    row = future.result()
                except Exception as exc:
                    row = _summarize_job(job, "failed", None, 0.0)
                    row["error"] = str(exc)
                print(f"\n>>> Matrix completed: {idx}/{len(jobs)} {job.output_dir.name} status={row['status']}", flush=True)
                summary_rows.append(row)
                _write_summaries(summary_rows, summary_json, summary_csv)
                if args.fail_fast and row["status"] == "failed":
                    for pending in future_to_job:
                        pending.cancel()
                    raise SystemExit(f"Stopping after failed job: {job.output_dir}")

    n_failed = sum(1 for row in summary_rows if row["status"] == "failed")
    n_skipped = sum(1 for row in summary_rows if row["status"] == "skipped_done")
    n_completed = sum(1 for row in summary_rows if row["status"] == "completed")
    print("\n" + "=" * 88)
    print("BASELINE MATRIX DONE")
    print(f"  completed: {n_completed}")
    print(f"  skipped  : {n_skipped}")
    print(f"  failed   : {n_failed}")
    print(f"  summary  : {summary_json}")
    print(f"  csv      : {summary_csv}")
    print("=" * 88)

    if n_failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
