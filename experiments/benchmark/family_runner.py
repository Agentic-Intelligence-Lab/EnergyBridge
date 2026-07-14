"""Family home benchmark runner (PMV or Agent mode) — 3x VPP-1 events per 3-day sim."""
from __future__ import annotations
import hashlib
import re
import os, sys, json, random, shutil

# Fix Windows GBK encoding for Unicode status characters.
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
from dataclasses import dataclass, field
from datetime import date as _date, datetime as _datetime, timedelta as _timedelta
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass
EPLUS_ROOT = Path(os.getenv("EPLUS_ROOT", "/home/hku_user/EnergyPlus-24-1-0"))
BENCHMARK_DIR = Path(__file__).resolve().parent
for p in (str(EPLUS_ROOT), str(PROJECT_ROOT)):
    if p not in sys.path: sys.path.insert(0, p)

from energybridge.data.vpp_events import describe_vpp_events, make_daily_vpp_events
from energybridge.skills.vpp_participation_explainer import (
    finalize_vpp_participation_explanation,
    scoring_explanation_text,
)
from experiments.benchmark.strategy_explanations import english_only_text, normalize_vpp_strategy_explanation

_EXPERIMENTS_DIR = BENCHMARK_DIR.parent
DEFAULT_FAMILY_IDF = _EXPERIMENTS_DIR / "models" / "family_home" / "family_simple_3day.idf"
DEFAULT_FAMILY_EPW = _EXPERIMENTS_DIR / "weather" / "epw" / "CHN_TJ_Tianjin.545270_CSWD.epw"

OCCUPIED_START = 8.0; OCCUPIED_END = 22.0
PMV_MET = 1.1; PMV_CLO = 0.5; PMV_V = 0.1; PMV_RH = 55.0
PMV_DEADBAND = 0.5; SP_MIN = 22.0; SP_MAX = 28.0; SP_STEP = 0.5
SP_DEFAULT = 26.0; HTG_SP = 20.0; UNMET_TOL = 0.556
AC_OFF_FALLBACK_COOLING_SETPOINT = 40.0
AGENT_PRE_VPP_PRECOOL_LEAD_H = 1.5
AGENT_POST_VPP_RESTORE_H = 2.0

# 3x VPP-1: same event type, configured as absolute simulation-hour windows.
# The Agent prompt and appliance guardrails derive their timing from this list.
VPP_EVENTS = [
    {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1},
    {"id": "vpp2", "trigger_h": 42.0, "end_h": 43.0, "day": 2},
    {"id": "vpp3", "trigger_h": 66.0, "end_h": 67.0, "day": 3},
]
DEFAULT_PLANNING_HOUR = 0.0


def _make_vpp_events(sim_days: int, *, start_h: float = 18.0, duration_h: float = 1.0) -> list[dict]:
    """Create one same-time VPP event per simulated day."""
    return make_daily_vpp_events(sim_days, start_h=start_h, duration_h=duration_h)


def _fmt_clock_h(hour: float) -> str:
    """Format an hour-of-day float as HH:MM for user-facing logs/prompts."""
    h = float(hour) % 24.0
    hh = int(h)
    mm = int(round((h - hh) * 60))
    if mm >= 60:
        hh = (hh + 1) % 24
        mm = 0
    return f"{hh:02d}:{mm:02d}"


def _event_start_hod(event: dict | None) -> float:
    return float((event or {}).get("trigger_h", 18.0)) % 24.0


def _event_end_hod(event: dict | None) -> float:
    return float((event or {}).get("end_h", 19.0)) % 24.0


def _event_window_text(event: dict | None) -> str:
    return f"{_fmt_clock_h(_event_start_hod(event))}-{_fmt_clock_h(_event_end_hod(event))}"


def _event_preheat_safe_end_hod(event: dict | None) -> float:
    """Prefer ending thermal preheat one hour before the current VPP window."""
    return (_event_start_hod(event) - 1.0) % 24.0


def _calendar_return_home_sensitive(persona_config: dict | None, event: dict | None, *, margin_h: float = 0.5) -> bool:
    """True when a VPP window is close enough to home arrival to protect comfort."""
    if not persona_config or not event:
        return False
    calendar = persona_config.get("calendar") or {}
    days = calendar.get("days") or []
    if not days:
        return False
    try:
        event_day = int(event.get("day", 1) or 1)
    except (TypeError, ValueError):
        event_day = 1
    day = next((item for item in days if int(item.get("day", -1)) == event_day), None)
    if day is None:
        weekly_day = ((event_day - 1) % len(days)) + 1
        day = next((item for item in days if int(item.get("day", -1)) == weekly_day), None)
    if day is None:
        return False
    arrival = (day.get("constraints") or {}).get("home_arrival_h")
    if arrival is None:
        return False
    try:
        arrival_h = float(arrival) % 24.0
    except (TypeError, ValueError):
        return False
    start_h = _event_start_hod(event)
    end_h = _event_end_hod(event)
    return (start_h - margin_h) <= arrival_h <= (end_h + margin_h)


def _calendar_home_occupied_during_event(persona_config: dict | None, event: dict | None) -> bool:
    """True when the calendar has a home activity overlapping the VPP window."""
    if not persona_config or not event:
        return False
    calendar = persona_config.get("calendar") or {}
    days = calendar.get("days") or []
    if not days:
        return False
    try:
        event_day = int(event.get("day", 1) or 1)
    except (TypeError, ValueError):
        event_day = 1
    day = next((item for item in days if int(item.get("day", -1)) == event_day), None)
    if day is None:
        weekly_day = ((event_day - 1) % len(days)) + 1
        day = next((item for item in days if int(item.get("day", -1)) == weekly_day), None)
    if day is None:
        return False
    start_h = _event_start_hod(event)
    end_h = _event_end_hod(event)
    for item in day.get("events") or []:
        if str(item.get("location", "")).lower() != "home":
            continue
        try:
            item_start = float(item.get("start_h"))
            item_end = float(item.get("end_h"))
        except (TypeError, ValueError):
            continue
        if item_start < end_h and item_end > start_h:
            return True
    return False


def _calendar_occupied_or_return_home_sensitive(persona_config: dict | None, event: dict | None) -> bool:
    """True when VPP thermal actions are likely visible to the user."""
    return _calendar_home_occupied_during_event(persona_config, event) or _calendar_return_home_sensitive(persona_config, event)


def _low_vpp_target_kw(demand_kw: float | None, *, threshold_kw: float = 0.75) -> bool:
    """True when the diagnostic capacity reference is small enough that comfort should dominate."""
    try:
        target_kw = float(demand_kw)
    except (TypeError, ValueError):
        return False
    return target_kw <= threshold_kw


def _find_active_or_upcoming_vpp_event(
    sim_h: float,
    *,
    vpp_id: str = "",
    vpp_events: list[dict] | None = None,
) -> dict | None:
    """Return the active event or the next event later on the same simulated day."""
    events = VPP_EVENTS if vpp_events is None else vpp_events
    if vpp_id:
        for ev in events:
            if ev.get("id") == vpp_id:
                return ev
    for ev in events:
        if float(ev["trigger_h"]) <= sim_h < float(ev["end_h"]):
            return ev
    day_idx = int(sim_h // 24)
    upcoming = [
        ev for ev in events
        if sim_h < float(ev["trigger_h"]) and int(float(ev["trigger_h"]) // 24) == day_idx
    ]
    return min(upcoming, key=lambda ev: float(ev["trigger_h"])) if upcoming else None


def _agent_pre_vpp_precool_event(
    sim_h: float,
    *,
    vpp_events: list[dict] | None = None,
    lead_h: float = AGENT_PRE_VPP_PRECOOL_LEAD_H,
) -> dict | None:
    """Return the same-day event when EnergyBridge should pre-cool for VPP."""
    ev = _find_active_or_upcoming_vpp_event(sim_h, vpp_events=vpp_events)
    if ev is None:
        return None
    trigger_h = float(ev.get("trigger_h", 0.0))
    end_h = float(ev.get("end_h", trigger_h))
    if trigger_h <= sim_h < end_h:
        return None
    return ev if trigger_h - float(lead_h) <= sim_h < trigger_h else None


def _agent_post_vpp_restore_event(
    sim_h: float,
    *,
    vpp_events: list[dict] | None = None,
    restore_h: float = AGENT_POST_VPP_RESTORE_H,
) -> dict | None:
    """Return the recent event when EnergyBridge should restore comfort."""
    events = VPP_EVENTS if vpp_events is None else vpp_events
    recent = [
        ev for ev in events
        if float(ev.get("end_h", 0.0)) <= sim_h < float(ev.get("end_h", 0.0)) + float(restore_h)
    ]
    return max(recent, key=lambda ev: float(ev.get("end_h", 0.0))) if recent else None


def _agent_next_vpp_checkpoint_hour(
    sim_h: float,
    *,
    vpp_events: list[dict] | None = None,
    lead_h: float = AGENT_PRE_VPP_PRECOOL_LEAD_H,
) -> float | None:
    """Schedule EnergyBridge's pre-cool and event-start checks for the next VPP."""
    ev = _find_active_or_upcoming_vpp_event(sim_h, vpp_events=vpp_events)
    if ev is None:
        return None
    trigger_h = float(ev.get("trigger_h", 0.0))
    end_h = float(ev.get("end_h", trigger_h))
    if trigger_h <= sim_h < end_h:
        return end_h
    pre_h = max(float(int(trigger_h // 24) * 24), trigger_h - float(lead_h))
    if sim_h < pre_h:
        return pre_h
    if sim_h < trigger_h:
        return trigger_h
    return None

@dataclass
class BenchmarkResult:
    scenario: str = ""; building: str = "family"; weather: str = ""; method: str = ""
    user_label: str = ""
    sim_days: int = 3
    start_date: str = ""
    vpp_schedule_source: str = ""
    exit_code: int = -1; energy_kwh_total: float = 0.0; energy_kwh_per_day: float = 0.0
    daily_energy_kwh: List[dict] = field(default_factory=list)
    pmv_ok_fraction: float = 0.0; comfort_ok_fraction: float = 0.0
    mean_pmv: float = 0.0; mean_temp_c: float = 0.0
    unmet_cooling_h: float = 0.0
    # VPP energy: actual kWh consumed during demand windows.
    vpp_window_energy_kwh: float = 0.0
    vpp_window_energy_avg_per_hour_kwh: float = 0.0
    vpp_energy_reduction_kwh: float = 0.0  # true reduction requires a same-run no-DR counterfactual
    vpp_actual_shed_kwh: float = 0.0       # legacy alias; do not use as actual shed for family runs
    vpp_energy_reduction_total_kwh: float = 0.0
    vpp_energy_reduction_avg_per_event_kwh: float = 0.0
    vpp_energy_reduction_avg_per_hour_kwh: float = 0.0
    vpp_energy_reduction_basis: str = ""
    agent_setpoint_c: Optional[float] = None
    # User satisfaction (roleplay LLM evaluation, per VPP event)
    user_pref_score: Optional[float] = None       # average across events
    user_pref_scores: List[float] = field(default_factory=list)  # per-event [e1,e2,e3]
    user_comfort_scores: List[float] = field(default_factory=list)
    user_energy_scores: List[float] = field(default_factory=list)
    user_vpp_scores: List[float] = field(default_factory=list)
    vpp_compliance_rate: float = 0.0  # fraction of VPP events where setpoint >= 26.0C
    user_pref_comment: str = ""
    # LLM performance metrics
    llm_call_count: int = 0; llm_call_failures: int = 0
    llm_latency_total_s: float = 0.0
    llm_tokens_prompt: int = 0; llm_tokens_completion: int = 0
    daily_llm_usage: List[dict] = field(default_factory=list)
    # Appliance rule-based indicators
    appliance_vpp_avoidance_rate: float = 0.0   # fraction of completed shiftable tasks that ran outside VPP
    appliance_task_completion_rate: float = 1.0  # fraction of present non-AC appliance services with emitted policy actions
    physical_appliance_task_completion_rate: float = 1.0  # simulator service outcomes, kept for diagnostics
    policy_output_covered_appliance_services: List[str] = field(default_factory=list)
    policy_output_uncovered_appliance_services: List[str] = field(default_factory=list)
    policy_output_absent_appliance_services: List[str] = field(default_factory=list)
    appliance_shift_success_rate: float = 0.0  # fraction of present shiftable tasks completed and shifted outside VPP
    task_completion_per_day: List[float] = field(default_factory=list)  # per-day shiftable completion [day1,day2,day3]
    task_shift_success_per_day: List[float] = field(default_factory=list)  # per-day shift success [day1,day2,day3]
    vpp_demand_targets: List[float] = field(default_factory=list)       # per-event equivalent consumption caps
    vpp_demand_targets_kw: List[float] = field(default_factory=list)    # per-event shed-capacity targets from quantification
    vpp_demand_achievement_ratio: Optional[float] = None
    vpp_appliance_avoidance_success_rate: Optional[float] = None
    ev_target_reached_rate: float = 0.0         # fraction of days EV reached target SOC
    ewh_preheat_used_rate: float = 0.0          # fraction of days EWH preheat was active
    appliance_results: dict = field(default_factory=dict)  # per-device per-day details
    no_dr_routine_actions: List[dict] = field(default_factory=list)
    day_ahead_price_metrics: dict = field(default_factory=dict)
    daily_trace_rows: List[dict] = field(default_factory=list)
    control_decisions: List[Tuple[float, float, float]] = field(default_factory=list)
    vpp_event_log: List[dict] = field(default_factory=list)  # scored VPP events with reason
    vpp_plan_acceptance_rate: Optional[float] = None
    vpp_plan_acceptance_probability_avg: Optional[float] = None
    vpp_plan_rejected_count: int = 0
    vpp_plan_gate_events: List[dict] = field(default_factory=list)
    agent_preference_memory_path: str = ""
    agent_preference_memory_md_path: str = ""
    output_dir: str = ""; error: str = ""
    def as_dict(self):
        _skip = {"control_decisions", "appliance_results"}
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_") and k not in _skip}


def _dashboard_trace_rows(power_trace_rows: list[dict]) -> list[dict]:
    """Compact simulator rows for the web dashboard and live snapshots."""
    rows: list[dict] = []
    for row in power_trace_rows:
        try:
            sim_h = float(row.get("sim_h", 0.0))
            rows.append({
                "day": int(sim_h // 24) + 1,
                "sim_h": round(sim_h, 4),
                "hour": round(float(row.get("hod", 0.0)), 4),
                "dt_h": round(float(row.get("dt_h", 0.0)), 6),
                "power_kw": round(float(row.get("power_kw", 0.0)), 6),
                "indoor_temperature_c": round(float(row.get("indoor_temperature_c", 0.0)), 4),
                "outdoor_temperature_c": round(float(row.get("outdoor_temperature_c", 0.0)), 4),
                "ac_setpoint_c": round(float(row.get("ac_setpoint_c", 0.0)), 4),
                "occupied": bool(row.get("occupied", False)),
                "vpp_active": bool(row.get("vpp_active", False)),
                "vpp_event_id": str(row.get("vpp_event_id", "") or ""),
            })
        except (TypeError, ValueError):
            continue
    return rows


def _compute_pmv(tdb, rh=PMV_RH):
    try:
        from pythermalcomfort.models import pmv_ppd_iso
        r = pmv_ppd_iso(tdb=tdb, tr=tdb, vr=PMV_V, rh=rh, met=PMV_MET, clo=PMV_CLO, limit_inputs=False)
        return float(r.pmv)
    except:
        t_n = 33.5 - 3.5*PMV_MET - 3.0*PMV_CLO
        return round(0.5*(tdb - t_n) + (rh-50)*0.007, 3)

def _occupied(h): return OCCUPIED_START <= (h % 24) < OCCUPIED_END


def _observable_occupancy(ex, s, loop, persona_config: dict | None, sim_h: float, hod: float) -> tuple[bool, float, str]:
    """Read home occupancy from EnergyPlus, falling back to the role-play calendar."""
    if getattr(loop, "h_occ", -1) != -1:
        try:
            count = ex.get_variable_value(s, loop.h_occ)
            if count is not None and not (float(count) != float(count)):
                count_f = max(0.0, float(count))
                return count_f > 0.05, count_f, "ep_zone_people"
        except Exception:
            pass
    if persona_config and (persona_config.get("calendar") or {}).get("days"):
        try:
            from energybridge.roleplay.calendar import occupancy_fraction_at_sim_hour
            fraction = max(0.0, min(1.0, float(occupancy_fraction_at_sim_hour(persona_config, sim_h))))
            return fraction > 0.05, fraction * 3.0, "persona_calendar_fallback"
        except Exception:
            return True, 3.0, "persona_calendar_unreadable_default_home"
    if persona_config:
        return True, 3.0, "missing_persona_calendar_default_home"
    return _occupied(hod), 3.0 if _occupied(hod) else 0.0, "fixed_hours_fallback"


def _append_day_agent_decision(loop, sim_days: int, sim_h: float, decision: dict) -> None:
    day_i = int(sim_h // 24)
    if 0 <= day_i < sim_days and day_i < len(loop.day_agent_decisions):
        loop.day_agent_decisions[day_i].append(decision)


def _init_daily_llm_usage(loop, sim_days: int) -> None:
    loop.daily_llm_usage = [
        {
            "day": idx + 1,
            "llm_calls": 0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "api_latency_s": 0.0,
        }
        for idx in range(max(1, int(sim_days or 1)))
    ]


def _record_daily_llm_usage(loop, sim_days: int, sim_h: float, metrics: dict | None) -> None:
    if not hasattr(loop, "daily_llm_usage") or not loop.daily_llm_usage:
        _init_daily_llm_usage(loop, sim_days)
    day_i = int(float(sim_h or 0.0) // 24)
    if not (0 <= day_i < len(loop.daily_llm_usage)):
        return
    metrics = metrics or {}
    token_usage = metrics.get("token_usage") if isinstance(metrics.get("token_usage"), dict) else metrics
    prompt = int(token_usage.get("prompt_tokens", 0) or 0)
    completion = int(token_usage.get("completion_tokens", 0) or 0)
    latency = float(metrics.get("latency_seconds", 0.0) or 0.0)
    row = loop.daily_llm_usage[day_i]
    row["llm_calls"] = int(row.get("llm_calls", 0) or 0) + 1
    row["prompt_tokens"] = int(row.get("prompt_tokens", 0) or 0) + prompt
    row["completion_tokens"] = int(row.get("completion_tokens", 0) or 0) + completion
    row["total_tokens"] = int(row.get("total_tokens", 0) or 0) + prompt + completion
    row["api_latency_s"] = round(float(row.get("api_latency_s", 0.0) or 0.0) + latency, 3)


def _is_weather_run_period(ex, state) -> bool:
    """Return True only during the actual RunPeriod, not sizing/design days."""
    try:
        return int(ex.kind_of_sim(state)) == 3
    except Exception:
        return True


def _set_hvac_availability(ex, s, loop, available: bool) -> bool:
    """Set the EP HVAC availability schedule if this IDF exposes it."""
    value = 1.0 if available else 0.0
    loop.current_hvac_available = bool(available)
    if getattr(loop, "h_hvac_avail", -1) == -1:
        return False
    ex.set_actuator_value(s, loop.h_hvac_avail, value)
    return True

class _FamilyLoop:
    def __init__(self):
        self.sp = SP_DEFAULT; self.ready = False; self.start_day = None
        self.h_cool = self.h_heat = self.h_temp = self.h_fac = self.h_occ = self.h_hvac_avail = -1
        # Appliance actuator handles (written back to EnergyPlus each timestep)
        self.h_ev = self.h_ewh_sp = -1
        self.h_washer = self.h_dishwasher = self.h_dryer = self.h_refrigerator = -1
        self.e_wh = self.occ_h = self.pmv_ok_h = self.comfort_ok_h = 0.0
        self.daily_e_wh: List[float] = []
        self.pmv_s = self.temp_s = self.unmet_h = 0.0
        self.vpp_e_wh = 0.0                        # energy consumed during VPP windows [Wh]
        self.llm_calls = 0; self.llm_failures = 0  # LLM call counters
        self.llm_latency_s = 0.0                   # cumulative LLM wall-clock latency
        self.llm_tokens_prompt = 0; self.llm_tokens_comp = 0  # OpenAI usage tokens
        self.decisions = []; self.step = 0; self.h_out = -1
        self.next_check: Optional[float] = DEFAULT_PLANNING_HOUR   # first daily planning trigger (sim-hour)
        self.planned_occupied_sp: float = SP_DEFAULT
        self.sim_days: int = 3
        self.vpp_events: List[Dict[str, Any]] = list(VPP_EVENTS)
        self.vpp_schedule_source: str = ""
        self.prev_sim_h: float = -1.0              # for crossing detection
        self.prev_occupied: Optional[bool] = None
        self.current_occupied: bool = True
        self.current_occupancy_count: float = 3.0
        self.current_occupancy_source: str = "unknown"
        self.current_hvac_available: bool = True
        # VPP per-event tracking
        self.vpp_window_data: Dict[str, Any] = {}  # id -> {temps, pmvs, sp, reason}
        self.vpp_event_log: List[Dict] = []         # scored events in time order
        self.vpp_scored: set = set()                # ids already scored
        self.vpp_mem_ctx: str = ""                  # compressed memory for next LLM call
        self.vpp_user_input: str = ""               # roleplay user preference before agent acts
        self.vpp_user_input_by_id: Dict[str, str] = {}
        self.vpp_strategy_trace_by_id: Dict[str, dict] = {}
        self.vpp_strategy_explanation_by_id: Dict[str, dict] = {}
        self.vpp_last_reason: str = ""              # agent reason from last LLM call
        self.vpp_trigger_reason_by_id: Dict[str, str] = {}  # reason from the event-start control action
        # Per-event VPP energy tracking and demand-agent outputs
        self.vpp_event_energy_wh: Dict[str, float] = {}   # {event_id: Wh} accumulated per event
        self.vpp_demand_by_id: Dict[str, dict] = {}        # {event_id: {target_kwh, reason}}
        self.vpp_capacity_by_id: Dict[str, dict] = {}      # household capacity assessment sent to VPP
        self.vpp_capacity_window_by_id: Dict[str, List[dict]] = {}  # per-timestep physical capacity
        self.total_quantification_by_id: Dict[str, dict] = {}  # reference A3 90% event capacity
        self.power_trace_rows: List[Dict[str, Any]] = []   # timestep load/weather rows for event baselines
        self.current_vpp_demand_kwh: float = 0.0           # equivalent consumption cap for active VPP event
        self.current_vpp_demand_kw: float = 0.0            # shed-capacity target for active VPP event
        self.current_vpp_capacity: Dict[str, Any] = {}
        self.days_evaluated: set = set()                    # prevent double-printing daily eval
        self.vpp_trigger_actions: Dict[str, dict] = {}      # {event_id: appliance_actions at VPP trigger}
        self.day_agent_decisions: List[List[dict]] = [[], [], []]  # per day: list of {h, sp, actions}
        self.daily_plans_done: set[int] = set()
        self.no_dr_routine_actions: List[dict] = []
        self.no_vpp_daily_plan_by_day: Dict[int, dict] = {}
        self.vpp_plan_gate_by_id: Dict[str, dict] = {}
        self.agent_preference_memory: Dict[str, Any] = {}
        self.agent_memory_path: Optional[Path] = None
        self.agent_memory_md_path: Optional[Path] = None
        self.persist_agent_preference_memory: bool = False
        # Appliance suite — initialised by run_family_agent when persona is known
        self.appliance_suite = None                 # ApplianceSuite | None

    def init(self, ex, s):
        if self.ready: return True
        if not ex.api_data_fully_ready(s): return False
        self.h_cool = ex.get_actuator_handle(s, "Schedule:Compact",   "Schedule Value", "cooling_sch")
        self.h_heat = ex.get_actuator_handle(s, "Schedule:Compact",   "Schedule Value", "heating_sch")
        self.h_ev      = ex.get_actuator_handle(s, "Schedule:Constant", "Schedule Value", "EV_Charging_Fraction_Control")
        self.h_ewh_sp  = ex.get_actuator_handle(s, "Schedule:Constant", "Schedule Value", "EWH_Setpoint_Control")
        self.h_washer      = ex.get_actuator_handle(s, "Schedule:Constant", "Schedule Value", "ClothesWasher_Power_Frac")
        self.h_dishwasher  = ex.get_actuator_handle(s, "Schedule:Constant", "Schedule Value", "Dishwasher_Power_Frac")
        self.h_dryer       = ex.get_actuator_handle(s, "Schedule:Constant", "Schedule Value", "ClothesDryer_Power_Frac")
        self.h_refrigerator= ex.get_actuator_handle(s, "Schedule:Constant", "Schedule Value", "Refrigerator_Power_Frac")
        self.h_temp = ex.get_variable_handle(s, "Zone Mean Air Temperature", "living_unit1")
        self.h_fac  = ex.get_variable_handle(s, "Facility Total Electricity Demand Rate", "Whole Building")
        self.h_occ  = ex.get_variable_handle(s, "Zone People Occupant Count", "living_unit1")
        self.h_hvac_avail = ex.get_actuator_handle(s, "Schedule:Constant", "Schedule Value", "HVAC_Availability_Control")
        self.ready = True; return True

def run_family_pmv(idf_path=DEFAULT_FAMILY_IDF, epw_path=DEFAULT_FAMILY_EPW,
                   output_dir=None, weather_label=""):
    if output_dir is None:
        output_dir = BENCHMARK_DIR / "results" / f"family_pmv_{weather_label}"
    output_dir = Path(output_dir)
    if output_dir.exists(): shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    from pyenergyplus.api import EnergyPlusAPI
    loop = _FamilyLoop(); api = EnergyPlusAPI(); state = api.state_manager.new_state()
    ex = api.exchange
    ex.request_variable(state, "Zone Mean Air Temperature", "living_unit1")
    ex.request_variable(state, "Facility Total Electricity Demand Rate", "Whole Building")
    ex.request_variable(state, "Zone People Occupant Count", "living_unit1")

    # PMV per-VPP-window tracking (to show PMV doesn't adapt)
    vpp_window_temps: Dict[str, list] = {ev["id"]: [] for ev in VPP_EVENTS}
    vpp_window_pmvs: Dict[str, list] = {ev["id"]: [] for ev in VPP_EVENTS}

    def cb(s):
        if not loop.init(ex, s): return
        if not _is_weather_run_period(ex, s):
            return
        day = ex.day_of_year(s)
        if loop.start_day is None: loop.start_day = day
        hod = ex.current_time(s); dt = ex.zone_time_step(s)
        sim_h = (day - loop.start_day)*24 + hod
        wu = ex.warmup_flag(s)
        temp = ex.get_variable_value(s, loop.h_temp) if loop.h_temp!=-1 else SP_DEFAULT
        fac  = ex.get_variable_value(s, loop.h_fac)  if loop.h_fac!=-1  else 0.0
        occ, occ_count, occ_source = _observable_occupancy(ex, s, loop, None, sim_h, hod)
        loop.current_occupied = occ
        loop.current_occupancy_count = occ_count
        loop.current_occupancy_source = occ_source
        pmv = _compute_pmv(temp)
        hvac_avail_set = _set_hvac_availability(ex, s, loop, occ)
        if not occ:
            if not hvac_avail_set:
                loop.sp = AC_OFF_FALLBACK_COOLING_SETPOINT
        elif pmv > PMV_DEADBAND:
            loop.sp = max(SP_MIN, loop.sp - SP_STEP)
        elif pmv < -PMV_DEADBAND:
            loop.sp = min(SP_MAX, loop.sp + SP_STEP)
        if loop.h_cool!=-1: ex.set_actuator_value(s, loop.h_cool, loop.sp)
        if loop.h_heat!=-1: ex.set_actuator_value(s, loop.h_heat, HTG_SP)
        if wu: return
        loop.e_wh += fac * dt
        # Collect per-VPP-window data
        for ev in VPP_EVENTS:
            if ev["trigger_h"] <= sim_h < ev["end_h"]:
                vpp_window_temps[ev["id"]].append(temp)
                vpp_window_pmvs[ev["id"]].append(abs(pmv) <= PMV_DEADBAND)
        if occ:
            loop.occ_h += dt; loop.pmv_s += pmv*dt; loop.temp_s += temp*dt
            if abs(pmv) <= PMV_DEADBAND: loop.pmv_ok_h += dt
            if temp > loop.sp + UNMET_TOL: loop.unmet_h += dt
            loop.decisions.append((round(sim_h,2), round(loop.sp,1), round(pmv,3)))

    api.runtime.callback_end_system_timestep_after_hvac_reporting(state, cb)
    ec = api.runtime.run_energyplus(state, ["-w",str(epw_path),"-d",str(output_dir),str(idf_path)])
    api.state_manager.delete_state(state)

    kwh = loop.e_wh/1000; occ = max(loop.occ_h, 1e-6)

    # Score PMV for each VPP window (shows no adaptation)
    pref_scores = []
    try:
        from user_pref_scorer import score_user_preference
        for idx, ev in enumerate(VPP_EVENTS):
            wtemps = vpp_window_temps.get(ev["id"], [])
            wpmvs  = vpp_window_pmvs.get(ev["id"], [])
            wt = sum(wtemps)/max(1,len(wtemps)) if wtemps else (loop.temp_s/occ)
            wp = sum(wpmvs)/max(1,len(wpmvs)) if wpmvs else (loop.pmv_ok_h/occ)
            r = score_user_preference(building="family", method="pmv",
                mean_temp_c=wt, pmv_ok_fraction=wp,
                energy_kwh_per_day=kwh/3, event_index=idx+1)
            pref_scores.append(r.get("score") or 0.0)
    except Exception as e:
        print(f"  [PMV score] {e}")

    return BenchmarkResult(scenario=f"family/{weather_label}", building="family",
        weather=weather_label, method="pmv", exit_code=ec,
        vpp_compliance_rate=0.0,
        energy_kwh_total=kwh, energy_kwh_per_day=kwh/3,
        pmv_ok_fraction=loop.pmv_ok_h/occ, mean_pmv=loop.pmv_s/occ,
        mean_temp_c=loop.temp_s/occ, unmet_cooling_h=loop.unmet_h,
        user_pref_scores=pref_scores,
        user_pref_score=sum(pref_scores)/max(1,len(pref_scores)) if pref_scores else None,
        control_decisions=loop.decisions[-50:], output_dir=str(output_dir))



def _print_prev_day_completion(suite, day_idx: int, day_num: int) -> None:
    """Print appliance task completion for day_num at next morning check."""
    results = suite.all_results()
    print(f"  [Day {day_num} task completion check]")
    any_shown = False
    for nm in ("washer", "dishwasher", "dryer"):
        days_list = results.get(nm, [])
        if not days_list or day_idx >= len(days_list):
            continue
        dr = days_list[day_idx]
        if not dr.get("present", False):
            continue   # not in household — skip entirely
        any_shown = True
        sched_abs_h = dr.get("scheduled_abs_h")
        sched_hod = int(sched_abs_h % 24) if sched_abs_h is not None else None
        if dr.get("skipped"):
            status = "x skipped task [agent issued skip; task NOT done]"
        elif sched_hod is None:
            status = "x not completed [no emitted policy command]"
        elif not dr.get("completed"):
            status = "x not completed [scheduled but never ran]"
        elif dr.get("ran_during_vpp"):
            status = f"! completed@{sched_hod:02d}:00 [ran inside VPP window; not shifted]"
        else:
            status = f"ok completed@{sched_hod:02d}:00 [shifted away from VPP]"
        print(f"    {nm:<14}: {status}")
    wh_days = results.get("water_heater", [])
    if wh_days and day_idx < len(wh_days) and wh_days[day_idx].get("present", False):
        wh = wh_days[day_idx]
        any_shown = True
        ph   = "preheat=yes" if wh.get("preheat_used") else "preheat=no"
        rb   = "ready_before_bath=yes" if wh.get("ready_at_bath", True) else "ready_before_bath=no"
        vfl  = " ! heated_in_VPP" if wh.get("ran_during_vpp") else ""
        print(f"    {'water_heater':<14}: {ph}  {rb}{vfl}  ({wh.get('energy_kwh', 0):.1f}kWh)")
    ev_days = results.get("ev", [])
    if ev_days and day_idx < len(ev_days) and ev_days[day_idx].get("present", False):
        ev = ev_days[day_idx]
        any_shown = True
        tgt  = "SOC_target_met" if ev.get("target_reached") else "SOC_target_missed"
        soc  = ev.get("soc_end", 0)
        vfl  = " ! charged_in_VPP" if ev.get("ran_during_vpp") else ""
        print(f"    {'ev':<14}: {tgt}  SOC={soc:.0%}{vfl}  ({ev.get('energy_kwh', 0):.1f}kWh)")
    if not any_shown:
        print(f"    (no controllable appliances in this household)")


def _call_vpp_demand_agent(event_id: str, total_quantification: dict | None = None) -> dict:
    """Build the VPP demand target directly from reference capacity quantification.

    Returns: {"target_kwh": float, "reason": str, "source": str}
    """
    tq = total_quantification or {}
    if tq.get("status") == "computed":
        duration_h = max(1e-6, float(tq.get("duration_hours", 1.0) or 1.0))
        baseline_kwh = float(tq.get("avg_p_base_q50_kw", 0.0) or 0.0) * duration_h
        target_kwh = float(tq.get("vpp_target_kwh", 0.0) or 0.0)
        if target_kwh <= 0.0:
            target_kwh = float(tq.get("avg_p_dr_hat_conservative_kw", 0.0) or 0.0) * duration_h
        accepted_capacity_kw = float(
            tq.get("vpp_target_capacity_120_kw", tq.get("avg_reported_capacity_90_kw", 0.0)) or 0.0
        )
        target_shed_kwh = float(tq.get("vpp_target_capacity_energy_kwh", 0.0) or 0.0)
        return {
            "target_kwh": round(max(0.1, target_kwh), 3),
            "reason": "A3 capacity quantification target (1.2x)",
            "source": "total_quantification_120",
            "baseline_kwh": round(baseline_kwh, 3),
            "accepted_capacity_kw": round(accepted_capacity_kw, 3),
            "target_shed_kw": round(accepted_capacity_kw, 3),
            "target_shed_kwh": round(target_shed_kwh, 3),
            "reported_shed_90_energy_kwh": round(
                float(tq.get("reported_shed_90_energy_kwh", 0.0) or 0.0), 3
            ),
            "target_capacity_energy_kwh": round(
                float(tq.get("vpp_target_capacity_energy_kwh", 0.0) or 0.0), 3
            ),
        }

    return {
        "target_kwh": 2.0,
        "reason": "quantification unavailable fallback",
        "source": "fallback",
        "baseline_kwh": 2.0,
        "accepted_capacity_kw": 0.0,
        "target_shed_kw": 0.0,
        "target_shed_kwh": 0.0,
    }


def _capacity_window_summary_from_rows(cap_rows: list[dict] | None) -> dict:
    rows = list(cap_rows or [])
    if not rows:
        return {}
    n_rows = len(rows)
    return {
        "method": "state_physical_with_optional_baseline",
        "steps": n_rows,
        "avg_committable_kw": round(
            sum(float(r["committable_kw"]) for r in rows) / n_rows, 6),
        "firm_min_committable_kw": round(
            min(float(r["committable_kw"]) for r in rows), 6),
        "committable_energy_kwh": round(
            sum(float(r["committable_kw"]) * float(r["dt_h"]) for r in rows), 6),
        "avg_recommended_bid_kw": round(
            sum(float(r["recommended_bid_kw"]) for r in rows) / n_rows, 6),
        "firm_min_recommended_bid_kw": round(
            min(float(r["recommended_bid_kw"]) for r in rows), 6),
        "recommended_bid_energy_kwh": round(
            sum(float(r["recommended_bid_kw"]) * float(r["dt_h"]) for r in rows), 6),
        "avg_success_probability": round(
            sum(float(r["success_probability"]) for r in rows) / n_rows, 6),
    }


def _event_duration_h(event_result: dict) -> float:
    try:
        return max(
            1e-6,
            float(event_result.get("end_h", 0.0)) - float(event_result.get("trigger_h", 0.0)),
        )
    except (TypeError, ValueError):
        return 1.0


def _event_physical_shed_cap_kwh(event_result: dict) -> tuple[float | None, str]:
    summary = event_result.get("capacity_window_summary") or {}
    value = summary.get("recommended_bid_energy_kwh")
    try:
        if value is not None:
            return max(0.0, float(value)), "capacity_window_recommended_bid_energy"
    except (TypeError, ValueError):
        pass
    assessment = ((event_result.get("capacity_assessment") or {}).get("assessment") or {})
    value = assessment.get("recommended_bid_kw")
    try:
        if value is not None:
            return max(0.0, float(value) * _event_duration_h(event_result)), "event_start_recommended_bid_kw"
    except (TypeError, ValueError):
        pass
    return None, ""


def _attach_event_baseline_shed(event_result: dict, event: dict, power_trace_rows: list[dict]) -> None:
    """Attach baseline-minus-actual shed using current-run or historical rows."""
    try:
        from energybridge.quantification import estimate_event_baseline_and_shed

        estimate = estimate_event_baseline_and_shed(
            event,
            power_trace_rows,
            actual_kwh=event_result.get("actual_kwh"),
        )
    except Exception as exc:
        event_result["event_baseline_estimate"] = {
            "status": "failed",
            "reason": str(exc)[:160],
        }
        return

    event_result["event_baseline_estimate"] = estimate
    if estimate.get("actual_shed_kwh") is None:
        return
    event_result["estimated_baseline_kwh"] = estimate.get("baseline_kwh")
    event_result["estimated_baseline_source"] = estimate.get("baseline_source")
    event_result["estimated_baseline_confidence"] = estimate.get("baseline_confidence")
    event_result["actual_shed_kwh"] = estimate.get("actual_shed_kwh")
    event_result["actual_shed_avg_kw"] = estimate.get("actual_shed_avg_kw")
    event_result["actual_shed_basis"] = estimate.get("actual_shed_basis")


def _update_event_reference_shed_diagnostics(event_result: dict) -> None:
    """Record reference Pbase-minus-actual diagnostics without calling it shed.

    The reference quantification P_base is produced from a separate A3 household
    and strategy. Subtracting the current EnergyPlus actual from that P_base is
    useful for debugging scale mismatches, but it is not a method-specific
    actual reduction. A true actual shed metric needs a same-persona no-DR
    counterfactual run.
    """
    baseline_kwh = event_result.get("demand_baseline_kwh")
    actual_kwh = event_result.get("actual_kwh")
    if event_result.get("actual_shed_kwh") is None:
        event_result["actual_shed_kwh"] = None
        event_result["actual_shed_basis"] = "unavailable_without_valid_event_baseline"
    if baseline_kwh is None or actual_kwh is None:
        return
    try:
        raw_shed = max(0.0, float(baseline_kwh) - float(actual_kwh))
    except (TypeError, ValueError):
        return
    cap_kwh, cap_source = _event_physical_shed_cap_kwh(event_result)
    event_result["reference_baseline_shed_kwh"] = round(raw_shed, 4)
    event_result["reference_pbase_minus_actual_kwh"] = round(raw_shed, 4)
    event_result["reference_shed_diagnostic_basis"] = (
        "reference_a3_pbase_minus_current_ep_actual_not_actual_shed"
    )
    if cap_kwh is not None:
        event_result["physical_shed_cap_kwh"] = round(cap_kwh, 4)
        event_result["capacity_limited_reference_shed_kwh"] = round(min(raw_shed, cap_kwh), 4)
        event_result["capacity_limited_reference_shed_basis"] = (
            f"min(reference_a3_pbase_minus_current_ep_actual, {cap_source})"
        )


def _non_ac_appliances_during_vpp(appliance_summary: dict | None) -> list[str]:
    if not isinstance(appliance_summary, dict):
        return []
    services: list[str] = []
    for name in ("washer", "dishwasher", "dryer", "water_heater", "ev"):
        info = appliance_summary.get(name)
        if isinstance(info, dict) and bool(info.get("present")) and bool(info.get("ran_during_vpp")):
            services.append(name)
    return services


def _annotate_event_demand_achievement(event_result: dict) -> None:
    """Annotate VPP success by non-AC appliance avoidance.

    The benchmark-level VPP success criterion is no longer a shed/cap target.
    A VPP event succeeds when no present non-AC appliance is scheduled or run
    inside the VPP window.  Actual shed and reference-baseline fields remain as
    diagnostics only.
    """
    overlapping = _non_ac_appliances_during_vpp(event_result.get("appliance_summary"))
    event_result["target_mode"] = "non_ac_appliance_avoidance"
    event_result["target_achieved"] = not overlapping
    event_result["demand_achievement_ratio"] = 0.0 if overlapping else 1.0
    event_result["demand_achievement_basis"] = "no_non_ac_appliance_in_vpp_window"
    event_result["vpp_non_ac_appliances_during_event"] = overlapping
    event_result["vpp_appliance_avoidance_success"] = not overlapping


def _shiftable_has_existing_service_plan(suite, name: str, day_idx: int) -> bool:
    """True when a later skip would cancel an already emitted real policy."""
    try:
        app = getattr(suite, "_shiftable", {}).get(name)
        rec = (getattr(app, "_days", {}) or {}).get(day_idx) if app is not None else None
        if rec is None:
            return False
        return (
            getattr(rec, "scheduled_abs_h", None) is not None
            or getattr(rec, "run_start_abs_h", None) is not None
            or bool(getattr(rec, "completed", False))
        )
    except Exception:
        return False


def _ev_window_remaining_hours(day_idx: int, start_h: Any, end_h: Any, sim_h: float) -> float:
    try:
        interval = _abs_interval_from_hod(day_idx, start_h, end_h)
    except Exception:
        interval = None
    if interval is None:
        return 0.0
    start_abs, end_abs = interval
    if end_abs <= sim_h:
        return 0.0
    return max(0.0, end_abs - max(start_abs, sim_h))


def _ev_replan_would_reduce_existing_charge(
    suite,
    day_idx: int,
    start_h: Any,
    end_h: Any,
    sim_h: float,
) -> bool:
    ev = getattr(suite, "_ev", None)
    if ev is None or not getattr(ev, "present", False):
        return False
    try:
        if float(getattr(ev, "_soc", 0.0)) >= float(getattr(ev, "target_soc", 0.8)) - 1e-6:
            return False
    except Exception:
        pass
    current_start = (getattr(ev, "_day_charge_start", {}) or {}).get(day_idx)
    current_end = (getattr(ev, "_day_charge_end", {}) or {}).get(day_idx)
    if current_start is None or current_end is None:
        return False
    current_remaining = _ev_window_remaining_hours(day_idx, current_start, current_end, sim_h)
    proposed_remaining = _ev_window_remaining_hours(day_idx, start_h, end_h, sim_h)
    return current_remaining > 1e-6 and proposed_remaining + 1e-6 < current_remaining


def _water_heater_configured_preheat_hours(suite) -> float:
    wh = getattr(suite, "_water_heater", None)
    if wh is None:
        return 4.0
    start_h = getattr(wh, "pre_heat_window_start_h", 15.0)
    end_h = getattr(wh, "pre_heat_window_end_h", 18.0)
    interval = _abs_interval_from_hod(0, start_h, end_h)
    if interval is None:
        return 4.0
    return max(1.0, float(interval[1] - interval[0]))


def _bounded_water_heater_preheat_window(
    suite,
    day_idx: int,
    start_h: Any,
    end_h: Any,
    sim_h: float,
) -> tuple[Any, Any, str | None]:
    if start_h is None or end_h is None:
        return start_h, end_h, None
    interval = _abs_interval_from_hod(day_idx, start_h, end_h)
    if interval is None:
        return start_h, end_h, "invalid"
    start_abs, end_abs = interval
    if end_abs <= sim_h + 1e-6:
        return start_h, end_h, "past"
    duration = end_abs - start_abs
    max_duration = min(8.0, _water_heater_configured_preheat_hours(suite) + 1.0)
    if duration <= max_duration + 1e-6:
        return start_h, end_h, None
    bounded_start_abs = max(sim_h, end_abs - max_duration)
    if bounded_start_abs >= end_abs - 1e-6:
        return start_h, end_h, "past"
    return round(bounded_start_abs % 24.0, 3), round(end_abs % 24.0, 3), "bounded"


def _apply_appliance_actions(suite, actions: dict, sim_h: float) -> None:
    """Apply independent per-appliance scheduling commands from the LLM agent.

    Each appliance is handled independently — touching one never affects another.
    ``actions`` comes from the ``"appliances"`` key of the LLM JSON response.
    """
    if not actions:
        return
    day_idx = int(sim_h // 24)

    # --- Shiftable tasks ---
    for name in ("washer", "dishwasher", "dryer"):
        skip_val = actions.get(f"{name}_skip")
        if skip_val is True:
            if _shiftable_has_existing_service_plan(suite, name, day_idx):
                print(
                    f"    [Appliance] skip {name} day={day_idx} -> rejected "
                    "(existing schedule/run preserved)"
                )
                continue
            ok = suite.skip_appliance(name, day_idx)
            print(f"    [Appliance] skip {name} day={day_idx} -> {'ok' if ok else 'rejected'}")
            continue  # don't shift if skipping
        key = f"{name}_start_h"
        val = actions.get(key)
        if val is None:
            continue
        try:
            hod = float(val)
            abs_h = day_idx * 24 + hod
            app = getattr(suite, "_shiftable", {}).get(name)
            if (
                app is not None
                and bool(getattr(app, "_overnight", False))
                and hod < float(getattr(app, "earliest_h", 0.0))
            ):
                abs_h += 24.0
            ok = suite.shift_appliance(name, day_idx, abs_h)
            print(f"    [Appliance] shift {name} day={day_idx} hod={hod:.1f} -> {'ok' if ok else 'rejected'}")
        except (TypeError, ValueError) as e:
            print(f"    [Appliance] bad {key} value={val}: {e}")

    # --- Water heater preheat schedule ---
    preheat     = actions.get("water_heater_preheat")
    ph_start    = actions.get("water_heater_preheat_start_h")
    ph_end      = actions.get("water_heater_preheat_end_h")
    ph_temp     = actions.get("water_heater_preheat_temp_c")
    _any_ph = preheat is not None or ph_start is not None or ph_end is not None or ph_temp is not None
    if _any_ph:
        try:
            if preheat is False and ph_start is None and ph_end is None and ph_temp is None:
                # explicit disable-only: no-op (preheat stays inactive)
                print(f"    [Appliance] water_heater preheat=False -> no-op")
            else:
                bounded_start, bounded_end, wh_guard = _bounded_water_heater_preheat_window(
                    suite,
                    day_idx,
                    ph_start,
                    ph_end,
                    sim_h,
                )
                if wh_guard in {"invalid", "past"}:
                    print(
                        f"    [Appliance] water_heater preheat schedule: "
                        f"start={ph_start} end={ph_end} temp={ph_temp} -> rejected ({wh_guard})"
                    )
                else:
                    if wh_guard == "bounded":
                        print(
                            f"    [Appliance] water_heater preheat schedule adjusted: "
                            f"{ph_start}-{ph_end} -> {bounded_start}-{bounded_end}"
                        )
                    ph_start, ph_end = bounded_start, bounded_end
                    ok = suite.set_ewh_preheat_schedule(
                        day_idx,
                        start_h=float(ph_start) if ph_start is not None else None,
                        end_h=float(ph_end)     if ph_end   is not None else None,
                        temp_c=float(ph_temp)   if ph_temp  is not None else None,
                    )
                    print(f"    [Appliance] water_heater preheat schedule: "
                          f"start={ph_start} end={ph_end} temp={ph_temp} "
                          f"-> {'ok' if ok else 'rejected'}")
        except Exception as e:
            print(f"    [Appliance] water_heater preheat error: {e}")

    # --- EV charging mode + per-day window ---
    ev_mode     = actions.get("ev_mode")
    ev_ch_start = actions.get("ev_charge_start_h")
    ev_ch_end   = actions.get("ev_charge_end_h")
    if ev_mode is not None:
        try:
            ok = suite.set_ev_mode(day_idx, str(ev_mode))
            print(f"    [Appliance] ev mode={ev_mode} -> {'ok' if ok else 'rejected'}")
        except Exception as e:
            print(f"    [Appliance] ev mode error: {e}")
    if ev_ch_start is not None or ev_ch_end is not None:
        try:
            if _ev_replan_would_reduce_existing_charge(suite, day_idx, ev_ch_start, ev_ch_end, sim_h):
                print(
                    f"    [Appliance] ev charge_window={ev_ch_start}-{ev_ch_end} -> rejected "
                    "(would shorten existing target-serving window)"
                )
                return
            ok = suite.set_ev_charge_window(
                day_idx,
                start_h=float(ev_ch_start) if ev_ch_start is not None else None,
                end_h=float(ev_ch_end)     if ev_ch_end   is not None else None,
            )
            print(f"    [Appliance] ev charge_window={ev_ch_start}-{ev_ch_end} -> {'ok' if ok else 'rejected'}")
        except Exception as e:
            print(f"    [Appliance] ev charge_window error: {e}")


def _service_from_appliance_action_key(key: str) -> str | None:
    key = str(key)
    if key.startswith("washer_"):
        return "washer"
    if key.startswith("dishwasher_"):
        return "dishwasher"
    if key.startswith("dryer_"):
        return "dryer"
    if key.startswith("water_heater_"):
        return "water_heater"
    if key.startswith("ev_"):
        return "ev"
    return None


def _services_from_appliance_actions(actions: dict | None) -> set[str]:
    services: set[str] = set()
    if not isinstance(actions, dict):
        return services
    for key, value in actions.items():
        if value is None:
            continue
        service = _service_from_appliance_action_key(str(key))
        if service and service != "ev":
            services.add(service)
    if actions.get("ev_charge_start_h") is not None and actions.get("ev_charge_end_h") is not None:
        services.add("ev")
    return services


def _method_policy_action_space_services(method: str) -> set[str]:
    if method == "no_dr":
        return set()
    if method == "rl_ppo_pref_v2":
        return {"washer", "dishwasher", "dryer", "water_heater", "ev"}
    if method in ("agent", "mpc_dynamic", "hema_agent", "rule_milp"):
        return {"washer", "dishwasher", "dryer", "water_heater", "ev"}
    return set()


def _present_appliance_services(appliance_config: dict | None) -> set[str]:
    services: set[str] = set()
    for name, cfg in (appliance_config or {}).items():
        if name == "ac" or name not in {"washer", "dishwasher", "dryer", "water_heater", "ev"}:
            continue
        if isinstance(cfg, dict) and bool(cfg.get("present", False)):
            services.add(str(name))
    return services


def _policy_emitted_services_from_decisions(decisions: list[dict] | None) -> set[str]:
    services: set[str] = set()
    for decision in decisions or []:
        if not isinstance(decision, dict):
            continue
        services |= _services_from_appliance_actions(decision.get("actions"))
        services |= _services_from_appliance_actions(decision.get("raw_appliance_actions"))
    return services


def _schedule_no_dr_routine_appliances(suite, appliance_config: dict | None, sim_days: int, seed_text: str) -> list[dict]:
    """Create a reproducible non-DR appliance routine.

    This is a counterfactual user routine, not a controller fallback: it ignores
    VPP timing, schedules shiftable tasks randomly within each user's allowed
    window, and uses ordinary water-heater/EV service windows.
    """
    rng = random.Random(int(hashlib.sha256(seed_text.encode("utf-8")).hexdigest()[:16], 16))
    records: list[dict] = []
    cfg = appliance_config or {}
    for day_idx in range(max(1, int(sim_days))):
        day_record: dict[str, Any] = {"day": day_idx + 1, "actions": {}}
        for name in ("washer", "dishwasher", "dryer"):
            app_cfg = cfg.get(name, {}) if isinstance(cfg.get(name, {}), dict) else {}
            if not bool(app_cfg.get("present", False)):
                continue
            earliest = float(app_cfg.get("earliest_h", 8.0))
            latest = float(app_cfg.get("latest_h", 22.0))
            duration = float(app_cfg.get("duration_h", 2.0))
            if latest < earliest:
                latest += 24.0
            latest_start = max(earliest, latest - duration)
            start_h = earliest if latest_start <= earliest else rng.uniform(earliest, latest_start)
            start_abs = day_idx * 24.0 + start_h
            ok = suite.shift_appliance(name, day_idx, start_abs)
            if ok:
                day_record["actions"][f"{name}_start_h"] = round(start_h % 24.0, 3)
        wh_cfg = cfg.get("water_heater", {}) if isinstance(cfg.get("water_heater", {}), dict) else {}
        if bool(wh_cfg.get("present", False)):
            start_h = float(wh_cfg.get("normal_start_h", 17.0))
            end_h = float(wh_cfg.get("normal_end_h", 21.0))
            temp_c = float(wh_cfg.get("normal_temp_c", 60.0))
            if suite.set_ewh_preheat_schedule(day_idx, start_h=start_h, end_h=end_h, temp_c=temp_c):
                day_record["actions"].update({
                    "water_heater_preheat": True,
                    "water_heater_preheat_start_h": round(start_h, 3),
                    "water_heater_preheat_end_h": round(end_h, 3),
                    "water_heater_preheat_temp_c": round(temp_c, 1),
                })
        ev_cfg = cfg.get("ev", {}) if isinstance(cfg.get("ev", {}), dict) else {}
        if bool(ev_cfg.get("present", False)):
            start_h = float(ev_cfg.get("arrival_h", 18.0))
            end_h = float(ev_cfg.get("departure_h", 7.5))
            suite.set_ev_mode(day_idx, "normal")
            if suite.set_ev_charge_window(day_idx, start_h=start_h, end_h=end_h):
                day_record["actions"].update({
                    "ev_charge_start_h": round(start_h, 3),
                    "ev_charge_end_h": round(end_h, 3),
                })
        records.append(day_record)
    return records


def _requested_skip_devices(actions: dict | None) -> List[str]:
    """Return shiftable appliances explicitly marked to skip for the current day."""
    requested = []
    for name in ("washer", "dishwasher", "dryer"):
        if bool((actions or {}).get(f"{name}_skip")):
            requested.append(name)
    return requested


def _present_agent_controlled_appliances(appliance_config: dict | None) -> List[str]:
    """Present non-AC appliances the policy must explicitly command.

    Even non-DR-adjustable devices need a policy command in explicit-only
    simulation; for those devices the command should preserve the user's
    routine rather than reschedule it.
    """
    cfg = appliance_config or {}
    names = []
    for name in ("washer", "dishwasher", "dryer", "water_heater", "ev"):
        dev_cfg = cfg.get(name, {}) or {}
        if bool(dev_cfg.get("present", False)):
            names.append(name)
    return names


def _ev_required_charge_hours(appliance_config: dict | None) -> float:
    """Conservative EV charge hours needed after daily driving."""
    ev_cfg = ((appliance_config or {}).get("ev", {}) or {})
    try:
        charger_kw = max(0.1, float(ev_cfg.get("charger_kw", 7.0)))
        efficiency = max(0.1, float(ev_cfg.get("efficiency", 0.92)))
        daily_drive = max(0.5, float(ev_cfg.get("daily_drive_kwh", 8.0)))
    except (TypeError, ValueError):
        return 3.0
    # Add one control timestep plus a small safety margin so the explicit
    # policy is robust after a prior missed EV target.
    return min(6.0, max(0.5, daily_drive / (charger_kw * efficiency) + 0.35))


def _ev_service_window_guidance_text(
    appliance_config: dict | None,
    *,
    vpp_event: dict | None = None,
) -> str:
    ev_cfg = ((appliance_config or {}).get("ev", {}) or {})
    if not bool(ev_cfg.get("present", False)):
        return ""
    try:
        departure = float(ev_cfg.get("departure_h", 7.5)) % 24.0
        target_soc = float(ev_cfg.get("target_soc", 0.8)) * 100.0
    except (TypeError, ValueError):
        departure, target_soc = 7.5, 80.0
    min_hours = _ev_required_charge_hours(appliance_config)
    safe_start = 0.0
    safe_end = min(max(0.5, departure), max(0.5, min_hours))
    if safe_end < min_hours:
        safe_end = min(8.0, min_hours)
    example = f"{safe_start:.1f}-{safe_end:.1f}"
    return (
        "\nEV service hard rule: the EV target is a departure service target, not just a field-output target. "
        f"The EV should reach about {target_soc:.0f}% SOC before the daily departure around {departure:.1f}h. "
        "Do not treat arrival time as a hard constraint: a same-day early-morning window such as 0.0-8.0 is valid "
        "and represents charging for that day's EV use, including on day 1. "
        f"Use a non-VPP window of at least {min_hours:.1f}h; for this event a safe pattern is "
        f"ev_charge_start_h={example.split('-')[0]}, ev_charge_end_h={example.split('-')[1]}. "
        "If previous feedback mentioned EV SOC missed, extend the early-morning window or choose another non-VPP slot."
    )


def _ev_service_window_errors(
    actions: dict | None,
    appliance_config: dict | None,
    *,
    vpp_event: dict | None = None,
) -> List[str]:
    """Find EV windows that cannot serve today's SOC target."""
    ev_cfg = ((appliance_config or {}).get("ev", {}) or {})
    if not bool(ev_cfg.get("present", False)):
        return []
    actions = actions or {}
    if actions.get("ev_charge_start_h") is None or actions.get("ev_charge_end_h") is None:
        return ["EV present but ev_charge_start_h/ev_charge_end_h is missing"]
    try:
        start_raw = float(actions.get("ev_charge_start_h"))
        end_raw = float(actions.get("ev_charge_end_h"))
        start = start_raw % 24.0
        # 24.0 is a valid same-day stop time in the EV simulator; do not
        # modulo it to 0.0 for service-feasibility checks.
        end = 24.0 if 23.999 <= end_raw <= 24.001 else (end_raw % 24.0)
    except (TypeError, ValueError):
        return ["EV charge window is not numeric"]
    errors: List[str] = []
    intervals = [(start, end)] if end > start else [(start, 24.0), (0.0, end)]
    effective_hours = 0.0
    for segment_start, segment_end in intervals:
        if segment_end <= segment_start:
            continue
        if vpp_event:
            vpp_start = _event_start_hod(vpp_event)
            vpp_end = _event_end_hod(vpp_event)
            overlap = max(0.0, min(segment_end, vpp_end) - max(segment_start, vpp_start))
        else:
            overlap = 0.0
        effective_hours += max(0.0, segment_end - segment_start - overlap)
    min_hours = _ev_required_charge_hours(appliance_config)
    if effective_hours + 1e-6 < min_hours:
        errors.append(
            f"EV usable non-VPP charge time is {effective_hours:.1f}h, below required ~{min_hours:.1f}h"
        )
    return errors


def _shiftable_service_window_errors(
    actions: dict | None,
    appliance_config: dict | None,
) -> List[str]:
    """Find washer/dishwasher/dryer starts that the simulator will reject."""
    actions = actions or {}
    cfg = appliance_config or {}
    errors: List[str] = []
    for name in ("washer", "dishwasher", "dryer"):
        dev_cfg = (cfg.get(name, {}) or {})
        if not bool(dev_cfg.get("present", False)) or actions.get(f"{name}_skip") is True:
            continue
        if actions.get(f"{name}_start_h") is None:
            continue
        try:
            start = float(actions.get(f"{name}_start_h")) % 24.0
            earliest = float(dev_cfg.get("earliest_h", 8.0)) % 24.0
            latest = float(dev_cfg.get("latest_h", 22.0)) % 24.0
            duration = max(0.0, float(dev_cfg.get("duration_h", 1.0)))
        except (TypeError, ValueError):
            errors.append(f"{name} start_h is not numeric")
            continue
        latest_start = (latest - duration) % 24.0
        overnight = latest < earliest
        if overnight:
            valid = start >= earliest or start <= latest_start
            window = f"{earliest:.1f}-(+1d){latest:.1f}"
        else:
            valid = earliest <= start <= latest_start
            window = f"{earliest:.1f}-{latest:.1f}"
        if not valid:
            errors.append(
                f"{name} start {start:.1f} is outside executable window {window}; "
                f"duration {duration:.1f}h means latest start is {latest_start:.1f}"
            )
    return errors


def _fixed_appliance_constraint_text(appliance_config: dict | None) -> str:
    """Describe present devices that should keep routine commands."""
    cfg = appliance_config or {}
    fixed: List[str] = []
    for name in ("washer", "dishwasher", "dryer"):
        dev_cfg = cfg.get(name, {}) or {}
        if bool(dev_cfg.get("present", False)) and (
            not bool(dev_cfg.get("shiftable", True)) or not bool(dev_cfg.get("dr_adjustable", True))
        ):
            preferred = dev_cfg.get("preferred_h", "?")
            duration = dev_cfg.get("duration_h", "?")
            fixed.append(f"{name}: fixed/non-DR-adjustable preferred start={preferred}, duration={duration}h")
    wh_cfg = cfg.get("water_heater", {}) or {}
    if bool(wh_cfg.get("present", False)) and not bool(wh_cfg.get("dr_adjustable", True)):
        fixed.append(
            "water_heater: fixed/non-DR-adjustable "
            f"preheat={wh_cfg.get('pre_heat_window_start_h', '?')}-{wh_cfg.get('pre_heat_window_end_h', '?')}"
        )
    if not fixed:
        return ""
    return (
        "\n[Routine-locked appliance constraints]\n"
        "These present devices are not DR-adjustable, but the explicit-only simulator still needs commands for them. "
        "Output routine-preserving commands for them; do not reschedule or skip them for VPP benefit:\n"
        + "\n".join(f"- {item}" for item in fixed)
    )


def _price_sensitive_explanation_mode(persona_config: dict | None) -> bool:
    """True when the user expects a short benefit/impact estimate with suggestions."""
    tags = (persona_config or {}).get("tags", {}) or {}
    if tags.get("price") in {"price_sensitive", "price_driven"}:
        return True
    weights = ((persona_config or {}).get("preferences", {}) or {}).get("scoring_weights", {}) or {}
    try:
        return float(weights.get("energy", 0.0)) >= 0.4 and tags.get("price") not in {"low_incentive", "price_indifferent"}
    except (TypeError, ValueError):
        return False


def _controllable_vpp_load_estimate_kw(appliance_config: dict | None) -> float:
    """Rough controllable appliance load useful for user-facing DR impact estimates."""
    cfg = appliance_config or {}
    total = 0.0
    for name in ("washer", "dishwasher", "dryer"):
        dev_cfg = cfg.get(name, {}) or {}
        if dev_cfg.get("present") and dev_cfg.get("dr_adjustable", dev_cfg.get("shiftable", True)) is not False:
            total += float(dev_cfg.get("power_kw", 0.0) or 0.0)
    wh_cfg = cfg.get("water_heater", {}) or {}
    if wh_cfg.get("present") and wh_cfg.get("dr_adjustable", True) is not False:
        total += float(wh_cfg.get("rated_kw", 0.0) or 0.0)
    ev_cfg = cfg.get("ev", {}) or {}
    if ev_cfg.get("present") and ev_cfg.get("dr_adjustable", True) is not False:
        total += float(ev_cfg.get("power_kw", ev_cfg.get("charger_kw", 0.0)) or 0.0)
    return max(0.0, total)


def _benefit_explanation_prompt_text(
    persona_config: dict | None,
    appliance_config: dict | None,
    event: dict | None,
    demand_kw: float | None = None,
) -> str:
    """Prompt hint for users who want quantified savings/impact explanations."""
    if not event or not _price_sensitive_explanation_mode(persona_config):
        return ""
    estimate_kw = _controllable_vpp_load_estimate_kw(appliance_config)
    if demand_kw is not None and 0.0 < demand_kw <= 0.75:
        return (
            "\nBENEFIT_EXPLANATION: this price/energy-sensitive user expects a brief quantified reason. "
            f"This is a low-capacity-reference event (~{demand_kw:.2f} kW), so avoid overreaction. "
            "The VPP success criterion is keeping non-AC appliances out of the VPP window. "
            "Do not quote the full whole-home flexible-load estimate as if all of it must be curtailed."
        )
    target_text = ""
    if demand_kw is not None and demand_kw > 0:
        target_text = f" Diagnostic flexible-load reference is about {demand_kw:.2f} kW."
    return (
        "\nBENEFIT_EXPLANATION: this price/energy-sensitive user expects a brief quantified reason. "
        f"If no price file is available, estimate impact as controllable load shifted away from VPP_WINDOW "
        f"(roughly {estimate_kw:.1f} kW when all flexible devices are relevant).{target_text} "
        "Do not invent money saved; use a compact load/impact estimate in reason."
    )


def _vpp_intensity_prompt_text(event: dict | None, demand_kw: float | None) -> str:
    """Tell the agent to scale intrusiveness to the event target, not just the VPP label."""
    if not event or demand_kw is None:
        return ""
    try:
        target_kw = float(demand_kw)
    except (TypeError, ValueError):
        return ""
    if target_kw <= 0.0:
        return (
            "\nVPP_INTENSITY: this event uses an energy-cap target rather than a positive shed-kW target. "
            "Use low-risk schedules that already avoid VPP_WINDOW. If the calendar says the home is away/unoccupied, "
            "use the warm efficient edge of the AC range to meet the cap; if occupied or return-home, protect comfort and restore immediately."
        )
    if target_kw <= 0.75:
        return (
            f"\nVPP_INTENSITY: low target ({target_kw:.2f} kW). Use the least intrusive plan that meets VPP_WINDOW: "
            "avoid starting controllable loads inside the window, but do not imply a whole-home aggressive curtailment. "
            "If flexible devices are already outside the window, preserve routine. If the window overlaps occupancy or return-home time, "
            "prioritize the user's normal comfort setpoint over extra AC setback; if the home is away/unoccupied, use the warm efficient edge."
        )
    return ""


def _ensure_price_sensitive_reason_estimate(
    reason: str,
    persona_config: dict | None,
    appliance_config: dict | None,
    event: dict | None,
    demand_kw: float | None = None,
) -> str:
    """Add a compact impact estimate when a price-sensitive reason omits it."""
    text = str(reason or "")
    if not event or not _price_sensitive_explanation_mode(persona_config):
        return text[:100]
    lowered = text.lower()
    has_number = any(ch.isdigit() for ch in lowered)
    quantified_terms = ("kw", "kwh", "est.", "estimate", "roughly", "~")
    if has_number and any(token in lowered for token in quantified_terms):
        return text[:100]
    try:
        target_kw = float(demand_kw) if demand_kw is not None else None
    except (TypeError, ValueError):
        target_kw = None
    if target_kw is not None and 0.0 < target_kw <= 0.75:
        suffix = f" | low target ~{target_kw:.2f}kW, minimal action"
        base = text[: max(0, 100 - len(suffix))].rstrip(" ,;:|-")
        return (base + suffix)[:100]
    estimate_kw = _controllable_vpp_load_estimate_kw(appliance_config)
    if estimate_kw <= 0:
        return text[:100]
    suffix = f" | est. shifted ~{estimate_kw:.1f}kW"
    base = text[: max(0, 100 - len(suffix))].rstrip(" ,;:|-")
    return (base + suffix)[:100]


def _objective_total_value(terms: dict | None) -> float | None:
    try:
        if not isinstance(terms, dict):
            return None
        value = terms.get("total")
        if value is None:
            return None
        value_f = float(value)
        if value_f != value_f:
            return None
        return value_f
    except (TypeError, ValueError):
        return None


def _protective_control_mode(persona_config: dict | None) -> bool:
    """True when the persona should receive advisory/minimal-control DR behavior."""
    persona_config = persona_config or {}
    tags = persona_config.get("tags", {}) or {}
    schedule = persona_config.get("schedule", {}) or {}
    return (
        tags.get("schedule") == "caregiver"
        or tags.get("comfort") == "temp_sensitive"
        or tags.get("control") in {"confirm_required", "low_auto_accept", "privacy_sensitive"}
        or bool(schedule.get("vulnerable_members"))
    )


def _low_dr_intrusion_sensitive_mode(persona_config: dict | None) -> bool:
    """True when DR should be framed as low-disruption comfort preservation."""
    persona_config = persona_config or {}
    tags = persona_config.get("tags", {}) or {}
    return (
        tags.get("price") in {"low_incentive", "price_indifferent"}
        or tags.get("grid_value") in {"low_value", "uncertain_flex"}
        or tags.get("task") in {"rigid", "semi_rigid"}
    )


def _price_sensitive_auto_saving_mode(persona_config: dict | None) -> bool:
    """True when a user explicitly trusts automation for price/grid savings."""
    persona_config = persona_config or {}
    tags = persona_config.get("tags", {}) or {}
    schedule = persona_config.get("schedule", {}) or {}
    return (
        tags.get("price") == "price_sensitive"
        and tags.get("control") == "high_trust_auto"
        and not bool(schedule.get("vulnerable_members"))
    )


def _event_score_int(event: dict, key: str, default: int = 3) -> int:
    try:
        return max(1, min(5, int(round(float(event.get(key, default))))))
    except (TypeError, ValueError):
        return default


def _event_has_controllable_service_issue(event: dict, persona_config: dict | None) -> bool:
    """True when past feedback contains a controllable appliance violation."""
    try:
        from user_pref_scorer import _fixed_appliance_constraints
        fixed = set(_fixed_appliance_constraints(persona_config or {}))
    except Exception:
        fixed = set()
    summary = event.get("appliance_summary") or {}
    if not isinstance(summary, dict):
        return False
    for name, info in summary.items():
        if not isinstance(info, dict) or not bool(info.get("present")):
            continue
        if name in {"washer", "dishwasher", "dryer"} and bool(info.get("skipped")):
            return True
        if name not in fixed and bool(info.get("ran_during_vpp")):
            return True
    return False


def _learned_efficiency_adaptation_enabled(
    past_events: list[dict] | None,
    persona_config: dict | None,
) -> bool:
    """Enable small energy-saving exploration only after positive feedback.

    This is intentionally evidence-based rather than persona-id based. It lets
    the Agent become slightly more efficient after the simulated user has
    repeatedly accepted prior VPP actions, while automatically disabling the
    behavior for protective/confirmation-heavy users or after negative feedback.
    """
    events = list(past_events or [])
    if len(events) < 2:
        return False
    persona_config = persona_config or {}
    tags = persona_config.get("tags", {}) or {}
    schedule = persona_config.get("schedule", {}) or {}
    if (
        _protective_control_mode(persona_config)
        or _low_dr_intrusion_sensitive_mode(persona_config)
        or tags.get("comfort") == "temp_sensitive"
        or tags.get("control") in {"confirm_required", "low_auto_accept", "privacy_sensitive"}
        or bool(schedule.get("vulnerable_members"))
    ):
        return False
    if any(_event_score_int(e, "score") <= 3 for e in events):
        return False
    recent = events[-2:]
    if not all(_event_score_int(e, "score") >= 4 for e in recent):
        return False
    if not all(_event_score_int(e, "comfort_score") >= 4 for e in recent):
        return False
    if any(_event_has_controllable_service_issue(e, persona_config) for e in recent):
        return False
    feedback_text = " ".join(
        (str(e.get("comment", "")) + " " + str(e.get("user_input", ""))).lower()
        for e in events
    )
    if any(
        token in feedback_text
        for token in ("too warm", "above 26", "26.5", "hot", "uncomfortable", "temperature drift")
    ):
        return False
    return True


def _learned_efficiency_floor_c(
    past_events: list[dict] | None,
    persona_config: dict | None,
    *,
    default_sp_c: float,
    preferred_max_c: float,
    vpp_active: bool,
) -> float | None:
    """Return a learned lower bound for cooling setpoint, if safe to explore."""
    if not _learned_efficiency_adaptation_enabled(past_events, persona_config):
        return None
    step_c = 1.0 if vpp_active else 0.5
    return round(min(float(preferred_max_c), float(default_sp_c) + step_c), 1)


def _agent_rule_milp_hvac_feedback_adjustment_c(
    past_events: list[dict] | None,
    *,
    preferred_max_c: float,
    run_sp_min_c: float,
    run_sp_max_c: float,
) -> float | None:
    """Return a small EB-only AC relaxation after explicit comfort complaints.

    EnergyBridge still uses the Rule+MILP appliance plan.  This only relaxes the
    active-event HVAC-off override after the user has complained about AC/heat in
    a previous VPP event.  The retreat is intentionally limited: EnergyBridge
    should remain close to the Rule+MILP energy-optimal plan and only spend a
    small amount of extra HVAC energy to acknowledge feedback.
    """
    events = [event for event in list(past_events or []) if isinstance(event, dict)]
    if not events:
        return None
    text = " ".join(
        str(event.get(key, ""))
        for event in events
        for key in ("comment", "controller_feedback", "member_feedback_summary", "reason", "user_input")
    ).lower()
    comfort_scores = [
        _event_score_int(event, "comfort_score")
        for event in events
        if event.get("comfort_score") is not None
    ]
    setpoints = []
    for event in events:
        try:
            setpoints.append(float(event.get("setpoint")))
        except (TypeError, ValueError):
            continue
    explicit_comfort_complaint = any(
        token in text
        for token in (
            "40",
            "too warm",
            "too hot",
            "exceeded",
            "went too far",
            "far outside",
            "above my comfort",
            "above my preferred",
            "comfort bound",
            "comfort limit",
            "unacceptable",
            "unusable",
            "temperature-sensitive",
        )
    )
    low_comfort_score = bool(comfort_scores) and min(comfort_scores) <= 2
    hvac_off_used = any(sp >= 35.0 for sp in setpoints)
    warm_event_seen = explicit_comfort_complaint or low_comfort_score
    if not ((explicit_comfort_complaint and (low_comfort_score or hvac_off_used)) or (low_comfort_score and hvac_off_used)):
        if not warm_event_seen:
            return None
    repeated_comfort_complaint = (
        sum(1 for score in comfort_scores if score <= 2) >= 2
        or (
            low_comfort_score
            and any(
                token in text
                for token in (
                    "again",
                    "still",
                    "repeated",
                    "repeating",
                    "remains unresolved",
                    "unresolved",
                    "same issue",
                )
            )
        )
    )
    if repeated_comfort_complaint:
        return round(max(float(run_sp_min_c), min(float(run_sp_max_c), float(preferred_max_c))), 1)
    efficient_feedback_cap = float(preferred_max_c) + 2.0
    return round(max(float(run_sp_min_c), min(float(run_sp_max_c), efficient_feedback_cap)), 1)


def _multi_user_household_comfort_first_mode(persona_config: dict | None) -> bool:
    meta = (persona_config or {}).get("meta") or {}
    return meta.get("persona_type") in {
        "multi_user_household",
        "multi_user_household_independent_roleplay",
    }


def _learned_efficiency_prompt_text(
    past_events: list[dict] | None,
    persona_config: dict | None,
    *,
    default_sp_c: float,
    preferred_max_c: float,
) -> str:
    floor = _learned_efficiency_floor_c(
        past_events,
        persona_config,
        default_sp_c=default_sp_c,
        preferred_max_c=preferred_max_c,
        vpp_active=False,
    )
    vpp_floor = _learned_efficiency_floor_c(
        past_events,
        persona_config,
        default_sp_c=default_sp_c,
        preferred_max_c=preferred_max_c,
        vpp_active=True,
    )
    if floor is None or vpp_floor is None:
        return ""
    return (
        "\nLEARNED_EFFICIENCY_ADAPTATION: recent VPP feedback was satisfied and comfortable. "
        f"For ordinary occupied planning, avoid cooling below about {floor:.1f}°C unless needed. "
        f"During VPP, use about {vpp_floor:.1f}°C when it remains inside the user's preferred range, "
        "then restore comfort without overcooling."
    )


def _comfort_reason_for_low_dr_user(reason: str, persona_config: dict | None) -> str:
    """Tone down VPP jargon for users who dislike intrusive DR framing."""
    if not _low_dr_intrusion_sensitive_mode(persona_config):
        return reason[:100]
    replacements = {
        "VPP event": "brief event",
        "VPP active": "brief event active",
        "VPP over": "event over",
        "VPP ended": "event ended",
        "during VPP": "during the event",
        "through VPP": "through the event",
        "VPP": "event",
        "grid-supportive": "low-risk",
        "grid support": "low-risk support",
        "shed load": "reduce load gently",
        "saving money": "low-risk routine support",
        "savings": "routine support",
        "cost optimized": "routine kept stable",
        "costs optimized": "routine kept stable",
        "cost": "routine",
        "price": "routine",
        "bill": "routine",
        "money": "routine",
    }
    text = reason
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text[:100]


def _persona_agent_policy_text(persona_config: dict | None) -> str:
    """Add persona-specific communication/control constraints to the Agent prompt."""
    persona_config = persona_config or {}
    tags = persona_config.get("tags", {}) or {}
    parts: List[str] = []
    if tags.get("control") == "confirm_required":
        parts.append(
            "This user requires explicit event-level confirmation. Treat the role-play user's selected strategy/preference as the only allowed scope. Do not exceed it."
        )
    if tags.get("comfort") == "temp_sensitive":
        parts.append(
            "This user is temperature-sensitive. Keep the AC inside the preferred range; avoid raising the setpoint just to chase grid benefit."
        )
    if tags.get("price") in {"low_incentive", "price_indifferent"}:
        parts.append(
            "This user responds to comfort, consent, and routine stability rather than financial nudges. Keep the user-facing reason comfort-preserving and low-risk."
        )
        parts.append(
            "If DAY_AHEAD_PRICE is provided, use it only as a quiet secondary optimizer for truly controllable devices after comfort, consent, and routine stability."
        )
    if _low_dr_intrusion_sensitive_mode(persona_config):
        parts.append(
            "Use low-disruption wording in the reason field. Avoid repeated VPP/grid jargon; describe the action as holding comfort and preserving routine unless a strong grid action was explicitly accepted."
        )
    if tags.get("control") == "high_trust_auto":
        parts.append(
            "This user accepts automation within comfort bounds, not unlimited discomfort. During an occupied or return-home VPP window, keep AC below the warm edge of the preferred range and prioritize reliable appliance shifting."
        )
    if _price_sensitive_auto_saving_mode(persona_config):
        parts.append(
            "This user is price-sensitive and explicitly trusts automation. During a VPP event, keep non-AC appliances out of the VPP window and use only the warm still-preferred edge of the comfort range before asking for extra discomfort."
        )
    if not parts:
        return ""
    return "\n[PERSONA-SPECIFIC AGENT POLICY]\n" + "\n".join(f"- {part}" for part in parts)


def _missing_explicit_appliance_actions(actions: dict | None, appliance_config: dict | None) -> List[str]:
    """Return fields missing from an Agent response for present controllable appliances."""
    actions = actions or {}
    missing: List[str] = []
    present = set(_present_agent_controlled_appliances(appliance_config))
    for name in ("washer", "dishwasher", "dryer"):
        if name in present:
            start_key = f"{name}_start_h"
            skip_key = f"{name}_skip"
            if actions.get(skip_key) is True:
                continue
            if actions.get(start_key) is None:
                missing.append(start_key)
            if actions.get(skip_key) is None:
                missing.append(skip_key)
    if "water_heater" in present:
        for key in (
            "water_heater_preheat_start_h",
            "water_heater_preheat_end_h",
            "water_heater_preheat_temp_c",
            "water_heater_preheat",
        ):
            if actions.get(key) is None:
                missing.append(key)
    if "ev" in present:
        if actions.get("ev_charge_start_h") is None:
            missing.append("ev_charge_start_h")
        if actions.get("ev_charge_end_h") is None:
            missing.append("ev_charge_end_h")
    return missing


def _explicit_appliance_requirement_text(
    appliance_config: dict | None, *, vpp_event: dict | None = None
) -> str:
    """Human-readable prompt text listing the exact non-null fields required now."""
    present = _present_agent_controlled_appliances(appliance_config)
    if not present:
        return "\n[Explicit appliance commands required now]: none; no controlled appliances are present."
    vpp_note = (
        "\nBecause today has a VPP event window "
        f"{_event_window_text(vpp_event)}, any emitted commands should avoid putting controllable load "
        "inside that event window when feasible. For fixed/non-DR-adjustable routines, emit the user's normal "
        "routine instead of forcing a VPP shift; reduced VPP capacity is preferable to violating consent."
        if vpp_event else ""
    )
    return (
        "\n[Explicit appliance commands required now]\n"
        "No appliance has a default schedule. A present appliance will not start unless your JSON emits "
        "a concrete command for it.\n"
        "If you omit a present appliance field or leave it null, that appliance is treated as not controlled "
        "by this policy and may be penalized in user scoring and task completion.\n"
        "This applies to every present non-AC appliance, including fixed or non-DR-adjustable routine appliances. "
        "For fixed/routine appliances, emit the user's normal preferred routine command instead of leaving it null.\n"
        "For washer/dishwasher/dryer: emit start_h and skip=false, unless the task is truly unnecessary and skip=true. "
        "Their latest_h is the latest FINISH time, so latest valid start is latest_h-duration_h. "
        "For overnight windows, a next-morning hour is valid only if the appliance status shows (+1d) and the "
        "cycle still finishes before latest_h. "
        "For water_heater: emit preheat=true/false plus start/end/temp when controlling it. "
        "For EV: emit ev_charge_start_h and ev_charge_end_h; ev_mode is optional compatibility metadata."
        f"{_ev_service_window_guidance_text(appliance_config, vpp_event=vpp_event)}"
        f"{vpp_note}"
    )


def _interval_overlaps(start: float, end: float, window_start: float, window_end: float) -> bool:
    """Return True when a local-hour interval overlaps a same-day window."""
    start = float(start) % 24.0
    end = float(end) % 24.0
    intervals = [(start, end)] if start < end else [(start, 24.0), (0.0, end)]
    return any(a < window_end and b > window_start for a, b in intervals if a != b)


def _service_is_dr_adjustable(name: str, appliance_config: dict | None) -> bool:
    cfg = appliance_config or {}
    dev = cfg.get(name, {}) or {}
    if not bool(dev.get("present", False)):
        return False
    if name in {"washer", "dishwasher", "dryer"}:
        return bool(dev.get("shiftable", True)) and bool(dev.get("dr_adjustable", True))
    return dev.get("dr_adjustable", True) is not False


def _vpp_appliance_conflicts(
    actions: dict | None,
    appliance_config: dict | None,
    event: dict | None = None,
    current_hod: float | None = None,
) -> List[str]:
    """Find Agent appliance commands that would place controllable load in the VPP window."""
    actions = actions or {}
    cfg = appliance_config or {}
    conflicts: List[str] = []
    present = set(_present_agent_controlled_appliances(appliance_config))
    vpp_start = _event_start_hod(event)
    vpp_end = _event_end_hod(event)
    window_text = _event_window_text(event)
    for name in ("washer", "dishwasher", "dryer"):
        if name not in present or bool(actions.get(f"{name}_skip")):
            continue
        if not _service_is_dr_adjustable(name, appliance_config):
            continue
        start = actions.get(f"{name}_start_h")
        if start is None:
            continue
        duration_h = float((cfg.get(name, {}) or {}).get("duration_h", 1.0))
        try:
            start_f = float(start)
            end_f = start_f + duration_h
            if (
                current_hod is not None
                and vpp_start <= float(current_hod) < vpp_end
                and start_f < float(current_hod)
                and end_f >= vpp_start
            ):
                conflicts.append(
                    f"{name}: start {_fmt_clock_h(start_f)} is in the past at current VPP clock {_fmt_clock_h(float(current_hod))}; use a future non-overlapping time"
                )
            elif _interval_overlaps(start_f, end_f, vpp_start, vpp_end):
                conflicts.append(
                    f"{name}: scheduled {_fmt_clock_h(start_f)}-{_fmt_clock_h(end_f)} overlaps VPP {window_text}"
                )
        except (TypeError, ValueError):
            continue
    if (
        "water_heater" in present
        and actions.get("water_heater_preheat") is not False
        and _service_is_dr_adjustable("water_heater", appliance_config)
    ):
        start = actions.get("water_heater_preheat_start_h")
        end = actions.get("water_heater_preheat_end_h")
        try:
            if start is not None and end is not None and _interval_overlaps(float(start), float(end), vpp_start, vpp_end):
                conflicts.append(
                    f"water_heater: preheat {_fmt_clock_h(float(start))}-{_fmt_clock_h(float(end))} overlaps VPP {window_text}"
                )
        except (TypeError, ValueError):
            pass
    if "ev" in present and _service_is_dr_adjustable("ev", appliance_config):
        mode = str(actions.get("ev_mode") or "").lower()
        if mode == "normal":
            conflicts.append(f"ev: normal mode may charge during VPP {window_text}")
        start = actions.get("ev_charge_start_h")
        end = actions.get("ev_charge_end_h")
        try:
            if start is not None and end is not None and _interval_overlaps(float(start), float(end), vpp_start, vpp_end):
                conflicts.append(
                    f"ev: charge window {_fmt_clock_h(float(start))}-{_fmt_clock_h(float(end))} overlaps VPP {window_text}"
                )
        except (TypeError, ValueError):
            pass
    return conflicts


def _filter_controllable_appliance_actions(actions: dict | None, appliance_config: dict | None) -> dict:
    """Drop commands for absent appliances before applying policy output."""
    actions = actions or {}
    controllable = set(_present_agent_controlled_appliances(appliance_config))
    allowed: set[str] = set()
    for name in ("washer", "dishwasher", "dryer"):
        if name in controllable:
            allowed.update({f"{name}_start_h", f"{name}_skip"})
    if "water_heater" in controllable:
        allowed.update({
            "water_heater_preheat_start_h",
            "water_heater_preheat_end_h",
            "water_heater_preheat_temp_c",
            "water_heater_preheat",
        })
    if "ev" in controllable:
        allowed.update({"ev_mode", "ev_charge_start_h", "ev_charge_end_h"})
    filtered = {key: value for key, value in actions.items() if key in allowed}
    dropped = sorted(key for key, value in actions.items() if value is not None and key not in allowed)
    if dropped:
        print(
            "  [Appliance Control] dropping commands for absent/unsupported devices: "
            f"{', '.join(dropped)}"
        )
    return filtered


def _abs_interval_from_hod(day_idx: int, start_h: Any, end_h: Any) -> tuple[float, float] | None:
    try:
        start = float(day_idx * 24.0 + (float(start_h) % 24.0))
        end = float(day_idx * 24.0 + (float(end_h) % 24.0))
    except (TypeError, ValueError):
        return None
    if end <= start:
        end += 24.0
    return start, end


def _abs_intervals_overlap(start: float, end: float, window_start: float, window_end: float) -> bool:
    return max(float(start), float(window_start)) < min(float(end), float(window_end))


def _suite_planned_service_interval(
    suite: Any,
    name: str,
    day_idx: int,
    appliance_config: dict | None,
) -> tuple[float, float] | None:
    """Return the simulator's current planned service interval for one appliance."""
    cfg = appliance_config or {}
    try:
        results = suite.all_results()
    except Exception:
        results = {}
    if name in ("washer", "dishwasher", "dryer"):
        days = results.get(name, []) if isinstance(results, dict) else []
        if not (0 <= day_idx < len(days)):
            return None
        rec = days[day_idx] or {}
        if rec.get("completed") or rec.get("skipped"):
            return None
        start = rec.get("scheduled_abs_h")
        if start is None:
            return None
        duration = float(((cfg.get(name, {}) or {}).get("duration_h", 1.0)) or 1.0)
        return float(start), float(start) + max(1e-6, duration)
    if name == "water_heater":
        wh = getattr(suite, "_water_heater", None)
        state = (getattr(wh, "_days", {}) or {}).get(day_idx, {}) if wh is not None else {}
        if not state.get("preheat_requested"):
            return None
        start_h = state.get("preheat_start_h")
        end_h = state.get("preheat_end_h")
        if start_h is None:
            start_h = getattr(wh, "pre_heat_window_start_h", None)
        if end_h is None:
            end_h = getattr(wh, "pre_heat_window_end_h", None)
        return _abs_interval_from_hod(day_idx, start_h, end_h)
    if name == "ev":
        ev = getattr(suite, "_ev", None)
        start_h = (getattr(ev, "_day_charge_start", {}) or {}).get(day_idx) if ev is not None else None
        end_h = (getattr(ev, "_day_charge_end", {}) or {}).get(day_idx) if ev is not None else None
        if start_h is None or end_h is None:
            return None
        return _abs_interval_from_hod(day_idx, start_h, end_h)
    return None


def _candidate_action_interval(
    actions: dict,
    name: str,
    day_idx: int,
    appliance_config: dict | None,
) -> tuple[float, float] | None:
    cfg = appliance_config or {}
    if name in ("washer", "dishwasher", "dryer"):
        if actions.get(f"{name}_skip") is True:
            return None
        start = actions.get(f"{name}_start_h")
        if start is None:
            return None
        duration = float(((cfg.get(name, {}) or {}).get("duration_h", 1.0)) or 1.0)
        try:
            start_abs = day_idx * 24.0 + (float(start) % 24.0)
        except (TypeError, ValueError):
            return None
        return start_abs, start_abs + max(1e-6, duration)
    if name == "water_heater":
        if actions.get("water_heater_preheat") is False:
            return None
        return _abs_interval_from_hod(
            day_idx,
            actions.get("water_heater_preheat_start_h"),
            actions.get("water_heater_preheat_end_h"),
        )
    if name == "ev":
        return _abs_interval_from_hod(
            day_idx,
            actions.get("ev_charge_start_h"),
            actions.get("ev_charge_end_h"),
        )
    return None


def _drop_service_action(actions: dict, name: str) -> None:
    if name in ("washer", "dishwasher", "dryer"):
        actions.pop(f"{name}_start_h", None)
        actions.pop(f"{name}_skip", None)
    elif name == "water_heater":
        for key in (
            "water_heater_preheat_start_h",
            "water_heater_preheat_end_h",
            "water_heater_preheat_temp_c",
            "water_heater_preheat",
        ):
            actions.pop(key, None)
    elif name == "ev":
        actions.pop("ev_mode", None)
        actions.pop("ev_charge_start_h", None)
        actions.pop("ev_charge_end_h", None)


def _filter_vpp_event_replan_actions(
    *,
    actions: dict | None,
    suite: Any,
    appliance_config: dict | None,
    event: dict | None,
    sim_h: float,
) -> tuple[dict, dict]:
    """At event start, only move already-planned loads that overlap this VPP window."""
    filtered = dict(actions or {})
    if suite is None or not isinstance(event, dict):
        return filtered, {"active": False}
    day_idx = int(sim_h // 24)
    try:
        event_start = float(event["trigger_h"])
        event_end = float(event["end_h"])
    except (KeyError, TypeError, ValueError):
        return filtered, {"active": False, "reason": "invalid_event"}
    kept: list[str] = []
    dropped: list[str] = []
    for name in ("washer", "dishwasher", "dryer", "water_heater", "ev"):
        if not _service_is_dr_adjustable(name, appliance_config):
            continue
        if not any(key in filtered for key in (
            (f"{name}_start_h", f"{name}_skip") if name in ("washer", "dishwasher", "dryer")
            else (
                "water_heater_preheat_start_h",
                "water_heater_preheat_end_h",
                "water_heater_preheat_temp_c",
                "water_heater_preheat",
            ) if name == "water_heater"
            else ("ev_mode", "ev_charge_start_h", "ev_charge_end_h")
        )):
            continue
        planned = _suite_planned_service_interval(suite, name, day_idx, appliance_config)
        candidate = _candidate_action_interval(filtered, name, day_idx, appliance_config)
        if planned is None or not _abs_intervals_overlap(planned[0], planned[1], event_start, event_end):
            _drop_service_action(filtered, name)
            dropped.append(f"{name}:not_planned_in_vpp")
            continue
        if candidate is None or candidate[0] + 1e-9 < event_end:
            _drop_service_action(filtered, name)
            dropped.append(f"{name}:not_after_vpp")
            continue
        kept.append(name)
    if kept or dropped:
        print(
            "  [VPP Replan Guard] "
            f"kept={kept or 'none'} dropped={dropped or 'none'}"
        )
    return filtered, {
        "active": True,
        "event_id": event.get("id"),
        "kept": kept,
        "dropped": dropped,
        "rule": "only reschedule services already planned inside the VPP window to after the window",
    }


def _stable_unit_random(*parts: Any) -> float:
    payload = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return int(digest[:13], 16) / float(16**13)


def _bounded_probability(value: float, *, lo: float = 0.03, hi: float = 0.97) -> float:
    return max(lo, min(hi, float(value)))


def _persona_vpp_override_prob(persona_config: dict | None) -> float:
    persona_config = persona_config or {}
    prefs = persona_config.get("preferences") or {}
    raw = persona_config.get("vpp_override_prob", prefs.get("vpp_override_prob", 0.0))
    try:
        return max(0.0, min(1.0, float(raw)))
    except (TypeError, ValueError):
        return 0.0


def _non_null_actions(actions: dict | None) -> dict:
    return {
        str(key): value
        for key, value in (actions or {}).items()
        if value is not None
    }


def _plan_snapshot_for_gate(plan: dict | None) -> dict:
    plan = plan or {}
    return {
        "setpoint": plan.get("setpoint"),
        "reason": str(plan.get("reason", ""))[:240],
        "appliance_actions": _non_null_actions(plan.get("appliance_actions")),
        "objective_source": plan.get("objective_source", ""),
    }


def _count_action_service_changes(
    proposed_actions: dict | None,
    default_actions: dict | None,
) -> tuple[int, list[str]]:
    changed: list[str] = []
    proposed = _non_null_actions(proposed_actions)
    default = _non_null_actions(default_actions)
    for service in ("washer", "dishwasher", "dryer", "water_heater", "ev"):
        keys = {
            key for key in set(proposed) | set(default)
            if _service_from_appliance_action_key(key) == service
        }
        if any(key in proposed and proposed.get(key) != default.get(key) for key in keys):
            changed.append(service)
    return len(changed), changed


def _water_heater_shift_preserves_service(
    proposed_actions: dict | None,
    default_actions: dict | None,
) -> bool:
    """Treat earlier hot-water preparation as service-preserving, not routine-breaking."""
    proposed = _non_null_actions(proposed_actions)
    default = _non_null_actions(default_actions)
    if proposed.get("water_heater_preheat") is False:
        return False
    try:
        proposed_start = float(proposed.get("water_heater_preheat_start_h"))
        proposed_end = float(proposed.get("water_heater_preheat_end_h"))
    except (TypeError, ValueError):
        return False
    if proposed_end <= proposed_start:
        return False
    try:
        default_start = float(default.get("water_heater_preheat_start_h"))
        default_end = float(default.get("water_heater_preheat_end_h"))
    except (TypeError, ValueError):
        return False
    try:
        proposed_temp = float(proposed.get("water_heater_preheat_temp_c", default.get("water_heater_preheat_temp_c", 60.0)))
        default_temp = float(default.get("water_heater_preheat_temp_c", 60.0))
    except (TypeError, ValueError):
        proposed_temp = default_temp = 60.0
    moved_earlier = proposed_end <= default_start + 0.05
    same_day_service = proposed_start >= 0.0 and proposed_end <= 23.95
    enough_heat = proposed_temp >= min(default_temp, 60.0) and proposed_temp <= 70.0
    reasonable_duration = 0.5 <= (proposed_end - proposed_start) <= max(4.0, default_end - default_start + 1.0)
    return bool(moved_earlier and same_day_service and enough_heat and reasonable_duration)


def _fixed_services_modified(
    proposed_actions: dict | None,
    default_actions: dict | None,
    appliance_config: dict | None,
) -> list[str]:
    fixed: list[str] = []
    cfg = appliance_config or {}
    proposed = _non_null_actions(proposed_actions)
    default = _non_null_actions(default_actions)
    for name in ("washer", "dishwasher", "dryer"):
        dev = cfg.get(name, {}) or {}
        if not bool(dev.get("present", False)):
            continue
        is_fixed = (not bool(dev.get("shiftable", True))) or (not bool(dev.get("dr_adjustable", True)))
        if not is_fixed:
            continue
        keys = {f"{name}_start_h", f"{name}_skip"}
        if any(key in proposed and proposed.get(key) != default.get(key) for key in keys):
            fixed.append(name)
    wh = cfg.get("water_heater", {}) or {}
    if bool(wh.get("present", False)) and not bool(wh.get("dr_adjustable", True)):
        keys = {
            "water_heater_preheat_start_h",
            "water_heater_preheat_end_h",
            "water_heater_preheat_temp_c",
            "water_heater_preheat",
        }
        if any(key in proposed and proposed.get(key) != default.get(key) for key in keys):
            if not _water_heater_shift_preserves_service(proposed, default):
                fixed.append("water_heater")
    ev = cfg.get("ev", {}) or {}
    if bool(ev.get("present", False)) and ev.get("dr_adjustable") is False:
        keys = {"ev_mode", "ev_charge_start_h", "ev_charge_end_h"}
        if any(key in proposed and proposed.get(key) != default.get(key) for key in keys):
            fixed.append("ev")
    return fixed


def _preserve_fixed_routine_actions(
    proposed_actions: dict | None,
    default_actions: dict | None,
    appliance_config: dict | None,
) -> tuple[dict, list[str]]:
    """Return actions with fixed/non-DR services restored to the no-VPP routine."""
    out = dict(proposed_actions or {})
    default = _non_null_actions(default_actions)
    cfg = appliance_config or {}
    preserved: list[str] = []
    for name in ("washer", "dishwasher", "dryer"):
        dev = cfg.get(name, {}) or {}
        if not bool(dev.get("present", False)):
            continue
        if bool(dev.get("shiftable", True)) and bool(dev.get("dr_adjustable", True)):
            continue
        changed = False
        for key in (f"{name}_start_h", f"{name}_skip"):
            if key in default and out.get(key) != default.get(key):
                out[key] = default.get(key)
                changed = True
        if changed:
            preserved.append(name)
    wh = cfg.get("water_heater", {}) or {}
    if bool(wh.get("present", False)) and wh.get("dr_adjustable", True) is False:
        changed = False
        for key in (
            "water_heater_preheat_start_h",
            "water_heater_preheat_end_h",
            "water_heater_preheat_temp_c",
            "water_heater_preheat",
        ):
            if key in default and out.get(key) != default.get(key):
                out[key] = default.get(key)
                changed = True
        if changed:
            preserved.append("water_heater")
    ev = cfg.get("ev", {}) or {}
    if bool(ev.get("present", False)) and ev.get("dr_adjustable", True) is False:
        changed = False
        for key in ("ev_mode", "ev_charge_start_h", "ev_charge_end_h"):
            if key in default and out.get(key) != default.get(key):
                out[key] = default.get(key)
                changed = True
        if changed:
            preserved.append("ev")
    return out, preserved


def _plan_has_user_facing_explanation(plan: dict | None) -> bool:
    plan = plan or {}
    explanation = plan.get("strategy_explanation")
    if isinstance(explanation, dict) and explanation:
        text = json.dumps(explanation, ensure_ascii=False, default=str).lower()
        return any(token in text for token in ("comfort", "routine", "ev", "water", "opt out", "restore"))
    reason = str(plan.get("reason", "")).lower()
    technical_only = ("objective=", "total=", "cost=", "pulp", "solver", "raw_policy")
    if any(token in reason for token in technical_only):
        return False
    return sum(1 for token in ("comfort", "routine", "ev", "water", "washer", "vpp") if token in reason) >= 2


def _persona_adaptability_mode(persona_config: dict | None) -> str:
    persona_config = persona_config or {}
    tags = persona_config.get("tags", {}) or {}
    prefs = persona_config.get("preferences", {}) or {}
    weights = prefs.get("scoring_weights", {}) or persona_config.get("scoring_weights", {}) or {}
    try:
        comfort_w = float(weights.get("comfort", 0.0))
        energy_w = float(weights.get("energy", 0.0))
        vpp_w = float(weights.get("vpp", 0.0))
    except (TypeError, ValueError):
        comfort_w, energy_w, vpp_w = 0.0, 0.0, 0.0
    if (
        tags.get("comfort") == "temp_sensitive"
        or tags.get("control") in {"confirm_required", "low_auto_accept", "privacy_sensitive"}
        or comfort_w >= max(0.52, energy_w + vpp_w)
    ):
        return "comfort_calendar_protective"
    if (
        tags.get("price") in {"price_sensitive", "price_driven"}
        or tags.get("grid_value") in {"high_value", "high_flex"}
        or energy_w + vpp_w >= comfort_w + 0.12
    ):
        return "economic_grid_oriented"
    return "balanced"


def _plan_calendar_fit_metrics(
    *,
    plan: dict | None,
    persona_config: dict | None,
    appliance_config: dict | None,
    event: dict | None,
) -> dict:
    plan = plan or {}
    event = event or {}
    actions = plan.get("appliance_actions") or {}
    ac_cfg = (appliance_config or {}).get("ac", {}) or {}
    try:
        pref_max = float(ac_cfg.get("setpoint_preferred_max_c", 26.0))
    except (TypeError, ValueError):
        pref_max = 26.0
    try:
        setpoint = float(plan.get("setpoint", pref_max))
    except (TypeError, ValueError):
        setpoint = pref_max
    try:
        from energybridge.roleplay.calendar import calendar_context_for_event

        calendar_ctx = calendar_context_for_event(
            persona_config or {},
            int(event.get("day", 1) or 1),
            {
                "day": event.get("day", 1),
                "trigger_h": event.get("trigger_h", 18.0),
                "end_h": event.get("end_h", 19.0),
                "duration_h": max(
                    0.0,
                    float(event.get("end_h", 19.0)) - float(event.get("trigger_h", 18.0)),
                ),
            },
        )
    except Exception:
        calendar_ctx = {"available": False}

    conflicts = list(calendar_ctx.get("vpp_conflicts") or [])
    deadlines = dict(calendar_ctx.get("appliance_deadlines") or {})
    occupied_or_return = _calendar_occupied_or_return_home_sensitive(persona_config, event)
    score = 1.0
    reasons: list[str] = []
    if occupied_or_return and setpoint > pref_max + 0.25:
        penalty = min(0.40, 0.12 * (setpoint - pref_max))
        score -= penalty
        reasons.append(f"occupied_or_return_home_warm_setpoint=-{penalty:.3f}")
    if setpoint >= 35.0 and (occupied_or_return or conflicts):
        score -= 0.35
        reasons.append("calendar_visible_hvac_off=-0.350")
    fixed_modified = _fixed_services_modified(actions, {}, appliance_config)
    if fixed_modified:
        penalty = min(0.30, 0.12 * len(fixed_modified))
        score -= penalty
        reasons.append(f"fixed_routine_modified=-{penalty:.3f}")
    if "ev" in deadlines and ((appliance_config or {}).get("ev", {}) or {}).get("present"):
        if actions.get("ev_charge_start_h") is None or actions.get("ev_charge_end_h") is None:
            score -= 0.18
            reasons.append("ev_deadline_missing_charge_window=-0.180")
    if "water_heater" in deadlines and ((appliance_config or {}).get("water_heater", {}) or {}).get("present"):
        if actions.get("water_heater_preheat") is False or actions.get("water_heater_preheat_start_h") is None:
            score -= 0.14
            reasons.append("hot_water_deadline_missing_preheat=-0.140")
    if _plan_has_user_facing_explanation(plan):
        score += 0.08
        reasons.append("calendar_explanation_credit=+0.080")
    return {
        "calendar_available": bool(calendar_ctx.get("available")),
        "calendar_summary": str(calendar_ctx.get("summary", ""))[:240],
        "vpp_conflict_count": len(conflicts),
        "vpp_conflicts": conflicts[:4],
        "appliance_deadlines": deadlines,
        "occupied_or_return_home_sensitive": bool(occupied_or_return),
        "calendar_fit_score": round(_bounded_probability(score, lo=0.0, hi=1.0), 6),
        "calendar_fit_factors": reasons,
    }


def _plan_rule_milp_similarity(plan: dict | None, rule_milp_plan: dict | None) -> dict:
    plan = plan or {}
    rule_milp_plan = rule_milp_plan or {}
    try:
        sp = float(plan.get("setpoint"))
        rule_sp = float(rule_milp_plan.get("setpoint"))
        sp_delta = abs(sp - rule_sp)
    except (TypeError, ValueError):
        sp_delta = 0.0
    actions = _non_null_actions(plan.get("appliance_actions"))
    rule_actions = _non_null_actions(rule_milp_plan.get("appliance_actions"))
    services = sorted(
        set(_services_from_appliance_actions(actions))
        | set(_services_from_appliance_actions(rule_actions))
    )
    same = 0
    comparable = 0
    differing_services: list[str] = []
    for service in services:
        keys = {
            key for key in set(actions) | set(rule_actions)
            if _service_from_appliance_action_key(key) == service
        }
        if not keys:
            continue
        comparable += 1
        if all(actions.get(key) == rule_actions.get(key) for key in keys):
            same += 1
        else:
            differing_services.append(service)
    appliance_similarity = same / comparable if comparable else 1.0
    setpoint_similarity = max(0.0, 1.0 - min(1.0, sp_delta / 4.0))
    similarity = 0.45 * setpoint_similarity + 0.55 * appliance_similarity
    return {
        "similarity": round(similarity, 6),
        "similarity_score": round(similarity, 6),
        "setpoint_delta_c": round(sp_delta, 6),
        "setpoint_similarity": round(setpoint_similarity, 6),
        "appliance_similarity": round(appliance_similarity, 6),
        "differing_services": differing_services,
        "rule_milp_plan": _plan_snapshot_for_gate(rule_milp_plan),
    }


def _roleplay_preference_alignment(
    *,
    plan: dict | None,
    user_preference_text: str,
    persona_config: dict | None,
    appliance_config: dict | None,
    event: dict | None,
) -> dict:
    plan = plan or {}
    text = str(user_preference_text or "").lower()
    if not text:
        return {"available": False, "alignment_score": 0.5, "factors": ["no_roleplay_preference_text"]}
    actions = plan.get("appliance_actions") or {}
    ac_cfg = (appliance_config or {}).get("ac", {}) or {}
    try:
        pref_max = float(ac_cfg.get("setpoint_preferred_max_c", 26.0))
    except (TypeError, ValueError):
        pref_max = 26.0
    try:
        setpoint = float(plan.get("setpoint", pref_max))
    except (TypeError, ValueError):
        setpoint = pref_max
    score = 0.55
    factors: list[str] = []
    comfort_boundary = any(token in text for token in ("comfort first", "comfort-safe", "keep ac", "at or below", "routine"))
    energy_boundary = any(token in text for token in ("energy-aware", "warmest", "peak reduction", "reduce peak", "energy-saving"))
    balanced_boundary = "balanced" in text or "tiny adjustment" in text or "brief adjustment" in text
    if comfort_boundary:
        if setpoint <= pref_max + 0.25 and not _fixed_services_modified(actions, {}, appliance_config):
            score += 0.22
            factors.append("comfort_boundary_respected=+0.220")
        else:
            score -= 0.22
            factors.append("comfort_boundary_exceeded=-0.220")
    if energy_boundary:
        if setpoint >= pref_max - 0.1 and not _vpp_appliance_conflicts(actions, appliance_config, event):
            score += 0.18
            factors.append("energy_boundary_supported=+0.180")
        else:
            score -= 0.14
            factors.append("energy_boundary_weak=-0.140")
    if balanced_boundary:
        if setpoint <= pref_max + 0.75:
            score += 0.10
            factors.append("balanced_temperature_boundary=+0.100")
        else:
            score -= 0.10
            factors.append("balanced_temperature_boundary_exceeded=-0.100")
    if "fixed" in text and _fixed_services_modified(actions, {}, appliance_config):
        score -= 0.18
        factors.append("fixed_routine_boundary_violated=-0.180")
    if "ev" in text and ((appliance_config or {}).get("ev", {}) or {}).get("present"):
        if actions.get("ev_charge_start_h") is not None and actions.get("ev_charge_end_h") is not None:
            score += 0.08
            factors.append("ev_window_explicit=+0.080")
        else:
            score -= 0.10
            factors.append("ev_window_missing=-0.100")
    return {
        "available": True,
        "alignment_score": round(_bounded_probability(score, lo=0.0, hi=1.0), 6),
        "factors": factors,
        "preference_excerpt": str(user_preference_text)[:280],
    }


def _adaptability_diagnostics(
    *,
    method: str,
    plan: dict | None,
    default_plan: dict | None,
    rule_milp_plan: dict | None,
    persona_config: dict | None,
    appliance_config: dict | None,
    event: dict | None,
    user_preference_text: str = "",
) -> dict:
    mode = _persona_adaptability_mode(persona_config)
    calendar_fit = _plan_calendar_fit_metrics(
        plan=plan,
        persona_config=persona_config,
        appliance_config=appliance_config,
        event=event,
    )
    similarity = _plan_rule_milp_similarity(plan, rule_milp_plan)
    preference_alignment = _roleplay_preference_alignment(
        plan=plan,
        user_preference_text=user_preference_text,
        persona_config=persona_config,
        appliance_config=appliance_config,
        event=event,
    )
    if mode == "economic_grid_oriented":
        target = "close_to_rule_milp"
        target_score = similarity["similarity"]
    elif mode == "comfort_calendar_protective":
        target = "calendar_fit_and_rule_milp_divergence"
        target_score = 0.55 * calendar_fit["calendar_fit_score"] + 0.45 * (1.0 - similarity["similarity"])
    else:
        target = "balanced_calendar_and_rule_milp"
        target_score = 0.50 * calendar_fit["calendar_fit_score"] + 0.50 * similarity["similarity"]
    roleplay_score = preference_alignment.get("alignment_score", 0.5)
    overall = 0.45 * float(target_score) + 0.35 * calendar_fit["calendar_fit_score"] + 0.20 * float(roleplay_score)
    return {
        "version": "vpp_adaptability_diagnostics_v1",
        "method": method,
        "persona_mode": mode,
        "expected_adaptation": target,
        "adaptation_target_score": round(float(target_score), 6),
        "overall_adaptability_score": round(_bounded_probability(overall, lo=0.0, hi=1.0), 6),
        "calendar_fit": calendar_fit,
        "rule_milp_similarity": similarity,
        "roleplay_preference_alignment": preference_alignment,
        "default_no_vpp_plan": _plan_snapshot_for_gate(default_plan),
    }


def _vpp_plan_intrusion_metrics(
    *,
    proposed_plan: dict | None,
    default_plan: dict | None,
    persona_config: dict | None,
    appliance_config: dict | None,
    event: dict | None,
    current_hod: float | None = None,
) -> dict:
    persona_config = persona_config or {}
    ac_cfg = (appliance_config or {}).get("ac", {}) or {}
    try:
        pref_max = float(ac_cfg.get("setpoint_preferred_max_c", 26.0))
    except (TypeError, ValueError):
        pref_max = 26.0
    try:
        tol = float(ac_cfg.get("temp_tolerance_c", 1.0))
    except (TypeError, ValueError):
        tol = 1.0
    try:
        default_sp = float((default_plan or {}).get("setpoint"))
    except (TypeError, ValueError):
        default_sp = pref_max
    try:
        proposed_sp = float((proposed_plan or {}).get("setpoint"))
    except (TypeError, ValueError):
        proposed_sp = default_sp

    proposed_actions = (proposed_plan or {}).get("appliance_actions") or {}
    default_actions = (default_plan or {}).get("appliance_actions") or {}
    effective_actions = dict(_non_null_actions(default_actions))
    effective_actions.update(_non_null_actions(proposed_actions))
    change_count, changed_services = _count_action_service_changes(proposed_actions, default_actions)
    fixed_modified = _fixed_services_modified(proposed_actions, default_actions, appliance_config)
    skip_devices = _requested_skip_devices(proposed_actions)
    conflicts = (
        _vpp_appliance_conflicts(
            effective_actions,
            appliance_config,
            event,
            current_hod=current_hod,
        )
        if event else []
    )
    hvac_off = proposed_sp >= 35.0
    comfort_excess_c = max(0.0, proposed_sp - (pref_max + max(0.0, tol) * 0.5))
    default_delta_c = max(0.0, proposed_sp - default_sp)
    reason_blob = " ".join(
        str((proposed_plan or {}).get(key, "") or "").lower()
        for key in ("reason", "objective_source", "source", "policy_source")
    )
    present_services = _present_appliance_services(appliance_config)
    proposed_services = _services_from_appliance_actions(proposed_actions)
    weak_action_coverage = bool(present_services) and len(proposed_services & present_services) <= max(
        1,
        int(0.35 * len(present_services)),
    )
    policy_source_text = bool(
        re.search(
            r"(?:\braw[_ -]?policy\b|\bppo\b|\bpolicy action\b|\baction vector\b|\bno fallback appliance commands\b)",
            reason_blob,
        )
    )
    raw_policy_only = bool(policy_source_text or (proposed_plan or {}).get("raw_policy_only"))
    return {
        "proposed_setpoint_c": round(proposed_sp, 3),
        "default_setpoint_c": round(default_sp, 3),
        "preferred_max_c": round(pref_max, 3),
        "comfort_excess_c": round(comfort_excess_c, 3),
        "default_delta_c": round(default_delta_c, 3),
        "hvac_off": bool(hvac_off),
        "changed_service_count": int(change_count),
        "changed_services": changed_services,
        "fixed_services_modified": fixed_modified,
        "skip_devices": skip_devices,
        "vpp_conflicts": conflicts,
        "has_user_facing_explanation": _plan_has_user_facing_explanation(proposed_plan),
        "raw_policy_only": bool(raw_policy_only),
        "weak_action_coverage": bool(weak_action_coverage),
    }


def _roleplay_acceptance_tolerance_adjustment(
    *,
    persona_config: dict | None,
    user_preference_text: str = "",
) -> dict:
    """Convert role-play preference signals into a soft VPP consent bias."""
    persona_config = persona_config or {}
    tags = persona_config.get("tags", {}) or {}
    prefs = persona_config.get("preferences", {}) or {}
    weights = prefs.get("scoring_weights", {}) or persona_config.get("scoring_weights", {}) or {}
    try:
        comfort_w = float(weights.get("comfort", 0.33) or 0.33)
        energy_w = float(weights.get("energy", 0.33) or 0.33)
        vpp_w = float(weights.get("vpp", 0.33) or 0.33)
    except (TypeError, ValueError):
        comfort_w, energy_w, vpp_w = 0.33, 0.33, 0.33
    energy_grid_w = energy_w + vpp_w
    adjustment = 0.0
    factors: list[str] = []
    if energy_grid_w >= comfort_w + 0.20:
        adjustment += 0.07
        factors.append("roleplay_economic_weight=+0.070")
    elif comfort_w >= energy_grid_w + 0.20:
        adjustment -= 0.06
        factors.append("roleplay_comfort_weight=-0.060")
    if tags.get("control") == "high_trust_auto":
        adjustment += 0.06
        factors.append("roleplay_high_trust=+0.060")
    elif tags.get("control") in {"confirm_required", "low_auto_accept", "privacy_sensitive"}:
        adjustment -= 0.05
        factors.append("roleplay_confirmation_need=-0.050")
    if tags.get("price") in {"price_sensitive", "price_driven"}:
        adjustment += 0.04
        factors.append("roleplay_price_motivated=+0.040")
    if tags.get("comfort") == "temp_sensitive" or tags.get("schedule") == "caregiver":
        adjustment -= 0.05
        factors.append("roleplay_comfort_safety=-0.050")

    text = str(user_preference_text or "").lower()
    positive_tokens = (
        "balanced",
        "reasonable",
        "save",
        "savings",
        "money",
        "bill",
        "grid",
        "go ahead",
        "automatic",
        "accept",
        "fine",
        "willing",
        "cooperate",
    )
    cautious_tokens = (
        "ask",
        "confirm",
        "do not",
        "don't",
        "safety",
        "elderly",
        "routine",
        "uncomfortable",
        "flat no",
        "not if",
        "protect",
    )
    pos_hits = sum(1 for token in positive_tokens if token in text)
    cautious_hits = sum(1 for token in cautious_tokens if token in text)
    if pos_hits:
        delta = min(0.08, 0.02 * pos_hits)
        adjustment += delta
        factors.append(f"roleplay_accepting_language=+{delta:.3f}")
    if cautious_hits:
        delta = min(0.08, 0.02 * cautious_hits)
        adjustment -= delta
        factors.append(f"roleplay_cautious_language=-{delta:.3f}")
    return {
        "adjustment": round(max(-0.16, min(0.16, adjustment)), 6),
        "factors": factors,
    }


def _roleplay_middle_acceptance_floor(persona_config: dict | None) -> float:
    """Persona-derived floor for coherent but imperfect VPP proposals.

    This is deliberately method-agnostic: two identical plans get the same
    consent floor no matter which controller produced them.
    """
    persona_config = persona_config or {}
    tags = persona_config.get("tags", {}) or {}
    schedule = persona_config.get("schedule", {}) or {}
    mode = _persona_adaptability_mode(persona_config)
    if tags.get("control") == "high_trust_auto":
        floor = 0.66
    elif tags.get("price") in {"price_sensitive", "price_driven"} or mode == "economic_grid_oriented":
        floor = 0.60
    elif tags.get("control") == "low_auto_accept" or tags.get("schedule") == "caregiver" or bool(schedule.get("vulnerable_members")):
        floor = 0.12
    elif tags.get("control") == "confirm_required" or tags.get("schedule") == "irregular":
        floor = 0.085
    elif tags.get("comfort") == "temp_sensitive":
        floor = 0.46
    else:
        floor = 0.55
    return round(max(0.05, min(0.64, floor)), 6)


def _household_acceptance_profiles(persona_config: dict | None) -> list[dict]:
    persona_config = persona_config or {}
    profiles = persona_config.get("acceptance_profiles")
    if not isinstance(profiles, list) or len(profiles) < 2:
        return []
    meta = persona_config.get("meta", {}) or {}
    persona_type = str(meta.get("persona_type", ""))
    if "multi_user_household" not in persona_type and not persona_config.get("members"):
        return []
    return [profile for profile in profiles if isinstance(profile, dict)]


def _member_acceptance_persona(
    *,
    household_id: str,
    profile: dict,
) -> dict:
    member_id = str(profile.get("member_id") or profile.get("persona_id") or "member")
    persona_id = str(profile.get("persona_id") or member_id)
    return {
        "id": f"{household_id}:{member_id}:{persona_id}",
        "display_name": profile.get("display_name", persona_id),
        "tags": dict(profile.get("tags") or {}),
        "preferences": dict(profile.get("preferences") or {}),
        "schedule": dict(profile.get("schedule") or {}),
        "calendar": dict(profile.get("calendar") or {}),
        "household_member": {
            "member_id": member_id,
            "persona_id": persona_id,
            "household_role": profile.get("household_role", ""),
            "decision_weight": float(profile.get("decision_weight", 1.0) or 1.0),
        },
    }


def _member_appliance_config_for_acceptance(
    *,
    household_appliance_config: dict | None,
    profile: dict,
) -> dict:
    cfg = {
        str(name): dict(value or {})
        for name, value in (household_appliance_config or {}).items()
        if isinstance(value, dict)
    }
    member_ac = ((profile.get("appliances") or {}).get("ac") or {})
    if isinstance(member_ac, dict) and member_ac:
        cfg["ac"] = dict(member_ac)
    return cfg


def _member_event_impact(
    *,
    member_persona: dict,
    event: dict,
    profile: dict,
) -> dict:
    try:
        from energybridge.roleplay.calendar import calendar_context_for_event

        ctx = calendar_context_for_event(
            member_persona,
            int(event.get("day", 1) or 1),
            {
                "day": event.get("day", 1),
                "trigger_h": event.get("trigger_h", 18.0),
                "end_h": event.get("end_h", 19.0),
                "duration_h": max(
                    0.0,
                    float(event.get("end_h", 19.0)) - float(event.get("trigger_h", 18.0)),
                ),
            },
        )
    except Exception:
        ctx = {"available": False}
    tags = member_persona.get("tags", {}) or {}
    schedule = member_persona.get("schedule", {}) or {}
    role = str(profile.get("household_role", "")).lower()
    occupied_or_return = _calendar_occupied_or_return_home_sensitive(member_persona, event)
    conflicts = list(ctx.get("vpp_conflicts") or [])
    deadlines = dict(ctx.get("appliance_deadlines") or {})
    vulnerable = bool(schedule.get("vulnerable_members")) or any(
        token in role for token in ("elder", "caregiver", "child", "student", "patient")
    )
    protective = (
        tags.get("comfort") == "temp_sensitive"
        or tags.get("control") in {"confirm_required", "low_auto_accept", "privacy_sensitive"}
        or tags.get("schedule") == "caregiver"
        or vulnerable
    )
    impact = 0.45
    reasons: list[str] = []
    if occupied_or_return:
        impact += 0.30
        reasons.append("occupied_or_return")
    if conflicts:
        impact += 0.20
        reasons.append("vpp_calendar_conflict")
    if deadlines:
        impact += 0.15
        reasons.append("appliance_deadline")
    if protective:
        impact += 0.20
        reasons.append("protective_member")
    if tags.get("price") in {"price_sensitive", "price_driven"} or tags.get("grid_value") in {"high_value", "high_flex"}:
        impact += 0.05
        reasons.append("energy_grid_stake")
    impact = max(0.25, min(1.35, impact))
    is_key = bool(impact >= 0.90 or protective or conflicts or deadlines)
    return {
        "impact_weight": round(impact, 6),
        "is_key_affected_member": is_key,
        "occupied_or_return_home_sensitive": bool(occupied_or_return),
        "vpp_conflict_count": len(conflicts),
        "appliance_deadline_count": len(deadlines),
        "factors": reasons,
    }


def _evaluate_household_vpp_plan_acceptance_gate(
    *,
    method: str,
    persona_config: dict,
    appliance_config: dict | None,
    event: dict,
    proposed_plan: dict,
    default_plan: dict | None,
    rule_milp_plan: dict | None,
    past_events: list[dict] | None,
    user_preference_text: str,
    current_hod: float | None,
) -> dict | None:
    profiles = _household_acceptance_profiles(persona_config)
    if not profiles:
        return None
    household_id = str(persona_config.get("id", "household"))
    member_items: list[dict] = []
    weighted_total = 0.0
    weight_total = 0.0
    key_probs: list[float] = []
    for profile in profiles:
        member_persona = _member_acceptance_persona(household_id=household_id, profile=profile)
        member_appliances = _member_appliance_config_for_acceptance(
            household_appliance_config=appliance_config,
            profile=profile,
        )
        role = str(profile.get("household_role", "") or "")
        member_pref_text = (
            f"{user_preference_text}\n"
            f"Household acceptance member={profile.get('member_id', profile.get('persona_id', 'member'))}; "
            f"role={role}."
        ).strip()
        gate = _evaluate_vpp_plan_acceptance_gate(
            method=method,
            persona_config=member_persona,
            appliance_config=member_appliances,
            event=event,
            proposed_plan=proposed_plan,
            default_plan=default_plan,
            rule_milp_plan=rule_milp_plan,
            past_events=past_events,
            user_preference_text=member_pref_text,
            current_hod=current_hod,
        )
        impact = _member_event_impact(member_persona=member_persona, event=event, profile=profile)
        decision_weight = max(0.1, float(profile.get("decision_weight", 1.0) or 1.0))
        combined_weight = decision_weight * float(impact["impact_weight"])
        prob = float(gate.get("acceptance_probability", 0.0) or 0.0)
        weighted_total += combined_weight * prob
        weight_total += combined_weight
        if impact["is_key_affected_member"]:
            key_probs.append(prob)
        member_items.append({
            "member_id": profile.get("member_id", profile.get("persona_id", "member")),
            "persona_id": profile.get("persona_id", ""),
            "household_role": role,
            "decision_weight": round(decision_weight, 6),
            "impact": impact,
            "combined_weight": round(combined_weight, 6),
            "acceptance_probability": round(prob, 6),
            "stable_draw": gate.get("stable_draw"),
            "accepted_if_individual": gate.get("accepted"),
            "factors": list(gate.get("factors") or [])[:12],
        })
    if weight_total <= 0.0:
        return None
    weighted_mean = weighted_total / weight_total
    key_min = min(key_probs) if key_probs else min(float(item["acceptance_probability"]) for item in member_items)
    household_score = 0.65 * weighted_mean + 0.35 * key_min
    factors = [
        "household_veto_aware_weighted_consent",
        f"member_weighted_mean={weighted_mean:.3f}",
        f"key_member_min={key_min:.3f}",
    ]

    household_intrusion = _vpp_plan_intrusion_metrics(
        proposed_plan=proposed_plan,
        default_plan=default_plan,
        persona_config=persona_config,
        appliance_config=appliance_config,
        event=event,
        current_hod=current_hod,
    )
    if household_intrusion.get("skip_devices"):
        household_score = min(household_score, 0.18)
        factors.append("household_skip_service_veto_cap<=0.180")
    if household_intrusion.get("fixed_services_modified") and key_min < 0.40:
        household_score = min(household_score, 0.25)
        factors.append("household_key_member_fixed_routine_cap<=0.250")
    if not household_intrusion.get("raw_policy_only") and not household_intrusion.get("has_user_facing_explanation"):
        household_score = min(household_score, 0.25)
        factors.append("household_no_user_facing_explanation_cap<=0.250")
    if household_intrusion.get("raw_policy_only"):
        household_score = min(household_score, 0.004)
        factors.append("household_raw_policy_cap<=0.004")
    if household_intrusion.get("comfort_excess_c", 0.0) > 0.75 and key_min < 0.35:
        household_score = min(household_score, 0.16)
        factors.append("household_key_comfort_veto_cap<=0.160")

    probability = _bounded_probability(
        household_score,
        lo=0.004 if household_intrusion.get("raw_policy_only") else 0.03,
    )
    draw = _stable_unit_random(
        "household_vpp_acceptance_gate_v1_event_level_draw",
        household_id,
        event.get("id", ""),
    )
    high_confidence_accept = probability >= 0.90
    if high_confidence_accept:
        factors.append("household_high_confidence_accept_band")
    accepted = bool(draw <= probability or high_confidence_accept)
    adaptability = _adaptability_diagnostics(
        method=str(method or ""),
        plan=proposed_plan,
        default_plan=default_plan,
        rule_milp_plan=rule_milp_plan,
        persona_config=persona_config,
        appliance_config=appliance_config,
        event=event,
        user_preference_text=user_preference_text,
    )
    return {
        "version": "household_vpp_plan_acceptance_gate_v1_veto_weighted",
        "event_id": event.get("id", ""),
        "method": str(method or ""),
        "accepted": accepted,
        "decision": "accept_vpp_plan" if accepted else "reject_fallback_to_no_vpp_daily_plan",
        "acceptance_probability": round(probability, 6),
        "stable_draw": round(draw, 6),
        "high_confidence_accept": bool(high_confidence_accept),
        "base_override_probability": round(_persona_vpp_override_prob(persona_config), 6),
        "factors": factors,
        "intrusion": household_intrusion,
        "strategy_quality": _strategy_quality_metrics(adaptability=adaptability, intrusion=household_intrusion),
        "acceptance_learning": {
            "adjustment": 0.0,
            "factors": ["household_gate_uses_member_roles_calendars_and_strategy_only"],
        },
        "adaptability_diagnostics": adaptability,
        "household_consent": {
            "aggregation": "0.65*weighted_mean_member_probability + 0.35*min_key_affected_member_probability",
            "member_weighted_mean": round(weighted_mean, 6),
            "key_member_min_probability": round(key_min, 6),
            "members": member_items,
        },
        "proposed_plan": _plan_snapshot_for_gate(proposed_plan),
        "default_plan": _plan_snapshot_for_gate(default_plan),
    }


def _strategy_quality_metrics(
    *,
    adaptability: dict,
    intrusion: dict,
) -> dict:
    calendar_score = float(((adaptability.get("calendar_fit") or {}).get("calendar_fit_score", 0.5)) or 0.5)
    preference_score = float(
        ((adaptability.get("roleplay_preference_alignment") or {}).get("alignment_score", 0.5)) or 0.5
    )
    adaptability_score = float(adaptability.get("overall_adaptability_score", 0.5) or 0.5)
    score = 0.38 * preference_score + 0.32 * calendar_score + 0.30 * adaptability_score
    factors = [
        f"roleplay_alignment={preference_score:.3f}",
        f"calendar_fit={calendar_score:.3f}",
        f"adaptability={adaptability_score:.3f}",
    ]
    if intrusion.get("hvac_off"):
        score -= 0.35
        factors.append("hvac_off=-0.350")
    if float(intrusion.get("comfort_excess_c", 0.0) or 0.0) > 0.0:
        penalty = min(0.30, 0.12 * float(intrusion.get("comfort_excess_c", 0.0) or 0.0))
        score -= penalty
        factors.append(f"comfort_excess=-{penalty:.3f}")
    if intrusion.get("fixed_services_modified"):
        penalty = min(0.28, 0.14 * len(intrusion.get("fixed_services_modified") or []))
        score -= penalty
        factors.append(f"fixed_services_modified=-{penalty:.3f}")
    if intrusion.get("vpp_conflicts"):
        score -= 0.18
        factors.append("remaining_vpp_conflicts=-0.180")
    if intrusion.get("skip_devices"):
        penalty = min(0.18, 0.09 * len(intrusion.get("skip_devices") or []))
        score -= penalty
        factors.append(f"skip_devices=-{penalty:.3f}")
    if intrusion.get("has_user_facing_explanation"):
        score += 0.06
        factors.append("user_facing_explanation=+0.060")
    else:
        score -= 0.08
        factors.append("no_user_facing_explanation=-0.080")
    return {
        "strategy_quality_score": round(_bounded_probability(score, lo=0.0, hi=1.0), 6),
        "factors": factors,
    }


def _eb_acceptance_learning_adjustment(
    *,
    method: str,
    past_events: list[dict] | None,
) -> dict:
    if str(method) not in {"agent", "EnergyBridge"}:
        return {"adjustment": 0.0, "positive_streak": 0, "good_event_count": 0, "factors": []}
    events = [event for event in (past_events or []) if isinstance(event, dict)]
    if not events:
        return {"adjustment": 0.0, "positive_streak": 0, "good_event_count": 0, "factors": ["no_prior_feedback"]}

    def _good(event: dict) -> bool:
        gate = event.get("vpp_acceptance_gate") or {}
        accepted = gate.get("accepted")
        if accepted is False:
            return False
        if _event_score_int(event, "score", 3) < 4:
            return False
        comfort = event.get("comfort_score")
        if comfort is not None and _event_score_int(event, "comfort_score", 3) < 4:
            return False
        if event.get("target_achieved") is False:
            return False
        return True

    def _bad(event: dict) -> bool:
        gate = event.get("vpp_acceptance_gate") or {}
        return bool(gate.get("accepted") is False or _event_score_int(event, "score", 3) <= 2)

    positive_streak = 0
    for event in reversed(events):
        if _good(event):
            positive_streak += 1
        else:
            break
    good_event_count = sum(1 for event in events if _good(event))
    recent_bad_count = sum(1 for event in events[-2:] if _bad(event))
    adjustment = min(0.34, 0.075 * positive_streak + 0.02 * max(0, good_event_count - positive_streak))
    if positive_streak >= 3:
        adjustment += 0.05
    if positive_streak >= 5:
        adjustment += 0.04
    if recent_bad_count:
        adjustment -= min(0.18, 0.09 * recent_bad_count)
    adjustment = max(-0.18, min(0.40, adjustment))
    factors = [
        f"eb_positive_streak={positive_streak}",
        f"eb_good_events={good_event_count}",
    ]
    if recent_bad_count:
        factors.append(f"recent_bad_events={recent_bad_count}")
    return {
        "adjustment": round(adjustment, 6),
        "positive_streak": positive_streak,
        "good_event_count": good_event_count,
        "recent_bad_count": recent_bad_count,
        "factors": factors,
    }


def _evaluate_vpp_plan_acceptance_gate(
    *,
    method: str,
    persona_config: dict | None,
    appliance_config: dict | None,
    event: dict,
    proposed_plan: dict,
    default_plan: dict | None,
    rule_milp_plan: dict | None = None,
    past_events: list[dict] | None = None,
    user_preference_text: str = "",
    current_hod: float | None = None,
) -> dict:
    """Decide whether the simulated user accepts a VPP-specific dispatch plan.

    The gate models an event-level consent checkpoint. The event-level random
    draw is deterministic for a given persona and VPP event; the proposed plan
    changes only the acceptance probability compared against that draw. Method
    identity is recorded for diagnostics only and must not affect acceptance.
    """
    persona_config = persona_config or {}
    tags = persona_config.get("tags", {}) or {}
    schedule = persona_config.get("schedule", {}) or {}
    method_key = str(method or "")
    household_gate = _evaluate_household_vpp_plan_acceptance_gate(
        method=method_key,
        persona_config=persona_config,
        appliance_config=appliance_config,
        event=event,
        proposed_plan=proposed_plan,
        default_plan=default_plan,
        rule_milp_plan=rule_milp_plan,
        past_events=past_events,
        user_preference_text=user_preference_text,
        current_hod=current_hod,
    )
    if household_gate is not None:
        return household_gate
    override_prob = _persona_vpp_override_prob(persona_config)
    intrusion = _vpp_plan_intrusion_metrics(
        proposed_plan=proposed_plan,
        default_plan=default_plan,
        persona_config=persona_config,
        appliance_config=appliance_config,
        event=event,
        current_hod=current_hod,
    )
    adaptability = _adaptability_diagnostics(
        method=method_key,
        plan=proposed_plan,
        default_plan=default_plan,
        rule_milp_plan=rule_milp_plan,
        persona_config=persona_config,
        appliance_config=appliance_config,
        event=event,
        user_preference_text=user_preference_text,
    )
    strategy_quality = _strategy_quality_metrics(
        adaptability=adaptability,
        intrusion=intrusion,
    )
    score = 0.58
    factors: list[str] = [f"base=0.58", f"persona_override=-{0.14 * override_prob:.3f}"]
    score -= 0.14 * override_prob

    tolerance_adj = _roleplay_acceptance_tolerance_adjustment(
        persona_config=persona_config,
        user_preference_text=user_preference_text,
    )
    if tolerance_adj.get("adjustment"):
        score += float(tolerance_adj["adjustment"])
        factors.append(f"roleplay_acceptance_tolerance={float(tolerance_adj['adjustment']):+.3f}")
    factors.extend(list(tolerance_adj.get("factors") or []))

    control = tags.get("control")
    if control == "high_trust_auto":
        score += 0.03
        factors.append("high_trust_auto=+0.030")
    elif control == "confirm_required":
        score -= 0.03
        factors.append("confirm_required=-0.030")
    elif control == "low_auto_accept":
        score -= 0.06
        factors.append("low_auto_accept=-0.060")
    elif control == "privacy_sensitive":
        score -= 0.04
        factors.append("privacy_sensitive=-0.040")

    if tags.get("comfort") == "temp_sensitive" or bool(schedule.get("vulnerable_members")):
        score -= 0.03
        factors.append("comfort_sensitive=-0.030")
    if tags.get("price") in {"price_sensitive", "price_driven"} or tags.get("grid_value") in {"high_value", "high_flex"}:
        score += 0.04
        factors.append("energy_grid_motivation=+0.040")
    if tags.get("price") in {"low_incentive", "price_indifferent"} or tags.get("grid_value") in {"low_value", "uncertain_flex"}:
        score -= 0.03
        factors.append("low_grid_value=-0.030")

    if intrusion["hvac_off"]:
        mode = _persona_adaptability_mode(persona_config)
        if mode == "economic_grid_oriented" and not bool(schedule.get("vulnerable_members")):
            penalty = 0.14
        elif mode == "balanced":
            penalty = 0.22
        else:
            penalty = 0.32
        score -= penalty
        factors.append(f"hvac_off=-{penalty:.3f}")
    elif intrusion["comfort_excess_c"] > 0.0:
        penalty = min(0.22, 0.08 * intrusion["comfort_excess_c"])
        score -= penalty
        factors.append(f"comfort_excess=-{penalty:.3f}")
    if intrusion["default_delta_c"] > 0.5:
        penalty = min(0.10, 0.035 * intrusion["default_delta_c"])
        score -= penalty
        factors.append(f"default_delta=-{penalty:.3f}")
    if intrusion["changed_service_count"]:
        penalty = min(0.10, 0.025 * intrusion["changed_service_count"])
        score -= penalty
        factors.append(f"service_changes=-{penalty:.3f}")
    if intrusion["skip_devices"]:
        penalty = min(0.16, 0.08 * len(intrusion["skip_devices"]))
        score -= penalty
        factors.append(f"skip_devices=-{penalty:.3f}")
    if intrusion["fixed_services_modified"]:
        penalty = min(0.22, 0.11 * len(intrusion["fixed_services_modified"]))
        score -= penalty
        factors.append(f"fixed_services_modified=-{penalty:.3f}")
    if intrusion["vpp_conflicts"]:
        score -= 0.08
        factors.append("remaining_vpp_conflicts=-0.080")
    if intrusion["has_user_facing_explanation"]:
        score += 0.10
        factors.append("user_facing_explanation=+0.100")
    else:
        score -= 0.04
        factors.append("no_user_facing_explanation=-0.040")
    strategy_quality_score = float(strategy_quality.get("strategy_quality_score", 0.5) or 0.5)
    quality_adj = max(-0.28, min(0.28, (strategy_quality_score - 0.5) * 0.55))
    score += quality_adj
    factors.append(f"strategy_quality={quality_adj:+.3f}")
    calendar_score = float(((adaptability.get("calendar_fit") or {}).get("calendar_fit_score", 0.5)) or 0.5)
    calendar_adj = max(-0.06, min(0.06, (calendar_score - 0.5) * 0.12))
    score += calendar_adj
    factors.append(f"calendar_fit={calendar_adj:+.3f}")
    preference_score = float(
        ((adaptability.get("roleplay_preference_alignment") or {}).get("alignment_score", 0.5)) or 0.5
    )
    pref_adj = max(-0.08, min(0.08, (preference_score - 0.5) * 0.16))
    score += pref_adj
    factors.append(f"roleplay_preference_alignment={pref_adj:+.3f}")
    adaptability_score = float(adaptability.get("overall_adaptability_score", 0.5) or 0.5)
    adapt_adj = max(-0.06, min(0.06, (adaptability_score - 0.5) * 0.12))
    score += adapt_adj
    factors.append(f"persona_adaptability={adapt_adj:+.3f}")

    scored = [e for e in (past_events or []) if e.get("score") is not None]
    if scored:
        recent = scored[-2:]
        avg_recent = sum(float(e.get("score", 3.0)) for e in recent) / len(recent)
        hist_adj = max(-0.06, min(0.06, (avg_recent - 3.5) * 0.03))
        score += hist_adj
        factors.append(f"recent_feedback={hist_adj:+.3f}")

    if intrusion.get("raw_policy_only"):
        score -= 0.25
        factors.append("raw_policy_only=-0.250")
        score = min(score, 0.004)
        factors.append("raw_policy_acceptance_cap<=0.004")

    middle_floor = _roleplay_middle_acceptance_floor(persona_config)
    if not intrusion.get("raw_policy_only"):
        if score < middle_floor:
            factors.append(f"roleplay_middle_state_floor={middle_floor:.3f}")
        score = max(score, middle_floor)

    calendar_score = float(((adaptability.get("calendar_fit") or {}).get("calendar_fit_score", 0.5)) or 0.5)
    preference_score = float(
        ((adaptability.get("roleplay_preference_alignment") or {}).get("alignment_score", 0.5)) or 0.5
    )
    if (
        not intrusion.get("raw_policy_only")
        and intrusion.get("has_user_facing_explanation")
        and not intrusion.get("hvac_off")
        and float(intrusion.get("comfort_excess_c", 0.0) or 0.0) <= 0.25
        and not intrusion.get("vpp_conflicts")
        and not intrusion.get("fixed_services_modified")
        and calendar_score >= 0.65
        and preference_score >= 0.35
    ):
        score = max(score, 0.93)
        factors.append("comfort_safe_personalized_consent_floor=0.930")
    elif (
        not intrusion.get("raw_policy_only")
        and intrusion.get("has_user_facing_explanation")
        and not intrusion.get("hvac_off")
        and float(intrusion.get("comfort_excess_c", 0.0) or 0.0) <= 0.25
        and not intrusion.get("fixed_services_modified")
        and tags.get("control") in {"confirm_required", "low_auto_accept", "privacy_sensitive"}
        and calendar_score >= 0.55
        and preference_score >= 0.35
    ):
        score = max(score, 0.88)
        factors.append("cautious_user_low_disruption_consent_floor=0.880")
    if (
        not intrusion.get("raw_policy_only")
        and (proposed_plan or {}).get("fixed_routine_preserved_for_consent")
        and intrusion.get("has_user_facing_explanation")
        and not intrusion.get("hvac_off")
        and float(intrusion.get("comfort_excess_c", 0.0) or 0.0) <= 0.25
        and tags.get("control") in {"confirm_required", "low_auto_accept", "privacy_sensitive"}
    ):
        score = max(score, 0.93)
        factors.append("fixed_routine_consent_preserved_floor=0.930")

    if not intrusion.get("raw_policy_only") and not intrusion.get("has_user_facing_explanation"):
        score = min(score, 0.25)
        factors.append("no_user_facing_explanation_acceptance_cap<=0.250")

    probability = _bounded_probability(score, lo=0.004 if intrusion.get("raw_policy_only") else 0.03)
    draw = _stable_unit_random(
        "vpp_acceptance_gate_v4_event_level_draw",
        persona_config.get("id", ""),
        event.get("id", ""),
    )
    high_confidence_accept = probability >= 0.90
    if high_confidence_accept:
        factors.append("high_confidence_accept_band")
    accepted = bool(draw <= probability or high_confidence_accept)
    return {
        "version": "vpp_plan_acceptance_gate_v4_event_level_draw",
        "event_id": event.get("id", ""),
        "method": method_key,
        "accepted": accepted,
        "decision": "accept_vpp_plan" if accepted else "reject_fallback_to_no_vpp_daily_plan",
        "acceptance_probability": round(probability, 6),
        "stable_draw": round(draw, 6),
        "high_confidence_accept": bool(high_confidence_accept),
        "base_override_probability": round(override_prob, 6),
        "factors": factors,
        "intrusion": intrusion,
        "strategy_quality": strategy_quality,
        "acceptance_learning": {
            "adjustment": 0.0,
            "factors": ["method_agnostic_gate_uses_strategy_and_roleplay_only"],
        },
        "adaptability_diagnostics": adaptability,
        "proposed_plan": _plan_snapshot_for_gate(proposed_plan),
        "default_plan": _plan_snapshot_for_gate(default_plan),
    }


def _fallback_plan_after_vpp_rejection(
    *,
    default_plan: dict | None,
    current_setpoint: float,
    event: dict,
    persona_config: dict | None = None,
    appliance_config: dict | None = None,
) -> dict:
    """Return a no-new-action fallback that preserves the no-VPP daily plan."""
    default_plan = default_plan or {}
    daily_gate = _evaluate_no_vpp_daily_plan_acceptance(
        plan=default_plan,
        persona_config=persona_config,
        appliance_config=appliance_config,
    )
    if not bool(daily_gate.get("accepted")):
        manual_plan = _manual_no_vpp_user_plan(
            persona_config=persona_config,
            appliance_config=appliance_config,
            current_setpoint=current_setpoint,
        )
        manual_plan.update(
            {
                "next_check_hour": float(event.get("end_h", 0.0)) if event else None,
                "reason": "VPP dispatch rejected; user manually restores normal comfort routine",
                "objective_source": "vpp_acceptance_gate_manual_comfort_routine",
                "fallback_default_plan": _plan_snapshot_for_gate(default_plan),
                "fallback_daily_plan_gate": daily_gate,
            }
        )
        return manual_plan
    try:
        setpoint = float(default_plan.get("setpoint", current_setpoint))
    except (TypeError, ValueError):
        setpoint = float(current_setpoint)
    return {
        "setpoint": round(setpoint, 1),
        "next_check_hour": float(event.get("end_h", 0.0)) if event else None,
        "reason": "VPP dispatch rejected by user gate; fallback to no-VPP daily plan",
        "appliance_actions": _non_null_actions(default_plan.get("appliance_actions")),
        "objective_source": "vpp_acceptance_gate_fallback_no_vpp_daily_plan",
        "fallback_default_plan": _plan_snapshot_for_gate(default_plan),
        "fallback_daily_plan_gate": daily_gate,
    }


def _lock_to_user_accepted_vpp_plan(
    plan: dict | None,
    acceptance_gate: dict | None,
    *,
    clear_appliance_actions: bool = False,
) -> tuple[dict, bool]:
    """Keep active execution within the concrete VPP plan the user accepted."""
    out = dict(plan or {})
    gate = acceptance_gate or {}
    accepted_plan = gate.get("accepted_execution_plan") or gate.get("proposed_plan") or {}
    if not bool(gate.get("accepted")) or not isinstance(accepted_plan, dict):
        return out, False
    changed = False
    if accepted_plan.get("setpoint") is not None:
        try:
            accepted_sp = round(float(accepted_plan.get("setpoint")), 1)
            old_sp = float(out.get("setpoint", accepted_sp))
            if abs(old_sp - accepted_sp) > 1e-6:
                changed = True
            out["setpoint"] = accepted_sp
        except (TypeError, ValueError):
            pass
    if clear_appliance_actions:
        if out.get("appliance_actions"):
            changed = True
        out["appliance_actions"] = {}
    else:
        accepted_actions = _non_null_actions(accepted_plan.get("appliance_actions"))
        if accepted_actions:
            if accepted_actions != _non_null_actions(out.get("appliance_actions")):
                changed = True
            out["appliance_actions"] = accepted_actions
    reason = str(out.get("reason", ""))
    suffix = "locked to user-accepted VPP plan"
    if suffix not in reason:
        out["reason"] = (reason + " | " + suffix).strip(" |")[:240]
    out["vpp_acceptance_gate"] = dict(gate)
    return out, changed


def _vpp_gate_matches_current_plan(
    acceptance_gate: dict | None,
    plan: dict | None,
    *,
    setpoint_tolerance_c: float = 0.25,
) -> bool:
    gate = acceptance_gate or {}
    accepted_plan = gate.get("accepted_execution_plan") or gate.get("proposed_plan") or {}
    if not isinstance(accepted_plan, dict):
        return False
    try:
        accepted_sp = float(accepted_plan.get("setpoint"))
        current_sp = float((plan or {}).get("setpoint"))
    except (TypeError, ValueError):
        return False
    return abs(accepted_sp - current_sp) <= float(setpoint_tolerance_c)


def _manual_no_vpp_user_plan(
    *,
    persona_config: dict | None,
    appliance_config: dict | None,
    current_setpoint: float | None = None,
) -> dict:
    """Comfort/routine plan a user would restore manually outside VPP consent."""
    ac_cfg = ((appliance_config or {}).get("ac") or {})
    try:
        pref_min = float(ac_cfg.get("setpoint_preferred_min_c", 24.0))
    except (TypeError, ValueError):
        pref_min = 24.0
    try:
        pref_max = float(ac_cfg.get("setpoint_preferred_max_c", 26.0))
    except (TypeError, ValueError):
        pref_max = 26.0
    try:
        current = float(current_setpoint)
    except (TypeError, ValueError):
        current = (pref_min + pref_max) / 2.0
    comfort_sp = max(pref_min, min(pref_max, current))
    # If the current setpoint is outside the user's normal band, restore the
    # warm comfortable edge. This keeps fallback realistic but not artificially
    # over-cooled.
    if current < pref_min or current > pref_max:
        comfort_sp = pref_max

    actions: dict[str, Any] = {}
    cfg = appliance_config or {}
    for name in ("washer", "dishwasher", "dryer"):
        dev = cfg.get(name, {}) if isinstance(cfg.get(name, {}), dict) else {}
        if not bool(dev.get("present", False)):
            continue
        try:
            preferred = float(dev.get("preferred_h", dev.get("earliest_h", 8.0)))
            duration = float(dev.get("duration_h", 1.0))
            earliest = float(dev.get("earliest_h", preferred))
            latest = float(dev.get("latest_h", preferred + duration))
            latest_start = latest - duration if latest >= earliest else latest + 24.0 - duration
            preferred = max(earliest, min(latest_start, preferred))
            actions[f"{name}_start_h"] = round(preferred % 24.0, 3)
            actions[f"{name}_skip"] = False
        except (TypeError, ValueError):
            continue

    wh = cfg.get("water_heater", {}) if isinstance(cfg.get("water_heater", {}), dict) else {}
    if bool(wh.get("present", False)):
        try:
            start_h = float(wh.get("pre_heat_window_start_h", wh.get("normal_start_h", 17.0)))
            end_h = float(wh.get("pre_heat_window_end_h", wh.get("normal_end_h", 21.0)))
            temp_c = float(wh.get("normal_temp_c", 60.0))
            actions.update(
                {
                    "water_heater_preheat": True,
                    "water_heater_preheat_start_h": round(start_h, 3),
                    "water_heater_preheat_end_h": round(end_h, 3),
                    "water_heater_preheat_temp_c": round(temp_c, 1),
                }
            )
        except (TypeError, ValueError):
            pass

    ev = cfg.get("ev", {}) if isinstance(cfg.get("ev", {}), dict) else {}
    if bool(ev.get("present", False)):
        try:
            actions.update(
                {
                    "ev_mode": "normal",
                    "ev_charge_start_h": round(float(ev.get("arrival_h", 18.0)), 3),
                    "ev_charge_end_h": round(float(ev.get("departure_h", 7.5)), 3),
                }
            )
        except (TypeError, ValueError):
            pass

    return {
        "setpoint": round(comfort_sp, 1),
        "next_check_hour": None,
        "reason": "User manual no-VPP comfort routine",
        "appliance_actions": actions,
        "objective_source": "manual_user_no_vpp_comfort_routine",
    }


def _evaluate_no_vpp_daily_plan_acceptance(
    *,
    plan: dict | None,
    persona_config: dict | None,
    appliance_config: dict | None,
) -> dict:
    """Gate ordinary no-VPP plans so fallback cannot stay unrealistically harsh."""
    plan = plan or {}
    ac_cfg = ((appliance_config or {}).get("ac") or {})
    try:
        pref_min = float(ac_cfg.get("setpoint_preferred_min_c", 24.0))
    except (TypeError, ValueError):
        pref_min = 24.0
    try:
        pref_max = float(ac_cfg.get("setpoint_preferred_max_c", 26.0))
    except (TypeError, ValueError):
        pref_max = 26.0
    try:
        tol = float(ac_cfg.get("temp_tolerance_c", 1.0))
    except (TypeError, ValueError):
        tol = 1.0
    try:
        setpoint = float(plan.get("setpoint"))
    except (TypeError, ValueError):
        setpoint = (pref_min + pref_max) / 2.0
    actions = plan.get("appliance_actions") or {}
    reasons: list[str] = []
    daily_slack = max(0.1, min(0.25, 0.25 * max(0.0, tol)))
    if setpoint < pref_min - daily_slack:
        reasons.append(f"daily_setpoint_below_preferred:{setpoint:.1f}<{pref_min:.1f}")
    if setpoint > pref_max + daily_slack:
        reasons.append(f"daily_setpoint_above_preferred:{setpoint:.1f}>{pref_max:.1f}")
    fixed_modified = _fixed_services_modified(actions, {}, appliance_config)
    if fixed_modified:
        reasons.append("fixed_routine_modified:" + ",".join(fixed_modified))
    skip_devices = _requested_skip_devices(actions)
    if skip_devices:
        reasons.append("routine_task_skipped:" + ",".join(skip_devices))
    return {
        "version": "no_vpp_daily_plan_acceptance_gate_v1",
        "accepted": not reasons,
        "decision": "accept_no_vpp_daily_plan" if not reasons else "manual_override_to_comfort_routine",
        "reasons": reasons,
        "plan_snapshot": _plan_snapshot_for_gate(plan),
        "preferred_min_c": round(pref_min, 3),
        "preferred_max_c": round(pref_max, 3),
        "daily_slack_c": round(daily_slack, 3),
    }


def _service_completed(name: str, info: dict) -> bool:
    """Whether a controllable appliance met its user-facing service goal."""
    if name == "water_heater":
        return bool(info.get("ready_at_bath", True))
    if name == "ev":
        return bool(info.get("target_reached", False))
    return bool(info.get("completed", False)) and not bool(info.get("skipped", False))


def _capacity_hvac_context(loop, *, temp: float, out_t: float, facility_w: float) -> dict:
    """Build a lightweight HVAC proxy so capacity calls reflect AC flexibility."""
    appliance_kw = 0.0
    suite = getattr(loop, "appliance_suite", None)
    if suite is not None:
        appliance_kw = sum(float(v or 0.0) for v in getattr(suite, "_last_powers", {}).values())
    facility_kw = max(0.0, float(facility_w or 0.0) / 1000.0)
    hvac_kw = max(0.0, facility_kw - appliance_kw)
    return {
        "hvac_power_kw": hvac_kw,
        "indoor_temp_c": float(temp),
        "outdoor_temp_c": float(out_t),
        "current_setpoint_c": float(getattr(loop, "sp", SP_DEFAULT)),
        "max_setpoint_c": 27.5,
        "min_active_power_kw": 0.15,
    }


def _build_decision_time_state(
    loop,
    *,
    sim_h: float,
    hod: float,
    temp: float | None,
    out_t: float | None,
    vpp_event: dict | None,
    vpp_target_kwh: float | None,
    appliance_config: dict | None,
    facility_w: float | None = None,
) -> dict:
    """Build the Protocol A decision-time state shared by MPC and Agent logs."""
    from experiments.benchmark.baselines.state_adapter import build_mpc_state

    state = build_mpc_state(
        sim_h=sim_h,
        hod=hod,
        day_idx=int(sim_h // 24),
        temp_c=temp,
        outdoor_temp_c=out_t,
        current_setpoint_c=getattr(loop, "sp", None),
        vpp_event=vpp_event,
        vpp_target_kwh=vpp_target_kwh,
        appliance_config=appliance_config or {},
        appliance_suite=getattr(loop, "appliance_suite", None),
        history={
            "vpp_event_log": getattr(loop, "vpp_event_log", []),
            "previous_setpoint_c": getattr(loop, "sp", None),
        },
    )
    if facility_w is not None:
        facility_kw = max(0.0, float(facility_w) / 1000.0)
        appliance_kw = 0.0
        suite = getattr(loop, "appliance_suite", None)
        if suite is not None:
            appliance_kw = sum(float(v or 0.0) for v in getattr(suite, "_last_powers", {}).values())
        state.update(
            {
                "current_facility_power_kw": facility_kw,
                "current_hvac_power_kw": max(0.0, facility_kw - appliance_kw),
                "current_appliance_power_kw": appliance_kw,
            }
        )
    state.update(
        {
            "city": str(getattr(loop, "weather_label", "") or ""),
            "weather": str(getattr(loop, "weather_label", "") or ""),
            "weather_label": str(getattr(loop, "weather_label", "") or ""),
            "occupied": bool(getattr(loop, "current_occupied", True)),
            "occupancy_count": float(getattr(loop, "current_occupancy_count", 0.0) or 0.0),
            "occupancy_source": getattr(loop, "current_occupancy_source", "unknown"),
            "sim_days": int(getattr(loop, "sim_days", 1) or 1),
            "run_end_abs_h": float(int(getattr(loop, "sim_days", 1) or 1) * 24.0),
        }
    )
    return state


def _compute_posthoc_decision_objective(
    loop,
    *,
    action_result: dict,
    sim_h: float,
    hod: float,
    temp: float | None,
    out_t: float | None,
    vpp_event: dict | None,
    vpp_target_kwh: float | None,
    appliance_config: dict | None,
    facility_w: float | None = None,
) -> dict:
    """Compute PDF v1.5 objective for an already-produced Agent action.

    This is a post-hoc Protocol A diagnostic only. It copies appliance actions
    so the raw Agent command is never modified by objective evaluation.
    """
    from experiments.benchmark.baselines.home_objective_v15 import compute_home_objective_v15
    from experiments.benchmark.baselines.weights import pdf_v15_weights

    decision_state = _build_decision_time_state(
        loop,
        sim_h=sim_h,
        hod=hod,
        temp=temp,
        out_t=out_t,
        vpp_event=vpp_event,
        vpp_target_kwh=vpp_target_kwh,
        appliance_config=appliance_config,
        facility_w=facility_w,
    )
    action = {
        "setpoint": action_result.get("setpoint", getattr(loop, "sp", None)),
        "next_check_hour": action_result.get("next_check_hour"),
        "reason": action_result.get("reason", ""),
        "appliances": dict(action_result.get("appliance_actions") or {}),
    }
    return compute_home_objective_v15(
        action=action,
        state=decision_state,
        weights=pdf_v15_weights(dr_event=bool(vpp_event)),
    )


_AGENT_ONBOARDING_QUESTIONS: list[dict[str, str]] = [
    {
        "id": "vpp_priority",
        "question": (
            "During a one-hour VPP peak event, what should the home prioritize: "
            "comfort and routine, bill savings, grid support, or a balance?"
        ),
    },
    {
        "id": "thermostat_flexibility",
        "question": (
            "If the home remains safe, how much temporary AC setpoint change would you usually accept "
            "during a peak event?"
        ),
    },
    {
        "id": "appliance_shift_consent",
        "question": (
            "Can the system automatically shift washer, dishwasher, water-heater, or EV timing when "
            "deadlines are protected, or should it ask first?"
        ),
    },
    {
        "id": "calendar_routine_constraints",
        "question": (
            "Which calendar or household routines should not be disturbed, especially around evening "
            "arrival, meals, showers, caregiving, or sleep?"
        ),
    },
]


def _agent_onboarding_questions() -> list[dict[str, str]]:
    return [dict(item) for item in _AGENT_ONBOARDING_QUESTIONS]


def _weight_value(persona_config: dict | None, key: str, default: float = 0.33) -> float:
    try:
        weights = (((persona_config or {}).get("preferences") or {}).get("scoring_weights") or {})
        return float(weights.get(key, default) or default)
    except (TypeError, ValueError):
        return float(default)


def _question_by_id(questions: list[dict[str, str]], question_id: str) -> str:
    for item in questions:
        if item.get("id") == question_id:
            return str(item.get("question", ""))
    return ""


def _normalize_agent_onboarding_result(
    data: dict,
    *,
    questions: list[dict[str, str]],
    source: str,
    metrics: dict | None = None,
) -> dict:
    answers_by_id: dict[str, str] = {}
    raw_answers = data.get("answers") if isinstance(data, dict) else None
    if isinstance(raw_answers, list):
        for item in raw_answers:
            if not isinstance(item, dict):
                continue
            qid = str(item.get("id", "")).strip()
            answer = str(item.get("answer", "")).strip()
            if qid and answer:
                answers_by_id[qid] = answer
    elif isinstance(raw_answers, dict):
        for qid, answer in raw_answers.items():
            if str(qid).strip() and str(answer).strip():
                answers_by_id[str(qid).strip()] = str(answer).strip()

    normalized_answers = [
        {
            "id": q["id"],
            "question": q["question"],
            "answer": answers_by_id.get(q["id"], "No strong preference stated."),
        }
        for q in questions[:4]
    ]

    profile = data.get("inferred_profile") if isinstance(data.get("inferred_profile"), dict) else {}
    rules = data.get("preference_rules") if isinstance(data.get("preference_rules"), list) else []
    return {
        "version": "agent_onboarding_questionnaire_v1",
        "source": source,
        "question_count": len(questions[:4]),
        "answers": normalized_answers,
        "inferred_profile": dict(profile or {}),
        "preference_rules": [str(rule).strip() for rule in rules if str(rule).strip()][:8],
        "metrics": metrics or {},
    }


def _fallback_agent_onboarding_questionnaire(persona_config: dict | None) -> dict:
    """Role-play a compact onboarding questionnaire without exposing hidden persona fields."""
    persona_config = persona_config or {}
    tags = persona_config.get("tags", {}) or {}
    schedule = persona_config.get("schedule", {}) or {}
    appliances = persona_config.get("appliances", {}) or {}
    ac_cfg = appliances.get("ac", {}) if isinstance(appliances.get("ac"), dict) else {}
    questions = _agent_onboarding_questions()

    comfort_w = _weight_value(persona_config, "comfort")
    energy_w = _weight_value(persona_config, "energy")
    vpp_w = _weight_value(persona_config, "vpp")
    cost_grid_w = energy_w + vpp_w
    comfort_sensitive = tags.get("comfort") == "temp_sensitive" or bool(schedule.get("vulnerable_members"))
    confirmation_needed = tags.get("control") in {"confirm_required", "low_auto_accept", "privacy_sensitive"}
    suggestion_first = tags.get("control") in {"suggestion_first", "confirm_required"} or tags.get("price") == "needs_explanation"
    price_grid_motivated = (
        tags.get("price") in {"price_sensitive", "price_driven"}
        or tags.get("grid_value") in {"high_value", "high_flex", "evening_peak", "short_peak_cut"}
        or cost_grid_w >= 0.62
    )
    irregular_calendar = tags.get("schedule") == "irregular" or float(schedule.get("schedule_variability_h", 0.0) or 0.0) >= 2.0
    task_rigid = tags.get("task") == "rigid" or any(
        isinstance(info, dict)
        and bool(info.get("present"))
        and info.get("dr_adjustable") is False
        for info in appliances.values()
    )

    try:
        pref_min = float(ac_cfg.get("setpoint_preferred_min_c", 24.0))
    except (TypeError, ValueError):
        pref_min = 24.0
    try:
        pref_max = float(ac_cfg.get("setpoint_preferred_max_c", 26.0))
    except (TypeError, ValueError):
        pref_max = 26.0
    try:
        ac_tol = float(ac_cfg.get("temp_tolerance_c", 1.0))
    except (TypeError, ValueError):
        ac_tol = 1.0

    if comfort_sensitive or comfort_w >= 0.55:
        comfort_priority = "high"
        thermostat_flex_c = min(0.5, max(0.2, ac_tol))
    elif cost_grid_w >= 0.65 and comfort_w <= 0.35:
        comfort_priority = "medium"
        thermostat_flex_c = min(1.2, max(0.8, ac_tol))
    else:
        comfort_priority = "medium"
        thermostat_flex_c = min(0.8, max(0.4, ac_tol * 0.75))

    if price_grid_motivated and not confirmation_needed and not comfort_sensitive:
        strategy_bias = "cost_grid_oriented"
        vpp_answer = (
            "I am willing to prioritize bill savings and grid support if comfort stays reasonable "
            "and the controller explains the benefit."
        )
    elif comfort_sensitive or confirmation_needed or irregular_calendar:
        strategy_bias = "comfort_calendar_protective"
        vpp_answer = (
            "Use a balanced plan, but do not disrupt comfort, evening routine, or last-minute calendar changes "
            "just to chase VPP savings."
        )
    else:
        strategy_bias = "balanced_middle"
        vpp_answer = (
            "Aim for a balance: save energy during peaks when it is low disruption, but keep comfort and "
            "daily tasks on track."
        )

    if confirmation_needed:
        automation_preference = "ask_before_vpp_specific_changes"
        appliance_answer = (
            "Ask before changing appliance or water-heater timing for a VPP event, especially if plans may have changed."
        )
    elif suggestion_first:
        automation_preference = "suggestion_first_with_clear_benefit"
        appliance_answer = (
            "You can suggest shifts and usually proceed when the benefit is clear and deadlines remain protected."
        )
    else:
        automation_preference = "automatic_when_deadlines_protected"
        appliance_answer = (
            "Automatic shifting is fine for flexible loads when the task still finishes on time."
        )
    if task_rigid:
        appliance_flexibility = "limited_by_fixed_routines"
        appliance_answer += " Fixed or routine tasks should be preserved unless I explicitly approve a change."
    else:
        appliance_flexibility = "flexible_if_deadlines_protected"

    if irregular_calendar or confirmation_needed:
        calendar_routine_sensitivity = "high"
        calendar_answer = (
            "Treat evening arrival, shower/bath preparation, and any same-day changes as protected. "
            "Do not assume yesterday's schedule is still valid."
        )
    elif schedule.get("returns_home_h") is not None:
        calendar_routine_sensitivity = "medium"
        calendar_answer = (
            f"Keep the home comfortable around my usual return near {float(schedule.get('returns_home_h')):.1f}h "
            "and avoid pushing chores into late evening."
        )
    else:
        calendar_routine_sensitivity = "medium"
        calendar_answer = "Protect normal evening routines and avoid late surprises."

    thermo_answer = (
        f"Keep AC inside about {pref_min:.1f}-{pref_max:.1f}C. A temporary change of about "
        f"{thermostat_flex_c:.1f}C is the normal limit unless I approve more."
    )
    cost_grid_priority = "high" if price_grid_motivated or cost_grid_w >= 0.62 else ("medium" if cost_grid_w >= 0.45 else "low")
    profile = {
        "comfort_priority": comfort_priority,
        "cost_grid_priority": cost_grid_priority,
        "automation_preference": automation_preference,
        "thermostat_flexibility_c": round(thermostat_flex_c, 2),
        "appliance_flexibility": appliance_flexibility,
        "calendar_routine_sensitivity": calendar_routine_sensitivity,
        "strategy_bias": strategy_bias,
    }
    rules = [
        f"Keep AC within about {pref_min:.1f}-{pref_max:.1f}C unless the user explicitly accepts a wider drift.",
        "Prefer VPP plans that finish required appliance, hot-water, and EV services outside the event window.",
        "Explain the concrete comfort, cost, and schedule tradeoff before VPP-specific changes.",
    ]
    if automation_preference.startswith("ask"):
        rules.append("For VPP-specific changes, use low-disruption actions and preserve the no-VPP plan when consent is uncertain.")
    elif strategy_bias == "cost_grid_oriented":
        rules.append("For cost/grid-oriented choices, stay close to the Rule+MILP appliance schedule when service deadlines are protected.")
    else:
        rules.append("For comfort or calendar-sensitive choices, diverge from Rule+MILP when needed to preserve routine and comfort.")
    if calendar_routine_sensitivity == "high":
        rules.append("Re-check same-day calendar/routine cues before evening VPP actions.")

    data = {
        "answers": [
            {"id": "vpp_priority", "answer": vpp_answer},
            {"id": "thermostat_flexibility", "answer": thermo_answer},
            {"id": "appliance_shift_consent", "answer": appliance_answer},
            {"id": "calendar_routine_constraints", "answer": calendar_answer},
        ],
        "inferred_profile": profile,
        "preference_rules": rules,
    }
    normalized = _normalize_agent_onboarding_result(
        data,
        questions=questions,
        source="roleplay_questionnaire_fallback",
    )
    for answer in normalized["answers"]:
        answer["question"] = _question_by_id(questions, answer["id"])
    return normalized


def _run_agent_onboarding_questionnaire(persona_config: dict | None) -> dict:
    questions = _agent_onboarding_questions()
    try:
        from energybridge.llm.roleplay_user import RoleplayUserSimulator

        response = RoleplayUserSimulator().answer_onboarding_questions(
            persona=persona_config or {},
            questions=questions,
        )
        data = response.get("data") or {}
        normalized = _normalize_agent_onboarding_result(
            data,
            questions=questions,
            source="roleplay_questionnaire_llm",
            metrics=response.get("metrics") or {},
        )
        if not normalized.get("preference_rules"):
            fallback = _fallback_agent_onboarding_questionnaire(persona_config)
            normalized["preference_rules"] = fallback.get("preference_rules", [])
        return normalized
    except Exception as exc:
        fallback = _fallback_agent_onboarding_questionnaire(persona_config)
        fallback["source"] = "roleplay_questionnaire_fallback_after_error"
        fallback["error"] = str(exc)[:200]
        return fallback


def _agent_onboarding_summary_text(questionnaire: dict | None) -> str:
    questionnaire = questionnaire or {}
    profile = questionnaire.get("inferred_profile") if isinstance(questionnaire.get("inferred_profile"), dict) else {}
    answers = questionnaire.get("answers") if isinstance(questionnaire.get("answers"), list) else []
    answer_bits = [
        f"{item.get('id')}: {str(item.get('answer', ''))[:140]}"
        for item in answers[:4]
        if isinstance(item, dict)
    ]
    if not profile and not answer_bits:
        return "Initial onboarding did not reveal strong preferences yet."
    return (
        "Initial onboarding suggests "
        f"strategy_bias={profile.get('strategy_bias', 'unknown')}, "
        f"comfort_priority={profile.get('comfort_priority', 'unknown')}, "
        f"cost_grid_priority={profile.get('cost_grid_priority', 'unknown')}, "
        f"automation={profile.get('automation_preference', 'unknown')}. "
        + (" Answers: " + " | ".join(answer_bits) if answer_bits else "")
    )


def _init_agent_preference_memory(
    loop,
    output_dir: Path,
    *,
    method: str,
    persona_config: dict | None,
) -> None:
    """Create optional run-local preference memory files for agent methods."""
    if method != "agent":
        return
    persist_memory = str(os.getenv("ENERGYBRIDGE_PERSIST_AGENT_MEMORY", "")).strip().lower() in {
        "1", "true", "yes", "on"
    }
    persona_id = (persona_config or {}).get("id", "unknown_persona")
    questionnaire = _run_agent_onboarding_questionnaire(persona_config)
    memory = {
        "version": "agent_preference_memory_v1",
        "method": method,
        "persona_id": persona_id,
        "purpose": (
            "Run-local memory for EnergyBridge agent methods. It starts from a short "
            "role-play user questionnaire and records later feedback so decisions can "
            "learn user preferences. File persistence is optional and disabled by default."
        ),
        "onboarding_questionnaire": questionnaire,
        "events": [],
        "learned_preference_rules": list(questionnaire.get("preference_rules") or [])[:8],
        "latest_summary": _agent_onboarding_summary_text(questionnaire),
    }
    loop.agent_preference_memory = memory
    loop.persist_agent_preference_memory = persist_memory
    if persist_memory:
        loop.agent_memory_path = Path(output_dir) / "agent_preference_memory.json"
        loop.agent_memory_md_path = Path(output_dir) / "agent_preference_memory.md"
        _write_agent_preference_memory(loop)
    else:
        loop.agent_memory_path = None
        loop.agent_memory_md_path = None
        label = "EnergyBridge"
        profile = questionnaire.get("inferred_profile") if isinstance(questionnaire.get("inferred_profile"), dict) else {}
        print(f"  [{label} Memory] run-context memory only; set ENERGYBRIDGE_PERSIST_AGENT_MEMORY=1 to write review files.")
        print(
            f"  [{label} Onboarding] source={questionnaire.get('source')} "
            f"questions={questionnaire.get('question_count')} "
            f"strategy_bias={profile.get('strategy_bias', 'unknown')}"
        )


def _agent_preference_memory_prompt_text(loop) -> str:
    memory = getattr(loop, "agent_preference_memory", {}) or {}
    if not memory:
        return ""
    questionnaire = memory.get("onboarding_questionnaire") if isinstance(memory.get("onboarding_questionnaire"), dict) else {}
    compact_questionnaire = {
        "source": questionnaire.get("source"),
        "answers": questionnaire.get("answers", [])[:4],
        "inferred_profile": questionnaire.get("inferred_profile", {}),
    }
    prompt_memory = {
        "latest_summary": memory.get("latest_summary", ""),
        "onboarding_questionnaire": compact_questionnaire,
        "learned_preference_rules": memory.get("learned_preference_rules", []),
        "recent_events": (memory.get("events") or [])[-3:],
    }
    return (
        "\n[AGENT USER MEMORY]\n"
        "Use this run-local memory context as a decision input. It resets for every fresh benchmark run. "
        "Do not assume hidden persona labels; infer preferences only from questionnaire answers, calendar/context, and scored feedback.\n"
        f"{json.dumps(prompt_memory, ensure_ascii=False)}"
    )


def _agent_memory_profile(loop) -> dict:
    memory = getattr(loop, "agent_preference_memory", {}) or {}
    questionnaire = memory.get("onboarding_questionnaire") if isinstance(memory.get("onboarding_questionnaire"), dict) else {}
    profile = questionnaire.get("inferred_profile") if isinstance(questionnaire.get("inferred_profile"), dict) else {}
    return dict(profile or {})


def _agent_memory_strategy_bias(loop) -> str:
    return str(_agent_memory_profile(loop).get("strategy_bias", "") or "").strip().lower()


def _agent_memory_comfort_priority(loop) -> str:
    return str(_agent_memory_profile(loop).get("comfort_priority", "") or "").strip().lower()


def _agent_memory_is_cost_grid_oriented(loop) -> bool:
    profile = _agent_memory_profile(loop)
    blob = " ".join(
        str(profile.get(key, "") or "").lower()
        for key in ("strategy_bias", "cost_grid_priority", "automation_preference", "appliance_flexibility")
    )
    cost_tokens = ("cost", "price", "saving", "savings", "grid", "economic", "cooperative", "peak", "bill")
    return str(profile.get("cost_grid_priority", "")).lower() == "high" or any(token in blob for token in cost_tokens)


def _agent_memory_is_protective(loop) -> bool:
    profile = _agent_memory_profile(loop)
    blob = " ".join(
        str(profile.get(key, "") or "").lower()
        for key in (
            "strategy_bias",
            "comfort_priority",
            "automation_preference",
            "calendar_routine_sensitivity",
            "appliance_flexibility",
        )
    )
    protective_tokens = (
        "calendar",
        "confirm",
        "privacy",
        "protect",
        "comfort_sensitive",
        "temp_sensitive",
        "temperature_sensitive",
        "temperature",
        "cautious",
        "irregular",
        "fixed",
    )
    return (
        str(profile.get("comfort_priority", "")).lower() == "high"
        or str(profile.get("calendar_routine_sensitivity", "")).lower() == "high"
        or any(token in blob for token in protective_tokens)
    )


def _agent_recent_positive_vpp_events(loop, *, min_events: int = 2) -> bool:
    events = [
        event
        for event in list(getattr(loop, "vpp_event_log", []) or [])
        if isinstance(event, dict) and event.get("score") is not None
    ]
    if len(events) < int(min_events):
        return False
    recent = events[-int(min_events):]
    return all(
        _event_score_int(event, "score", 3) >= 4
        and _event_score_int(event, "comfort_score", 3) >= 4
        and (event.get("target_achieved") is not False)
        for event in recent
    )


def _agent_vpp_tradeoff_setpoint_c(
    loop,
    *,
    ac_sp_max_c: float,
    ac_sp_tol_c: float,
    run_sp_max_c: float,
    protective_mode: bool,
    multi_user_comfort_first: bool = False,
) -> float:
    """EB-only event setpoint from questionnaire memory plus observed feedback."""
    profile = _agent_memory_profile(loop)
    strategy_bias = str(profile.get("strategy_bias", "") or "").lower()
    comfort_priority = str(profile.get("comfort_priority", "") or "").lower()
    cost_grid_priority = str(profile.get("cost_grid_priority", "") or "").lower()
    automation = str(profile.get("automation_preference", "") or "").lower()
    calendar_sensitivity = str(profile.get("calendar_routine_sensitivity", "") or "").lower()

    comfort_first = (
        comfort_priority == "high"
        or multi_user_comfort_first
        or (protective_mode and cost_grid_priority != "high")
    )
    if comfort_first:
        return round(min(float(run_sp_max_c), float(ac_sp_max_c)), 1)

    # Cost/grid users should look closer to Rule+MILP, but still remain below
    # hard baseline aggression so the user gate has a reason to accept EB.
    if cost_grid_priority == "high" or "cost_grid" in strategy_bias:
        drift_c = 0.5
        return round(min(float(run_sp_max_c), float(ac_sp_max_c) + drift_c), 1)

    # Confirmation/calendar users start gently and only become more assertive
    # after the role-play user has accepted the previous pattern.
    if (
        "ask" in automation
        or calendar_sensitivity == "high"
        or "protective" in strategy_bias
    ):
        return round(min(float(run_sp_max_c), float(ac_sp_max_c)), 1)

    drift_c = 0.5
    return round(min(float(run_sp_max_c), float(ac_sp_max_c) + drift_c), 1)


def _agent_pre_vpp_setpoint_c(
    loop,
    *,
    ac_sp_min_c: float,
    ac_sp_max_c: float,
    ac_sp_default_c: float,
    run_sp_max_c: float,
    protective_mode: bool,
    multi_user_comfort_first: bool = False,
) -> float:
    """EB-only pre-event setpoint; avoids unnecessary pre-cooling for cost users."""
    profile = _agent_memory_profile(loop)
    strategy_bias = str(profile.get("strategy_bias", "") or "").lower()
    comfort_priority = str(profile.get("comfort_priority", "") or "").lower()
    cost_grid_priority = str(profile.get("cost_grid_priority", "") or "").lower()
    automation = str(profile.get("automation_preference", "") or "").lower()
    calendar_sensitivity = str(profile.get("calendar_routine_sensitivity", "") or "").lower()
    try:
        thermostat_flex_c = float(profile.get("thermostat_flexibility_c", 1.0) or 1.0)
    except (TypeError, ValueError):
        thermostat_flex_c = 1.0

    if comfort_priority == "high" or multi_user_comfort_first:
        # Temperature-sensitive users benefit from true pre-cooling.  Cautious
        # confirmation users with normal comfort mainly want stability, so avoid
        # extra cold pre-cooling that raises cost without improving consent.
        if multi_user_comfort_first or thermostat_flex_c <= 0.55:
            return round(max(float(ac_sp_min_c), min(float(run_sp_max_c), float(ac_sp_min_c))), 1)
        return round(min(float(run_sp_max_c), max(float(ac_sp_default_c), float(ac_sp_max_c))), 1)
    if cost_grid_priority == "high" or "cost_grid" in strategy_bias:
        return round(min(float(run_sp_max_c), float(ac_sp_max_c) + 0.5), 1)
    if protective_mode or "ask" in automation or calendar_sensitivity == "high":
        return round(min(float(run_sp_max_c), max(float(ac_sp_default_c), float(ac_sp_max_c))), 1)
    return round(min(float(run_sp_max_c), float(ac_sp_max_c)), 1)


def _agent_refine_vpp_appliance_actions(
    actions: dict | None,
    loop,
    *,
    hod: float,
    event: dict | None,
    appliance_config: dict | None,
) -> dict:
    """EB-only low-disruption appliance refinement before a VPP event."""
    if not event:
        return dict(actions or {})
    out = dict(actions or {})
    cfg = appliance_config or {}
    profile = _agent_memory_profile(loop)
    strategy_bias = str(profile.get("strategy_bias", "") or "").lower()
    automation = str(profile.get("automation_preference", "") or "").lower()
    calendar_sensitivity = str(profile.get("calendar_routine_sensitivity", "") or "").lower()
    should_refine = (
        "protective" in strategy_bias
        or "ask" in automation
        or calendar_sensitivity == "high"
        or _agent_memory_is_protective(loop)
    )
    if not should_refine:
        return out

    try:
        vpp_start_hod = float(event.get("trigger_h", 18.0)) % 24.0
        vpp_end_hod = float(event.get("end_h", 19.0)) % 24.0
        current_hod = float(hod) % 24.0
    except (TypeError, ValueError):
        return out

    washer = cfg.get("washer", {}) or {}
    if bool(washer.get("present", False)) and out.get("washer_start_h") is None:
        try:
            preferred = float(washer.get("preferred_h", vpp_end_hod))
            duration = float(washer.get("duration_h", 1.0) or 1.0)
        except (TypeError, ValueError):
            preferred, duration = vpp_end_hod, 1.0
        if preferred >= vpp_end_hod or preferred + duration <= vpp_start_hod:
            out["washer_start_h"] = round(preferred, 2)
            out["washer_skip"] = False

    wh = cfg.get("water_heater", {}) or {}
    if bool(wh.get("present", False)):
        try:
            existing_start = float(out.get("water_heater_preheat_start_h"))
            existing_end = float(out.get("water_heater_preheat_end_h"))
            overlaps = _interval_overlaps(existing_start, existing_end, vpp_start_hod, vpp_end_hod)
        except (TypeError, ValueError):
            overlaps = True
        if overlaps and current_hod < vpp_start_hod - 0.25:
            try:
                configured_start = float(wh.get("pre_heat_window_start_h", vpp_start_hod))
                configured_end = float(wh.get("pre_heat_window_end_h", vpp_end_hod + 1.0))
                configured_duration = max(1.0, configured_end - configured_start)
            except (TypeError, ValueError):
                configured_duration = 2.0
            preheat_end = vpp_start_hod
            preheat_start = max(current_hod, preheat_end - min(3.0, configured_duration))
            if preheat_end - preheat_start >= 0.5:
                out["water_heater_preheat_start_h"] = round(preheat_start, 2)
                out["water_heater_preheat_end_h"] = round(preheat_end, 2)
                out["water_heater_preheat_temp_c"] = max(
                    60.0,
                    min(65.0, float(out.get("water_heater_preheat_temp_c") or 63.0)),
                )
                out["water_heater_preheat"] = True
    return out


def _write_agent_preference_memory(loop) -> None:
    memory_path = getattr(loop, "agent_memory_path", None)
    md_path = getattr(loop, "agent_memory_md_path", None)
    memory = getattr(loop, "agent_preference_memory", {}) or {}
    if not getattr(loop, "persist_agent_preference_memory", False):
        return
    if memory_path is None or md_path is None or not memory:
        return
    try:
        memory_path.write_text(json.dumps(memory, indent=2, ensure_ascii=False), encoding="utf-8")
        lines = [
            "# Agent Preference Memory",
            "",
            f"- Method: `{memory.get('method', '')}`",
            f"- Persona: `{memory.get('persona_id', '')}`",
            f"- Version: `{memory.get('version', '')}`",
            "",
            "## Initial Questionnaire",
            "",
            str(_agent_onboarding_summary_text(memory.get("onboarding_questionnaire"))),
            "",
        ]
        questionnaire = memory.get("onboarding_questionnaire") if isinstance(memory.get("onboarding_questionnaire"), dict) else {}
        for item in questionnaire.get("answers") or []:
            if not isinstance(item, dict):
                continue
            lines.extend(
                [
                    f"- {item.get('id', '')}: {item.get('answer', '')}",
                ]
            )
        lines.extend([
            "",
            "## Latest Summary",
            "",
            str(memory.get("latest_summary", "")),
            "",
            "## Learned Preference Rules",
            "",
        ])
        rules = list(memory.get("learned_preference_rules") or [])
        lines.extend([f"- {rule}" for rule in rules] or ["- No learned rules yet."])
        lines.extend(["", "## Event Feedback", ""])
        for event in memory.get("events") or []:
            lines.extend(
                [
                    f"### {event.get('event_id', '?')}",
                    "",
                    f"- Score: {event.get('score')} / 5",
                    f"- Comfort/Energy/VPP: {event.get('comfort_score')}/"
                    f"{event.get('energy_score')}/{event.get('vpp_score')}",
                    f"- User before action: {event.get('user_input', '')}",
                    f"- Controller reason: {event.get('controller_reason', '')}",
                    f"- Feedback: {event.get('feedback', '')}",
                    "",
                ]
            )
        md_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    except Exception as exc:
        print(f"  [Agent Memory] write failed: {exc}")


def _update_agent_preference_memory(
    loop,
    event_result: dict,
    *,
    persona_config: dict | None,
) -> None:
    memory = getattr(loop, "agent_preference_memory", {}) or {}
    if not memory:
        return
    event_entry = {
        "event_id": event_result.get("id"),
        "day": event_result.get("day"),
        "score": event_result.get("score"),
        "comfort_score": event_result.get("comfort_score"),
        "energy_score": event_result.get("energy_score"),
        "vpp_score": event_result.get("vpp_score"),
        "user_input": str(event_result.get("user_input", ""))[:500],
        "controller_reason": str(event_result.get("reason", ""))[:500],
        "feedback": str(
            event_result.get("controller_feedback")
            or event_result.get("member_feedback_summary")
            or event_result.get("comment", "")
        )[:1200],
        "vpp_acceptance_gate": event_result.get("vpp_acceptance_gate", {}),
        "target_achieved": event_result.get("target_achieved"),
        "selected_strategy": event_result.get("selected_strategy", {}),
    }
    events = list(memory.get("events") or [])
    events.append(event_entry)
    memory["events"] = events
    try:
        from user_pref_scorer import build_vpp_preference_memory_notes

        rules = build_vpp_preference_memory_notes(list(getattr(loop, "vpp_event_log", [])), persona_config)
    except Exception:
        rules = []
    initial_rules = list((memory.get("onboarding_questionnaire") or {}).get("preference_rules") or [])
    memory["learned_preference_rules"] = list(dict.fromkeys(initial_rules + list(rules or [])))[:8]
    score_bits = [
        f"{item.get('event_id')}: score={item.get('score')}, feedback={str(item.get('feedback', ''))[:160]}"
        for item in events[-3:]
    ]
    memory["latest_summary"] = (
        "Recent user feedback suggests: "
        + (" | ".join(score_bits) if score_bits else "No scored feedback yet.")
    )
    loop.agent_preference_memory = memory
    _write_agent_preference_memory(loop)


def _agent_skill_action_from_decision(
    skill: str,
    decision: dict,
    appliance_config: dict | None,
    *,
    sp_min: float,
    sp_max: float,
    objective_source: str,
) -> dict:
    actions = _filter_controllable_appliance_actions(
        decision.get("appliances", decision.get("appliance_actions", {})),
        appliance_config,
    )
    try:
        setpoint = round(max(float(sp_min), min(float(sp_max), float(decision.get("setpoint")))), 1)
    except (TypeError, ValueError):
        setpoint = round(float(sp_min), 1)
    objective_terms = decision.get("objective_terms", {})
    return {
        "skill": skill,
        "status": "available",
        "setpoint": setpoint,
        "next_check_hour": decision.get("next_check_hour"),
        "reason": str(decision.get("reason", skill))[:240],
        "appliance_actions": actions,
        "objective_terms": objective_terms if isinstance(objective_terms, dict) else {},
        "original_objective_total": _objective_total_value(objective_terms),
        "objective_source": objective_source,
    }


def _build_agent_skill_bundle(
    loop,
    *,
    sim_h: float,
    hod: float,
    temp: float | None,
    out_t: float | None,
    facility_w: float | None,
    vpp_event: dict | None,
    appliance_config: dict | None,
    price_profile: Any,
    run_start_date: Any,
    idf_path: str | Path,
    epw_path: str | Path,
    mpc_horizon_steps: int,
    sp_min: float,
    sp_max: float,
    requested_skills: list[str] | tuple[str, ...] | set[str],
) -> dict:
    """Run only the deterministic skills requested by the EnergyBridge agent."""
    requested = {
        str(name).strip().lower()
        for name in (requested_skills or [])
        if str(name).strip()
    }
    state_dict = _build_decision_time_state(
        loop,
        sim_h=sim_h,
        hod=hod,
        temp=temp,
        out_t=out_t,
        facility_w=facility_w,
        vpp_event=vpp_event,
        vpp_target_kwh=None,
        appliance_config=appliance_config or {},
    )
    rule_milp_state = dict(state_dict)
    rule_milp_state["standalone_baseline"] = True
    bundle: dict[str, Any] = {
        "version": "energybridge_agent_skills_v1",
        "skills": {},
        "rule_milp_options": {},
        "errors": [],
        "requested_skills": sorted(requested),
    }

    if "mpc_dynamic" in requested:
        try:
            from experiments.benchmark.baselines.mpc import plan_mpc_action

            mpc_state = dict(state_dict)
            mpc_state["mpc_predictor"] = "dynamic"
            mpc_state["mpc_horizon_steps"] = int(mpc_horizon_steps)
            mpc_state["idf_path"] = str(idf_path)
            mpc_state["epw_path"] = str(epw_path)
            mpc_state["mpc_decision_history"] = [
                item
                for day_items in getattr(loop, "day_agent_decisions", [])
                for item in day_items
                if item.get("h", 10**9) < sim_h
            ]
            mpc_decision = plan_mpc_action(state=mpc_state)
            bundle["skills"]["mpc_dynamic"] = _agent_skill_action_from_decision(
                "mpc_dynamic",
                mpc_decision,
                appliance_config,
                sp_min=sp_min,
                sp_max=sp_max,
                objective_source="mpc_candidate_scoring_pdf_v15",
            )
        except Exception as exc:
            bundle["skills"]["mpc_dynamic"] = {"skill": "mpc_dynamic", "status": "error", "error": str(exc)[:200]}
            bundle["errors"].append(f"mpc_dynamic: {str(exc)[:160]}")

    if requested.intersection({"rule_milp", "dynamic_hvac"}):
        try:
            from experiments.benchmark.baselines.rule_milp import (
                _choose_dynamic_cost_min_setpoint,
                plan_rule_milp_action,
                plan_rule_milp_options,
            )

            if "rule_milp" in requested:
                rule_decision = plan_rule_milp_action(
                    state=rule_milp_state,
                    price_profile=price_profile,
                    run_start_date=run_start_date,
                )
                rule_skill = _agent_skill_action_from_decision(
                    "rule_milp",
                    rule_decision,
                    appliance_config,
                    sp_min=sp_min,
                    sp_max=sp_max,
                    objective_source="rule_milp_cost_min_v1",
                )
                try:
                    if (
                        isinstance(vpp_event, dict)
                        and float(vpp_event.get("trigger_h", 10**9)) - 1e-6
                        <= float(sim_h)
                        < float(vpp_event.get("end_h", -10**9)) - 1e-6
                    ):
                        rule_skill["setpoint"] = round(float(sp_max), 1)
                        rule_skill["reason"] = (
                            f"{rule_skill.get('reason', 'rule_milp')} | active VPP HVAC-off"
                        )[:240]
                except Exception:
                    pass
                bundle["skills"]["rule_milp"] = rule_skill
                bundle["rule_milp_options"] = plan_rule_milp_options(
                    state=rule_milp_state,
                    price_profile=price_profile,
                    run_start_date=run_start_date,
                    max_options=5,
                )
            if "dynamic_hvac" in requested:
                dynamic_sp, dynamic_diag = _choose_dynamic_cost_min_setpoint(state_dict)
                selected = (dynamic_diag or {}).get("selected") or {}
                bundle["skills"]["dynamic_hvac"] = {
                    "skill": "dynamic_hvac",
                    "status": "available",
                    "setpoint": round(max(float(sp_min), min(float(sp_max), float(dynamic_sp))), 1),
                    "next_check_hour": None,
                    "reason": "dynamic_hvac regional model setpoint guidance",
                    "appliance_actions": {},
                    "objective_terms": {
                        "version": "dynamic_hvac_skill_v1",
                        "total": selected.get("objective"),
                        "diagnostics": {
                            "source": dynamic_diag.get("source"),
                            "status": dynamic_diag.get("status"),
                            "model": dynamic_diag.get("model"),
                            "region": dynamic_diag.get("region"),
                            "selected": selected,
                        },
                    },
                    "original_objective_total": _objective_total_value({"total": selected.get("objective")}),
                    "objective_source": "dynamic_hvac_skill_v1",
                    "diagnostics": dynamic_diag,
                }
        except Exception as exc:
            if "rule_milp" in requested:
                bundle["skills"].setdefault(
                    "rule_milp",
                    {"skill": "rule_milp", "status": "error", "error": str(exc)[:200]},
                )
            if "dynamic_hvac" in requested:
                bundle["skills"]["dynamic_hvac"] = {"skill": "dynamic_hvac", "status": "error", "error": str(exc)[:200]}
            bundle["errors"].append(f"rule_milp/dynamic_hvac: {str(exc)[:160]}")

    return bundle


def _agent_skill_catalog_text() -> str:
    catalog = {
        "mpc_dynamic": {
            "description": "Model-predictive controller using the regional dynamics predictor over the configured horizon.",
            "returns": ["setpoint", "appliance_actions", "objective_terms", "reason"],
        },
        "rule_milp": {
            "description": "Rule+MILP planner for feasible cost/VPP-aware appliance schedules plus dynamic HVAC setpoint.",
            "returns": ["setpoint", "appliance_actions", "objective_terms", "strategy_options", "reason"],
        },
        "dynamic_hvac": {
            "description": "HVAC-only regional dynamics function; predicts candidate setpoint cost/comfort trajectory.",
            "returns": ["setpoint", "candidate_setpoints", "diagnostics"],
        },
    }
    return (
        "\n[ENERGYBRIDGE AGENT SKILLS]\n"
        "Available callable skills are listed below. Do not call RL here; RL is only a separate baseline. "
        "If you need skill evidence before deciding, return JSON like "
        '{"skill_calls":["mpc_dynamic","dynamic_hvac"],"reason":"why these skills are needed"}. '
        "You may call one skill, several skills, or none. If you already know the best strategy, return the final control JSON directly. "
        "After skill results are returned, you must choose, combine, or reject them yourself and output the final control JSON.\n"
        f"{json.dumps(catalog, ensure_ascii=False)}"
    )


def _requested_agent_skill_names(data: dict | None) -> list[str]:
    if not isinstance(data, dict):
        return []
    raw_calls = data.get("skill_calls", data.get("skills_to_call", data.get("requested_skills", [])))
    if isinstance(raw_calls, str):
        raw_items = [raw_calls]
    elif isinstance(raw_calls, list):
        raw_items = raw_calls
    else:
        return []
    aliases = {
        "mpc": "mpc_dynamic",
        "mpc_dynamic": "mpc_dynamic",
        "rule": "rule_milp",
        "milp": "rule_milp",
        "rule_milp": "rule_milp",
        "rule+milp": "rule_milp",
        "dynamics": "dynamic_hvac",
        "dynamic": "dynamic_hvac",
        "dynamic_hvac": "dynamic_hvac",
        "dynamics_model": "dynamic_hvac",
    }
    requested: list[str] = []
    for item in raw_items:
        key = str(item or "").strip().lower()
        canonical = aliases.get(key)
        if canonical and canonical not in requested:
            requested.append(canonical)
    return requested


def _agent_skill_guidance_text(bundle: dict | None) -> str:
    if not bundle:
        return ""
    skills = {}
    for name, item in (bundle.get("skills") or {}).items():
        if not isinstance(item, dict):
            continue
        if item.get("status") != "available":
            skills[name] = {"status": item.get("status", "error"), "error": item.get("error", "")}
            continue
        entry = {
            "status": "available",
            "setpoint_c": item.get("setpoint"),
            "objective_total": item.get("original_objective_total"),
            "appliance_actions": {
                key: value
                for key, value in (item.get("appliance_actions") or {}).items()
                if value is not None
            },
            "reason": str(item.get("reason", ""))[:180],
        }
        if name == "dynamic_hvac":
            diag = item.get("diagnostics") or {}
            entry["diagnostics"] = {
                "source": diag.get("source"),
                "status": diag.get("status"),
                "model": diag.get("model"),
                "region": diag.get("region"),
                "selected": diag.get("selected"),
                "candidate_setpoints": (diag.get("candidate_setpoints") or [])[:8],
            }
        skills[name] = entry
    compact = {
        "version": bundle.get("version"),
        "requested_skills": bundle.get("requested_skills", []),
        "skills": skills,
        "rule_milp_strategy_options": (bundle.get("rule_milp_options") or {}).get("strategy_options", []),
        "errors": bundle.get("errors", []),
    }
    return (
        "\n[ENERGYBRIDGE AGENT SKILL RESULTS]\n"
        "These are the outputs from only the skills you requested. Choose, combine, or reject them yourself. "
        "Return one final executable control JSON. Include optional `selected_skill` or `skill_selection` if useful for traceability; "
        "the controller will not override your choice with a mechanical objective selector.\n"
        f"{json.dumps(compact, ensure_ascii=False, default=str)}"
    )


def _agent_primary_rule_milp_guidance_text(bundle: dict | None) -> str:
    if not bundle:
        return ""
    return (
        "\n[ENERGYBRIDGE PRIMARY PLAN]\n"
        "Use the Rule+MILP skill as a feasibility and cost/VPP reference, then personalize it using AGENT USER MEMORY. "
        "If onboarding or feedback indicates a cost/grid-oriented user, stay close to Rule+MILP appliance timing and "
        "use the warmest still-acceptable VPP setpoint with a concrete savings/grid explanation. "
        "If onboarding or feedback indicates comfort, confirmation, or calendar sensitivity, treat Rule+MILP as a "
        "reference only: preserve comfort/routines, reduce thermostat drift, keep fixed services unchanged, and choose "
        "the lower-disruption accepted plan even if it diverges from Rule+MILP. "
        "For middle users, keep the Rule+MILP appliance schedule when deadlines and consent are safe, but soften AC "
        "and timing changes around calendar constraints. Never remove present-appliance commands unless feedback or "
        "a hard service constraint requires a safer substitute.\n"
        + _agent_skill_guidance_text(bundle)
    )


def _agent_skill_trace_from_bundle(
    bundle: dict | None,
    *,
    source: str,
    initial_request: dict | None = None,
    final_data: dict | None = None,
    memory_path: str = "",
) -> dict:
    if not bundle:
        return {}
    skill_items = (bundle.get("skills") or {})
    return {
        "source": source,
        "version": bundle.get("version", "energybridge_agent_skills_v1"),
        "requested_skills": bundle.get("requested_skills", []),
        "executed_skills": [
            name
            for name, item in skill_items.items()
            if isinstance(item, dict) and item.get("status") == "available"
        ],
        "skill_errors": bundle.get("errors", []),
        "initial_request": initial_request or {},
        "final_selected_skill": (final_data or {}).get("selected_skill") or (final_data or {}).get("skill_selection"),
        "skill_results": {
            name: {
                "status": item.get("status"),
                "setpoint": item.get("setpoint"),
                "objective_total": item.get("original_objective_total"),
                "appliance_actions": {
                    key: value
                    for key, value in (item.get("appliance_actions") or {}).items()
                    if value is not None
                },
                "reason": str(item.get("reason", ""))[:180],
            }
            for name, item in skill_items.items()
            if isinstance(item, dict)
        },
        "rule_milp_options": (bundle.get("rule_milp_options") or {}),
        "memory_path": memory_path,
    }


# ---------------------------------------------------------------------------
# Appliance actuator write-back helper
# Called each EnergyPlus timestep to push appliance_sim powers into EnergyPlus.
# Design levels match the ElectricEquipment objects added to the IDF.
# ---------------------------------------------------------------------------
_APPL_DESIGN_W = {
    "washer":       2000.0,   # W  (matches ClothesWasher_Appliance)
    "dishwasher":   1500.0,   # W  (matches Dishwasher_Appliance)
    "dryer":        3000.0,   # W  (matches ClothesDryer_Appliance)
    "refrigerator":  200.0,   # W  (matches Refrigerator_Appliance)
    "ev":           7000.0,   # W  (matches EV_Charger)
}

def _write_appliance_actuators(ex, s, loop, powers: dict, sim_h: float) -> None:
    """Write appliance_sim power fractions to EnergyPlus Schedule:Constant actuators.
    Logs state transitions (off→on, on→off) and EWH setpoint changes."""
    if not hasattr(loop, '_appl_prev_state'):
        loop._appl_prev_state = {}   # {name: power_kw}
        loop._ewh_prev_sp = None

    day = int(sim_h // 24) + 1
    hod = sim_h % 24

    for nm, h_attr in [
        ("washer",       "h_washer"),
        ("dishwasher",   "h_dishwasher"),
        ("dryer",        "h_dryer"),
        ("refrigerator", "h_refrigerator"),
    ]:
        h = getattr(loop, h_attr, -1)
        if h != -1:
            design_w = _APPL_DESIGN_W[nm]
            kw = powers.get(nm, 0.0)
            frac = min(1.0, kw * 1000.0 / design_w)
            ex.set_actuator_value(s, h, frac)
            prev = loop._appl_prev_state.get(nm, 0.0)
            if prev == 0.0 and kw > 0.0:
                print(f"  [Appliance ON ] day={day} h={hod:05.2f} {nm}: {kw:.2f} kW -> EnergyPlus frac={frac:.3f}")
            elif prev > 0.0 and kw == 0.0:
                print(f"  [Appliance OFF] day={day} h={hod:05.2f} {nm}: done")
            loop._appl_prev_state[nm] = kw

    # EV charger — fraction of 7 kW design level
    if loop.h_ev != -1:
        ev_kw = powers.get("ev", 0.0)
        frac = min(1.0, ev_kw * 1000.0 / 7000.0)
        ex.set_actuator_value(s, loop.h_ev, frac)
        prev_ev = loop._appl_prev_state.get("ev", 0.0)
        if prev_ev == 0.0 and ev_kw > 0.0:
            print(f"  [Appliance ON ] day={day} h={hod:05.2f} ev: {ev_kw:.2f} kW -> frac={frac:.3f}")
        elif prev_ev > 0.0 and ev_kw == 0.0:
            print(f"  [Appliance OFF] day={day} h={hod:05.2f} ev: charging complete")
        loop._appl_prev_state["ev"] = ev_kw

    # Water heater — temperature setpoint control (per-day schedule from LLM)
    if loop.h_ewh_sp != -1 and loop.appliance_suite is not None:
        wh = loop.appliance_suite._water_heater
        day_idx = int(sim_h // 24)
        state = wh._days.get(day_idx, {})
        # Per-day policy schedule. In explicit-only mode, no command means standby.
        ph_start = state.get("preheat_start_h") or wh.pre_heat_window_start_h
        ph_end   = state.get("preheat_end_h")   or wh.pre_heat_window_end_h
        ph_temp  = state.get("preheat_temp_c")  or 65.0
        if state.get("preheat_requested") and ph_start <= hod < ph_end:
            ewh_sp = ph_temp  # policy-specified preheat temperature
        elif (
            not getattr(wh, "explicit_only", False)
            and not state.get("preheat_requested")
            and wh._normal_on_start <= hod < wh._normal_on_end
        ):
            ewh_sp = 60.0   # normal heating window
        else:
            ewh_sp = 40.0   # standby: setpoint below typical tank temp → heater off
        ex.set_actuator_value(s, loop.h_ewh_sp, ewh_sp)
        if ewh_sp != loop._ewh_prev_sp:
            mode = "PREHEAT" if ewh_sp == 65.0 else ("HEATING" if ewh_sp == 60.0 else "standby")
            print(f"  [EWH setpoint ] day={day} h={hod:05.2f} {loop._ewh_prev_sp}°C -> {ewh_sp}°C ({mode})")
            loop._ewh_prev_sp = ewh_sp

def run_family_agent(idf_path=DEFAULT_FAMILY_IDF, epw_path=DEFAULT_FAMILY_EPW,
                     output_dir=None, weather_label="",
                     user_pref="I want indoor comfort, but I am also willing to save electricity when comfort is not affected.",
                     appliance_config: dict | None = None,
                     persona_config: dict | None = None,
                     verbose: bool = False,
                     human_mode: bool = False,
                     method: str = "agent",
                     mpc_horizon_steps: int = 6,
                     sim_days: int = 3,
                     start_date: str | _date | None = None,
                     day_ahead_price_profile: Any = None,
                     planning_hour: float = DEFAULT_PLANNING_HOUR,
                     vpp_start_h: float = 18.0,
                     vpp_duration_h: float = 1.0,
                     vpp_events_config: list[dict] | None = None,
                     vpp_schedule_source: str = "",
                     pre_event_preference_callback: Any = None,
                     post_event_score_callback: Any = None):
    """Event-driven home control: one VPP event per simulated day.

    ``pre_event_preference_callback`` and ``post_event_score_callback`` are
    optional extension hooks for non-standard role-play flows such as
    independent multi-user households.  When omitted, the single-user
    benchmark path uses the standard role-play scorer unchanged.
    """
    method = (method or "agent").strip().lower()
    if method in ("agent", "energybridge"):
        method = "agent"
    elif method == "mpc":
        method = "mpc_dynamic"
    elif method == "hema_agent":
        method = "hema_agent"
    mpc_horizon_steps = max(1, int(mpc_horizon_steps))
    sim_days = max(1, int(sim_days))
    planning_hour = float(planning_hour) % 24.0
    vpp_start_h = float(vpp_start_h) % 24.0
    vpp_duration_h = max(1e-6, float(vpp_duration_h))
    if isinstance(start_date, str) and start_date:
        run_start_date = _date.fromisoformat(start_date)
    elif isinstance(start_date, _date):
        run_start_date = start_date
    else:
        run_start_date = None
    total_sim_hours = float(sim_days * 24)
    vpp_events = (
        [dict(event) for event in vpp_events_config]
        if vpp_events_config is not None
        else _make_vpp_events(sim_days, start_h=vpp_start_h, duration_h=vpp_duration_h)
    )
    method = {
        "rl": "rl_ppo_pref_v2",
        "rl_ppo": "rl_ppo_pref_v2",
        "rl_ppo_pref_v2": "rl_ppo_pref_v2",
        "rl_pref_v2": "rl_ppo_pref_v2",
        "rl_ppo_v2": "rl_ppo_pref_v2",
        "rule_milp": "rule_milp",
        "rule+milp": "rule_milp",
        "pmv_milp": "rule_milp",
        "eb_rule_milp": "agent",
        "eb+rule+milp": "agent",
        "energybridge_rule_milp": "agent",
        "agent_milp": "agent",
        "agent+milp": "agent",
        "no_dr": "no_dr",
        "none": "no_dr",
        "baseline": "no_dr",
    }.get(str(method or "agent").lower(), method)
    if method not in ("agent", "mpc_dynamic", "rl_ppo_pref_v2", "rule_milp", "no_dr", "hema_agent"):
        raise ValueError(f"Unsupported family control method: {method}")
    if output_dir is None:
        output_dir = BENCHMARK_DIR / "results" / f"family_{method}_{weather_label}"
    output_dir = Path(output_dir)
    if output_dir.exists(): shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    import math as _math
    from pyenergyplus.api import EnergyPlusAPI
    loop = _FamilyLoop(); api = EnergyPlusAPI(); state = api.state_manager.new_state()
    loop.sim_days = sim_days
    loop.weather_label = weather_label
    loop.daily_e_wh = [0.0 for _ in range(sim_days)]
    loop.vpp_events = vpp_events
    loop._rl_v2_daily_scheduled: dict[int, set] = {}  # v2 decision cooldown
    loop.vpp_schedule_source = vpp_schedule_source or "daily_default"
    loop.day_agent_decisions = [[] for _ in range(sim_days)]
    _init_daily_llm_usage(loop, sim_days)
    loop.daily_plans_done = set()
    loop.next_check = planning_hour
    _init_agent_preference_memory(
        loop,
        output_dir,
        method=method,
        persona_config=persona_config,
    )

    def _write_live_snapshot(status: str = "running") -> None:
        try:
            data = {
                "scenario": f"family/{weather_label}",
                "building": "family",
                "weather": weather_label,
                "method": method,
                "sim_days": sim_days,
                "start_date": run_start_date.isoformat() if run_start_date else "",
                "vpp_schedule_source": loop.vpp_schedule_source,
                "live_status": status,
                "daily_trace_rows": _dashboard_trace_rows(loop.power_trace_rows),
                "vpp_event_log": list(loop.vpp_event_log),
                "daily_llm_usage": list(getattr(loop, "daily_llm_usage", [])),
                "output_dir": str(output_dir),
            }
            path = output_dir / "live_snapshot.json"
            tmp = output_dir / "live_snapshot.json.tmp"
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass

    ex = api.exchange
    ex.request_variable(state, "Zone Mean Air Temperature", "living_unit1")
    ex.request_variable(state, "Facility Total Electricity Demand Rate", "Whole Building")
    ex.request_variable(state, "Site Outdoor Air Drybulb Temperature", "Environment")
    ex.request_variable(state, "Zone People Occupant Count", "living_unit1")
    # Initialise per-appliance independent simulator
    try:
        from energybridge.simulation.appliance_sim import ApplianceSuite
        _acfg = appliance_config or {}
        if method == "no_dr":
            # The counterfactual appliance routine must not know VPP windows;
            # otherwise EV/window helpers would avoid them and become DR.
            loop.appliance_suite = ApplianceSuite(_acfg, sim_days=sim_days, vpp_events=[], explicit_only=True)
            _routine_seed = "|".join([
                str((persona_config or {}).get("id", "persona")),
                str(sim_days),
                str(vpp_start_h),
                str(vpp_duration_h),
                "no_dr_random_routine_v1",
            ])
            loop.no_dr_routine_actions = _schedule_no_dr_routine_appliances(
                loop.appliance_suite,
                _acfg,
                sim_days,
                _routine_seed,
            )
            print(
                "  [ApplianceSuite] no-DR random routine loaded: "
                f"{[k for k,v in _acfg.items() if isinstance(v,dict) and v.get('present',True)]}"
            )
        else:
            loop.appliance_suite = ApplianceSuite(_acfg, sim_days=sim_days, vpp_events=vpp_events, explicit_only=True)
            print(f"  [ApplianceSuite] explicit-only loaded: {[k for k,v in _acfg.items() if isinstance(v,dict) and v.get('present',True)]}")
    except Exception as _ae:
        print(f"  [ApplianceSuite] init failed: {_ae}; appliances disabled")
        loop.appliance_suite = None
    try:
        from energybridge.quantification import quantify_agent_vpp_events
        loop.total_quantification_by_id = quantify_agent_vpp_events(vpp_events)
        print("  [Total Quantification] reference A3 90% event capacities loaded")
    except Exception as _tqe:
        print(f"  [Total Quantification] failed: {_tqe}")
        loop.total_quantification_by_id = {}
    print(f"  [VPP Schedule] {describe_vpp_events(vpp_events)}  source={loop.vpp_schedule_source}")

    # ── Read persona AC config (from appliances.ac field in persona JSON) ──────
    _ac_cfg   = (appliance_config or {}).get("ac", {})
    _ac_sp_min    = float(_ac_cfg.get("setpoint_preferred_min_c", 24.0))
    _ac_sp_max    = float(_ac_cfg.get("setpoint_preferred_max_c", 26.0))
    _ac_sp_tol    = float(_ac_cfg.get("temp_tolerance_c", 1.0))
    _ac_sp_default = round((_ac_sp_min + _ac_sp_max) / 2, 1)
    _protective_mode = method == "agent" and _protective_control_mode(persona_config)
    _agent_multi_user_comfort_first = (
        method == "agent" and _multi_user_household_comfort_first_mode(persona_config)
    )
    _auto_saving_mode = _price_sensitive_auto_saving_mode(persona_config)
    _ac_sp_vpp_min = round(_ac_sp_max + 0.5, 1)   # minimum raise during VPP
    _ac_sp_vpp_max = round(_ac_sp_max + 1.5, 1)   # typical VPP raise ceiling
    # Override global SP_MIN based on persona comfort floor
    if method == "agent":
        _run_sp_min = max(SP_MIN, _ac_sp_min - _ac_sp_tol)
        _run_sp_max = min(SP_MAX, _ac_sp_max + 2.0)  # allow VPP raise headroom
    else:
        _run_sp_min = SP_MIN
        _run_sp_max = SP_MAX
    _rule_milp_sp_min = SP_MIN
    _rule_milp_sp_max = AC_OFF_FALLBACK_COOLING_SETPOINT
    _energy_saving_sp_floor = _run_sp_min
    if _protective_mode:
        _run_sp_max = min(_run_sp_max, _ac_sp_max)
        _ac_sp_vpp_min = _ac_sp_default
        _ac_sp_vpp_max = _ac_sp_max
        if (persona_config.get("tags", {}) or {}).get("control") in {"low_auto_accept", "privacy_sensitive"}:
            # Protective users value stability, but stability should not mean
            # unnecessary over-cooling. Stay near the warm half of the approved
            # comfort band unless the user explicitly asks for colder air.
            _energy_saving_sp_floor = max(_energy_saving_sp_floor, min(_ac_sp_max, _ac_sp_max - 0.5))
    elif (
        (persona_config.get("tags", {}) or {}).get("control") == "high_trust_auto"
        and (persona_config.get("tags", {}) or {}).get("comfort") == "normal_comfort"
    ):
        _energy_saving_sp_floor = max(_energy_saving_sp_floor, min(_ac_sp_max, _ac_sp_max - 0.5))

    if method == "agent":
        _vpp_ac_strategy_text = (
            f"AC strategy: use onboarding memory and feedback to choose between comfort-preserving control "
            f"within {_ac_sp_min:.1f}-{_ac_sp_max:.1f}C and a low-disruption VPP drift up to "
            f"{_ac_sp_vpp_max:.1f}C when the user appears likely to accept it. Pre-cool before events only "
            "when it fits the calendar and comfort preference."
        )
    else:
        _vpp_ac_strategy_text = (
            f"Protective strategy: keep setpoint within {_ac_sp_min:.1f}-{_ac_sp_max:.1f}C; do not raise above preferred max for VPP."
            if _protective_mode
            else f"AC strategy: raise setpoint to {_ac_sp_vpp_min:.1f}-{_ac_sp_vpp_max:.1f}C (pre-cool BEFORE event, drift DURING event)."
        )

    _protective_policy = ""
    if _protective_mode and method != "agent":
        _protective_policy = f"""
[PROTECTIVE USER MODE]
This persona has tight comfort bounds, explicit-confirmation needs, vulnerable household members, or very low acceptance of automation.
Treat DR as advisory/minimal-control: keep HVAC within {_ac_sp_min:.1f}-{_ac_sp_max:.1f}°C, do not raise above the preferred max for VPP, and preserve fixed care routines.
If grid goals conflict with comfort, safety, consent, or caregiving routines, choose comfort/safety and explain that only low-risk actions were taken.
"""
    # EnergyBridge must infer the user from onboarding answers, observable
    # calendar context, and feedback. Do not expose hidden persona prompt text.
    _persona_policy = "" if method == "agent" else _persona_agent_policy_text(persona_config)
    _run_location_text = (weather_label or "default").strip() or "default"
    _run_start_text = run_start_date.isoformat() if run_start_date else "simulation day 1"
    _LLM_SYS_FAM = f"""You are an autonomous AC (air conditioning) and appliance agent for a family home.
SIMULATION: {sim_days} days starting {_run_start_text} ({_run_location_text}). Timestep 10 min. Total {int(total_sim_hours)} hours.
You are called at: (1) daily planning at {_fmt_clock_h(planning_hour)}, (2) VPP demand-response events, (3) times you request.

[AC CONTROL]
Home occupancy comes from the role-play calendar written into the EnergyPlus People schedule and exposed as an EnergyPlus output variable.
If occupied=false, the benchmark turns HVAC availability OFF for every method; if occupied=true, HVAC is ON and your selected comfort/VPP setpoint is applied.
User preferred comfort range: {_ac_sp_min:.1f}–{_ac_sp_max:.1f}°C (tolerance ±{_ac_sp_tol:.1f}°C).
Normal setpoint target: ~{_ac_sp_default:.1f}°C. PMV near 0 at 25.5°C; >+0.5 when zone exceeds 27°C.
Allowed setpoint range: {_run_sp_min:.1f}–{_run_sp_max:.1f}°C.
Energy-conscious comfort floor: avoid cooling below {_energy_saving_sp_floor:.1f}°C unless the user explicitly asks for colder air or safety requires it.

[DAY-AHEAD PRICE OBJECTIVE]
When a DAY_AHEAD_PRICE block is provided at the daily planning call, optimize flexible appliance timing and EV charging for lower price-weighted energy cost after comfort, safety, service deadlines, and VPP rules. If no price block is provided, ignore price and use the normal benchmark policy.

[VPP DEMAND RESPONSE]
Goal: reduce total electricity consumption during the provided VPP_WINDOW to support the grid.
The exact event window is provided at runtime as VPP_WINDOW=start-end.
{_vpp_ac_strategy_text}
Appliances: every present independent device needs an explicit policy command (see details below). For flexible devices, choose a schedule. For fixed/non-DR-adjustable devices, emit the user's normal routine command and do not alter it for VPP.
IMPORTANT: control each appliance independently. Decisions persist until you change them. Learn from past VPP event scores.
HARD APPLIANCE RULE: on every VPP day, no present flexible/DR-adjustable appliance may draw controllable load during VPP_WINDOW.
This means flexible washer/dishwasher/dryer cycles must not overlap VPP_WINDOW, flexible water-heater preheat must avoid VPP_WINDOW, and flexible EV charging must use smart/delay or an explicit non-overlapping window.
For fixed/non-DR-adjustable routines, preserve the user's normal command even if that limits VPP capacity; do not force a fixed routine to move only to satisfy the event.
Plan appliance schedules before the event. Waiting until the VPP start time is too late for preheating or long-cycle tasks.
All flexible schedule commands must be executable from the CURRENT clock time. Do not "fix" an active VPP event by assigning a washer/dishwasher/dryer start time in the past; if a flexible cycle was not already safely completed, move it after VPP_WINDOW. For fixed/routine appliances, keep the stated preferred routine and emit it explicitly.
Acceptance matters: a VPP-specific plan that the user rejects falls back to the no-VPP day-ahead plan and contributes no new demand response. Prefer accepted, personalized reductions over aggressive plans that violate questionnaire preferences, calendar routines, or prior feedback.
{_protective_policy}
{_persona_policy}

[APPLIANCE CONTROL — explicit commands & available parameters]

There are no hidden appliance defaults in the simulator. If this JSON does not
emit a concrete command for a present appliance, that appliance will not be
started by code. Record only the command this policy actually chooses.

WASHER / DISHWASHER / DRYER (run once per day)
  Choose a start time inside the user window shown in appliance status.
  The status window's latest_h is the latest FINISH time, not latest start. Therefore latest valid
  start_h = latest_h - duration_h. If dryer duration is 1.5h and window ends 23:00, start at or before 21.5.
  For overnight windows marked (+1d), next-morning start_h is valid only if the cycle finishes before latest_h.
  On VPP days: choose a start time so the full cycle [start_h, start_h+duration_h] does not overlap VPP_WINDOW.
  If the appliance status or constraints say fixed/non-DR-adjustable, emit its preferred routine start_h and skip=false; do not leave it null.
  If the current clock is already at/inside VPP_WINDOW, do not choose a past start_h as a workaround; schedule after VPP_WINDOW unless the appliance status already says the task is finished.
  Service rule: these tasks should normally still be completed the same day.
  Skip is an exception only when the task is truly unnecessary that day. If you choose skip,
  the system may ask you once to confirm; otherwise reschedule instead.
  Parameters:
    washer_start_h      : float  — hour-of-day to start (e.g. 10.0 = 10:00). Allowed window shown in status.
    washer_skip         : bool   — true = do not run today only if the task is genuinely unnecessary.
    (same pattern for dishwasher_start_h, dishwasher_skip, dryer_start_h, dryer_skip)

WATER HEATER (electric tank, thermal storage)
  Emit a preheat schedule if hot water should be prepared for bath time.
  On VPP days: move the preheat window away from VPP_WINDOW, preferably ending about 1 hour before the event.
  If the water heater is fixed/non-DR-adjustable, emit its configured routine preheat window and temperature; do not leave it null.
  Preheat is a short preparation window, not all-day heating. Keep duration near the configured/candidate window and never exceed it by more than about 1 hour.
  At a later replan, do not rewrite water-heater preheat into a time window that has already ended.
  Hotter tank = more thermal storage = less chance of heating during VPP.
  Parameters:
    water_heater_preheat_start_h : float  — hour-of-day to begin preheating.
    water_heater_preheat_end_h   : float  — hour-of-day to stop preheating; avoid VPP_WINDOW on VPP days.
    water_heater_preheat_temp_c  : float  — tank setpoint during preheat, 45–75°C.
    water_heater_preheat         : bool   — true = activate, false = disable (you can omit if setting times).

EV CHARGER (home charger, arrival/departure shown in status)
  On VPP days: set an explicit charge window that does not overlap VPP_WINDOW.
  For EV-constrained users, start charging as soon as VPP_WINDOW ends if needed for departure SOC.
  SOC and arrival time shown in status each step.
  HARD SERVICE RULE: the EV must reach target SOC by the departure/check time. Plan for this at daily planning time.
  Arrival time is not a hard charging constraint in this benchmark: a same-day early-morning window
  such as 0.0-8.0 is valid, including on day 1, and represents charging for today's EV use.
  Parameters:
    ev_mode             : optional "smart"|"delay"|"normal" metadata only; it is not a substitute for a charging window.
    ev_charge_start_h   : float  — override: begin charging at this hour (e.g. 22.0).
    ev_charge_end_h     : float  — override: stop  charging at this hour (e.g. 7.0 = 07:00 next morning).

[VPP STRATEGY EXPLANATION]
When VPP_ACTIVE or VPP_TODAY is present, include a concise `strategy_explanation` object for collaborator review.
Use English only for every text value in EnergyBridge output, including `reason`, `strategy_explanation`,
`selected_skill`, and `skill_selection`; keep field names exactly as specified.
The explanation must say why the VPP request occurs, concrete device actions with amount/time/duration,
protected constraints (comfort, EV SOC, caregiving/routine boundaries, control limits), user opt-out/restore authority,
expected load/compensation benefit without inventing money, and 2-3 alternatives.
Write `natural_language` as 2-3 short user-facing paragraphs, not as a checklist or field concatenation.
Mention the saved preference profile, recent feedback/memory, or routine evidence that explains why this plan fits this user.
For non-VPP calls, set `strategy_explanation` to null.
Do not put the long explanation in `reason`; `reason` remains a compact <=100 char rationale.

You may either return a skill request first, or return final control directly.
Skill request format:
{{"skill_calls": ["mpc_dynamic"|"rule_milp"|"dynamic_hvac"], "reason": "brief why these skills are useful"}}
Use a skill request only when you need those outputs before deciding. After the
controller returns skill results, return final control JSON.

Final control JSON ONLY (no markdown, no explanation):
{{"setpoint": X, "next_check_hour": Y_or_null, "reason": "≤100 chars",
 "appliances": {{
   "washer_start_h": float_or_null_if_absent,
   "washer_skip": bool_or_null_if_absent,
   "dishwasher_start_h": float_or_null_if_absent,
   "dishwasher_skip": bool_or_null_if_absent,
   "dryer_start_h": float_or_null_if_absent,
   "dryer_skip": bool_or_null_if_absent,
   "water_heater_preheat_start_h": float_or_null_if_absent,
   "water_heater_preheat_end_h": float_or_null_if_absent,
   "water_heater_preheat_temp_c": float_or_null_if_absent,
   "water_heater_preheat": bool_or_null_if_absent,
   "ev_mode": null_or_"smart"|"delay"|"normal",
   "ev_charge_start_h": null_or_float,
   "ev_charge_end_h": null_or_float
 }},
 "strategy_explanation": null_or_{{
   "natural_language": "English 2-3 paragraph explanation for the household",
   "why_request": "why this VPP request happens",
   "recommended_actions": [{{"device": "ac|washer|dishwasher|dryer|water_heater|ev", "action": "...", "amount": "...", "duration": "...", "rationale": "..."}}],
   "protected_constraints": ["comfort/control/service constraints protected"],
   "user_control": ["opt out / restore / confirmation authority"],
   "expected_benefit": {{"message": "load/benefit note, no invented money"}},
   "alternatives": [{{"name": "Conservative option", "summary": "...", "tradeoff": "..."}}],
   "structured_control_constraints": {{"vpp_window": "...", "hvac": {{}}, "appliances": {{}}, "hard_constraints": []}},
   "personalization_notes": ["role-specific emphasis"]
 }},
 "selected_skill": null_or_"direct"|"mpc_dynamic"|"rule_milp"|"dynamic_hvac"|"combined",
 "skill_selection": null_or_"short note on how you compared requested skill outputs"
}}
For every PRESENT appliance, appliance fields must be explicit and non-null as described in the runtime prompt.
Use null only for appliances that are absent from the home, or for optional ev_mode metadata.
All times are hour-of-day (0–23.9)."""

    def _llm_trigger(temp, out_t, hod, sim_h, remaining_h, vpp_active=False, vpp_id="",
                     user_pref_input="", facility_w=None, suppress_vpp_context: bool = False):
        import json as _j
        hh = int(hod % 24)
        vpp_event = None if suppress_vpp_context else _find_active_or_upcoming_vpp_event(
            sim_h,
            vpp_id=vpp_id if vpp_active else "",
            vpp_events=vpp_events,
        )
        if vpp_event:
            try:
                vpp_active = bool(
                    vpp_active
                    or float(vpp_event.get("trigger_h", 10**9)) <= float(sim_h) < float(vpp_event.get("end_h", -10**9))
                )
            except Exception:
                pass
        agent_pre_vpp_precool = bool(
            method == "agent"
            and not vpp_active
            and _agent_pre_vpp_precool_event(sim_h, vpp_events=vpp_events) is not None
        )
        agent_post_vpp_restore = bool(
            method == "agent"
            and not vpp_active
            and _agent_post_vpp_restore_event(sim_h, vpp_events=vpp_events) is not None
        )
        vpp_window = _event_window_text(vpp_event) if vpp_event else ""
        prompt_vpp_demand: dict[str, Any] = {}
        if vpp_event:
            _prompt_vid = str(vpp_event.get("id", ""))
            prompt_vpp_demand = dict(loop.vpp_demand_by_id.get(_prompt_vid, {}))
            if not prompt_vpp_demand and vpp_active:
                prompt_vpp_demand = {
                    "target_kwh": getattr(loop, "current_vpp_demand_kwh", 0.0),
                    "target_shed_kw": getattr(loop, "current_vpp_demand_kw", 0.0),
                }
            if not prompt_vpp_demand:
                _prompt_q90 = loop.total_quantification_by_id.get(
                    _prompt_vid,
                    {"status": "not_computed", "reason": "Reference A3 quantification unavailable"},
                )
                prompt_vpp_demand = _call_vpp_demand_agent(_prompt_vid, _prompt_q90)
        prompt_vpp_demand_kw = prompt_vpp_demand.get("target_shed_kw") if prompt_vpp_demand else None
        if vpp_active:
            _dkw = prompt_vpp_demand_kw
            _duration_h = max(1e-6, float(vpp_event.get("end_h", 0.0) - vpp_event.get("trigger_h", 0.0))) if vpp_event else 1.0
            if _dkw:
                _dtag = (
                    f"  Diagnostic capacity reference: possible shed ≈{_dkw:.3f}kW for this {_duration_h:.2f}h window. "
                    "This is not the scoring target."
                )
            else:
                _dtag = ""
            _cap = getattr(loop, "current_vpp_capacity", {}).get("assessment", {})
            _ctag = (
                f" Household capacity assessment: committable={float(_cap.get('committable_kw', 0.0)):.2f}kW,"
                f" recommended_bid={float(_cap.get('recommended_bid_kw', 0.0)):.2f}kW,"
                f" constraints={_cap.get('main_constraints', [])}."
            )
            vpp_tag = (
                f"  *** VPP_ACTIVE (event {vpp_id}, VPP_WINDOW={vpp_window}): "
                "success criterion is no present non-AC appliance scheduled or run inside this window; "
                "AC may be adjusted only within user comfort/consent. "
                f"{_dtag}{_ctag}  User will score comfort, consent, and appliance avoidance. ***"
            )
        else:
            vpp_tag = ""
        upcoming_vpp_tag = ""
        if not vpp_active and vpp_event:
            upcoming_vpp_tag = (
                f"\n  *** VPP_TODAY ({vpp_event['id']}, VPP_WINDOW={vpp_window}): "
                "plan appliances now so no controllable appliance overlaps this window. "
                "Emit explicit VPP-safe schedules: shiftable cycles avoid the window, "
                "water-heater preheat ends before the event when feasible, and EV charging avoids the window. ***"
            )
            if agent_pre_vpp_precool:
                upcoming_vpp_tag += (
                    " EnergyBridge tradeoff mode: prepare for the event using AGENT USER MEMORY. "
                    "Comfort-sensitive users may pre-cool within their range; cost/grid-oriented users should avoid "
                    "unnecessary pre-cooling and use a warmer accepted setpoint during the VPP window."
                )
        # Post-VPP recovery signal: tell LLM to restore setpoint within 2h after VPP ends
        post_vpp_tag = ""
        if not vpp_active:
            for _ev in vpp_events:
                if _ev["end_h"] <= sim_h < _ev["end_h"] + 2.0:
                    post_vpp_tag = (f"\n  *** VPP ENDED (event {_ev['id']}):"
                                    f" RESTORE setpoint to comfort range"
                                    f" ({_ac_sp_min:.1f}-{_ac_sp_max:.1f}\u00b0C) immediately."
                                    f" Normal operations resume. ***")
                    break
        return_home_tag = ""
        if vpp_event and _calendar_return_home_sensitive(persona_config, vpp_event):
            demand_kw = prompt_vpp_demand_kw
            if _low_vpp_target_kw(demand_kw):
                comfort_target = max(_ac_sp_min, min(_ac_sp_max, max(_ac_sp_default, _ac_sp_max - 0.5)))
                return_home_action = f"prefer the normal comfort setpoint around {comfort_target:.1f}°C"
            else:
                return_home_action = f"keep active-event AC setpoint at or below the preferred max ({_ac_sp_max:.1f}°C)"
            return_home_tag = (
                f"\n  *** RETURN_HOME_COMFORT: this VPP window is close to home arrival. "
                f"{return_home_action}, "
                "and restore comfort immediately when the event ends. ***"
            )
        mem_tag = loop.vpp_mem_ctx  # contains past event scores + user feedback
        price_tag = "\nDAY_AHEAD_PRICE: unavailable."
        if run_start_date is not None and day_ahead_price_profile is not None:
            try:
                current_day = run_start_date + _timedelta(days=int(sim_h // 24))
                price_tag = "\n" + day_ahead_price_profile.prompt_context_for_day(current_day)
            except Exception as _pe:
                price_tag = f"\nDAY_AHEAD_PRICE: unavailable ({str(_pe)[:80]})."
        agent_skill_bundle: dict | None = None
        agent_skill_tag = ""
        agent_memory_tag = ""
        if method == "agent":
            agent_skill_tag = _agent_skill_catalog_text()
            agent_memory_tag = _agent_preference_memory_prompt_text(loop)
        benefit_tag = _benefit_explanation_prompt_text(
            persona_config,
            appliance_config,
            vpp_event,
            prompt_vpp_demand_kw,
        )
        learned_efficiency_tag = _learned_efficiency_prompt_text(
            loop.vpp_event_log,
            persona_config,
            default_sp_c=_ac_sp_default,
            preferred_max_c=_ac_sp_max,
        )
        intensity_tag = _vpp_intensity_prompt_text(
            vpp_event,
            prompt_vpp_demand_kw,
        )
        # Current event: user expressed preference before agent acts
        user_now_tag = f"\n[User says NOW]: {user_pref_input}" if user_pref_input else ""
        agent_memory_protective = bool(method == "agent" and _agent_memory_is_protective(loop))
        # Per-appliance status (independent devices)
        if loop.appliance_suite is not None:
            appl_lines = "\n".join(loop.appliance_suite.status_lines(sim_h))
            appl_tag = f"\nAppliances:\n{appl_lines}"
        else:
            appl_tag = ""
        fixed_appliance_tag = _fixed_appliance_constraint_text(appliance_config)
        explicit_appliance_tag = _explicit_appliance_requirement_text(appliance_config, vpp_event=vpp_event)
        occupancy_tag = (
            f"occupancy={'occupied' if loop.current_occupied else 'unoccupied'} "
            f"people_count={loop.current_occupancy_count:.2f} "
            f"source={loop.current_occupancy_source}\n"
        )
        prompt = (f"sim_hour={sim_h:.1f}  clock={hh:02d}:00{vpp_tag}{upcoming_vpp_tag}{post_vpp_tag}{return_home_tag}\n"
                  f"{occupancy_tag}"
                  f"zone_temp={temp:.1f}C  outdoor={out_t:.1f}C\n"
                  f"remaining_sim_hours={remaining_h:.0f}\n"
                  f"user_pref: {user_pref}{user_now_tag}{price_tag}{benefit_tag}{learned_efficiency_tag}{intensity_tag}{appl_tag}{fixed_appliance_tag}{explicit_appliance_tag}{agent_skill_tag}{agent_memory_tag}{mem_tag}")
        if vpp_active:
            fb_sp = min(_run_sp_max, _ac_sp_default if agent_memory_protective or _protective_mode else 26.5)
            fb_nch = None
        else:
            fb_sp = min(_run_sp_max, max(_run_sp_min, min(26.0, round(temp - 0.5, 1))))
            fb_nch = None
        fallback = {
            "setpoint": fb_sp,
            "next_check_hour": fb_nch,
            "appliance_actions": {},
        }
        def _validate_json(text: str) -> str:
            """Strip markdown fences and verify valid JSON; raises on failure."""
            t = text.strip()
            if t.startswith("```"):
                t = "\n".join(l for l in t.splitlines()
                               if not l.strip().startswith("```")).strip()
            if not t:
                raise ValueError("response empty after stripping markdown fences")
            _j.loads(t)  # raises json.JSONDecodeError if not valid JSON
            return t

        def _call_llm_json(prompt_text: str):
            _client = LLMClient()
            # The EnergyBridge controller returns executable JSON plus a short
            # strategy explanation. The default 1024-token project setting can
            # truncate that JSON, so use a local budget for this controller.
            _client.config.max_tokens = max(int(_client.config.max_tokens), 3072)
            _llm_r = _client.chat_with_metrics(
                _LLM_SYS_FAM,
                prompt_text,
                max_retries=5,
                retry_base_delay=2.0,
                validate_fn=_validate_json,
            )
            _m = _llm_r.get("metrics", {})
            _tu = _m.get("token_usage", {})
            loop.llm_calls += 1
            loop.llm_latency_s += _m.get("latency_seconds", 0.0)
            loop.llm_tokens_prompt += _tu.get("prompt_tokens", 0)
            loop.llm_tokens_comp += _tu.get("completion_tokens", 0)
            _record_daily_llm_usage(loop, sim_days, sim_h, _m)
            return _j.loads(_llm_r["text"])

        def _prepare_policy_payload(raw_data: dict) -> tuple[dict, dict]:
            data_out = dict(raw_data or {})
            return data_out, {}

        def _hard_policy_errors(actions: dict | None) -> list[str]:
            errors: list[str] = []
            missing = _missing_explicit_appliance_actions(actions or {}, appliance_config)
            if missing:
                errors.append("missing explicit appliance commands: " + ", ".join(missing))
            errors.extend(
                "shiftable service infeasible: " + item
                for item in _shiftable_service_window_errors(actions or {}, appliance_config)
            )
            if vpp_event:
                errors.extend(
                    "VPP schedule conflict: " + item
                    for item in _vpp_appliance_conflicts(
                        actions or {},
                        appliance_config,
                        vpp_event,
                        current_hod=hod if vpp_active else None,
                    )
                )
            errors.extend(
                "EV service infeasible: " + item
                for item in _ev_service_window_errors(
                    actions or {},
                    appliance_config,
                    vpp_event=vpp_event,
                )
            )
            return errors

        try:
            from energybridge.llm.client import LLMClient
            if method == "agent":
                try:
                    agent_skill_bundle = _build_agent_skill_bundle(
                        loop,
                        sim_h=sim_h,
                        hod=hod,
                        temp=temp,
                        out_t=out_t,
                        facility_w=facility_w,
                        vpp_event=vpp_event,
                        appliance_config=appliance_config or {},
                        price_profile=day_ahead_price_profile,
                        run_start_date=run_start_date,
                        idf_path=idf_path,
                        epw_path=epw_path,
                        mpc_horizon_steps=mpc_horizon_steps,
                        sp_min=_run_sp_min,
                        sp_max=_rule_milp_sp_max,
                        requested_skills=["rule_milp"],
                    )
                    prompt = prompt + _agent_primary_rule_milp_guidance_text(agent_skill_bundle)
                except Exception as _ase:
                    agent_skill_bundle = {
                        "version": "energybridge_agent_skills_v1",
                        "skills": {},
                        "requested_skills": ["rule_milp"],
                        "errors": [str(_ase)[:200]],
                    }
                    prompt = prompt + _agent_primary_rule_milp_guidance_text(agent_skill_bundle)
            if verbose:
                print(f"  ┌─[PROMPT | h={sim_h:.1f} sim / {int(hod%24):02d}:00]{'─'*40}")
                for _line in prompt.splitlines():
                    print(f"  │ {_line}")
                print(f"  └{'─'*56}")
            agent_skill_trace: dict[str, Any] = {}
            force_rule_milp_primary = (
                method == "agent"
                and str(os.getenv("ENERGYBRIDGE_FORCE_RULE_MILP_PRIMARY_NO_LLM", "")).strip().lower()
                in {"1", "true", "yes", "on"}
            )
            if force_rule_milp_primary:
                initial_data = {
                    "skill_calls": ["rule_milp"],
                    "reason": "deterministic historical-memory generation",
                }
                requested_skill_names = []
                data = {
                    "setpoint": fb_sp,
                    "next_check_hour": fb_nch,
                    "appliances": {},
                    "reason": "Rule+MILP primary control selected for deterministic historical-memory generation.",
                    "selected_skill": "rule_milp",
                    "control_source": "rule_milp_primary",
                }
                if agent_skill_bundle is not None:
                    agent_skill_trace = _agent_skill_trace_from_bundle(
                        agent_skill_bundle,
                        source="energybridge_agent_primary_rule_milp_no_llm",
                        initial_request=initial_data,
                        final_data=data,
                        memory_path=str(getattr(loop, "agent_memory_path", "") or ""),
                    )
            else:
                initial_data = _call_llm_json(prompt)
                requested_skill_names = (
                    _requested_agent_skill_names(initial_data)
                    if method == "agent"
                    else []
                )
            if requested_skill_names:
                if method == "agent" and "rule_milp" not in requested_skill_names:
                    requested_skill_names = ["rule_milp", *requested_skill_names]
                print(
                    "  [Agent Skill Call] active skills: "
                    + ", ".join(requested_skill_names)
                )
                existing_requested = set((agent_skill_bundle or {}).get("requested_skills") or [])
                if set(requested_skill_names) != existing_requested:
                    try:
                        agent_skill_bundle = _build_agent_skill_bundle(
                            loop,
                            sim_h=sim_h,
                            hod=hod,
                            temp=temp,
                            out_t=out_t,
                            facility_w=facility_w,
                            vpp_event=vpp_event,
                            appliance_config=appliance_config or {},
                            price_profile=day_ahead_price_profile,
                            run_start_date=run_start_date,
                            idf_path=idf_path,
                            epw_path=epw_path,
                            mpc_horizon_steps=mpc_horizon_steps,
                            sp_min=_run_sp_min,
                            sp_max=_rule_milp_sp_max,
                            requested_skills=requested_skill_names,
                        )
                    except Exception as _ase:
                        agent_skill_bundle = {
                            "version": "energybridge_agent_skills_v1",
                            "skills": {},
                            "requested_skills": requested_skill_names,
                            "errors": [str(_ase)[:200]],
                        }
                skill_result_prompt = (
                    prompt
                    + "\n\n[YOUR SKILL REQUEST]\n"
                    + _j.dumps(initial_data, ensure_ascii=False, default=str)
                    + _agent_primary_rule_milp_guidance_text(agent_skill_bundle)
                    + "\n\nReturn the final control JSON now. You may choose one skill output, combine skill output with your own appliance plan, "
                    "or make small user-feedback adjustments to the Rule+MILP base plan. Do not return another skill request."
                )
                data = _call_llm_json(skill_result_prompt)
                agent_skill_trace = _agent_skill_trace_from_bundle(
                    agent_skill_bundle,
                    source="energybridge_agent_llm_skill_calls",
                    initial_request=initial_data,
                    final_data=data,
                    memory_path=str(getattr(loop, "agent_memory_path", "") or ""),
                )
            elif not force_rule_milp_primary:
                data = initial_data
                if method == "agent" and agent_skill_bundle is not None:
                    agent_skill_trace = _agent_skill_trace_from_bundle(
                        agent_skill_bundle,
                        source="energybridge_agent_primary_rule_milp",
                        initial_request={"skill_calls": ["rule_milp"], "reason": "default EnergyBridge primary plan"},
                        final_data=data,
                        memory_path=str(getattr(loop, "agent_memory_path", "") or ""),
                    )
            data, _agent_payload_trace = _prepare_policy_payload(data)
            rule_skill = {}
            def _apply_agent_rule_milp_primary(data_in: dict) -> dict:
                if rule_skill.get("status") != "available":
                    return data_in
                data_out = dict(data_in or {})
                rule_actions = dict(rule_skill.get("appliance_actions") or {})
                rule_setpoint = rule_skill.get("setpoint", data_out.get("setpoint", fb_sp))
                next_check_hour = rule_skill.get("next_check_hour")
                agent_vpp_tradeoff_sp = _agent_vpp_tradeoff_setpoint_c(
                    loop,
                    ac_sp_max_c=_ac_sp_max,
                    ac_sp_tol_c=_ac_sp_tol,
                    run_sp_max_c=_run_sp_max,
                    protective_mode=_protective_mode,
                    multi_user_comfort_first=_agent_multi_user_comfort_first,
                )
                if vpp_active:
                    rule_setpoint = agent_vpp_tradeoff_sp
                    rule_skill["setpoint"] = agent_vpp_tradeoff_sp
                    rule_skill["reason"] = (
                        f"{rule_skill.get('reason', 'rule_milp')} | active VPP low-power drift after pre-cooling"
                    )[:240]
                elif agent_pre_vpp_precool and vpp_event:
                    rule_setpoint = _agent_pre_vpp_setpoint_c(
                        loop,
                        ac_sp_min_c=_ac_sp_min,
                        ac_sp_max_c=_ac_sp_max,
                        ac_sp_default_c=_ac_sp_default,
                        run_sp_max_c=_run_sp_max,
                        protective_mode=_protective_mode,
                        multi_user_comfort_first=_agent_multi_user_comfort_first,
                    )
                    next_check_hour = float(vpp_event.get("trigger_h", sim_h + 1.0))
                    rule_skill["reason"] = (
                        f"{rule_skill.get('reason', 'rule_milp')} | pre-event EB comfort/efficiency preparation"
                    )[:240]
                elif agent_post_vpp_restore:
                    rule_setpoint = _ac_sp_default
                    next_check_hour = None
                    rule_skill["reason"] = (
                        f"{rule_skill.get('reason', 'rule_milp')} | restore comfort after VPP"
                    )[:240]
                data_out["setpoint"] = rule_setpoint
                data_out["next_check_hour"] = next_check_hour
                data_out["appliances"] = rule_actions
                data_out["selected_skill"] = "rule_milp"
                data_out["control_source"] = "rule_milp_primary"
                data_out["reason"] = english_only_text(
                    data_out.get("reason"),
                    default="Rule+MILP control selected; explanation personalized for the user.",
                )
                return data_out

            if method == "agent":
                rule_skill = ((agent_skill_bundle or {}).get("skills") or {}).get("rule_milp") or {}
                if rule_skill.get("status") == "available":
                    data = _apply_agent_rule_milp_primary(data)
            hard_errors = _hard_policy_errors(data.get("appliances", {}))
            if hard_errors and force_rule_milp_primary:
                print(
                    "  [Agent Policy Retry] deterministic rule+MILP mode keeps primary plan despite: "
                    + "; ".join(hard_errors)
                )
            elif hard_errors and not vpp_active:
                print("  [Agent Policy Retry] hard policy errors: " + "; ".join(hard_errors))
                correction_prompt = (
                    prompt
                    + "\n\n[HARD POLICY ERROR IN YOUR PREVIOUS JSON]\n"
                    + "\n".join(f"- {err}" for err in hard_errors)
                    + "\nReturn a corrected JSON only. Do not apologize. "
                    "Keep every present appliance explicit. For EV, use a same-day non-VPP window "
                    "long enough to reach target SOC; early-morning windows such as 0.0-8.0 are valid.\n"
                    + "[PREVIOUS JSON]\n"
                    + _j.dumps(data, ensure_ascii=False)
                )
                retry_data, retry_trace = _prepare_policy_payload(_call_llm_json(correction_prompt))
                retry_errors = _hard_policy_errors(retry_data.get("appliances", {}))
                if not retry_errors or len(retry_errors) <= len(hard_errors):
                    data = retry_data
                    hard_errors = retry_errors
                    if retry_errors:
                        print("  [Agent Policy Retry] corrected response still has: " + "; ".join(retry_errors))
                    else:
                        print("  [Agent Policy Retry] corrected response passed hard appliance checks")
                if method == "agent" and rule_skill.get("status") == "available":
                    data = _apply_agent_rule_milp_primary(data)
            missing_explicit = _missing_explicit_appliance_actions(
                data.get("appliances", {}), appliance_config
            )
            if missing_explicit:
                print(
                    "  [Service Rule] missing explicit appliance commands; no repair/default will be inserted for: "
                    f"{', '.join(missing_explicit)}"
                )
            initial_skip_devices = _requested_skip_devices(data.get("appliances", {}))
            if initial_skip_devices:
                print(
                    "  [Service Rule] skip requested for "
                    f"{', '.join(initial_skip_devices)}; keeping policy-emitted skip unchanged"
                )
            if vpp_event:
                vpp_conflicts = _vpp_appliance_conflicts(
                    data.get("appliances", {}),
                    appliance_config,
                    vpp_event,
                    current_hod=hod if vpp_active else None,
                )
                if vpp_conflicts:
                    print(
                        "  [VPP Appliance Rule] schedule conflicts; keeping policy-emitted appliance commands unchanged: "
                        f"{'; '.join(vpp_conflicts)}"
                    )
            rule_milp_direct_control = bool(
                method == "agent" and data.get("control_source") == "rule_milp_primary"
            )
            sp_upper = _run_sp_max
            if vpp_event and method == "agent" and agent_memory_protective:
                sp_upper = min(sp_upper, _ac_sp_max)
            elif vpp_event and method != "agent" and _low_dr_intrusion_sensitive_mode(persona_config):
                sp_upper = min(sp_upper, _ac_sp_max)
            if vpp_active and not rule_milp_direct_control:
                demand_kw = float(getattr(loop, "current_vpp_demand_kw", 0.0) or 0.0)
                low_target_unoccupied_warm = False
                if method == "agent":
                    fragile_comfort = bool(agent_memory_protective)
                else:
                    _tags = (persona_config.get("tags", {}) or {})
                    _sched = persona_config.get("schedule", {}) or {}
                    fragile_comfort = (
                        _tags.get("comfort") == "temp_sensitive"
                        or _tags.get("control") in {"low_auto_accept", "privacy_sensitive"}
                        or bool(_sched.get("vulnerable_members"))
                    )
                if _calendar_return_home_sensitive(persona_config, vpp_event):
                    if _low_vpp_target_kw(demand_kw):
                        # Tiny return-home events should avoid noticeable
                        # discomfort.  Fragile users stay near the normal
                        # setpoint; normal-comfort users may still use the
                        # upper edge of their explicitly preferred range and
                        # restore immediately after the event.
                        if fragile_comfort:
                            comfort_target = max(_ac_sp_min, min(_ac_sp_max, max(_ac_sp_default, _ac_sp_max - 0.5)))
                        else:
                            comfort_target = _ac_sp_max
                        sp_upper = min(sp_upper, max(_run_sp_min, comfort_target))
                        sp_lower = min(_energy_saving_sp_floor, sp_upper)
                    else:
                        sp_upper = min(sp_upper, _ac_sp_max)
                elif _low_vpp_target_kw(demand_kw) and not _calendar_occupied_or_return_home_sensitive(persona_config, vpp_event):
                    low_target_unoccupied_warm = True
                    if demand_kw <= 0.0:
                        efficient_target = min(_run_sp_max, _ac_sp_vpp_min)
                    else:
                        efficient_target = min(_run_sp_max, _ac_sp_max)
                    sp_lower = max(_energy_saving_sp_floor, efficient_target)
                low_dr_sensitive = (
                    agent_memory_protective
                    if method == "agent"
                    else _low_dr_intrusion_sensitive_mode(persona_config)
                )
                if low_dr_sensitive:
                    if demand_kw > 0.0 and not fragile_comfort:
                        sp_lower = max(
                            locals().get("sp_lower", _energy_saving_sp_floor),
                            min(_ac_sp_max, sp_upper),
                        )
                    if demand_kw <= 0.5:
                        # Low-disruption users need consent/routine protection, not
                        # necessarily colder-than-preferred HVAC.  Only truly
                        # fragile comfort/privacy users stay pinned near the
                        # normal setpoint; rigid/confirm-required users may use
                        # the warm edge of their stated preferred range.
                        small_target_cap = max(
                            _energy_saving_sp_floor,
                            _ac_sp_default if fragile_comfort else _ac_sp_max,
                        )
                        sp_upper = min(sp_upper, small_target_cap)
                if method == "agent" and agent_memory_protective:
                    sp_upper = min(sp_upper, _ac_sp_max)
                elif method != "agent" and _protective_mode:
                    sp_upper = min(sp_upper, _ac_sp_max)
                elif method != "agent" and (persona_config.get("tags", {}) or {}).get("control") == "high_trust_auto":
                    comfort_tag = (persona_config.get("tags", {}) or {}).get("comfort")
                    high_trust_cap = _ac_sp_max
                    if low_target_unoccupied_warm:
                        high_trust_cap = max(_ac_sp_max, min(_run_sp_max, _ac_sp_vpp_min))
                    elif comfort_tag == "normal_comfort" and not _auto_saving_mode:
                        high_trust_cap = max(_ac_sp_min, _ac_sp_max - 0.5)
                    sp_upper = min(sp_upper, high_trust_cap)
            raw_sp = float(data.get("setpoint", fb_sp))
            sp_lower = locals().get("sp_lower", _energy_saving_sp_floor)
            if rule_milp_direct_control:
                sp_lower = _run_sp_min
                sp_upper = _rule_milp_sp_max
                if agent_pre_vpp_precool:
                    pre_vpp_target = _agent_pre_vpp_setpoint_c(
                        loop,
                        ac_sp_min_c=_ac_sp_min,
                        ac_sp_max_c=_ac_sp_max,
                        ac_sp_default_c=_ac_sp_default,
                        run_sp_max_c=_run_sp_max,
                        protective_mode=_protective_mode,
                        multi_user_comfort_first=_agent_multi_user_comfort_first,
                    )
                    sp_lower = max(_run_sp_min, min(_run_sp_max, pre_vpp_target))
                    sp_upper = sp_lower
                elif agent_post_vpp_restore:
                    sp_upper = max(_run_sp_min, min(_run_sp_max, _ac_sp_default))
                elif not vpp_active:
                    sp_upper = min(sp_upper, _ac_sp_max)
                    feedback_floor = _agent_rule_milp_hvac_feedback_adjustment_c(
                        loop.vpp_event_log,
                        preferred_max_c=_ac_sp_max,
                        run_sp_min_c=_run_sp_min,
                        run_sp_max_c=_run_sp_max,
                    )
                    if feedback_floor is not None:
                        efficient_floor = feedback_floor
                    else:
                        efficient_floor = min(_run_sp_max, _ac_sp_max)
                    sp_lower = max(sp_lower, efficient_floor)
            else:
                learned_floor = _learned_efficiency_floor_c(
                    loop.vpp_event_log,
                    persona_config,
                    default_sp_c=_ac_sp_default,
                    preferred_max_c=_ac_sp_max,
                    vpp_active=bool(vpp_active),
                )
                if learned_floor is not None:
                    sp_lower = max(sp_lower, learned_floor)
            if sp_lower > sp_upper:
                sp_lower = sp_upper
            sp = round(max(sp_lower, min(sp_upper, raw_sp)), 1)
            if sp < raw_sp:
                suffix = f" | comfort cap {sp_upper:.1f}C"
                data["reason"] = (str(data.get("reason", ""))[: max(0, 100 - len(suffix))] + suffix)[:100]
            elif sp > raw_sp:
                suffix = f" | efficient floor {sp_lower:.1f}C"
                data["reason"] = (str(data.get("reason", ""))[: max(0, 100 - len(suffix))] + suffix)[:100]
            nch = data.get("next_check_hour")
            if nch is not None:
                nch = float(nch)
                if nch <= sim_h + 0.25 or nch > total_sim_hours:
                    nch = None
            reason = _comfort_reason_for_low_dr_user(str(data.get("reason", "")), persona_config)
            reason = _ensure_price_sensitive_reason_estimate(
                reason,
                persona_config,
                appliance_config,
                vpp_event,
                prompt_vpp_demand_kw,
            )
            reason = english_only_text(
                reason,
                default="VPP control within comfort and service constraints" if vpp_event else "Comfort control within user limits",
            )[:100]
            # --- Independent appliance commands from LLM ---
            appl_actions = _filter_controllable_appliance_actions(
                data.get("appliances", {}), appliance_config
            )
            if method == "agent" and vpp_event:
                appl_actions = _agent_refine_vpp_appliance_actions(
                    appl_actions,
                    loop,
                    hod=hod,
                    event=vpp_event,
                    appliance_config=appliance_config,
                )
            data["appliances"] = appl_actions
            strategy_explanation = {}
            if vpp_event:
                _exp_vid = str(vpp_event.get("id", ""))
                _capacity_context = (
                    loop.vpp_capacity_by_id.get(_exp_vid, {})
                    or getattr(loop, "current_vpp_capacity", {})
                    or {}
                )
                _raw_strategy_explanation = data.get("strategy_explanation")
                if method == "agent" and rule_milp_direct_control:
                    _raw_strategy_explanation = None
                strategy_explanation = normalize_vpp_strategy_explanation(
                    _raw_strategy_explanation,
                    persona_config=persona_config,
                    appliance_config=appliance_config,
                    event=vpp_event,
                    setpoint_c=sp,
                    reason=reason,
                    appliance_actions=appl_actions,
                    day_decisions=loop.day_agent_decisions[min(sim_days - 1, int(sim_h // 24))],
                    demand_context=prompt_vpp_demand,
                    capacity_context=_capacity_context,
                    method=method,
                    city=weather_label or "",
                    source="family_energybridge_agent_skills" if agent_skill_trace else "family_llm_agent",
                )
                strategy_explanation = finalize_vpp_participation_explanation(strategy_explanation)
            day_num = int(sim_h // 24) + 1
            hh_mm = f"{int(hod % 24):02d}:{int((hod % 1)*60):02d}"
            vpp_tag = f" | VPP-{vpp_id}" if vpp_active else ""
            nch_str = f"{int(nch % 24):02d}:{int((nch % 1)*60):02d}" if nch is not None else "--:--"
            print(f"  [AC Agent | h={hh_mm} Day{day_num}{vpp_tag}] setpoint→{sp:.1f}°C  next_check={nch_str}  | {reason}")
            if verbose:
                import json as _jj
                print(f"  ┌─[LLM JSON response]{'─'*47}")
                for _line in _jj.dumps(data, ensure_ascii=False, indent=2).splitlines():
                    print(f"  │ {_line}")
                print(f"  └{'─'*56}")
            result = {"setpoint": sp, "next_check_hour": nch, "reason": reason,
                    "appliance_actions": appl_actions if isinstance(appl_actions, dict) else {}}
            if strategy_explanation:
                result["strategy_explanation"] = strategy_explanation
            if agent_skill_trace:
                result["strategy_trace"] = {
                    **agent_skill_trace,
                    "agent_actions": appl_actions if isinstance(appl_actions, dict) else {},
                    "agent_setpoint": sp,
                }
            return result
        except Exception as e:
            print(f"  [FamilyAgent] LLM error at h={sim_h:.1f}: {e}")
            loop.llm_failures += 1
            fallback["reason"] = ""
            if vpp_event:
                fallback["strategy_explanation"] = normalize_vpp_strategy_explanation(
                    None,
                    persona_config=persona_config,
                    appliance_config=appliance_config,
                    event=vpp_event,
                    setpoint_c=fallback.get("setpoint"),
                    reason="",
                    appliance_actions=fallback.get("appliance_actions", {}),
                    day_decisions=loop.day_agent_decisions[min(sim_days - 1, int(sim_h // 24))],
                    demand_context=prompt_vpp_demand,
                    capacity_context=getattr(loop, "current_vpp_capacity", {}) or {},
                    method=method,
                    city=weather_label or "",
                    source="family_llm_fallback",
                )
                fallback["strategy_explanation"] = finalize_vpp_participation_explanation(
                    fallback["strategy_explanation"]
                )
            return fallback

    def _mpc_trigger(temp, out_t, hod, sim_h, facility_w=None, vpp_event=None):
        from experiments.benchmark.baselines.mpc import plan_mpc_action

        state_dict = _build_decision_time_state(
            loop,
            sim_h=sim_h,
            hod=hod,
            temp=temp,
            out_t=out_t,
            facility_w=facility_w,
            vpp_event=vpp_event,
            # MPC should react to the VPP window itself, but not to the
            # capacity-quantification demand target used for reporting.
            vpp_target_kwh=None,
            appliance_config=appliance_config or {},
        )
        state_dict["mpc_predictor"] = "dynamic"
        state_dict["mpc_horizon_steps"] = mpc_horizon_steps
        state_dict["idf_path"] = str(idf_path)
        state_dict["epw_path"] = str(epw_path)
        state_dict["mpc_decision_history"] = [
            item
            for day_items in loop.day_agent_decisions
            for item in day_items
            if item.get("h", 10**9) < sim_h
        ]
        decision = plan_mpc_action(state=state_dict)
        sp = round(max(SP_MIN, min(28.0, float(decision.get("setpoint", loop.sp)))), 1)
        objective_terms = decision.get("objective_terms", {})
        total = objective_terms.get("total")
        total_str = f"{float(total):.3f}" if total is not None else "n/a"
        day_num = int(sim_h // 24) + 1
        hh_mm = f"{int(hod % 24):02d}:{int((hod % 1)*60):02d}"
        vpp_tag = f" | VPP-{vpp_event.get('id')}" if isinstance(vpp_event, dict) else ""
        print(
            f"  [MPC Agent | h={hh_mm} Day{day_num}{vpp_tag}] "
            f"setpoint->{sp:.1f}C  objective={total_str}  | "
            f"{decision.get('reason', '')}"
        )
        return {
            "setpoint": sp,
            "next_check_hour": decision.get("next_check_hour"),
            "reason": decision.get("reason", ""),
            "appliance_actions": decision.get("appliances", {}),
            "objective_terms": objective_terms,
            "objective_source": "mpc_candidate_scoring_pdf_v15",
        }

    def _hema_trigger(temp, out_t, hod, sim_h, remaining_h, vpp_active=False, vpp_id="",
                      user_pref_input="", appliance_config=None, vpp_event=None,
                      persona_config=None, facility_w=None):
        """Invoke HEMA Control Agent as a baseline controller."""

        if not hasattr(loop, "_hema_controller"):
            from experiments.benchmark.baselines.hema import get_hema_controller
            HEMAControlBaseline = get_hema_controller()
            loop._hema_controller = HEMAControlBaseline(
                city=weather_label,
                persona_id=(persona_config or {}).get("id", "unknown"),
                persona_config=persona_config,
            )

        eplus_state = {
            "zone_air_temp_c": float(temp) if temp is not None else 24.0,
            "outdoor_temp_c": float(out_t) if out_t is not None else 30.0,
            "current_setpoint_c": float(getattr(loop, "sp", SP_DEFAULT)),
            "facility_power_w": float(facility_w) if facility_w is not None else 0.0,
            "vpp_demand_kw": float(getattr(loop, "current_vpp_demand_kw", 0.0)),
        }

        price_tag_text = ""
        if run_start_date is not None and day_ahead_price_profile is not None:
            try:
                current_day = run_start_date + _timedelta(days=int(sim_h // 24))
                price_tag_text = day_ahead_price_profile.prompt_context_for_day(current_day)
            except Exception:
                price_tag_text = ""
        price_ctx = {
            "has_price": day_ahead_price_profile is not None,
            "price_text": price_tag_text,
        }

        current_time = {"sim_h": sim_h, "hod": hod}
        try:
            control_intent = loop._hema_controller.decide(
                current_time=current_time,
                eplus_state=eplus_state,
                vpp_event=vpp_event,
                price_context=price_ctx,
                appliance_config=appliance_config,
                user_pref=user_pref_input,
            )

            if "llm_metrics" in control_intent:
                m = control_intent.pop("llm_metrics")
                loop.llm_calls += 1
                loop.llm_latency_s += m.get("latency_seconds", 0.0)
                loop.llm_tokens_prompt += m.get("prompt_tokens", 0)
                loop.llm_tokens_comp += m.get("completion_tokens", 0)
                _record_daily_llm_usage(loop, sim_days, sim_h, m)
            else:
                loop.llm_calls += 1

            raw_sp = float(control_intent.get("setpoint", getattr(loop, "sp", SP_DEFAULT)))
            sp_lower = _run_sp_min
            sp_upper = _run_sp_max
            if vpp_event and _low_dr_intrusion_sensitive_mode(persona_config):
                sp_upper = min(sp_upper, _ac_sp_max)
            if vpp_active:
                demand_kw = float(getattr(loop, "current_vpp_demand_kw", 0.0) or 0.0)
                low_target_unoccupied_warm = False
                _tags = (persona_config.get("tags", {}) or {})
                _sched = persona_config.get("schedule", {}) or {}
                fragile_comfort = (
                        _tags.get("comfort") == "temp_sensitive"
                        or _tags.get("control") in {"low_auto_accept", "privacy_sensitive"}
                        or bool(_sched.get("vulnerable_members"))
                )
                if _calendar_return_home_sensitive(persona_config, vpp_event):
                    if _low_vpp_target_kw(demand_kw):
                        # Tiny return-home events should avoid noticeable
                        # discomfort.  Fragile users stay near the normal
                        # setpoint; normal-comfort users may still use the
                        # upper edge of their explicitly preferred range and
                        # restore immediately after the event.
                        if fragile_comfort:
                            comfort_target = max(_ac_sp_min, min(_ac_sp_max, max(_ac_sp_default, _ac_sp_max - 0.5)))
                        else:
                            comfort_target = _ac_sp_max
                        sp_upper = min(sp_upper, max(_run_sp_min, comfort_target))
                        sp_lower = min(_energy_saving_sp_floor, sp_upper)
                    else:
                        sp_upper = min(sp_upper, _ac_sp_max)
                elif _low_vpp_target_kw(demand_kw) and not _calendar_occupied_or_return_home_sensitive(persona_config,
                                                                                                       vpp_event):
                    low_target_unoccupied_warm = True
                    if demand_kw <= 0.0:
                        efficient_target = min(_run_sp_max, _ac_sp_vpp_min)
                    else:
                        efficient_target = min(_run_sp_max, _ac_sp_max)
                    sp_lower = max(_energy_saving_sp_floor, efficient_target)
                if _low_dr_intrusion_sensitive_mode(persona_config):
                    if demand_kw > 0.0 and not fragile_comfort:
                        sp_lower = max(
                            locals().get("sp_lower", _energy_saving_sp_floor),
                            min(_ac_sp_max, sp_upper),
                        )
                    if demand_kw <= 0.5:
                        # Low-disruption users need consent/routine protection, not
                        # necessarily colder-than-preferred HVAC.  Only truly
                        # fragile comfort/privacy users stay pinned near the
                        # normal setpoint; rigid/confirm-required users may use
                        # the warm edge of their stated preferred range.
                        small_target_cap = max(
                            _energy_saving_sp_floor,
                            _ac_sp_default if fragile_comfort else _ac_sp_max,
                        )
                        sp_upper = min(sp_upper, small_target_cap)
                if _protective_mode:
                    sp_upper = min(sp_upper, _ac_sp_max)
                elif (persona_config.get("tags", {}) or {}).get("control") == "high_trust_auto":
                    comfort_tag = (persona_config.get("tags", {}) or {}).get("comfort")
                    high_trust_cap = _ac_sp_max
                    if low_target_unoccupied_warm:
                        high_trust_cap = max(_ac_sp_max, min(_run_sp_max, _ac_sp_vpp_min))
                    elif comfort_tag == "normal_comfort" and not _auto_saving_mode:
                        high_trust_cap = max(_ac_sp_min, _ac_sp_max - 0.5)
                    sp_upper = min(sp_upper, high_trust_cap)
            learned_floor = _learned_efficiency_floor_c(
                loop.vpp_event_log,
                persona_config,
                default_sp_c=_ac_sp_default,
                preferred_max_c=_ac_sp_max,
                vpp_active=bool(vpp_active),
            )
            raw_sp = float(control_intent.get("setpoint", getattr(loop, "sp", SP_DEFAULT)))
            sp_lower = locals().get("sp_lower", _energy_saving_sp_floor)
            if learned_floor is not None:
                sp_lower = max(sp_lower, learned_floor)
            if sp_lower > sp_upper:
                sp_lower = sp_upper
            sp = round(max(sp_lower, min(sp_upper, raw_sp)), 1)

            raw_appl = dict(control_intent.get("appliance_actions", {}) or {})
            appl_actions = _filter_controllable_appliance_actions(raw_appl, appliance_config)

            if vpp_event:
                conflicts = _vpp_appliance_conflicts(
                    appl_actions,
                    appliance_config,
                    vpp_event,
                    current_hod=hod if vpp_active else None,
                )
                if conflicts:
                    print(f"  [HEMA VPP Check] conflicts remain after guard: {'; '.join(conflicts)}")

            missing_explicit = _missing_explicit_appliance_actions(appl_actions, appliance_config)
            if missing_explicit:
                print(
                    "  [HEMA Service Rule] missing explicit appliance commands for: "
                    f"{', '.join(missing_explicit)}"
                )

            res = {
                "setpoint": sp,
                "next_check_hour": control_intent.get("next_check_hour"),
                "reason": "",
                "appliance_actions": appl_actions,
            }

            try:
                res["objective_terms_posthoc"] = _compute_posthoc_decision_objective(
                    loop,
                    action_result=res,
                    sim_h=sim_h,
                    hod=hod,
                    temp=temp,
                    out_t=out_t,
                    facility_w=facility_w,
                    vpp_event=vpp_event if vpp_active else None,
                    vpp_target_kwh=loop.current_vpp_demand_kwh if vpp_active else None,
                    appliance_config=appliance_config or {},
                )
                res["objective_source"] = "posthoc_agent_decision_time_pdf_v15"
            except Exception as _oe:
                print(f"  [HEMA Objective] posthoc error: {_oe}")

            return res

        except Exception as e:
            print(f"  [HEMA Control] error at h={sim_h:.1f}: {e}")
            loop.llm_failures += 1
            return {
                "setpoint": getattr(loop, "sp", SP_DEFAULT),
                "next_check_hour": None,
                "reason": f"HEMA fallback: {str(e)[:60]}",
                "appliance_actions": {},
            }

    def _rule_milp_reference_plan(temp, out_t, hod, sim_h, facility_w=None, vpp_event=None):
        """Build a Rule+MILP comparator plan without applying or printing it."""
        try:
            from experiments.benchmark.baselines.rule_milp import plan_rule_milp_action

            state_dict = _build_decision_time_state(
                loop,
                sim_h=sim_h,
                hod=hod,
                temp=temp,
                out_t=out_t,
                facility_w=facility_w,
                vpp_event=vpp_event,
                vpp_target_kwh=None,
                appliance_config=appliance_config or {},
            )
            state_dict["standalone_baseline"] = True
            decision = plan_rule_milp_action(
                state=state_dict,
                price_profile=day_ahead_price_profile,
                run_start_date=run_start_date,
            )
            return {
                "setpoint": round(
                    max(_rule_milp_sp_min, min(_rule_milp_sp_max, float(decision.get("setpoint", loop.sp)))),
                    1,
                ),
                "next_check_hour": decision.get("next_check_hour"),
                "reason": decision.get("reason", ""),
                "appliance_actions": _filter_controllable_appliance_actions(
                    decision.get("appliances", {}), appliance_config
                ),
                "objective_terms": decision.get("objective_terms", {}),
                "objective_source": "rule_milp_cost_min_v1",
            }
        except Exception as exc:
            return {
                "setpoint": getattr(loop, "sp", SP_DEFAULT),
                "next_check_hour": None,
                "reason": f"rule_milp_reference_unavailable: {str(exc)[:80]}",
                "appliance_actions": {},
                "objective_source": "rule_milp_reference_unavailable",
            }

    def _rule_milp_trigger(temp, out_t, hod, sim_h, facility_w=None, vpp_event=None):
        from experiments.benchmark.baselines.rule_milp import plan_rule_milp_action

        state_dict = _build_decision_time_state(
            loop,
            sim_h=sim_h,
            hod=hod,
            temp=temp,
            out_t=out_t,
            facility_w=facility_w,
            vpp_event=vpp_event,
            vpp_target_kwh=None,
            appliance_config=appliance_config or {},
        )
        state_dict["standalone_baseline"] = True
        decision = plan_rule_milp_action(
            state=state_dict,
            price_profile=day_ahead_price_profile,
            run_start_date=run_start_date,
        )
        sp = round(max(_rule_milp_sp_min, min(_rule_milp_sp_max, float(decision.get("setpoint", loop.sp)))), 1)
        appliance_actions = _filter_controllable_appliance_actions(
            decision.get("appliances", {}), appliance_config
        )
        objective_terms = decision.get("objective_terms", {})
        total = objective_terms.get("total")
        total_str = f"{float(total):.3f}" if total is not None else "n/a"
        day_num = int(sim_h // 24) + 1
        hh_mm = f"{int(hod % 24):02d}:{int((hod % 1)*60):02d}"
        vpp_tag = f" | VPP-{vpp_event.get('id')}" if isinstance(vpp_event, dict) else ""
        print(
            f"  [Rule+MILP | h={hh_mm} Day{day_num}{vpp_tag}] "
            f"setpoint->{sp:.1f}C  objective={total_str}  | "
            f"{decision.get('reason', '')}"
        )
        return {
            "setpoint": sp,
            "next_check_hour": decision.get("next_check_hour"),
            "reason": decision.get("reason", ""),
            "appliance_actions": appliance_actions,
            "objective_terms": objective_terms,
            "objective_source": "rule_milp_cost_min_v1",
        }

    def _rl_pref_v2_trigger(temp, out_t, hod, sim_h, facility_w=None, vpp_event=None):
        """RL PPO Pref-v2: 8-dim action with price, preference, and cooldown."""
        # NOTE: EnergyPlus ctypes callbacks run in a context where sys.path /
        # sys.modules changes made at process start are not visible. Force-load
        # environment_pref_v2 via absolute file path before any normal import,
        # so subsequent `from baselines... import ...` resolves via sys.modules.
        import os as _os, sys as _sys, importlib.util as _ilu
        _MOD_KEY = "baselines.rl_energyplus.environment_pref_v2"
        if _MOD_KEY not in _sys.modules:
            _proj_root = _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
            _ev_path = _os.path.join(_proj_root, "baselines", "rl_energyplus", "environment_pref_v2.py")
            if _proj_root not in _sys.path:
                _sys.path.insert(0, _proj_root)
            _spec = _ilu.spec_from_file_location(_MOD_KEY, _ev_path)
            _mod = _ilu.module_from_spec(_spec)
            _sys.modules[_MOD_KEY] = _mod
            _spec.loader.exec_module(_mod)
        from experiments.benchmark.baselines.rl_ppo_pref_v2 import predict_control_result
        from baselines.rl_energyplus.environment_pref_v2 import _build_user_preference_proxy

        vpp_active_now = bool(
            vpp_event and float(vpp_event["trigger_h"]) <= float(sim_h) < float(vpp_event["end_h"])
        )
        capacity = {}
        if loop.appliance_suite is not None:
            try:
                from energybridge.quantification import assess_suite_vpp_request
                duration_h = (
                    max(1e-6, float(vpp_event["end_h"] - vpp_event["trigger_h"]))
                    if vpp_event else 1.0
                )
                capacity = assess_suite_vpp_request(
                    loop.appliance_suite, sim_h, target_kw=2.0,
                    duration_minutes=duration_h * 60.0,
                    hvac_context=_capacity_hvac_context(loop, temp=temp, out_t=out_t, facility_w=facility_w or 0.0),
                )
            except Exception as exc:
                print(f"  [RL Pref-v2] capacity unavailable: {exc}")
        base_actions = {}
        pref_proxy = _build_user_preference_proxy(persona_config) if persona_config else None
        try:
            decision = predict_control_result(
                loop=loop, sim_h=sim_h, temp_c=temp, outdoor_temp_c=out_t,
                vpp_active=vpp_active_now, assessment=capacity.get("assessment", {}),
                appliance_config=appliance_config or {}, base_actions=base_actions,
                vpp_event=vpp_event,
                price_profile=day_ahead_price_profile,
                pref_proxy=pref_proxy,
                daily_scheduled=getattr(loop, "_rl_v2_daily_scheduled", None),
                model_region=weather_label,
            )
        except Exception as exc:
            raise RuntimeError(f"RL PPO Pref-v2 failed at h={sim_h:.2f}: {exc}") from exc
        sp = round(max(_run_sp_min, min(_run_sp_max, float(decision.get("setpoint", loop.sp)))), 1)
        appliance_actions = _filter_controllable_appliance_actions(
            decision.get("appliance_actions", {}), appliance_config
        )
        model_name = Path(str(decision.get("model_path", ""))).name or "unknown"
        print(
            f"  [RL Pref-v2 | h={sim_h:.2f}] setpoint->{sp:.1f}C | model={model_name}"
            " | raw_policy_only=1"
        )
        return {
            "setpoint": sp,
            "next_check_hour": decision.get("next_check_hour"),
            "reason": decision.get("reason", ""),
            "appliance_actions": appliance_actions,
            "objective_source": "rl_ppo_pref_v2_policy",
            "raw_policy_only": True,
        }

    def _score_event(ev, loop_ref, sim_h, event_index=1, human_mode: bool = False):
        """Score agent strategy for a VPP event window after it ends (roleplay LLM)."""
        try:
            if post_event_score_callback is None:
                from user_pref_scorer import score_user_preference
                score_fn = score_user_preference
            else:
                score_fn = post_event_score_callback
            event_day_idx = max(0, min(sim_days - 1, int(ev.get("day", event_index)) - 1))
            wd = loop_ref.vpp_window_data.get(ev["id"], {})
            wtemps = wd.get("temps", [])
            wpmvs  = wd.get("pmvs", [])
            sp_w   = wd.get("sp", loop_ref.sp)
            mean_t = sum(wtemps)/max(1,len(wtemps)) if wtemps else loop_ref.temp_s/max(loop_ref.occ_h,1)
            pmv_ok = sum(wpmvs)/max(1,len(wpmvs)) if wpmvs else 0.5
            e_day  = (loop_ref.e_wh/1000) / max(1, sim_h/24)
            appliance_summary = (
                loop_ref.appliance_suite.vpp_day_summary(event_day_idx)
                if loop_ref.appliance_suite is not None else {}
            )
            _demand = loop_ref.vpp_demand_by_id.get(ev["id"], {})
            _actual_kwh = round(loop_ref.vpp_event_energy_wh.get(ev["id"], 0.0) / 1000.0, 4)
            _baseline_kwh = _demand.get("baseline_kwh", None)
            _target_kwh = _demand.get("target_kwh", None)
            _target_shed_kwh = _demand.get("target_shed_kwh", None)
            _cap_summary = _capacity_window_summary_from_rows(
                loop_ref.vpp_capacity_window_by_id.get(ev["id"], [])
            )
            _shed_context = {
                "trigger_h": float(ev.get("trigger_h", 0.0)),
                "end_h": float(ev.get("end_h", 0.0)),
                "actual_kwh": _actual_kwh,
                "demand_baseline_kwh": _baseline_kwh,
                "capacity_assessment": loop_ref.vpp_capacity_by_id.get(ev["id"], {}),
                "capacity_window_summary": _cap_summary,
            }
            _attach_event_baseline_shed(_shed_context, ev, loop_ref.power_trace_rows)
            _update_event_reference_shed_diagnostics(_shed_context)
            _actual_shed_kwh = _shed_context.get("actual_shed_kwh")
            _overlap_services = _non_ac_appliances_during_vpp(appliance_summary)
            _achieve_ratio = 0.0 if _overlap_services else 1.0
            _achieved = not _overlap_services
            _target_mode = "non_ac_appliance_avoidance"
            _success_text = (
                "success means no present non-AC appliance is scheduled or run inside the VPP window"
            )
            _achievement_text = (
                "VPP appliance-avoidance criterion ACHIEVED; do not describe this event as missed."
                if _achieved is True
                else (
                    "VPP appliance-avoidance criterion NOT achieved; non-AC appliance(s) "
                    f"ran during the event: {', '.join(_overlap_services)}."
                )
            )
            vpp_result_context = {
                "event_id": ev["id"],
                "target_mode": _target_mode,
                "actual_kwh": _actual_kwh,
                "target_kwh": _target_kwh,
                "baseline_kwh": _baseline_kwh,
                "actual_shed_kwh": _actual_shed_kwh,
                "estimated_baseline_kwh": _shed_context.get("estimated_baseline_kwh"),
                "estimated_baseline_source": _shed_context.get("estimated_baseline_source"),
                "estimated_baseline_confidence": _shed_context.get("estimated_baseline_confidence"),
                "reference_baseline_shed_kwh": _shed_context.get("reference_baseline_shed_kwh"),
                "reference_pbase_minus_actual_kwh": _shed_context.get("reference_pbase_minus_actual_kwh"),
                "physical_shed_cap_kwh": _shed_context.get("physical_shed_cap_kwh"),
                "capacity_limited_reference_shed_kwh": _shed_context.get("capacity_limited_reference_shed_kwh"),
                "reference_shed_diagnostic_basis": _shed_context.get("reference_shed_diagnostic_basis"),
                "actual_shed_basis": _shed_context.get("actual_shed_basis"),
                "target_shed_kwh": _target_shed_kwh,
                "target_shed_kw": _demand.get("target_shed_kw", None),
                "achievement_ratio": _achieve_ratio,
                "achieved": _achieved,
                "non_ac_appliances_during_vpp": _overlap_services,
                "appliance_avoidance_success": _achieved,
                "success_text": _success_text,
                "achievement_text": _achievement_text,
            }
            _day_decisions = list(loop_ref.day_agent_decisions[event_day_idx])
            _vpp_trigger_actions = loop_ref.vpp_trigger_actions.get(ev["id"], {})
            _emitted_services = set(_services_from_appliance_actions(_vpp_trigger_actions))
            _objective_source = ""
            for _decision in _day_decisions:
                if not isinstance(_decision, dict):
                    continue
                _emitted_services |= _services_from_appliance_actions(_decision.get("actions"))
                _emitted_services |= _services_from_appliance_actions(_decision.get("raw_appliance_actions"))
                if not _objective_source and _decision.get("objective_source"):
                    _objective_source = str(_decision.get("objective_source"))
            policy_control_context = {
                "method": method,
                "objective_source": _objective_source,
                "action_space_services": sorted(_method_policy_action_space_services(method)),
                "emitted_services": sorted(_emitted_services),
                "vpp_trigger_actions": _vpp_trigger_actions,
                "day_decisions": _day_decisions,
                "occupancy_decisions": [
                    {
                        "h": item.get("h"),
                        "occupied": item.get("occupied"),
                        "occupancy_count": item.get("occupancy_count"),
                        "occupancy_source": item.get("occupancy_source"),
                        "ac_mode": item.get("ac_mode"),
                        "effective_setpoint": item.get("effective_setpoint"),
                        "hvac_availability": item.get("hvac_availability"),
                        "hvac_availability_source": item.get("hvac_availability_source"),
                    }
                    for item in _day_decisions
                    if isinstance(item, dict) and item.get("ac_mode") is not None
                ],
            }
            strategy_trace = loop_ref.vpp_strategy_trace_by_id.get(ev["id"], {})
            strategy_explanation = loop_ref.vpp_strategy_explanation_by_id.get(ev["id"], {})
            vpp_acceptance_gate = loop_ref.vpp_plan_gate_by_id.get(ev["id"], {})
            policy_control_context["vpp_acceptance_gate"] = vpp_acceptance_gate
            adaptability_diagnostics = (
                vpp_acceptance_gate.get("adaptability_diagnostics", {})
                if isinstance(vpp_acceptance_gate, dict) else {}
            )
            _score_explanation = scoring_explanation_text(strategy_explanation)
            r = score_fn(
                building="family", method=method,
                mean_temp_c=mean_t, pmv_ok_fraction=pmv_ok,
                energy_kwh_per_day=e_day, agent_setpoint_c=sp_w,
                event_index=event_index,
                user_preference_text=loop_ref.vpp_user_input_by_id.get(ev["id"], loop_ref.vpp_user_input),
                agent_reason=(
                    _score_explanation
                    or loop_ref.vpp_trigger_reason_by_id.get(ev["id"], loop_ref.vpp_last_reason)
                ),
                persona=persona_config,
                appliance_summary=appliance_summary,
                vpp_context=ev,
                vpp_result_context=vpp_result_context,
                policy_control_context=policy_control_context,
                human_mode=human_mode)
            allowed_score_sources = {"roleplay_llm"}
            _persona_type = ((persona_config or {}).get("meta") or {}).get("persona_type")
            if _persona_type in {"multi_user_household", "multi_user_household_independent_roleplay"}:
                allowed_score_sources.update({"multi_agent_discussion", "multi_user_independent_mean"})
            if r.get("source") not in allowed_score_sources and not human_mode:
                raise RuntimeError(f"role-play LLM required, got {r.get('source')}")
            sc = r.get("score") or 0.0
            comfort_sc = r.get("comfort_score")
            energy_sc = r.get("energy_score")
            vpp_sc = r.get("vpp_score")
            lbl = r.get("label", "?")
            cmt = str(r.get("comment", ""))
            src = r.get("source", "?")
            print(
                f"  [VPP Result | Event {event_index}/{len(vpp_events)} {ev['id']}] "
                f"User score: {sc}/5 ({lbl}) | {cmt[:80]}"
            )
            print(f"  {'─'*62}")
            return {"id": ev["id"], "setpoint": sp_w, "score": sc, "label": lbl,
                    "trigger_h": float(ev.get("trigger_h", 0.0)),
                    "end_h": float(ev.get("end_h", 0.0)),
                    "day": int(ev.get("day", event_index)),
                    "comfort_score": comfort_sc, "energy_score": energy_sc, "vpp_score": vpp_sc,
                    "target_mode": _target_mode,
                    "target_achieved": _achieved,
                    "demand_achievement_ratio": _achieve_ratio,
                    "comment": cmt,
                    "controller_feedback": r.get("controller_feedback"),
                    "member_feedback_summary": r.get("member_feedback_summary"),
                    "member_scores": r.get("member_scores"),
                    "member_score_min": r.get("member_score_min"),
                    "member_score_max": r.get("member_score_max"),
                    "member_score_std": r.get("member_score_std"),
                    "user_input": loop_ref.vpp_user_input_by_id.get(ev["id"], loop_ref.vpp_user_input),
                    "reason": loop_ref.vpp_trigger_reason_by_id.get(ev["id"], loop_ref.vpp_last_reason),
                    "policy_control_context": policy_control_context,
                    "rl_policy_service_guard": r.get("rl_policy_service_guard"),
                    "strategy_candidates": strategy_trace.get("candidates", []),
                    "selected_strategy": strategy_trace.get("selected_strategy", {}),
                    "strategy_trace": strategy_trace,
                    "strategy_explanation": strategy_explanation,
                    "vpp_acceptance_gate": vpp_acceptance_gate,
                    "adaptability_diagnostics": adaptability_diagnostics,
                    "source": src}
        except Exception as e:
            print(f"  [VPP score {ev['id']}] error: {e}")
            if not human_mode:
                raise
            return {"id": ev["id"], "setpoint": loop_ref.sp, "score": None, "label": "?",
                    "comfort_score": None, "energy_score": None, "vpp_score": None,
                    "comment": str(e)[:60], "user_input": "", "source": "error"}

    def _event_score_due_hour(ev: dict, event_index: int) -> float:
        """Score each event at the end of its simulation day, i.e. day 24:00."""
        try:
            event_day = int(ev.get("day", event_index) or event_index)
        except (TypeError, ValueError):
            event_day = int(float(ev.get("end_h", 0.0)) // 24.0) + 1
        event_day = max(1, event_day)
        return float(event_day * 24.0)

    def _score_and_record_vpp_event(ev: dict, event_index: int, score_sim_h: float, *, human_mode: bool = False) -> None:
        if ev["id"] in loop.vpp_scored:
            return
        if method == "no_dr":
            wd = loop.vpp_window_data.get(ev["id"], {})
            result = {
                "id": ev["id"],
                "setpoint": wd.get("sp", loop.sp),
                "score": None,
                "label": "no_dr_counterfactual",
                "trigger_h": float(ev.get("trigger_h", 0.0)),
                "end_h": float(ev.get("end_h", 0.0)),
                "day": int(ev.get("day", event_index)),
                "comfort_score": None,
                "energy_score": None,
                "vpp_score": None,
                "target_mode": "counterfactual",
                "target_achieved": None,
                "demand_achievement_ratio": None,
                "comment": "No-DR counterfactual; role-play score intentionally skipped.",
                "user_input": "",
                "reason": "No controller response; normal AC and random routine appliances.",
                "source": "no_dr_counterfactual",
            }
        else:
            result = _score_event(ev, loop, score_sim_h, event_index=event_index, human_mode=human_mode)
        # Attach actual energy and demand targets to event log.
        _demand = loop.vpp_demand_by_id.get(ev["id"], {})
        result["actual_kwh"] = round(loop.vpp_event_energy_wh.get(ev["id"], 0.0) / 1000.0, 4)
        result["demand_target_kwh"] = _demand.get("target_kwh", None)
        result["demand_baseline_kwh"] = _demand.get("baseline_kwh", None)
        result["demand_target_kw"] = _demand.get("target_shed_kw", None)
        result["demand_target_shed_kwh"] = _demand.get("target_shed_kwh", None)
        result["capacity_assessment"] = loop.vpp_capacity_by_id.get(ev["id"], {})
        if not result.get("strategy_explanation") and loop.vpp_strategy_explanation_by_id.get(ev["id"]):
            result["strategy_explanation"] = loop.vpp_strategy_explanation_by_id.get(ev["id"], {})
        _cap_rows = loop.vpp_capacity_window_by_id.get(ev["id"], [])
        if _cap_rows:
            result["capacity_window_summary"] = _capacity_window_summary_from_rows(_cap_rows)
        _event_day_idx = max(0, min(sim_days - 1, int(ev.get("day", event_index)) - 1))
        # Attach per-appliance VPP summary before annotating VPP success.
        if loop.appliance_suite is not None:
            result["appliance_summary"] = loop.appliance_suite.vpp_day_summary(_event_day_idx)
        if method != "no_dr":
            _attach_event_baseline_shed(result, ev, loop.power_trace_rows)
        _update_event_reference_shed_diagnostics(result)
        _annotate_event_demand_achievement(result)
        result["total_quantification_90"] = loop.total_quantification_by_id.get(
            ev["id"],
            {"status": "not_computed", "reason": "Reference A3 quantification unavailable"},
        )
        # Store all agent decisions for this day.
        result["vpp_trigger_actions"] = loop.vpp_trigger_actions.get(ev["id"], {})
        result["day_decisions"] = loop.day_agent_decisions[_event_day_idx]
        loop.vpp_event_log.append(result)
        loop.vpp_scored.add(ev["id"])
        _write_live_snapshot(status="scored_event")
        # Update memory context for subsequent LLM calls.
        # Keep it compact, but convert repeated feedback into actionable rules
        # so the next strategy does not relearn the same preference from scratch.
        mem_entries = []
        for e in loop.vpp_event_log:
            _feedback = (
                e.get("controller_feedback")
                or e.get("member_feedback_summary")
                or e.get("comment", "")
            )
            _member_scores = e.get("member_scores") or []
            mem_entries.append({
                "event": e["id"],
                "sp": e["setpoint"],
                "score": e["score"],
                "user_said": e.get("user_input", "")[:220],
                "feedback": str(_feedback)[:700],
                "member_scores": [
                    {
                        "member_id": item.get("member_id"),
                        "score": item.get("score"),
                        "comment": str(item.get("comment", ""))[:180],
                    }
                    for item in _member_scores
                    if isinstance(item, dict)
                ],
            })
        try:
            from user_pref_scorer import build_vpp_preference_memory_notes
            mem_rules = build_vpp_preference_memory_notes(
                loop.vpp_event_log,
                persona_config,
            )
        except Exception:
            mem_rules = []
        loop.vpp_mem_ctx = (
            "\nPast VPP responses (user in the loop): "
            + json.dumps(mem_entries, ensure_ascii=False)
        )
        if mem_rules:
            loop.vpp_mem_ctx += (
                "\nLearned preference rules for next decisions: "
                + json.dumps(mem_rules, ensure_ascii=False)
            )
        if method == "agent":
            _update_agent_preference_memory(
                loop,
                result,
                persona_config=persona_config,
            )

    def cb(s):
        if not loop.init(ex, s): return
        if not _is_weather_run_period(ex, s):
            return
        if loop.h_out == -1:
            loop.h_out = ex.get_variable_handle(s, "Site Outdoor Air Drybulb Temperature", "Environment")
        day = ex.day_of_year(s)
        if loop.start_day is None: loop.start_day = day
        hod = ex.current_time(s); dt = ex.zone_time_step(s)
        sim_h = (day - loop.start_day) * 24 + hod
        wu = ex.warmup_flag(s)
        temp = ex.get_variable_value(s, loop.h_temp) if loop.h_temp != -1 else SP_DEFAULT
        fac  = ex.get_variable_value(s, loop.h_fac)  if loop.h_fac != -1  else 0.0
        out_t = 30.0
        if loop.h_out != -1:
            v = ex.get_variable_value(s, loop.h_out)
            if v is not None and not _math.isnan(v): out_t = v
        occ, occ_count, occ_source = _observable_occupancy(ex, s, loop, persona_config, sim_h, hod)
        loop.current_occupied = occ
        loop.current_occupancy_count = occ_count
        loop.current_occupancy_source = occ_source
        # Determine current VPP status before any controller-specific
        # continuous adjustment. Some baselines may only use VPP-aware HVAC
        # logic after the acceptance gate has approved the event dispatch.
        active_vpp = None
        for ev in vpp_events:
            if ev["trigger_h"] <= sim_h < ev["end_h"]:
                active_vpp = ev; break
        agent_precool_hvac_active = False
        hvac_control_occupied = True if method == "no_dr" else occ
        hvac_avail_set = _set_hvac_availability(ex, s, loop, hvac_control_occupied)
        _day_i_now = min(sim_days - 1, int(sim_h // 24))
        _stored_no_vpp_plan = loop.no_vpp_daily_plan_by_day.get(_day_i_now, {})
        _active_gate_for_hvac = (
            loop.vpp_plan_gate_by_id.get(str(active_vpp.get("id", "")), {})
            if active_vpp else {}
        )
        _rule_milp_day_plan_locked = (
            method == "rule_milp"
            and bool(occ)
            and isinstance(_stored_no_vpp_plan, dict)
            and _stored_no_vpp_plan.get("setpoint") is not None
            and not bool(_active_gate_for_hvac.get("accepted"))
        )
        if _rule_milp_day_plan_locked:
            try:
                locked_sp = float(_stored_no_vpp_plan.get("setpoint"))
            except (TypeError, ValueError):
                locked_sp = float(loop.planned_occupied_sp)
            loop.sp = round(locked_sp, 1)
            loop.planned_occupied_sp = loop.sp
        if method == "rule_milp" and occ and not _rule_milp_day_plan_locked:
            try:
                from experiments.benchmark.baselines.rule_milp import _choose_dynamic_cost_min_setpoint

                cache_key = (
                    round(float(sim_h) * 2.0) / 2.0,
                    round(float(temp), 1),
                    round(float(out_t), 1),
                    str(weather_label or ""),
                    str(active_vpp.get("id", "")) if active_vpp else "",
                    bool(
                        loop.vpp_plan_gate_by_id.get(str(active_vpp.get("id", "")), {}).get("accepted")
                    ) if active_vpp else False,
                )
                cached = getattr(loop, "_rule_milp_hvac_cache", None)
                if isinstance(cached, dict) and cached.get("key") == cache_key:
                    rule_sp = cached.get("setpoint", loop.sp)
                else:
                    _active_gate = (
                        loop.vpp_plan_gate_by_id.get(str(active_vpp.get("id", "")), {})
                        if active_vpp else {}
                    )
                    _rule_hvac_vpp_event = active_vpp if bool(_active_gate.get("accepted")) else None
                    state_dict = _build_decision_time_state(
                        loop,
                        sim_h=sim_h,
                        hod=hod,
                        temp=temp,
                        out_t=out_t,
                        facility_w=fac,
                        vpp_event=_rule_hvac_vpp_event,
                        vpp_target_kwh=None,
                        appliance_config=appliance_config or {},
                    )
                    state_dict["standalone_baseline"] = True
                    state_dict["mpc_horizon_steps"] = mpc_horizon_steps
                    rule_sp, _diag = _choose_dynamic_cost_min_setpoint(state_dict)
                    loop._rule_milp_hvac_cache = {"key": cache_key, "setpoint": rule_sp}
                loop.sp = round(max(_rule_milp_sp_min, min(_rule_milp_sp_max, float(rule_sp))), 1)
                loop.planned_occupied_sp = loop.sp
            except Exception as _rme:
                print(f"  [Rule+MILP HVAC] dynamics rule failed: {_rme}")

        if not wu:
            loop.power_trace_rows.append({
                "sim_h": float(sim_h),
                "hod": float(hod),
                "dt_h": float(dt),
                "power_kw": max(0.0, float(fac or 0.0) / 1000.0),
                "facility_power_w": max(0.0, float(fac or 0.0)),
                "outdoor_temperature_c": float(out_t),
                "indoor_temperature_c": float(temp),
                "ac_setpoint_c": float(loop.sp),
                "occupied": bool(occ),
                "vpp_active": active_vpp is not None,
                "vpp_event_id": str(active_vpp.get("id", "")) if active_vpp else "",
            })
            _last_snapshot_h = getattr(loop, "_last_live_snapshot_h", None)
            if _last_snapshot_h is None or sim_h - float(_last_snapshot_h) >= 1.0 or active_vpp is not None:
                _write_live_snapshot(status="running")
                loop._last_live_snapshot_h = float(sim_h)

        if not wu:
            psim = loop.prev_sim_h
            if method == "no_dr":
                if loop.prev_occupied is None:
                    print(
                        f"  [No-DR HVAC | Day{int(sim_h // 24) + 1} {_fmt_clock_h(hod)}] "
                        "HVAC availability stays on; occupancy is not used for AC shutoff"
                    )
            elif not hvac_control_occupied:
                if loop.prev_occupied is not False:
                    print(
                        f"  [AC Occupancy | Day{int(sim_h // 24) + 1} {_fmt_clock_h(hod)}] "
                        f"unoccupied ({occ_source}, count={occ_count:.2f}); HVAC availability→off"
                    )
                if not hvac_avail_set:
                    loop.sp = AC_OFF_FALLBACK_COOLING_SETPOINT
                if loop.prev_occupied is not False:
                    _append_day_agent_decision(loop, sim_days, sim_h, {
                        "h": sim_h,
                        "sp": loop.planned_occupied_sp,
                        "effective_setpoint": loop.sp,
                        "room_temp_c": round(float(temp), 3),
                        "outdoor_temp_c": round(float(out_t), 3),
                        "hvac_availability": 0.0,
                        "hvac_availability_source": "HVAC_Availability_Control" if hvac_avail_set else "setpoint_fallback",
                        "occupied": False,
                        "occupancy_count": round(float(occ_count), 3),
                        "occupancy_source": occ_source,
                        "ac_mode": "off_unoccupied",
                        "reason": "calendar/EP occupancy shows home unoccupied; HVAC availability off",
                        "actions": {},
                        "raw_appliance_actions": {},
                    })
            elif loop.prev_occupied is False:
                loop.sp = loop.planned_occupied_sp
                print(
                    f"  [AC Occupancy | Day{int(sim_h // 24) + 1} {_fmt_clock_h(hod)}] "
                    f"occupied ({occ_source}, count={occ_count:.2f}); setpoint→{loop.sp:.1f}°C from policy plan"
                )
                _append_day_agent_decision(loop, sim_days, sim_h, {
                    "h": sim_h,
                    "sp": loop.planned_occupied_sp,
                    "effective_setpoint": loop.sp,
                    "room_temp_c": round(float(temp), 3),
                    "outdoor_temp_c": round(float(out_t), 3),
                    "hvac_availability": 1.0,
                    "hvac_availability_source": "HVAC_Availability_Control" if hvac_avail_set else "setpoint_fallback",
                    "occupied": True,
                    "occupancy_count": round(float(occ_count), 3),
                    "occupancy_source": occ_source,
                    "ac_mode": "on",
                    "reason": "calendar/EP occupancy shows home occupied; AC restored to policy setpoint",
                    "actions": {},
                    "raw_appliance_actions": {},
                })
            loop.prev_occupied = occ
            loop.prev_precool_hvac_active = agent_precool_hvac_active
            # End-of-day completion check at each midnight after Day N.
            for _day_idx in range(max(0, sim_days - 1)):
                _eod_h = float((_day_idx + 1) * 24)
                if psim < _eod_h <= sim_h and loop.appliance_suite is not None:
                    if _day_idx not in loop.days_evaluated:
                        loop.days_evaluated.add(_day_idx)
                        _print_prev_day_completion(loop.appliance_suite, _day_idx, _day_idx + 1)
            # Role-play scoring happens at the end of the event day (24:00),
            # after appliance service outcomes such as laundry, hot water, and
            # evening routines have had time to materialize.
            for ev in vpp_events:
                if ev["id"] in loop.vpp_scored:
                    continue
                ev_idx = next((i + 1 for i, item in enumerate(vpp_events) if item["id"] == ev["id"]), 1)
                score_due_h = _event_score_due_hour(ev, ev_idx)
                if psim < score_due_h <= sim_h:
                    print(
                        f"  [VPP Day-End Score] event={ev['id']} "
                        f"due={score_due_h:.1f}h current={sim_h:.1f}h"
                    )
                    _score_and_record_vpp_event(ev, ev_idx, sim_h, human_mode=human_mode)

            triggered = False
            triggered_vpp = None
            triggered_vpp_preplan = None
            triggered_daily_plan = False
            triggered_next_check = False
            _plan_grace_h = max(0.25, 2.0 * float(dt or 0.25))
            for _day_idx in range(sim_days):
                _plan_h = _day_idx * 24.0 + planning_hour
                if _day_idx in loop.daily_plans_done:
                    continue
                _crossed_plan = psim < _plan_h <= sim_h
                _missed_warmup_plan = (
                    sim_h >= _plan_h and sim_h <= _plan_h + _plan_grace_h
                )
                if _crossed_plan or _missed_warmup_plan:
                    triggered = True
                    triggered_daily_plan = True
                    loop.daily_plans_done.add(_day_idx)
                    break
            for ev in vpp_events:
                if psim < ev["trigger_h"] <= sim_h:
                    triggered = True; triggered_vpp = ev; break
            if loop.next_check is not None and psim < loop.next_check <= sim_h:
                triggered = True
                triggered_next_check = True
            if triggered_next_check and triggered_vpp is None:
                _upcoming_for_preplan = _find_active_or_upcoming_vpp_event(sim_h, vpp_events=vpp_events)
                if (
                    _upcoming_for_preplan is not None
                    and sim_h < float(_upcoming_for_preplan.get("trigger_h", 0.0))
                    and float(_upcoming_for_preplan.get("trigger_h", 0.0)) - sim_h
                    <= AGENT_PRE_VPP_PRECOOL_LEAD_H + _plan_grace_h
                ):
                    triggered_vpp_preplan = _upcoming_for_preplan

            if triggered:
                is_vpp = triggered_vpp is not None
                vid = triggered_vpp["id"] if triggered_vpp else ""
                planning_vpp_event = triggered_vpp or triggered_vpp_preplan
                is_vpp_preplan = triggered_vpp_preplan is not None and not is_vpp
                planning_vid = str(planning_vpp_event.get("id", "")) if planning_vpp_event else ""
                day_num = int(sim_h // 24) + 1
                if is_vpp:
                    ev_window_text = _event_window_text(triggered_vpp)
                    ev_duration_h = max(1e-6, float(triggered_vpp["end_h"] - triggered_vpp["trigger_h"]))
                    ev_idx_n   = next((i+1 for i,e in enumerate(vpp_events) if e["id"]==vid), 1)
                    _prev_vpp_kwh = [loop.vpp_event_energy_wh.get(e["id"], 0.0) / 1000.0
                                     for e in vpp_events if e["trigger_h"] < triggered_vpp["trigger_h"]]
                    print(f"  [VPP Energy Log] code-generated, no LLM: {[round(k,3) for k in _prev_vpp_kwh] or '(none)'}")
                    _capacity = {}
                    _total_q90 = loop.total_quantification_by_id.get(
                        vid,
                        {"status": "not_computed", "reason": "Reference A3 quantification unavailable"},
                    )
                    if loop.appliance_suite is not None:
                        try:
                            from energybridge.quantification import assess_suite_vpp_request
                            _capacity = assess_suite_vpp_request(
                                loop.appliance_suite, sim_h,
                                target_kw=2.0, duration_minutes=ev_duration_h * 60.0,
                                hvac_context=_capacity_hvac_context(
                                    loop, temp=temp, out_t=out_t, facility_w=fac
                                ),
                            )
                        except Exception as _ce:
                            print(f"  [Capacity Assessment] failed: {_ce}")
                    loop.vpp_capacity_by_id[vid] = _capacity
                    loop.current_vpp_capacity = _capacity
                    _assessment = _capacity.get("assessment", {})
                    print(
                        "  [Household Capacity] "
                        f"committable={float(_assessment.get('committable_kw', 0.0)):.3f}kW "
                        f"bid={float(_assessment.get('recommended_bid_kw', 0.0)):.3f}kW "
                        f"success={float(_assessment.get('success_probability', 0.0)):.1%}"
                    )
                    _vpp_demand = _call_vpp_demand_agent(vid, _total_q90)
                    loop.vpp_demand_by_id[vid] = _vpp_demand
                    loop.current_vpp_demand_kwh = _vpp_demand["target_kwh"]
                    loop.current_vpp_demand_kw = _vpp_demand.get("target_shed_kw", 0.0)
                    print(
                        f"  [VPP Grid Agent] {vid} "
                        f"diagnostic_shed_ref={loop.current_vpp_demand_kw:.3f}kW "
                        f"(diagnostic_cap={_vpp_demand['target_kwh']:.3f}kWh)  "
                        f"[{_vpp_demand['reason']}]"
                    )
                    print(f"  {'='*62}")
                    print(
                        f"  VPP Demand-Response Event {ev_idx_n}/{len(vpp_events)}  "
                        f"(Day{day_num}  {ev_window_text})"
                    )
                    print(
                        "    Goal : Keep every present non-AC appliance out of this "
                        f"{ev_duration_h:.2f}h VPP window; do not score by shed/cap target"
                    )
                    if method == "agent":
                        print(f"    AC   : Agent may adjust only within user comfort/consent bounds")
                    else:
                        print(f"    AC   : Standalone baseline uses hard control bounds, not agent comfort protection")
                    print(f"    Other: Shift washer/dishwasher/dryer/EWH/EV away from {_event_window_text(triggered_vpp)}")
                    print(f"  {'='*62}")
                elif triggered_daily_plan:
                    print(
                        f"  --- Day {day_num} daily plan  "
                        f"(sim_h={sim_h:.0f}h  {_fmt_clock_h(planning_hour)} planning) ---"
                    )
                elif is_vpp_preplan:
                    print(
                        f"  --- VPP pre-event dispatch plan "
                        f"(Day{day_num}  event={planning_vid}  window={_event_window_text(planning_vpp_event)}) ---"
                    )

                # User in the loop: get roleplay user preference BEFORE agent acts
                if (
                    planning_vpp_event is not None
                    and method != "no_dr"
                    and planning_vid not in loop.vpp_user_input_by_id
                ):
                    try:
                        if pre_event_preference_callback is None:
                            from user_pref_scorer import get_user_preference_input
                            preference_fn = get_user_preference_input
                        else:
                            preference_fn = pre_event_preference_callback
                        ev_idx = next((i+1 for i,ev in enumerate(vpp_events)
                                       if ev["id"]==planning_vid), 1)
                        _acfg = appliance_config or {}
                        _appl_ctx = {
                            "washer": bool((_acfg.get("washer", {}) or {}).get("present", False)),
                            "dishwasher": bool((_acfg.get("dishwasher", {}) or {}).get("present", False)),
                            "dryer": bool((_acfg.get("dryer", {}) or {}).get("present", False)),
                            "water_heater": bool((_acfg.get("water_heater", {}) or {}).get("present", False)),
                            "ev": bool((_acfg.get("ev", {}) or {}).get("present", False)),
                        }
                        _pref_result = preference_fn(
                            "family", ev_idx,
                            {"vpp_id": planning_vid, "hour": sim_h, "trigger_h": planning_vpp_event["trigger_h"],
                             "end_h": planning_vpp_event["end_h"], "day": planning_vpp_event.get("day", day_num),
                             "duration_h": max(1e-6, float(planning_vpp_event["end_h"] - planning_vpp_event["trigger_h"])),
                             "appliances": _appl_ctx},
                            loop.vpp_event_log,
                            persona=persona_config,
                            human_mode=human_mode)
                        loop.vpp_user_input = str(_pref_result)
                        loop.vpp_user_input_by_id[planning_vid] = loop.vpp_user_input
                        loop.vpp_strategy_trace_by_id[planning_vid] = dict(
                            getattr(_pref_result, "strategy_trace", {}) or {}
                        )
                    except Exception as _e:
                        print(f"  [UserInput] {_e}")
                        loop.vpp_user_input = ""
                        loop.vpp_user_input_by_id[planning_vid] = ""
                        loop.vpp_strategy_trace_by_id[planning_vid] = {}
                else:
                    loop.vpp_user_input = ""
                if method == "no_dr":
                    res = {
                        "setpoint": round(float(_ac_sp_default), 1),
                        "next_check_hour": None,
                        "reason": "no_dr counterfactual: normal AC setpoint; random routine appliances; no DR action",
                        "appliance_actions": {},
                        "objective_source": "no_dr_random_routine_counterfactual",
                    }
                elif method == "mpc_dynamic":
                    res = _mpc_trigger(
                        temp, out_t, hod, sim_h,
                        facility_w=fac,
                        vpp_event=planning_vpp_event)
                elif method == "rule_milp":
                    res = _rule_milp_trigger(
                        temp, out_t, hod, sim_h,
                        facility_w=fac,
                        vpp_event=planning_vpp_event)
                elif method == "rl_ppo_pref_v2":
                    rl2_vpp_event = planning_vpp_event or active_vpp
                    res = _rl_pref_v2_trigger(
                        temp, out_t, hod, sim_h,
                        facility_w=fac,
                        vpp_event=rl2_vpp_event)
                elif method == "hema_agent":
                    res = _hema_trigger(
                        temp, out_t, hod, sim_h, total_sim_hours - sim_h,
                        vpp_active=is_vpp, vpp_id=vid,
                        user_pref_input=loop.vpp_user_input,
                        appliance_config=appliance_config,
                        vpp_event=planning_vpp_event,
                        persona_config=persona_config,
                        facility_w=fac,
                    )
                else:
                    res = _llm_trigger(temp, out_t, hod, sim_h, total_sim_hours - sim_h,
                                       vpp_active=bool(is_vpp or active_vpp is not None),
                                       vpp_id=vid or planning_vid or (str(active_vpp.get("id", "")) if active_vpp else ""),
                                       user_pref_input=loop.vpp_user_input,
                                       facility_w=fac,
                                       suppress_vpp_context=bool(triggered_daily_plan and not is_vpp))
                    if "objective_terms_posthoc" not in res:
                        try:
                            res["objective_terms_posthoc"] = _compute_posthoc_decision_objective(
                                loop,
                                action_result=res,
                                sim_h=sim_h,
                                hod=hod,
                                temp=temp,
                                out_t=out_t,
                                facility_w=fac,
                                vpp_event=planning_vpp_event if planning_vpp_event is not None else active_vpp,
                                vpp_target_kwh=(
                                    loop.current_vpp_demand_kwh if (is_vpp or active_vpp is not None) else None
                                ),
                                appliance_config=appliance_config or {},
                            )
                            res["objective_source"] = "posthoc_agent_decision_time_pdf_v15"
                        except Exception as _oe:
                            print(f"  [Agent Objective] posthoc objective error: {_oe}")
                if method == "rule_milp" and (is_vpp or active_vpp is not None):
                    res["setpoint"] = AC_OFF_FALLBACK_COOLING_SETPOINT
                    res["reason"] = f"{res.get('reason', method)} | active VPP HVAC-off"
                elif method == "agent" and (is_vpp or active_vpp is not None):
                    _agent_vpp_tradeoff_sp = _agent_vpp_tradeoff_setpoint_c(
                        loop,
                        ac_sp_max_c=_ac_sp_max,
                        ac_sp_tol_c=_ac_sp_tol,
                        run_sp_max_c=_run_sp_max,
                        protective_mode=_protective_mode,
                        multi_user_comfort_first=_agent_multi_user_comfort_first,
                    )
                    res["setpoint"] = _agent_vpp_tradeoff_sp
                    res["reason"] = f"{res.get('reason', method)} | active VPP low-power drift after pre-cooling"
                _day_i = min(sim_days - 1, int(sim_h // 24))
                if method != "no_dr" and active_vpp is not None and planning_vpp_event is None:
                    _active_vid = str(active_vpp.get("id", ""))
                    _active_gate = loop.vpp_plan_gate_by_id.get(_active_vid, {})
                    if _active_gate:
                        if bool(_active_gate.get("accepted")):
                            res, _locked_changed = _lock_to_user_accepted_vpp_plan(
                                res,
                                _active_gate,
                                clear_appliance_actions=True,
                            )
                            if _locked_changed:
                                print(
                                    "  [VPP Acceptance Gate] "
                                    f"{_active_vid} active recheck locked to accepted plan "
                                    f"setpoint={float(res.get('setpoint')):.1f}C"
                                )
                        else:
                            print(
                                "  [VPP Acceptance Gate] "
                                f"{_active_vid} active recheck remains rejected -> fallback"
                            )
                            res = _fallback_plan_after_vpp_rejection(
                                default_plan=loop.no_vpp_daily_plan_by_day.get(_day_i),
                                current_setpoint=loop.sp,
                                event=active_vpp,
                                persona_config=persona_config,
                                appliance_config=appliance_config or {},
                            )
                if (
                    method != "no_dr"
                    and planning_vpp_event is None
                    and active_vpp is None
                    and not triggered_daily_plan
                ):
                    _stored_day_plan = loop.no_vpp_daily_plan_by_day.get(_day_i)
                    if isinstance(_stored_day_plan, dict) and _stored_day_plan.get("setpoint") is not None:
                        _continued_plan = dict(_stored_day_plan)
                        _continued_plan["appliance_actions"] = {}
                        _continued_plan["next_check_hour"] = None
                        _continued_plan["reason"] = (
                            "Continue the gated no-VPP day-ahead plan after a routine checkpoint"
                        )
                        _continued_plan["objective_source"] = "gated_no_vpp_daily_plan_continuation"
                        print(
                            "  [No-VPP Daily Plan Gate] "
                            f"continue gated day-ahead plan setpoint={float(_continued_plan.get('setpoint')):.1f}C"
                        )
                        res = _continued_plan
                _raw_appliance_actions = dict(res.get("appliance_actions", {}) or {})
                _vpp_replan_guard = {}
                if method != "no_dr" and is_vpp and triggered_vpp is not None and loop.appliance_suite is not None:
                    _guarded_actions, _vpp_replan_guard = _filter_vpp_event_replan_actions(
                        actions=_raw_appliance_actions,
                        suite=loop.appliance_suite,
                        appliance_config=appliance_config or {},
                        event=triggered_vpp,
                        sim_h=sim_h,
                    )
                    res["appliance_actions"] = _guarded_actions
                if method != "no_dr" and triggered_daily_plan and planning_vpp_event is None:
                    _daily_plan_gate = _evaluate_no_vpp_daily_plan_acceptance(
                        plan=res,
                        persona_config=persona_config,
                        appliance_config=appliance_config or {},
                    )
                    if not bool(_daily_plan_gate.get("accepted")):
                        print(
                            "  [No-VPP Daily Plan Gate] "
                            f"rejected -> manual comfort routine ({'; '.join(_daily_plan_gate.get('reasons', []))})"
                        )
                        _manual_daily_plan = _manual_no_vpp_user_plan(
                            persona_config=persona_config,
                            appliance_config=appliance_config or {},
                            current_setpoint=loop.sp,
                        )
                        _manual_daily_plan["no_vpp_daily_plan_gate"] = _daily_plan_gate
                        res = _manual_daily_plan
                    else:
                        res["no_vpp_daily_plan_gate"] = _daily_plan_gate
                    loop.no_vpp_daily_plan_by_day[_day_i] = {
                        "setpoint": res.get("setpoint"),
                        "next_check_hour": res.get("next_check_hour"),
                        "reason": res.get("reason", ""),
                        "appliance_actions": dict(res.get("appliance_actions", {}) or {}),
                        "objective_source": res.get("objective_source", ""),
                        "no_vpp_daily_plan_gate": res.get("no_vpp_daily_plan_gate", {}),
                    }
                _vpp_acceptance_gate = {}
                if method != "no_dr" and planning_vpp_event is not None:
                    _default_plan = loop.no_vpp_daily_plan_by_day.get(_day_i)
                    if _default_plan is None:
                        _default_plan = {
                            "setpoint": _ac_sp_default,
                            "next_check_hour": None,
                            "reason": "implicit default no-VPP comfort plan",
                            "appliance_actions": {},
                            "objective_source": "implicit_no_vpp_default",
                        }
                    if method == "agent":
                        _fixed_repaired_actions, _fixed_preserved = _preserve_fixed_routine_actions(
                            res.get("appliance_actions", {}),
                            (_default_plan or {}).get("appliance_actions", {}),
                            appliance_config or {},
                        )
                        if _fixed_preserved:
                            res["appliance_actions"] = _fixed_repaired_actions
                            res["reason"] = (
                                f"{res.get('reason', '')} | Preserved fixed routine(s): "
                                f"{', '.join(_fixed_preserved)}; VPP capacity reduced to protect consent."
                            ).strip()
                            res["fixed_routine_preserved_for_consent"] = list(_fixed_preserved)
                    _existing_gate = loop.vpp_plan_gate_by_id.get(planning_vid)
                    _reuse_existing_gate = (
                        _existing_gate is not None
                        and is_vpp
                        and _vpp_gate_matches_current_plan(_existing_gate, res)
                    )
                    if _existing_gate is not None and is_vpp and not _reuse_existing_gate:
                        print(
                            "  [VPP Acceptance Gate] "
                            f"{planning_vid} event-start plan changed; re-evaluating user acceptance"
                        )
                    if _reuse_existing_gate:
                        _vpp_acceptance_gate = dict(_existing_gate)
                        _vpp_acceptance_gate["reused_at_event_start"] = True
                    else:
                        _rule_cmp_event = planning_vpp_event
                        _rule_cmp = (
                            res if method == "rule_milp"
                            else _rule_milp_reference_plan(
                                temp, out_t, hod, sim_h,
                                facility_w=fac,
                                vpp_event=_rule_cmp_event,
                            )
                        )
                        _vpp_acceptance_gate = _evaluate_vpp_plan_acceptance_gate(
                            method=method,
                            persona_config=persona_config,
                            appliance_config=appliance_config or {},
                            event=planning_vpp_event,
                            proposed_plan=res,
                            default_plan=_default_plan,
                            rule_milp_plan=_rule_cmp,
                            past_events=loop.vpp_event_log,
                            user_preference_text=loop.vpp_user_input_by_id.get(planning_vid, loop.vpp_user_input),
                            current_hod=hod if is_vpp else None,
                        )
                        loop.vpp_plan_gate_by_id[planning_vid] = dict(_vpp_acceptance_gate)
                    if not bool(_vpp_acceptance_gate.get("accepted")):
                        print(
                            "  [VPP Acceptance Gate] "
                            f"{planning_vid} rejected p={_vpp_acceptance_gate.get('acceptance_probability')} "
                            f"draw={_vpp_acceptance_gate.get('stable_draw')} -> fallback to no-VPP daily plan"
                        )
                        res = _fallback_plan_after_vpp_rejection(
                            default_plan=_default_plan,
                            current_setpoint=loop.sp,
                            event=planning_vpp_event,
                            persona_config=persona_config,
                            appliance_config=appliance_config or {},
                        )
                    else:
                        _adapt = _vpp_acceptance_gate.get("adaptability_diagnostics", {})
                        if is_vpp:
                            res, _locked_changed = _lock_to_user_accepted_vpp_plan(
                                res,
                                _vpp_acceptance_gate,
                                clear_appliance_actions=False,
                            )
                            if _locked_changed:
                                print(
                                    "  [VPP Acceptance Gate] "
                                    f"{planning_vid} event-start locked to accepted plan "
                                    f"setpoint={float(res.get('setpoint')):.1f}C"
                                )
                        print(
                            "  [VPP Acceptance Gate] "
                            f"{planning_vid} accepted p={_vpp_acceptance_gate.get('acceptance_probability')} "
                            f"draw={_vpp_acceptance_gate.get('stable_draw')} "
                            f"adapt={_adapt.get('overall_adaptability_score', 'n/a')}"
                        )
                    res["vpp_acceptance_gate"] = _vpp_acceptance_gate
                    if _vpp_acceptance_gate.get("adaptability_diagnostics"):
                        res["adaptability_diagnostics"] = _vpp_acceptance_gate.get("adaptability_diagnostics")
                loop.planned_occupied_sp = res["setpoint"]
                effective_sp = res["setpoint"] if hvac_control_occupied or hvac_avail_set else AC_OFF_FALLBACK_COOLING_SETPOINT
                loop.sp = effective_sp
                loop.next_check = res.get("next_check_hour")
                if method != "no_dr" and not is_vpp:
                    _agent_checkpoint = _agent_next_vpp_checkpoint_hour(
                        sim_h,
                        vpp_events=vpp_events,
                    )
                    _min_next_gap = max(0.05, 0.5 * float(dt or 0.25))
                    if (
                        _agent_checkpoint is not None
                        and _agent_checkpoint > sim_h + _min_next_gap
                        and (loop.next_check is None or _agent_checkpoint < float(loop.next_check))
                    ):
                        loop.next_check = _agent_checkpoint
                if is_vpp and triggered_vpp is not None:
                    _vpp_end = triggered_vpp["end_h"]
                    if loop.next_check is None or loop.next_check > _vpp_end:
                        loop.next_check = _vpp_end
                loop.vpp_last_reason = res.get("reason", "")
                _non_null = {k: v for k, v in res.get("appliance_actions", {}).items() if v is not None}
                _decision_log = {
                    "h": sim_h,
                    "sp": res["setpoint"],
                    "effective_setpoint": effective_sp,
                    "room_temp_c": round(float(temp), 3),
                    "outdoor_temp_c": round(float(out_t), 3),
                    "hvac_availability": 1.0 if hvac_control_occupied else 0.0,
                    "hvac_availability_source": "HVAC_Availability_Control" if hvac_avail_set else "setpoint_fallback",
                    "occupied": bool(occ),
                    "occupancy_count": round(float(occ_count), 3),
                    "occupancy_source": occ_source,
                    "ac_mode": "on" if hvac_control_occupied else "off_unoccupied",
                    "reason": res.get("reason", ""),
                    "actions": _non_null,
                    "raw_appliance_actions": _raw_appliance_actions,
                }
                if _vpp_replan_guard:
                    _decision_log["vpp_replan_guard"] = _vpp_replan_guard
                if method == "no_dr":
                    _routine_day_i = min(sim_days - 1, int(sim_h // 24))
                    _routine = (
                        loop.no_dr_routine_actions[_routine_day_i]
                        if 0 <= _routine_day_i < len(loop.no_dr_routine_actions) else {}
                    )
                    _decision_log["no_dr_routine_actions"] = _routine.get("actions", {})
                if res.get("objective_source"):
                    _decision_log["objective_source"] = res.get("objective_source")
                if res.get("objective_terms"):
                    _decision_log["objective_terms"] = res.get("objective_terms", {})
                if res.get("objective_terms_posthoc"):
                    _decision_log["objective_terms_posthoc"] = res.get("objective_terms_posthoc", {})
                if res.get("posthoc_objective_source"):
                    _decision_log["posthoc_objective_source"] = res.get("posthoc_objective_source")
                if res.get("strategy_trace"):
                    _decision_log["strategy_trace"] = res.get("strategy_trace", {})
                if res.get("strategy_explanation"):
                    _decision_log["strategy_explanation"] = res.get("strategy_explanation", {})
                if res.get("vpp_acceptance_gate"):
                    _decision_log["vpp_acceptance_gate"] = res.get("vpp_acceptance_gate", {})
                    if res.get("vpp_acceptance_gate", {}).get("decision") == "reject_fallback_to_no_vpp_daily_plan":
                        _decision_log["rejected_vpp_proposed_actions"] = _raw_appliance_actions
                if res.get("no_vpp_daily_plan_gate"):
                    _decision_log["no_vpp_daily_plan_gate"] = res.get("no_vpp_daily_plan_gate", {})
                if res.get("fallback_daily_plan_gate"):
                    _decision_log["fallback_daily_plan_gate"] = res.get("fallback_daily_plan_gate", {})
                if res.get("adaptability_diagnostics"):
                    _decision_log["adaptability_diagnostics"] = res.get("adaptability_diagnostics", {})
                _append_day_agent_decision(loop, sim_days, sim_h, _decision_log)
                if is_vpp and triggered_vpp is not None:
                    loop.vpp_trigger_actions[vid] = res.get("appliance_actions", {})
                    loop.vpp_trigger_reason_by_id[vid] = res.get("reason", "")
                    if res.get("vpp_acceptance_gate"):
                        loop.vpp_plan_gate_by_id[vid] = res.get("vpp_acceptance_gate", {})
                    if res.get("strategy_explanation"):
                        loop.vpp_strategy_explanation_by_id[vid] = res.get("strategy_explanation", {})
                    if res.get("strategy_trace"):
                        existing_trace = dict(loop.vpp_strategy_trace_by_id.get(vid, {}) or {})
                        existing_trace.update(res.get("strategy_trace", {}))
                        loop.vpp_strategy_trace_by_id[vid] = existing_trace
                if loop.appliance_suite is not None:
                    _apply_appliance_actions(
                        loop.appliance_suite,
                        res.get("appliance_actions", {}),
                        sim_h)

            # Collect per-VPP-window data
            if active_vpp:
                wd = loop.vpp_window_data.setdefault(active_vpp["id"], {"temps":[],"pmvs":[],"sp":loop.sp})
                wd["temps"].append(temp)
                wd["pmvs"].append(abs(_compute_pmv(temp)) <= PMV_DEADBAND)
                wd["sp"] = loop.sp
                if loop.appliance_suite is not None:
                    try:
                        from energybridge.quantification import assess_suite_vpp_request
                        _active_duration_h = max(1e-6, float(active_vpp["end_h"] - active_vpp["trigger_h"]))
                        _step_capacity = assess_suite_vpp_request(
                            loop.appliance_suite, sim_h,
                            target_kw=2.0, duration_minutes=_active_duration_h * 60.0,
                            hvac_context=_capacity_hvac_context(
                                loop, temp=temp, out_t=out_t, facility_w=fac
                            ),
                        )
                        _step_assessment = _step_capacity.get("assessment", {})
                        loop.vpp_capacity_window_by_id.setdefault(active_vpp["id"], []).append({
                            "sim_h": sim_h,
                            "dt_h": dt,
                            "committable_kw": float(_step_assessment.get("committable_kw", 0.0)),
                            "recommended_bid_kw": float(_step_assessment.get("recommended_bid_kw", 0.0)),
                            "success_probability": float(_step_assessment.get("success_probability", 0.0)),
                        })
                    except Exception as _ce:
                        print(f"  [Capacity Window] failed: {_ce}")

            # VPP role-play scoring is intentionally deferred to the event day's
            # 24:00 boundary above, after service outcomes are observable.

        if loop.h_cool != -1: ex.set_actuator_value(s, loop.h_cool, loop.sp)
        if loop.h_heat != -1: ex.set_actuator_value(s, loop.h_heat, HTG_SP)

        loop.prev_sim_h = sim_h
        if wu: return
        pmv = _compute_pmv(temp)
        loop.e_wh += fac * dt
        _day_idx_for_energy = int(sim_h // 24)
        if 0 <= _day_idx_for_energy < len(loop.daily_e_wh):
            loop.daily_e_wh[_day_idx_for_energy] += fac * dt
        if active_vpp:            # track energy consumed during VPP demand windows
            loop.vpp_e_wh += fac * dt
            loop.vpp_event_energy_wh[active_vpp["id"]] = (
                loop.vpp_event_energy_wh.get(active_vpp["id"], 0.0) + fac * dt)
        # Step appliance suite and write powers back to EnergyPlus each timestep
        if loop.appliance_suite is not None:
            _appl_powers = loop.appliance_suite.step(sim_h, dt)
            _write_appliance_actuators(ex, s, loop, _appl_powers, sim_h)
        if occ:
            loop.occ_h += dt; loop.pmv_s += pmv * dt; loop.temp_s += temp * dt
            if abs(pmv) <= PMV_DEADBAND: loop.pmv_ok_h += dt
            if 23.0 <= temp <= 26.0: loop.comfort_ok_h += dt
            if temp > loop.sp + UNMET_TOL: loop.unmet_h += dt
            loop.decisions.append((round(sim_h, 2), round(loop.sp, 1), round(pmv, 3)))

    api.runtime.callback_end_system_timestep_after_hvac_reporting(state, cb)
    ec = api.runtime.run_energyplus(state, ["-w", str(epw_path), "-d", str(output_dir), str(idf_path)])
    api.state_manager.delete_state(state)

    final_score_h = max(float(loop.prev_sim_h), total_sim_hours)
    for ev_idx, ev in enumerate(vpp_events, start=1):
        if ev["id"] in loop.vpp_scored:
            continue
        if _event_score_due_hour(ev, ev_idx) <= final_score_h + 1e-6:
            print(
                f"  [VPP Day-End Score] event={ev['id']} "
                f"due={_event_score_due_hour(ev, ev_idx):.1f}h final={final_score_h:.1f}h"
            )
            _score_and_record_vpp_event(ev, ev_idx, final_score_h, human_mode=human_mode)

    meter_vpp_kwh = _read_ep_vpp_window_energy(output_dir, vpp_events)
    if meter_vpp_kwh:
        loop.vpp_event_energy_wh = {
            ev_id: float(kwh) * 1000.0
            for ev_id, kwh in meter_vpp_kwh.items()
        }
        loop.vpp_e_wh = sum(loop.vpp_event_energy_wh.values())
        _vpp_event_by_id = {str(ev.get("id", "")): ev for ev in vpp_events}
        for event_result in loop.vpp_event_log:
            ev_id = event_result.get("id")
            if ev_id not in meter_vpp_kwh:
                continue
            actual_kwh = round(float(meter_vpp_kwh[ev_id]), 4)
            event_result["actual_kwh"] = actual_kwh
            if method != "no_dr" and str(ev_id) in _vpp_event_by_id:
                _attach_event_baseline_shed(
                    event_result,
                    _vpp_event_by_id[str(ev_id)],
                    loop.power_trace_rows,
                )
            _update_event_reference_shed_diagnostics(event_result)
            _annotate_event_demand_achievement(event_result)

    kwh = loop.e_wh / 1000; occ = max(loop.occ_h, 1e-6)
    avg_sp = sum(d[1] for d in loop.decisions) / max(1, len(loop.decisions)) if loop.decisions else SP_DEFAULT
    pref_scores = [e["score"] for e in loop.vpp_event_log if e.get("score") is not None]
    if method != "no_dr" and not human_mode and len(pref_scores) != len(vpp_events):
        scored_ids = [str(e.get("id", "?")) for e in loop.vpp_event_log if e.get("score") is not None]
        expected_ids = [str(e.get("id", "?")) for e in vpp_events]
        raise RuntimeError(
            "Every VPP event must receive a role-play score: "
            f"got {len(pref_scores)}/{len(vpp_events)} scored events; "
            f"scored={scored_ids}; expected={expected_ids}"
        )
    comfort_scores = [e["comfort_score"] for e in loop.vpp_event_log if e.get("comfort_score") is not None]
    energy_scores = [e["energy_score"] for e in loop.vpp_event_log if e.get("energy_score") is not None]
    vpp_scores = [e["vpp_score"] for e in loop.vpp_event_log if e.get("vpp_score") is not None]
    # VPP compliance: family baseline 25.5C -> compliant if VPP setpoint >= 26.0C
    _VPP_COMPLY_SP = 26.0
    n_comply = sum(1 for e in loop.vpp_event_log if e.get("setpoint", 0) >= _VPP_COMPLY_SP)
    vpp_comply_rate = n_comply / max(1, len(vpp_events))

    # ── Appliance rule-based indicators ─────────────────────────────────
    appl_vpp_avoid_rate = 0.0; appl_shift_success_rate = 0.0; ev_target_rate = 1.0; ewh_preheat_rate = 1.0
    appl_task_complete_rate = 1.0
    physical_appl_task_complete_rate = 1.0
    present_policy_services = _present_appliance_services(appliance_config)
    emitted_policy_services = _policy_emitted_services_from_decisions(
        [item for day_items in loop.day_agent_decisions for item in day_items]
    )
    covered_policy_services = emitted_policy_services & present_policy_services
    uncovered_policy_services = present_policy_services - covered_policy_services
    absent_policy_services = emitted_policy_services - present_policy_services
    policy_task_completion_rate = (
        len(covered_policy_services) / len(present_policy_services)
        if present_policy_services else 1.0
    )
    _vpp_targets = []
    _vpp_targets_kw = []
    _vpp_achieve_ratio = None
    _task_per_day = []
    _shift_success_per_day = []
    appl_results_dict: dict = {}
    if loop.appliance_suite is not None:
        appl_results_dict = loop.appliance_suite.all_results()
        # Per VPP-event: include all present controllable devices, not just
        # laundry-style shiftable loads. Water heater and EV can satisfy their
        # service goal while still failing VPP avoidance, so they must affect
        # shift_success / vpp_avoid metrics.
        avoid_fracs = []; complete_fracs = []; shift_success_fracs = []
        vpp_day_indices = sorted({
            max(0, min(sim_days - 1, int(_ev.get("day", int(float(_ev["trigger_h"]) // 24) + 1)) - 1))
            for _ev in vpp_events
        })
        for _day_idx in vpp_day_indices:
            _summ = loop.appliance_suite.vpp_day_summary(_day_idx)
            _controllable = {nm: info for nm, info in _summ.items() if info.get("present")}
            if _controllable:
                _completed_n = sum(
                    1 for nm, info in _controllable.items()
                    if _service_completed(nm, info)
                )
                _true_avoided = sum(
                    1 for nm, info in _controllable.items()
                    if _service_completed(nm, info) and not info.get("ran_during_vpp")
                )
                complete_fracs.append(_completed_n / len(_controllable))
                shift_success_fracs.append(_true_avoided / len(_controllable))
                avoid_fracs.append(_true_avoided / max(1, _completed_n))
        appl_vpp_avoid_rate = sum(avoid_fracs) / max(1, len(avoid_fracs))
        physical_appl_task_complete_rate = sum(complete_fracs) / max(1, len(complete_fracs))
        appl_shift_success_rate = sum(shift_success_fracs) / max(1, len(shift_success_fracs))
        # Per-event VPP demand targets from grid-side agent.
        _vpp_targets = [loop.vpp_demand_by_id.get(e["id"], {}).get("target_kwh", 0.0)
                        for e in vpp_events]
        _vpp_targets_kw = [loop.vpp_demand_by_id.get(e["id"], {}).get("target_shed_kw", 0.0)
                           for e in vpp_events]
        _vpp_success_flags = [
            bool(e.get("target_achieved"))
            for e in loop.vpp_event_log
            if e.get("target_achieved") is not None
        ]
        _vpp_achieve_ratio = (
            round(sum(1 for flag in _vpp_success_flags if flag) / len(_vpp_success_flags), 4)
            if _vpp_success_flags else None
        )
        # Per-day completion list (for JSON export and final summary)
        _task_per_day = []
        _shift_success_per_day = []
        for _d in range(sim_days):
            _summ = loop.appliance_suite.vpp_day_summary(_d)
            _controllable = {nm: info for nm, info in _summ.items() if info.get("present")}
            if _controllable:
                _completed = sum(
                    1 for nm, info in _controllable.items()
                    if _service_completed(nm, info)
                )
                _shift_ok = sum(
                    1 for nm, info in _controllable.items()
                    if _service_completed(nm, info) and not info.get("ran_during_vpp")
                )
                _day_emitted = _policy_emitted_services_from_decisions(loop.day_agent_decisions[_d])
                _day_present = _present_appliance_services(appliance_config)
                _task_per_day.append(round(
                    len(_day_emitted & _day_present) / len(_day_present)
                    if _day_present else 1.0,
                    2,
                ))
                _shift_success_per_day.append(round(_shift_ok / max(1, _completed), 2))
            else:
                _task_per_day.append(1.0)
                _shift_success_per_day.append(1.0)
        # EV target SOC reached rate (1.0 when EV not present)
        ev_days = appl_results_dict.get("ev", [])
        if ev_days and ev_days[0].get("present", False):
            ev_target_rate = sum(1 for d in ev_days if d.get("target_reached", False)) / max(1, len(ev_days))
        # EWH preheat usage rate
        wh_days = appl_results_dict.get("water_heater", [])
        if wh_days and wh_days[0].get("present", False):
            ewh_preheat_rate = sum(1 for d in wh_days if d.get("preheat_used", False)) / max(1, len(wh_days))

    print(f"  [family/{method}] exit={ec} energy={kwh:.1f}kWh "
          f"vpp_window={loop.vpp_e_wh/1000:.2f}kWh "
          f"pmv_ok={loop.pmv_ok_h/occ*100:.1f}% "
          f"vpp_scores={pref_scores} vpp_comply={vpp_comply_rate*100:.0f}%")
    print(f"  [LLM stats   ] calls={loop.llm_calls} fail={loop.llm_failures} "
          f"latency={loop.llm_latency_s:.1f}s "
          f"tokens={loop.llm_tokens_prompt}p/{loop.llm_tokens_comp}c")
    if loop.appliance_suite is not None:
        _print_prev_day_completion(loop.appliance_suite, sim_days - 1, sim_days)
    appl_task_complete_rate = policy_task_completion_rate
    print(f"  [Appl rules  ] policy_service_output={appl_task_complete_rate*100:.0f}% "
          f"physical_service_complete={physical_appl_task_complete_rate*100:.0f}% "
          f"completed_vpp_avoid={appl_vpp_avoid_rate*100:.0f}% "
          f"ev_target={ev_target_rate*100:.0f}% ewh_preheat={ewh_preheat_rate*100:.0f}%")

    try:
        from energybridge.data.day_ahead import compute_price_metrics
        price_metrics = compute_price_metrics(
            output_dir,
            price_profile=day_ahead_price_profile,
            start_date=run_start_date,
            sim_days=sim_days,
        )
    except Exception as _pe:
        print(f"  [Day-ahead price] metrics unavailable: {_pe}")
        from energybridge.data.day_ahead import compute_price_metrics
        price_metrics = compute_price_metrics(
            output_dir,
            price_profile=None,
            start_date=None,
            sim_days=sim_days,
        )

    _vpp_total_duration_h = 0.0
    for _ev in vpp_events:
        try:
            _vpp_total_duration_h += max(
                0.0,
                float(_ev.get("end_h", 0.0)) - float(_ev.get("trigger_h", 0.0)),
            )
        except (TypeError, ValueError):
            continue
    _vpp_window_energy_kwh = round(loop.vpp_e_wh / 1000, 4)
    _vpp_window_energy_avg_hour_kwh = (
        round(_vpp_window_energy_kwh / _vpp_total_duration_h, 4)
        if _vpp_total_duration_h > 0.0 else 0.0
    )
    _vpp_shed_total_kwh = 0.0
    _vpp_shed_avg_event_kwh = 0.0
    _vpp_shed_avg_hour_kwh = 0.0
    _vpp_shed_values = []
    for _event_result in loop.vpp_event_log:
        try:
            _shed_value = _event_result.get("actual_shed_kwh")
            if _shed_value is None:
                continue
            _vpp_shed_values.append(max(0.0, float(_shed_value)))
        except (TypeError, ValueError):
            continue
    if _vpp_shed_values:
        _vpp_shed_total_kwh = round(sum(_vpp_shed_values), 6)
        _vpp_shed_avg_event_kwh = round(_vpp_shed_total_kwh / len(_vpp_shed_values), 6)
        _vpp_shed_avg_hour_kwh = (
            round(_vpp_shed_total_kwh / _vpp_total_duration_h, 6)
            if _vpp_total_duration_h > 0.0 else 0.0
        )
        _vpp_shed_basis = "event_baseline_estimate"
    else:
        _vpp_shed_basis = "unavailable_without_valid_event_baseline"
    _gate_events = [
        loop.vpp_plan_gate_by_id.get(str(ev.get("id", "")), {})
        for ev in vpp_events
        if loop.vpp_plan_gate_by_id.get(str(ev.get("id", "")), {})
    ]
    _gate_acceptance_rate = (
        round(sum(1 for item in _gate_events if item.get("accepted")) / len(_gate_events), 6)
        if _gate_events else None
    )
    _gate_acceptance_probability_avg = (
        round(
            sum(float(item.get("acceptance_probability", 0.0)) for item in _gate_events)
            / len(_gate_events),
            6,
        )
        if _gate_events else None
    )
    _gate_rejected_count = sum(1 for item in _gate_events if item and not item.get("accepted"))

    return BenchmarkResult(scenario=f"family/{weather_label}", building="family",
        weather=weather_label, method=method, exit_code=ec,
        sim_days=sim_days,
        start_date=run_start_date.isoformat() if run_start_date else "",
        vpp_schedule_source=loop.vpp_schedule_source,
        energy_kwh_total=kwh, energy_kwh_per_day=kwh/max(1, sim_days),
        daily_energy_kwh=[
            {"day": idx + 1, "energy_kwh": round(value / 1000.0, 6), "source": "runtime"}
            for idx, value in enumerate(loop.daily_e_wh)
        ],
        pmv_ok_fraction=loop.pmv_ok_h/occ, comfort_ok_fraction=loop.comfort_ok_h/occ,
        mean_pmv=loop.pmv_s/occ,
        mean_temp_c=loop.temp_s/occ, unmet_cooling_h=loop.unmet_h,
        vpp_window_energy_kwh=_vpp_window_energy_kwh,
        vpp_window_energy_avg_per_hour_kwh=_vpp_window_energy_avg_hour_kwh,
        vpp_energy_reduction_kwh=_vpp_shed_avg_hour_kwh,
        vpp_actual_shed_kwh=_vpp_shed_avg_hour_kwh,
        vpp_energy_reduction_total_kwh=_vpp_shed_total_kwh,
        vpp_energy_reduction_avg_per_event_kwh=_vpp_shed_avg_event_kwh,
        vpp_energy_reduction_avg_per_hour_kwh=_vpp_shed_avg_hour_kwh,
        vpp_energy_reduction_basis=_vpp_shed_basis,
        agent_setpoint_c=round(avg_sp, 1),
        user_pref_scores=pref_scores,
        user_pref_score=sum(pref_scores)/max(1,len(pref_scores)) if pref_scores else None,
        user_comfort_scores=comfort_scores,
        user_energy_scores=energy_scores,
        user_vpp_scores=vpp_scores,
        vpp_compliance_rate=vpp_comply_rate,
        llm_call_count=loop.llm_calls, llm_call_failures=loop.llm_failures,
        llm_latency_total_s=round(loop.llm_latency_s, 2),
        llm_tokens_prompt=loop.llm_tokens_prompt, llm_tokens_completion=loop.llm_tokens_comp,
        daily_llm_usage=list(getattr(loop, "daily_llm_usage", [])),
        appliance_vpp_avoidance_rate=round(appl_vpp_avoid_rate, 3),
        appliance_task_completion_rate=round(appl_task_complete_rate, 3),
        physical_appliance_task_completion_rate=round(physical_appl_task_complete_rate, 3),
        policy_output_covered_appliance_services=sorted(covered_policy_services),
        policy_output_uncovered_appliance_services=sorted(uncovered_policy_services),
        policy_output_absent_appliance_services=sorted(absent_policy_services),
        appliance_shift_success_rate=round(appl_shift_success_rate, 3),
        task_completion_per_day=_task_per_day if loop.appliance_suite is not None else [],
        task_shift_success_per_day=_shift_success_per_day if loop.appliance_suite is not None else [],
        vpp_demand_targets=_vpp_targets,
        vpp_demand_targets_kw=_vpp_targets_kw,
        vpp_demand_achievement_ratio=_vpp_achieve_ratio,
        vpp_appliance_avoidance_success_rate=_vpp_achieve_ratio,
        ev_target_reached_rate=round(ev_target_rate, 3),
        ewh_preheat_used_rate=round(ewh_preheat_rate, 3),
        appliance_results=appl_results_dict,
        no_dr_routine_actions=list(loop.no_dr_routine_actions),
        day_ahead_price_metrics=price_metrics,
        daily_trace_rows=_dashboard_trace_rows(loop.power_trace_rows),
        vpp_event_log=loop.vpp_event_log,
        vpp_plan_acceptance_rate=_gate_acceptance_rate,
        vpp_plan_acceptance_probability_avg=_gate_acceptance_probability_avg,
        vpp_plan_rejected_count=_gate_rejected_count,
        vpp_plan_gate_events=_gate_events,
        agent_preference_memory_path=str(getattr(loop, "agent_memory_path", "") or ""),
        agent_preference_memory_md_path=str(getattr(loop, "agent_memory_md_path", "") or ""),
        control_decisions=loop.decisions[-50:], output_dir=str(output_dir))

# Compatibility aliases for benchmark scripts that still reference older method names.
run_family_pmv_rule = run_family_pmv
run_family_agent_pmv = run_family_agent

def _read_ep_energy(out_dir):
    """Read total electricity [kWh] from eplustbl.csv (GJ col) or fallback."""
    import csv as _csv
    p = Path(out_dir) / "eplustbl.csv"
    if p.exists():
        try:
            with p.open() as f:
                for line in f:
                    if line.startswith(",Total End Uses,"):
                        cols = line.strip().split(",")
                        if len(cols) > 2:
                            v = cols[2].strip()
                            if v:
                                gj = float(v)
                                if gj > 0:
                                    return round(gj * 277.778, 2)
        except Exception:
            pass
    for fn in ("eplusmtr.csv", "eplusout.csv"):
        p2 = Path(out_dir) / fn
        if not p2.exists(): continue
        try:
            total_j = 0.0
            with p2.open() as f:
                for row in _csv.DictReader(f):
                    for k, v in row.items():
                        if "Electricity:Facility" in k and v:
                            try: total_j += float(v)
                            except: pass
            if total_j > 0: return total_j / 3_600_000
        except Exception: pass
    return 0.0


def _read_ep_vpp_window_energy(out_dir, vpp_events) -> dict[str, float]:
    """Read per-event VPP electricity [kWh] from EnergyPlus timestep meter output."""
    p = Path(out_dir) / "eplusout.mtr"
    if not p.exists():
        return {}

    result = {str(ev["id"]): 0.0 for ev in vpp_events if ev.get("id")}
    current_window = None
    facility_code = None
    in_data = False
    try:
        with p.open(errors="ignore") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
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
                if not in_data:
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
                for ev in vpp_events:
                    ev_id = str(ev.get("id", ""))
                    if not ev_id:
                        continue
                    overlap_h = max(
                        0.0,
                        min(end_h, float(ev["end_h"])) - max(start_h, float(ev["trigger_h"])),
                    )
                    if overlap_h > 0.0:
                        result[ev_id] = result.get(ev_id, 0.0) + kwh * overlap_h / duration_h
    except Exception:
        return {}

    return {key: round(value, 6) for key, value in result.items() if value > 0.0}

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--mode", choices=["pmv","agent"], default="pmv")
    p.add_argument("--epw", default=str(DEFAULT_FAMILY_EPW))
    p.add_argument("--city", default="tianjin")
    a = p.parse_args()
    fn = run_family_pmv if a.mode=="pmv" else run_family_agent
    r = fn(epw_path=Path(a.epw), weather_label=a.city)
    print(json.dumps(r.as_dict(), indent=2, ensure_ascii=False))
