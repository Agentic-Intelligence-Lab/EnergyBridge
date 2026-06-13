"""Family home benchmark runner (PMV or Agent mode) — 3x VPP-1 events per 3-day sim."""
from __future__ import annotations
import os, sys, json, shutil

# Fix Windows GBK encoding for Unicode characters (✓ ✗ ⚠)
if sys.platform == 'win32':
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass
from dataclasses import dataclass, field
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

_EXPERIMENTS_DIR = BENCHMARK_DIR.parent
DEFAULT_FAMILY_IDF = _EXPERIMENTS_DIR / "models" / "family_home" / "family_simple_3day.idf"
DEFAULT_FAMILY_EPW = _EXPERIMENTS_DIR / "weather" / "epw" / "CHN_TJ_Tianjin.545270_CSWD.epw"

OCCUPIED_START = 8.0; OCCUPIED_END = 22.0
PMV_MET = 1.1; PMV_CLO = 0.5; PMV_V = 0.1; PMV_RH = 55.0
PMV_DEADBAND = 0.5; SP_MIN = 22.0; SP_MAX = 28.0; SP_STEP = 0.5
SP_DEFAULT = 26.0; HTG_SP = 20.0; UNMET_TOL = 0.556

# 3x VPP-1: same event type, triggered every day at 18:00
# Day1 h=18, Day2 h=42, Day3 h=66 (1-hour demand reduction window)
VPP_EVENTS = [
    {"id": "vpp1", "trigger_h": 18.0, "end_h": 19.0, "day": 1},
    {"id": "vpp2", "trigger_h": 42.0, "end_h": 43.0, "day": 2},
    {"id": "vpp3", "trigger_h": 66.0, "end_h": 67.0, "day": 3},
]

@dataclass
class BenchmarkResult:
    scenario: str = ""; building: str = "family"; weather: str = ""; method: str = ""
    exit_code: int = -1; energy_kwh_total: float = 0.0; energy_kwh_per_day: float = 0.0
    pmv_ok_fraction: float = 0.0; comfort_ok_fraction: float = 0.0
    mean_pmv: float = 0.0; mean_temp_c: float = 0.0
    unmet_cooling_h: float = 0.0
    # VPP energy: actual kWh consumed during the 3x 1-hour demand windows
    vpp_window_energy_kwh: float = 0.0
    vpp_energy_reduction_kwh: float = 0.0
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
    appliance_task_completion_rate: float = 1.0  # fraction of present shiftable tasks that actually completed
    appliance_shift_success_rate: float = 0.0  # fraction of present shiftable tasks completed and shifted outside VPP
    task_completion_per_day: List[float] = field(default_factory=list)  # per-day shiftable completion [day1,day2,day3]
    task_shift_success_per_day: List[float] = field(default_factory=list)  # per-day shift success [day1,day2,day3]
    vpp_demand_targets: List[float] = field(default_factory=list)       # per-event equivalent consumption caps
    vpp_demand_targets_kw: List[float] = field(default_factory=list)    # per-event shed-capacity targets from quantification
    vpp_demand_achievement_ratio: float = 0.0  # sum(actual_shed_kwh) / sum(target_shed_kwh)
    ev_target_reached_rate: float = 0.0         # fraction of days EV reached target SOC
    ewh_preheat_used_rate: float = 0.0          # fraction of days EWH preheat was active
    appliance_results: dict = field(default_factory=dict)  # per-device per-day details
    control_decisions: List[Tuple[float, float, float]] = field(default_factory=list)
    vpp_event_log: List[dict] = field(default_factory=list)  # scored VPP events with reason
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

