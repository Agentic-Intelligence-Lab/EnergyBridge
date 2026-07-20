#!/usr/bin/env python3
"""Leave-one-out self-evaluation of a DR event memory library.

For every event in the memory library this script:
  1. removes that single event from the pool (leave-one-out exclusion),
  2. synthesizes a target-side event dict from the memory record,
  3. runs ``apply_agent_capacity_reporting`` against the reduced memory,
  4. compares the reported capacity to the record's own realized delivery.

The script is self-contained: it only needs the memory library JSON to run,
which makes it a reproducible baseline check for the June 2025 memory
without needing the original per-day ``benchmark_result.json`` files.

Use ``--dry-run`` to run the deterministic P70 selection instead of the
LLM band choice; that keeps a full 285-event self-evaluation to a few
seconds and is what the committed baseline numbers were produced with.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_BENCH_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from energybridge.quantification.agent_capacity_reporter import (  # noqa: E402
    apply_agent_capacity_reporting,
)


def _load_memory_events(path: Path) -> list[dict]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    events = payload.get("events") if isinstance(payload, dict) else payload
    if not isinstance(events, list):
        raise SystemExit(f"Memory JSON must contain an 'events' list: {path}")
    return events


def _synthesize_query_event(record: dict) -> dict:
    """Build a target-side event dict from a memory record.

    Only the fields that ``estimate_event_capacity_from_memory`` and its
    callers read from an event are populated. Missing optional fields are
    left absent so the retrieval falls back to its own defaults.
    """
    query = {
        "id": record.get("event_id") or record.get("memory_event_id"),
        "trigger_h": record.get("trigger_h"),
        "end_h": record.get("end_h"),
        "hour_of_day": record.get("hour_of_day"),
        "duration_h": record.get("duration_h"),
        "day": record.get("day"),
        "actual_kwh": record.get("actual_kwh"),
        "counterfactual_baseline_kwh": record.get("no_dr_baseline_kwh"),
        "counterfactual_actual_shed_kwh": record.get("realized_delivery_kwh"),
        "actual_shed_kwh": record.get("realized_delivery_kwh"),
        "actual_shed_basis": "loo_self_evaluation_from_memory_record",
    }
    return {k: v for k, v in query.items() if v is not None}


def _metadata_from_record(record: dict) -> dict:
    return {
        "household_id": record.get("household_id") or record.get("entity_id"),
        "persona_id": record.get("persona_id") or record.get("household_id") or record.get("entity_id"),
        "city": record.get("city"),
        "method": record.get("method"),
        "start_date": record.get("start_date"),
    }


def _run_single_loo(record: dict, all_events: list[dict], *, top_k: int, dry_run: bool) -> dict:
    key = record.get("memory_event_id")
    if not key:
        raise ValueError(
            "Memory record missing 'memory_event_id'; LOO exclusion requires a stable per-event id."
        )
    loo_memory = {"events": [e for e in all_events if e.get("memory_event_id") != key]}
    metadata = _metadata_from_record(record)
    query_event = _synthesize_query_event(record)
    mock_result = {
        "vpp_event_log": [query_event],
        "start_date": metadata["start_date"],
        "city": metadata["city"],
        "method": metadata["method"],
    }
    updated = apply_agent_capacity_reporting(
        mock_result, loo_memory, metadata=metadata, top_k=top_k, dry_run=dry_run,
    )
    reported_total_kwh = updated.get("agent_capacity_report_total_kwh")
    actual_shed = query_event.get("actual_shed_kwh")
    ratio = None
    if reported_total_kwh is not None and actual_shed and actual_shed > 1e-9:
        ratio = round(float(reported_total_kwh) / float(actual_shed), 4)
    out = dict(metadata)
    out.update(
        {
            "memory_event_id": key,
            "event_id": record.get("event_id"),
            "trigger_h": record.get("trigger_h"),
            "hour_of_day": record.get("hour_of_day"),
            "actual_shed_kwh": round(actual_shed, 6) if actual_shed is not None else None,
            "agent_capacity_report_status": updated.get("agent_capacity_report_status"),
            "agent_capacity_report_event_count": updated.get("agent_capacity_report_event_count"),
            "agent_capacity_report_total_kwh": (
                round(float(reported_total_kwh), 6) if reported_total_kwh is not None else None
            ),
            "reported_over_actual_ratio": ratio,
        }
    )
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--memory-json", required=True, help="Path to the memory library JSON to self-evaluate.")
    ap.add_argument("--output-json", required=True, help="Where to write the per-event LOO results.")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Use deterministic P70 band choice instead of LLM (fast; matches committed baseline).",
    )
    ap.add_argument("--limit", type=int, default=0, help="Debug: cap number of records processed.")
    args = ap.parse_args()

    events = _load_memory_events(Path(args.memory_json))
    if args.limit:
        events = events[: args.limit]
    print(f"[info] memory pool: {len(events)} events", file=sys.stderr)

    out_rows = []
    for idx, record in enumerate(events, start=1):
        row = _run_single_loo(record, events, top_k=args.top_k, dry_run=args.dry_run)
        out_rows.append(row)
        if idx % 25 == 0 or idx == len(events):
            print(
                f"[progress] {idx}/{len(events)}  latest ratio={row.get('reported_over_actual_ratio')}",
                file=sys.stderr,
            )

    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(
        json.dumps(out_rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    valid = [r for r in out_rows if r.get("reported_over_actual_ratio") is not None]
    passed = [r for r in valid if 0.8 <= r["reported_over_actual_ratio"] <= 1.2]
    pass_rate = (len(passed) / len(valid) * 100) if valid else 0.0
    print(
        f"[OK] wrote {args.output_json} "
        f"n={len(out_rows)} valid={len(valid)} "
        f"pass_rate_0.8-1.2={pass_rate:.2f}%"
    )


if __name__ == "__main__":
    main()
