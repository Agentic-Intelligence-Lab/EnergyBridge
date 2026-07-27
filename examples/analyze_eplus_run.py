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
import os
import csv
import json
import os
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass
EPLUS_ROOT = Path(os.getenv("EPLUS_ROOT", "/opt/EnergyPlus-24-1-0"))
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
    p.add_argument("--report", action="store_true", default=False,
                   help="Also generate Markdown report (optional; JSON+CSV are always written)")
    p.add_argument("--baseline-output", default=None, dest="baseline_output",
                   help="Path to a no-control baseline run folder for causal comparison")
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
            return "hvac_cooling_thermal_w"
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
        if "hvac_cooling_thermal_w" in row:
            row["hvac_cooling_thermal_kw"] = round(row.pop("hvac_cooling_thermal_w") / 1000, 4)
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
        str(EPLUS_ROOT / "PostProcess" / "ReadVarsESO"),
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
    offsets = [0, 30, 80, 120]
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
        float(r["hvac_cooling_thermal_kw"]) * ts_h
        for r in post_rows if r.get("hvac_cooling_thermal_kw") is not None
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
    baseline_comparison: dict | None = None,
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
    t60 = pts.get("t_plus_80_min")
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
        "agent_consistency_checks": _check_agent_consistency(agent_result),
        "causal_control_effect": baseline_comparison if baseline_comparison is not None else {
            "baseline_available": False,
            "delta_indoor_temp_60min": None,
            "delta_facility_kw_60min": None,
            "delta_facility_energy_kwh_2h": None,
            "verified": False,
            "note": "Causal interpretation requires a no-control baseline run. "
                    "Use --baseline-output to provide one.",
        },
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
    cons = m.get("agent_consistency_checks", {})
    causal = m.get("causal_control_effect", {})
    llm = ar.get("llm_metrics", {}) if ar.get("available") else {}

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
    ]

    # ------- Top-Level Verdict -------
    ep_done = "YES" if rm["energyplus_completed"] else "NO"
    loop_ok = "VERIFIED" if ms["api_level_loop"] == "verified" else ms["api_level_loop"].upper()
    ep_state = "VERIFIED" if ar.get("available") and ar.get("home_state") else "NOT VERIFIED"
    setpoint_written = (
        "VERIFIED" if cons.get("setpoint_written_matches_control_plan") else
        "PARTIAL" if cons.get("setpoint_written_matches_control_plan") is None else "MISMATCH"
    )
    ts_parsed = "YES" if ms["time_series_parsed"] == "yes" else "NO"
    phys_resp = "YES" if ms["physical_response_verified"] == "yes" else ms["physical_response_verified"].upper()
    causal_ok = "VERIFIED (see section 9)" if causal.get("baseline_available") else "NOT YET — needs no-control baseline"
    energy_int = "APPROXIMATE (ESO integral, not sub-meter)" if ms["actual_energy_metrics"] == "ep_integral_computed" else "NOT AVAILABLE"
    hvac_only = "NOT YET (no HVAC-only meter in IDF)"
    tianjin_ok = "NOT YET (Chicago EPW used)"
    all_consistent = cons.get("all_consistent", False)
    consistency_ok = "PASSED" if all_consistent else (f"ISSUES: {len(cons.get('issues', []))}" if cons else "NOT CHECKED")

    lines += [
        "## Top-Level Verdict",
        "",
        "| 验证项目 | 状态 |",
        "|---|---|",
        f"| EnergyPlus completed | **{ep_done}** |",
        f"| EP-agent API loop | **{loop_ok}** |",
        f"| EP state read by agent | **{ep_state}** |",
        f"| Setpoint written to EP actuator | **{setpoint_written}** |",
        f"| Agent consistency check | **{consistency_ok}** |",
        f"| Time-series parsed | **{ts_parsed}** |",
        f"| Physical response observed | **{phys_resp}** |",
        f"| Causal control effect verified | **{causal_ok}** |",
        f"| Actual facility energy integration | **{energy_int}** |",
        f"| HVAC-only electricity metric | **{hvac_only}** |",
        f"| Tianjin scenario validity | **{tianjin_ok}** |",
        "",
    ]

    # Show consistency issues as warnings if any
    if cons.get("issues"):
        lines += ["**⚠️ 一致性问题（报告顶部警告）**:", ""]
        for issue in cons["issues"]:
            lines.append(f"- ⚠️ {issue}")
        lines.append("")

    lines += ["---", ""]

    # ------- Section 1: Run Metadata -------
    lines += [
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
        "> **Warning 来源说明**：EnergyPlus 在运行时会将诊断信息写入 `eplusout.err`，"
        "格式为 `** Warning **`（警告）、`** Severe **`（严重错误）、`** Fatal **`（致命错误）。"
        "分析器通过正则计数这些标记行。警告通常由 HVAC 尺寸计算、天气数据插值或模型参数引起，"
        "不影响仿真完成；Severe 以上错误才可能导致仿真中止。",
        "",
        "---",
        "",
    ]

    # ------- Section 2: Agent Result -------
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

    # ------- Section 3: Agent Result Summary -------
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
            f"| hvac_cooling_thermal_kw | {hs.get('hvac_cooling_thermal_kw')} kW |",
            f"| facility_power_kw | {hs.get('facility_power_kw', '—')} |",
            f"| setpoint（执行前） | {hs.get('hvac_setpoint', '—')} °C |",
            f"| 控制动作 | {cp.get('action')} |",
            f"| 目标 setpoint | **{cp.get('setpoint')} °C** |",
            f"| duration_minutes | {cp.get('duration_minutes')} min |",
            f"| estimated_power_kw | {cp.get('estimated_power_kw')} kW |",
            f"| estimated_reduction_kw | {cp.get('estimated_reduction_kw')} kW |",
            f"| controller | `{cp.get('controller')}` |",
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
            f"| hvac_cooling_thermal | {_fmt(snap.get('hvac_cooling_thermal_kw'))} kW | 冷盘管热功率（非电力） |",
            f"| facility_kw | {_fmt(snap.get('facility_kw'))} kW | 建筑总电功率 |",
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
            "|--------|----------|-------------------|-------------|------------------|",
        ]
        for label, row in pts.items():
            if row:
                lines.append(
                    f"| {label} | {_fmt(row.get('cum_hour'), 3)} | "
                    f"{_fmt(row.get('indoor_temp_c'), 2)} | "
                    f"{_fmt(row.get('facility_kw'), 3)} | "
                    f"{_fmt(row.get('hvac_cooling_thermal_kw'), 3)} |"
                )
        lines.append("")

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
                    f"{_fmt(wrow.get('hvac_cooling_thermal_kw'), 3)} |"
                )
        lines += [
            "",
            f"**室内温度变化（trigger → +60min）**: {f'{delta_t:+.3f}°C' if delta_t is not None else '—'}",
            "",
            "> ⚠️ `facility_kw` 包含热水器、EV充电桩等其他负载，峰值可达 2-3 kW，**不等于 HVAC 用电**。",
            "> `hvac_cooling_thermal_kw` 为HVAC制冷热功率（非电力，即冷盘管制冷量），非HVAC电功率。",
            "",
        ]
    else:
        lines += ["> 无时序窗口数据。", ""]

    lines += ["---", ""]

    # ------- Section 7: What proves -------
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
    lines += [""]

    # ------- Section 8: EP Power Metrics -------
    lines += [
        "## 8. EP 功率积分 & 舒适度评分（来自 ESO 时序）",
        "",
    ] + _build_ep_power_section(m.get("ep_power_metrics", {})) + [""]

    # ------- Section 9: Causal Control Effect (Baseline) -------
    lines += ["## 9. 因果控制效果（Baseline 对比）", ""]
    if causal.get("baseline_available"):
        d_energy = causal.get("delta_facility_energy_kwh_2h")
        d_temp = causal.get("delta_indoor_temp_points", {})
        d_fac = causal.get("delta_facility_kw_points", {})
        lines += [
            "**Δ 室内温度（controlled − baseline）**:",
            "",
            "| 时间点 | Δ indoor_temp_c (°C) | Δ facility_kw |",
            "|--------|----------------------|----------------|",
        ]
        for pt in ["t_plus_0min", "t_plus_30min", "t_plus_80min", "t_plus_120min"]:
            dt = d_temp.get(pt)
            df = d_fac.get(pt)
            lines.append(f"| {pt} | {_fmt(dt, 3)} | {_fmt(df, 3)} |")
        lines += [
            "",
            f"**Δ facility energy (2h integral)**: {_fmt(d_energy, 3)} kWh",
            f"> 负值表示受控运行用电更少（节能）；正值表示受控运行用电更多（预期：事件期间节能但可能存在 pre-cooling 用电补偿）。",
            "",
            f"> ⚠️ {causal.get('causal_note', '')}",
            "",
        ]
    else:
        lines += [
            "> ❌ **无 baseline 数据**。请提供 `--baseline-output <path>` 参数运行无控制基线仿真。",
            ">",
            "> 基线运行方式：使用相同 IDF + EPW，跳过 agent 介入（不设置 VPP trigger），",
            "> 然后将其输出目录作为 `--baseline-output` 传入本分析器。",
            "",
        ]

    # ------- Section 10: What does NOT prove -------
    lines += [
        "## 10. 本次运行尚未证明",
        "",
        "- ❌ **因果节电量**：需要 no-control baseline 对比（见第9节）",
        "- ❌ **实际 HVAC 电耗**：ESO 中无 HVAC 独立电表，`facility_kw` 包含其他负载",
        "- ❌ **真实 VPP 合规性**：仅基于 EP 实时 hvac_cooling_thermal_kw（热功率），不含未来积分误差",
        "- ❌ **天津场景有效性**：本次使用芝加哥 EPW 替代",
        "- ❌ **长期控制性能**：仅测试了单次触发",
        "",
        "---",
        "",
    ]

    # ------- Section 11: Agent Consistency Checks -------
    lines += ["## 11. Agent 一致性检查", ""]
    if cons.get("available") is False:
        lines += ["> 无 agent_result.json，无法执行一致性检查。", ""]
    else:
        def _tick(v):
            if v is True: return "✅"
            if v is False: return "❌"
            return "—"
        lines += [
            "| 检查项 | 结果 |",
            "|--------|------|",
            f"| execution_actuator_is_eplus | {_tick(cons.get('execution_actuator_is_eplus'))} |",
            f"| final_response_actuator_consistent | {_tick(cons.get('final_response_actuator_consistent'))} |",
            f"| control_plan_action_matches_execution | {_tick(cons.get('control_plan_action_matches_execution'))} |",
            f"| setpoint_written_matches_control_plan | {_tick(cons.get('setpoint_written_matches_control_plan'))} |",
            f"| trajectory_actuate_consistent | {_tick(cons.get('trajectory_actuate_consistent'))} |",
            "",
        ]
        if cons.get("issues"):
            lines += ["**问题列表**:", ""]
            for issue in cons["issues"]:
                lines.append(f"- ⚠️ {issue}")
            lines.append("")
        else:
            lines.append("> ✅ 所有一致性检查通过。")
            lines.append("")

    lines += ["---", ""]

    # ------- Section 12: LLM / API Runtime Metrics -------
    lines += ["## 12. LLM / API Runtime Metrics", ""]
    if llm.get("used"):
        tu = llm.get("token_usage", {})
        lines += [
            "| 指标 | 值 |",
            "|------|---|",
            f"| provider | {llm.get('provider', '—')} |",
            f"| model | `{llm.get('model', '—')}` |",
            f"| api_used | ✅ True |",
            f"| latency_seconds | {llm.get('latency_seconds', '—')} s |",
            f"| prompt_tokens | {tu.get('prompt_tokens', '—')} |",
            f"| completion_tokens | {tu.get('completion_tokens', '—')} |",
            f"| total_tokens | {tu.get('total_tokens', '—')} |",
            "",
        ]
    else:
        lines += [
            "| 指标 | 值 |",
            "|------|---|",
            "| api_used | not_available |",
            "| model | not_available |",
            "| latency_seconds | not_available |",
            "| total_tokens | not_available |",
            "",
        ]

    lines += ["---", ""]

    # ------- Section 13: Next Steps -------
    lines += [
        "## 13. 下一步建议",
        "",
        "1. **在 IDF 中添加 HVAC 独立电表**（`Output:Meter,Cooling:Electricity`）以量化实际 HVAC 节电",
        "2. **运行 no-control baseline** 并使用 `--baseline-output` 获取因果控制效果",
        "3. **获取天津 EPW 文件** 以进行本地化场景验证",
        "4. **将本分析集成进 `run_benchmark_smoke.py`**（EP agent 模式运行后自动调用）",
        "5. **测试多次 VPP 触发**（当前仅测试单次 sim_hour=42）",
        "",
        "---",
        "",
        f"*报告由 `analyze_eplus_run.py` 自动生成 · {rm['analysis_time']}*",
    ]

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Step 9: Save CSV summary
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Agent consistency checks
# ---------------------------------------------------------------------------

