"""Unified metric extraction for EnergyBridge benchmark.

Extracts a flat metric dict from:
  - existing trajectory JSON files (logs/trajectory_*.json)
  - AgentResult-like objects produced by EplusEnv or the benchmark runner

Metric categories (see docs/metrics_schema.md for full taxonomy):
  - api_metrics        : LLM runtime (latency, tokens, model)
  - control_metrics    : agent decision (action, setpoint, safety, execution)
  - vpp_metrics        : grid demand request vs agent estimate
  - user_metrics       : satisfaction score, preference weights
  - physical_snapshot  : building state at event time (from EP or mock)
  - energy_proxy       : agent-estimated energy reduction (NOT physical measurement)
  - future_physical    : placeholder fields — require .eso/.csv parsing

Hard constraints:
  - Do NOT modify energybridge/simulation/.
  - Use None for unavailable fields; never crash on missing keys.
  - Physical trajectory metrics are NOT implemented here.
    They require .eso/.csv parsing (simulator-side work).
"""

from __future__ import annotations

import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Required field list — all output dicts have exactly these keys
# ---------------------------------------------------------------------------

REQUIRED_METRIC_FIELDS: list[str] = [
    # Source metadata
    "source_path", "run_id", "scenario_id", "agent_id", "timestamp",

    # API / LLM runtime
    "api_latency_seconds", "total_tokens", "prompt_tokens", "completion_tokens",
    "llm_model", "llm_provider", "api_success",

    # Control / action
    "agent_triggered", "valid_control_plan", "action_type", "setpoint",
    "duration_minutes", "execution_status", "written_actuators",
    "safety_ok", "safety_violations",

    # VPP / grid
    "vpp_task_type", "requested_reduction_kw", "estimated_reduction_kw",
    "estimated_vpp_compliance", "event_start_time", "event_end_time",
    "trigger_hour", "sim_hour",

    # User preference / satisfaction
    "user_satisfaction_score", "comfort_score", "preference_learning_score",
    "comfort_priority", "cost_priority", "grid_priority",
    "user_feedback_text",

    # Physical snapshot (event-level, NOT trajectory-level)
    "indoor_temp_at_event", "outdoor_temp_at_event",
    "hvac_power_kw_at_event", "facility_power_kw_at_event",
    "hvac_setpoint_at_event", "occupancy",

    # Energy / comfort proxy (estimates, NOT physical measurements)
    "estimated_energy_kwh", "estimated_cost",
    "simple_temp_deviation", "comfort_violation_flag",

    # Future physical trajectory metrics (placeholders, not implemented)
    "actual_energy_kwh", "actual_peak_power_kw", "actual_peak_reduction_kw",
    "comfort_violation_minutes", "mean_temperature_deviation",
    "setpoint_tracking_error", "post_action_temperature_delta",
]

# Metric source labels for documentation
METRIC_SOURCES: dict[str, str] = {
    "api_latency_seconds": "llm_runtime",
    "total_tokens": "llm_runtime",
    "prompt_tokens": "llm_runtime",
    "completion_tokens": "llm_runtime",
    "llm_model": "llm_runtime",
    "llm_provider": "llm_runtime",
    "api_success": "llm_runtime",
    "agent_triggered": "api_control",
    "valid_control_plan": "api_control",
    "action_type": "agent_estimate",
    "setpoint": "agent_estimate",
    "execution_status": "api_control",
    "written_actuators": "api_control",
    "safety_ok": "api_control",
    "safety_violations": "api_control",
    "vpp_task_type": "vpp_context",
    "requested_reduction_kw": "vpp_context",
    "estimated_reduction_kw": "agent_estimate",
    "estimated_vpp_compliance": "proxy",
    "event_start_time": "vpp_context",
    "event_end_time": "vpp_context",
    "trigger_hour": "vpp_context",
    "sim_hour": "api_control",
    "user_satisfaction_score": "user_feedback",
    "comfort_score": "user_feedback",
    "preference_learning_score": "proxy",
    "comfort_priority": "user_feedback",
    "cost_priority": "user_feedback",
    "grid_priority": "user_feedback",
    "user_feedback_text": "user_feedback",
    "indoor_temp_at_event": "energyplus_snapshot",
    "outdoor_temp_at_event": "energyplus_snapshot",
    "hvac_power_kw_at_event": "energyplus_snapshot",
    "facility_power_kw_at_event": "energyplus_snapshot",
    "hvac_setpoint_at_event": "python_reflected",  # from ActuatorWriter, not EP read
    "occupancy": "hardcoded",
    "estimated_energy_kwh": "agent_estimate",
    "estimated_cost": "agent_estimate",
    "simple_temp_deviation": "proxy",
    "comfort_violation_flag": "proxy",
    "actual_energy_kwh": "future_placeholder",
    "actual_peak_power_kw": "future_placeholder",
    "actual_peak_reduction_kw": "future_placeholder",
    "comfort_violation_minutes": "future_placeholder",
    "mean_temperature_deviation": "future_placeholder",
    "setpoint_tracking_error": "future_placeholder",
    "post_action_temperature_delta": "future_placeholder",
}


