"""Local web dashboard for EnergyBridge benchmark results.

Run:
    python experiments/benchmark/web_dashboard.py --port 8787
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shlex
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RESULTS_DIR = PROJECT_ROOT / "benchmark_results"
PERSONA_DIR = PROJECT_ROOT / "energybridge" / "roleplay" / "personas"
JOBS: dict[str, dict] = {}
JOB_PROCS: dict[str, subprocess.Popen] = {}
JOBS_LOCK = threading.Lock()

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _append_job_log(job_id: str, line: str) -> None:
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job is not None:
            job.setdefault("logs", []).append(line.rstrip("\n"))


def _set_job(job_id: str, **updates) -> None:
    with JOBS_LOCK:
        if job_id in JOBS:
            JOBS[job_id].update(updates)


def _infer_human_input_context(logs: list[str], requested_kind: str = "") -> tuple[str, str]:
    """Infer which blocking human prompt the web input is answering."""
    tail = "\n".join(logs[-100:])
    event_matches = re.findall(r"VPP event\s*(\d+)", tail)
    selected_matches = re.findall(r"\[Strategy Selected\s+\|\s+event=(\d+)\]", tail)
    score_matches = re.findall(r"\[Human Score Selected\s+\|\s+event=(\d+)\]", tail)
    event_id = (event_matches or selected_matches or score_matches or [""])[-1]

    strategy_idx = max(tail.rfind("[Choose Strategy"), tail.rfind("Enter A / B / C"))
    selected_idx = tail.rfind("[Strategy Selected")
    score_prompt_idx = tail.rfind("Rate this VPP handling")
    score_done_idx = tail.rfind("[Human Score Selected")
    comment_idx = tail.rfind("Optional: leave brief feedback")

    if comment_idx > max(score_prompt_idx, strategy_idx) and comment_idx > score_done_idx:
        return "score_comment", event_id
    if score_prompt_idx > max(strategy_idx, selected_idx) and score_prompt_idx > score_done_idx:
        return "score", event_id
    if strategy_idx > selected_idx:
        return "strategy_choice", event_id
    return requested_kind or "unknown", event_id


def _attach_finished_summary(job_id: str) -> None:
    with JOBS_LOCK:
        logs = list(JOBS.get(job_id, {}).get("logs", []))
    summary_path = None
    for line in reversed(logs):
        if "run_summary.txt" in line:
            tail = line.split("run_summary.txt", 1)[-1].strip()
            if "→" in line:
                tail = line.rsplit("→", 1)[-1].strip()
            summary_path = tail if tail.endswith("run_summary.txt") else None
            if summary_path:
                break
    if not summary_path:
        for line in reversed(logs):
            if line.strip().startswith("OUTPUT"):
                output_dir = line.split(":", 1)[-1].strip()
                candidate = Path(output_dir) / "run_summary.txt"
                if candidate.exists():
                    summary_path = str(candidate)
                    break
    if summary_path:
        path = Path(summary_path)
        if path.exists():
            updates = {
                "run_summary_path": str(path),
                "run_summary_text": path.read_text(encoding="utf-8", errors="replace"),
            }
            result_path = path.parent / "benchmark_result.json"
            if result_path.exists():
                try:
                    result_data = json.loads(result_path.read_text(encoding="utf-8"))
                    _apply_meter_vpp_energy(result_data, path.parent)
                    _normalize_dashboard_result(result_data)
                    updates["benchmark_result"] = result_data
                except Exception:
                    pass
            _set_job(job_id, **updates)


def _run_command_job(job_id: str, argv: list[str]) -> None:
    _set_job(job_id, status="running", started_at=datetime.now().isoformat(timespec="seconds"))
    try:
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        proc = subprocess.Popen(
            argv,
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE,
            text=True,
            bufsize=0,
            env=env,
        )
        with JOBS_LOCK:
            JOB_PROCS[job_id] = proc
            if job_id in JOBS:
                JOBS[job_id]["pid"] = proc.pid
        assert proc.stdout is not None
        chunk = []
        while True:
            char = proc.stdout.read(1)
            if char == "" and proc.poll() is not None:
                break
            if not char:
                continue
            chunk.append(char)
            if char == "\n" or "".join(chunk).endswith("  > "):
                _append_job_log(job_id, "".join(chunk))
                chunk = []
        if chunk:
            _append_job_log(job_id, "".join(chunk))
        code = proc.wait()
        _attach_finished_summary(job_id)
        _set_job(
            job_id,
            status="succeeded" if code == 0 else "failed",
            exit_code=code,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
    except Exception as exc:
        _append_job_log(job_id, f"[dashboard error] {exc}")
        _set_job(
            job_id,
            status="failed",
            exit_code=-1,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )
    finally:
        with JOBS_LOCK:
            JOB_PROCS.pop(job_id, None)


def _validate_run_command(command: str) -> list[str]:
    argv = shlex.split(command)
    if not argv:
        raise ValueError("Command is empty")
    allowed_prefixes = [
        ["python", "experiments/benchmark/run_persona_json.py"],
        [sys.executable, "experiments/benchmark/run_persona_json.py"],
        ["conda", "run", "--no-capture-output", "-n", "energybridge", "python", "experiments/benchmark/run_persona_json.py"],
        ["conda", "run", "-n", "energybridge", "python", "experiments/benchmark/run_persona_json.py"],
    ]
    if not any(argv[: len(prefix)] == prefix for prefix in allowed_prefixes):
        raise ValueError(
            "Only run_persona_json commands are allowed from the dashboard."
        )
    forbidden = {";", "&&", "||", "|", ">", "<"}
    if any(token in forbidden for token in argv):
        raise ValueError("Shell operators are not allowed")
    if argv[:2] == ["conda", "run"] and "--no-capture-output" not in argv[:6]:
        argv = ["conda", "run", "--no-capture-output"] + argv[2:]
    return argv


def _list_personas() -> list[dict]:
    personas = []
    for path in sorted(PERSONA_DIR.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        persona_id = data.get("id") or path.stem
        label = persona_id
        if persona_id.startswith("basic_role_"):
            parts = persona_id.split("_")
            label = f"role_{parts[2]}" if len(parts) > 2 else persona_id
        personas.append({
            "id": persona_id,
            "label": label,
            "name": data.get("name", persona_id),
            "path": str(path),
        })
    return personas


def _read_meter_vpp_energy(run_path: Path, events_source: list[dict] | None = None) -> dict[str, float]:
    """Read VPP-window Electricity:Facility kWh from EnergyPlus timestep meters."""
    mtr_path = run_path / "eplusout.mtr"
    if not mtr_path.exists():
        return {}
    events = {}
    for idx, event in enumerate(events_source or [], start=1):
        try:
            event_id = str(event.get("id") or f"vpp{idx}")
            events[event_id] = (float(event["trigger_h"]), float(event["end_h"]))
        except (KeyError, TypeError, ValueError):
            continue
    if not events:
        events = {
            "vpp1": (18.0, 19.0),
            "vpp2": (42.0, 43.0),
            "vpp3": (66.0, 67.0),
        }
    values = {key: 0.0 for key in events}
    in_data = False
    current_window = None
    facility_code = None
    try:
        with mtr_path.open(errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not in_data:
                    parts = [part.strip() for part in line.split(",")]
                    if (
                        len(parts) >= 3
                        and parts[0].isdigit()
                        and "Electricity:Facility" in line
                        and "!TimeStep" in line
                    ):
                        facility_code = int(parts[0])
                if line.startswith("End of Data Dictionary"):
                    in_data = True
                    continue
                if not in_data or not line:
                    continue
                parts = [part.strip() for part in line.split(",")]
                if not parts or not parts[0].isdigit():
                    continue
                code = int(parts[0])
                if code == 2 and len(parts) >= 8:
                    day = int(parts[1])
                    hour = int(parts[5])
                    start_min = float(parts[6])
                    end_min = float(parts[7])
                    start_h = (day - 1) * 24.0 + (hour - 1) + start_min / 60.0
                    end_h = (day - 1) * 24.0 + (hour - 1) + end_min / 60.0
                    current_window = (start_h, end_h)
                    continue
                if facility_code is None:
                    facility_code = 9
                if code != facility_code or current_window is None or len(parts) < 2:
                    continue
                try:
                    kwh = float(parts[1]) / 3_600_000.0
                except ValueError:
                    continue
                start_h, end_h = current_window
                duration_h = max(1e-9, end_h - start_h)
                for event_id, (event_start, event_end) in events.items():
                    overlap_h = max(0.0, min(end_h, event_end) - max(start_h, event_start))
                    if overlap_h > 0.0:
                        values[event_id] += kwh * overlap_h / duration_h
    except Exception:
        return {}
    return {key: round(value, 6) for key, value in values.items() if value > 0.0}


def _event_duration_h(event: dict) -> float:
    try:
        return max(1e-6, float(event.get("end_h", 0.0)) - float(event.get("trigger_h", 0.0)))
    except (TypeError, ValueError):
        return 1.0


def _event_physical_shed_cap_kwh(event: dict) -> float | None:
    summary = event.get("capacity_window_summary") or {}
    value = summary.get("recommended_bid_energy_kwh")
    try:
        if value is not None:
            return max(0.0, float(value))
    except (TypeError, ValueError):
        pass
    assessment = ((event.get("capacity_assessment") or {}).get("assessment") or {})
    value = assessment.get("recommended_bid_kw")
    try:
        if value is not None:
            return max(0.0, float(value) * _event_duration_h(event))
    except (TypeError, ValueError):
        pass
    return None


def _update_event_reference_shed_diagnostics(event: dict) -> None:
    baseline_kwh = event.get("demand_baseline_kwh")
    actual_kwh = event.get("actual_kwh")
    event["actual_shed_kwh"] = None
    event["actual_shed_basis"] = "unavailable_without_same_run_no_dr_counterfactual"
    if baseline_kwh is None or actual_kwh is None:
        return
    try:
        raw_shed = max(0.0, float(baseline_kwh) - float(actual_kwh))
    except (TypeError, ValueError):
        return
    cap_kwh = _event_physical_shed_cap_kwh(event)
    event["reference_baseline_shed_kwh"] = round(raw_shed, 4)
    event["reference_pbase_minus_actual_kwh"] = round(raw_shed, 4)
    event["reference_shed_diagnostic_basis"] = (
        "reference_a3_pbase_minus_current_ep_actual_not_actual_shed"
    )
    if cap_kwh is not None:
        event["physical_shed_cap_kwh"] = round(cap_kwh, 4)
        event["capacity_limited_reference_shed_kwh"] = round(min(raw_shed, cap_kwh), 4)
        event["capacity_limited_reference_shed_basis"] = (
            "min(reference_a3_pbase_minus_current_ep_actual, physical_capacity_recommended_bid)"
        )


def _read_meter_daily_energy(run_path: Path) -> list[dict]:
    """Read per-day Electricity:Facility kWh from EnergyPlus timestep meters."""
    mtr_path = run_path / "eplusout.mtr"
    if not mtr_path.exists():
        return []
    values: dict[int, float] = {}
    in_data = False
    current_day = None
    facility_code = None
    try:
        with mtr_path.open(errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not in_data:
                    parts = [part.strip() for part in line.split(",")]
                    if (
                        len(parts) >= 3
                        and parts[0].isdigit()
                        and "Electricity:Facility" in line
                        and "!TimeStep" in line
                    ):
                        facility_code = int(parts[0])
                if line.startswith("End of Data Dictionary"):
                    in_data = True
                    continue
                if not in_data or not line:
                    continue
                parts = [part.strip() for part in line.split(",")]
                if not parts or not parts[0].isdigit():
                    continue
                code = int(parts[0])
                if code == 2 and len(parts) >= 2:
                    try:
                        current_day = int(parts[1])
                    except ValueError:
                        current_day = None
                    continue
                if facility_code is None:
                    facility_code = 9
                if code != facility_code or current_day is None or len(parts) < 2:
                    continue
                try:
                    values[current_day] = values.get(current_day, 0.0) + float(parts[1]) / 3_600_000.0
                except ValueError:
                    continue
    except Exception:
        return []
    return [
        {"day": day, "energy_kwh": round(value, 6), "source": "meter"}
        for day, value in sorted(values.items())
        if value > 0.0
    ]


def _coerce_daily_energy_values(raw) -> list[dict]:
    if isinstance(raw, dict):
        items = []
        for key, value in raw.items():
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("day", key)
            else:
                item = {"day": key, "energy_kwh": value}
            items.append(item)
    elif isinstance(raw, list):
        items = []
        for idx, value in enumerate(raw, start=1):
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("day", idx)
            else:
                item = {"day": idx, "energy_kwh": value}
            items.append(item)
    else:
        return []

    normalized = []
    for item in items:
        try:
            day = int(item.get("day"))
            energy = float(item.get("energy_kwh", item.get("total_energy_kwh")))
        except (TypeError, ValueError):
            continue
        normalized.append({"day": day, "energy_kwh": round(energy, 6), "source": item.get("source", "runtime")})
    return normalized


def _apply_meter_vpp_energy(data: dict, run_path: Path) -> None:
    meter_values = _read_meter_vpp_energy(run_path, data.get("vpp_event_log", []))
    if meter_values:
        total = 0.0
        for event in data.get("vpp_event_log", []) or []:
            event_id = event.get("id")
            if event_id not in meter_values:
                continue
            actual_kwh = round(float(meter_values[event_id]), 4)
            total += actual_kwh
            event["actual_kwh"] = actual_kwh
            baseline_kwh = event.get("demand_baseline_kwh")
            if baseline_kwh is not None:
                _update_event_reference_shed_diagnostics(event)
        if total > 0.0:
            data["vpp_window_energy_kwh"] = round(total, 4)
    else:
        for event in data.get("vpp_event_log", []) or []:
            _update_event_reference_shed_diagnostics(event)
    total_duration_h = 0.0
    for event in data.get("vpp_event_log", []) or []:
        total_duration_h += _event_duration_h(event)
    if total_duration_h > 0.0 and data.get("vpp_window_energy_kwh") is not None:
        try:
            data["vpp_window_energy_avg_per_hour_kwh"] = round(
                float(data["vpp_window_energy_kwh"]) / total_duration_h,
                4,
            )
        except (TypeError, ValueError):
            pass
    data["vpp_energy_reduction_basis"] = "unavailable_without_same_run_no_dr_counterfactual"
    existing_daily_values = _coerce_daily_energy_values(data.get("daily_energy_kwh") or data.get("energy_kwh_per_day"))
    meter_daily_values = _read_meter_daily_energy(run_path)
    daily_values = existing_daily_values or meter_daily_values
    if daily_values and not data.get("daily_energy_kwh"):
        data["daily_energy_kwh"] = daily_values
    if daily_values:
        daily_by_day = {
            int(item["day"]): item
            for item in daily_values
            if isinstance(item, dict) and str(item.get("day", "")).isdigit()
        }
        for event in data.get("vpp_event_log", []) or []:
            try:
                day = int(event.get("day"))
            except (TypeError, ValueError):
                continue
            if day in daily_by_day and not event.get("daily_energy_kwh"):
                event["daily_energy_kwh"] = daily_by_day[day]["energy_kwh"]


def _normalize_dashboard_result(data: dict) -> None:
    """Backfill optional dashboard fields so old/new result JSONs render alike."""
    for event in data.get("vpp_event_log", []) or []:
        trace = event.get("strategy_trace") or {}
        if not event.get("strategy_candidates") and trace.get("candidates"):
            event["strategy_candidates"] = trace.get("candidates") or []
        if not event.get("selected_strategy") and trace.get("selected_strategy"):
            event["selected_strategy"] = trace.get("selected_strategy") or {}
        event.setdefault("strategy_candidates", [])
        event.setdefault("selected_strategy", {})


def _json_safe(value):
    """Return browser-parseable JSON data by replacing NaN/Inf with null."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>EnergyBridge Agent Console</title>
  <style>
    :root {
      --ink: #1f2933;
      --muted: #667085;
      --line: #d8dee7;
      --paper: #f7f3ea;
      --panel: #ffffff;
      --leaf: #25705a;
      --clay: #b5533c;
      --sky: #2e6f9e;
      --gold: #be8a22;
      --shadow: 0 18px 45px rgba(31, 41, 51, .12);
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      color: var(--ink);
      background:
        linear-gradient(135deg, rgba(37,112,90,.09), transparent 34%),
        linear-gradient(315deg, rgba(190,138,34,.13), transparent 40%),
        var(--paper);
      font-family: ui-sans-serif, "Avenir Next", "Segoe UI", "Noto Sans SC", sans-serif;
    }
    .shell {
      min-height: 100vh;
      display: grid;
      grid-template-columns: 320px 1fr;
      transition: grid-template-columns .2s ease;
    }
    .shell.sidebar-collapsed { grid-template-columns: 0 1fr; }
    aside {
      border-right: 1px solid var(--line);
      background: rgba(255,255,255,.72);
      backdrop-filter: blur(14px);
      padding: 24px 18px;
      position: sticky;
      top: 0;
      height: 100vh;
      overflow: auto;
      transition: opacity .15s ease, padding .2s ease;
      z-index: 3;
    }
    .shell.sidebar-collapsed aside {
      opacity: 0;
      pointer-events: none;
      padding-left: 0;
      padding-right: 0;
    }
    main { padding: 26px; overflow: hidden; }
    h1 { font-size: 26px; line-height: 1.1; margin: 0 0 8px; letter-spacing: 0; }
    h2 { font-size: 18px; margin: 0 0 12px; }
    h3 { font-size: 15px; margin: 0 0 10px; color: var(--muted); font-weight: 700; }
    .sub { color: var(--muted); font-size: 13px; line-height: 1.5; margin-bottom: 20px; }
    select, textarea, input, button {
      width: 100%;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 10px 11px;
      background: #fff;
      color: var(--ink);
      font: inherit;
    }
    button {
      cursor: pointer;
      background: var(--ink);
      color: #fff;
      border-color: var(--ink);
      font-weight: 700;
    }
    button.secondary { background: #fff; color: var(--ink); }
    .sidebar-toggle {
      position: fixed;
      left: 14px;
      bottom: 14px;
      z-index: 5;
      width: auto;
      border-radius: 999px;
      padding: 10px 14px;
      box-shadow: var(--shadow);
    }
    .run-list { display: grid; gap: 8px; margin-top: 14px; }
    .run-btn {
      text-align: left;
      background: #fff;
      color: var(--ink);
      border: 1px solid var(--line);
      font-weight: 650;
    }
    .run-btn.active { border-color: var(--leaf); box-shadow: inset 4px 0 0 var(--leaf); }
    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      gap: 18px;
      margin-bottom: 18px;
    }
    .title-block { max-width: 860px; }
    .brand-title { font-size: 44px; letter-spacing: -1px; }
    .result-name {
      display: inline-flex;
      gap: 8px;
      align-items: center;
      margin: 2px 0 14px;
      color: var(--muted);
      font-weight: 750;
      overflow-wrap: anywhere;
    }
    .result-name strong { color: var(--ink); }
    .pill-row { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 10px; }
    .pill {
      border: 1px solid var(--line);
      background: rgba(255,255,255,.82);
      border-radius: 999px;
      padding: 6px 10px;
      font-size: 13px;
      color: var(--muted);
    }
    .grid { display: grid; gap: 16px; }
    .metrics { grid-template-columns: repeat(4, minmax(140px, 1fr)); }
    .panel {
      background: rgba(255,255,255,.88);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: var(--shadow);
      padding: 16px;
    }
    .metric .label { color: var(--muted); font-size: 12px; }
    .metric .value { font-size: 26px; font-weight: 800; margin-top: 6px; }
    .event-layout { grid-template-columns: 1fr; align-items: start; }
    .events { display: grid; gap: 14px; }
    .event {
      border-left: 5px solid var(--sky);
      transition: transform .15s ease, box-shadow .15s ease;
    }
    .event:hover { transform: translateY(-1px); }
    .event-head { display: flex; justify-content: space-between; gap: 12px; margin-bottom: 12px; }
    .score { font-size: 28px; font-weight: 850; color: var(--leaf); }
    .kv {
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 10px;
      margin: 12px 0;
    }
    .kv div { padding: 10px; background: #f8faf9; border: 1px solid #edf0f2; border-radius: 7px; }
    .kv span { display: block; color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .timeline { display: grid; gap: 8px; margin-top: 10px; }
    .decision {
      display: grid;
      grid-template-columns: 72px 86px 1fr;
      gap: 10px;
      align-items: start;
      padding: 9px 0;
      border-top: 1px solid #eef1f3;
      font-size: 13px;
    }
    .actions { color: var(--muted); overflow-wrap: anywhere; }
    .command-row { display: grid; grid-template-columns: 1fr 120px; gap: 10px; }
    .preset-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 0 0 10px; }
    .preset-grid button { padding: 9px; }
    .field-row { margin: 0 0 12px; }
    .field-row label { display: block; color: var(--muted); font-size: 12px; font-weight: 800; margin-bottom: 6px; }
    .inline-fields { display: grid; grid-template-columns: 120px 1fr; gap: 8px; }
    .live-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 10px 0; }
    .live-card { background: #f8faf9; border: 1px solid #edf0f2; border-radius: 7px; padding: 9px; }
    .live-card span { display: block; color: var(--muted); font-size: 11px; margin-bottom: 3px; }
    .live-card strong {
      display: block;
      min-width: 0;
      font-size: 16px;
      line-height: 1.35;
      white-space: normal;
      overflow-wrap: anywhere;
    }
    .dialogue-feed {
      max-height: 220px;
      overflow: auto;
      display: grid;
      gap: 8px;
      background: #f8faf9;
      border: 1px solid #edf0f2;
      border-radius: 7px;
      padding: 10px;
      margin-top: 10px;
    }
    .dialogue-item {
      padding: 8px 10px;
      border-radius: 7px;
      background: #fff;
      border-left: 4px solid var(--sky);
      line-height: 1.45;
      white-space: pre-wrap;
      overflow-wrap: anywhere;
    }
    .dialogue-item.user { border-left-color: var(--clay); }
    .dialogue-item.grid { border-left-color: var(--gold); }
    .dialogue-item.result { border-left-color: var(--leaf); }
    .dialogue-item small { display: block; color: var(--muted); margin-bottom: 3px; font-weight: 700; }
    .progress-viz {
      display: grid;
      grid-template-columns: repeat(3, minmax(220px, 1fr));
      gap: 12px;
      margin-top: 10px;
    }
    .progress-card {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      border-top: 4px solid var(--sky);
      min-height: 170px;
      min-width: 0;
    }
    .progress-card.active { border-top-color: var(--gold); box-shadow: 0 10px 24px rgba(190, 138, 34, .15); }
    .progress-card.done { border-top-color: var(--leaf); }
    .progress-card h4 { margin: 0 0 8px; font-size: 15px; }
    .progress-row {
      display: grid;
      grid-template-columns: 88px minmax(0, 1fr);
      gap: 8px;
      align-items: start;
      padding: 5px 0;
      border-top: 1px solid #eef1f3;
      font-size: 12px;
    }
    .progress-row span { color: var(--muted); }
    .progress-row strong {
      min-width: 0;
      line-height: 1.42;
      white-space: normal;
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    .progress-log { margin-top: 8px; display: grid; gap: 5px; }
    .progress-chip {
      min-width: 0;
      font-size: 12px;
      line-height: 1.45;
      border-radius: 12px;
      padding: 7px 9px;
      background: #f8faf9;
      border: 1px solid #edf0f2;
      white-space: normal;
      overflow-wrap: anywhere;
      word-break: break-word;
    }
    .progress-section { margin-top: 10px; }
    .progress-section strong { display: block; font-size: 12px; margin-bottom: 5px; color: var(--muted); }
    .appliance-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 6px; }
    .appliance-pill { font-size: 12px; border-radius: 7px; padding: 6px 8px; background: #fffdf8; border: 1px solid #eee2c9; overflow-wrap: anywhere; word-break: break-word; }
    .terminal {
      height: 260px;
      overflow: auto;
      background: #101820;
      color: #d9f0e3;
      border-radius: 7px;
      padding: 12px;
      font: 12px/1.45 ui-monospace, "SFMono-Regular", Consolas, monospace;
      white-space: pre-wrap;
    }
    .summary-log {
      max-height: 520px;
      overflow: auto;
      background: #fffdf8;
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 12px;
      margin-top: 10px;
      white-space: pre-wrap;
      font: 12px/1.55 ui-monospace, "SFMono-Regular", Consolas, monospace;
    }
    .human-dialogue {
      background: #fffdf8;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      margin-top: 12px;
    }
    .human-custom { display: grid; grid-template-columns: 1fr 110px; gap: 10px; }
    .empty { padding: 40px; text-align: center; color: var(--muted); }
    @media (max-width: 980px) {
      .shell { grid-template-columns: 1fr; }
      .shell.sidebar-collapsed { grid-template-columns: 1fr; }
      aside { position: relative; height: auto; }
      .metrics, .event-layout, .progress-viz { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <button id="sidebarToggle" class="sidebar-toggle secondary">Hide history</button>
  <div class="shell" id="shell">
    <aside id="sidebar">
      <h1>EnergyBridge</h1>
      <div class="sub">Agent decisions and VPP capacity quantification console</div>
      <button id="newRunButton">New run</button>
      <div style="height:14px"></div>
      <label for="dateSelect">Date</label>
      <select id="dateSelect"></select>
      <div style="height:10px"></div>
      <label for="runSelect">Method / result</label>
      <select id="runSelect"></select>
    </aside>
    <main>
      <div id="app" class="empty">Loading benchmark results...</div>
    </main>
  </div>
  <script>
    const state = {
      runs: [],
      personas: [],
      active: null,
      data: null,
      view: 'run',
      sidebarOpen: true,
      eventId: 'vpp1',
      jobId: null,
      jobPoll: null,
      selectedDate: '',
      selectedMethod: 'EnergyBridge',
      selectedPersona: 'basic_role_a_commuter_price_cooperative',
      selectedCity: 'Tianjin',
      selectedDays: 3,
      startDate: '',
      priceCsv: '',
      priceCsvManual: false,
      vppStartHour: 18,
      vppDurationHours: 1,
      vppEventsJson: '',
      userMode: 'roleplay',
      humanName: 'human_user'
    };
    const $ = (id) => document.getElementById(id);
    const fmt = (n, d=2) => Number.isFinite(Number(n)) ? Number(n).toFixed(d) : 'N/A';
    const pct = (n) => Number.isFinite(Number(n)) ? `${(Number(n)*100).toFixed(0)}%` : 'N/A';
    const DAY_OPTIONS = [
      {value: 3, label: '3day'},
      {value: 7, label: '7day'}
    ];
    const START_DATE_OPTIONS = [
      {value: '', label: 'Default date'},
      {value: '2025-06-01', label: '2025-06-01'}
    ];
    const PRICE_CSV_DEFAULTS = {
      Germany: 'experiments/real_data/germany_2025_price.csv'
    };

    async function api(path, opts) {
      const res = await fetch(path, opts);
      if (!res.ok) throw new Error(await res.text());
      return res.json();
    }

    async function loadRuns() {
      state.runs = await api('/api/runs');
      const dates = [...new Set(state.runs.map(r => r.date).filter(Boolean))].sort().reverse();
      if (!state.selectedDate || !dates.includes(state.selectedDate)) {
        state.selectedDate = dates[0] || '';
      }
      renderHistorySelectors();
      render();
    }

    async function loadPersonas() {
      state.personas = await api('/api/personas');
      if (!state.personas.some(p => p.id === state.selectedPersona) && state.personas[0]) {
        state.selectedPersona = state.personas[0].id;
      }
    }

    function renderHistorySelectors() {
      const dates = [...new Set(state.runs.map(r => r.date).filter(Boolean))].sort().reverse();
      const dateSelect = $('dateSelect');
      if (dateSelect) {
        dateSelect.innerHTML = dates.length
          ? dates.map(date => `<option value="${date}" ${state.selectedDate === date ? 'selected' : ''}>${date}</option>`).join('')
          : '<option value="">No results</option>';
        dateSelect.onchange = () => {
          state.selectedDate = dateSelect.value;
          state.active = null;
          renderHistorySelectors();
        };
      }
      const runs = state.runs.filter(r => r.date === state.selectedDate);
      const runSelect = $('runSelect');
      if (runSelect) {
        runSelect.innerHTML = '<option value="">Select result...</option>' + runs.map(r => {
          const status = r.has_result ? '' : 'incomplete · ';
          return `<option value="${r.id}" ${state.active === r.id ? 'selected' : ''}>${status}${r.label}</option>`;
        }).join('');
        runSelect.onchange = () => loadRun(runSelect.value);
      }
    }

    async function loadRun(id) {
      if (!id) return;
      state.view = 'history';
      state.active = id;
      state.data = await api(`/api/run?id=${encodeURIComponent(id)}`);
      state.eventId = (state.data.vpp_event_log?.[0]?.id) || 'vpp1';
      $('runSelect').value = id;
      renderHistorySelectors();
      render();
    }

    function clockHour(value) {
      const raw = Number(value || 0);
      let hour = ((raw % 24) + 24) % 24;
      let hh = Math.floor(hour);
      let mm = Math.round((hour - hh) * 60);
      if (mm >= 60) {
        hh = (hh + 1) % 24;
        mm = 0;
      }
      return `${String(hh).padStart(2, '0')}:${String(mm).padStart(2, '0')}`;
    }

    function eventTitle(ev, index) {
      const day = index + 1;
      const start = ev.trigger_h !== undefined ? ev.trigger_h : 18 + index * 24;
      const end = ev.end_h !== undefined ? ev.end_h : 19 + index * 24;
      return `${ev.id || `vpp${day}`} · Day ${ev.day || day} ${clockHour(start)}-${clockHour(end)}`;
    }

    function actionsLine(actions) {
      const entries = Object.entries(actions || {}).filter(([, v]) => v !== null && v !== undefined);
      if (!entries.length) return 'keep current strategy';
      return entries.map(([k, v]) => `${k}=${v}`).join(' · ');
    }

    function slug(value) {
      return String(value || '').replace(/[^a-zA-Z0-9]+/g, '_').replace(/^_+|_+$/g, '').toLowerCase();
    }

    function personaRunLabel(personaId) {
      const match = String(personaId || '').match(/^basic_role_([a-z])(?:_|$)/);
      return match ? `role_${match[1]}` : (slug(personaId) || 'persona');
    }

    function methodRunToken(method) {
      if (method === 'agent' || String(method || '').toLowerCase() === 'energybridge') return 'EnergyBridge';
      return method === 'mpc_dynamic' ? `${method}_H6` : (method || 'EnergyBridge');
    }

    function todayIso() {
      return new Date().toISOString().slice(0, 10);
    }

    function expectedRunName() {
      normalizeScenarioSelection();
      const prefix = state.userMode === 'human'
        ? `${slug(state.humanName) || 'human'}_human`
        : personaRunLabel(state.selectedPersona);
      return `${todayIso()}/${prefix}_${methodRunToken(state.selectedMethod)}_${String(state.selectedCity || 'Tianjin').toLowerCase()}_${Number(state.selectedDays || 3)}days`;
    }

    function normalizeScenarioSelection() {
      const selectedDays = Number(state.selectedDays || 3);
      state.selectedDays = DAY_OPTIONS.some(item => item.value === selectedDays) ? selectedDays : 3;
      const allowedDates = START_DATE_OPTIONS.map(item => item.value);
      if (!allowedDates.includes(state.startDate)) state.startDate = '';
      syncDefaultPriceCsv();
    }

    function defaultPriceCsvForCity(city) {
      return PRICE_CSV_DEFAULTS[String(city || '')] || '';
    }

    function syncDefaultPriceCsv(force = false) {
      if (!force && state.priceCsvManual) return;
      state.priceCsv = defaultPriceCsvForCity(state.selectedCity);
      state.priceCsvManual = false;
    }

    function runNameFromOutput(outputDir) {
      const raw = String(outputDir || '').replaceAll('\\\\', '/');
      const marker = '/benchmark_results/';
      if (raw.includes(marker)) return raw.split(marker).pop();
      const parts = raw.split('/').filter(Boolean);
      return parts.length >= 2 ? `${parts[parts.length - 2]}/${parts[parts.length - 1]}` : (raw || expectedRunName());
    }

    function appliancePlanFromActions(actions) {
      const plan = {};
      const a = actions || {};
      if (a.washer_skip) plan.washer = 'skipped';
      else if (a.washer_start_h !== undefined && a.washer_start_h !== null) plan.washer = `scheduled@${Number(a.washer_start_h).toFixed(1)}`;
      if (a.dishwasher_skip) plan.dishwasher = 'skipped';
      else if (a.dishwasher_start_h !== undefined && a.dishwasher_start_h !== null) plan.dishwasher = `scheduled@${Number(a.dishwasher_start_h).toFixed(1)}`;
      if (a.water_heater_preheat) {
        const start = a.water_heater_preheat_start_h ?? '?';
        const end = a.water_heater_preheat_end_h ?? '?';
        const temp = a.water_heater_preheat_temp_c ?? '?';
        plan.water_heater = `preheat ${start}-${end} @ ${temp}°C`;
      }
      if (a.ev_charge_start_h !== undefined && a.ev_charge_start_h !== null && a.ev_charge_end_h !== undefined && a.ev_charge_end_h !== null) {
        plan.ev = `charge ${Number(a.ev_charge_start_h).toFixed(1)}-${Number(a.ev_charge_end_h).toFixed(1)}`;
      } else if (a.ev_mode) {
        plan.ev = `mode ${a.ev_mode} only`;
      }
      return plan;
    }

    function vppTargetText(ev) {
      const tq = ev?.total_quantification_90 || {};
      const kw = ev?.demand_target_kw
        ?? ev?.demand_target_shed_kwh
        ?? tq.vpp_target_capacity_120_kw
        ?? (Number.isFinite(Number(tq.avg_reported_capacity_90_kw)) ? Number(tq.avg_reported_capacity_90_kw) * 1.2 : null);
      return Number.isFinite(Number(kw)) ? `${fmt(kw, 3)} kW` : 'N/A';
    }

    function dailyTotalEnergyKwh(data, day) {
      const targetDay = Number(day);
      const dailyRaw = data?.daily_energy_kwh ?? data?.energy_kwh_per_day ?? [];
      const daily = Array.isArray(dailyRaw)
        ? dailyRaw
        : Object.entries(dailyRaw || {}).map(([key, value]) => (
            value && typeof value === 'object' ? {day: Number(key), ...value} : {day: Number(key), energy_kwh: Number(value)}
          ));
      const exact = daily.find((item, idx) => {
        if (typeof item === 'number') return idx + 1 === targetDay;
        return Number(item.day ?? item.date_index) === targetDay;
      });
      if (typeof exact === 'number' && Number.isFinite(Number(exact))) return Number(exact);
      if (exact && Number.isFinite(Number(exact.energy_kwh))) return Number(exact.energy_kwh);
      if (exact && Number.isFinite(Number(exact.total_energy_kwh))) return Number(exact.total_energy_kwh);
      const priceDaily = data?.day_ahead_price_metrics?.per_day || [];
      const priced = priceDaily.find(item => Number(item.day) === targetDay);
      if (priced && Number.isFinite(Number(priced.energy_kwh))) return Number(priced.energy_kwh);
      if (priced && Number.isFinite(Number(priced.total_energy_kwh))) return Number(priced.total_energy_kwh);
      return null;
    }

    function dayEnergyText(data, ev) {
      const day = ev?.day;
      const eventDaily = Number.isFinite(Number(ev?.daily_energy_kwh)) ? Number(ev.daily_energy_kwh) : null;
      const total = eventDaily !== null ? eventDaily : dailyTotalEnergyKwh(data, day);
      const vpp = Number.isFinite(Number(ev?.actual_kwh)) ? Number(ev.actual_kwh) : null;
      if (total !== null && vpp !== null) return `${fmt(total, 1)} kWh today / ${fmt(vpp, 3)} kWh VPP`;
      if (total !== null) return `${fmt(total, 1)} kWh today / VPP waiting`;
      if (vpp !== null) return `today waiting / ${fmt(vpp, 3)} kWh VPP`;
      return 'waiting';
    }

    function candidateStrategiesFromEvent(ev, decisions) {
      const trace = ev.strategy_trace || {};
      const candidates = ev.strategy_candidates || trace.candidates || [];
      const lines = candidates.map(item => {
        if (typeof item === 'string') return item;
        const base = `[${item.id || '?'}] ${item.label || ''} - ${item.description || ''}`;
        const tradeoff = item.tradeoff ? ` (${item.tradeoff})` : '';
        const appliancePlan = item.appliance_plan || item.appliance_plan_cn;
        const appliance = appliancePlan ? ` | Appliance control: ${appliancePlan}` : '';
        const pref = item.user_pref ? ` | User preference: ${item.user_pref}` : '';
        return `${base}${tradeoff}${appliance}${pref}`;
      }).filter(Boolean);
      if (lines.length) return lines;
      if (decisions.length) {
        return decisions.map(item => `${fmt(item.h % 24, 1)}h -> ${fmt(item.sp, 1)}°C | ${item.reason || 'no explanation'}`);
      }
      return ['not recorded in this run'];
    }

    function metricCardsInner(items) {
      return items.map(([label, value]) => `<div class="live-card"><span>${label}</span><strong>${value}</strong></div>`).join('');
    }

    function resultMetricsHtml(items) {
      return `<div class="live-grid" id="liveMetrics">${metricCardsInner(items)}</div>`;
    }

    function progressCardsHtml(progress, emptyText = 'Waiting for results...') {
      return progress && progress.length
        ? progress.map(ev => `
          <div class="progress-card ${ev.status}">
            <h4>${ev.label}</h4>
            <div class="progress-row"><span>Day</span><strong>${ev.day}</strong></div>
            <div class="progress-row"><span>VPP target</span><strong>${ev.target}</strong></div>
            <div class="progress-row"><span>AC setpoint</span><strong>${ev.setpoint}</strong></div>
            <div class="progress-row"><span>User score</span><strong>${ev.score}</strong></div>
            <div class="progress-row"><span>Energy</span><strong>${ev.energy}</strong></div>
            <div class="progress-row"><span>Selected strategy</span><strong>${ev.selectedStrategy}</strong></div>
            <div class="progress-section">
              <strong>Candidates / decision log</strong>
              <div class="progress-log">
                ${ev.strategies.length ? ev.strategies.map(item => `<div class="progress-chip">${item}</div>`).join('') : '<div class="progress-chip">waiting</div>'}
              </div>
            </div>
            <div class="progress-section">
              <strong>Appliance schedule</strong>
              <div class="appliance-grid">
                ${Object.keys(ev.appliances).length
                  ? Object.entries(ev.appliances).map(([name, text]) => `<div class="appliance-pill">${name}: ${text}</div>`).join('')
                  : '<div class="appliance-pill">waiting</div>'}
              </div>
            </div>
            <div class="progress-log">
              ${ev.notes.map(note => `<div class="progress-chip">${note}</div>`).join('')}
            </div>
          </div>`).join('')
        : `<div class="sub">${emptyText}</div>`;
    }

    function historyToLive(d) {
      const events = d.vpp_event_log || [];
      const progress = events.map((ev, idx) => {
        const decisions = ev.day_decisions || [];
        const lastDecision = decisions[decisions.length - 1] || {};
        const actions = ev.vpp_trigger_actions || lastDecision.actions || lastDecision.raw_appliance_actions || {};
        const trace = ev.strategy_trace || {};
        const selected = ev.selected_strategy || trace.selected_strategy || {};
        const selectedDesc = selected.description ? ` - ${selected.description}` : '';
        const selectedTradeoff = selected.tradeoff ? ` (${selected.tradeoff})` : '';
        const selectedPref = selected.preference_text ? ` | Preference: ${selected.preference_text}` : '';
        const selectedReason = selected.selection_meta?.reason ? ` | Selection reason: ${selected.selection_meta.reason}` : '';
        const candidateStrategies = candidateStrategiesFromEvent(ev, decisions);
        return {
          key: String(idx + 1),
          label: `Day ${idx + 1}`,
          status: 'done',
          day: String(idx + 1),
          target: vppTargetText(ev),
          setpoint: ev.setpoint !== undefined && ev.setpoint !== null ? `${fmt(ev.setpoint, 1)}°C` : 'N/A',
          score: ev.score !== undefined && ev.score !== null ? `${ev.score}/5 ${ev.label || ''}` : 'N/A',
          energy: dayEnergyText(d, ev),
          selectedStrategy: selected.id
            ? `[${selected.id}] ${selected.label || ''}${selectedDesc}${selectedTradeoff} (${selected.source || ev.source || 'selected'})${selectedPref}${selectedReason}`
            : (ev.user_input || ev.label || ev.source || 'not recorded in this run'),
          strategies: candidateStrategies,
          appliances: appliancePlanFromActions(actions),
          notes: [ev.reason, ev.comment].filter(Boolean).slice(-5)
        };
      });
      const dialogue = events.flatMap((ev, idx) => [
        {type: 'agent', label: `Event ${idx + 1} Agent`, text: ev.reason || 'no Agent rationale'},
        {type: 'result', label: `Event ${idx + 1} Score`, text: `${ev.score ?? 'N/A'}/5 ${ev.label || ''} | ${ev.comment || ''}`}
      ]);
      return {progress, dialogue};
    }

    function renderEvent(ev, index) {
      const tq = ev.total_quantification_90 || {};
      const actualShed = ev.actual_shed_kwh;
      const targetShed = ev.demand_target_shed_kwh ?? tq.vpp_target_capacity_energy_kwh;
      const shedText = actualShed == null
        ? 'n/a without no-DR counterfactual'
        : `${fmt(actualShed, 3)} / ${fmt(targetShed, 3)} kWh`;
      const decisions = ev.day_decisions || [];
      return `
        <section class="panel event" data-event="${ev.id}">
          <div class="event-head">
            <div>
              <h2>${eventTitle(ev, index)}</h2>
              <div class="sub">${ev.reason || 'no Agent rationale'}</div>
            </div>
            <div class="score">${ev.score ?? 'N/A'}/5</div>
          </div>
          <div class="kv">
            <div><span>VPP shed target</span>${vppTargetText(ev)}</div>
            <div><span>Actual shed</span>${shedText}</div>
            <div><span>Reference Pbase diagnostic</span>${fmt(ev.reference_pbase_minus_actual_kwh, 3)} kWh</div>
            <div><span>Equivalent energy cap</span>${fmt(ev.demand_target_kwh, 3)} kWh</div>
            <div><span>Actual VPP energy</span>${fmt(ev.actual_kwh, 3)} kWh</div>
            <div><span>90% firm capacity</span>${fmt(tq.avg_reported_capacity_90_kw, 3)} kW</div>
            <div><span>1.2x target capacity</span>${fmt(tq.vpp_target_capacity_120_kw, 3)} kW</div>
          </div>
          <h3>Daily decision trajectory</h3>
          <div class="timeline">
            ${decisions.map(d => `
              <div class="decision">
                <strong>${fmt(d.h % 24, 1)}h</strong>
                <span>${fmt(d.sp, 1)}°C</span>
                <div>
                  <div>${d.reason || 'no explanation'}</div>
                  <div class="actions">${actionsLine(d.actions || d.raw_appliance_actions)}</div>
                </div>
              </div>`).join('')}
          </div>
        </section>`;
    }

    function defaultCommand() {
      return commandPreset(state.selectedMethod, state.selectedPersona, state.userMode, state.humanName);
    }

    function shellQuote(value) {
      return `'${String(value || '').replaceAll("'", "'\"'\"'")}'`;
    }

    function commandPreset(method, persona, userMode, humanName) {
      normalizeScenarioSelection();
      const base = `conda run --no-capture-output -n energybridge python experiments/benchmark/run_persona_json.py ${persona || state.selectedPersona}`;
      const controllerMethod = method || 'EnergyBridge';
      const horizon = controllerMethod === 'mpc_dynamic' ? ' --mpc-horizon 6' : '';
      const human = userMode === 'human'
        ? ` --user-mode human --human-name ${shellQuote(humanName || 'human')}`
        : ' --user-mode roleplay';
      const city = ` --city ${state.selectedCity || 'Tianjin'} --days ${Number(state.selectedDays || 3)}`;
      const start = state.startDate ? ` --start-date ${shellQuote(state.startDate)}` : '';
      const price = state.priceCsv ? ` --price-csv ${shellQuote(state.priceCsv)}` : '';
      const vpp = ` --vpp-start-hour ${Number(state.vppStartHour || 18)} --vpp-duration-hours ${Number(state.vppDurationHours || 1)}`;
      const vppJson = state.vppEventsJson ? ` --vpp-events-json ${shellQuote(state.vppEventsJson)}` : '';
      return `${base} --method ${controllerMethod}${horizon}${human}${city}${start}${price}${vpp}${vppJson}`;
    }

    function methodLabel(method) {
      return {
        EnergyBridge: 'EnergyBridge',
        agent: 'EnergyBridge',
        mpc_dynamic: 'MPC Dynamic',
        rule_milp: 'Rule+MILP',
        no_dr: 'No-DR',
        rl_ppo_pref_v2: 'RL PPO Pref-v2'
      }[method] || method;
    }

    function syncCommandFromSelections() {
      const input = $('commandInput');
      if (input) input.value = defaultCommand();
      const resultName = $('resultName');
      if (resultName) resultName.textContent = expectedRunName();
    }

    function ensureProgressEvent(events, key) {
      if (!events[key]) {
        events[key] = {
          key,
          label: `Day ${key}`,
          status: 'active',
          day: '—',
          target: 'waiting',
          setpoint: 'waiting',
          score: 'waiting',
          energy: 'waiting',
          strategies: [],
          selectedStrategy: 'waiting',
          appliances: {},
          notes: []
        };
      }
      return events[key];
    }

    function setProgressEventDay(ev, key, dayValue) {
      const day = dayValue && dayValue !== '—' ? String(dayValue) : String(key);
      ev.day = day;
      ev.label = day === String(key) ? `Day ${day}` : `Day ${day} / Event ${key}`;
    }

    function parseLive(logs) {
      const text = (logs || []).join('\n');
      const latest = (pattern) => {
        const matches = [...text.matchAll(pattern)];
        return matches.length ? matches[matches.length - 1] : null;
      };
      const day = latest(/\[(?:AC|MPC) Agent[^\]]*Day(\d+)/g)?.[1] || latest(/--- Day\s+(\d+)\s+(?:start|daily plan)/g)?.[1] || '—';
      const event = latest(/VPP Demand-Response Event\s+(\d+)\/\d+/g)?.[1] || '—';
      const energy = latest(/\[family\/[^\]]+\]\s+exit=\d+\s+energy=([0-9.]+)kWh\s+vpp_window=([0-9.]+)kWh/g);
      const score = latest(/User score:\s*([0-9.]+)\/5\s+\(([^)]+)\)/g);
      const sp = latest(/\[(?:AC|MPC) Agent[^\]]*\].*?setpoint(?:→|->)([0-9.]+)/g);
      const demand = latest(/shed_target=([0-9.]+)kW\s+\(cap=([0-9.]+)kWh\)/g);
      const output = latest(/^OUTPUT\s+:\s+(.+)$/gm);
      const dialogue = [];
      const progressEvents = {};
      let currentKey = null;
      let currentDay = '—';
      let currentEventKey = null;
      let candidateEventKey = null;
      const eventDays = {};
      const eventKeyFromLine = (line) => {
        const patterns = [
          /\bvpp(\d+)\b/i,
          /event=(\d+)/i,
          /VPP event\s+(\d+)/i,
          /VPP Demand-Response Event\s+(\d+)\/\d+/i,
          /\[VPP Result \| Event\s+(\d+)\/\d+/i,
          /\[Human Score Selected\s+\|\s+event=(\d+)\]/i,
          /\[Strategy Selected\s+\|\s+event=(\d+)\]/i
        ];
        for (const pattern of patterns) {
          const match = line.match(pattern);
          if (match) return match[1];
        }
        return null;
      };
      const ensureEventForKey = (key, dayHint = null) => {
        const ev = ensureProgressEvent(progressEvents, key);
        const mappedDay = dayHint || eventDays[key] || (currentDay !== '—' ? currentDay : key);
        eventDays[key] = mappedDay;
        setProgressEventDay(ev, key, mappedDay);
        return ev;
      };
      for (const raw of logs || []) {
        const line = raw.trim();
        if (!line) continue;
        const inferredEventKey = eventKeyFromLine(line);
        const dayMatch = line.match(/--- Day\s+(\d+)\s+(?:start|daily plan)/);
        if (dayMatch) {
          currentDay = dayMatch[1];
          currentKey = currentDay;
          currentEventKey = null;
          const dayEv = ensureProgressEvent(progressEvents, currentDay);
          setProgressEventDay(dayEv, currentDay, currentDay);
        }
        const acDayMatch = line.match(/\[(?:AC|MPC) Agent[^\]]*Day(\d+)/);
        if (acDayMatch) {
          currentDay = acDayMatch[1];
          currentKey = inferredEventKey || currentEventKey || currentDay;
          const dayEv = ensureProgressEvent(progressEvents, currentKey);
          setProgressEventDay(dayEv, currentKey, currentDay);
          dayEv.status = 'active';
        }
        if (inferredEventKey) {
          currentEventKey = inferredEventKey;
          currentKey = inferredEventKey;
          ensureEventForKey(inferredEventKey);
        }
        const eventMatch = line.match(/VPP Demand-Response Event\s+(\d+)\/\d+/);
        if (eventMatch) {
          currentEventKey = eventMatch[1];
          currentKey = eventMatch[1];
          const ev = ensureEventForKey(currentKey);
          ev.status = 'active';
          ev.notes.push('VPP event started');
        }
        const candidateHeader = line.match(/\[Strategy Candidates\s*\|\s*VPP event\s+(\d+)\]/i);
        if (candidateHeader) {
          candidateEventKey = candidateHeader[1];
          currentEventKey = candidateEventKey;
          currentKey = candidateEventKey;
          ensureEventForKey(currentKey);
        }
        const candidateMatch = line.match(/^(?:[│|]\s*)?\[([ABC])\]\s+(.+)$/);
        if (candidateMatch && candidateEventKey) {
          const ev = ensureEventForKey(candidateEventKey);
          ev.strategies.push(`[${candidateMatch[1]}] ${candidateMatch[2].trim()}`);
        }
        const candidateApplianceMatch = line.match(/^(?:[│|]\s*)?Appliance control:\s+(.+)/i);
        if (candidateApplianceMatch && candidateEventKey) {
          const ev = ensureEventForKey(candidateEventKey);
          if (ev.strategies.length) {
            ev.strategies[ev.strategies.length - 1] += ` | Appliance control: ${candidateApplianceMatch[1].trim()}`;
          } else {
            ev.strategies.push(`Appliance control: ${candidateApplianceMatch[1].trim()}`);
          }
        }
        const resultMatch = line.match(/\[VPP Result \| Event\s+(\d+)\/\d+/);
        if (resultMatch) {
          currentKey = resultMatch[1];
          currentEventKey = currentKey;
          ensureEventForKey(currentKey);
        }
        const humanScoreMatch = line.match(/\[Human Score Selected\s+\|\s+event=(\d+)\]\s+(?:→|->)\s+([0-9.]+)\/5\s+\|\s*(.*)/);
        if (humanScoreMatch) {
          currentKey = humanScoreMatch[1];
          currentEventKey = currentKey;
          const scoreEv = ensureEventForKey(currentKey);
          scoreEv.score = `${humanScoreMatch[2]}/5 human`;
          scoreEv.status = 'done';
          const comment = humanScoreMatch[3].trim();
          if (comment && comment !== '-') scoreEv.notes.push(`Human feedback: ${comment}`);
        }
        const webInputMatch = line.match(/\[web human input(?:\s+\|\s+kind=([^ ]+)\s+event=([^\]]+))?\]\s*(.*)/);
        if (webInputMatch) {
          const kind = webInputMatch[1] || 'unknown';
          const inputEvent = webInputMatch[2] || '';
          const value = (webInputMatch[3] || '').trim() || 'Enter default';
          const targetKey = inputEvent && inputEvent !== '?' ? inputEvent : currentKey;
          if (targetKey) {
            const inputEv = ensureEventForKey(targetKey);
            if (kind === 'strategy_choice') {
              inputEv.notes.push(`Human strategy input: ${value}`);
            } else if (kind === 'score') {
              inputEv.score = `${value}/5 human`;
              inputEv.notes.push(`Human score input: ${value}`);
            } else if (kind === 'score_comment') {
              inputEv.notes.push(`Human score feedback: ${value}`);
            }
          }
          dialogue.push({type: 'user', label: 'Human Input', text: line});
          continue;
        }
        if (!currentKey) continue;
        const ev = ensureEventForKey(currentKey);
        const selectedMatch = line.match(/\[Strategy Selected\s+\|\s+event=(\d+)\]\s+(?:→|->)\s+(.+)/);
        if (selectedMatch) {
          const selectedEv = ensureEventForKey(selectedMatch[1]);
          selectedEv.selectedStrategy = selectedMatch[2].trim();
        }
        const targetMatch = line.match(/shed_target=([0-9.]+)kW\s+\(cap=([0-9.]+)kWh\)/);
        if (targetMatch) {
          const targetEv = inferredEventKey ? ensureEventForKey(inferredEventKey) : ev;
          targetEv.target = `${targetMatch[1]} kW`;
        }
        const acMatch = line.match(/\[(?:AC|MPC) Agent[^\]]*\].*?setpoint(?:→|->)([0-9.]+)/);
        if (acMatch) {
          ev.setpoint = `${acMatch[1]}°C`;
        }
        const scoreMatch = line.match(/User score:\s*([0-9.]+)\/5\s+\(([^)]+)\)/);
        if (scoreMatch) {
          ev.score = `${scoreMatch[1]}/5 ${scoreMatch[2]}`;
          ev.status = 'done';
        }
        const energyMatch = line.match(/\[family\/[^\]]+\]\s+exit=\d+\s+energy=([0-9.]+)kWh\s+vpp_window=([0-9.]+)kWh/);
        if (energyMatch) {
          ev.notes.push(`Run summary: ${energyMatch[1]} kWh total / ${energyMatch[2]} kWh VPP`);
        }
        const shiftMatch = line.match(/\[Appliance\]\s+shift\s+(\w+)\s+day=(\d+)\s+hod=([0-9.]+)\s+->\s+(\w+)/);
        if (shiftMatch) {
          const dayKey = String(Number(shiftMatch[2]) + 1);
          const applianceEv = ensureProgressEvent(progressEvents, dayKey);
          setProgressEventDay(applianceEv, dayKey, dayKey);
          applianceEv.appliances[shiftMatch[1]] = `scheduled@${Number(shiftMatch[3]).toFixed(1)} (${shiftMatch[4]})`;
        }
        const skipMatch = line.match(/\[Appliance\]\s+skip\s+(\w+)\s+day=(\d+)\s+->\s+(\w+)/);
        if (skipMatch) {
          const dayKey = String(Number(skipMatch[2]) + 1);
          const applianceEv = ensureProgressEvent(progressEvents, dayKey);
          setProgressEventDay(applianceEv, dayKey, dayKey);
          applianceEv.appliances[skipMatch[1]] = `skipped (${skipMatch[3]})`;
        }
        const whMatch = line.match(/\[Appliance\]\s+water_heater preheat schedule:\s+start=([^ ]+)\s+end=([^ ]+)\s+temp=([^ ]+)\s+->\s+(\w+)/);
        if (whMatch) {
          const whEv = ensureProgressEvent(progressEvents, currentKey);
          whEv.appliances.water_heater = `preheat ${whMatch[1]}-${whMatch[2]} @ ${whMatch[3]}°C (${whMatch[4]})`;
        }
        const evModeMatch = line.match(/\[Appliance\]\s+ev mode=([^ ]+)\s+->\s+(\w+)/);
        if (evModeMatch) {
          ev.appliances.ev = `mode ${evModeMatch[1]} (${evModeMatch[2]})`;
        }
        const evWindowMatch = line.match(/\[Appliance\]\s+ev charge_window=([^ ]+)\s+->\s+(\w+)/);
        if (evWindowMatch) {
          ev.appliances.ev = `charge window ${evWindowMatch[1]} (${evWindowMatch[2]})`;
        }
        if (line.includes('[VPP Grid Agent]')) {
          dialogue.push({type: 'grid', label: 'Grid VPP', text: line});
          ev.notes.push(line.replace('[VPP Grid Agent]', '').trim());
        } else if (line.includes('[Strategy Selected')) {
          dialogue.push({type: 'user', label: 'User Strategy', text: line});
          ev.notes.push(line);
        } else if (line.includes('[Human Score Selected')) {
          dialogue.push({type: 'result', label: 'Human Score', text: line});
          ev.notes.push(line);
        } else if (line.includes('[AC Agent') || line.includes('[MPC Agent')) {
          dialogue.push({type: 'agent', label: 'Agent', text: line});
          ev.notes.push(line);
        } else if (line.includes('[VPP Result')) {
          dialogue.push({type: 'result', label: 'Role-play Score', text: line});
          ev.notes.push(line);
        } else if (line.includes('[LLM stats')) {
          dialogue.push({type: 'result', label: 'LLM Stats', text: line});
        }
        if (ev.notes.length > 5) ev.notes = ev.notes.slice(-5);
      }
      const progress = Object.values(progressEvents)
        .sort((a, b) => Number(a.key) - Number(b.key));
      return {
        day,
        event,
        energy: energy ? `${energy[1]} kWh` : 'running',
        vpp: energy ? `${energy[2]} kWh` : 'running',
        score: score ? `${score[1]}/5 ${score[2]}` : 'waiting',
        sp: sp ? `${sp[1]}°C` : 'waiting',
        demand: demand ? `${demand[1]} kW` : 'waiting',
        cap: demand ? `${demand[2]} kWh` : 'waiting',
        outputName: output ? runNameFromOutput(output[1].trim()) : expectedRunName(),
        dialogue,
        progress
      };
    }

    function renderOps(events) {
      normalizeScenarioSelection();
      const methods = ['EnergyBridge', 'mpc_dynamic', 'rule_milp', 'rl_ppo_pref_v2'];
      const personaOptions = state.personas.map(p => `
        <option value="${p.id}" ${state.selectedPersona === p.id ? 'selected' : ''}>
          ${p.label} · ${p.name}
        </option>`).join('');
      const userTypeField = state.userMode === 'human' ? `
        <div class="field-row">
          <label for="humanNameInput">2. User type</label>
          <input id="humanNameInput" value="${state.humanName}" placeholder="Enter a human user name, e.g. alice">
        </div>` : `
        <div class="field-row">
          <label for="personaSelect">2. User type</label>
          <select id="personaSelect">${personaOptions}</select>
        </div>`;
      const humanInputPanel = state.userMode === 'human' ? `
        <div class="human-dialogue">
          <h3>Human-in-loop input</h3>
          <div class="sub">Follow the current terminal prompt: enter A/B/C for strategy, 1-5 for score, or a short feedback sentence. Sending an empty value means pressing Enter.</div>
          <div class="human-custom">
            <input id="humanCustomInput" placeholder="Enter A/B/C, 1-5, feedback text, or leave blank">
            <button id="sendHumanCustom">Enter</button>
          </div>
        </div>` : '';
      return `
        <div class="grid">
          <section class="panel">
            <h2>Live run</h2>
            <div class="result-name">Result name: <strong id="resultName">${expectedRunName()}</strong></div>
            <div class="sub">Select a user and method, then run. Logs and results will appear below.</div>
            <h3>1. User category</h3>
            <div class="preset-grid">
              <button class="${state.userMode === 'roleplay' ? '' : 'secondary'}" data-user-mode="roleplay">Role-play LLM</button>
              <button class="${state.userMode === 'human' ? '' : 'secondary'}" data-user-mode="human">Human</button>
            </div>
            ${userTypeField}
            <div class="field-row">
              <label for="citySelect">3. Scenario</label>
              <select id="citySelect">
                ${['Tianjin', 'Beijing', 'Shanghai', 'Germany'].map(c => `<option value="${c}" ${state.selectedCity === c ? 'selected' : ''}>${c}</option>`).join('')}
              </select>
            </div>
            <div class="field-row">
              <label for="daysSelect">Days / start date</label>
              <div class="inline-fields">
                <select id="daysSelect">
                  ${DAY_OPTIONS.map(item => `<option value="${item.value}" ${Number(state.selectedDays || 3) === item.value ? 'selected' : ''}>${item.label}</option>`).join('')}
                </select>
                <select id="startDateSelect">
                  ${START_DATE_OPTIONS.map(item => `<option value="${item.value}" ${state.startDate === item.value ? 'selected' : ''}>${item.label}</option>`).join('')}
                </select>
              </div>
            </div>
            <div class="field-row">
              <label for="priceCsvInput">Advanced: day-ahead price CSV</label>
              <input id="priceCsvInput" value="${state.priceCsv}" placeholder="Leave empty if no price file is used; you can edit the CSV path manually">
            </div>
            <div class="field-row">
              <label for="vppStartHourInput">VPP window: start / duration (hours)</label>
              <div class="inline-fields">
                <input id="vppStartHourInput" type="number" step="0.25" min="0" max="23.99" value="${Number(state.vppStartHour || 18)}">
                <input id="vppDurationHoursInput" type="number" step="0.25" min="0.25" value="${Number(state.vppDurationHours || 1)}">
              </div>
            </div>
            <div class="field-row">
              <label for="vppEventsJsonInput">Advanced: VPP events JSON</label>
              <input id="vppEventsJsonInput" value="${state.vppEventsJson}" placeholder="Optional; e.g. experiments/benchmark/configs/vpp_events_7day_variable.json">
            </div>
            <h3>4. Method</h3>
            <div class="preset-grid">
              ${methods.map(m => `<button class="${state.selectedMethod === m ? '' : 'secondary'}" data-method="${m}">${methodLabel(m)}</button>`).join('')}
            </div>
            <div class="command-row">
              <input id="commandInput" value="${defaultCommand()}">
              <button id="startRun">Run ${state.userMode === 'human' ? 'Human' : 'Role-play LLM'}</button>
            </div>
            <div class="live-grid" id="liveMetrics">
              <div class="live-card"><span>Day/Event</span><strong>-</strong></div>
              <div class="live-card"><span>Setpoint</span><strong>waiting</strong></div>
              <div class="live-card"><span>VPP target</span><strong>waiting</strong></div>
              <div class="live-card"><span>User score</span><strong>waiting</strong></div>
            </div>
            <div style="height:10px"></div>
            <h3>Step-by-step visualization</h3>
            <div class="progress-viz" id="progressViz"><div class="sub">Visualization cards will appear event by event during the run.</div></div>
            <div style="height:10px"></div>
            <h3>Live log summary / dialogue</h3>
            <div class="dialogue-feed" id="dialogueFeed"><div class="sub">Grid, Agent, user choices, role-play scores, and LLM stats will appear during the run.</div></div>
            <div id="finishedSummary"></div>
            <div style="height:10px"></div>
            <div class="terminal" id="runLog">Waiting for a run command...</div>
            ${humanInputPanel}
          </section>
        </div>`;
    }

    function renderRunPage() {
      $('app').innerHTML = `
        <div class="topbar">
          <div class="title-block">
            <h1 class="brand-title">EnergyBridge</h1>
          </div>
        </div>
        ${renderOps()}`;
      bindRunControls();
    }

    function renderHistoryPage() {
      const d = state.data;
      const live = historyToLive(d);
      const resultName = d._run_id || runNameFromOutput(d.output_dir);
      $('app').innerHTML = `
        <div class="topbar">
          <div class="title-block">
            <h1 class="brand-title">EnergyBridge</h1>
          </div>
        </div>
        <div class="grid">
          <section class="panel">
            <h2>Historical result</h2>
            <div class="result-name">Result name: <strong>${resultName}</strong></div>
            ${resultMetricsHtml([
              ['Method', methodLabel(d.method || 'unknown')],
              ['City', d.weather || 'unknown'],
              ['User score', `${fmt(d.user_pref_score, 1)}/5`],
              ['Total energy', `${fmt(d.energy_kwh_total, 1)} kWh`],
              ['VPP energy', `${fmt(d.vpp_window_energy_kwh, 3)} kWh`],
              ['VPP energy / h', `${fmt(d.vpp_window_energy_avg_per_hour_kwh, 3)} kWh`],
              ['Appliance shift', pct(d.appliance_shift_success_rate)],
              ['Status', `exit ${d.exit_code}`]
            ])}
            <div style="height:10px"></div>
            <h3>Step-by-step visualization</h3>
            <div class="progress-viz">${progressCardsHtml(live.progress, 'No event records.')}</div>
            <div style="height:10px"></div>
            <h3>Log summary / dialogue</h3>
            <div class="dialogue-feed">
              ${live.dialogue.length
                ? live.dialogue.map(item => `<div class="dialogue-item ${item.type}"><small>${item.label}</small>${item.text}</div>`).join('')
                : '<div class="sub">No dialogue records.</div>'}
            </div>
            ${d.run_summary_text ? `<h3>Full user log / run_summary.txt</h3><div class="summary-log">${d.run_summary_text}</div>` : ''}
          </section>
        </div>`;
    }

    function bindRunControls() {
      $('startRun').onclick = startRun;
      document.querySelectorAll('[data-method]').forEach(btn => {
        btn.onclick = () => {
          state.selectedMethod = btn.dataset.method;
          render();
        };
      });
      const personaSelect = $('personaSelect');
      if (personaSelect) {
        personaSelect.onchange = (e) => {
          state.selectedPersona = e.target.value;
          syncCommandFromSelections();
        };
      }
      const humanNameInput = $('humanNameInput');
      if (humanNameInput) {
        humanNameInput.oninput = (e) => {
          state.humanName = e.target.value;
          syncCommandFromSelections();
        };
      }
      const citySelect = $('citySelect');
      if (citySelect) {
        citySelect.onchange = (e) => {
          state.selectedCity = e.target.value;
          if (state.selectedCity === 'Germany') {
            state.selectedDays = 7;
            state.startDate = state.startDate || '2025-06-01';
          }
          syncDefaultPriceCsv(true);
          normalizeScenarioSelection();
          render();
        };
      }
      const daysSelect = $('daysSelect');
      if (daysSelect) {
        daysSelect.onchange = (e) => {
          state.selectedDays = Number(e.target.value || 3);
          normalizeScenarioSelection();
          syncCommandFromSelections();
        };
      }
      const startDateSelect = $('startDateSelect');
      if (startDateSelect) {
        startDateSelect.onchange = (e) => {
          state.startDate = e.target.value;
          normalizeScenarioSelection();
          syncCommandFromSelections();
        };
      }
      const priceCsvInput = $('priceCsvInput');
      if (priceCsvInput) {
        priceCsvInput.oninput = (e) => {
          state.priceCsv = e.target.value.trim();
          state.priceCsvManual = true;
          syncCommandFromSelections();
        };
      }
      const vppStartHourInput = $('vppStartHourInput');
      if (vppStartHourInput) {
        vppStartHourInput.oninput = (e) => {
          state.vppStartHour = Number(e.target.value || 18);
          syncCommandFromSelections();
        };
      }
      const vppDurationHoursInput = $('vppDurationHoursInput');
      if (vppDurationHoursInput) {
        vppDurationHoursInput.oninput = (e) => {
          state.vppDurationHours = Math.max(0.25, Number(e.target.value || 1));
          syncCommandFromSelections();
        };
      }
      const vppEventsJsonInput = $('vppEventsJsonInput');
      if (vppEventsJsonInput) {
        vppEventsJsonInput.oninput = (e) => {
          state.vppEventsJson = e.target.value.trim();
          syncCommandFromSelections();
        };
      }
      document.querySelectorAll('[data-user-mode]').forEach(btn => {
        btn.onclick = () => {
          state.userMode = btn.dataset.userMode;
          render();
        };
      });
      document.querySelectorAll('[data-human-input]').forEach(btn => {
        btn.onclick = () => sendHumanInput(btn.dataset.humanInput, btn.dataset.humanKind || '');
      });
      const humanCustom = $('sendHumanCustom');
      if (humanCustom) {
        humanCustom.onclick = () => {
          const input = $('humanCustomInput');
          sendHumanInput(input.value.trim(), 'custom');
          input.value = '';
        };
        const input = $('humanCustomInput');
        if (input) {
          input.onkeydown = (event) => {
            if (event.key === 'Enter') {
              event.preventDefault();
              sendHumanInput(input.value.trim(), 'custom');
              input.value = '';
            }
          };
        }
      }
    }

    function render() {
      const shell = $('shell');
      shell.classList.toggle('sidebar-collapsed', !state.sidebarOpen);
      $('sidebarToggle').textContent = state.sidebarOpen ? 'Hide history' : 'Open history';
      $('newRunButton').onclick = () => {
        state.view = 'run';
        state.active = null;
      state.data = null;
        const runSelect = $('runSelect');
        if (runSelect) runSelect.value = '';
        renderHistorySelectors();
        render();
      };
      $('sidebarToggle').onclick = () => {
        state.sidebarOpen = !state.sidebarOpen;
        render();
      };
      if (state.view === 'history' && state.data) renderHistoryPage();
      else renderRunPage();
    }

    async function startRun() {
      const command = $('commandInput').value.trim();
      const res = await api('/api/start_run', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({command})
      });
      state.jobId = res.job_id;
      $('runLog').textContent = `job ${state.jobId} queued...\n`;
      if (state.jobPoll) clearInterval(state.jobPoll);
      state.jobPoll = setInterval(pollJob, 1500);
      pollJob();
    }

    async function pollJob() {
      if (!state.jobId) return;
      let job;
      try {
        job = await api(`/api/job?id=${encodeURIComponent(state.jobId)}`);
      } catch (err) {
        if (state.jobPoll) clearInterval(state.jobPoll);
        state.jobPoll = null;
        const log = $('runLog');
        if (log) {
          log.textContent += `\n[web] The current job no longer exists, possibly because the dashboard restarted. Please run it again.`;
          log.scrollTop = log.scrollHeight;
        }
        state.jobId = null;
        return;
      }
      const log = $('runLog');
      if (log) {
        log.textContent = `[${job.status}] ${job.command}\n\n${(job.logs || []).join('\n')}`;
        log.scrollTop = log.scrollHeight;
      }
      renderLiveJob(job);
      if (job.status === 'succeeded' || job.status === 'failed') {
        clearInterval(state.jobPoll);
        state.jobPoll = null;
        await loadRuns();
        const refreshedLog = $('runLog');
        if (refreshedLog) {
          refreshedLog.textContent = `[${job.status}] ${job.command}\n\n${(job.logs || []).join('\n')}`;
          refreshedLog.scrollTop = refreshedLog.scrollHeight;
        }
        renderLiveJob(job);
      }
    }

    function renderLiveJob(job) {
      const live = parseLive(job.logs || []);
      const resultData = job.benchmark_result || null;
      const resultLive = resultData ? historyToLive(resultData) : null;
      const shownProgress = resultLive ? resultLive.progress : live.progress;
      const shownDialogue = resultLive ? resultLive.dialogue : live.dialogue;
      const resultName = $('resultName');
      if (resultName) resultName.textContent = live.outputName || expectedRunName();
      const metrics = $('liveMetrics');
      if (metrics) {
        const firstEvent = resultData?.vpp_event_log?.[0] || null;
        metrics.innerHTML = metricCardsInner([
          ['Day/Event', `D${live.day} / E${live.event}`],
          ['Setpoint', resultData?.agent_setpoint_c ? `${fmt(resultData.agent_setpoint_c, 1)}°C` : live.sp],
          ['VPP target', firstEvent ? vppTargetText(firstEvent) : live.demand],
          ['User score', resultData?.user_pref_score ? `${fmt(resultData.user_pref_score, 1)}/5` : live.score],
          ['Total energy', resultData?.energy_kwh_total ? `${fmt(resultData.energy_kwh_total, 1)} kWh` : live.energy],
          ['VPP energy', resultData?.vpp_window_energy_kwh ? `${fmt(resultData.vpp_window_energy_kwh, 3)} kWh` : live.vpp],
          ['Status', job.status]
        ]);
      }
      const feed = $('dialogueFeed');
      if (feed) {
        feed.innerHTML = shownDialogue.length
          ? shownDialogue.map(item => `<div class="dialogue-item ${item.type}"><small>${item.label}</small>${item.text}</div>`).join('')
          : '<div class="sub">Grid, Agent, user choices, and role-play scores will appear after the run starts.</div>';
        feed.scrollTop = feed.scrollHeight;
      }
      const progressViz = $('progressViz');
      if (progressViz) {
        progressViz.innerHTML = progressCardsHtml(shownProgress, 'Visualization cards will appear event by event during the run.');
      }
      const summary = $('finishedSummary');
      if (summary) {
        if (job.run_summary_text) {
          summary.innerHTML = `<h3>Full user log / run_summary.txt</h3><div class="summary-log">${job.run_summary_text}</div>`;
        } else {
          summary.innerHTML = '';
        }
      }
    }

    async function sendHumanInput(text, kind = '') {
      const log = $('runLog');
      if (!state.jobId) {
        if (log) log.textContent += '\n[web] No running human job is available yet.';
        return;
      }
      await api('/api/job_input', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({job_id: state.jobId, text, kind})
      });
      if (log) {
        log.textContent += `\n[web] sent human input${kind ? ` (${kind})` : ''}: ${text || '(default enter)'}`;
        log.scrollTop = log.scrollHeight;
      }
    }

    (async function boot() {
      await loadPersonas();
      await loadRuns();
    })().catch(err => $('app').textContent = `Load failed: ${err.message}`);
  </script>
