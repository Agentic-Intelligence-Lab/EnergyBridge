#!/usr/bin/env python3
"""Accept-only shed-ratio pass-rate analyzer for capacity reporting evaluation.

Takes the summary JSON emitted by
``experiments/benchmark/dr_event_memory_library.py agent-report`` (each row is
a one-day EnergyBridge run enriched with ``agent_capacity_report_total_kwh``).
For every row this script reads the corresponding ``benchmark_result.json``
from the row's ``output_dir`` to pick up:

- ``vpp_plan_acceptance_rate``  (persona-aware consent gate outcome, 0.0 or
  1.0 for single-event days; fractional for multi-event days)
- per-event ``actual_shed_kwh``  (no_dr counterfactual baseline minus event
  actual, aggregated per result file)

Events are then split by the gate outcome and the shed-ratio pass rate at
the 0.8-1.2 band is reported separately for accepted, rejected, and mixed
days. The accepted-only pass rate is the primary signal for RAG reporting
quality since rejected events fall back to a rule-based comfort routine
which is intentionally opaque to the retrieval memory.
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from statistics import mean


def _load_summary_rows(path: Path) -> list[dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise SystemExit(f"Summary JSON must be a list of row dicts: {path}")
    return data


def _classify_gate(rate) -> str:
    if rate is None:
        return "no_gate"
    try:
        r = float(rate)
    except (TypeError, ValueError):
        return "no_gate"
    if math.isclose(r, 1.0, abs_tol=1e-9):
        return "accepted"
    if math.isclose(r, 0.0, abs_tol=1e-9):
        return "rejected"
    return "mixed"


def _enrich_row(row: dict) -> dict:
    """Attach vpp_plan_acceptance_rate + actual_shed_kwh from benchmark_result.json."""
    out = dict(row)
    output_dir = row.get("output_dir")
    if not output_dir:
        out["_gate_status"] = "missing_output_dir"
        out["_actual_shed_kwh"] = None
        return out
    result_path = Path(output_dir) / "benchmark_result.json"
    if not result_path.exists():
        out["_gate_status"] = "missing_result_file"
        out["_actual_shed_kwh"] = None
        return out
    result = json.loads(result_path.read_text(encoding="utf-8"))
    rate = result.get("vpp_plan_acceptance_rate")
    out["_acceptance_rate"] = rate
    out["_gate_status"] = _classify_gate(rate)

    # aggregate actual_shed across events in this run
    events = result.get("vpp_event_log") or []
    shed_total = 0.0
    have_any = False
    for evt in events:
        v = evt.get("actual_shed_kwh")
        if v is None:
            continue
        try:
            shed_total += float(v)
            have_any = True
        except (TypeError, ValueError):
            continue
    out["_actual_shed_kwh"] = round(shed_total, 6) if have_any else None
    return out


def _ratio(row: dict) -> float | None:
    reported = row.get("agent_capacity_report_total_kwh")
    actual = row.get("_actual_shed_kwh")
    if reported is None or actual is None:
        return None
    try:
        reported = float(reported)
        actual = float(actual)
    except (TypeError, ValueError):
        return None
    if actual <= 1e-9:
        return None
    return reported / actual


def _summarize(rows: list[dict], name: str) -> dict:
    ratios = [_ratio(r) for r in rows]
    valid = [x for x in ratios if x is not None]
    passed = [x for x in valid if 0.8 <= x <= 1.2]
    devs = [abs(v - 1.0) for v in valid]

    errors = []
    sq_errors = []
    for r in rows:
        rep = r.get("agent_capacity_report_total_kwh")
        act = r.get("_actual_shed_kwh")
        if rep is None or act is None:
            continue
        try:
            e = float(rep) - float(act)
        except (TypeError, ValueError):
            continue
        errors.append(abs(e))
        sq_errors.append(e * e)

    return {
        "name": name,
        "n_total": len(rows),
        "n_valid_ratio": len(valid),
        "n_pass": len(passed),
        "n_out_of_band": len(valid) - len(passed),
        "pass_rate_pct": round(len(passed) / len(valid) * 100, 2) if valid else None,
        "mean_ratio": round(mean(valid), 4) if valid else None,
        "std_ratio": round(statistics.stdev(valid), 4) if len(valid) > 1 else None,
        "mean_abs_dev_from_1": round(mean(devs), 4) if devs else None,
        "mae_kwh": round(mean(errors), 4) if errors else None,
        "rmse_kwh": round((sum(sq_errors) / len(sq_errors)) ** 0.5, 4) if sq_errors else None,
        "min_ratio": round(min(valid), 4) if valid else None,
        "max_ratio": round(max(valid), 4) if valid else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--summary-json",
        required=True,
        help=(
            "Summary JSON produced by dr_event_memory_library.py agent-report "
            "(each row must contain output_dir and agent_capacity_report_total_kwh)."
        ),
    )
    ap.add_argument(
        "--method",
        default="EnergyBridge",
        help="Restrict analysis to rows with this method (default: EnergyBridge).",
    )
    ap.add_argument("--output-json", required=True)
    args = ap.parse_args()

    all_rows = _load_summary_rows(Path(args.summary_json))
    rows = [r for r in all_rows if str(r.get("method") or "") == args.method]
    rows = [_enrich_row(r) for r in rows]

    buckets: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        buckets[r["_gate_status"]].append(r)

    summary = {
        "input_summary_json": args.summary_json,
        "method": args.method,
        "n_rows": len(rows),
        "counts_by_gate_status": {k: len(v) for k, v in buckets.items()},
        "overall_all_events": _summarize(rows, "overall_all_events"),
        "accepted_only": _summarize(buckets.get("accepted", []), "accepted_only (rate==1.0)"),
        "rejected_only": _summarize(buckets.get("rejected", []), "rejected_only (rate==0.0)"),
        "mixed_days": _summarize(buckets.get("mixed", []), "mixed_partial_acceptance"),
    }

    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[INPUT ] {args.summary_json}")
    print(f"         method={args.method}  rows={len(rows)}")
    print()
    print("Gate outcome buckets:")
    for status in sorted(buckets):
        print(f"  {status:<22} n={len(buckets[status])}")
    print()
    print("=" * 72)
    for key in ("overall_all_events", "accepted_only", "rejected_only", "mixed_days"):
        s = summary[key]
        print(f"[{s['name']}]")
        print(f"  n_total={s['n_total']}  n_valid={s['n_valid_ratio']}")
        if s["pass_rate_pct"] is not None:
            print(f"  pass rate (0.8-1.2) : {s['pass_rate_pct']}%  ({s['n_pass']}/{s['n_valid_ratio']})")
            print(f"  mean ratio          : {s['mean_ratio']}   (min={s['min_ratio']}, max={s['max_ratio']})")
            print(f"  std ratio           : {s['std_ratio']}")
            print(f"  |ratio - 1| mean    : {s['mean_abs_dev_from_1']}")
            print(f"  MAE (kWh)           : {s['mae_kwh']}")
            print(f"  RMSE (kWh)          : {s['rmse_kwh']}")
        print()
    print(f"[OK] wrote {args.output_json}")


if __name__ == "__main__":
    main()