def _safe_get(d: Any, *keys, default=None):
    """Safely traverse nested dicts."""
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k, default)
        if cur is None:
            return default
    return cur


def _blank_metrics() -> dict[str, Any]:
    """Return a dict with all required fields set to None."""
    return {k: None for k in REQUIRED_METRIC_FIELDS}


def _status_block(m: dict) -> dict:
    """Compute metric_status from a filled metrics dict."""
    api_ok = any(m.get(f) is not None
                 for f in ("api_latency_seconds", "total_tokens", "llm_model"))
    snap_ok = any(m.get(f) is not None
                  for f in ("indoor_temp_at_event", "outdoor_temp_at_event",
                            "hvac_power_kw_at_event", "facility_power_kw_at_event"))
    snap_partial = any(m.get(f) is not None
                       for f in ("indoor_temp_at_event", "hvac_power_kw_at_event"))
    user_ok = m.get("user_satisfaction_score") is not None

    return {
        "api_metrics": "available" if api_ok else "missing",
        "event_physical_snapshot": (
            "available" if snap_ok else
            ("partial" if snap_partial else "missing")
        ),
        "user_preference_metrics": "available" if user_ok else "missing",
        "physical_trajectory_metrics": "not_implemented",
    }


# ---------------------------------------------------------------------------
# Extract from trajectory JSON (non-EP path, two format variants)
# ---------------------------------------------------------------------------

