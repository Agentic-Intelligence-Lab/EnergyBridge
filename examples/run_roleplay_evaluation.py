"""Run one role-play user evaluation for 5 turns by default."""

from __future__ import annotations

import json
import sys
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from energybridge.evaluation.roleplay_evaluator import run_roleplay_evaluation


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run one EnergyBridge role-play evaluation.")
    parser.add_argument("--turns", type=int, default=5, help="Number of turns for this simulated user.")
    parser.add_argument(
        "--output-root",
        default="logs/evaluations",
        help="Directory under which the user evaluation artifacts are written.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run_roleplay_evaluation(turns=args.turns, output_root=args.output_root)
    summary = result["summary"]

    print("=== Roleplay Evaluation Complete ===")
    print(f"Evaluation user id: {result['evaluation_user_id']}")
    print(f"Artifacts directory: {result['user_dir']}")
    print()

    print("=== Learning Summary ===")
    print(json.dumps(summary.get("learning_summary", {}), ensure_ascii=False, indent=2))
    print()

    print("=== Turn Overview ===")
    for item in summary.get("turn_overview", []):
        print(json.dumps(item, ensure_ascii=False))


if __name__ == "__main__":
    main()
