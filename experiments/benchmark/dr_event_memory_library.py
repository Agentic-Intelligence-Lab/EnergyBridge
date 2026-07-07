#!/usr/bin/env python3
"""Build/apply historical DR event memory for capacity reporting.

This is the reporting-side companion to ``counterfactual_baseline_library.py``.
The counterfactual library answers "what was actually delivered after the
event?"  This memory library answers "what should we report for a future event
given similar historical deliveries?"
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path
from typing import Any

_BENCH_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from energybridge.quantification.dr_event_memory import (  # noqa: E402
    apply_dr_memory_capacity_estimate,
    build_dr_event_memory,
)
from energybridge.quantification.agent_capacity_reporter import (  # noqa: E402
    apply_agent_capacity_reporting,
)


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_summary_rows(paths: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in paths:
        path = Path(raw)
        data = _read_json(path)
        if not isinstance(data, list):
            raise SystemExit(f"Summary must be a JSON list: {path}")
        for row in data:
            if isinstance(row, dict):
                copied = dict(row)
                copied["_summary_source"] = str(path)
                rows.append(copied)
    return rows


def _result_path_from_row(row: dict[str, Any]) -> Path | None:
    output_dir = row.get("output_dir")
    if not output_dir:
        return None
    candidate = Path(str(output_dir)) / "benchmark_result.json"
    return candidate if candidate.exists() else None


def _csv_fieldnames(rows: list[dict[str, Any]]) -> list[str]:
    fields: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fields and not key.startswith("_"):
                fields.append(key)
    return fields


def _write_summary_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    fields = _csv_fieldnames(rows)
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: value for key, value in row.items() if key in fields})


def _update_row_from_estimate(row: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    updated = dict(row)
    for key in (
        "historical_dr_memory_status",
        "historical_dr_memory_event_count",
        "historical_dr_memory_reported_capacity_total_kwh",
        "historical_dr_memory_reported_capacity_avg_kw",
        "historical_dr_memory_basis",
    ):
        if key in result:
            updated[key] = result[key]
    return updated


def _update_row_from_agent_report(row: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    updated = dict(row)
    for key in (
        "agent_capacity_report_status",
        "agent_capacity_report_event_count",
        "agent_capacity_report_total_kwh",
        "agent_capacity_report_avg_kw",
        "agent_capacity_report_basis",
        "agent_capacity_report_distribution_positions",
        "agent_capacity_report_primary_distribution_position",
        "agent_capacity_report_distribution_position_counts",
        "agent_capacity_report_choices",
        "agent_capacity_report_primary_choice",
        "agent_capacity_report_choice_counts",
    ):
        if key in result:
            updated[key] = result[key]
    return updated


def cmd_generate_events(args: argparse.Namespace) -> None:
    rng = random.Random(args.seed)
    hour_pool = [float(item) for item in args.hours]
    duration_pool = [float(item) for item in args.durations]
    events: list[dict[str, Any]] = []
    for day in range(1, int(args.days) + 1):
        for seq in range(int(args.events_per_day)):
            start_h = rng.choice(hour_pool)
            duration_h = rng.choice(duration_pool)
            if start_h + duration_h > 24.0:
                start_h = 24.0 - duration_h
            events.append(
                {
                    "id": f"hist_d{day:02d}_{seq + 1}",
                    "day": day,
                    "start_h": round(start_h, 3),
                    "duration_h": round(duration_h, 3),
                    "label": f"historical memory event day {day}",
                    "source": "generated_historical_dr_memory",
                }
            )
    payload = {
        "description": (
            "Generated historical DR events for capacity-reporting memory. "
            "Use this schedule for both no_dr and EnergyBridge runs."
        ),
        "seed": args.seed,
        "days": args.days,
        "events": events,
    }
    _write_json(Path(args.output), payload)
    print(f"[OK] wrote {args.output} events={len(events)}")


def cmd_build(args: argparse.Namespace) -> None:
    wanted_methods = [method.lower() for method in args.methods]
    items: list[tuple[dict[str, Any], dict[str, Any], Path | None]] = []
    for row in _load_summary_rows(args.summary_json):
        method = str(row.get("method") or "").lower()
        if wanted_methods and method not in wanted_methods:
            continue
        result_path = _result_path_from_row(row)
        if result_path is None:
            print(f"[WARN] result missing for row: {row.get('output_dir')}", file=sys.stderr)
            continue
        items.append((_read_json(result_path), row, result_path))
    for raw in args.result_json:
        path = Path(raw)
        result = _read_json(path)
        items.append((result, {"method": result.get("method", wanted_methods[0] if wanted_methods else "")}, path))
    memory = build_dr_event_memory(items, methods=wanted_methods)
    _write_json(Path(args.output), memory)
    print(
        f"[OK] wrote {args.output} events={len(memory.get('events') or [])} "
        f"methods={','.join(memory.get('summary', {}).get('methods', []))}"
    )


def cmd_estimate(args: argparse.Namespace) -> None:
    memory = _read_json(Path(args.memory))
    rows = _load_summary_rows(args.summary_json)
    wanted_methods = {str(method).lower() for method in args.methods}
    out_rows: list[dict[str, Any]] = []
    matched = 0
    missing = 0
    for row in rows:
        if wanted_methods and str(row.get("method") or "").lower() not in wanted_methods:
            out_rows.append(row)
            continue
        result_path = _result_path_from_row(row)
        if result_path is None:
            out_rows.append(row)
            missing += 1
            continue
        result = _read_json(result_path)
        updated_result = apply_dr_memory_capacity_estimate(
            result,
            memory,
            metadata=row,
            top_k=args.top_k,
            factor_cap=args.factor_cap,
        )
        if updated_result.get("historical_dr_memory_event_count", 0):
            matched += 1
        else:
            missing += 1
        if args.write_result_json:
            _write_json(result_path, updated_result)
        out_rows.append(_update_row_from_estimate(row, updated_result))

    output_json = Path(args.output_summary_json)
    output_csv = Path(args.output_summary_csv) if args.output_summary_csv else output_json.with_suffix(".csv")
    clean_rows = [{key: value for key, value in row.items() if not key.startswith("_")} for row in out_rows]
    _write_json(output_json, clean_rows)
    _write_summary_csv(output_csv, clean_rows)
    print(
        f"[OK] estimated matched={matched} missing={missing} "
        f"summary={output_json} csv={output_csv}"
    )


def cmd_agent_report(args: argparse.Namespace) -> None:
    memory = _read_json(Path(args.memory))
    rows = _load_summary_rows(args.summary_json)
    wanted_methods = {str(method).lower() for method in args.methods}
    out_rows: list[dict[str, Any]] = []
    matched = 0
    missing = 0
    for row in rows:
        if wanted_methods and str(row.get("method") or "").lower() not in wanted_methods:
            out_rows.append(row)
            continue
        result_path = _result_path_from_row(row)
        if result_path is None:
            out_rows.append(row)
            missing += 1
            continue
        result = _read_json(result_path)
        updated_result = apply_agent_capacity_reporting(
            result,
            memory,
            metadata=row,
            top_k=args.top_k,
            dry_run=args.dry_run,
        )
        if updated_result.get("agent_capacity_report_event_count", 0):
            matched += 1
        else:
            missing += 1
        if args.write_result_json:
            _write_json(result_path, updated_result)
        out_rows.append(_update_row_from_agent_report(row, updated_result))

    output_json = Path(args.output_summary_json)
    output_csv = Path(args.output_summary_csv) if args.output_summary_csv else output_json.with_suffix(".csv")
    clean_rows = [{key: value for key, value in row.items() if not key.startswith("_")} for row in out_rows]
    _write_json(output_json, clean_rows)
    _write_summary_csv(output_csv, clean_rows)
    print(
        f"[OK] agent-reported matched={matched} missing={missing} "
        f"dry_run={args.dry_run} summary={output_json} csv={output_csv}"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser("generate-events", help="Generate a reusable historical DR event schedule.")
    gen.add_argument("--days", type=int, default=30)
    gen.add_argument("--events-per-day", type=int, default=1)
    gen.add_argument("--hours", nargs="+", default=["16.0", "17.0", "18.0", "19.0", "20.0"])
    gen.add_argument("--durations", nargs="+", default=["1.0"])
    gen.add_argument("--seed", type=int, default=20260630)
    gen.add_argument("--output", required=True)
    gen.set_defaults(func=cmd_generate_events)

    build = sub.add_parser("build", help="Build memory from calibrated EnergyBridge result(s).")
    build.add_argument("--summary-json", nargs="*", default=[], help="Matrix summary JSON(s).")
    build.add_argument("--result-json", nargs="*", default=[], help="Standalone benchmark_result.json path(s).")
    build.add_argument("--methods", nargs="+", default=["EnergyBridge"], help="Methods to include.")
    build.add_argument("--output", required=True)
    build.set_defaults(func=cmd_build)

    estimate = sub.add_parser("estimate", help="Apply historical DR memory estimates to a target summary.")
    estimate.add_argument("--memory", required=True)
    estimate.add_argument("--summary-json", nargs="+", required=True)
    estimate.add_argument("--output-summary-json", required=True)
    estimate.add_argument("--output-summary-csv", default="")
    estimate.add_argument("--top-k", type=int, default=5)
    estimate.add_argument("--factor-cap", type=float, default=2.0)
    estimate.add_argument("--methods", nargs="*", default=[], help="Optional target methods to annotate.")
    estimate.add_argument("--write-result-json", action="store_true")
    estimate.set_defaults(func=cmd_estimate)

    agent = sub.add_parser("agent-report", help="Use top-k memory distribution as compact LLM context for capacity reporting.")
    agent.add_argument("--memory", required=True)
    agent.add_argument("--summary-json", nargs="+", required=True)
    agent.add_argument("--output-summary-json", required=True)
    agent.add_argument("--output-summary-csv", default="")
    agent.add_argument("--top-k", type=int, default=5, help="Default is top-5 to provide a compact delivery distribution.")
    agent.add_argument("--methods", nargs="*", default=["EnergyBridge"], help="Target methods to annotate.")
    agent.add_argument("--dry-run", action="store_true", help="Use deterministic calibrated choice without LLM calls.")
    agent.add_argument("--write-result-json", action="store_true")
    agent.set_defaults(func=cmd_agent_report)

    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