class _FamilyLoop:
    def __init__(self):
        self.sp = SP_DEFAULT; self.ready = False; self.start_day = None
        self.h_cool = self.h_heat = self.h_temp = self.h_fac = -1
        # Appliance actuator handles (written back to EnergyPlus each timestep)
        self.h_ev = self.h_ewh_sp = -1
        self.h_washer = self.h_dishwasher = self.h_dryer = self.h_refrigerator = -1
        self.e_wh = self.occ_h = self.pmv_ok_h = self.comfort_ok_h = 0.0
        self.pmv_s = self.temp_s = self.unmet_h = 0.0
        self.vpp_e_wh = 0.0                        # energy consumed during VPP windows [Wh]
        self.llm_calls = 0; self.llm_failures = 0  # LLM call counters
        self.llm_latency_s = 0.0                   # cumulative LLM wall-clock latency
        self.llm_tokens_prompt = 0; self.llm_tokens_comp = 0  # OpenAI usage tokens
        self.decisions = []; self.step = 0; self.h_out = -1
        self.next_check: Optional[float] = 8.0   # first LLM trigger (sim-hour)
        self.prev_sim_h: float = -1.0              # for crossing detection
        # VPP per-event tracking
        self.vpp_window_data: Dict[str, Any] = {}  # id -> {temps, pmvs, sp, reason}
        self.vpp_event_log: List[Dict] = []         # scored events in time order
        self.vpp_scored: set = set()                # ids already scored
        self.vpp_mem_ctx: str = ""                  # compressed memory for next LLM call
        self.vpp_user_input: str = ""               # roleplay user preference before agent acts
        self.vpp_last_reason: str = ""              # agent reason from last LLM call
        # Per-event VPP energy tracking and demand-agent outputs
        self.vpp_event_energy_wh: Dict[str, float] = {}   # {event_id: Wh} accumulated per event
        self.vpp_demand_by_id: Dict[str, dict] = {}        # {event_id: {target_kwh, reason}}
        self.vpp_capacity_by_id: Dict[str, dict] = {}      # household capacity assessment sent to VPP
        self.vpp_capacity_window_by_id: Dict[str, List[dict]] = {}  # per-timestep physical capacity
        self.total_quantification_by_id: Dict[str, dict] = {}  # reference A3 90% event capacity
        self.current_vpp_demand_kwh: float = 0.0           # equivalent consumption cap for active VPP event
        self.current_vpp_demand_kw: float = 0.0            # shed-capacity target for active VPP event
        self.current_vpp_capacity: Dict[str, Any] = {}
        self.days_evaluated: set = set()                    # prevent double-printing daily eval
        self.vpp_trigger_actions: Dict[str, dict] = {}      # {event_id: appliance_actions at VPP trigger}
        self.day_agent_decisions: List[List[dict]] = [[], [], []]  # per day: list of {h, sp, actions}
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

    # PMV per-VPP-window tracking (to show PMV doesn't adapt)
    vpp_window_temps: Dict[str, list] = {ev["id"]: [] for ev in VPP_EVENTS}
    vpp_window_pmvs: Dict[str, list] = {ev["id"]: [] for ev in VPP_EVENTS}

    def cb(s):
        if not loop.init(ex, s): return
        day = ex.day_of_year(s)
        if loop.start_day is None: loop.start_day = day
        hod = ex.current_time(s); dt = ex.zone_time_step(s)
        sim_h = (day - loop.start_day)*24 + hod
        wu = ex.warmup_flag(s)
        temp = ex.get_variable_value(s, loop.h_temp) if loop.h_temp!=-1 else SP_DEFAULT
        fac  = ex.get_variable_value(s, loop.h_fac)  if loop.h_fac!=-1  else 0.0
        pmv = _compute_pmv(temp)
        if pmv > PMV_DEADBAND: loop.sp = max(SP_MIN, loop.sp - SP_STEP)
        elif pmv < -PMV_DEADBAND: loop.sp = min(SP_MAX, loop.sp + SP_STEP)
        if loop.h_cool!=-1: ex.set_actuator_value(s, loop.h_cool, loop.sp)
        if loop.h_heat!=-1: ex.set_actuator_value(s, loop.h_heat, HTG_SP)
        if wu: return
        loop.e_wh += fac * dt
        # Collect per-VPP-window data
        for ev in VPP_EVENTS:
            if ev["trigger_h"] <= sim_h < ev["end_h"]:
                vpp_window_temps[ev["id"]].append(temp)
                vpp_window_pmvs[ev["id"]].append(abs(pmv) <= PMV_DEADBAND)
        if _occupied(hod):
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
    print(f"  [Day {day_num} 任务完成评估]")
    any_shown = False
    for nm in ("washer", "dishwasher", "dryer"):
        days_list = results.get(nm, [])
        if not days_list or day_idx >= len(days_list):
            continue
        dr = days_list[day_idx]
        if not dr.get("present", False):
            continue   # not in household — skip entirely
        any_shown = True
        sched_hod = int(dr.get("scheduled_abs_h", 0) % 24)
        if dr.get("skipped"):
            status = "✗ 跳过任务 [agent issued skip — task NOT done]"
        elif not dr.get("completed"):
            status = "✗ 未完成 [scheduled but never ran]"
        elif dr.get("ran_during_vpp"):
            status = f"⚠  完成@{sched_hod:02d}:00 [在VPP窗口内运行, 未错峰]"
        else:
            status = f"✓  完成@{sched_hod:02d}:00 [错峰完成]"
        print(f"    {nm:<14}: {status}")
    wh_days = results.get("water_heater", [])
    if wh_days and day_idx < len(wh_days) and wh_days[day_idx].get("present", False):
        wh = wh_days[day_idx]
        any_shown = True
        ph   = "预热✓" if wh.get("preheat_used") else "预热✗"
        rb   = "浴前就绪✓" if wh.get("ready_at_bath", True) else "浴前就绪✗"
        vfl  = " ⚠VPP中加热" if wh.get("ran_during_vpp") else ""
        print(f"    {'water_heater':<14}: {ph}  {rb}{vfl}  ({wh.get('energy_kwh', 0):.1f}kWh)")
    ev_days = results.get("ev", [])
    if ev_days and day_idx < len(ev_days) and ev_days[day_idx].get("present", False):
        ev = ev_days[day_idx]
        any_shown = True
        tgt  = "SOC达标✓" if ev.get("target_reached") else "SOC未达标✗"
        soc  = ev.get("soc_end", 0)
        vfl  = " ⚠VPP中充电" if ev.get("ran_during_vpp") else ""
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
            ok = suite.set_ev_charge_window(
                day_idx,
                start_h=float(ev_ch_start) if ev_ch_start is not None else None,
                end_h=float(ev_ch_end)     if ev_ch_end   is not None else None,
            )
            print(f"    [Appliance] ev charge_window={ev_ch_start}-{ev_ch_end} -> {'ok' if ok else 'rejected'}")
        except Exception as e:
            print(f"    [Appliance] ev charge_window error: {e}")


def _requested_skip_devices(actions: dict | None) -> List[str]:
    """Return shiftable appliances explicitly marked to skip for the current day."""
    requested = []
    for name in ("washer", "dishwasher", "dryer"):
        if bool((actions or {}).get(f"{name}_skip")):
            requested.append(name)
    return requested


def _present_agent_controlled_appliances(appliance_config: dict | None) -> List[str]:
    """Appliances the Agent must explicitly command on every decision call."""
    cfg = appliance_config or {}
    names = []
    for name in ("washer", "dishwasher", "dryer", "water_heater", "ev"):
        dev_cfg = cfg.get(name, {}) or {}
        if bool(dev_cfg.get("present", False)):
            names.append(name)
    return names


def _default_explicit_appliance_actions(appliance_config: dict | None) -> dict:
    """Safe explicit appliance defaults used in prompts, fallback, and repair checks."""
    actions: dict = {}
    present = set(_present_agent_controlled_appliances(appliance_config))
    for name in ("washer", "dishwasher", "dryer"):
        if name in present:
            actions[f"{name}_start_h"] = 14.0
            actions[f"{name}_skip"] = False
    if "water_heater" in present:
        actions.update({
            "water_heater_preheat_start_h": 14.0,
            "water_heater_preheat_end_h": 18.0,
            "water_heater_preheat_temp_c": 68.0,
            "water_heater_preheat": True,
        })
    if "ev" in present:
        actions.update({
            "ev_mode": "smart",
            "ev_charge_start_h": None,
            "ev_charge_end_h": None,
        })
    return actions


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
    if "ev" in present and actions.get("ev_mode") is None:
        missing.append("ev_mode")
    return missing


