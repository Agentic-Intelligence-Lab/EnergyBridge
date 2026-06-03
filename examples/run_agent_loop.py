#!/usr/bin/env python3
"""EnergyBridge interactive agent loop — human-in-the-loop mode.

Runs the identical 3-day EnergyPlus + VPP co-simulation as run_persona_json.py,
but replaces the LLM-simulated user with real human terminal input.

Usage
-----
    cd /path/to/EnergyBridge
    conda activate energybridge
    python3 examples/run_agent_loop.py
    python3 examples/run_agent_loop.py --city Tianjin
    python3 examples/run_agent_loop.py --output /tmp/my_run
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

_BENCH_DIR = Path(__file__).resolve().parent.parent / "experiments" / "benchmark"
_PROJECT_ROOT = Path(__file__).resolve().parent.parent

from dotenv import load_dotenv

load_dotenv(_PROJECT_ROOT / ".env")

for p in (str(_PROJECT_ROOT), str(_BENCH_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import family_runner as fr
from run_persona_json import _write_run_summary

# Human mode default: all controllable appliances are enabled.
_DEFAULT_APPLIANCES = {
    "washer": {
        "present": True,
        "earliest_h": 8.0,
        "latest_h": 22.0,
        "preferred_h": 14.0,
        "duration_h": 2.0,
        "power_kw": 2.0,
        "shiftable": True,
        "dr_adjustable": True,
    },
    "dishwasher": {
        "present": True,
        "earliest_h": 9.0,
        "latest_h": 23.0,
        "preferred_h": 14.0,
        "duration_h": 1.5,
        "power_kw": 1.5,
        "shiftable": True,
        "dr_adjustable": True,
    },
    "dryer": {
        "present": True,
        "earliest_h": 9.0,
        "latest_h": 23.0,
        "preferred_h": 15.0,
        "duration_h": 1.0,
        "power_kw": 3.0,
        "shiftable": True,
        "dr_adjustable": True,
    },
    "water_heater": {
        "present": True,
        "rated_kw": 2.0,
        "bath_required_h": 21.0,
        "dr_adjustable": True,
        "pre_heat_window_start_h": 15.0,
        "pre_heat_window_end_h": 18.0,
    },
    "ev": {
        "present": True,
        "charger_kw": 7.0,
        "capacity_kwh": 60.0,
        "target_soc": 0.80,
        "min_soc": 0.15,
        "arrival_h": 18.0,
        "departure_h": 7.5,
        "daily_drive_kwh": 8.0,
        "efficiency": 0.92,
    },
    "ac": {
        "setpoint_preferred_min_c": 24.0,
        "setpoint_preferred_max_c": 26.0,
        "temp_tolerance_c": 1.0,
    },
}


def _human_persona(city: str) -> dict:
    return {
        "id": f"human_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
        "display_name": "Human Player",
        "summary": "A real household user making their own VPP decisions.",
        "preferences": {
            "scoring_weights": {"comfort": 0.4, "energy": 0.3, "vpp": 0.3},
            "comfort_priority": 0.4,
        },
        "appliances": _DEFAULT_APPLIANCES,
        "llm_prompts": {
            "system_prompt": (
                "I am a real household user. I will make my own decisions "
                "about VPP events."
            ),
        },
        "city": city,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="EnergyBridge interactive agent loop (human-in-the-loop)."
    )
    parser.add_argument("--city", "-c", default="Tianjin", help="EPW city label")
    parser.add_argument("--output", "-o", default=None, help="Override output directory")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(args.output) if args.output else _PROJECT_ROOT / "benchmark_results" / f"human_{ts}"

    persona = _human_persona(args.city)

    print("=" * 62)
    print("  EnergyBridge — Interactive Agent Loop (Human Mode)")
    print("=" * 62)
    print(f"  City       : {args.city}")
    print(f"  Output     : {output_dir}")
    print("  Appliances : washer, dishwasher, dryer, water_heater, ev (all enabled)")
    print()
    print("  You will be prompted 3 times (before each VPP event) to choose")
    print("  a strategy, and 3 times (after each event) to rate the result.")
    print("  The AC + appliance agent acts autonomously between prompts.")
    print("=" * 62)
    print()

    result = fr.run_family_agent(
        user_pref=(
            "I am a real household user. Show me strategy options before each "
            "VPP event, including appliance control plans."
        ),
        appliance_config=_DEFAULT_APPLIANCES,
        output_dir=output_dir,
        weather_label=args.city,
        verbose=args.verbose,
        human_mode=True,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "benchmark_result.json"
    payload = result._asdict() if hasattr(result, "_asdict") else vars(result)
    json_path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    print(f"\n[Saved] benchmark_result.json → {json_path}")

    txt_path = _write_run_summary(result, persona, args.city, output_dir)
    print(f"[Saved] run_summary.txt        → {txt_path}")
    print("\nDone. Check run_summary.txt for the full event log.")


if __name__ == "__main__":
    main()
