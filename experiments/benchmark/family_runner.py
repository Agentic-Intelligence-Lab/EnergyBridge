"""Family home benchmark runner (PMV or Agent mode) — 3x VPP-1 events per 3-day sim."""
from __future__ import annotations
import hashlib
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

_EXPERIMENTS_DIR = BENCHMARK_DIR.parent
DEFAULT_FAMILY_IDF = _EXPERIMENTS_DIR / "models" / "family_home" / "family_simple_3day.idf"
DEFAULT_FAMILY_EPW = _EXPERIMENTS_DIR / "weather" / "epw" / "CHN_TJ_Tianjin.545270_CSWD.epw"

OCCUPIED_START = 8.0; OCCUPIED_END = 22.0
PMV_MET = 1.1; PMV_CLO = 0.5; PMV_V = 0.1; PMV_RH = 55.0
PMV_DEADBAND = 0.5; SP_MIN = 22.0; SP_MAX = 28.0; SP_STEP = 0.5
SP_DEFAULT = 26.0; HTG_SP = 20.0; UNMET_TOL = 0.556
AC_OFF_FALLBACK_COOLING_SETPOINT = 40.0

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
    control_decisions: List[Tuple[float, float, float]] = field(default_factory=list)
    vpp_event_log: List[dict] = field(default_factory=list)  # scored VPP events with reason
    agent_preference_memory_path: str = ""
    agent_preference_memory_md_path: str = ""
    output_dir: str = ""; error: str = ""
    def as_dict(self):
        _skip = {"control_decisions", "appliance_results"}
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_") and k not in _skip}

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
    if method == "rl_ppo_3day":
        return {"washer", "water_heater"}
    if method == "rl_ppo_pref_v2":
        return {"washer", "dishwasher", "dryer", "water_heater", "ev"}
    if method in ("agent", "eb_rule_milp", "mpc_dynamic", "mpc_ep", "hema_agent", "rule_milp"):
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
    """Conservative same-evening EV charge hours needed after daily driving."""
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
        arrival = float(ev_cfg.get("arrival_h", 18.0)) % 24.0
        departure = float(ev_cfg.get("departure_h", 7.5)) % 24.0
        target_soc = float(ev_cfg.get("target_soc", 0.8)) * 100.0
    except (TypeError, ValueError):
        arrival, departure, target_soc = 18.0, 7.5, 80.0
    min_hours = _ev_required_charge_hours(appliance_config)
    safe_start = arrival
    if vpp_event and _event_start_hod(vpp_event) <= arrival < _event_end_hod(vpp_event):
        safe_start = _event_end_hod(vpp_event)
    safe_start = min(23.4, max(0.0, safe_start))
    safe_end = min(23.9, max(safe_start + 0.5, safe_start + min_hours))
    if safe_end >= 23.9:
        example = f"{safe_start:.1f}-23.9"
    else:
        example = f"{safe_start:.1f}-{safe_end:.1f}"
    return (
        "\nEV service hard rule: the EV target is a departure service target, not just a field-output target. "
        f"The car arrives around {arrival:.1f}h and departs around {departure:.1f}h; schedule enough same-evening "
        f"post-arrival charging to reach about {target_soc:.0f}% SOC. In this simulator, EV charge windows are "
        "stored per simulation day, so a crossing-midnight command such as 23.0-7.5 or a pre-arrival command "
        "such as 0.0-7.0 / 0.0-18.0 does NOT reliably recharge today's arrival. "
        f"Use a same-day, post-arrival, non-VPP window of at least {min_hours:.1f}h; for this event a safe pattern is "
        f"ev_charge_start_h={example.split('-')[0]}, ev_charge_end_h={example.split('-')[1]}. "
        "If previous feedback mentioned EV SOC missed, use the longest available same-evening window ending near 23.9."
    )


