#!/usr/bin/env python3
"""Run the 10-persona baseline matrix for EnergyBridge.

Default matrix:
  approved personas x {agent, mpc_dynamic, mpc_ep}

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


DEFAULT_METHODS = ("agent", "mpc_dynamic", "mpc_ep")


@dataclass(frozen=True)
class Job:
    persona_id: str
    method: str
    city: str
    mpc_horizon: int
    output_dir: Path
    log_file: Path


def _slug_label(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value or "").strip("_").lower()


def _persona_run_label(persona_id: str) -> str:
    match = re.match(r"^basic_role_([a-z])(?:_|$)", persona_id)
    if match:
        return f"role_{match.group(1)}"
    return _slug_label(persona_id)


def _method_token(method: str, horizon: int) -> str:
    if method in ("mpc_dynamic", "mpc_ep"):
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


def _result_success(output_dir: Path) -> bool:
    data = _read_json(output_dir / "benchmark_result.json")
    return bool(data) and data.get("exit_code") == 0


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


def _make_jobs(args: argparse.Namespace) -> list[Job]:
    run_date = args.date
    date_dir = Path(args.results_root) / run_date
    log_dir = date_dir / "_batch_logs" / f"baseline_matrix_{args.city.lower()}_H{args.mpc_horizon}"
    personas = args.personas or list_personas(approved_only=True)
    methods = args.methods or list(DEFAULT_METHODS)

    jobs: list[Job] = []
    for persona_id in personas:
        for method in methods:
            run_name = _run_name(persona_id, method, args.city, args.mpc_horizon)
            output_dir = date_dir / run_name
            log_file = log_dir / f"{run_name}.log"
            jobs.append(
                Job(
                    persona_id=persona_id,
                    method=method,
                    city=args.city,
                    mpc_horizon=args.mpc_horizon,
                    output_dir=output_dir,
                    log_file=log_file,
                )
            )
    return jobs


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
        "--output",
        str(job.output_dir),
    ]
    if job.method in ("mpc_dynamic", "mpc_ep"):
        cmd += ["--mpc-horizon", str(job.mpc_horizon)]
    return cmd


def _summarize_job(job: Job, status: str, return_code: int | None, elapsed_s: float = 0.0) -> dict[str, Any]:
    data = _read_json(job.output_dir / "benchmark_result.json")
    return {
        "persona_id": job.persona_id,
        "method": job.method,
        "city": job.city,
        "mpc_horizon": job.mpc_horizon if job.method in ("mpc_dynamic", "mpc_ep") else "",
        "status": status,
        "return_code": return_code,
        "elapsed_s": round(elapsed_s, 1),
        "output_dir": str(job.output_dir),
        "log_file": str(job.log_file),
        "exit_code": _metric(data, "exit_code"),
        "energy_kwh": _first_metric(data, ("energy_kwh", "energy_kwh_total")),
        "vpp_window_energy_kwh": _metric(data, "vpp_window_energy_kwh"),
        "vpp_actual_shed_kwh": _metric(data, "vpp_actual_shed_kwh"),
        "vpp_demand_achievement_ratio": _metric(data, "vpp_demand_achievement_ratio"),
        "user_pref_score": _metric(data, "user_pref_score"),
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
    if resume and _result_success(job.output_dir):
        print(f"[SKIP] {job.output_dir.name} already has exit_code=0", flush=True)
        return _summarize_job(job, "skipped_done", 0, 0.0)

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
            print(line, end="", flush=True)
            log_fh.write(line)
        return_code = proc.wait()

    elapsed = time.time() - start
    status = "completed" if return_code == 0 and _result_success(job.output_dir) else "failed"
    print(f"[{status.upper()}] {job.output_dir.name} return_code={return_code} elapsed={elapsed:.1f}s", flush=True)
    return _summarize_job(job, status, return_code, elapsed)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run approved personas across agent, mpc_dynamic, and mpc_ep baselines."
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
        choices=list(DEFAULT_METHODS),
        default=None,
        help="Methods to run. Defaults to agent mpc_dynamic mpc_ep.",
    )
    parser.add_argument(
        "--city",
        default="Tianjin",
        choices=["Tianjin", "Beijing", "Shanghai"],
        help="Weather city label. Default: Tianjin.",
    )
    parser.add_argument(
        "--mpc-horizon",
        type=int,
        default=6,
        help="MPC horizon in 10-minute steps for mpc_dynamic/mpc_ep. Default: 6.",
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
        help="Skip jobs whose benchmark_result.json already has exit_code=0.",
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
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.mpc_horizon < 1:
        raise SystemExit("--mpc-horizon must be >= 1")
    jobs = _make_jobs(args)
    if args.max_runs:
        jobs = jobs[: args.max_runs]

    summary_dir = Path(args.results_root) / args.date / "_batch_logs"
    suffix = f"{args.city.lower()}_H{args.mpc_horizon}"
    summary_json = summary_dir / f"baseline_matrix_summary_{suffix}.json"
    summary_csv = summary_dir / f"baseline_matrix_summary_{suffix}.csv"

    print("=" * 88)
    print("EnergyBridge baseline matrix")
    print(f"  personas : {len(set(j.persona_id for j in jobs))}")
    print(f"  methods  : {', '.join(dict.fromkeys(j.method for j in jobs))}")
    print(f"  jobs     : {len(jobs)}")
    print(f"  city     : {args.city}")
    print(f"  horizon  : H={args.mpc_horizon}")
    print(f"  summary  : {summary_json}")
    print("=" * 88)

    if args.dry_run:
        for idx, job in enumerate(jobs, start=1):
            print(f"[{idx:02d}/{len(jobs):02d}] {' '.join(_command_for(job))}")
        return

    summary_rows: list[dict[str, Any]] = []
    for idx, job in enumerate(jobs, start=1):
        print(f"\n>>> Matrix progress: {idx}/{len(jobs)}", flush=True)
        row = _run_job(job, resume=args.resume)
        summary_rows.append(row)
        _write_summaries(summary_rows, summary_json, summary_csv)
        if args.fail_fast and row["status"] == "failed":
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
