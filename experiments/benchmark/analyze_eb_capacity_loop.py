#!/usr/bin/env python3
"""Analyze an EB-only capacity reporting loop.

The main simulator already records three quantities for each EnergyBridge VPP
event: the raw A3 90% capacity report, device-level expected shed components,
and the realized event shed.  This script keeps the loop lightweight by using a
leave-one-household-out calibration factor for EV-heavy events, then reports
whether the calibrated capacity report is within 20% of the realized delivery.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
import re
from statistics import mean
from typing import Any

import matplotlib.pyplot as plt
import numpy as np


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        if value in ("", None):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _load_rows(summary_json: Path) -> list[dict[str, Any]]:
    rows = json.loads(summary_json.read_text(encoding="utf-8"))
    if not isinstance(rows, list):
        raise ValueError(f"Unexpected summary format: {summary_json}")
    return rows


def _event_records(summary_json: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in _load_rows(summary_json):
        if str(row.get("method")) != "EnergyBridge":
            continue
        result_path = Path(str(row.get("output_dir") or "")) / "benchmark_result.json"
        if not result_path.exists():
            continue
        result = json.loads(result_path.read_text(encoding="utf-8"))
        for event_idx, event in enumerate(result.get("vpp_event_log") or [], start=1):
            q90 = event.get("total_quantification_90") or {}
            actual = _float(event.get("actual_shed_kwh"))
            raw_q90 = _float(q90.get("reported_shed_90_energy_kwh"))
            if raw_q90 is None:
                raw_q90 = _float((event.get("capacity_window_summary") or {}).get("recommended_bid_energy_kwh"))
            if actual is None or raw_q90 is None or raw_q90 <= 0:
                continue
            ev_expected = _float(q90.get("ev_expected_shed_energy_kwh"), 0.0) or 0.0
            profile = _profile_context(str(row.get("household_id") or row.get("persona_id") or ""))
            records.append(
                {
                    "entity_id": row.get("household_id") or row.get("persona_id") or result_path.parent.name,
                    "city": row.get("city") or result.get("weather") or "",
                    "event_id": event.get("id") or f"event_{event_idx}",
                    "duration_h": _event_duration_h(event),
                    "actual_shed_kwh": actual,
                    "raw_reported_90_kwh": raw_q90,
                    "ev_expected_shed_kwh": ev_expected,
                    "hvac_expected_shed_kwh": _float(q90.get("hvac_expected_shed_energy_kwh"), 0.0) or 0.0,
                    "ewh_expected_shed_kwh": _float(q90.get("ewh_expected_shed_energy_kwh"), 0.0) or 0.0,
                    "profile_segment": profile["segment"],
                    "profile_tokens": " ".join(sorted(profile["tokens"])),
                    "source_result": str(result_path),
                }
            )
    if not records:
        raise ValueError(f"No usable EnergyBridge capacity records found in {summary_json}")
    return records


def _profile_context(entity_id: str) -> dict[str, Any]:
    profile_path = PROJECT_ROOT / "energybridge" / "roleplay" / "households" / f"{entity_id}.json"
    if not profile_path.exists():
        profile_path = PROJECT_ROOT / "energybridge" / "roleplay" / "personas" / f"{entity_id}.json"
    try:
        profile = json.loads(profile_path.read_text(encoding="utf-8"))
    except Exception:
        profile = {"id": entity_id, "tags": {}}
    segment_text = " ".join(
        [
            entity_id,
            str(profile.get("display_name") or ""),
            json.dumps(profile.get("tags") or {}, ensure_ascii=False),
        ]
    ).lower()
    text_parts = [
        entity_id,
        str(profile.get("display_name") or ""),
        json.dumps(profile.get("tags") or {}, ensure_ascii=False),
    ]
    for member in profile.get("members") or []:
        if isinstance(member, dict):
            text_parts.append(str(member.get("persona_id") or ""))
    text = " ".join(text_parts).lower()
    tokens = set(re.findall(r"[a-z0-9]+", text))
    segment_tokens = set(re.findall(r"[a-z0-9]+", segment_text))
    constrained_markers = {
        "caregiver",
        "cautious",
        "confirm",
        "confirmation",
        "low",
        "protective",
        "vulnerable",
        "workday",
        "stability",
    }
    segment = (
        "constrained_low_flex_profile"
        if segment_tokens & constrained_markers
        else "flexible_event_memory_profile"
    )
    return {"segment": segment, "tokens": tokens}


def _event_duration_h(event: dict[str, Any]) -> float:
    try:
        duration = float(event.get("end_h", 0.0)) - float(event.get("trigger_h", 0.0))
    except (TypeError, ValueError):
        duration = 1.0
    return max(1e-6, duration)


def _within_20(actual: float, reported: float) -> bool:
    if reported <= 0:
        return False
    ratio = actual / reported
    return 0.8 <= ratio <= 1.2


def _calibrate(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    flexible_factors: list[tuple[str, float]] = []
    for record in records:
        if record["profile_segment"] != "flexible_event_memory_profile":
            continue
        factor = float(record["actual_shed_kwh"]) / float(record["raw_reported_90_kwh"])
        if factor > 0:
            flexible_factors.append((str(record["entity_id"]), factor))

    global_factor = mean(factor for _, factor in flexible_factors) if flexible_factors else 1.0
    calibrated: list[dict[str, Any]] = []
    for record in records:
        row = dict(record)
        if row["profile_segment"] == "flexible_event_memory_profile" and flexible_factors:
            loo = [factor for entity, factor in flexible_factors if entity != str(row["entity_id"])]
            factor = mean(loo) if loo else global_factor
            loop_report = float(row["raw_reported_90_kwh"]) * factor
            basis = "leave_one_household_out_profile_ratio_calibration"
        else:
            factor = 1.0
            loop_report = float(row["raw_reported_90_kwh"])
            basis = "raw_90_report_constrained_profile"
        row["loop_ratio_calibration_factor"] = round(factor, 6)
        row["loop_reported_90_kwh"] = round(max(0.0, loop_report), 6)
        row["raw_actual_to_reported_ratio"] = round(
            float(row["actual_shed_kwh"]) / float(row["raw_reported_90_kwh"]), 6
        )
        row["loop_actual_to_reported_ratio"] = round(
            float(row["actual_shed_kwh"]) / max(1e-9, float(row["loop_reported_90_kwh"])), 6
        )
        row["raw_within_20pct"] = _within_20(float(row["actual_shed_kwh"]), float(row["raw_reported_90_kwh"]))
        row["loop_within_20pct"] = _within_20(float(row["actual_shed_kwh"]), float(row["loop_reported_90_kwh"]))
        row["loop_basis"] = basis
        calibrated.append(row)
    return calibrated


def _write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _plot(rows: list[dict[str, Any]], path: Path) -> None:
    labels = [str(row["entity_id"]).replace("household_", "h_") for row in rows]
    x = np.arange(len(rows))
    width = 0.25
    raw = [float(row["raw_reported_90_kwh"]) for row in rows]
    looped = [float(row["loop_reported_90_kwh"]) for row in rows]
    actual = [float(row["actual_shed_kwh"]) for row in rows]

    plt.style.use("seaborn-v0_8-whitegrid")
    fig, (ax0, ax1) = plt.subplots(2, 1, figsize=(14, 10), constrained_layout=True)
    ax0.bar(x - width, raw, width, label="Raw A3 90% report", color="#8ecae6")
    ax0.bar(x, looped, width, label="Loop-calibrated 90% report", color="#219ebc")
    ax0.bar(x + width, actual, width, label="Realized shed", color="#ffb703")
    ax0.set_title("EnergyBridge Capacity Loop: Reported vs Realized Delivery", fontweight="bold")
    ax0.set_ylabel("Capacity / delivery (kWh)")
    ax0.set_xticks(x)
    ax0.set_xticklabels(labels, rotation=20, ha="right")
    ax0.legend(loc="upper left")

    raw_ratio = [float(row["raw_actual_to_reported_ratio"]) for row in rows]
    loop_ratio = [float(row["loop_actual_to_reported_ratio"]) for row in rows]
    ax1.axhspan(0.8, 1.2, color="#d8f3dc", alpha=0.8, label="0.8-1.2 target band")
    ax1.plot(x, raw_ratio, marker="o", label="Raw actual / report", color="#457b9d")
    ax1.plot(x, loop_ratio, marker="o", label="Loop actual / report", color="#d00000")
    ax1.set_title("Capacity Accuracy Band", fontweight="bold")
    ax1.set_ylabel("Actual / reported")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=20, ha="right")
    ax1.set_ylim(0, max(5.0, max(raw_ratio + loop_ratio) * 1.1))
    ax1.legend(loc="upper left")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=220)
    plt.close(fig)


def _write_markdown(rows: list[dict[str, Any]], path: Path, *, source: Path) -> None:
    raw_hits = sum(1 for row in rows if row["raw_within_20pct"])
    loop_hits = sum(1 for row in rows if row["loop_within_20pct"])
    n = len(rows)
    raw_ratio = mean(float(row["raw_actual_to_reported_ratio"]) for row in rows)
    loop_ratio = mean(float(row["loop_actual_to_reported_ratio"]) for row in rows)
    lines = [
        "# EB Capacity Loop Summary",
        "",
        f"- Source summary: `{source}`",
        f"- Events: `{n}` EnergyBridge VPP events",
        f"- Raw A3-90 within 0.8-1.2: `{raw_hits}/{n}`",
        f"- Loop-calibrated 90% report within 0.8-1.2: `{loop_hits}/{n}`",
        f"- Mean actual/reported ratio: raw `{raw_ratio:.2f}`, loop `{loop_ratio:.2f}`",
        "",
        "The loop keeps the original A3 90% report for constrained low-flex profiles. For flexible profiles it uses a leave-one-household-out actual/raw ratio from similar historical EB executions, which closes the capacity-reporting loop without changing the controller.",
        "",
        "This is a lightweight EB-only capacity quantification add-on, not a new market-bidding pipeline.",
    ]
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze EB-only capacity quantification loop.")
    parser.add_argument("--summary-json", required=True, help="Household/persona matrix summary JSON.")
    parser.add_argument("--output-dir", required=True, help="Directory for CSV, PNG, and markdown outputs.")
    args = parser.parse_args()

    summary_json = Path(args.summary_json)
    out_dir = Path(args.output_dir)
    rows = _calibrate(_event_records(summary_json))
    csv_path = out_dir / "eb_capacity_loop_event_table.csv"
    png_path = out_dir / "eb_capacity_loop_report.png"
    md_path = out_dir / "eb_capacity_loop_summary.md"
    _write_csv(rows, csv_path)
    _plot(rows, png_path)
    _write_markdown(rows, md_path, source=summary_json)
    print(f"[OK] csv: {csv_path}")
    print(f"[OK] png: {png_path}")
    print(f"[OK] md : {md_path}")


if __name__ == "__main__":
    main()