def _ev_service_window_errors(
    actions: dict | None,
    appliance_config: dict | None,
    *,
    vpp_event: dict | None = None,
) -> List[str]:
    """Find EV windows that cannot serve today's post-arrival SOC target."""
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
        arrival = float(ev_cfg.get("arrival_h", 18.0)) % 24.0
    except (TypeError, ValueError):
        return ["EV charge window is not numeric"]
    errors: List[str] = []
    if end <= start:
        errors.append(
            f"EV window {start:.1f}-{end:.1f} crosses midnight; this simulator needs same-day post-arrival charging for today's target"
        )
    if end <= arrival:
        errors.append(
            f"EV window {start:.1f}-{end:.1f} ends before arrival {arrival:.1f}, so it cannot recharge after today's trip"
        )
    service_start = max(start, arrival)
    if vpp_event and _event_start_hod(vpp_event) <= service_start < _event_end_hod(vpp_event):
        service_start = _event_end_hod(vpp_event)
    effective_hours = max(0.0, end - service_start) if end > service_start else 0.0
    min_hours = _ev_required_charge_hours(appliance_config)
    if effective_hours + 1e-6 < min_hours:
        errors.append(
            f"EV post-arrival usable charge time is {effective_hours:.1f}h, below required ~{min_hours:.1f}h"
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
        "inside that event window when feasible."
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
    if "water_heater" in present and actions.get("water_heater_preheat") is not False:
        start = actions.get("water_heater_preheat_start_h")
        end = actions.get("water_heater_preheat_end_h")
        try:
            if start is not None and end is not None and _interval_overlaps(float(start), float(end), vpp_start, vpp_end):
                conflicts.append(
                    f"water_heater: preheat {_fmt_clock_h(float(start))}-{_fmt_clock_h(float(end))} overlaps VPP {window_text}"
                )
        except (TypeError, ValueError):
            pass
    if "ev" in present:
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


def _hybrid_rule_milp_setpoint_floor(options: dict | None, cap_c: float) -> float | None:
    """Return the PMV/cost-min cooling target, capped only by run safety bounds."""
    try:
        hvac = (options or {}).get("hvac") or {}
        target = hvac.get("cost_min_pmv_setpoint_c")
        if target is None:
            return None
        return round(min(float(cap_c), float(target)), 1)
    except (TypeError, ValueError):
        return None


def _hybrid_rule_milp_initial_comfort_cap(preferred_max_c: float) -> float:
    """Conservative first-event comfort cap for the hybrid agent."""
    return round(float(preferred_max_c), 1)


def _hybrid_rule_milp_comfort_override_threshold() -> int:
    """Comfort feedback count needed before trading optimality for comfort."""
    try:
        value = int(os.getenv("ENERGYBRIDGE_HYBRID_COMFORT_OVERRIDE_AFTER", "1"))
    except (TypeError, ValueError):
        value = 1
    return max(1, value)


def _hybrid_rule_milp_floor_allowed(past_events: list[dict] | None) -> bool:
    """Keep the PMV/cost floor until warm feedback asks for comfort recovery."""
    return _hybrid_rule_milp_warm_feedback_count(past_events) < _hybrid_rule_milp_comfort_override_threshold()


def _hybrid_rule_milp_warm_feedback_count(past_events: list[dict] | None) -> int:
    """Count events where user/member feedback says the hybrid HVAC was too warm."""
    warm_tokens = (
        "too warm",
        "felt too warm",
        "above my comfort",
        "above comfort",
        "exceeded my comfort",
        "exceeded comfort",
        "comfort boundary",
        "comfort slipped",
        "hot",
        "uncomfortable",
        "temperature drift",
    )
    count = 0
    for event in (past_events or []):
        text = (
            str(event.get("comment", ""))
            + " "
            + str(event.get("controller_feedback", ""))
            + " "
            + str(event.get("member_feedback_summary", ""))
        ).lower()
        if any(token in text for token in warm_tokens):
            count += 1
    return count


def _hybrid_rule_milp_feedback_comfort_cap(past_events: list[dict] | None, preferred_max_c: float) -> float | None:
    warm_count = _hybrid_rule_milp_warm_feedback_count(past_events)
    threshold = _hybrid_rule_milp_comfort_override_threshold()
    if warm_count < threshold:
        return None
    step_down = 0.0
    if warm_count >= threshold + 2:
        step_down = 1.0
    elif warm_count >= threshold + 1:
        step_down = 0.5
    return round(max(22.0, float(preferred_max_c) - step_down), 1)


def _merge_hybrid_rule_milp_actions(
    agent_actions: dict | None,
    hybrid_options: dict | None,
    appliance_config: dict | None,
    *,
    sim_h: float,
) -> tuple[dict, dict]:
    """Use Rule+MILP as the appliance policy for the hybrid method.

    The agent still sees member preferences and can tune HVAC/explanations, but
    appliance timing remains the MILP-selected feasible schedule. This keeps the
    hybrid method comparable to the oracle baseline and prevents sparse
    post-event LLM replies from re-opening completed services or shortening EV
    charge windows.
    """
    milp_selected = ((hybrid_options or {}).get("selected_rule_milp_action") or {})
    milp_actions = _filter_controllable_appliance_actions(milp_selected, appliance_config)
    milp_actions = {key: value for key, value in milp_actions.items() if value is not None}
    milp_actions.update(
        _hybrid_same_day_ev_window(
            milp_actions,
            appliance_config,
            sim_h=sim_h,
            replace_existing=True,
        )
    )
    ev_fallback = _hybrid_explicit_ev_window_if_missing(
        milp_actions,
        appliance_config,
        sim_h=sim_h,
    )
    milp_actions.update(ev_fallback)
    agent_non_null = {key: value for key, value in (agent_actions or {}).items() if value is not None}
    trace = {
        "milp_inherited_keys": sorted(milp_actions),
        "agent_ignored_appliance_keys": sorted(agent_non_null),
        "agent_override_keys": [],
        "ev_policy_fallback": dict(ev_fallback),
        "milp_default_action": dict(milp_actions),
    }
    return dict(milp_actions), trace


def _hybrid_same_day_ev_window(
    actions: dict,
    appliance_config: dict | None,
    *,
    sim_h: float,
    replace_existing: bool,
) -> dict:
    """Return a same-local-day EV window when the existing one crosses midnight."""
    cfg = ((appliance_config or {}).get("ev", {}) or {})
    if not bool(cfg.get("present", False)):
        return {}
    if not replace_existing and (
        actions.get("ev_charge_start_h") is not None
        and actions.get("ev_charge_end_h") is not None
    ):
        return {}
    try:
        existing_start = actions.get("ev_charge_start_h")
        existing_end = actions.get("ev_charge_end_h")
        hod = float(sim_h) % 24.0
        arrival = float(cfg.get("arrival_h", 18.0))
        duration_raw = _ev_required_charge_hours(appliance_config)
        duration = max(0.5, int(duration_raw * 2.0 + 0.999999) / 2.0)
        if replace_existing and existing_start is not None and existing_end is not None:
            start_f = float(existing_start)
            end_f = float(existing_end)
            if end_f > start_f and end_f <= 24.0 and (end_f - start_f) + 1e-6 >= duration:
                return {}
    except (TypeError, ValueError):
        return {}
    start = max(arrival, hod)
    if actions.get("ev_charge_start_h") is not None:
        try:
            start = max(start, float(actions["ev_charge_start_h"]))
        except (TypeError, ValueError):
            pass
    if start < 19.0 and arrival < 19.0:
        start = 19.0
    if start + duration > 24.0:
        start = max(arrival, 24.0 - duration)
        if start < 19.0 and arrival < 19.0:
            start = 19.0
    end = min(24.0, start + duration)
    if end <= start + 1e-6:
        return {}
    return {
        "ev_charge_start_h": round(start, 2),
        "ev_charge_end_h": round(end, 2),
    }


def _hybrid_explicit_ev_window_if_missing(
    actions: dict,
    appliance_config: dict | None,
    *,
    sim_h: float,
) -> dict:
    """Emit an explicit EV policy window when MILP has no active EV work.

    Some days start with the EV already at target SOC, so MILP may correctly
    omit EV from the cost-min action. Role-play scoring still expects a present
    controllable EV to have an explicit policy. A same-day post-VPP window is a
    harmless policy declaration when no charging is needed, and executable when
    charging is needed.
    """
    return _hybrid_same_day_ev_window(
        actions,
        appliance_config,
        sim_h=sim_h,
        replace_existing=False,
    )


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


def _init_agent_preference_memory(
    loop,
    output_dir: Path,
    *,
    method: str,
    persona_config: dict | None,
) -> None:
    """Create optional run-local preference memory files for agent methods."""
    if method not in {"agent", "eb_rule_milp"}:
        return
    persist_memory = str(os.getenv("ENERGYBRIDGE_PERSIST_AGENT_MEMORY", "")).strip().lower() in {
        "1", "true", "yes", "on"
    }
    persona_id = (persona_config or {}).get("id", "unknown_persona")
    memory = {
        "version": "agent_preference_memory_v1",
        "method": method,
        "persona_id": persona_id,
        "purpose": (
            "Run-local memory for EnergyBridge agent methods. It records daily "
            "role-play feedback so later decisions can use learned user "
            "preferences. File persistence is optional and disabled by default."
        ),
        "events": [],
        "learned_preference_rules": [],
        "latest_summary": "No scored VPP feedback yet.",
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
        label = "EB+rule+MILP" if method == "eb_rule_milp" else "EnergyBridge"
        print(f"  [{label} Memory] run-context memory only; set ENERGYBRIDGE_PERSIST_AGENT_MEMORY=1 to write review files.")


def _agent_preference_memory_prompt_text(loop) -> str:
    memory = getattr(loop, "agent_preference_memory", {}) or {}
    if not memory:
        return ""
    prompt_memory = {
        "latest_summary": memory.get("latest_summary", ""),
        "learned_preference_rules": memory.get("learned_preference_rules", []),
        "recent_events": (memory.get("events") or [])[-3:],
    }
    return (
        "\n[AGENT USER MEMORY]\n"
        "Use this run-local memory context as a decision input. It resets for every fresh benchmark run.\n"
        f"{json.dumps(prompt_memory, ensure_ascii=False)}"
    )


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
            "## Latest Summary",
            "",
            str(memory.get("latest_summary", "")),
            "",
            "## Learned Preference Rules",
            "",
        ]
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
    memory["learned_preference_rules"] = list(dict.fromkeys(rules))[:8]
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


def _hybrid_rule_milp_guidance_text(options: dict | None) -> str:
    if not options:
        return ""
    compact = {
        "hvac": options.get("hvac", {}),
        "strategy_options": options.get("strategy_options", []),
        "selected_rule_milp_action": options.get("selected_rule_milp_action", {}),
        "solver": options.get("solver", {}),
        "notes": options.get("notes", []),
    }
    return (
        "\n[EB+rule+MILP CANDIDATES]\n"
        "Rule+MILP proposes appliance schedules that are cost/VPP-optimal under the current physical model. "
        "PMV guidance gives the comfort-feasible AC range. Treat `selected_rule_milp_action` and "
        "`cost_min_pmv_setpoint_c` as the default plan only when it remains inside the strict household comfort ceiling. "
        "For the first event, be conservative: do not exceed the strictest stated comfort cap just to save cost. "
        "Keep this optimality unless clear member feedback states a hard comfort, safety, hot-water, EV-readiness, or deadline preference. Choose among equal-objective MILP "
        "options using preference memory; do not trade cost/VPP optimality for soft preference wording. "
        "Keep appliance timing on the MILP-selected feasible schedule; use the user preference mainly to "
        "explain the action or justify a rare bounded AC adjustment. "
        "Water-heater preheat must stay close to the candidate/configured short window; never stretch it into all-day heating. "
        "Do not cool below the PMV/cost-min setpoint unless a member's stated comfort cap or hard comfort feedback requires it. "
        "In the `reason`, explicitly reassure the household that the MILP plan keeps required services feasible "
        "(EV readiness, hot water, laundry/dishwasher/dryer) and moves non-AC loads outside the VPP window while preserving comfort. "
        "If you deviate from a MILP option, explain the hard user-preference reason in the `reason` field.\n"
        f"{json.dumps(compact, ensure_ascii=False)}"
    )


def _build_hybrid_rule_milp_options(
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
) -> dict:
    from experiments.benchmark.baselines.rule_milp import plan_rule_milp_options

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
    return plan_rule_milp_options(
        state=state_dict,
        price_profile=price_profile,
        run_start_date=run_start_date,
        max_options=5,
    )


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
        "rl": "rl_ppo_3day",
        "rl_ppo": "rl_ppo_3day",
        "rl_ppo_3day": "rl_ppo_3day",
        "rl_ppo_pref_v2": "rl_ppo_pref_v2",
        "rl_pref_v2": "rl_ppo_pref_v2",
        "rl_ppo_v2": "rl_ppo_pref_v2",
        "rule_milp": "rule_milp",
        "rule+milp": "rule_milp",
        "pmv_milp": "rule_milp",
        "eb_rule_milp": "eb_rule_milp",
        "eb+rule+milp": "eb_rule_milp",
        "energybridge_rule_milp": "eb_rule_milp",
        "agent_milp": "eb_rule_milp",
        "agent+milp": "eb_rule_milp",
        "no_dr": "no_dr",
        "none": "no_dr",
        "baseline": "no_dr",
    }.get(str(method or "agent").lower(), method)
    if method not in ("agent", "eb_rule_milp", "mpc_dynamic", "mpc_ep", "rl_ppo_3day", "rl_ppo_pref_v2", "rule_milp", "no_dr", "hema_agent"):
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
    loop.daily_e_wh = [0.0 for _ in range(sim_days)]
    loop.vpp_events = vpp_events
    loop._rl_v2_daily_scheduled: dict[int, set] = {}  # v2 decision cooldown
    loop.vpp_schedule_source = vpp_schedule_source or "daily_default"
    loop.day_agent_decisions = [[] for _ in range(sim_days)]
    loop.daily_plans_done = set()
    loop.next_check = planning_hour
    _init_agent_preference_memory(
        loop,
        output_dir,
        method=method,
        persona_config=persona_config,
    )
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
    _protective_mode = _protective_control_mode(persona_config)
    _auto_saving_mode = _price_sensitive_auto_saving_mode(persona_config)
    _ac_sp_vpp_min = round(_ac_sp_max + 0.5, 1)   # minimum raise during VPP
    _ac_sp_vpp_max = round(_ac_sp_max + 1.5, 1)   # typical VPP raise ceiling
    # Override global SP_MIN based on persona comfort floor
    _run_sp_min = max(SP_MIN, _ac_sp_min - _ac_sp_tol)
    _run_sp_max = min(SP_MAX, _ac_sp_max + 2.0)  # allow VPP raise headroom
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

    _vpp_ac_strategy_text = (
        f"Protective strategy: keep setpoint within {_ac_sp_min:.1f}-{_ac_sp_max:.1f}°C; do not raise above preferred max for VPP."
        if _protective_mode
        else f"AC strategy: raise setpoint to {_ac_sp_vpp_min:.1f}–{_ac_sp_vpp_max:.1f}°C (pre-cool BEFORE event, drift DURING event)."
    )

    _protective_policy = ""
    if _protective_mode:
        _protective_policy = f"""
[PROTECTIVE USER MODE]
This persona has tight comfort bounds, explicit-confirmation needs, vulnerable household members, or very low acceptance of automation.
Treat DR as advisory/minimal-control: keep HVAC within {_ac_sp_min:.1f}-{_ac_sp_max:.1f}°C, do not raise above the preferred max for VPP, and preserve fixed care routines.
If grid goals conflict with comfort, safety, consent, or caregiving routines, choose comfort/safety and explain that only low-risk actions were taken.
"""
    _persona_policy = _persona_agent_policy_text(persona_config)
    _hybrid_policy = ""
    if method == "eb_rule_milp":
        _hybrid_policy = """
[EB+RULE+MILP HYBRID MODE]
You are EnergyBridge augmented with Rule+MILP candidates.
Rule+MILP generates physically feasible, cost/VPP-optimal appliance strategy options; PMV rule gives an AC comfort/efficiency range.
Your default job is to follow the selected Rule+MILP appliance plan and the warmest cost-saving AC setpoint that still stays inside the strict household comfort ceiling.
On the first event, do not wait for negative feedback before respecting a strict comfort cap; never exceed a member's stated maximum just to save cost.
Choose among equal-objective MILP options using live preference and run-local memory.
Only make a bounded suboptimal adjustment when clear member feedback states a hard comfort, safety, hot-water, EV-readiness, or deadline requirement. Soft preferences should not override cost/VPP optimality.
If you deviate from the MILP option, keep every present appliance explicitly controlled, keep VPP-window non-AC avoidance when feasible, and explain the hard user-preference reason briefly.
Even when you do not deviate, use the `reason` field to explain why the plan protects each household priority: comfort, EV departure readiness, hot-water availability, laundry/dishwasher/dryer completion, and VPP-window avoidance.
Use the run-local memory context as learned preference evidence. It resets for each fresh benchmark run.
"""

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
HARD APPLIANCE RULE: on every VPP day, no present controllable appliance may draw controllable load during VPP_WINDOW.
This means washer/dishwasher/dryer cycles must not overlap VPP_WINDOW, water-heater preheat must avoid VPP_WINDOW, and EV charging must use smart/delay or an explicit non-overlapping window.
Plan appliance schedules before the event. Waiting until the VPP start time is too late for preheating or long-cycle tasks.
All flexible schedule commands must be executable from the CURRENT clock time. Do not "fix" an active VPP event by assigning a washer/dishwasher/dryer start time in the past; if a flexible cycle was not already safely completed, move it after VPP_WINDOW. For fixed/routine appliances, keep the stated preferred routine and emit it explicitly.
{_protective_policy}
{_persona_policy}
{_hybrid_policy}

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
  The simulator stores explicit EV windows per same simulation day. Therefore a crossing-midnight window
  like 23.0-7.5, or a morning/pre-arrival window like 0.0-7.0 or 0.0-18.0, does not reliably recharge
  the car after today's evening arrival. Prefer a same-day post-arrival window long enough to cover the
  daily drive energy, e.g. if arrival is about 18.5 and VPP is 18.0-19.0, use about 19.0-23.9.
  Parameters:
    ev_mode             : optional "smart"|"delay"|"normal" metadata only; it is not a substitute for a charging window.
    ev_charge_start_h   : float  — override: begin charging at this hour (e.g. 22.0).
    ev_charge_end_h     : float  — override: stop  charging at this hour (e.g. 7.0 = 07:00 next morning).