</body>
</html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    results_dir: Path = DEFAULT_RESULTS_DIR

    def do_HEAD(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path in {"/", "/api/runs", "/api/run"}:
            self.send_response(HTTPStatus.OK)
            self.end_headers()
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._send_text(INDEX_HTML, "text/html; charset=utf-8")
            return
        if parsed.path == "/api/runs":
            self._send_json(self._list_runs())
            return
        if parsed.path == "/api/personas":
            self._send_json(_list_personas())
            return
        if parsed.path == "/api/run":
            run_id = parse_qs(parsed.query).get("id", [""])[0]
            run_path = self._resolve_run(run_id)
            if run_path is None:
                self._send_error(HTTPStatus.NOT_FOUND, "Unknown run")
                return
            result_path = run_path / "benchmark_result.json"
            if result_path.exists():
                try:
                    data = json.loads(result_path.read_text(encoding="utf-8"))
                    data["method"] = self._canonical_method_id(data.get("method") or self._infer_method_from_run_name(run_path.name))
                    _apply_meter_vpp_energy(data, run_path)
                    _normalize_dashboard_result(data)
                except Exception as exc:
                    rel = run_path.relative_to(self.results_dir).as_posix()
                    data = {
                        "_run_id": rel,
                        "output_dir": str(run_path),
                        "method": self._canonical_method_id(self._infer_method_from_run_name(run_path.name)),
                        "weather": self._infer_city_from_run_name(run_path.name),
                        "exit_code": "incomplete",
                        "vpp_event_log": [],
                        "error": f"benchmark_result.json could not be loaded yet: {exc}",
                    }
            else:
                rel = run_path.relative_to(self.results_dir).as_posix()
                data = {
                    "_run_id": rel,
                    "output_dir": str(run_path),
                    "method": self._canonical_method_id(self._infer_method_from_run_name(run_path.name)),
                    "weather": self._infer_city_from_run_name(run_path.name),
                    "exit_code": "incomplete",
                    "vpp_event_log": [],
                    "error": "benchmark_result.json not found; run may still be running or stopped before summary export.",
                }
            data["_run_id"] = run_id
            summary_path = run_path / "run_summary.txt"
            if summary_path.exists():
                data["run_summary_text"] = summary_path.read_text(encoding="utf-8", errors="replace")
            elif not result_path.exists():
                err_path = run_path / "eplusout.err"
                if err_path.exists():
                    data["run_summary_text"] = err_path.read_text(encoding="utf-8", errors="replace")
            self._send_json(data)
            return
        if parsed.path == "/api/job":
            job_id = parse_qs(parsed.query).get("id", [""])[0]
            with JOBS_LOCK:
                existing = dict(JOBS.get(job_id, {}))
            if existing and existing.get("status") in {"succeeded", "failed"} and not existing.get("run_summary_text"):
                _attach_finished_summary(job_id)
            with JOBS_LOCK:
                job = dict(JOBS.get(job_id, {}))
            if not job:
                self._send_error(HTTPStatus.NOT_FOUND, "Unknown job")
                return
            self._send_json(job)
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/start_run":
            self._handle_start_run()
            return
        if parsed.path == "/api/job_input":
            self._handle_job_input()
            return
        self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _handle_start_run(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            command = str(payload.get("command", "")).strip()
            argv = _validate_run_command(command)
            job_id = uuid.uuid4().hex[:12]
            with JOBS_LOCK:
                JOBS[job_id] = {
                    "id": job_id,
                    "status": "queued",
                    "command": command,
                    "argv": argv,
                    "logs": [],
                    "created_at": datetime.now().isoformat(timespec="seconds"),
                }
            thread = threading.Thread(target=_run_command_job, args=(job_id, argv), daemon=True)
            thread.start()
            self._send_json({"ok": True, "job_id": job_id})
        except Exception as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

    def _handle_job_input(self) -> None:
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            job_id = str(payload.get("job_id", ""))
            text = str(payload.get("text", ""))
            requested_kind = str(payload.get("kind", ""))
            if "\n" in text or "\r" in text:
                raise ValueError("Input must be a single line")
            with JOBS_LOCK:
                proc = JOB_PROCS.get(job_id)
                logs = list(JOBS.get(job_id, {}).get("logs", []))
            if proc is None or proc.stdin is None or proc.poll() is not None:
                self._send_error(HTTPStatus.NOT_FOUND, "Job is not accepting input")
                return
            kind, event_id = _infer_human_input_context(logs, requested_kind=requested_kind)
            proc.stdin.write(text + "\n")
            proc.stdin.flush()
            _append_job_log(job_id, f"[web human input | kind={kind} event={event_id or '?'}] {text}")
            self._send_json({"ok": True})
        except Exception as exc:
            self._send_error(HTTPStatus.BAD_REQUEST, str(exc))

    def log_message(self, fmt: str, *args) -> None:
        print(f"[dashboard] {self.address_string()} - {fmt % args}")

    def _list_runs(self) -> list[dict]:
        runs = []
        for run_dir in sorted((p for p in self.results_dir.glob("*/*") if p.is_dir()), reverse=True):
            result_path = run_dir / "benchmark_result.json"
            data = {}
            if result_path.exists():
                try:
                    data = json.loads(result_path.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            rel = run_dir.relative_to(self.results_dir).as_posix()
            method = self._canonical_method_id(data.get("method") or self._infer_method_from_run_name(run_dir.name))
            city = data.get("weather") or self._infer_city_from_run_name(run_dir.name)
            runs.append({
                "id": rel,
                "label": run_dir.name,
                "date": rel.split("/", 1)[0] if "/" in rel else "",
                "persona": run_dir.name.split("_", 2)[0],
                "method": method,
                "city": city,
                "score": data.get("user_pref_score"),
                "has_result": result_path.exists(),
            })
        return runs

    @staticmethod
    def _canonical_method_id(method: str) -> str:
        key = str(method or "").strip().lower()
        if key in {"agent", "energybridge"}:
            return "EnergyBridge"
        if key == "mpc":
            return "mpc_dynamic"
        if key in {"rule_milp", "rule+milp", "pmv_milp"}:
            return "rule_milp"
        if key in {"rl", "rl_ppo", "rl_ppo_pref_v2", "rl_pref_v2"}:
            return "rl_ppo_pref_v2"
        return str(method or "unknown")

    @staticmethod
    def _infer_method_from_run_name(name: str) -> str:
        lower_name = name.lower()
        if "_mpc_dynamic" in lower_name:
            return "mpc_dynamic"
        if "_rule_milp" in lower_name or "_pmv_milp" in lower_name:
            return "rule_milp"
        if "_rl_ppo_pref_v2" in lower_name:
            return "rl_ppo_pref_v2"
        if "_rl_" in lower_name:
            return "rl_ppo_pref_v2"
        if "_energybridge_" in lower_name or "_agent_" in lower_name:
            return "EnergyBridge"
        return "unknown"

    @staticmethod
    def _infer_city_from_run_name(name: str) -> str:
        parts = name.split("_")
        for city in ("tianjin", "beijing", "shanghai", "germany"):
            if city in parts:
                return city
        return ""

    def _resolve_run(self, run_id: str) -> Path | None:
        if not run_id or ".." in Path(run_id).parts:
            return None
        run_path = (self.results_dir / run_id).resolve()
        try:
            run_path.relative_to(self.results_dir.resolve())
        except ValueError:
            return None
        if not run_path.is_dir():
            return None
        return run_path

    def _send_json(self, data: object) -> None:
        raw = json.dumps(_json_safe(data), ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_text(self, text: str, content_type: str) -> None:
        raw = text.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        raw = json.dumps({"error": message}, ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve EnergyBridge benchmark dashboard")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    args = parser.parse_args()

    DashboardHandler.results_dir = args.results_dir.resolve()
    server = ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    print(f"EnergyBridge dashboard: http://{args.host}:{args.port}")
    print(f"Reading results from: {DashboardHandler.results_dir}")
    server.serve_forever()


if __name__ == "__main__":
    main()