def _explicit_appliance_requirement_text(appliance_config: dict | None) -> str:
    """Human-readable prompt text listing the exact non-null fields required now."""
    present = _present_agent_controlled_appliances(appliance_config)
    if not present:
        return "\n[Explicit appliance commands required now]: none; no controlled appliances are present."
    defaults = _default_explicit_appliance_actions(appliance_config)
    return (
        "\n[Explicit appliance commands required now]\n"
        "Every present controllable appliance must receive an explicit non-null command in every JSON response.\n"
        "Do NOT leave present-device fields null to mean default/no-change. Use the safe explicit defaults below if unchanged:\n"
        f"{json.dumps(defaults, ensure_ascii=False, sort_keys=True)}\n"
        "For washer/dishwasher/dryer: provide start_h and skip=false, unless the task is truly unnecessary and skip=true.\n"
        "For water_heater: provide preheat=true/false plus start/end/temp. For EV: provide ev_mode."
    )


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
) -> dict:
    """Build the Protocol A decision-time state shared by MPC and Agent logs."""
    from experiments.benchmark.baselines.state_adapter import build_mpc_state

    return build_mpc_state(
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
        # Per-day LLM schedule overrides (fall back to class defaults if not set)
        ph_start = state.get("preheat_start_h") or wh.pre_heat_window_start_h
        ph_end   = state.get("preheat_end_h")   or wh.pre_heat_window_end_h
        ph_temp  = state.get("preheat_temp_c")  or 65.0
        if state.get("preheat_requested") and ph_start <= hod < ph_end:
            ewh_sp = ph_temp  # LLM-specified (or default) preheat temperature
        elif not state.get("preheat_requested") and wh._normal_on_start <= hod < wh._normal_on_end:
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
                     user_pref="我希望室内舒适，但也愿意在不影响舒适的前提下节约电力。",
                     appliance_config: dict | None = None,
                     persona_config: dict | None = None,
                     verbose: bool = False,
                     human_mode: bool = False,
                     method: str = "agent",
                     mpc_horizon_steps: int = 6):
    """Event-driven LLM control: 3x VPP-1 events (Day1/2/3 18:00). Score after each."""
    method = (method or "agent").lower()
    if method == "mpc":
        method = "mpc_dynamic"
    mpc_horizon_steps = max(1, int(mpc_horizon_steps))
    if method not in ("agent", "mpc_dynamic", "mpc_ep"):
        raise ValueError(f"Unsupported family control method: {method}")
    if output_dir is None:
        output_dir = BENCHMARK_DIR / "results" / f"family_{method}_{weather_label}"
    output_dir = Path(output_dir)
    if output_dir.exists(): shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    import math as _math
    from pyenergyplus.api import EnergyPlusAPI
    loop = _FamilyLoop(); api = EnergyPlusAPI(); state = api.state_manager.new_state()
    ex = api.exchange
    ex.request_variable(state, "Zone Mean Air Temperature", "living_unit1")
    ex.request_variable(state, "Facility Total Electricity Demand Rate", "Whole Building")
    ex.request_variable(state, "Site Outdoor Air Drybulb Temperature", "Environment")
    # Initialise per-appliance independent simulator
    try:
        from energybridge.simulation.appliance_sim import ApplianceSuite
        _acfg = appliance_config or {}
        loop.appliance_suite = ApplianceSuite(_acfg, sim_days=3, vpp_events=VPP_EVENTS)
        print(f"  [ApplianceSuite] loaded: {[k for k,v in _acfg.items() if isinstance(v,dict) and v.get('present',True)]}")
    except Exception as _ae:
        print(f"  [ApplianceSuite] init failed: {_ae}; appliances disabled")
        loop.appliance_suite = None
    try:
        from energybridge.quantification import quantify_agent_vpp_events
        loop.total_quantification_by_id = quantify_agent_vpp_events(VPP_EVENTS)
        print("  [Total Quantification] reference A3 90% event capacities loaded")
    except Exception as _tqe:
        print(f"  [Total Quantification] failed: {_tqe}")
        loop.total_quantification_by_id = {}

    # ── Read persona AC config (from appliances.ac field in persona JSON) ──────
    _ac_cfg   = (appliance_config or {}).get("ac", {})
    _ac_sp_min    = float(_ac_cfg.get("setpoint_preferred_min_c", 24.0))
    _ac_sp_max    = float(_ac_cfg.get("setpoint_preferred_max_c", 26.0))
    _ac_sp_tol    = float(_ac_cfg.get("temp_tolerance_c", 1.0))
    _ac_sp_default = round((_ac_sp_min + _ac_sp_max) / 2, 1)
    _ac_sp_vpp_min = round(_ac_sp_max + 0.5, 1)   # minimum raise during VPP
    _ac_sp_vpp_max = round(_ac_sp_max + 1.5, 1)   # typical VPP raise ceiling
    # Override global SP_MIN based on persona comfort floor
    _run_sp_min = max(SP_MIN, _ac_sp_min - _ac_sp_tol)
    _run_sp_max = min(SP_MAX, _ac_sp_max + 2.0)  # allow VPP raise headroom

    _LLM_SYS_FAM = f"""You are an autonomous AC (air conditioning) and appliance agent for a family home.
SIMULATION: 3 days in July (Tianjin, China). Timestep 10 min. Total 72 hours.
You are called at: (1) start of occupied period each day 08:00, (2) VPP demand-response events 18:00, (3) times you request.

[AC CONTROL]
Occupied hours: 08:00-22:00. Outside this window AC is automatically set to 28°C (standby).
User preferred comfort range: {_ac_sp_min:.1f}–{_ac_sp_max:.1f}°C (tolerance ±{_ac_sp_tol:.1f}°C).
Normal setpoint target: ~{_ac_sp_default:.1f}°C. PMV near 0 at 25.5°C; >+0.5 when zone exceeds 27°C.
Allowed setpoint range: {_run_sp_min:.1f}–{_run_sp_max:.1f}°C.

[VPP DEMAND RESPONSE — 18:00-19:00 each day]
Goal: reduce total electricity consumption for 1 hour to support the grid.
AC strategy: raise setpoint to {_ac_sp_vpp_min:.1f}–{_ac_sp_vpp_max:.1f}°C (pre-cool BEFORE event, drift DURING event).
Appliances: you have full scheduling control over all independent devices (see details below).
IMPORTANT: control each appliance independently. Decisions persist until you change them. Learn from past VPP event scores.

[APPLIANCE CONTROL — default strategy & available parameters]

WASHER / DISHWASHER / DRYER (run once per day)
  Default: run at 14:00 (well before VPP window). Shift earlier or later as needed.
  On VPP days: shift to BEFORE 17:00 so laundry finishes before peak demand.
  Service rule: these tasks should normally still be completed the same day.
  Skip is an exception only when the task is truly unnecessary that day. If you choose skip,
  the system may ask you once to confirm; otherwise reschedule instead.
  Parameters:
    washer_start_h      : float  — hour-of-day to start (e.g. 10.0 = 10:00). Allowed window shown in status.
    washer_skip         : bool   — true = do not run today only if the task is genuinely unnecessary.
    (same pattern for dishwasher_start_h, dishwasher_skip, dryer_start_h, dryer_skip)

WATER HEATER (electric tank, thermal storage)
  Default: preheat 15:00-18:00 at 65°C so hot water is ready by bath time 21:00.
  On VPP days: keep same or extend window earlier (e.g. 13:00-17:00) to avoid heating during 18:00-19:00.
  Hotter tank = more thermal storage = less chance of heating during VPP.
  Parameters:
    water_heater_preheat_start_h : float  — hour-of-day to begin preheating (default 15.0).
    water_heater_preheat_end_h   : float  — hour-of-day to stop preheating  (default 18.0, must be ≤18 on VPP days).
    water_heater_preheat_temp_c  : float  — tank setpoint during preheat, 45–75°C (default 65.0).
    water_heater_preheat         : bool   — true = activate, false = disable (you can omit if setting times).

EV CHARGER (home charger, arrives 18:00, departs 07:30)
  Default (smart mode): charge immediately on arrival, skip VPP window automatically.
  On VPP days: use delay mode OR set explicit window to charge 22:00-07:00 (overnight valley).
  SOC and arrival time shown in status each step.
  Parameters:
    ev_mode             : "smart"|"delay"|"normal"  — smart=avoid-VPP, delay=after-22:00, normal=immediate.
    ev_charge_start_h   : float  — override: begin charging at this hour (e.g. 22.0).
    ev_charge_end_h     : float  — override: stop  charging at this hour (e.g. 7.0 = 07:00 next morning).
    (window override takes priority over ev_mode when set)

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
Use null only for appliances that are absent from the home, or for optional EV charge-window overrides.
All times are hour-of-day (0–23.9)."""

    def _llm_trigger(temp, out_t, hod, sim_h, remaining_h, vpp_active=False, vpp_id="",
                     user_pref_input=""):
        import json as _j
        hh = int(hod % 24)
        if vpp_active:
            _dkwh = getattr(loop, "current_vpp_demand_kwh", None)
            _dkw = getattr(loop, "current_vpp_demand_kw", None)
            if _dkw:
                _dtag = (
                    f"  Grid target: shed ≥{_dkw:.3f}kW for this 1h window"
                    + (f" (equivalent consumption cap ≤{_dkwh:.2f}kWh)." if _dkwh else ".")
                )
            else:
                _dtag = f"  Grid target ≤{_dkwh:.2f}kWh this 1h window." if _dkwh else ""
            _cap = getattr(loop, "current_vpp_capacity", {}).get("assessment", {})
            _ctag = (
                f" Household capacity assessment: committable={float(_cap.get('committable_kw', 0.0)):.2f}kW,"
                f" recommended_bid={float(_cap.get('recommended_bid_kw', 0.0)):.2f}kW,"
                f" constraints={_cap.get('main_constraints', [])}."
            )
            vpp_tag = f"  *** VPP_ACTIVE (event {vpp_id}): reduce load!{_dtag}{_ctag}  User will score your response ***"
        else:
            vpp_tag = ""
        # Post-VPP recovery signal: tell LLM to restore setpoint within 2h after VPP ends
        post_vpp_tag = ""
        if not vpp_active:
            for _ev in VPP_EVENTS:
                if _ev["end_h"] <= sim_h < _ev["end_h"] + 2.0:
                    post_vpp_tag = (f"\n  *** VPP ENDED (event {_ev['id']}):"
                                    f" RESTORE setpoint to comfort range"
                                    f" ({_ac_sp_min:.1f}-{_ac_sp_max:.1f}\u00b0C) immediately."
                                    f" Normal operations resume. ***")
                    break
        mem_tag = loop.vpp_mem_ctx  # contains past event scores + user feedback
        # Current event: user expressed preference before agent acts
        user_now_tag = f"\n[User says NOW]: {user_pref_input}" if user_pref_input else ""
        # Per-appliance status (independent devices)
        if loop.appliance_suite is not None:
            appl_lines = "\n".join(loop.appliance_suite.status_lines(sim_h))
            appl_tag = f"\nAppliances:\n{appl_lines}"
        else:
            appl_tag = ""
        explicit_appliance_tag = _explicit_appliance_requirement_text(appliance_config)
        prompt = (f"sim_hour={sim_h:.1f}  clock={hh:02d}:00{vpp_tag}{post_vpp_tag}\n"
                  f"zone_temp={temp:.1f}C  outdoor={out_t:.1f}C\n"
                  f"remaining_sim_hours={remaining_h:.0f}\n"
                  f"user_pref: {user_pref}{user_now_tag}{appl_tag}{explicit_appliance_tag}{mem_tag}")
        if vpp_active:
            fb_sp, fb_nch = 26.5, None
        else:
            fb_sp = min(26.0, max(SP_MIN, round(temp - 0.5, 1)))
            fb_nch = None
        fallback = {
            "setpoint": fb_sp,
            "next_check_hour": fb_nch,
            "appliance_actions": _default_explicit_appliance_actions(appliance_config),
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

        try:
            from energybridge.llm.client import LLMClient
            if verbose:
                print(f"  ┌─[PROMPT | h={sim_h:.1f} sim / {int(hod%24):02d}:00]{'─'*40}")
                for _line in prompt.splitlines():
                    print(f"  │ {_line}")
                print(f"  └{'─'*56}")
            data = _call_llm_json(prompt)
            missing_explicit = _missing_explicit_appliance_actions(
                data.get("appliances", {}), appliance_config
            )
            if missing_explicit:
                repair_prompt = (
                    f"{prompt}\n\n"
                    "[Explicit appliance command repair required]\n"
                    f"Your previous JSON omitted required present-appliance command fields: {', '.join(missing_explicit)}.\n"
                    "Return the full JSON again. Keep the same AC setpoint if still appropriate, but every present appliance must have explicit non-null settings.\n"
                    "Use the safe defaults from the prompt when you do not want to change an appliance.\n"
                    "Return full JSON only."
                )
                print(
                    "  [Service Rule] missing explicit appliance commands -> asking LLM to repair: "
                    f"{', '.join(missing_explicit)}"
                )
                if verbose:
                    print(f"  ┌─[EXPLICIT APPLIANCE REPAIR PROMPT]{'─'*25}")
                    for _line in repair_prompt.splitlines():
                        print(f"  │ {_line}")
                    print(f"  └{'─'*56}")
                data = _call_llm_json(repair_prompt)
            initial_skip_devices = _requested_skip_devices(data.get("appliances", {}))
            if initial_skip_devices:
                confirm_prompt = (
                    f"{prompt}\n\n"
                    f"[Skip confirmation required]\n"
                    f"You proposed skip for: {', '.join(initial_skip_devices)}.\n"
                    f"Daily service tasks should normally still be completed the same day.\n"
                    f"Please reconsider once.\n"
                    f"If the task is still genuinely unnecessary today, you may keep skip, but state that clearly in reason.\n"
                    f"Otherwise replace skip with a valid start time and set the skip field to false or null.\n"
                    f"Return full JSON only."
                )
                print(
                    "  [Service Rule] skip requested for "
                    f"{', '.join(initial_skip_devices)} -> asking LLM to confirm once"
                )
                if verbose:
                    print(f"  ┌─[SKIP CONFIRM PROMPT]{'─'*42}")
                    for _line in confirm_prompt.splitlines():
                        print(f"  │ {_line}")
                    print(f"  └{'─'*56}")
                data = _call_llm_json(confirm_prompt)
                confirmed_skip_devices = _requested_skip_devices(data.get("appliances", {}))
                if confirmed_skip_devices:
                    print(
                        "  [Service Rule] confirmed skip accepted for "
                        f"{', '.join(confirmed_skip_devices)}"
                    )
                else:
                    print("  [Service Rule] skip revised to schedule/action")
            final_missing_explicit = _missing_explicit_appliance_actions(
                data.get("appliances", {}), appliance_config
            )
            if final_missing_explicit:
                print(
                    "  [Service Rule] still missing explicit appliance commands; applying safe explicit defaults for: "
                    f"{', '.join(final_missing_explicit)}"
                )
                repaired_actions = _default_explicit_appliance_actions(appliance_config)
                for key, value in (data.get("appliances", {}) or {}).items():
                    if value is not None:
                        repaired_actions[key] = value
                data["appliances"] = repaired_actions
            sp = round(max(SP_MIN, min(28.0, float(data.get("setpoint", fb_sp)))), 1)
            nch = data.get("next_check_hour")
            if nch is not None:
                nch = float(nch)
                if nch <= sim_h + 0.25 or nch > 72.0:
                    nch = None
            reason = str(data.get("reason", ""))[:100]
            # --- Independent appliance commands from LLM ---
            appl_actions = data.get("appliances", {})
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
            return {"setpoint": sp, "next_check_hour": nch, "reason": reason,
                    "appliance_actions": appl_actions if isinstance(appl_actions, dict) else {}}
        except Exception as e:
            print(f"  [FamilyAgent] LLM error at h={sim_h:.1f}: {e}")
            loop.llm_failures += 1
            fallback["reason"] = ""
            return fallback

    def _mpc_trigger(temp, out_t, hod, sim_h, vpp_event=None):
        from experiments.benchmark.baselines.mpc import plan_mpc_action

        predictor = "energyplus" if method == "mpc_ep" else "dynamic"
        state_dict = _build_decision_time_state(
            loop,
            sim_h=sim_h,
            hod=hod,
            temp=temp,
            out_t=out_t,
            vpp_event=vpp_event,
            vpp_target_kwh=(
                loop.current_vpp_demand_kwh if vpp_event is not None else None
            ),
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

    def _score_event(ev, loop_ref, sim_h, event_index=1, human_mode: bool = False):
        """Score agent strategy for a VPP event window after it ends (roleplay LLM)."""
        try:
            from user_pref_scorer import score_user_preference
            wd = loop_ref.vpp_window_data.get(ev["id"], {})
            wtemps = wd.get("temps", [])
            wpmvs  = wd.get("pmvs", [])
            sp_w   = wd.get("sp", loop_ref.sp)
            mean_t = sum(wtemps)/max(1,len(wtemps)) if wtemps else loop_ref.temp_s/max(loop_ref.occ_h,1)
            pmv_ok = sum(wpmvs)/max(1,len(wpmvs)) if wpmvs else 0.5
            e_day  = (loop_ref.e_wh/1000) / max(1, sim_h/24)
            appliance_summary = (
                loop_ref.appliance_suite.vpp_day_summary(event_index - 1)
                if loop_ref.appliance_suite is not None else {}
            )
            r = score_user_preference(
                building="family", method=method,
                mean_temp_c=mean_t, pmv_ok_fraction=pmv_ok,
                energy_kwh_per_day=e_day, agent_setpoint_c=sp_w,
                event_index=event_index,
                user_preference_text=loop_ref.vpp_user_input,
                agent_reason=loop_ref.vpp_last_reason,
                persona=persona_config,
                appliance_summary=appliance_summary,
                human_mode=human_mode)
            if r.get("source") != "roleplay_llm":
                raise RuntimeError(f"role-play LLM required, got {r.get('source')}")
            sc = r.get("score") or 0.0
            lbl = r.get("label", "?")
            cmt = r.get("comment", "")[:100]
            src = r.get("source", "?")
            print(f"  [VPP Result | Event {event_index}/3 {ev['id']}] User score: {sc}/5 ({lbl}) | {cmt[:80]}")
            print(f"  {'─'*62}")
            return {"id": ev["id"], "setpoint": sp_w, "score": sc, "label": lbl,
                    "comment": cmt, "user_input": loop_ref.vpp_user_input[:80],
                    "reason": loop_ref.vpp_last_reason[:120], "source": src}
        except Exception as e:
            print(f"  [VPP score {ev['id']}] error: {e}")
            return {"id": ev["id"], "setpoint": loop_ref.sp, "score": None, "label": "?",
                    "comment": str(e)[:60], "user_input": "", "source": "error"}

    def cb(s):
        if not loop.init(ex, s): return
        if loop.h_out == -1:
            loop.h_out = ex.get_variable_handle(s, "Site Outdoor Air Drybulb Temperature", "Environment")
        day = ex.day_of_year(s)
        if loop.start_day is None: loop.start_day = day
        hod = ex.current_time(s); dt = ex.zone_time_step(s)
        sim_h = (day - loop.start_day) * 24 + hod
        wu = ex.warmup_flag(s)
        occ = _occupied(hod)
        temp = ex.get_variable_value(s, loop.h_temp) if loop.h_temp != -1 else SP_DEFAULT
        fac  = ex.get_variable_value(s, loop.h_fac)  if loop.h_fac != -1  else 0.0
        out_t = 30.0
        if loop.h_out != -1:
            v = ex.get_variable_value(s, loop.h_out)
            if v is not None and not _math.isnan(v): out_t = v

        # Determine current VPP status
        active_vpp = None
        for ev in VPP_EVENTS:
            if ev["trigger_h"] <= sim_h < ev["end_h"]:
                active_vpp = ev; break

        if not wu:
            if not occ:
                if method not in ("mpc_dynamic", "mpc_ep"):
                    loop.sp = 28.0   # unoccupied: save energy automatically
                # End-of-day completion check at midnight (24h/48h)
                for _eod_h in (24.0, 48.0):
                    if loop.prev_sim_h < _eod_h <= sim_h and loop.appliance_suite is not None:
                        _day_idx = int(_eod_h / 24) - 1
                        if _day_idx not in loop.days_evaluated:
                            loop.days_evaluated.add(_day_idx)
                            _print_prev_day_completion(loop.appliance_suite, _day_idx, _day_idx + 1)
            else:
                psim = loop.prev_sim_h
                triggered = False
                triggered_vpp = None
                # Trigger 1: crossing into occupied period each day
                if psim >= 0 and (psim % 24) < OCCUPIED_START <= (sim_h % 24):
                    triggered = True
                # Trigger 2: VPP event start crossing
                for ev in VPP_EVENTS:
                    if psim < ev["trigger_h"] <= sim_h:
                        triggered = True; triggered_vpp = ev; break
                # Trigger 3: agent-scheduled next check
                if loop.next_check is not None and psim < loop.next_check <= sim_h:
                    triggered = True
                if triggered:
                    is_vpp = triggered_vpp is not None
                    vid = triggered_vpp["id"] if triggered_vpp else ""
                    day_num = int(sim_h // 24) + 1
                    # Print context banner so logs are human-readable
                    if is_vpp:
                        ev_h_start = int(triggered_vpp["trigger_h"] % 24)
                        ev_h_end   = int(triggered_vpp["end_h"] % 24)
                        ev_idx_n   = next((i+1 for i,e in enumerate(VPP_EVENTS) if e["id"]==vid), 1)
                        # Grid-side VPP demand agent (separate context — no shared history)
                        _prev_vpp_kwh = [loop.vpp_event_energy_wh.get(e["id"], 0.0) / 1000.0
                                         for e in VPP_EVENTS if e["trigger_h"] < triggered_vpp["trigger_h"]]
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
                                    target_kw=2.0, duration_minutes=60.0,
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
                            f"shed_target={loop.current_vpp_demand_kw:.3f}kW "
                            f"(cap={_vpp_demand['target_kwh']:.3f}kWh)  "
                            f"[{_vpp_demand['reason']}]"
                        )
                        print(f"  {'='*62}")
                        print(f"  VPP Demand-Response Event {ev_idx_n}/3  (Day{day_num}  {ev_h_start:02d}:00-{ev_h_end:02d}:00)")
                        print(
                            f"    Goal : Shed ≥{loop.current_vpp_demand_kw:.3f} kW "
                            f"for this 1-hour window "
                            f"(equivalent consumption cap ≤{_vpp_demand['target_kwh']:.2f} kWh)"
                        )
                        print(f"    AC   : Raise setpoint {_ac_sp_vpp_min:.1f}-{_ac_sp_vpp_max:.1f}°C  (pre-cool before, drift during)")
                        print(f"    Other: Shift washer/EWH preheat/EV delay away from 18:00-19:00")
                        print(f"  {'='*62}")
                    else:
                        hh = int(sim_h % 24)
                        if hh == int(OCCUPIED_START):
                            print(f"  --- Day {day_num} start  (sim_h={sim_h:.0f}h  08:00 occupied period begins) ---")

                    # User in the loop: get roleplay user preference BEFORE agent acts
                    if is_vpp and method == "agent":
                        try:
                            from user_pref_scorer import get_user_preference_input
                            ev_idx = next((i+1 for i,ev in enumerate(VPP_EVENTS)
                                           if ev["id"]==vid), 1)
                            _acfg = appliance_config or {}
                            _appl_ctx = {
                                "washer": bool((_acfg.get("washer", {}) or {}).get("present", False)),
                                "dishwasher": bool((_acfg.get("dishwasher", {}) or {}).get("present", False)),
                                "dryer": bool((_acfg.get("dryer", {}) or {}).get("present", False)),
                                "water_heater": bool((_acfg.get("water_heater", {}) or {}).get("present", False)),
                                "ev": bool((_acfg.get("ev", {}) or {}).get("present", False)),
                            }
                            loop.vpp_user_input = get_user_preference_input(
                                "family", ev_idx,
                                {"vpp_id": vid, "hour": sim_h, "duration_h": 1.0,
                                 "appliances": _appl_ctx},
                                loop.vpp_event_log,
                                persona=persona_config,
                                human_mode=human_mode)
                        except Exception as _e:
                            print(f"  [UserInput] {_e}")
                            loop.vpp_user_input = ""
                    else:
                        loop.vpp_user_input = ""
                    if method in ("mpc_dynamic", "mpc_ep"):
                        res = _mpc_trigger(
                            temp, out_t, hod, sim_h,
                            vpp_event=triggered_vpp if is_vpp else None)
                    else:
                        res = _llm_trigger(temp, out_t, hod, sim_h, 72.0 - sim_h,
                                           vpp_active=is_vpp, vpp_id=vid,
                                           user_pref_input=loop.vpp_user_input)
                        try:
                            res["objective_terms_posthoc"] = _compute_posthoc_decision_objective(
                                loop,
                                action_result=res,
                                sim_h=sim_h,
                                hod=hod,
                                temp=temp,
                                out_t=out_t,
                                vpp_event=triggered_vpp if is_vpp else None,
                                vpp_target_kwh=(
                                    loop.current_vpp_demand_kwh if is_vpp else None
                                ),
                                appliance_config=appliance_config or {},
                            )
                            res["objective_source"] = "posthoc_agent_decision_time_pdf_v15"
                        except Exception as _oe:
                            print(f"  [Agent Objective] posthoc objective error: {_oe}")
                    loop.sp = res["setpoint"]
                    loop.next_check = res.get("next_check_hour")
                    # Guarantee a post-VPP check: if LLM didn't schedule one at/before
                    # VPP end_h, force next_check = end_h so "VPP ENDED" signal fires.
                    if is_vpp and triggered_vpp is not None:
                        _vpp_end = triggered_vpp["end_h"]
                        if loop.next_check is None or loop.next_check > _vpp_end:
                            loop.next_check = _vpp_end
                    loop.vpp_last_reason = res.get("reason", "")
                    # Record this controller decision for daily/event logs.
                    _day_i = min(2, int(sim_h // 24))
                    _non_null = {k: v for k, v in res.get("appliance_actions", {}).items() if v is not None}
                    _decision_log = {
                        "h": sim_h,
                        "sp": res["setpoint"],
                        "reason": res.get("reason", ""),
                        "actions": _non_null,
                        "raw_appliance_actions": res.get("appliance_actions", {}),
                    }
                    if res.get("objective_source"):
                        _decision_log["objective_source"] = res.get("objective_source")
                    if res.get("objective_terms"):
                        _decision_log["objective_terms"] = res.get("objective_terms", {})
                    if res.get("objective_terms_posthoc"):
                        _decision_log["objective_terms_posthoc"] = res.get("objective_terms_posthoc", {})
                    loop.day_agent_decisions[_day_i].append(_decision_log)
                    # Store VPP-trigger actions separately
                    if is_vpp and triggered_vpp is not None:
                        loop.vpp_trigger_actions[vid] = res.get("appliance_actions", {})
                    # Apply independent per-appliance actions through existing path.
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
                        _step_capacity = assess_suite_vpp_request(
                            loop.appliance_suite, sim_h,
                            target_kw=2.0, duration_minutes=60.0,
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

            # Score VPP event after its window ends
            psim = loop.prev_sim_h
            for ev in VPP_EVENTS:
                if psim < ev["end_h"] <= sim_h and ev["id"] not in loop.vpp_scored:
                    ev_idx = next((i+1 for i,e in enumerate(VPP_EVENTS) if e["id"]==ev["id"]), 1)
                    result = _score_event(ev, loop, sim_h, event_index=ev_idx, human_mode=human_mode)
                    # Attach actual energy and demand targets to event log.
                    _demand = loop.vpp_demand_by_id.get(ev["id"], {})
                    result["actual_kwh"] = round(loop.vpp_event_energy_wh.get(ev["id"], 0.0) / 1000.0, 4)
                    result["demand_target_kwh"] = _demand.get("target_kwh", None)
                    result["demand_baseline_kwh"] = _demand.get("baseline_kwh", None)
                    result["demand_target_kw"] = _demand.get("target_shed_kw", None)
                    result["demand_target_shed_kwh"] = _demand.get("target_shed_kwh", None)
                    if result["demand_baseline_kwh"] is not None:
                        result["actual_shed_kwh"] = round(
                            max(0.0, float(result["demand_baseline_kwh"]) - result["actual_kwh"]),
                            4,
                        )
                    result["capacity_assessment"] = loop.vpp_capacity_by_id.get(ev["id"], {})
                    _cap_rows = loop.vpp_capacity_window_by_id.get(ev["id"], [])
                    if _cap_rows:
                        _ncap = len(_cap_rows)
                        result["capacity_window_summary"] = {
                            "method": "state_physical_with_optional_baseline",
                            "steps": _ncap,
                            "avg_committable_kw": round(
                                sum(r["committable_kw"] for r in _cap_rows) / _ncap, 6),
                            "firm_min_committable_kw": round(
                                min(r["committable_kw"] for r in _cap_rows), 6),
                            "committable_energy_kwh": round(
                                sum(r["committable_kw"] * r["dt_h"] for r in _cap_rows), 6),
                            "avg_recommended_bid_kw": round(
                                sum(r["recommended_bid_kw"] for r in _cap_rows) / _ncap, 6),
                            "firm_min_recommended_bid_kw": round(
                                min(r["recommended_bid_kw"] for r in _cap_rows), 6),
                            "recommended_bid_energy_kwh": round(
                                sum(r["recommended_bid_kw"] * r["dt_h"] for r in _cap_rows), 6),
                            "avg_success_probability": round(
                                sum(r["success_probability"] for r in _cap_rows) / _ncap, 6),
                        }
                    result["total_quantification_90"] = loop.total_quantification_by_id.get(
                        ev["id"],
                        {"status": "not_computed", "reason": "Reference A3 quantification unavailable"},
                    )
                    # Store all agent decisions for this day
                    result["vpp_trigger_actions"] = loop.vpp_trigger_actions.get(ev["id"], {})
                    result["day_decisions"] = loop.day_agent_decisions[ev_idx - 1]
                    # Attach per-appliance VPP summary to event log
                    if loop.appliance_suite is not None:
                        result["appliance_summary"] = loop.appliance_suite.vpp_day_summary(ev_idx - 1)
                    loop.vpp_event_log.append(result)
                    loop.vpp_scored.add(ev["id"])
                    # Update memory context for subsequent LLM calls
                    mem_entries = [{"event": e["id"], "sp": e["setpoint"],
                                    "score": e["score"],
                                    "user_said": e.get("user_input","")[:50],
                                    "feedback": e["comment"][:60]}
                                   for e in loop.vpp_event_log]
                    loop.vpp_mem_ctx = ("\nPast VPP responses (user in the loop): "
                                        + json.dumps(mem_entries, ensure_ascii=False))

        if loop.h_cool != -1: ex.set_actuator_value(s, loop.h_cool, loop.sp)
        if loop.h_heat != -1: ex.set_actuator_value(s, loop.h_heat, HTG_SP)

        loop.prev_sim_h = sim_h
        if wu: return
        pmv = _compute_pmv(temp)
        loop.e_wh += fac * dt
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

    kwh = loop.e_wh / 1000; occ = max(loop.occ_h, 1e-6)
    avg_sp = sum(d[1] for d in loop.decisions) / max(1, len(loop.decisions)) if loop.decisions else SP_DEFAULT
    pref_scores = [e["score"] for e in loop.vpp_event_log if e.get("score") is not None]
    # VPP compliance: family baseline 25.5C -> compliant if VPP setpoint >= 26.0C
    _VPP_COMPLY_SP = 26.0
    n_comply = sum(1 for e in loop.vpp_event_log if e.get("setpoint", 0) >= _VPP_COMPLY_SP)
    vpp_comply_rate = n_comply / max(1, len(VPP_EVENTS))

    # ── Appliance rule-based indicators ─────────────────────────────────
    appl_vpp_avoid_rate = 0.0; appl_shift_success_rate = 0.0; ev_target_rate = 1.0; ewh_preheat_rate = 1.0
    appl_results_dict: dict = {}
    if loop.appliance_suite is not None:
        appl_results_dict = loop.appliance_suite.all_results()
        # Per VPP-event: include all present controllable devices, not just
        # laundry-style shiftable loads. Water heater and EV can satisfy their
        # service goal while still failing VPP avoidance, so they must affect
        # shift_success / vpp_avoid metrics.
        avoid_fracs = []; complete_fracs = []; shift_success_fracs = []
        for _day_idx, _ev in enumerate(VPP_EVENTS):
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
        appl_task_complete_rate = sum(complete_fracs) / max(1, len(complete_fracs))
        appl_shift_success_rate = sum(shift_success_fracs) / max(1, len(shift_success_fracs))
        # Per-event VPP demand targets from grid-side agent.
        _vpp_targets = [loop.vpp_demand_by_id.get(e["id"], {}).get("target_kwh", 0.0)
                        for e in VPP_EVENTS]
        _vpp_targets_kw = [loop.vpp_demand_by_id.get(e["id"], {}).get("target_shed_kw", 0.0)
                           for e in VPP_EVENTS]
        _vpp_shed_targets = [loop.vpp_demand_by_id.get(e["id"], {}).get("target_shed_kwh", 0.0)
                             for e in VPP_EVENTS]
        _vpp_actual_shed_total = 0.0
        for e in VPP_EVENTS:
            _demand = loop.vpp_demand_by_id.get(e["id"], {})
            _baseline = float(_demand.get("baseline_kwh", 0.0) or 0.0)
            _actual = loop.vpp_event_energy_wh.get(e["id"], 0.0) / 1000.0
            _vpp_actual_shed_total += max(0.0, _baseline - _actual)
        _vpp_shed_target_total = sum(v for v in _vpp_shed_targets if v > 0)
        _vpp_achieve_ratio = (
            round(_vpp_actual_shed_total / _vpp_shed_target_total, 4)
            if _vpp_shed_target_total > 0 else 0.0
        )
        # Per-day completion list (for JSON export and final summary)
        _task_per_day = []
        _shift_success_per_day = []
        for _d in range(3):
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
                _task_per_day.append(round(_completed / len(_controllable), 2))
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
        _print_prev_day_completion(loop.appliance_suite, 2, 3)
    print(f"  [Appl rules  ] service_complete={appl_task_complete_rate*100:.0f}% "
          f"completed_vpp_avoid={appl_vpp_avoid_rate*100:.0f}% "
          f"ev_target={ev_target_rate*100:.0f}% ewh_preheat={ewh_preheat_rate*100:.0f}%")
    return BenchmarkResult(scenario=f"family/{weather_label}", building="family",
        weather=weather_label, method=method, exit_code=ec,
        energy_kwh_total=kwh, energy_kwh_per_day=kwh/3,
        pmv_ok_fraction=loop.pmv_ok_h/occ, comfort_ok_fraction=loop.comfort_ok_h/occ,
        mean_pmv=loop.pmv_s/occ,
        mean_temp_c=loop.temp_s/occ, unmet_cooling_h=loop.unmet_h,
        vpp_window_energy_kwh=round(loop.vpp_e_wh / 1000, 4),
        agent_setpoint_c=round(avg_sp, 1),
        user_pref_scores=pref_scores,
        user_pref_score=sum(pref_scores)/max(1,len(pref_scores)) if pref_scores else None,
        vpp_compliance_rate=vpp_comply_rate,
        llm_call_count=loop.llm_calls, llm_call_failures=loop.llm_failures,
        llm_latency_total_s=round(loop.llm_latency_s, 2),
        llm_tokens_prompt=loop.llm_tokens_prompt, llm_tokens_completion=loop.llm_tokens_comp,
        appliance_vpp_avoidance_rate=round(appl_vpp_avoid_rate, 3),
        appliance_task_completion_rate=round(appl_task_complete_rate, 3),
        appliance_shift_success_rate=round(appl_shift_success_rate, 3),
        task_completion_per_day=_task_per_day if loop.appliance_suite is not None else [],
        task_shift_success_per_day=_shift_success_per_day if loop.appliance_suite is not None else [],
        vpp_demand_targets=_vpp_targets,
        vpp_demand_targets_kw=_vpp_targets_kw,
        vpp_demand_achievement_ratio=_vpp_achieve_ratio,
        ev_target_reached_rate=round(ev_target_rate, 3),
        ewh_preheat_used_rate=round(ewh_preheat_rate, 3),
        appliance_results=appl_results_dict,
        vpp_event_log=loop.vpp_event_log,
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