Return JSON ONLY (no markdown, no explanation):
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
}}
}}
For every PRESENT appliance, appliance fields must be explicit and non-null as described in the runtime prompt.
Use null only for appliances that are absent from the home, or for optional ev_mode metadata.
All times are hour-of-day (0–23.9)."""

    def _llm_trigger(temp, out_t, hod, sim_h, remaining_h, vpp_active=False, vpp_id="",
                     user_pref_input="", facility_w=None):
        import json as _j
        hh = int(hod % 24)
        vpp_event = _find_active_or_upcoming_vpp_event(
            sim_h,
            vpp_id=vpp_id if vpp_active else "",
            vpp_events=vpp_events,
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
        hybrid_options: dict | None = None
        hybrid_tag = ""
        hybrid_memory_tag = ""
        if method == "eb_rule_milp":
            try:
                hybrid_options = _build_hybrid_rule_milp_options(
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
                )
                hybrid_tag = _hybrid_rule_milp_guidance_text(hybrid_options)
            except Exception as _hme:
                hybrid_tag = f"\n[EB+rule+MILP CANDIDATES unavailable: {str(_hme)[:160]}]"
                hybrid_options = {}
            hybrid_memory_tag = _agent_preference_memory_prompt_text(loop)
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
                  f"user_pref: {user_pref}{user_now_tag}{price_tag}{benefit_tag}{learned_efficiency_tag}{intensity_tag}{appl_tag}{fixed_appliance_tag}{explicit_appliance_tag}{hybrid_tag}{hybrid_memory_tag}{mem_tag}")
        if vpp_active:
            fb_sp = min(_run_sp_max, _ac_sp_default if _protective_mode else 26.5)
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
            _llm_r = LLMClient().chat_with_metrics(
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
            return _j.loads(_llm_r["text"])

        def _prepare_policy_payload(raw_data: dict) -> tuple[dict, dict]:
            data_out = dict(raw_data or {})
            trace_out = {}
            if method == "eb_rule_milp":
                merged_actions, trace_out = _merge_hybrid_rule_milp_actions(
                    data_out.get("appliances", {}),
                    hybrid_options,
                    appliance_config,
                    sim_h=sim_h,
                )
                data_out["appliances"] = merged_actions
            return data_out, trace_out

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
            if verbose:
                print(f"  ┌─[PROMPT | h={sim_h:.1f} sim / {int(hod%24):02d}:00]{'─'*40}")
                for _line in prompt.splitlines():
                    print(f"  │ {_line}")
                print(f"  └{'─'*56}")
            data, hybrid_action_trace = _prepare_policy_payload(_call_llm_json(prompt))
            hard_errors = _hard_policy_errors(data.get("appliances", {}))
            if hard_errors and not vpp_active:
                print("  [Agent Policy Retry] hard policy errors: " + "; ".join(hard_errors))
                correction_prompt = (
                    prompt
                    + "\n\n[HARD POLICY ERROR IN YOUR PREVIOUS JSON]\n"
                    + "\n".join(f"- {err}" for err in hard_errors)
                    + "\nReturn a corrected JSON only. Do not apologize. "
                    "Keep every present appliance explicit. For EV, use a same-day post-arrival window "
                    "long enough to reach target SOC; avoid crossing-midnight or pre-arrival windows.\n"
                    + "[PREVIOUS JSON]\n"
                    + _j.dumps(data, ensure_ascii=False)
                )
                retry_data, retry_trace = _prepare_policy_payload(_call_llm_json(correction_prompt))
                retry_errors = _hard_policy_errors(retry_data.get("appliances", {}))
                if not retry_errors or len(retry_errors) <= len(hard_errors):
                    data = retry_data
                    hybrid_action_trace = retry_trace
                    hard_errors = retry_errors
                    if retry_errors:
                        print("  [Agent Policy Retry] corrected response still has: " + "; ".join(retry_errors))
                    else:
                        print("  [Agent Policy Retry] corrected response passed hard appliance checks")
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
                elif _low_vpp_target_kw(demand_kw) and not _calendar_occupied_or_return_home_sensitive(persona_config, vpp_event):
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
            raw_sp = float(data.get("setpoint", fb_sp))
            sp_lower = locals().get("sp_lower", _energy_saving_sp_floor)
            if learned_floor is not None:
                sp_lower = max(sp_lower, learned_floor)
            if method == "eb_rule_milp":
                # In the hybrid method, Rule+MILP/PMV provides candidates; it
                # should keep the cost-min PMV target only when it is still
                # inside the user's comfort ceiling.  This avoids the first
                # event learning by failing at a too-warm PMV/cost floor.
                hybrid_floor = _hybrid_rule_milp_setpoint_floor(hybrid_options, _run_sp_max)
                initial_cap = _hybrid_rule_milp_initial_comfort_cap(_ac_sp_max)
                sp_upper = min(sp_upper, initial_cap)
                feedback_cap = _hybrid_rule_milp_feedback_comfort_cap(loop.vpp_event_log, _ac_sp_max)
                if feedback_cap is not None:
                    sp_upper = min(sp_upper, feedback_cap)
                if (
                    hybrid_floor is not None
                    and _hybrid_rule_milp_floor_allowed(loop.vpp_event_log)
                    and hybrid_floor <= sp_upper + 1e-6
                ):
                    sp_lower = max(sp_lower, hybrid_floor)
                elif feedback_cap is not None:
                    sp_lower = max(sp_lower, min(sp_upper, feedback_cap))
                sp_lower = min(sp_lower, sp_upper)
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
            if method == "eb_rule_milp":
                # Keep the hybrid benchmark on deterministic decision points:
                # daily plans plus system-triggered VPP start/end. Free-form
                # LLM next_check callbacks slow the matrix and can create
                # post-hoc replans for already scheduled appliance work.
                nch = None
            reason = _comfort_reason_for_low_dr_user(str(data.get("reason", "")), persona_config)
            reason = _ensure_price_sensitive_reason_estimate(
                reason,
                persona_config,
                appliance_config,
                vpp_event,
                prompt_vpp_demand_kw,
            )
            # --- Independent appliance commands from LLM ---
            appl_actions = _filter_controllable_appliance_actions(
                data.get("appliances", {}), appliance_config
            )
            data["appliances"] = appl_actions
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
            if method == "eb_rule_milp":
                result["objective_source"] = "eb_rule_milp_agent_choice_v1"
                result["strategy_trace"] = {
                    "source": "eb_rule_milp_candidates",
                    "candidates": (hybrid_options or {}).get("strategy_options", []),
                    "selected_rule_milp_action": (hybrid_options or {}).get("selected_rule_milp_action", {}),
                    "hvac": (hybrid_options or {}).get("hvac", {}),
                    "solver": (hybrid_options or {}).get("solver", {}),
                    "hybrid_action_merge": hybrid_action_trace,
                    "agent_actions": appl_actions if isinstance(appl_actions, dict) else {},
                    "agent_setpoint": sp,
                    "memory_path": str(getattr(loop, "agent_memory_path", "") or ""),
                }
            return result
        except Exception as e:
            print(f"  [FamilyAgent] LLM error at h={sim_h:.1f}: {e}")
            loop.llm_failures += 1
            fallback["reason"] = ""
            return fallback

    def _mpc_trigger(temp, out_t, hod, sim_h, facility_w=None, vpp_event=None):
        from experiments.benchmark.baselines.mpc import plan_mpc_action

        predictor = "energyplus" if method == "mpc_ep" else "dynamic"
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
        state_dict["mpc_predictor"] = predictor
        state_dict["mpc_horizon_steps"] = mpc_horizon_steps
        state_dict["idf_path"] = str(idf_path)
        state_dict["epw_path"] = str(epw_path)
        state_dict["mpc_ep_output_dir"] = str(output_dir / "_mpc_ep_predictor")
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
        decision = plan_rule_milp_action(
            state=state_dict,
            price_profile=day_ahead_price_profile,
            run_start_date=run_start_date,
        )
        sp = round(max(_run_sp_min, min(_run_sp_max, float(decision.get("setpoint", loop.sp)))), 1)
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

    def _rl_trigger(temp, out_t, hod, sim_h, facility_w=None, vpp_event=None):
        from experiments.benchmark.baselines.rl_ppo_3day import predict_control_result

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
                    loop.appliance_suite,
                    sim_h,
                    target_kw=2.0,
                    duration_minutes=duration_h * 60.0,
                    hvac_context=_capacity_hvac_context(
                        loop,
                        temp=temp,
                        out_t=out_t,
                        facility_w=facility_w or 0.0,
                    ),
                )
            except Exception as exc:
                print(f"  [RL PPO] capacity assessment unavailable: {exc}")
        base_actions = {}
        try:
            decision = predict_control_result(
                loop=loop,
                sim_h=sim_h,
                temp_c=temp,
                outdoor_temp_c=out_t,
                vpp_active=vpp_active_now,
                assessment=capacity.get("assessment", {}),
                appliance_config=appliance_config or {},
                base_actions=base_actions,
                vpp_event=vpp_event,
            )
        except Exception as exc:
            raise RuntimeError(f"RL PPO 3-day policy failed at h={sim_h:.2f}: {exc}") from exc
        sp = round(max(_run_sp_min, min(_run_sp_max, float(decision.get("setpoint", loop.sp)))), 1)
        appliance_actions = _filter_controllable_appliance_actions(
            decision.get("appliance_actions", {}), appliance_config
        )
        model_name = Path(str(decision.get("model_path", ""))).name or "unknown"
        print(
            f"  [RL PPO | h={sim_h:.2f}] setpoint->{sp:.1f}C | "
            f"model={model_name}"
            " | raw_policy_only=1"
        )
        return {
            "setpoint": sp,
            "next_check_hour": decision.get("next_check_hour"),
            "reason": decision.get("reason", ""),
            "appliance_actions": appliance_actions,
            "objective_source": "rl_ppo_3day_policy",
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
            r = score_fn(
                building="family", method=method,
                mean_temp_c=mean_t, pmv_ok_fraction=pmv_ok,
                energy_kwh_per_day=e_day, agent_setpoint_c=sp_w,
                event_index=event_index,
                user_preference_text=loop_ref.vpp_user_input_by_id.get(ev["id"], loop_ref.vpp_user_input),
                agent_reason=loop_ref.vpp_trigger_reason_by_id.get(ev["id"], loop_ref.vpp_last_reason),
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
            strategy_trace = loop_ref.vpp_strategy_trace_by_id.get(ev["id"], {})
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
        if method in {"agent", "eb_rule_milp"}:
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
        hvac_control_occupied = True if method == "no_dr" else occ
        hvac_avail_set = _set_hvac_availability(ex, s, loop, hvac_control_occupied)
        if method == "rule_milp" and occ:
            try:
                from experiments.benchmark.baselines.rule_milp import _choose_pmv_cost_min_setpoint

                rule_sp = _choose_pmv_cost_min_setpoint({
                    "appliance_config": appliance_config or {},
                    "current_setpoint_c": loop.sp,
                    "temp_c": temp,
                })
                loop.sp = round(max(_run_sp_min, min(_run_sp_max, float(rule_sp))), 1)
                loop.planned_occupied_sp = loop.sp
            except Exception as _rme:
                print(f"  [Rule+MILP HVAC] PMV rule failed: {_rme}")

        # Determine current VPP status
        active_vpp = None
        for ev in vpp_events:
            if ev["trigger_h"] <= sim_h < ev["end_h"]:
                active_vpp = ev; break

        if not wu:
            loop.power_trace_rows.append({
                "sim_h": float(sim_h),
                "hod": float(hod),
                "dt_h": float(dt),
                "power_kw": max(0.0, float(fac or 0.0) / 1000.0),
                "facility_power_w": max(0.0, float(fac or 0.0)),
                "outdoor_temperature_c": float(out_t),
                "indoor_temperature_c": float(temp),
                "occupied": bool(occ),
                "vpp_active": active_vpp is not None,
                "vpp_event_id": str(active_vpp.get("id", "")) if active_vpp else "",
            })

        if not wu:
            psim = loop.prev_sim_h
            if method == "no_dr":
                if loop.prev_occupied is None:
                    print(
                        f"  [No-DR HVAC | Day{int(sim_h // 24) + 1} {_fmt_clock_h(hod)}] "
                        "HVAC availability stays on; occupancy is not used for AC shutoff"
                    )
            elif not occ:
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
            triggered_daily_plan = False
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

            if triggered:
                is_vpp = triggered_vpp is not None
                vid = triggered_vpp["id"] if triggered_vpp else ""
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
                    print(f"    AC   : May adjust only within user comfort/consent bounds")
                    print(f"    Other: Shift washer/dishwasher/dryer/EWH/EV away from {_event_window_text(triggered_vpp)}")
                    print(f"  {'='*62}")
                elif triggered_daily_plan:
                    print(
                        f"  --- Day {day_num} daily plan  "
                        f"(sim_h={sim_h:.0f}h  {_fmt_clock_h(planning_hour)} planning) ---"
                    )

                # User in the loop: get roleplay user preference BEFORE agent acts
                if is_vpp and method in ("agent", "eb_rule_milp"):
                    try:
                        if pre_event_preference_callback is None:
                            from user_pref_scorer import get_user_preference_input
                            preference_fn = get_user_preference_input
                        else:
                            preference_fn = pre_event_preference_callback
                        ev_idx = next((i+1 for i,ev in enumerate(vpp_events)
                                       if ev["id"]==vid), 1)
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
                            {"vpp_id": vid, "hour": sim_h, "trigger_h": triggered_vpp["trigger_h"],
                             "end_h": triggered_vpp["end_h"], "day": triggered_vpp.get("day", day_num),
                             "duration_h": ev_duration_h,
                             "appliances": _appl_ctx},
                            loop.vpp_event_log,
                            persona=persona_config,
                            human_mode=human_mode)
                        loop.vpp_user_input = str(_pref_result)
                        loop.vpp_user_input_by_id[vid] = loop.vpp_user_input
                        loop.vpp_strategy_trace_by_id[vid] = dict(
                            getattr(_pref_result, "strategy_trace", {}) or {}
                        )
                    except Exception as _e:
                        print(f"  [UserInput] {_e}")
                        loop.vpp_user_input = ""
                        loop.vpp_user_input_by_id[vid] = ""
                        loop.vpp_strategy_trace_by_id[vid] = {}
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
                elif method in ("mpc_dynamic", "mpc_ep"):
                    res = _mpc_trigger(
                        temp, out_t, hod, sim_h,
                        facility_w=fac,
                        vpp_event=triggered_vpp if is_vpp else None)
                elif method == "rule_milp":
                    rule_vpp_event = (
                        triggered_vpp if is_vpp
                        else _find_active_or_upcoming_vpp_event(sim_h, vpp_events=vpp_events)
                    )
                    res = _rule_milp_trigger(
                        temp, out_t, hod, sim_h,
                        facility_w=fac,
                        vpp_event=rule_vpp_event)
                elif method == "rl_ppo_3day":
                    rl_vpp_event = triggered_vpp if is_vpp else active_vpp
                    res = _rl_trigger(
                        temp, out_t, hod, sim_h,
                        facility_w=fac,
                        vpp_event=rl_vpp_event)
                elif method == "rl_ppo_pref_v2":
                    rl2_vpp_event = triggered_vpp if is_vpp else active_vpp
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
                        vpp_event=triggered_vpp if is_vpp else None,
                        persona_config=persona_config,
                        facility_w=fac,
                    )
                else:
                    res = _llm_trigger(temp, out_t, hod, sim_h, total_sim_hours - sim_h,
                                       vpp_active=is_vpp, vpp_id=vid,
                                       user_pref_input=loop.vpp_user_input,
                                       facility_w=fac)
                    try:
                        res["objective_terms_posthoc"] = _compute_posthoc_decision_objective(
                            loop,
                            action_result=res,
                            sim_h=sim_h,
                            hod=hod,
                            temp=temp,
                            out_t=out_t,
                            facility_w=fac,
                            vpp_event=triggered_vpp if is_vpp else None,
                            vpp_target_kwh=(
                                loop.current_vpp_demand_kwh if is_vpp else None
                            ),
                            appliance_config=appliance_config or {},
                        )
                        if method == "eb_rule_milp":
                            res.setdefault("objective_source", "eb_rule_milp_agent_choice_v1")
                            res["posthoc_objective_source"] = "posthoc_agent_decision_time_pdf_v15"
                        else:
                            res["objective_source"] = "posthoc_agent_decision_time_pdf_v15"
                    except Exception as _oe:
                        print(f"  [Agent Objective] posthoc objective error: {_oe}")
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
                loop.planned_occupied_sp = res["setpoint"]
                effective_sp = res["setpoint"] if hvac_control_occupied or hvac_avail_set else AC_OFF_FALLBACK_COOLING_SETPOINT
                loop.sp = effective_sp
                loop.next_check = res.get("next_check_hour")
                if is_vpp and triggered_vpp is not None:
                    _vpp_end = triggered_vpp["end_h"]
                    if loop.next_check is None or loop.next_check > _vpp_end:
                        loop.next_check = _vpp_end
                loop.vpp_last_reason = res.get("reason", "")
                _day_i = min(sim_days - 1, int(sim_h // 24))
                _non_null = {k: v for k, v in res.get("appliance_actions", {}).items() if v is not None}
                _decision_log = {
                    "h": sim_h,
                    "sp": res["setpoint"],
                    "effective_setpoint": effective_sp,
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
                _append_day_agent_decision(loop, sim_days, sim_h, _decision_log)
                if is_vpp and triggered_vpp is not None:
                    loop.vpp_trigger_actions[vid] = res.get("appliance_actions", {})
                    loop.vpp_trigger_reason_by_id[vid] = res.get("reason", "")
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
        vpp_event_log=loop.vpp_event_log,
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
