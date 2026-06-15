#!/usr/bin/env python3
"""
Generate benchmark metrics as a CSV file.

Usage:
    python3 generate_metrics_csv.py
    python3 generate_metrics_csv.py --results-dir /path/to/results --output metrics.csv
    python3 generate_metrics_csv.py --building family  # only family results
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

# Constants

BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS = BENCHMARK_DIR / "results"
DEFAULT_OUTPUT  = BENCHMARK_DIR / "metrics_table.csv"

BUILDINGS = ["family", "office"]
CITIES    = ["beijing", "shanghai", "tianjin"]
METHODS   = ["pmv", "agent", "agent_pmv"]

BUILDING_LABEL = {"family": "family", "office": "office"}
CITY_LABEL     = {"beijing": "Beijing", "shanghai": "Shanghai", "tianjin": "Tianjin"}
METHOD_LABEL   = {"pmv": "PMV baseline", "agent": "Agent", "agent_pmv": "Agent+PMV"}

CSV_HEADERS = [
    "Building",
    "City",
    "Method",
    "Total energy kWh",
    "VPP 3h savings vs PMV",
    "PMV pass rate",
    "VPP compliance",
    "Overall score",
    "Three scores (E/C/V)",
]

# Helpers

def vpp_3h_kwh(d: dict) -> float:
    """Compute the 3-hour VPP-window energy in kWh from benchmark_result."""
    total   = d.get("energy_kwh_total", 0) or 0.0
    vpp_red = d.get("vpp_energy_reduction_kwh", 0) or 0.0
    return (3 * total - 69 * vpp_red) / 72


def fmt_savings(savings_kwh: float) -> str:
    return f"+{savings_kwh:.2f} kWh"


def fmt_ecv(e_scores: list, c_scores: list, v_scores: list) -> str:
    """Format three E/C/V scores; collapse identical triples as x3."""
    if not e_scores:
        return "N/A"
    parts = [f"E{e}/C{c}/V{v}" for e, c, v in zip(e_scores, c_scores, v_scores)]
    if len(set(parts)) == 1:
        return f"{parts[0]} x3"
    return ", ".join(parts)


def load_result(results_dir: Path, building: str, city: str, method: str) -> dict | None:
    key  = f"{building}_{city}_{method}"
    path = results_dir / key / "benchmark_result.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


# Core logic

def build_rows(results_dir: Path, buildings: list) -> list:
    rows = []

    for building in buildings:
        for city in CITIES:
            pmv_data = load_result(results_dir, building, city, "pmv")
            pmv_v3h  = vpp_3h_kwh(pmv_data) if pmv_data else None

            for method in METHODS:
                d = load_result(results_dir, building, city, method)
                if d is None:
                    continue

                total    = d.get("energy_kwh_total", 0) or 0.0
                v3h      = vpp_3h_kwh(d)
                pmv_ok   = (d.get("pmv_ok_fraction", 0) or 0.0) * 100
                vpp_c    = (d.get("vpp_compliance_rate", 0) or 0.0) * 100
                overall  = d.get("user_pref_score", 0) or 0.0
                e_scores = d.get("user_energy_scores") or []
                c_scores = d.get("user_comfort_scores") or []
                v_scores = d.get("user_vpp_scores") or []

                savings_col = "N/A" if method == "pmv" else (
                    fmt_savings(pmv_v3h - v3h) if pmv_v3h is not None else "N/A"
                )

                rows.append({
                    "Building":                 BUILDING_LABEL[building],
                    "City":                     CITY_LABEL[city],
                    "Method":                   METHOD_LABEL[method],
                    "Total energy kWh":         f"{total:.1f}",
                    "VPP 3h savings vs PMV":    savings_col,
                    "PMV pass rate":            f"{pmv_ok:.1f}%",
                    "VPP compliance":           f"{vpp_c:.1f}%",
                    "Overall score":            f"{overall:.2f}",
                    "Three scores (E/C/V)":     fmt_ecv(e_scores, c_scores, v_scores),
                })

    return rows


def print_table(rows: list) -> None:
    """Print a formatted table grouped by building type."""
    col_w = {
        "Building": 8, "City": 9, "Method": 13, "Total energy kWh": 16,
        "VPP 3h savings vs PMV": 24, "PMV pass rate": 13, "VPP compliance": 14,
        "Overall score": 14, "Three scores (E/C/V)": 34,
    }

    cur_building = None
    for row in rows:
        if row["Building"] != cur_building:
            cur_building = row["Building"]
            print(f"\n=== {cur_building} ===\n")
            header = "  ".join(h.ljust(col_w[h]) for h in CSV_HEADERS)
            print(header)
            print("-" * len(header))

        line = "  ".join(str(row[h]).ljust(col_w[h]) for h in CSV_HEADERS)
        print(line)

        if row["Method"] == METHOD_LABEL["agent_pmv"]:
            print()


def write_csv(rows: list, output: Path) -> None:
    with output.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n[OK] CSV written to: {output}")


# Entrypoint

def main() -> None:
    ap = argparse.ArgumentParser(description="Generate benchmark metrics CSV")
    ap.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS,
                    help=f"results directory (default: {DEFAULT_RESULTS})")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                    help=f"output CSV path (default: {DEFAULT_OUTPUT})")
    ap.add_argument("--building", choices=["family", "office", "all"], default="all",
                    help="only output the specified building type (default: all)")
    ap.add_argument("--no-print", action="store_true",
                    help="do not print to terminal; only write CSV")
    args = ap.parse_args()

    buildings = BUILDINGS if args.building == "all" else [args.building]

    rows = build_rows(args.results_dir, buildings)
    if not rows:
        print("[ERROR] No result files found. Check the --results-dir path.")
        return

    if not args.no_print:
        print_table(rows)

    write_csv(rows, args.output)


if __name__ == "__main__":
    main()
