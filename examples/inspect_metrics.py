"""CLI: inspect benchmark metrics from trajectory files.

Usage
-----
Show the latest trajectory:
    python examples/inspect_metrics.py --latest

Show a specific trajectory file:
    python examples/inspect_metrics.py --trajectory logs/trajectory_20260512_120758.json

Show all trajectories in a directory and export CSV:
    python examples/inspect_metrics.py --dir logs

Export benchmark run metrics:
    python examples/inspect_metrics.py --dir logs/benchmark_runs --export
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
    extract_metrics_from_dir,
    export_metrics_to_csv,
    find_latest_trajectory,
    print_metric_summary,
    save_metrics,
)


def _collect_benchmark_runs(root: Path) -> list[Path]:
    """Collect all unified_metrics.json from logs/benchmark_runs/."""
    return sorted(root.rglob("unified_metrics.json"))


def _collect_trajectories(root: Path) -> list[Path]:
    """Collect all trajectory_*.json files under root."""
    return sorted(root.rglob("trajectory_*.json"))


def _load_json_metrics(path: Path) -> dict:
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        return {"source_path": str(path), "_load_error": str(e)}


def main():
    ap = argparse.ArgumentParser(
        description="EnergyBridge: inspect and export benchmark metrics",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "--latest", action="store_true",
        help="Print metrics from the most recent logs/trajectory_*.json",
    )
    ap.add_argument(
        "--trajectory", metavar="PATH",
        help="Print metrics from a specific trajectory_*.json file",
    )
    ap.add_argument(
        "--dir", metavar="DIR",
        help="Scan a directory for trajectory_*.json files and print summary table",
    )
    ap.add_argument(
        "--export", action="store_true",
        help="Export to CSV when using --dir (saved under logs/metric_exports/)",
    )
    ap.add_argument(
        "--benchmark-runs", action="store_true",
        help="Also include unified_metrics.json from logs/benchmark_runs/ (with --dir)",
    )
    args = ap.parse_args()

    if not any([args.latest, args.trajectory, args.dir]):
        ap.print_help()
        sys.exit(0)

    # ── --latest ──────────────────────────────────────────────────────────
    if args.latest:
        latest = find_latest_trajectory(log_dir=str(PROJECT_ROOT / "logs"))
        if latest is None:
            print("No trajectory files found in logs/")
            sys.exit(1)
        print(f"Latest trajectory: {latest}\n")
        m = extract_metrics_from_trajectory(latest)
        print_metric_summary(m)

    # ── --trajectory ─────────────────────────────────────────────────────
    if args.trajectory:
        p = Path(args.trajectory)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        if not p.exists():
            print(f"File not found: {p}"); sys.exit(1)
        m = extract_metrics_from_trajectory(p)
        print_metric_summary(m)

    # ── --dir ─────────────────────────────────────────────────────────────
    if args.dir:
        root = Path(args.dir)
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        if not root.exists():
            print(f"Directory not found: {root}"); sys.exit(1)

        # Collect metrics from trajectory files
        metrics_list = [
            extract_metrics_from_trajectory(p)
            for p in _collect_trajectories(root)
        ]

        # Optionally include unified_metrics.json from benchmark runs
        if args.benchmark_runs:
            for p in _collect_benchmark_runs(root):
                metrics_list.append(_load_json_metrics(p))

        if not metrics_list:
            print(f"No trajectory_*.json files found under: {root}")
            sys.exit(0)

        # Print compact table
        header_fields = [
            "run_id", "agent_id", "action_type", "setpoint",
            "execution_status", "safety_ok", "estimated_reduction_kw",
            "indoor_temp_at_event", "user_satisfaction_score",
            "api_latency_seconds", "total_tokens",
        ]
        width = 20
        print(f"\n{'File / Run':<38}", end="")
        for h in header_fields:
            print(f" {h[:width]:<{width}}", end="")
        print()
        print("-" * (38 + len(header_fields) * (width + 1)))
        for m in metrics_list:
            src = Path(m.get("source_path", "?")).name[:36]
            print(f"{src:<38}", end="")
            for h in header_fields:
                v = m.get(h)
                s = str(v)[:width] if v is not None else "-"
                print(f" {s:<{width}}", end="")
            print()

        print(f"\nTotal: {len(metrics_list)} runs")

        # ── Export to CSV ─────────────────────────────────────────────────
        if args.export:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            export_dir = PROJECT_ROOT / "logs" / "metric_exports"
            csv_path = export_dir / f"metrics_{ts}.csv"
            export_metrics_to_csv(metrics_list, csv_path)
            print(f"\nCSV exported to: {csv_path}")


if __name__ == "__main__":
    main()
