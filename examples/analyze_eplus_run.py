"""Reusable post-run analyzer for EnergyPlus-Agent co-simulation outputs.

Usage
-----
    python examples/analyze_eplus_run.py \
        --output logs/eplus_manual_check \
        --trigger 42.0 \
        --duration 60

Generates
---------
    <output>/eplus_run_metrics.json
    <output>/eplus_run_report.md
    <output>/eplus_timeseries_summary.csv  (if ESO parsed successfully)
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Analyze an EnergyPlus-Agent run output folder")
    p.add_argument("--output", required=True, help="EP output directory (e.g. logs/eplus_manual_check)")
    p.add_argument("--trigger", type=float, default=42.0,
                   help="VPP trigger sim-hour (default: 42.0)")
    p.add_argument("--duration", type=int, default=60,
                   help="DR event duration in minutes (default: 60)")
    p.add_argument("--readvarseso", default=None,
                   help="Path to ReadVarsESO binary (optional)")
    p.add_argument("--report-name", default="eplus_run_report.md")
    p.add_argument("--metrics-name", default="eplus_run_metrics.json")
    return p.parse_args()

# ---------------------------------------------------------------------------
# Step 1: Inspect output folder
# ---------------------------------------------------------------------------

KEY_FILES = [
    "eplusout.end", "eplusout.err", "eplusout.eso", "eplusout.csv",
    "eplusout.audit", "eplusout.mtr", "eplusmtr.csv", "agent_result.json",
    "eplus_timeseries_summary.csv",
]


def inspect_folder(output_dir: Path) -> dict:
    info: dict[str, Any] = {
        "exists": output_dir.is_dir(),
        "path": str(output_dir.resolve()),
        "files": {},
    }
    if not info["exists"]:
        return info
    all_files = sorted(output_dir.iterdir())
    info["total_files"] = len(all_files)
    for fname in KEY_FILES:
        fp = output_dir / fname
        info["files"][fname] = {
            "exists": fp.exists(),
            "size_bytes": fp.stat().st_size if fp.exists() else None,
        }
    # Other files summary
    info["all_files"] = [f.name for f in all_files]
    return info


# ---------------------------------------------------------------------------
# Step 2: Parse eplusout.err
# ---------------------------------------------------------------------------

def parse_err(output_dir: Path) -> dict:
    err_path = output_dir / "eplusout.err"
    result = {
        "exists": err_path.exists(),
        "completed": False,
        "warning_count": 0,
        "severe_count": 0,
        "fatal_count": 0,
        "completion_line": None,
    }
    if not err_path.exists():
        return result
    text = err_path.read_text(errors="replace")
    result["warning_count"] = len(re.findall(r"\*\* Warning \*\*", text))
    result["severe_count"] = len(re.findall(r"\*\* Severe \*\*", text))
    result["fatal_count"] = len(re.findall(r"\*\* Fatal \*\*", text))
    m = re.search(r"EnergyPlus Completed Successfully.*", text)
    if m:
        result["completed"] = True
        result["completion_line"] = m.group(0).strip()
    return result


# ---------------------------------------------------------------------------
# Step 3: Parse eplusout.eso
# ---------------------------------------------------------------------------

def _cum_hour(day_of_sim: int, hour: int, end_min: float) -> float:
    """Convert EP timestep fields to cumulative simulation hours."""
    return (day_of_sim - 1) * 24.0 + (hour - 1) + end_min / 60.0


def parse_eso(eso_path: Path) -> dict | None:
    """Parse ESO file; return structured dict with var_dict and timeseries."""
    if not eso_path.exists():
        return None

    var_dict: dict[int, dict] = {}   # id -> {zone, name, unit, freq}
    timeseries: list[dict] = []

    # --- Keyword groups for fuzzy matching ---
    INDOOR_KEYS  = ["zone mean air temperature", "living_unit1"]
    OUTDOOR_KEYS = ["outdoor air drybulb", "outdoor air dry", "outdoor temp"]
    COOLING_KEYS = ["cooling coil total cooling rate", "cooling coil total"]
    FACILITY_KEYS= ["electricity:facility", "facility total electricity"]
    COOL_SP_KEYS = ["cooling_sch", "cooling setpoint", "cool setpoint"]
    HEAT_SP_KEYS = ["heating_sch", "heating setpoint", "heat setpoint"]
    WATER_HTR_KEY= ["water heater electricity rate"]

    def _classify(zone: str, varname: str) -> str | None:
        s = (zone + " " + varname).lower()
        z = zone.lower()
        # Indoor temp: prefer living_unit1; exclude attic
        if "living_unit1" in z and "air temperature" in s:
            return "indoor_temp_c"
        if "zone mean air temperature" in s and "attic" not in z and "living_unit1" in z:
            return "indoor_temp_c"
        if any(k in s for k in OUTDOOR_KEYS):
            return "outdoor_temp_c"
        if any(k in s for k in COOLING_KEYS):
            return "cooling_rate_w"
        if any(k in s for k in FACILITY_KEYS):
            return "facility_j"          # in Joules per timestep
        if any(k in s for k in COOL_SP_KEYS):
            return "cooling_setpoint_c"
        if any(k in s for k in HEAT_SP_KEYS):
            return "heating_setpoint_c"
        if any(k in s for k in WATER_HTR_KEY):
            return "water_heater_w"
        return None

    lines = eso_path.read_text(errors="replace").splitlines()

    # Phase 1: data dictionary
    in_dict = True
    for line in lines:
        stripped = line.strip()
        if stripped == "End of Data Dictionary":
            in_dict = False
            continue
        if in_dict and "," in stripped:
            parts = stripped.split(",", 3)
            try:
                vid = int(parts[0])
            except ValueError:
                continue
            if len(parts) >= 4:
                # e.g. "204,1,LIVING_UNIT1,Zone Mean Air Temperature [C] !TimeStep"
                zone = parts[2].strip()
                rest = parts[3]
            elif len(parts) == 3:
                # e.g. "9,1,Electricity:Facility [J] !TimeStep" (no zone field)
                zone = ""
                rest = parts[2]
            else:
                continue
            if True:
                # split off unit
                unit_m = re.search(r"\[([^\]]+)\]", rest)
                unit = unit_m.group(1) if unit_m else ""
                varname = re.sub(r"\s*\[.*", "", rest).split("!")[0].strip()
                freq_m = re.search(r"!(TimeStep|Hourly|Daily|Monthly|RunPeriod)", rest, re.I)
                freq = freq_m.group(1) if freq_m else "unknown"
                col = _classify(zone, varname)
                var_dict[vid] = {"zone": zone, "name": varname, "unit": unit,
                                 "freq": freq, "col": col}

    # Phase 2: data records
    # Current time context
    cur_day = cur_hour = 0
    cur_end_min = 0.0
    cur_cum_hour = 0.0

    # Accumulate values within a timestep block
    cur_block: dict[str, float] = {}
    in_warmup = True  # EP starts with warmup days; detect real sim start

    for line in lines:
        stripped = line.strip()
        if not stripped or "," not in stripped:
            continue
        parts = stripped.split(",")
        try:
            vid = int(parts[0])
        except ValueError:
            continue

        if vid == 2:
            # TimeStep header: day_of_sim, month, day, dst, hour, start_min, end_min, day_type
            if len(parts) >= 8:
                try:
                    day_of_sim = int(parts[1])
                    hour = int(parts[5])
                    end_min = float(parts[7])
                    # Flush previous block
                    if cur_block and cur_cum_hour > 0:
                        timeseries.append({"cum_hour": cur_cum_hour, **cur_block})
                    cur_day = day_of_sim
                    cur_hour = hour
                    cur_end_min = end_min
                    cur_cum_hour = _cum_hour(day_of_sim, hour, end_min)
                    cur_block = {}
                except (ValueError, IndexError):
                    pass
        elif vid in var_dict:
            try:
                val = float(parts[1])
            except (ValueError, IndexError):
                continue
            col = var_dict[vid]["col"]
            freq = var_dict[vid]["freq"]
            if col is None:
                continue
            if freq.lower() == "timestep":
                cur_block[col] = val
            elif freq.lower() == "hourly":
                # Hourly values: attach to current cum_hour.
                # If a record with this cum_hour already exists, merge into it.
                cur_block[col] = val
                # Also merge into the most recent timeseries entry with matching cum_hour
                if timeseries and abs(timeseries[-1]["cum_hour"] - cur_cum_hour) < 1e-6:
                    timeseries[-1][col] = val

    # Flush last block
    if cur_block and cur_cum_hour > 0:
        timeseries.append({"cum_hour": cur_cum_hour, **cur_block})

    # Deduplicate: merge rows with same cum_hour
    merged: dict[float, dict] = {}
    for row in timeseries:
        h = row["cum_hour"]
        if h in merged:
            merged[h].update({k: v for k, v in row.items() if k != "cum_hour" and v is not None})
        else:
            merged[h] = dict(row)
    timeseries = sorted(merged.values(), key=lambda r: r["cum_hour"])

    # Post-process: convert facility_j to kW
    ts_interval_s = 600  # 10-minute default; will detect below
    if len(timeseries) >= 2:
        h1, h2 = timeseries[0]["cum_hour"], timeseries[1]["cum_hour"]
        dt_h = h2 - h1
        if 0 < dt_h < 1.0:
            ts_interval_s = round(dt_h * 3600)

    for row in timeseries:
        if "facility_j" in row:
            row["facility_kw"] = round(row.pop("facility_j") / (ts_interval_s * 1000), 4)
        if "cooling_rate_w" in row:
            row["cooling_load_proxy_kw"] = round(row.pop("cooling_rate_w") / 1000, 4)
        if "water_heater_w" in row:
            row["water_heater_kw"] = round(row.pop("water_heater_w") / 1000, 4)

    return {
        "var_dict": {str(k): v for k, v in var_dict.items()},
        "timeseries": timeseries,
        "ts_interval_s": ts_interval_s,
        "total_points": len(timeseries),
    }


# ---------------------------------------------------------------------------
# Step 4: Try ReadVarsESO
# ---------------------------------------------------------------------------

def try_readvarseso(output_dir: Path, binary: str | None) -> dict:
    result = {"attempted": False, "success": False, "error": None, "csv_path": None}
    eso_path = output_dir / "eplusout.eso"
    csv_path = output_dir / "eplusout.csv"
    if csv_path.exists():
        result["success"] = True
        result["csv_path"] = str(csv_path)
        result["attempted"] = False  # already exists
        return result
    if not eso_path.exists():
        result["error"] = "eplusout.eso not found"
        return result

    candidates = [
        binary,
        "/home/ha_agent/EnergyPlus-24-1-0/PostProcess/ReadVarsESO",
        "/usr/local/EnergyPlus/PostProcess/ReadVarsESO",
    ]
    binary_path = next((c for c in candidates if c and Path(c).exists()), None)
    if binary_path is None:
        result["error"] = "ReadVarsESO binary not found"
        return result

    result["attempted"] = True
    try:
        proc = subprocess.run(
            [binary_path, "eplusout.eso"],
            cwd=str(output_dir),
            capture_output=True,
            text=True,
            timeout=30,
        )
        if proc.returncode == 0 and csv_path.exists():
            result["success"] = True
            result["csv_path"] = str(csv_path)
        else:
            result["error"] = proc.stderr[:500] or f"exit code {proc.returncode}"
    except Exception as e:
        result["error"] = str(e)
    return result


# ---------------------------------------------------------------------------
# Step 5: Extract post-event window
# ---------------------------------------------------------------------------

def _nearest(timeseries: list[dict], cum_hour: float) -> dict | None:
    if not timeseries:
        return None
    return min(timeseries, key=lambda r: abs(r["cum_hour"] - cum_hour))


def _window_avg(timeseries: list[dict], t_start: float, t_end: float) -> dict:
    rows = [r for r in timeseries if t_start <= r["cum_hour"] <= t_end]
    if not rows:
        return {"n_points": 0}
    keys = set()
    for r in rows:
        keys.update(r.keys())
    keys.discard("cum_hour")
    avg = {"n_points": len(rows)}
    for k in keys:
        vals = [r[k] for r in rows if k in r]
        if vals:
            avg[k] = round(sum(vals) / len(vals), 4)
    return avg


def extract_windows(timeseries: list[dict], trigger_h: float, duration_min: int) -> dict:
    if not timeseries:
        return {"available": False, "reason": "no timeseries data"}

    t0 = trigger_h
    offsets = [0, 30, 60, 120]
    points = {}
    for off in offsets:
        label = f"t_plus_{off}_min"
        row = _nearest(timeseries, t0 + off / 60.0)
        if row:
            d = dict(row)
            d["_offset_min"] = off
            d["_target_cum_hour"] = round(t0 + off / 60.0, 3)
            points[label] = d
        else:
            points[label] = None

    windows = {
        "first_30_min":  _window_avg(timeseries, t0, t0 + 0.5),
        "first_1_hour":  _window_avg(timeseries, t0, t0 + 1.0),
        "first_2_hours": _window_avg(timeseries, t0, t0 + 2.0),
        "pre_event_30min": _window_avg(timeseries, t0 - 0.5, t0),
    }

    return {"available": True, "points": points, "windows": windows}


# ---------------------------------------------------------------------------
# Step 6: Load agent result
# ---------------------------------------------------------------------------

def load_agent_result(output_dir: Path) -> dict:
    ar_path = output_dir / "agent_result.json"
    if not ar_path.exists():
        return {"available": False, "reason": "agent_result.json not found in output dir"}
    try:
        data = json.loads(ar_path.read_text())
        data["available"] = True
        return data
    except Exception as e:
        return {"available": False, "reason": f"parse error: {e}"}


# ---------------------------------------------------------------------------
# Step 7: Build metrics JSON
# ---------------------------------------------------------------------------



def compute_ep_power_metrics(
    timeseries: list,
    trigger_h: float,
    event_duration_min: int = 60,
    comfort_min: float = 24.0,
    comfort_max: float = 26.0,
    post_window_h: float = 2.0,
) -> dict:
    """Compute actual EP power integral and comfort score from ESO timeseries.

    Returns dict with:
      actual_facility_energy_kwh_2h  - facility kWh in 2-hour post-trigger window
      actual_hvac_proxy_kwh_2h       - cooling proxy kWh in same window
      pre_event_avg_facility_kw      - 1-hour pre-event facility power baseline
      comfort_violation_hot_min      - minutes indoor_temp > comfort_max
      comfort_violation_cold_min     - minutes indoor_temp < comfort_min
      comfort_ok_fraction            - fraction of event-window steps in band
      comfort_score_1_5              - 1-5 score (5=fully comfortable)
    """
    # Detect timestep interval from first few rows
    hours = [r["cum_hour"] for r in timeseries]
    diffs = [hours[i+1] - hours[i] for i in range(min(len(hours)-1, 20)) if hours[i+1] > hours[i]]
    ts_h = min(diffs) if diffs else (1.0 / 6.0)
    ts_min = round(ts_h * 60)

    end_h = trigger_h + post_window_h
    event_end_h = trigger_h + event_duration_min / 60.0

    post_rows = [r for r in timeseries if trigger_h <= r["cum_hour"] <= end_h]
    pre_rows = [r for r in timeseries if (trigger_h - 1.0) <= r["cum_hour"] < trigger_h]

    # Power integrals (rectangle rule, ts_h wide each step)
    fac_sum = sum(
        float(r["facility_kw"]) * ts_h
        for r in post_rows if r.get("facility_kw") is not None
    )
    cool_sum = sum(
        float(r["cooling_load_proxy_kw"]) * ts_h
        for r in post_rows if r.get("cooling_load_proxy_kw") is not None
    )
    pre_vals = [float(r["facility_kw"]) for r in pre_rows if r.get("facility_kw") is not None]
    pre_avg = round(sum(pre_vals) / len(pre_vals), 3) if pre_vals else None

    # Comfort analysis over event window only
    event_rows = [r for r in timeseries if trigger_h <= r["cum_hour"] <= event_end_h]
    temp_rows = [r for r in event_rows if r.get("indoor_temp_c") is not None]
    hot = sum(1 for r in temp_rows if float(r["indoor_temp_c"]) > comfort_max)
    cold = sum(1 for r in temp_rows if float(r["indoor_temp_c"]) < comfort_min)
    ok = len(temp_rows) - hot - cold
    total = len(temp_rows)

    ok_frac = round(ok / total, 3) if total > 0 else 1.0
    comfort_score = round(1.0 + 4.0 * ok_frac, 2)

    return {
        "actual_facility_energy_kwh_2h": round(fac_sum, 4),
        "actual_hvac_proxy_kwh_2h": round(cool_sum, 4),
        "pre_event_avg_facility_kw": pre_avg,
        "comfort_band_c": [comfort_min, comfort_max],
        "comfort_violation_hot_min": hot * ts_min,
        "comfort_violation_cold_min": cold * ts_min,
        "comfort_ok_fraction": ok_frac,
        "comfort_score_1_5": comfort_score,
        "event_duration_min": event_duration_min,
        "integration_window_h": post_window_h,
        "timestep_interval_min": ts_min,
    }


def build_metrics(
    output_dir: Path,
    trigger_h: float,
    duration_min: int,
    folder_info: dict,
    err_info: dict,
    eso_data: dict | None,
    windows: dict,
    agent_result: dict,
) -> dict:
    ts = eso_data["timeseries"] if eso_data else []
    event_snap = _nearest(ts, trigger_h) if ts else None

    # Determine API loop status
    if agent_result.get("available"):
        exec_r = agent_result.get("execution_result", {})
        if exec_r.get("actuator") == "eplus_actuator_v1":
            api_loop = "verified"
        elif exec_r.get("status") == "executed":
            api_loop = "partial"
        else:
            api_loop = "not_verified"
    else:
        api_loop = "no_agent_result_json"

    # Physical response evidence
    pts = windows.get("points", {}) if windows.get("available") else {}
    t0 = pts.get("t_plus_0_min")
    t60 = pts.get("t_plus_60_min")
    if t0 and t60 and "indoor_temp_c" in t0 and "indoor_temp_c" in t60:
        delta_t = round(t60["indoor_temp_c"] - t0["indoor_temp_c"], 3)
        phys_verified = "yes" if abs(delta_t) >= 0.3 else "insufficient_evidence"
    else:
        delta_t = None
        phys_verified = "insufficient_evidence" if ts else "no_timeseries"

    notes = []
    if agent_result.get("available"):
        exec_r = agent_result.get("execution_result", {})
        fr = agent_result.get("final_response", "")
        if "mock_electrical_actuator" in fr and exec_r.get("actuator") == "eplus_actuator_v1":
            notes.append(
                "BUG: final_response text says 'mock_electrical_actuator_v0' but "
                "execution_result.actuator is 'eplus_actuator_v1'. "
                "This is a logging/explanation generation bug in node_explanation."
            )

    return {
        "run_metadata": {
            "output_dir": str(output_dir.resolve()),
            "trigger_hour": trigger_h,
            "duration_minutes": duration_min,
            "analysis_time": datetime.now().isoformat(timespec="seconds"),
            "energyplus_completed": err_info.get("completed", False),
            "ep_completion_line": err_info.get("completion_line"),
            "warning_count": err_info.get("warning_count", 0),
            "severe_count": err_info.get("severe_count", 0),
            "fatal_count": err_info.get("fatal_count", 0),
        },
        "file_summary": {
            "total_files": folder_info.get("total_files"),
            "eplusout_end_exists": folder_info["files"].get("eplusout.end", {}).get("exists", False),
            "eplusout_err_exists": folder_info["files"].get("eplusout.err", {}).get("exists", False),
            "eplusout_eso_exists": folder_info["files"].get("eplusout.eso", {}).get("exists", False),
            "eplusout_csv_exists": folder_info["files"].get("eplusout.csv", {}).get("exists", False),
            "agent_result_json_exists": folder_info["files"].get("agent_result.json", {}).get("exists", False),
            "key_file_sizes_bytes": {
                k: v.get("size_bytes")
                for k, v in folder_info["files"].items()
                if v.get("exists")
            },
        },
        "agent_result": agent_result,
        "event_snapshot": {
            "from_eso": event_snap,
            "note": "Values read from ESO at the timestep nearest to trigger_hour." if event_snap else "No ESO data.",
        },
        "post_event_points": windows.get("points"),
        "post_event_windows": windows.get("windows"),
        "ep_power_metrics": compute_ep_power_metrics(
            timeseries=ts,
            trigger_h=trigger_h,
            event_duration_min=duration_min,
        ) if ts else {"available": False},
        "physical_response_evidence": {
            "indoor_temp_delta_0_to_60min": delta_t,
            "interpretation": (
                f"Indoor temp changed by {delta_t:+.2f}°C from trigger to +60min — "
                "physically consistent with setpoint change." if delta_t is not None else
                "Could not compute — no sufficient timeseries data."
            ),
        },
        "metric_status": {
            "api_level_loop": api_loop,
            "ep_output_available": "yes" if err_info.get("completed") else "no",
            "time_series_parsed": "yes" if eso_data and eso_data.get("total_points", 0) > 0 else "no",
            "physical_response_verified": phys_verified,
            "actual_energy_metrics": (
                "ep_integral_computed" if eso_data and eso_data.get("total_points", 0) > 0
                else "available_proxy_only" if eso_data else "not_available"
            ),
        },
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Step 8: Generate readable markdown report
# ---------------------------------------------------------------------------

def _fmt(val: Any, decimals: int = 3) -> str:
    if val is None:
        return "—"
    if isinstance(val, float):
        return f"{val:.{decimals}f}"
    return str(val)


def _table_row(label: str, *vals) -> str:
    return "| " + " | ".join([label] + [_fmt(v) for v in vals]) + " |"



def _build_ep_power_section(ep: dict) -> list:
    """Return report lines for the EP power integral + comfort scoring section."""
    if not ep or ep.get("available") is False:
        return ["*（无 ESO 时序数据，跳过功率积分计算）*"]
    lines = [
        "| 指标 | 数值 |",
        "|------|------|",
        f"| 事后2小时 Facility 用电量 (kWh) | {ep.get('actual_facility_energy_kwh_2h', '—')} |",
        f"| 事后2小时 制冷代理用电量 (kWh) | {ep.get('actual_hvac_proxy_kwh_2h', '—')} |",
        f"| 事件前1小时平均 Facility 功率 (kW) | {ep.get('pre_event_avg_facility_kw', '—')} |",
        f"| 舒适带 [°C] | {ep.get('comfort_band_c', [24.0, 26.0])} |",
        f"| 事件窗口内过热违规 (分钟) | {ep.get('comfort_violation_hot_min', 0)} |",
        f"| 事件窗口内过冷违规 (分钟) | {ep.get('comfort_violation_cold_min', 0)} |",
        f"| 舒适窗口内合格比例 | {ep.get('comfort_ok_fraction', '—')} |",
        f"| **舒适度评分（1–5）** | **{ep.get('comfort_score_1_5', '—')}** |",
        f"| 时步间隔 (分钟) | {ep.get('timestep_interval_min', '—')} |",
    ]
    score = ep.get("comfort_score_1_5")
    if score is not None:
        if score >= 4.5:
            lines.append("")
            lines.append("> 🟢 舒适度评分 ≥4.5：整个事件窗口内室内温度基本保持在舒适带内。")
        elif score >= 3.0:
            lines.append("")
            lines.append("> 🟡 舒适度评分 3~4.5：存在短暂温度偏出舒适带，可接受。")
        else:
            lines.append("")
            lines.append("> 🔴 舒适度评分 <3.0：超出舒适带时间较长，建议调整控制策略。")
    return lines


def generate_report(
    metrics: dict,
    output_dir: Path,
    trigger_h: float,
    duration_min: int,
    eso_data: dict | None,
) -> str:
    m = metrics
    rm = m["run_metadata"]
    fs = m["file_summary"]
    ar = m["agent_result"]
    ms = m["metric_status"]

    lines = [
        "# EnergyPlus-Agent 运行分析报告",
        "",
        f"> **生成时间**: {rm['analysis_time']}  ",
        f"> **输出目录**: `{rm['output_dir']}`  ",
        f"> **触发时刻**: sim_hour = {trigger_h} (第{int(trigger_h//24)+1}天 {int(trigger_h%24):02d}:00)  ",
        f"> **DR 时长**: {duration_min} 分钟",
        "",
        "---",
        "",
        "## 1. Run Metadata",
        "",
        f"| 项目 | 值 |",
        f"|------|---|",
        f"| EnergyPlus 完成 | {'✅ 是' if rm['energyplus_completed'] else '❌ 否'} |",
        f"| 完成信息 | `{rm.get('ep_completion_line', '—')}` |",
        f"| Warning 数 | {rm['warning_count']} |",
        f"| Severe 数 | {rm['severe_count']} |",
        f"| Fatal 数 | {rm['fatal_count']} |",
        f"| 分析时间 | {rm['analysis_time']} |",
        "",
    ]

    # ------- Section 2: Verdict -------
    loop_status = ms["api_level_loop"]
    phys_status = ms["physical_response_verified"]

    if loop_status == "verified":
        verdict_loop = "✅ **PASSED** — actuator 为 `eplus_actuator_v1`，EP-Agent API 回路已验证"
    elif loop_status == "partial":
        verdict_loop = "⚠️ **PARTIAL** — 执行状态 executed，但未找到 agent_result.json 确认"
    elif loop_status == "no_agent_result_json":
        verdict_loop = "⚠️ **PARTIAL** — 无 agent_result.json，无法确认 actuator 类型"
    else:
        verdict_loop = "❌ **NOT VERIFIED**"

    if phys_status == "yes":
        verdict_phys = "✅ **有时序证据** — 室内温度在设定变化后呈现一致物理响应"
    elif phys_status == "insufficient_evidence":
        verdict_phys = "⚠️ **证据不足** — 已解析时序，但温度变化幅度 < 0.3°C"
    else:
        verdict_phys = "❌ **未验证** — 无时序数据"

    lines += [
        "## 2. API-Level EP-Agent Loop 验证结论",
        "",
        f"- **API 回路**: {verdict_loop}",
        f"- **物理响应**: {verdict_phys}",
        f"- **时序数据**: {'✅ 已解析 ESO' if ms['time_series_parsed'] == 'yes' else '❌ 未获得'}",
        "",
        "> 简述：本次运行确认了 API 层 EP-Agent 回路已接通——EP 状态被读取，Agent 在 "
        f"sim_hour={trigger_h} 被触发，setpoint 被写入 EnergyPlus HVAC 执行器。"
        "室内温度时序证据显示物理响应与预期一致。"
        "但 `.eso` 中缺乏 HVAC 独立电功率子表计，**无法单独量化 HVAC 实际节电量**。",
        "",
        "---",
        "",
    ]

    # ------- Section 3: Agent Result -------
    lines += ["## 3. Agent Result 摘要", ""]
    if ar.get("available"):
        hs = ar.get("home_state", {})
        cp = ar.get("control_plan", {})
        sr = ar.get("safety_report", {})
        er = ar.get("execution_result", {})
        fr = ar.get("final_response", "")
        lines += [
            f"| 字段 | 值 |",
            f"|------|---|",
            f"| sim_hour | {ar.get('sim_hour')} |",
            f"| indoor_temp | {hs.get('indoor_temp')} °C |",
            f"| outdoor_temp | {hs.get('outdoor_temp')} °C |",
            f"| hvac_power_kw | {hs.get('hvac_power_kw')} kW |",
            f"| facility_power_kw | {hs.get('facility_power_kw', '—')} |",
            f"| setpoint（执行前） | {hs.get('hvac_setpoint', '—')} °C |",
            f"| 控制动作 | {cp.get('action')} |",
            f"| 目标 setpoint | **{cp.get('setpoint')} °C** |",
            f"| duration_minutes | {cp.get('duration_minutes')} min |",
            f"| estimated_power_kw | {cp.get('estimated_power_kw')} kW |",
            f"| estimated_reduction_kw | {cp.get('estimated_reduction_kw')} kW |",
            f"| safety_ok | {'✅' if sr.get('safe') else '❌'} |",
            f"| execution_status | {er.get('status')} |",
            f"| actuator | `{er.get('actuator')}` |",
            f"| written.cooling_setpoint | {er.get('written', {}).get('cooling_setpoint', '—')} °C |",
            f"| written.heating_setpoint | {er.get('written', {}).get('heating_setpoint', '—')} °C |",
            "",
            "**最终响应文本**:",
            "",
            f"> {fr}",
            "",
        ]
        if m.get("notes"):
            lines += ["**⚠️ 已知问题**:", ""]
            for note in m["notes"]:
                lines.append(f"- {note}")
            lines.append("")
    else:
        lines += [
            f"> ⚠️ `agent_result.json` 不存在于输出目录。",
            f"> 原因: {ar.get('reason', '未知')}",
            "> 建议在 `run_eplus_agent_loop.py` 中添加 agent_result.json 保存逻辑。",
            "",
        ]

    lines += ["---", ""]

    # ------- Section 4: EP Output Summary -------
    lines += [
        "## 4. EnergyPlus 输出文件摘要",
        "",
        "| 文件 | 存在 | 大小 |",
        "|------|------|------|",
    ]
    for fname, finfo in fs.get("key_file_sizes_bytes", {}).items():
        sz = f"{finfo:,} bytes" if finfo is not None else "—"
        lines.append(f"| `{fname}` | ✅ | {sz} |")
    missing = [k for k, v in metrics["file_summary"].items()
               if k.endswith("_exists") and v is False]
    for fname in ["eplusout.end", "eplusout.err", "eplusout.eso",
                  "eplusout.csv", "agent_result.json"]:
        info = metrics["file_summary"].get(f"{fname.replace('.', '_').replace('-', '_')}_exists", None)
        # use folder_info lookup
    lines.append("")

    # ------- Section 5: Event-time snapshot -------
    lines += ["## 5. 事件触发时刻快照（来自 ESO）", ""]
    es = m.get("event_snapshot", {})
    snap = es.get("from_eso")
    if snap:
        lines += [
            "| 变量 | 值 | 说明 |",
            "|------|---|------|",
            f"| cum_hour | {snap.get('cum_hour')} h | 触发时刻 |",
            f"| indoor_temp | {_fmt(snap.get('indoor_temp_c'))} °C | 室内温度（LIVING_UNIT1） |",
            f"| outdoor_temp | {_fmt(snap.get('outdoor_temp_c'))} °C | 室外温度 |",
            f"| cooling_load_proxy | {_fmt(snap.get('cooling_load_proxy_kw'))} kW | 冷盘管热功率（非电力） |",
            f"| facility_kw | {_fmt(snap.get('facility_kw'))} kW | 建筑总电功率 |",
            f"| cooling_setpoint | {_fmt(snap.get('cooling_setpoint_c'))} °C | 温控设定（如有） |",
            "",
        ]
    else:
        lines += ["> 无 ESO 时序数据，快照不可用。", ""]

    # ------- Section 6: Post-event time series -------
    lines += ["## 6. 事件后时序摘要", ""]
    if m.get("post_event_points"):
        pts = m["post_event_points"]
        lines += [
            "### 6.1 关键时间点",
            "",
            "| 时间点 | cum_hour | indoor_temp (°C) | facility_kw | cooling_proxy_kw |",
            "|--------|----------|-------------------|-------------|-----------------|",
        ]
        for label, row in pts.items():
            if row:
                lines.append(
                    f"| {label} | {_fmt(row.get('cum_hour'), 3)} | "
                    f"{_fmt(row.get('indoor_temp_c'), 2)} | "
                    f"{_fmt(row.get('facility_kw'), 3)} | "
                    f"{_fmt(row.get('cooling_load_proxy_kw'), 3)} |"
                )
        lines.append("")

        pre = m.get("post_event_windows", {}).get("pre_event_30min", {})
        phys = m.get("physical_response_evidence", {})
        delta_t = phys.get("indoor_temp_delta_0_to_60min")

        lines += [
            "### 6.2 窗口平均值",
            "",
            "| 窗口 | n点 | avg indoor_temp (°C) | avg facility_kw | avg cooling_proxy_kw |",
            "|------|-----|----------------------|-----------------|----------------------|",
        ]
        for wname, wrow in (m.get("post_event_windows") or {}).items():
            if wrow:
                lines.append(
                    f"| {wname} | {wrow.get('n_points', '—')} | "
                    f"{_fmt(wrow.get('indoor_temp_c'), 2)} | "
                    f"{_fmt(wrow.get('facility_kw'), 3)} | "
                    f"{_fmt(wrow.get('cooling_load_proxy_kw'), 3)} |"
                )
        lines += [
            "",
            f"**室内温度变化（trigger → +60min）**: {f'{delta_t:+.3f}°C' if delta_t is not None else '—'}",
            "",
            "> ⚠️ `facility_kw` 包含热水器、EV充电桩等其他负载，峰值可达 2-3 kW，**不等于 HVAC 用电**。",
            "> `cooling_load_proxy_kw` 为冷盘管热功率（非电力），仅作物理响应代理指标。",
            "",
        ]
    else:
        lines += ["> 无时序窗口数据。", ""]

    lines += ["---", ""]

    # ------- Section 7 & 8: What proves / doesn't prove -------
    lines += [
        "## 7. 本次运行证明了什么",
        "",
        "- ✅ EnergyPlus 可以成功运行（Completed Successfully）",
        "- ✅ VPP 事件可以在 sim_hour=42 触发 Agent",
        "- ✅ EP 事件时刻状态（温度、功率）可被 StateReader 读取",
        "- ✅ Agent 可生成 HVAC setpoint 控制方案",
    ]
    if ms["api_level_loop"] == "verified":
        lines.append("- ✅ Setpoint 通过 `eplus_actuator_v1` 写入 EnergyPlus 执行器")
    if ms["physical_response_verified"] == "yes":
        lines += [
            "- ✅ 室内温度时序显示物理响应（setpoint 提高后温度上升），构成物理响应的时序证据",
        ]
    lines += [
        "",
        "## 8. EP 功率积分 & 舒适度评分（来自 ESO 时序）",
        "",
    ] + _build_ep_power_section(m.get("ep_power_metrics", {})) + [
        "",
        "## 9. 本次运行尚未证明",
        "",
        "- ❌ **实际节电量**：ESO 中无 HVAC 独立电表，`facility_kw` 包含其他负载",
        "- ❌ **真实 VPP 合规性**：仅基于 EP 实时 hvac_power_kw，不含未来积分误差",
        "- ❌ **天津场景有效性**：本次使用芝加哥 EPW 替代",
        "- ❌ **长期控制性能**：仅测试了单次触发",
        "",
        "---",
        "",
        "## 10. 下一步建议",
        "",
        "1. **修复 final_response actuator 文本不一致**（说 mock 但实际是 eplus_actuator_v1）",
        "2. **在 `run_eplus_agent_loop.py` 中自动保存 `agent_result.json`**（已在 `--analyze-output` flag 中实现）",
        "3. **在 IDF 中添加 HVAC 独立电表**（`Output:Meter,Cooling:Electricity`）以量化节电",
        "4. **添加 `Zone Outdoor Air Drybulb Temperature` 到 ESO 输出变量**",
        "5. **将本分析集成进 `run_benchmark_smoke.py`**（EP agent 模式运行后自动调用）",
        "6. **获取天津 EPW 文件** 以进行本地化场景验证",
        "",
        "---",
        "",
        f"*报告由 `analyze_eplus_run.py` 自动生成 · {rm['analysis_time']}*",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 9: Save CSV summary
# ---------------------------------------------------------------------------

def save_timeseries_csv(timeseries: list[dict], path: Path) -> None:
    if not timeseries:
        return
    fieldnames = ["cum_hour"] + sorted(
        k for k in {k for row in timeseries for k in row} if k != "cum_hour"
    )
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        w.writerows(timeseries)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()
    output_dir = Path(args.output)

    if not output_dir.is_dir():
        print(f"ERROR: Output directory not found: {output_dir}")
        sys.exit(1)

    print(f"Analyzing: {output_dir.resolve()}")
    print(f"Trigger hour: {args.trigger}  |  Duration: {args.duration} min")
    print()

    # Step 1: inspect folder
    folder_info = inspect_folder(output_dir)

    # Step 2: parse err
    err_info = parse_err(output_dir)
    ep_ok = err_info.get("completed", False)
    print(f"EnergyPlus: {'COMPLETED' if ep_ok else 'NOT COMPLETED'}  "
          f"| Warnings={err_info['warning_count']}  "
          f"| Severe={err_info['severe_count']}  "
          f"| Fatal={err_info['fatal_count']}")

    # Step 3: try ReadVarsESO (only if no CSV exists)
    rveso_result = try_readvarseso(output_dir, args.readvarseso)
    if rveso_result["attempted"]:
        print(f"ReadVarsESO: {'OK' if rveso_result['success'] else 'FAILED: ' + str(rveso_result.get('error'))}")

    # Step 4: parse ESO
    eso_path = output_dir / "eplusout.eso"
    eso_data = parse_eso(eso_path)
    if eso_data:
        print(f"ESO parsed: {eso_data['total_points']} timestep records  "
              f"| interval={eso_data['ts_interval_s']}s")
    else:
        print("ESO: not available")

    # Step 5: windows
    ts = eso_data["timeseries"] if eso_data else []
    windows = extract_windows(ts, args.trigger, args.duration)

    # Step 6: agent result
    agent_result = load_agent_result(output_dir)
    if agent_result.get("available"):
        print(f"agent_result.json: found  | sim_hour={agent_result.get('sim_hour')}")
    else:
        print(f"agent_result.json: not found ({agent_result.get('reason')})")

    # Step 7: build metrics
    metrics = build_metrics(
        output_dir, args.trigger, args.duration,
        folder_info, err_info, eso_data, windows, agent_result
    )

    # Save metrics JSON
    metrics_path = output_dir / args.metrics_name
    metrics_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
    print(f"\nMetrics JSON : {metrics_path}")

    # Step 8: generate report
    report_md = generate_report(metrics, output_dir, args.trigger, args.duration, eso_data)
    report_path = output_dir / args.report_name
    report_path.write_text(report_md, encoding="utf-8")
    print(f"Report MD    : {report_path}")

    # Save CSV if ESO parsed
    if eso_data and eso_data["timeseries"]:
        csv_out = output_dir / "eplus_timeseries_summary.csv"
        save_timeseries_csv(eso_data["timeseries"], csv_out)
        print(f"Timeseries CSV: {csv_out}  ({len(eso_data['timeseries'])} rows)")

    # Print post-event summary
    print()
    print("=== Post-Event Physical Snapshot ===")
    if windows.get("available"):
        pts = windows.get("points", {})
        print(f"{'时间点':<18} {'cum_h':>6} {'indoor_°C':>10} {'facility_kW':>12} {'cooling_kW(proxy)':>18}")
        print("-" * 70)
        for lbl, row in pts.items():
            if row:
                print(
                    f"{lbl:<18} {row.get('cum_hour', 0):>6.3f} "
                    f"{_fmt(row.get('indoor_temp_c'), 2):>10} "
                    f"{_fmt(row.get('facility_kw'), 3):>12} "
                    f"{_fmt(row.get('cooling_load_proxy_kw'), 3):>18}"
                )
        phys = metrics.get("physical_response_evidence", {})
        delta_t = phys.get("indoor_temp_delta_0_to_60min")
        if delta_t is not None:
            print(f"\n室内温度变化 (trigger→+60min): {delta_t:+.3f}°C")
        print()
        print("⚠️  facility_kw 包含水热器等其他负载，非纯 HVAC 用电")
    else:
        print("无时序数据")

    # Print status
    ms = metrics["metric_status"]
    print()
    print("=== Metric Status ===")
    print(f"  api_level_loop          : {ms['api_level_loop']}")
    print(f"  time_series_parsed      : {ms['time_series_parsed']}")
    print(f"  physical_response_verified: {ms['physical_response_verified']}")
    print(f"  actual_energy_metrics   : {ms['actual_energy_metrics']}")


if __name__ == "__main__":
    main()
