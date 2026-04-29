"""Run batch role-play evaluations and export JSON/CSV reports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from energybridge.evaluation.roleplay_evaluator import run_batch_roleplay_evaluation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run batch EnergyBridge role-play evaluations.")
    parser.add_argument("--users", type=int, default=10, help="Number of simulated users to generate.")
    parser.add_argument("--turns", type=int, default=5, help="Number of turns per simulated user.")
    parser.add_argument(
        "--output-root",
        default="logs/evaluations",
        help="Directory under which batch reports and per-user artifacts are written.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_batch_roleplay_evaluation(
        user_count=args.users,
        turns=args.turns,
        output_root=args.output_root,
    )

    print("=== Batch Roleplay Evaluation Complete ===")
    print(f"Batch id: {result['batch_id']}")
    print(f"Batch directory: {result['batch_dir']}")
    print()
    print("=== Aggregate Metrics ===")
    print(json.dumps(result["aggregate"], ensure_ascii=False, indent=2))
    print()
    print("Reports:")
    print(f"- {result['batch_dir']}/batch_summary.json")
    print(f"- {result['batch_dir']}/batch_summary.csv")
    print(f"- {result['batch_dir']}/batch_turns.csv")


if __name__ == "__main__":
    main()
