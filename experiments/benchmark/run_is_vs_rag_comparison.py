#!/usr/bin/env python3
"""Apples-to-apples comparison of pure-RAG vs importance-sampling reweighted
capacity reporting.

For every event in a target summary this script:
  1. Retrieves the top-k neighbors from the memory library via
     ``estimate_event_capacity_from_memory`` (identical retrieval to the
     production agent_capacity_reporter -- retrieval stage is not modified).
  2. Aggregates the neighbors' ``realized_delivery_kw`` two ways:
       - **pure RAG**   = unweighted mean of top-k
       - **IS weighted**= weighted mean of top-k, weights taken from a PR #20
         ``weights_package_*.json`` (defaults to 1.0 for records not listed in
         the weight package, e.g. Tianjin rows).
  3. Compares each version's ``reported / actual_shed`` ratio against the
     0.8-1.2 pass-rate band, split by the persona-aware acceptance gate.

The LLM band choice from ``apply_agent_capacity_reporting`` is intentionally
skipped so that the two versions differ only in the retrieval-aggregation
step, isolating the effect of importance-sampling reweighting.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

_BENCH_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from energybridge.quantification.dr_event_memory import (  # noqa: E402
    estimate_event_capacity_from_memory,
)


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _attach_is_weights(memory_events: list[dict], weights_pkg: dict) -> dict[str, float]:
    """Attach per-record IS weight to each memory event (in-place).

    Weights come from ``weights_pkg["per_event_weights"]``, keyed on
    ``memory_event_id``. Records not present in the package (e.g. Tianjin
    rows when only a Germany weights package is available) default to 1.0.
    """
    per_event = weights_pkg.get("per_event_weights") or []
    weight_map: dict[str, float] = {}
    for entry in per_event:
        mid = entry.get("memory_event_id")
        raw = entry.get("raw_weight")
        if mid is None or raw is None:
            continue
        try:
            weight_map[str(mid)] = float(raw)
        except (TypeError, ValueError):
            continue
    for event in memory_events:
        event["is_weight"] = weight_map.get(event.get("memory_event_id", ""), 1.0)
    return weight_map


def _metadata_for_row(row: dict) -> dict:
    return {
        "household_id": row.get("household_id") or row.get("persona_id"),
        "persona_id": row.get("persona_id") or row.get("household_id"),
        "city": row.get("city"),
        "method": row.get("method"),
        "start_date": row.get("start_date"),
    }


def _row_result_path(row: dict) -> Path | None:
    output_dir = row.get("output_dir")
    if not output_dir:
        return None
    candidate = Path(str(output_dir)) / "benchmark_result.json"
    return candidate if candidate.exists() else None


def _weighted_mean(values: list[float], weights: list[float]) -> float:
    if not values:
        return 0.0
    total_w = sum(weights)
    if total_w <= 0:
        return statistics.mean(values)
    return sum(v * w for v, w in zip(values, weights)) / total_w


def _pass(x: float | None) -> bool:
    return x is not None and 0.8 <= x <= 1.2


def _bucket_stats(rows: list[dict], ratio_key: str, reported_key: str) -> dict:
    ratios = [r[ratio_key] for r in rows if r.get(ratio_key) is not None]
    passed = [v for v in ratios if 0.8 <= v <= 1.2]
    devs = [abs(v - 1.0) for v in ratios]

    errors = []
    sq_errors = []
    for r in rows:
        rep = r.get(reported_key)
        act = r.get("actual_shed_kwh")
        if rep is None or act is None:
            continue
        try:
            err = float(rep) - float(act)
        except (TypeError, ValueError):
            continue
        errors.append(abs(err))
        sq_errors.append(err * err)

    return {
        "n": len(ratios),
        "pass": len(passed),
        "pass_pct": round(len(passed) / len(ratios) * 100, 2) if ratios else None,
        "mean_ratio": round(statistics.mean(ratios), 4) if ratios else None,
        "std_ratio": round(statistics.stdev(ratios), 4) if len(ratios) > 1 else None,
        "mean_abs_dev_from_1": round(statistics.mean(devs), 4) if devs else None,
        "mae_kwh": round(statistics.mean(errors), 4) if errors else None,
        "rmse_kwh": round((sum(sq_errors) / len(sq_errors)) ** 0.5, 4) if sq_errors else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--memory", required=True, help="Memory library JSON (v2 weather 285-event library or equivalent).")
    ap.add_argument("--is-weights-json", required=True, help="PR #20 weights_package_*.json.")
    ap.add_argument(
        "--summary-json",
        required=True,
        help="Target summary JSON from dr_event_memory_library.py agent-report (rows must have output_dir).",
    )
    ap.add_argument("--method", default="EnergyBridge")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--output-json", required=True)
    args = ap.parse_args()

    memory_payload = _load_json(Path(args.memory))
    memory_events = memory_payload.get("events") if isinstance(memory_payload, dict) else memory_payload
    if not memory_events:
        raise SystemExit(f"Memory JSON has no 'events' list: {args.memory}")
    weights_pkg = _load_json(Path(args.is_weights_json))
    weight_map = _attach_is_weights(memory_events, weights_pkg)
    memory = {"events": memory_events}
    id_to_weight = {e.get("memory_event_id"): e.get("is_weight", 1.0) for e in memory_events}
    print(f"[info] memory events: {len(memory_events)}  IS-weighted: {len(weight_map)}", file=sys.stderr)

    summary_rows = _load_json(Path(args.summary_json))
    if not isinstance(summary_rows, list):
        raise SystemExit("--summary-json must contain a list of row dicts")
    summary_rows = [r for r in summary_rows if str(r.get("method") or "") == args.method]
    print(f"[info] target summary rows (method={args.method}): {len(summary_rows)}", file=sys.stderr)

    out_rows = []
    skipped = 0
    for idx, row in enumerate(summary_rows, start=1):
        result_path = _row_result_path(row)
        if result_path is None:
            skipped += 1
            continue
        result = _load_json(result_path)
        gate_rate = result.get("vpp_plan_acceptance_rate")
        metadata = _metadata_for_row(row)
        for evt in result.get("vpp_event_log") or []:
            actual_shed = evt.get("actual_shed_kwh")
            duration = 1.0
            try:
                duration = float(evt.get("end_h") or 0) - float(evt.get("trigger_h") or 0)
                if duration <= 0:
                    duration = 1.0
            except Exception:
                duration = 1.0
            estimate = estimate_event_capacity_from_memory(
                evt, memory, result=result, metadata=metadata, top_k=args.top_k
            )
            neighbors = estimate.get("retrieved_events")
            if neighbors is None:
                raise RuntimeError(
                    "estimate_event_capacity_from_memory() no longer returns a 'retrieved_events' "
                    "list; this script depends on that key. Update the aggregation logic if the "
                    "reporter's public shape changed."
                )
            kws, wts = [], []
            for nb in neighbors:
                kw = nb.get("realized_delivery_kw")
                if kw is None:
                    continue
                w = id_to_weight.get(nb.get("memory_event_id", ""), 1.0)
                kws.append(float(kw))
                wts.append(float(w))
            if kws:
                rag_kwh = statistics.mean(kws) * duration
                is_kwh = _weighted_mean(kws, wts) * duration
            else:
                rag_kwh, is_kwh = None, None

            ratio_rag = (rag_kwh / actual_shed) if (rag_kwh is not None and actual_shed and actual_shed > 1e-9) else None
            ratio_is = (is_kwh / actual_shed) if (is_kwh is not None and actual_shed and actual_shed > 1e-9) else None

            out_rows.append({
                "household_id": metadata["household_id"],
                "city": metadata["city"],
                "start_date": metadata["start_date"],
                "event_id": evt.get("id"),
                "trigger_h": evt.get("trigger_h"),
                "vpp_plan_acceptance_rate": gate_rate,
                "actual_shed_kwh": actual_shed,
                "n_neighbors": len(kws),
                "reported_rag_kwh": round(rag_kwh, 6) if rag_kwh is not None else None,
                "reported_is_kwh": round(is_kwh, 6) if is_kwh is not None else None,
                "ratio_rag": round(ratio_rag, 4) if ratio_rag is not None else None,
                "ratio_is": round(ratio_is, 4) if ratio_is is not None else None,
                "source_result_path": str(result_path),
            })
        if idx % 25 == 0:
            print(f"[progress] {idx}/{len(summary_rows)}", file=sys.stderr)

    Path(args.output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output_json).write_text(json.dumps(out_rows, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[OK] wrote {args.output_json} rows={len(out_rows)} skipped_missing_result={skipped}", file=sys.stderr)

    accepted = [r for r in out_rows if r["vpp_plan_acceptance_rate"] == 1.0]
    rejected = [r for r in out_rows if r["vpp_plan_acceptance_rate"] == 0.0]

    print()
    print("=" * 92)
    print("pure-RAG (unweighted mean of top-k)  vs  IS-weighted (weighted mean of top-k)")
    print("=" * 92)
    for name, group in [("overall", out_rows), ("accepted-only", accepted), ("rejected-only", rejected)]:
        s_rag = _bucket_stats(group, "ratio_rag", "reported_rag_kwh")
        s_is = _bucket_stats(group, "ratio_is", "reported_is_kwh")
        print(f"\n[{name}]  n={s_rag['n']}")
        print(f"  {'metric':<24}{'pure-RAG':<18}{'IS-weighted':<18}")
        for key, label in [
            ("pass_pct", "pass rate % (0.8-1.2)"),
            ("mean_ratio", "mean ratio"),
            ("std_ratio", "std ratio"),
            ("mean_abs_dev_from_1", "|ratio - 1| mean"),
            ("mae_kwh", "MAE (kWh)"),
            ("rmse_kwh", "RMSE (kWh)"),
        ]:
            print(f"  {label:<24}{str(s_rag[key]):<18}{str(s_is[key]):<18}")


if __name__ == "__main__":
    main()
