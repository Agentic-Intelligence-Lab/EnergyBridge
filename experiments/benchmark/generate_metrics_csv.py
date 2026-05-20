#!/usr/bin/env python3
"""
生成基准测试指标 CSV 文件。

Usage:
    python3 generate_metrics_csv.py
    python3 generate_metrics_csv.py --results-dir /path/to/results --output metrics.csv
    python3 generate_metrics_csv.py --building family  # 只输出住宅
"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

# ── 常量 ─────────────────────────────────────────────────────────────────────

BENCHMARK_DIR = Path(__file__).resolve().parent
DEFAULT_RESULTS = BENCHMARK_DIR / "results"
DEFAULT_OUTPUT  = BENCHMARK_DIR / "metrics_table.csv"

BUILDINGS = ["family", "office"]
CITIES    = ["beijing", "shanghai", "tianjin"]
METHODS   = ["pmv", "agent", "agent_pmv"]

BUILDING_LABEL = {"family": "住宅", "office": "办公楼"}
CITY_LABEL     = {"beijing": "北京", "shanghai": "上海", "tianjin": "天津"}
METHOD_LABEL   = {"pmv": "PMV基准", "agent": "Agent", "agent_pmv": "Agent+PMV"}

CSV_HEADERS = [
    "建筑类型",
    "城市",
    "方法",
    "总能耗 kWh",
    "VPP 3h 节能 vs PMV",
    "PMV达标率",
    "VPP合规",
    "综合分",
    "三次评分 (E/C/V)",
]

# ── 工具函数 ──────────────────────────────────────────────────────────────────

def vpp_3h_kwh(d: dict) -> float:
    """从 benchmark_result 计算 VPP 3 小时能耗（kWh）。"""
    total   = d.get("energy_kwh_total", 0) or 0.0
    vpp_red = d.get("vpp_energy_reduction_kwh", 0) or 0.0
    return (3 * total - 69 * vpp_red) / 72


def fmt_savings(savings_kwh: float) -> str:
    return f"+{savings_kwh:.2f} kWh"


def fmt_ecv(e_scores: list, c_scores: list, v_scores: list) -> str:
    """格式化三次 E/C/V 评分，完全相同则折叠为 × 3。"""
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


# ── 核心逻辑 ──────────────────────────────────────────────────────────────────

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
                    "建筑类型":           BUILDING_LABEL[building],
                    "城市":               CITY_LABEL[city],
                    "方法":               METHOD_LABEL[method],
                    "总能耗 kWh":         f"{total:.1f}",
                    "VPP 3h 节能 vs PMV": savings_col,
                    "PMV达标率":          f"{pmv_ok:.1f}%",
                    "VPP合规":            f"{vpp_c:.1f}%",
                    "综合分":             f"{overall:.2f}",
                    "三次评分 (E/C/V)":   fmt_ecv(e_scores, c_scores, v_scores),
                })

    return rows


def print_table(rows: list) -> None:
    """终端打印格式化表格（按建筑类型分组）。"""
    col_w = {
        "建筑类型": 6, "城市": 4, "方法": 10, "总能耗 kWh": 12,
        "VPP 3h 节能 vs PMV": 20, "PMV达标率": 9, "VPP合规": 7,
        "综合分": 6, "三次评分 (E/C/V)": 34,
    }

    cur_building = None
    for row in rows:
        if row["建筑类型"] != cur_building:
            cur_building = row["建筑类型"]
            print(f"\n=== {cur_building} ===\n")
            header = "  ".join(h.ljust(col_w[h]) for h in CSV_HEADERS)
            print(header)
            print("-" * len(header))

        line = "  ".join(str(row[h]).ljust(col_w[h]) for h in CSV_HEADERS)
        print(line)

        if row["方法"] == METHOD_LABEL["agent_pmv"]:
            print()


def write_csv(rows: list, output: Path) -> None:
    with output.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
    print(f"\n[OK] CSV 已写入: {output}")


# ── 入口 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="生成基准测试指标 CSV")
    ap.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS,
                    help=f"results 目录（默认: {DEFAULT_RESULTS}）")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                    help=f"输出 CSV 路径（默认: {DEFAULT_OUTPUT}）")
    ap.add_argument("--building", choices=["family", "office", "all"], default="all",
                    help="只输出指定建筑类型（默认: all）")
    ap.add_argument("--no-print", action="store_true",
                    help="不在终端打印，只写 CSV")
    args = ap.parse_args()

    buildings = BUILDINGS if args.building == "all" else [args.building]

    rows = build_rows(args.results_dir, buildings)
    if not rows:
        print("[ERROR] 未找到任何结果文件，请检查 --results-dir 路径。")
        return

    if not args.no_print:
        print_table(rows)

    write_csv(rows, args.output)


if __name__ == "__main__":
    main()
