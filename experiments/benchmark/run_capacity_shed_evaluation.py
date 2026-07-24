#!/usr/bin/env python3
"""Unified capacity-shed evaluation, generalized across all five DR methods
(EnergyBridge / rl_ppo_pref_v2 / rule_milp / mpc_dynamic / hema_agent).

Replaces the ad-hoc, method-specific scripts that used to live in
experiments/benchmark/ (analyze_shed_ratio_accept_only.py,
run_agent_capacity_report_loo.py, run_is_vs_rag_comparison.py -- all now
deleted) and the pile of one-off variants that accumulated under analysis/
(run_agent_capacity_report_loo.py / _v2.py / _july.py, eb_real_llm_capture.py,
run_is_weighted_quantile_comparison.py) while iterating on the methodology.

Host-independent by design: every query event is rebuilt from the per-event
memory record's own fields (trigger_h/end_h/hour_of_day/duration_h/day/
actual_kwh/no_dr_baseline_kwh/realized_delivery_kwh) instead of re-reading an
external benchmark_result.json via output_dir/source_result_path. That path
only exists for EnergyBridge (dr_event_memory_library.py's agent-report
subcommand relies on it) -- MPC/HEMA's original simulation output never left
the Windows machine that produced it, and even for EB/RL/rule_milp regenerating
from disk is unnecessary extra I/O. Every dr_capacity_memory_toolkit/*/data/
*_daily_dr_memory.json file carries everything this script needs on its own.

Two evaluation modes, picked automatically from --pool-month/--query-month:
  - leave-one-out (pool-month == query-month): each event is scored against
    the rest of its own month's library with itself excluded.
  - cross-month self-to-self (pool-month != query-month): events in
    query-month are scored against the full pool-month library of the same
    method.

Examples:
  # June leave-one-out self-check with a real LLM call per event choosing the
  # p50/p70/p90 band.
  python run_capacity_shed_evaluation.py --method rl --pool-month june --query-month june \\
      --output /tmp/rl_june_loo.json

  # Same, but --dry-run skips the LLM and deterministically picks p70 --
  # useful for checking the retrieval math before spending on the real run.
  python run_capacity_shed_evaluation.py --method rl --pool-month june --query-month june \\
      --dry-run --output /tmp/rl_june_loo_dry.json

  # July held-out using June as the retrieval pool, real LLM band choice.
  python run_capacity_shed_evaluation.py --method eb --pool-month june --query-month july \\
      --output analysis/eb_july_self_to_self.json

  # Same, plus IS-weighted quantile correction on top (Germany/Tianjin only --
  # importance_sampling/IS_result only has June->July weight packages for those two)
  python run_capacity_shed_evaluation.py --method eb --pool-month june --query-month july \\
      --apply-is --output analysis/eb_july_self_to_self_is.json
"""
from __future__ import annotations

import argparse
import json
import statistics as st
import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from energybridge.quantification.agent_capacity_reporter import (  # noqa: E402
    report_event_capacity_with_agent,
)

TOOLKIT_ROOT = PROJECT_ROOT / "dr_capacity_memory_toolkit"
TOP_K = 5
QUANTILE_VALUE = {"p50": 0.50, "p70": 0.70, "p90": 0.90}

METHOD_MEMORY_FILE = {
    "eb": {
        "june": "energybridge_daily_dr_memory_rag_v2_weather.json",
        "july": "energybridge_daily_dr_memory.json",
    },
    "rl": {"june": "rl_ppo_pref_v2_daily_dr_memory.json", "july": "rl_ppo_pref_v2_daily_dr_memory.json"},
    "rule_milp": {"june": "rule_milp_daily_dr_memory.json", "july": "rule_milp_daily_dr_memory.json"},
    "mpc": {"june": "mpc_dynamic_daily_dr_memory.json", "july": "mpc_dynamic_daily_dr_memory.json"},
    "hema": {"june": "hema_agent_daily_dr_memory.json", "july": "hema_agent_daily_dr_memory.json"},
}

_print_lock = threading.Lock()


def _memory_path(method: str, month: str) -> Path:
    filename = METHOD_MEMORY_FILE[method][month]
    return TOOLKIT_ROOT / f"{month}_2025_daily_{method}" / "data" / filename


def _load_events(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text())
    events = data.get("events") if isinstance(data, dict) else data
    return list(events or [])


