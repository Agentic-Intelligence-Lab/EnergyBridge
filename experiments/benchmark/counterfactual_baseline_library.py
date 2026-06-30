#!/usr/bin/env python3
"""Build/apply no-DR counterfactual VPP baselines.

Typical workflow:

1. Run no-DR counterfactuals once and keep their result folders.
2. Build a reusable library from the no-DR summary.
3. Apply the library to method result JSONs or matrix summaries.

The applied metrics use event-window settlement:

    delivered_kWh = no_dr_baseline_event_kWh - method_actual_event_kWh

Delivered energy is intentionally allowed to be negative when a method consumes
more than its no-DR counterfactual during the event window.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

_BENCH_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from energybridge.quantification.counterfactual_baseline import (  # noqa: E402
    apply_counterfactual_baseline,
    build_counterfactual_library,
    extract_counterfactual_baseline,
    find_matching_baseline,
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
    if output_dir:
        candidate = Path(str(output_dir)) / "benchmark_result.json"
        if candidate.exists():
            return candidate
    return None


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


def _update_summary_row_from_result(row: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    updated = dict(row)
    for key in (
        "counterfactual_baseline_status",
        "counterfactual_baseline_id",
        "counterfactual_baseline_source",
        "counterfactual_baseline_basis",
        "counterfactual_baseline_event_count",
        "counterfactual_baseline_vpp_window_kwh",
        "counterfactual_actual_vpp_window_kwh",
        "counterfactual_baseline_vpp_window_avg_per_hour_kwh",
        "counterfactual_actual_vpp_window_avg_per_hour_kwh",
        "counterfactual_capacity_upper_bound_total_kwh",
        "counterfactual_capacity_upper_bound_avg_per_hour_kwh",
        "counterfactual_capacity_upper_bound_basis",
        "counterfactual_actual_shed_total_kwh",
        "counterfactual_actual_shed_avg_per_event_kwh",
        "counterfactual_actual_shed_avg_per_hour_kwh",
        "counterfactual_delivery_ratio_vs_target_avg",
        "counterfactual_delivery_ratio_vs_recommended_bid_avg",
        "counterfactual_delivery_ratio_vs_baseline_upper_bound_avg",
        "counterfactual_delivery_ratio_vs_baseline_upper_bound_total",
        "vpp_energy_reduction_kwh",
        "vpp_actual_shed_kwh",
        "vpp_energy_reduction_total_kwh",
        "vpp_energy_reduction_avg_per_event_kwh",
        "vpp_energy_reduction_avg_per_hour_kwh",
        "vpp_energy_reduction_basis",
    ):
        if key in result:
            updated[key] = result[key]
    return updated


def cmd_build(args: argparse.Namespace) -> None:
    items: list[tuple[dict[str, Any], dict[str, Any], Path | None]] = []
    wanted_methods = {method.lower() for method in args.baseline_methods}
    for row in _load_summary_rows(args.summary_json):
        method = str(row.get("method") or "").lower()
        if wanted_methods and method not in wanted_methods:
            continue
        result_path = _result_path_from_row(row)
        if result_path is None:
            print(f"[WARN] result missing for summary row: {row.get('output_dir')}", file=sys.stderr)
            continue
        items.append((_read_json(result_path), row, result_path))
    for raw in args.result_json:
        path = Path(raw)
        result = _read_json(path)
        items.append((result, {"method": result.get("method", "no_dr")}, path))
    library = build_counterfactual_library(items)
    _write_json(Path(args.output), library)
    print(f"[OK] wrote {args.output} baselines={len(library.get('baselines') or [])}")


def cmd_extract_one(args: argparse.Namespace) -> None:
    path = Path(args.result_json)
    result = _read_json(path)
    metadata = {
        "persona_id": args.persona_id,
        "household_id": args.household_id,
        "city": args.city,
        "days": args.days,
        "start_date": args.start_date,
        "method": args.method or result.get("method", "no_dr"),
    }
    record = extract_counterfactual_baseline(result, metadata=metadata, source_path=path)
    _write_json(Path(args.output), record)
    print(f"[OK] wrote {args.output} events={len(record.get('event_baselines') or [])}")


def cmd_apply(args: argparse.Namespace) -> None:
    library = _read_json(Path(args.library))
    rows = _load_summary_rows(args.summary_json)
    out_rows: list[dict[str, Any]] = []
    matched = 0
    missing = 0
    for row in rows:
        result_path = _result_path_from_row(row)
        if result_path is None:
            out_rows.append(row)
            missing += 1
            continue
        result = _read_json(result_path)
        baseline = find_matching_baseline(library, result, metadata=row)
        if baseline is None:
            out_rows.append(row)
            missing += 1
            continue
        updated_result = apply_counterfactual_baseline(result, baseline, metadata=row)
        matched += 1
        if args.write_result_json:
            _write_json(result_path, updated_result)
        out_rows.append(_update_summary_row_from_result(row, updated_result))

    output_json = Path(args.output_summary_json)
    output_csv = Path(args.output_summary_csv) if args.output_summary_csv else output_json.with_suffix(".csv")
    clean_rows = [
        {key: value for key, value in row.items() if not key.startswith("_")}
        for row in out_rows
    ]
    _write_json(output_json, clean_rows)
    _write_summary_csv(output_csv, clean_rows)
    print(
        f"[OK] applied library matched={matched} missing={missing} "
        f"summary={output_json} csv={output_csv}"
    )


def cmd_apply_results(args: argparse.Namespace) -> None:
    library = _read_json(Path(args.library))
    matched = 0
    missing = 0
    for raw in args.result_json:
        path = Path(raw)
        result = _read_json(path)
        metadata = {
            "persona_id": args.persona_id,
            "household_id": args.household_id,
            "city": args.city,
            "days": args.days,
            "start_date": args.start_date,
        }
        baseline = find_matching_baseline(library, result, metadata=metadata)
        if baseline is None:
            print(f"[WARN] no matching baseline: {path}", file=sys.stderr)
            missing += 1
            continue
        updated = apply_counterfactual_baseline(result, baseline, metadata=metadata)
        output = path if args.in_place else Path(args.output_dir) / path.parent.name / path.name
        _write_json(output, updated)
        print(f"[OK] {path} -> {output}")
        matched += 1
    print(f"[DONE] matched={matched} missing={missing}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="Build a reusable library from no-DR result(s).")
    build.add_argument("--summary-json", nargs="*", default=[], help="Matrix summary JSON(s) containing no_dr rows.")
    build.add_argument("--result-json", nargs="*", default=[], help="Standalone no-DR benchmark_result.json path(s).")
    build.add_argument("--baseline-methods", nargs="+", default=["no_dr"], help="Methods treated as counterfactual baselines.")
    build.add_argument("--output", required=True, help="Output library JSON path.")
    build.set_defaults(func=cmd_build)

    one = sub.add_parser("extract-one", help="Extract one baseline record from a benchmark_result.json.")
    one.add_argument("result_json")
    one.add_argument("--output", required=True)
    one.add_argument("--persona-id", default="")
    one.add_argument("--household-id", default="")
    one.add_argument("--city", default="")
    one.add_argument("--days", type=int, default=None)
    one.add_argument("--start-date", default="")
    one.add_argument("--method", default="no_dr")
    one.set_defaults(func=cmd_extract_one)

    apply = sub.add_parser("apply", help="Apply a library to a matrix summary.")
    apply.add_argument("--library", required=True)
    apply.add_argument("--summary-json", nargs="+", required=True)
    apply.add_argument("--output-summary-json", required=True)
    apply.add_argument("--output-summary-csv", default="")
    apply.add_argument("--write-result-json", action="store_true", help="Overwrite each matched benchmark_result.json.")
    apply.set_defaults(func=cmd_apply)

    apply_results = sub.add_parser("apply-results", help="Apply a library to explicit result JSON files.")
    apply_results.add_argument("--library", required=True)
    apply_results.add_argument("--result-json", nargs="+", required=True)
    apply_results.add_argument("--in-place", action="store_true")
    apply_results.add_argument("--output-dir", default="benchmark_results/counterfactual_applied")
    apply_results.add_argument("--persona-id", default="")
    apply_results.add_argument("--household-id", default="")
    apply_results.add_argument("--city", default="")
    apply_results.add_argument("--days", type=int, default=None)
    apply_results.add_argument("--start-date", default="")
    apply_results.set_defaults(func=cmd_apply_results)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
