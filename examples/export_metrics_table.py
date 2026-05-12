"""Export all benchmark metrics to a single CSV file.

Scans:
  - logs/trajectory_*.json          (direct run trajectories)
  - logs/benchmark_runs/            (benchmark runner outputs, unified_metrics.json)

Usage
-----
    python examples/export_metrics_table.py
    python examples/export_metrics_table.py --log-dir logs --output-dir logs/metric_exports

Output: logs/metric_exports/metrics_<timestamp>.csv
"""

from __future__ import annotations
import argparse, json, sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from energybridge.evaluation.trajectory_metrics import (
    extract_metrics_from_trajectory,
    export_metrics_to_csv,
)


def _load_json(path: Path) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {"source_path": str(path), "_load_error": str(e)}


def collect_all(log_dir: Path) -> list[dict]:
    metrics_list = []

    # Trajectory files directly in log_dir
    for p in sorted(log_dir.glob("trajectory_*.json")):
        metrics_list.append(extract_metrics_from_trajectory(p))

    # unified_metrics.json under benchmark_runs/ sub-directories
    for p in sorted((log_dir / "benchmark_runs").rglob("unified_metrics.json")):
        metrics_list.append(_load_json(p))

    return metrics_list


def main():
    ap = argparse.ArgumentParser(
        description="EnergyBridge: export all benchmark metrics to CSV"
    )
    ap.add_argument(
        "--log-dir", default="logs",
        help="Root log directory (default: logs/)",
    )
    ap.add_argument(
        "--output-dir", default="logs/metric_exports",
        help="Directory for the output CSV (default: logs/metric_exports/)",
    )
    args = ap.parse_args()

    log_dir = Path(args.log_dir)
    if not log_dir.is_absolute():
        log_dir = PROJECT_ROOT / log_dir

    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = PROJECT_ROOT / output_dir

    if not log_dir.exists():
        print(f"Log directory not found: {log_dir}")
        sys.exit(1)

    print(f"Scanning: {log_dir}")
    metrics_list = collect_all(log_dir)

    if not metrics_list:
        print("No metrics found.")
        sys.exit(0)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    csv_path = output_dir / f"metrics_{ts}.csv"
    export_metrics_to_csv(metrics_list, csv_path)

    print(f"Exported {len(metrics_list)} runs → {csv_path}")

    # Print a compact summary to terminal
    print(f"\n{'Source':<50} {'action':<22} {'setpoint':>10} {'safety':>8} "
          f"{'est_red_kw':>12} {'indoor_T':>10}")
    print("-" * 115)
    for m in metrics_list:
        src = Path(m.get("source_path") or "unknown").name[:48]
        print(
            f"{src:<50} "
            f"{str(m.get('action_type') or '-'):<22} "
            f"{str(m.get('setpoint') or '-'):>10} "
            f"{str(m.get('safety_ok') or '-'):>8} "
            f"{str(m.get('estimated_reduction_kw') or '-'):>12} "
            f"{str(m.get('indoor_temp_at_event') or '-'):>10}"
        )


if __name__ == "__main__":
    main()
