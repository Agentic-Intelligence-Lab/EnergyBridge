#!/usr/bin/env python3
"""Run a single family home scenario with a specific user persona.

Usage:
  python3 run_single.py --method agent --city Beijing --persona commuter
  python3 run_single.py --method agent --city Shanghai --persona comfort_sensitive
  python3 run_single.py --method agent --city Tianjin --persona price_sensitive

Runs ONE scenario (city × method × persona) and prints results + dialogue log path.
Designed for quick per-persona validation before full benchmark.

Available cities:   Beijing, Shanghai, Tianjin
Available methods:  agent, pmv, agent_pmv
Available personas: commuter, comfort_sensitive, price_sensitive
"""
from __future__ import annotations
import argparse, sys, json
from pathlib import Path

BENCH_DIR   = Path(__file__).resolve().parent
WEATHER_DIR = BENCH_DIR.parent / "weather" / "epw"
MODELS_DIR  = BENCH_DIR.parent / "models" / "family_home"
IDF_PATH    = MODELS_DIR / "family_simple_3day.idf"

CITY_EPW = {
    "Beijing":  WEATHER_DIR / "CHN_BJ_Beijing.545110_CSWD.epw",
    "Shanghai": WEATHER_DIR / "CHN_SH_Shanghai.583670_CSWD.epw",
    "Tianjin":  WEATHER_DIR / "CHN_TJ_Tianjin.545270_CSWD.epw",
}

sys.path.insert(0, str(BENCH_DIR))
sys.path.insert(0, str(BENCH_DIR.parent.parent))

def main():
    parser = argparse.ArgumentParser(description="Run single family scenario with persona.")
    parser.add_argument("--method",  default="agent",
                        choices=["agent", "pmv", "agent_pmv"],
                        help="Control method")
    parser.add_argument("--city",    default="Beijing",
                        choices=list(CITY_EPW.keys()),
                        help="Weather city")
    parser.add_argument("--persona", default="commuter",
                        choices=["commuter", "comfort_sensitive", "price_sensitive",
                                 "irregular_schedule", "caregiver_rigid", "night_owl_fatigued"],
                        help="User persona (for agent/agent_pmv methods)")
    args = parser.parse_args()

    epw = CITY_EPW[args.city]
    if not epw.exists():
        print(f"ERROR: EPW not found: {epw}")
        sys.exit(1)
    if not IDF_PATH.exists():
        print(f"ERROR: IDF not found: {IDF_PATH}")
        sys.exit(1)

    out_dir = BENCH_DIR / "results_persona" / f"family_{args.method}_{args.city}_{args.persona}"
    print(f"\n{'='*60}")
    print(f"  EnergyBridge Single Persona Run")
    print(f"  Method:  {args.method}")
    print(f"  City:    {args.city}")
    print(f"  Persona: {args.persona}")
    print(f"  Output:  {out_dir}")
    print(f"{'='*60}\n")

    from family_runner import run_family_agent, run_family_pmv, run_family_agent_pmv

    if args.method == "pmv":
        result = run_family_pmv(
            idf_path=IDF_PATH, epw_path=epw,
            output_dir=out_dir, weather_label=args.city,
        )
    elif args.method == "agent":
        result = run_family_agent(
            idf_path=IDF_PATH, epw_path=epw,
            output_dir=out_dir, weather_label=args.city,
            persona_name=args.persona,
        )
    elif args.method == "agent_pmv":
        # agent_pmv doesn't have persona_name yet — use default
        result = run_family_agent_pmv(
            idf_path=IDF_PATH, epw_path=epw,
            output_dir=out_dir, weather_label=args.city,
        )

    # ── Print summary ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"  RESULTS — {args.method} / {args.city} / {args.persona}")
    print(f"{'='*60}")
    d = result.as_dict()
    for k, v in d.items():
        if k in ("control_decisions", "output_dir", "error"):
            continue
        print(f"  {k:<30s}: {v}")

    # ── Show dialogue log if it exists ─────────────────────────────────────
    diag_log = BENCH_DIR / "logs" / "dialogue" / f"family_agent_{args.persona}_{args.city}.jsonl"
    if diag_log.exists():
        print(f"\n{'─'*60}")
        print(f"  Dialogue log: {diag_log}")
        print(f"{'─'*60}")
        with open(diag_log) as f:
            for i, line in enumerate(f):
                try:
                    entry = json.loads(line)
                    t = entry.get("type", "?")
                    if t == "user_input":
                        print(f"\n  [User → Event {entry.get('event_index')}] "
                              f"({entry.get('source', '?')})")
                        print(f"    {entry.get('text', '')}")
                    elif t == "feedback":
                        sc = entry.get("scores", {})
                        print(f"\n  [Score Event {entry.get('event_index')}] "
                              f"overall={sc.get('overall')} comfort={sc.get('comfort')} "
                              f"energy={sc.get('energy')} vpp={sc.get('vpp')} "
                              f"| {entry.get('comment', '')[:60]}")
                except Exception:
                    pass
    else:
        print(f"\n  (No dialogue log found at {diag_log})")

    print(f"\nDone.\n")
    return result

if __name__ == "__main__":
    main()
