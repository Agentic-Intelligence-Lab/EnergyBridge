"""Family home benchmark runner (PMV or Agent mode) — 3x VPP-1 events per 3-day sim."""
from __future__ import annotations
import sys, json, shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

EPLUS_ROOT = Path("/home/ha_agent/EnergyPlus-24-1-0")
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
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
    pmv_ok_fraction: float = 0.0; mean_pmv: float = 0.0; mean_temp_c: float = 0.0
    unmet_cooling_h: float = 0.0
    # VPP energy: actual kWh consumed during the 3x 1-hour demand windows
    vpp_window_energy_kwh: float = 0.0
    agent_setpoint_c: Optional[float] = None
    # User satisfaction (roleplay LLM evaluation, per VPP event)
    user_pref_score: Optional[float] = None       # average across events
    user_pref_scores: List[float] = field(default_factory=list)  # per-event [e1,e2,e3]
    vpp_compliance_rate: float = 0.0  # fraction of VPP events where setpoint >= 26.0C
    user_pref_comment: str = ""
    # LLM performance metrics
    llm_call_count: int = 0; llm_call_failures: int = 0
    llm_latency_total_s: float = 0.0
    llm_tokens_prompt: int = 0; llm_tokens_completion: int = 0
    # Appliance rule-based indicators
    appliance_vpp_avoidance_rate: float = 0.0   # avg fraction of present devices avoiding VPP
    ev_target_reached_rate: float = 0.0         # fraction of days EV reached target SOC
    ewh_preheat_used_rate: float = 0.0          # fraction of days EWH preheat was active
    appliance_results: dict = field(default_factory=dict)  # per-device per-day details
    control_decisions: List[Tuple[float, float, float]] = field(default_factory=list)
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
        self.e_wh = self.occ_h = self.pmv_ok_h = self.pmv_s = self.temp_s = self.unmet_h = 0.0
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
                     verbose: bool = False,
                     human_mode: bool = False):
    """Event-driven LLM control: 3x VPP-1 events (Day1/2/3 18:00). Score after each."""
    if output_dir is None:
        output_dir = BENCHMARK_DIR / "results" / f"family_agent_{weather_label}"
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
  Parameters:
    washer_start_h      : float  — hour-of-day to start (e.g. 10.0 = 10:00). Allowed window shown in status.
    washer_skip         : bool   — true = do not run today (e.g. no laundry needed).
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
   "washer_start_h": null_or_float,
   "washer_skip": null_or_bool,
   "dishwasher_start_h": null_or_float,
   "dishwasher_skip": null_or_bool,
   "dryer_start_h": null_or_float,
   "dryer_skip": null_or_bool,
   "water_heater_preheat_start_h": null_or_float,
   "water_heater_preheat_end_h": null_or_float,
   "water_heater_preheat_temp_c": null_or_float,
   "water_heater_preheat": null_or_bool,
   "ev_mode": null_or_"smart"|"delay"|"normal",
   "ev_charge_start_h": null_or_float,
   "ev_charge_end_h": null_or_float
 }}
}}
null means no change / keep current. All times are hour-of-day (0–23.9)."""

    def _llm_trigger(temp, out_t, hod, sim_h, remaining_h, vpp_active=False, vpp_id="",
                     user_pref_input=""):
        import json as _j
        hh = int(hod % 24)
        vpp_tag = f"  *** VPP_ACTIVE (event {vpp_id}): reduce load! user will score your response ***" if vpp_active else ""
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
        prompt = (f"sim_hour={sim_h:.1f}  clock={hh:02d}:00{vpp_tag}{post_vpp_tag}\n"
                  f"zone_temp={temp:.1f}C  outdoor={out_t:.1f}C\n"
                  f"remaining_sim_hours={remaining_h:.0f}\n"
                  f"user_pref: {user_pref}{user_now_tag}{appl_tag}{mem_tag}")
        if vpp_active:
            fb_sp, fb_nch = 26.5, None
        else:
            fb_sp = min(26.0, max(SP_MIN, round(temp - 0.5, 1)))
            fb_nch = None
        fallback = {"setpoint": fb_sp, "next_check_hour": fb_nch}
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

        try:
            from energybridge.llm.client import LLMClient
            if verbose:
                print(f"  ┌─[PROMPT | h={sim_h:.1f} sim / {int(hod%24):02d}:00]{'─'*40}")
                for _line in prompt.splitlines():
                    print(f"  │ {_line}")
                print(f"  └{'─'*56}")
            _llm_r = LLMClient().chat_with_metrics(_LLM_SYS_FAM, prompt,
                                    max_retries=5, retry_base_delay=2.0,
                                    validate_fn=_validate_json)
            resp = _llm_r["text"]
            _m = _llm_r.get("metrics", {}); _tu = _m.get("token_usage", {})
            loop.llm_calls += 1
            loop.llm_latency_s += _m.get("latency_seconds", 0.0)
            loop.llm_tokens_prompt += _tu.get("prompt_tokens", 0)
            loop.llm_tokens_comp   += _tu.get("completion_tokens", 0)
            data = _j.loads(resp)
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

    def _score_event(ev, loop_ref, sim_h, event_index=1):
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
            r = score_user_preference(
                building="family", method="agent",
                mean_temp_c=mean_t, pmv_ok_fraction=pmv_ok,
                energy_kwh_per_day=e_day, agent_setpoint_c=sp_w,
                event_index=event_index,
                user_preference_text=loop_ref.vpp_user_input,
                agent_reason=loop_ref.vpp_last_reason)
            sc = r.get("score") or 0.0
            lbl = r.get("label", "?")
            cmt = r.get("comment", "")[:100]
            src = r.get("source", "?")
            print(f"  [VPP Result | Event {event_index}/3 {ev['id']}] User score: {sc}/5 ({lbl}) | {cmt[:80]}")
            print(f"  {'─'*62}")
            return {"id": ev["id"], "setpoint": sp_w, "score": sc, "label": lbl,
                    "comment": cmt, "user_input": loop_ref.vpp_user_input[:80], "source": src}
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
                loop.sp = 28.0   # unoccupied: save energy automatically
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
                        print(f"  {'='*62}")
                        print(f"  VPP Demand-Response Event {ev_idx_n}/3  (Day{day_num}  {ev_h_start:02d}:00-{ev_h_end:02d}:00)")
                        print(f"    Goal : Reduce total electricity for 1 hour (grid peak-shaving)")
                        print(f"    AC   : Raise setpoint {_ac_sp_vpp_min:.1f}-{_ac_sp_vpp_max:.1f}°C  (pre-cool before, drift during)")
                        print(f"    Other: Shift washer/EWH preheat/EV delay away from 18:00-19:00")
                        print(f"  {'='*62}")
                    else:
                        hh = int(sim_h % 24)
                        if hh == int(OCCUPIED_START):
                            print(f"  --- Day {day_num} start  (sim_h={sim_h:.0f}h  08:00 occupied period begins) ---")
                    # User in the loop: get roleplay user preference BEFORE agent acts
                    if is_vpp:
                        try:
                            from user_pref_scorer import get_user_preference_input
                            ev_idx = next((i+1 for i,ev in enumerate(VPP_EVENTS)
                                           if ev["id"]==vid), 1)
                            loop.vpp_user_input = get_user_preference_input(
                                "family", ev_idx,
                                {"vpp_id": vid, "hour": sim_h, "duration_h": 1.0},
                                loop.vpp_event_log,
                                human_mode=human_mode)
                        except Exception as _e:
                            print(f"  [UserInput] {_e}")
                            loop.vpp_user_input = ""
                    else:
                        loop.vpp_user_input = ""
                    res = _llm_trigger(temp, out_t, hod, sim_h, 72.0 - sim_h,
                                       vpp_active=is_vpp, vpp_id=vid,
                                       user_pref_input=loop.vpp_user_input)
                    loop.sp = res["setpoint"]
                    loop.next_check = res.get("next_check_hour")
                    # Guarantee a post-VPP check: if LLM didn't schedule one at/before
                    # VPP end_h, force next_check = end_h so "VPP ENDED" signal fires.
                    if is_vpp and triggered_vpp is not None:
                        _vpp_end = triggered_vpp["end_h"]
                        if loop.next_check is None or loop.next_check > _vpp_end:
                            loop.next_check = _vpp_end
                    loop.vpp_last_reason = res.get("reason", "")
                    # Apply independent per-appliance actions from LLM
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

            # Score VPP event after its window ends
            psim = loop.prev_sim_h
            for ev in VPP_EVENTS:
                if psim < ev["end_h"] <= sim_h and ev["id"] not in loop.vpp_scored:
                    ev_idx = next((i+1 for i,e in enumerate(VPP_EVENTS) if e["id"]==ev["id"]), 1)
                    result = _score_event(ev, loop, sim_h, event_index=ev_idx)
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
        # Step appliance suite and write powers back to EnergyPlus each timestep
        if loop.appliance_suite is not None:
            _appl_powers = loop.appliance_suite.step(sim_h, dt)
            _write_appliance_actuators(ex, s, loop, _appl_powers, sim_h)
        if occ:
            loop.occ_h += dt; loop.pmv_s += pmv * dt; loop.temp_s += temp * dt
            if abs(pmv) <= PMV_DEADBAND: loop.pmv_ok_h += dt
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
    appl_vpp_avoid_rate = 0.0; ev_target_rate = 1.0; ewh_preheat_rate = 1.0
    appl_results_dict: dict = {}
    if loop.appliance_suite is not None:
        appl_results_dict = loop.appliance_suite.all_results()
        # Per VPP-event: fraction of present controllable devices that avoided VPP window
        avoid_fracs = []
        for _day_idx, _ev in enumerate(VPP_EVENTS):
            _summ = loop.appliance_suite.vpp_day_summary(_day_idx)
            _present = [(nm, info) for nm, info in _summ.items() if info.get("present")]
            if _present:
                _avoided = sum(1 for _, info in _present if not info.get("ran_during_vpp", False))
                avoid_fracs.append(_avoided / len(_present))
        appl_vpp_avoid_rate = sum(avoid_fracs) / max(1, len(avoid_fracs))
        # EV target SOC reached rate (1.0 when EV not present)
        ev_days = appl_results_dict.get("ev", [])
        if ev_days and ev_days[0].get("present", False):
            ev_target_rate = sum(1 for d in ev_days if d.get("target_reached", False)) / max(1, len(ev_days))
        # EWH preheat usage rate
        wh_days = appl_results_dict.get("water_heater", [])
        if wh_days and wh_days[0].get("present", False):
            ewh_preheat_rate = sum(1 for d in wh_days if d.get("preheat_used", False)) / max(1, len(wh_days))

    print(f"  [family/agent] exit={ec} energy={kwh:.1f}kWh "
          f"vpp_window={loop.vpp_e_wh/1000:.2f}kWh "
          f"pmv_ok={loop.pmv_ok_h/occ*100:.1f}% "
          f"vpp_scores={pref_scores} vpp_comply={vpp_comply_rate*100:.0f}%")
    print(f"  [LLM stats   ] calls={loop.llm_calls} fail={loop.llm_failures} "
          f"latency={loop.llm_latency_s:.1f}s "
          f"tokens={loop.llm_tokens_prompt}p/{loop.llm_tokens_comp}c")
    print(f"  [Appl rules  ] vpp_avoid={appl_vpp_avoid_rate*100:.0f}% "
          f"ev_target={ev_target_rate*100:.0f}% ewh_preheat={ewh_preheat_rate*100:.0f}%")
    return BenchmarkResult(scenario=f"family/{weather_label}", building="family",
        weather=weather_label, method="agent", exit_code=ec,
        energy_kwh_total=kwh, energy_kwh_per_day=kwh/3,
        pmv_ok_fraction=loop.pmv_ok_h/occ, mean_pmv=loop.pmv_s/occ,
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
        ev_target_reached_rate=round(ev_target_rate, 3),
        ewh_preheat_used_rate=round(ewh_preheat_rate, 3),
        appliance_results=appl_results_dict,
        control_decisions=loop.decisions[-50:], output_dir=str(output_dir))

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
