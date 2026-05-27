#!/usr/bin/env python3
"""Run the family home benchmark for a single persona JSON file.

Usage
-----
  python3 run_persona_json.py <persona_id_or_json_path> [--output <dir>] [--city <Tianjin|Beijing|Shanghai>]

Examples
--------
  python3 run_persona_json.py atom_comfort_sensitive
  python3 run_persona_json.py ../../energybridge/roleplay/personas/atom_comfort_sensitive.json
  python3 run_persona_json.py basic_role_f_commuter_ev_optimizer --city Shanghai --output /tmp/out

Output
------
  <output_dir>/  - EnergyPlus raw files (CSV, HTML, audit...)
  Console shows per-VPP-event LLM decisions + appliance rule summary.

Prerequisites
-------------
  conda activate energybridge
  cp .env.example .env   # set LLM_API_KEY_POOL
  pip install -r requirements.txt
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from dotenv import load_dotenv

_BENCH_DIR    = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))

import family_runner as fr

PERSONA_DIR = _PROJECT_ROOT / "energybridge" / "roleplay" / "personas"


def _load_persona_json(persona_arg: str) -> dict:
    """Accept a persona ID or a path to a JSON file."""
    p = Path(persona_arg)
    if p.exists() and p.suffix == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    # Try by ID in the standard location
    candidate = PERSONA_DIR / f"{persona_arg}.json"
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        f"Persona '{persona_arg}' not found. "
        f"Checked: {p}, {candidate}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run family home benchmark for a single persona."
    )
    parser.add_argument(
        "persona",
        help="Persona ID (e.g. atom_comfort_sensitive) or path to a persona JSON file.",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Directory for EnergyPlus output files. "
             "Defaults to experiments/benchmark/results/<persona_id>/",
    )
    parser.add_argument(
        "--city", "-c", default="Tianjin",
        choices=["Tianjin", "Beijing", "Shanghai"],
        help="Weather city label (default: Tianjin).",
    )
    args = parser.parse_args()

    persona = _load_persona_json(args.persona)
    pid     = persona["id"]
    output_dir = (
        Path(args.output) if args.output
        else _BENCH_DIR / "results" / pid
    )

    print("=" * 70)
    print(f"PERSONA : {pid}")
    print(f"CITY    : {args.city}")
    print(f"OUTPUT  : {output_dir}")
    print("=" * 70)

    result = fr.run_family_agent(
        user_pref        = persona["llm_prompts"]["system_prompt"],
        appliance_config = persona.get("appliances", {}),
        output_dir       = output_dir,
        weather_label    = args.city.lower(),
    )

    print()
    print("=" * 70)
    print("RESULT SUMMARY")
    print("=" * 70)
    for k, v in result.as_dict().items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