def _build_query_event(record: dict[str, Any]) -> dict[str, Any]:
    query = {
        "id": record.get("event_id") or record.get("memory_event_id"),
        "day": record.get("day"),
        "trigger_h": record.get("trigger_h"),
        "end_h": record.get("end_h"),
        "hour_of_day": record.get("hour_of_day"),
        "duration_h": record.get("duration_h"),
        "counterfactual_baseline_kwh": record.get("no_dr_baseline_kwh"),
        "actual_shed_kwh": record.get("realized_delivery_kwh"),
        "actual_shed_basis": "self_contained_memory_record_replay",
    }
    return {key: value for key, value in query.items() if value is not None}


def _build_metadata(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "household_id": record.get("household_id") or record.get("entity_id"),
        "persona_id": record.get("persona_id") or record.get("household_id"),
        "city": record.get("city"),
        "method": record.get("method"),
        "start_date": record.get("start_date"),
    }


def _extract_report_row(report: dict[str, Any]) -> dict[str, Any]:
    estimate = report.get("deterministic_memory_estimate") or {}
    has_target_baseline = estimate.get("target_baseline_kwh") is not None
    neighbors = []
    for nb in estimate.get("retrieved_events") or []:
        val = nb.get("baseline_adjusted_delivery_kw") if has_target_baseline else nb.get("realized_delivery_kw")
        if val is None:
            continue
        neighbors.append({"memory_event_id": str(nb.get("memory_event_id") or ""), "value_kw": float(val)})
    return {
        "recommended_quantile": report.get("recommended_quantile"),
        "reported_capacity_kwh": report.get("reported_capacity_kwh"),
        "duration_h": float(estimate.get("duration_h") or 0.0),
        "retrieval_count": estimate.get("retrieval_count"),
        "neighbors": neighbors,
        "llm_used": bool((report.get("llm_metrics") or {}).get("used", True)),
    }


def _evaluate_one(
    record: dict[str, Any],
    pool_events: list[dict[str, Any]],
    *,
    exclude_self: bool,
    client: Any,
    dry_run: bool,
) -> dict[str, Any]:
    if exclude_self:
        key = record.get("memory_event_id")
        memory = {"events": [e for e in pool_events if e.get("memory_event_id") != key]}
    else:
        memory = {"events": pool_events}
    metadata = _build_metadata(record)
    query_event = _build_query_event(record)
    report = report_event_capacity_with_agent(
        query_event, memory, result={}, metadata=metadata, client=client, top_k=TOP_K, dry_run=dry_run,
    )
    row = dict(metadata)
    row["memory_event_id"] = record.get("memory_event_id")
    row["actual_shed_kwh"] = query_event.get("actual_shed_kwh")
    row.update(_extract_report_row(report))
    return row


def run_evaluation(
    method: str,
    pool_month: str,
    query_month: str,
    *,
    dry_run: bool,
    workers: int,
    limit: int,
) -> list[dict[str, Any]]:
    pool_events = _load_events(_memory_path(method, pool_month))
    query_events = _load_events(_memory_path(method, query_month))
    if limit:
        query_events = query_events[:limit]
    exclude_self = pool_month == query_month

    client = None
    if not dry_run:
        from energybridge.llm.client import LLMClient

        client = LLMClient()

    total = len(query_events)

    def _one(idx_record):
        idx, record = idx_record
        row = _evaluate_one(record, pool_events, exclude_self=exclude_self, client=client, dry_run=dry_run)
        with _print_lock:
            if idx % 20 == 0 or idx == total:
                print(
                    f"[{method} {idx}/{total}] {row.get('household_id')} {row.get('city')} {row.get('start_date')}",
                    file=sys.stderr, flush=True,
                )
        return row

    rows: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(_one, (i, r)) for i, r in enumerate(query_events, start=1)]
        for fut in as_completed(futs):
            rows.append(fut.result())
    return rows


