#!/usr/bin/env python3
"""Quick Germany 3-day benchmark runner for fast Agent iteration.

This wrapper keeps the full EnergyBridge stack (calendar, capacity
quantification, EnergyPlus, day-ahead price, and role-play scoring) while using
a short Sunday-to-Tuesday Germany run.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
RUN_PERSONA = PROJECT_ROOT / "experiments" / "benchmark" / "run_persona_json.py"
DEFAULT_PRICE_CSV = PROJECT_ROOT / "experiments" / "real_data" / "germany_2025_price.csv"


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Run the recommended fast Germany 3-day benchmark "
            "(Sunday 2025-06-01 through Tuesday 2025-06-03)."
        )
    )
    parser.add_argument(
        "persona",
        nargs="?",
        default="basic_role_a_commuter_price_cooperative",
        help="Persona ID or persona JSON path. Default: basic_role_a_commuter_price_cooperative.",
    )
    parser.add_argument(
        "--method",
        choices=["agent", "mpc_dynamic", "mpc_ep", "mpc"],
        default="agent",
        help="Controller method. Default: agent.",
    )
    parser.add_argument(
        "--mpc-horizon",
        type=int,
        default=6,
        help="MPC horizon in 10-minute steps for mpc_dynamic/mpc_ep. Default: 6.",
    )
    parser.add_argument(
        "--price-csv",
        default=str(DEFAULT_PRICE_CSV),
        help="Day-ahead price CSV. Default: Germany 2025 real price data.",
    )
    parser.add_argument(
        "--no-price",
        action="store_true",
        help="Disable day-ahead price input while keeping Germany weather/date.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional explicit output directory. Default uses benchmark_results/<date>/...",
    )
    parser.add_argument(
        "--vpp-start-hour",
        type=float,
        default=18.0,
        help="Daily VPP start hour. Default matches Tianjin 3-day quick setting: 18.0.",
    )
    parser.add_argument(
        "--vpp-duration-hours",
        type=float,
        default=1.0,
        help="Daily VPP duration. Default matches Tianjin 3-day quick setting: 1.0.",
    )
    parser.add_argument(
        "--vpp-events-json",
        default="",
        help="Optional VPP event JSON. If omitted, daily 18:00-19:00 events are used.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the expanded command without executing it.",
    )
    args, passthrough = parser.parse_known_args()

    cmd = [
        sys.executable,
        str(RUN_PERSONA),
        args.persona,
        "--city",
        "Germany",
        "--method",
        args.method,
        "--days",
        "3",
        "--start-date",
        "2025-06-01",
        "--mpc-horizon",
        str(max(1, int(args.mpc_horizon))),
        "--vpp-start-hour",
        str(float(args.vpp_start_hour)),
        "--vpp-duration-hours",
        str(float(args.vpp_duration_hours)),
    ]
    if args.output:
        cmd += ["--output", args.output]
    if args.vpp_events_json:
        cmd += ["--vpp-events-json", args.vpp_events_json]
    if not args.no_price:
        cmd += ["--price-csv", args.price_csv]
    cmd += passthrough

    print("[Germany 3-day quick] " + " ".join(_quote(part) for part in cmd), flush=True)
    print(
        "[Germany 3-day quick] IDF is generated from experiments/models/family_home/family_simple_3day.idf",
        flush=True,
    )
    print("[Germany 3-day quick] RunPeriod: 2025-06-01 to 2025-06-03 (Sunday start)", flush=True)
    if args.dry_run:
        return
    raise SystemExit(subprocess.run(cmd, cwd=PROJECT_ROOT).returncode)


def _quote(value: str) -> str:
    if not value:
        return "''"
    if any(ch.isspace() or ch in "\"'`$" for ch in value):
        return "'" + value.replace("'", "'\\''") + "'"
    return value


if __name__ == "__main__":
    main()