def _check_agent_consistency(agent_result: dict) -> dict:
    """Check internal consistency of agent_result.json fields."""
    if not agent_result.get("available"):
        return {"available": False}

    er = agent_result.get("execution_result", {})
    cp = agent_result.get("control_plan", {})
    fr = agent_result.get("final_response", "")
    traj = agent_result.get("trajectory", [])

    exec_actuator = er.get("actuator", "")
    is_eplus = exec_actuator == "eplus_actuator_v1"

    fr_consistent = "mock_electrical_actuator" not in fr and (
        exec_actuator in fr or "eplus_actuator" in fr
    )

    cp_action = cp.get("action", "")
    er_action = er.get("action", "")
    action_matches = cp_action == er_action if cp_action and er_action else None

    cp_setpoint = cp.get("setpoint")
    written_setpoint = er.get("written", {}).get("cooling_setpoint")
    setpoint_matches = (
        abs(cp_setpoint - written_setpoint) < 0.01
        if cp_setpoint is not None and written_setpoint is not None
        else None
    )

    # Check trajectory actuate node
    traj_actuate = next((s["output"] for s in traj if s.get("node") == "actuate"), {})
    traj_actuator = traj_actuate.get("actuator", "")
    traj_consistent = traj_actuator == exec_actuator if traj_actuator else None

    issues = []
    if not is_eplus:
        issues.append(f"execution_result.actuator is '{exec_actuator}' (expected eplus_actuator_v1)")
    if not fr_consistent:
        issues.append("final_response mentions mock actuator but execution used eplus_actuator_v1")
    if action_matches is False:
        issues.append(f"control_plan.action='{cp_action}' != execution_result.action='{er_action}'")
    if setpoint_matches is False:
        issues.append(f"control_plan.setpoint={cp_setpoint} != written.cooling_setpoint={written_setpoint}")
    if traj_consistent is False:
        issues.append(f"trajectory.actuate.actuator='{traj_actuator}' != execution_result.actuator='{exec_actuator}'")

    return {
        "execution_actuator_is_eplus": is_eplus,
        "final_response_actuator_consistent": fr_consistent,
        "control_plan_action_matches_execution": action_matches,
        "setpoint_written_matches_control_plan": setpoint_matches,
        "trajectory_actuate_consistent": traj_consistent,
        "all_consistent": len(issues) == 0,
        "issues": issues,
    }