def _weighted_percentile(pairs: list[tuple[float, float]], q: float) -> float:
    if not pairs:
        return 0.0
    pairs = sorted(pairs, key=lambda p: p[0])
    if len(pairs) == 1:
        return pairs[0][0]
    total_w = sum(w for _, w in pairs)
    if total_w <= 0:
        return pairs[len(pairs) // 2][0]
    q = max(0.0, min(1.0, q))
    cum, cum_fracs = 0.0, []
    for _, w in pairs:
        cum += w
        cum_fracs.append(cum / total_w)
    for i, cf in enumerate(cum_fracs):
        if cf >= q:
            if i == 0:
                return pairs[0][0]
            lo, hi = cum_fracs[i - 1], cf
            span = hi - lo
            frac = (q - lo) / span if span > 1e-12 else 0.0
            return pairs[i - 1][0] * (1 - frac) + pairs[i][0] * frac
    return pairs[-1][0]


def apply_is_correction(rows: list[dict[str, Any]], is_weights_dir: Path) -> list[dict[str, Any]]:
    """Recompute the quantile the LLM already picked as a weighted quantile over
    the same top-k neighbors, using each neighbor's June->July importance-sampling
    raw_weight (PR #20). Mirrors eb_apply_is_to_real_llm.py / IS_weather_and_preload.py's
    own weighted_quantile validation, not the old (and since-deleted)
    run_is_vs_rag_comparison.py, which multiplied the final report by a single
    arbitrarily-chosen neighbor's weight instead of reweighting the quantile itself.
    """
    weight_maps: dict[str, dict[str, float]] = {}

    def _load_weight_map(city: str) -> dict[str, float]:
        if city in weight_maps:
            return weight_maps[city]
        path = is_weights_dir / f"weights_package_{city.lower()}_6to7.json"
        out: dict[str, float] = {}
        if path.exists():
            pkg = json.loads(path.read_text())
            for e in pkg.get("per_event_weights") or []:
                mid, w = e.get("memory_event_id"), e.get("raw_weight")
                if mid is not None and w is not None:
                    out[str(mid)] = float(w)
        weight_maps[city] = out
        return out

    out_rows = []
    for r in rows:
        wmap = _load_weight_map(str(r.get("city") or ""))
        q = QUANTILE_VALUE.get(str(r.get("recommended_quantile") or "p70").lower(), 0.70)
        duration_h = float(r.get("duration_h") or 0.0)
        pairs = [(nb["value_kw"], wmap.get(nb["memory_event_id"], 1.0)) for nb in (r.get("neighbors") or [])]
        if pairs and duration_h > 1e-9:
            reported_is_kwh = max(0.0, _weighted_percentile(pairs, q) * duration_h)
        else:
            reported_is_kwh = r.get("reported_capacity_kwh")
        out = dict(r)
        out["reported_capacity_kwh_is"] = round(reported_is_kwh, 6) if reported_is_kwh is not None else None
        out_rows.append(out)
    return out_rows


def _ratio_stats(rows: list[dict[str, Any]], capacity_key: str) -> dict[str, Any]:
    ratios = []
    for r in rows:
        actual = r.get("actual_shed_kwh")
        reported = r.get(capacity_key)
        if actual is None or reported is None or float(actual) <= 1e-9:
            continue
        ratios.append(float(reported) / float(actual))
    passed = [v for v in ratios if 0.8 <= v <= 1.2]
    return {
        "n_total": len(rows),
        "n_valid_ratio": len(ratios),
        "n_pass": len(passed),
        "pass_rate_pct": round(len(passed) / len(rows) * 100, 2) if rows else None,
        "mean_ratio": round(st.mean(ratios), 4) if ratios else None,
    }


def summarize(rows: list[dict[str, Any]], is_applied: bool) -> dict[str, Any]:
    summary = {"pure": _ratio_stats(rows, "reported_capacity_kwh")}
    if is_applied:
        summary["is_corrected"] = _ratio_stats(rows, "reported_capacity_kwh_is")
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--method", required=True, choices=list(METHOD_MEMORY_FILE))
    parser.add_argument("--pool-month", required=True, choices=["june", "july"])
    parser.add_argument("--query-month", required=True, choices=["june", "july"])
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Use deterministic calibrated choice (p70) without LLM calls.",
    )
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--limit", type=int, default=0, help="Cap number of query events (0 = all).")
    parser.add_argument("--apply-is", action="store_true", help="Also compute an IS-weighted quantile correction.")
    parser.add_argument("--is-weights-dir", default=str(PROJECT_ROOT / "importance_sampling" / "IS_result"))
    parser.add_argument("--output", required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    rows = run_evaluation(
        args.method,
        args.pool_month,
        args.query_month,
        dry_run=args.dry_run,
        workers=args.workers,
        limit=args.limit,
    )
    if args.apply_is:
        rows = apply_is_correction(rows, Path(args.is_weights_dir))

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = summarize(rows, args.apply_is)
    print(f"[OK] wrote {output_path} n={len(rows)}")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
