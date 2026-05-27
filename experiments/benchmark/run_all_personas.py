#!/usr/bin/env python3
"""Batch-run the family home benchmark across ALL persona JSON files.

Runs every persona in  energybridge/roleplay/personas/*.json  sequentially,
captures stdout+stderr to a dated log file per persona, and writes a JSON
summary when all personas complete.

Output layout
-------------
  logs/results_<YYYYMMDD>/
  ├── summary_<YYYYMMDD>.json          <- machine-readable summary
  ├── <persona_id>/
  │   ├── <persona_id>_log_<YYYYMMDD>.log   <- full console log
  │   └── eplus/                            <- EnergyPlus raw files
  └── ...

Usage
-----
  conda activate energybridge
  cd experiments/benchmark
  python3 run_all_personas.py
  python3 run_all_personas.py --results-dir /tmp/my_results --city Shanghai

Notes
-----
- A persona is skipped if its log already contains '[family/agent]' (useful
  for resuming after a crash). Use --no-skip to force re-run.
- EnergyPlus writes non-UTF-8 bytes; they are replaced with U+FFFD in the log.
"""
from __future__ import annotations
import argparse, json, subprocess, sys
from pathlib import Path
from datetime import date
from dotenv import load_dotenv

_BENCH_DIR    = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

PERSONA_DIR = _PROJECT_ROOT / "energybridge" / "roleplay" / "personas"
PYTHON      = sys.executable
RUNNER      = _BENCH_DIR / "run_persona_json.py"


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Batch-run family benchmark for all personas."
    )
    ap.add_argument(
        "--results-dir",
        default=None,
        help="Root folder for results. Defaults to <project_root>/logs/results_<YYYYMMDD>/",
    )
    ap.add_argument(
        "--city", "-c",
        default="Tianjin",
        choices=["Tianjin", "Beijing", "Shanghai"],
    )
    ap.add_argument(
        "--no-skip", action="store_true",
        help="Re-run personas even if their log already exists.",
    )
    return ap.parse_args()


def _extract_line(lines: list, keyword: str) -> str:
    return next((l.strip() for l in reversed(lines) if keyword in l), "N/A")


def main() -> None:
    args     = parse_args()
    date_str = date.today().strftime("%Y%m%d")

    results_dir = (
        Path(args.results_dir)
        if args.results_dir
        else _PROJECT_ROOT / "logs" / f"results_{date_str}"
    )
    results_dir.mkdir(parents=True, exist_ok=True)

    personas = sorted(PERSONA_DIR.glob("*.json"))
    if not personas:
        sys.exit(f"No persona JSON files found in {PERSONA_DIR}")

    all_summary = []

    for i, pf in enumerate(personas):
        persona_id = json.loads(pf.read_text(encoding="utf-8"))["id"]
        per_dir    = results_dir / persona_id
        eplus_dir  = per_dir / "eplus"   # family_runner rmtree's output_dir
        log_file   = per_dir / f"{persona_id}_log_{date_str}.log"

        if not args.no_skip and log_file.exists():
            text = log_file.read_text(encoding="utf-8", errors="replace")
            if "[family/agent]" in text:
                print(f"[{i+1}/{len(personas)}] SKIP (done): {persona_id}", flush=True)
                lines = text.splitlines()
                all_summary.append({
                    "persona_id": persona_id,
                    "agent": _extract_line(lines, "[family/agent]"),
                    "llm":   _extract_line(lines, "[LLM stats"),
                    "appl":  _extract_line(lines, "[Appl rules"),
                    "log":   str(log_file),
                })
                continue

        per_dir.mkdir(parents=True, exist_ok=True)
        print(f"\n{'='*70}", flush=True)
        print(f"[{i+1}/{len(personas)}] PERSONA: {persona_id}", flush=True)
        print(f"  log  -> {log_file}", flush=True)
        print(f"  data -> {eplus_dir}", flush=True)
        print(f"{'='*70}", flush=True)

        cmd = [PYTHON, "-u", str(RUNNER), persona_id,
               "--output", str(eplus_dir), "--city", args.city]

        with open(log_file, "w", encoding="utf-8", errors="replace", buffering=1) as lf:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            for raw_line in proc.stdout:
                line = raw_line.decode("utf-8", errors="replace")
                print(line, end="", flush=True)
                lf.write(line)
                lf.flush()
            proc.wait()

        text  = log_file.read_text(encoding="utf-8", errors="replace")
        lines = text.splitlines()
        agent_line = _extract_line(lines, "[family/agent]")
        print(f"\n  >> {agent_line}")
        all_summary.append({
            "persona_id": persona_id,
            "agent": agent_line,
            "llm":   _extract_line(lines, "[LLM stats"),
            "appl":  _extract_line(lines, "[Appl rules"),
            "log":   str(log_file),
        })

    summary_file = results_dir / f"summary_{date_str}.json"
    summary_file.write_text(
        json.dumps(all_summary, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print(f"\n{'='*70}")
    print(f"ALL DONE - {len(all_summary)} personas")
    print(f"Results folder : {results_dir}")
    print(f"Summary        : {summary_file}")


if __name__ == "__main__":
    main()