def extract_metrics_from_trajectory(path: str | Path) -> dict[str, Any]:
    """Load one trajectory JSON and extract unified metrics.

    Handles both the older format (with top-level llm_metrics) and the
    newer format (llm_metrics embedded inside the metrics block).
    """
    path = Path(path)
    m = _blank_metrics()
    m["source_path"] = str(path)
    m["run_id"] = path.stem

    try:
        with open(path, encoding="utf-8") as f:
            t = json.load(f)
    except Exception as e:
        m["_load_error"] = str(e)
        m["metric_status"] = _status_block(m)
        return m

    # ── API / LLM runtime ─────────────────────────────────────────────────
    # Two formats: old has top-level "llm_metrics"; new has it inside "metrics"
    llm = t.get("llm_metrics") or _safe_get(t, "metrics", default={})
    token_usage = (
        _safe_get(llm, "token_usage") or
        _safe_get(t, "metrics", "token_usage") or {}
    )
    m["api_latency_seconds"] = (
        _safe_get(llm, "latency_seconds") or
        _safe_get(t, "metrics", "api_latency_seconds")
    )
    m["total_tokens"] = _safe_get(token_usage, "total_tokens")
    m["prompt_tokens"] = _safe_get(token_usage, "prompt_tokens")
    m["completion_tokens"] = _safe_get(token_usage, "completion_tokens")
    m["llm_model"] = (
        _safe_get(llm, "model") or
        _safe_get(t, "metrics", "llm_model")
    )
    m["llm_provider"] = _safe_get(llm, "provider")
    api_used = _safe_get(llm, "used") or _safe_get(t, "metrics", "api_used")
    m["api_success"] = bool(api_used) if api_used is not None else None

    # ── Control / action ──────────────────────────────────────────────────
    cp = t.get("control_plan") or {}
    sr = t.get("safety_report") or {}
    er = t.get("execution_result") or {}
    m["agent_triggered"] = True  # trajectory exists ⇒ agent ran
    m["valid_control_plan"] = bool(cp.get("action"))
    m["action_type"] = cp.get("action")
    m["setpoint"] = cp.get("setpoint") or cp.get("target_temperature")
    m["duration_minutes"] = cp.get("duration_minutes")
    m["execution_status"] = er.get("status")
    m["written_actuators"] = er.get("written") or er.get("actuator")
    m["safety_ok"] = sr.get("safe")
    m["safety_violations"] = sr.get("violations")

    # ── VPP / grid ────────────────────────────────────────────────────────
    vpp = t.get("vpp_context") or t.get("vpp_task") or {}
    tgs = t.get("translated_grid_signal") or {}
    m["vpp_task_type"] = vpp.get("vpp_task_type")
    m["requested_reduction_kw"] = (
        vpp.get("vpp_required_capacity_kw") or
        tgs.get("total_required_capacity_kw")
    )
    m["estimated_reduction_kw"] = (
        cp.get("estimated_reduction_kw") or
        _safe_get(t, "metrics", "expected_reduction_kw")
    )
    if m["estimated_reduction_kw"] is not None and m["requested_reduction_kw"] is not None:
        m["estimated_vpp_compliance"] = (
            float(m["estimated_reduction_kw"]) >= float(m["requested_reduction_kw"])
        )
    m["event_start_time"] = vpp.get("vpp_start_time")
    m["event_end_time"] = vpp.get("vpp_end_time")
    m["trigger_hour"] = None  # not stored in non-EP trajectory files
    m["sim_hour"] = None

    # ── User preference / satisfaction ────────────────────────────────────
    uf = t.get("user_feedback") or {}
    # old format: satisfaction_score; new format: user_satisfaction_score
    m["user_satisfaction_score"] = (
        uf.get("satisfaction_score") or
        _safe_get(t, "metrics", "user_satisfaction_score")
    )
    m["user_feedback_text"] = uf.get("comment")
    up = t.get("user_preferences") or {}
    m["comfort_priority"] = up.get("comfort_priority")
    m["cost_priority"] = up.get("cost_priority")
    m["grid_priority"] = up.get("grid_priority")

    # ── Physical snapshot ─────────────────────────────────────────────────
    hs = t.get("home_state") or {}
    m["indoor_temp_at_event"] = hs.get("indoor_temp")
    m["outdoor_temp_at_event"] = hs.get("outdoor_temp")
    m["hvac_power_kw_at_event"] = hs.get("hvac_power_kw")
    m["facility_power_kw_at_event"] = hs.get("facility_power_kw")
    m["hvac_setpoint_at_event"] = hs.get("hvac_setpoint")
    m["occupancy"] = hs.get("occupancy")

    # ── Energy / comfort proxy ────────────────────────────────────────────
    m["estimated_energy_kwh"] = _safe_get(t, "metrics", "expected_energy_kwh")
    m["estimated_cost"] = _safe_get(t, "metrics", "estimated_cost")
    if m["indoor_temp_at_event"] is not None and m["setpoint"] is not None:
        m["simple_temp_deviation"] = round(
            float(m["setpoint"]) - float(m["indoor_temp_at_event"]), 2
        )
    pref_max = up.get("preferred_temp_max")
    if m["indoor_temp_at_event"] is not None and pref_max is not None:
        m["comfort_violation_flag"] = float(m["indoor_temp_at_event"]) > float(pref_max)

    m["metric_status"] = _status_block(m)
    return m


# ---------------------------------------------------------------------------
# Extract from AgentResult-like object (EP path / benchmark runner)
# ---------------------------------------------------------------------------

def extract_metrics_from_agent_result(
    agent_result: Any,
    scenario: Optional[dict] = None,
    agent_id: str = "unknown",
) -> dict[str, Any]:
    """Extract unified metrics from an AgentResult-like object.

    Works with both the real AgentResult dataclass from EplusEnv and
    the lightweight stubs used by the benchmark runner's baseline mode.
    """
    m = _blank_metrics()
    m["run_id"] = f"ep_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    m["scenario_id"] = (scenario or {}).get("id", "")
    m["agent_id"] = agent_id
    m["agent_triggered"] = True
    m["sim_hour"] = getattr(agent_result, "sim_hour", None)
    m["trigger_hour"] = (scenario or {}).get("trigger_hour")

    # ── Control ───────────────────────────────────────────────────────────
    cp = getattr(agent_result, "control_plan", None) or {}
    sr = getattr(agent_result, "safety_report", None) or {}
    er = getattr(agent_result, "execution_result", None) or {}
    m["valid_control_plan"] = bool(cp.get("action"))
    m["action_type"] = cp.get("action")
    m["setpoint"] = cp.get("setpoint") or cp.get("target_temperature")
    m["duration_minutes"] = cp.get("duration_minutes")
    m["execution_status"] = er.get("status")
    m["written_actuators"] = er.get("written") or er.get("actuator")
    m["safety_ok"] = sr.get("safe")
    m["safety_violations"] = sr.get("violations")
    m["estimated_reduction_kw"] = cp.get("estimated_reduction_kw")

    # ── VPP ──────────────────────────────────────────────────────────────
    vpp = (scenario or {}).get("vpp_context", {})
    m["vpp_task_type"] = vpp.get("vpp_task_type")
    m["requested_reduction_kw"] = vpp.get("requested_reduction_kw")
    m["event_start_time"] = vpp.get("event_start_time")
    m["event_end_time"] = vpp.get("event_end_time")
    if m["estimated_reduction_kw"] is not None and m["requested_reduction_kw"] is not None:
        m["estimated_vpp_compliance"] = (
            float(m["estimated_reduction_kw"]) >= float(m["requested_reduction_kw"])
        )

    # ── Physical snapshot ─────────────────────────────────────────────────
    hs = getattr(agent_result, "home_state", None) or {}
    m["indoor_temp_at_event"] = hs.get("indoor_temp")
    m["outdoor_temp_at_event"] = hs.get("outdoor_temp")
    m["hvac_power_kw_at_event"] = hs.get("hvac_power_kw")
    m["facility_power_kw_at_event"] = hs.get("facility_power_kw")
    m["hvac_setpoint_at_event"] = hs.get("hvac_setpoint")
    m["occupancy"] = hs.get("occupancy")

    # ── Proxy ─────────────────────────────────────────────────────────────
    if m["indoor_temp_at_event"] is not None and m["setpoint"] is not None:
        m["simple_temp_deviation"] = round(
            float(m["setpoint"]) - float(m["indoor_temp_at_event"]), 2
        )

    m["metric_status"] = _status_block(m)
    return m