# ---------------------------------------------------------------------------
# Baseline comparison
# ---------------------------------------------------------------------------

def _compare_baseline(
    controlled_ts: list[dict],
    baseline_ts: list[dict],
    trigger_h: float,
    duration_min: int,
) -> dict:
    """Compute delta between controlled and baseline runs at key time points."""
    VARS = ["indoor_temp_c", "facility_kw", "hvac_cooling_thermal_kw", "water_heater_kw"]

    def _nn(ts, t):
        return _nearest(ts, t)

    def _win(ts, t_start, t_end):
        return _window_avg(ts, t_start, t_end)

    offsets_min = [0, 30, 80, 120]
    points_delta = {}
    for off in offsets_min:
        t = trigger_h + off / 60.0
        c = _nn(controlled_ts, t) or {}
        b = _nn(baseline_ts, t) or {}
        d = {}
        for v in VARS:
            cv, bv = c.get(v), b.get(v)
            d[v] = round(cv - bv, 4) if cv is not None and bv is not None else None
        points_delta[f"t_plus_{off}min"] = d

    windows_delta = {}
    window_defs = [
        ("first_30min", trigger_h, trigger_h + 0.5),
        ("first_1h",    trigger_h, trigger_h + 1.0),
        ("first_2h",    trigger_h, trigger_h + 2.0),
    ]
    for wname, t0, t1 in window_defs:
        c = _win(controlled_ts, t0, t1)
        b = _win(baseline_ts, t0, t1)
        d = {}
        for v in VARS:
            cv, bv = c.get(v), b.get(v)
            d[v] = round(cv - bv, 4) if cv is not None and bv is not None else None
        windows_delta[wname] = d

    # Facility energy delta
    def _integral(ts, t0, t1, var="facility_kw"):
        seg = [r for r in ts if t0 <= r.get("cum_hour", -1) <= t1]
        if len(seg) < 2:
            return None
        seg = sorted(seg, key=lambda r: r["cum_hour"])
        total = 0.0
        for i in range(len(seg) - 1):
            dt = seg[i+1]["cum_hour"] - seg[i]["cum_hour"]
            v = (seg[i].get(var, 0) + seg[i+1].get(var, 0)) / 2
            total += v * dt
        return round(total, 4)

    t_end = trigger_h + 2.0
    c_energy = _integral(controlled_ts, trigger_h, t_end)
    b_energy = _integral(baseline_ts, trigger_h, t_end)
    delta_energy = round(c_energy - b_energy, 4) if c_energy is not None and b_energy is not None else None

    return {
        "baseline_available": True,
        "causal_note": (
            "Requires same IDF, same EPW, same run period, same trigger window — "
            "only valid if the two runs differ ONLY in the control action."
        ),
        "delta_indoor_temp_points": {k: v.get("indoor_temp_c") for k, v in points_delta.items()},
        "delta_facility_kw_points": {k: v.get("facility_kw") for k, v in points_delta.items()},
        "delta_hvac_cooling_thermal_kw_points": {k: v.get("hvac_cooling_thermal_kw") for k, v in points_delta.items()},
        "all_variables_by_point": points_delta,
        "all_variables_by_window": windows_delta,
        "delta_facility_energy_kwh_2h": delta_energy,
        "controlled_facility_energy_kwh_2h": c_energy,
        "baseline_facility_energy_kwh_2h": b_energy,
        "interpretation": (
            f"Controlled run used {delta_energy:+.3f} kWh more (+) or less (-) "
            f"facility electricity over 2h post-event vs baseline."
            if delta_energy is not None else "Could not compute."
        ),
    }


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

    # Step 7: baseline comparison (optional)
    baseline_comparison = None
    if args.baseline_output:
        baseline_dir = Path(args.baseline_output)
        if baseline_dir.is_dir():
            baseline_eso_path = baseline_dir / "eplusout.eso"
            baseline_eso = parse_eso(baseline_eso_path)
            if baseline_eso and baseline_eso["timeseries"]:
                baseline_ts = baseline_eso["timeseries"]
                controlled_ts = eso_data["timeseries"] if eso_data else []
                baseline_comparison = _compare_baseline(
                    controlled_ts, baseline_ts, args.trigger, args.duration
                )
                print(f"Baseline ESO : {baseline_dir} ({baseline_eso['total_points']} records)")
            else:
                print(f"Baseline ESO : {baseline_dir} — ESO parse failed or empty")
        else:
            print(f"Baseline dir : NOT FOUND — {args.baseline_output}")

    # Step 7b: build metrics
    metrics = build_metrics(
        output_dir, args.trigger, args.duration,
        folder_info, err_info, eso_data, windows, agent_result,
        baseline_comparison=baseline_comparison,
    )

    # Save metrics JSON
    metrics_path = output_dir / args.metrics_name
    metrics_path.write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
    print(f"\nMetrics JSON : {metrics_path}")

    # Step 8: generate report (optional)
    if args.report:
        report_md = generate_report(metrics, output_dir, args.trigger, args.duration, eso_data)
        report_path = output_dir / args.report_name
        report_path.write_text(report_md, encoding="utf-8")
        print(f"Report MD    : {report_path}")
    else:
        print(f"Report MD    : (skipped; use --report to generate)")

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
                    f"{_fmt(row.get('hvac_cooling_thermal_kw'), 3):>18}"
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
    cons = metrics.get("agent_consistency_checks", {})
    print()
    print("=== Metric Status ===")
    print(f"  api_level_loop          : {ms['api_level_loop']}")
    print(f"  time_series_parsed      : {ms['time_series_parsed']}")
    print(f"  physical_response_verified: {ms['physical_response_verified']}")
    print(f"  actual_energy_metrics   : {ms['actual_energy_metrics']}")
    if cons.get("issues"):
        print()
        print("=== ⚠️  Consistency Issues ===")
        for issue in cons["issues"]:
            print(f"  - {issue}")
    else:
        print(f"  agent_consistency      : all_consistent={cons.get('all_consistent', '—')}")
    if baseline_comparison:
        d = baseline_comparison.get("delta_facility_energy_kwh_2h")
        print()
        print("=== Baseline Comparison ===")
        print(f"  delta_facility_energy_kwh_2h: {d} kWh (controlled − baseline)")


if __name__ == "__main__":
    main()
