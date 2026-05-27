#!/usr/bin/env python3
"""Run the family home benchmark for a single persona JSON file.

Usage
-----
  python3 run_persona_json.py <persona_id_or_json_path> [--output <dir>] [--city <Tianjin|Beijing|Shanghai>]

Examples
--------
  # By persona ID (looks up energybridge/roleplay/personas/<id>.json automatically)
  python3 run_persona_json.py atom_comfort_sensitive

  # By explicit JSON path
  python3 run_persona_json.py ../../energybridge/roleplay/personas/atom_comfort_sensitive.json

  # Full options
  python3 run_persona_json.py basic_role_a_commuter_price_cooperative --output /tmp/results --city Tianjin

Output
------
  <output_dir>/  - EnergyPlus raw files (CSV, HTML, audit...)
  Console shows per-VPP-event decisions + final result summary.

Prerequisites
-------------
  conda activate energybridge        # or your virtualenv
  cp .env.example .env               # set LLM_API_KEY_POOL
  pip install -r requirements.txt
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from dotenv import load_dotenv

# Resolve project root and load env
_BENCH_DIR    = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import family_runner as fr


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run family home benchmark for a single persona."
    )
    parser.add_argument(
        "persona",
        help="Persona ID (e.g. atom_comfort_sensitive) or path to a persona JSON file.",
    )
    parser.add_argument(
        "--output", "-o",
        default=None,
        help="Directory for EnergyPlus output files. "
             "Defaults to experiments/benchmark/results/<persona_id>/",
    )
    parser.add_argument(
        "--city", "-c",
        default="Tianjin",
        choices=["Tianjin", "Beijing", "Shanghai"],
        help="Weather city label (default: Tianjin).",
    )
    args = parser.parse_args()

    # Resolve persona id
    p = Path(args.persona)
    if p.exists() and p.suffix == ".json":
        persona_id = json.loads(p.read_text(encoding="utf-8"))["id"]
    else:
        persona_id = args.persona

    output_dir = (
        Path(args.output)
        if args.output
        else _BENCH_DIR / "results" / persona_id
    )

    print("=" * 70)
    print(f"PERSONA : {persona_id}")
    print(f"CITY    : {args.city}")
    print(f"OUTPUT  : {output_dir}")
    print("=" * 70)

    result = fr.run_family_agent(
        persona_name=persona_id,
        output_dir=output_dir,
        weather_label=args.city.lower(),
    )

    print()
    print("=" * 70)
    print("RESULT SUMMARY")
    print("=" * 70)
    for k, v in result.as_dict().items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