# ---------------------------------------------------------------------------
# Extract from all trajectories in a directory
# ---------------------------------------------------------------------------

def extract_metrics_from_dir(log_dir: str = "logs") -> list[dict[str, Any]]:
    """Extract metrics from all trajectory_*.json files in log_dir."""
    results = []
    for p in sorted(Path(log_dir).glob("trajectory_*.json")):
        results.append(extract_metrics_from_trajectory(p))
    return results


# ---------------------------------------------------------------------------
# Pretty-print
# ---------------------------------------------------------------------------

_PRINT_FIELDS = [
    ("agent_triggered",         "agent_triggered"),
    ("action_type",             "action_type"),
    ("setpoint",                "setpoint (°C)"),
    ("execution_status",        "execution_status"),
    ("safety_ok",               "safety_ok"),
    ("api_latency_seconds",     "api_latency_s"),
    ("total_tokens",            "total_tokens"),
    ("llm_model",               "llm_model"),
    ("user_satisfaction_score", "user_satisfaction"),
    ("indoor_temp_at_event",    "indoor_temp (°C)"),
    ("outdoor_temp_at_event",   "outdoor_temp (°C)"),
    ("hvac_power_kw_at_event",  "hvac_power_kw"),
    ("facility_power_kw_at_event", "facility_power_kw"),
    ("estimated_reduction_kw",  "est_reduction_kw"),
    ("estimated_vpp_compliance","est_vpp_compliance"),
    ("simple_temp_deviation",   "temp_deviation (°C)"),
]


def print_metric_summary(metrics: dict[str, Any]) -> None:
    """Pretty-print key metrics to terminal."""
    print("=== Benchmark Metrics ===")
    for key, label in _PRINT_FIELDS:
        val = metrics.get(key)
        if val is not None:
            print(f"  {label:<30} {val}")
        else:
            print(f"  {label:<30} (not available)")
    status = metrics.get("metric_status", {})
    if status:
        print("  --- metric status ---")
        for k, v in status.items():
            print(f"  {k:<30} {v}")
    src = metrics.get("source_path")
    if src:
        print(f"  source: {src}")


# ---------------------------------------------------------------------------
# Save / load
# ---------------------------------------------------------------------------

def save_metrics(metrics: dict[str, Any], output_path: str | Path) -> None:
    """Save metrics as JSON."""
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump(metrics, f, indent=2, ensure_ascii=False, default=str)


def find_latest_trajectory(log_dir: str = "logs") -> Optional[Path]:
    """Find the latest logs/trajectory_*.json file."""
    files = sorted(Path(log_dir).glob("trajectory_*.json"))
    return files[-1] if files else None


# ---------------------------------------------------------------------------
# CSV export
# ---------------------------------------------------------------------------

def export_metrics_to_csv(
    metrics_list: list[dict[str, Any]],
    output_path: str | Path,
) -> None:
    """Write a list of metric dicts to CSV."""
    if not metrics_list:
        return
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    # Use REQUIRED_METRIC_FIELDS as column order; add any extra keys found
    all_keys = list(REQUIRED_METRIC_FIELDS)
    for m in metrics_list:
        for k in m:
            if k not in all_keys:
                all_keys.append(k)
    with open(p, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        for m in metrics_list:
            writer.writerow({k: m.get(k) for k in all_keys})
