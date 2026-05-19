"""Family home benchmark runner (PMV or Agent mode) — 3x VPP-1 events per 3-day sim."""
from __future__ import annotations
import sys, json, shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

EPLUS_ROOT = Path("/home/ha_agent/EnergyPlus-24-1-0")
PROJECT_ROOT = Path("/home/ha_agent/work/EnergyBridge")
BENCHMARK_DIR = Path(__file__).resolve().parent
for p in (str(EPLUS_ROOT), str(PROJECT_ROOT)):
    if p not in sys.path: sys.path.insert(0, p)

_BENCH_DIR = Path(__file__).parent
_EXPERIMENTS_DIR = _BENCH_DIR.parent
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
    unmet_cooling_h: float = 0.0; vpp_energy_reduction_kwh: float = 0.0
    agent_setpoint_c: Optional[float] = None
    user_pref_score: Optional[float] = None       # legacy single score (PMV)
    user_pref_scores: List[float] = field(default_factory=list)  # per-VPP-event scores [e1,e2,e3]
    vpp_compliance_rate: float = 0.0  # fraction of VPP events where setpoint raised >=0.5C above normal
    user_pref_comment: str = ""
    control_decisions: List[Tuple[float, float, float]] = field(default_factory=list)
    output_dir: str = ""; error: str = ""
    def as_dict(self):
        return {k: v for k, v in self.__dict__.items() if not k.startswith("_") and k != "control_decisions"}

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
        self.e_wh = self.occ_h = self.pmv_ok_h = self.pmv_s = self.temp_s = self.unmet_h = 0.0
        self.e_vpp_wh: float = 0.0  # energy consumed during VPP windows only
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

    def init(self, ex, s):
        if self.ready: return True
        if not ex.api_data_fully_ready(s): return False
        self.h_cool = ex.get_actuator_handle(s, "Schedule:Compact", "Schedule Value", "cooling_sch")
        self.h_heat = ex.get_actuator_handle(s, "Schedule:Compact", "Schedule Value", "heating_sch")
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
        if any(ev["trigger_h"] <= sim_h < ev["end_h"] for ev in VPP_EVENTS):
            loop.e_vpp_wh += fac * dt
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
    _vpp_total_h = float(len(VPP_EVENTS))  # 3h total VPP window
    _e_vpp_kwh = loop.e_vpp_wh / 1000
    _e_non_vpp_kwh = kwh - _e_vpp_kwh
    _non_vpp_rate = _e_non_vpp_kwh / max(1, 72.0 - _vpp_total_h)  # avg kW outside VPP
    _vpp_reduction_kwh = _non_vpp_rate * _vpp_total_h - _e_vpp_kwh  # positive = saved

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

    _vpp_total_h = float(len(VPP_EVENTS))
    _e_vpp_kwh = loop.e_vpp_wh / 1000
    _non_vpp_rate = (kwh - _e_vpp_kwh) / max(1.0, 72.0 - _vpp_total_h)
    _vpp_reduction_kwh = round(_non_vpp_rate * _vpp_total_h - _e_vpp_kwh, 4)

    return BenchmarkResult(scenario=f"family/{weather_label}", building="family",
        weather=weather_label, method="pmv", exit_code=ec,
        vpp_compliance_rate=0.0,
        energy_kwh_total=kwh, energy_kwh_per_day=kwh/3,
        vpp_energy_reduction_kwh=_vpp_reduction_kwh,
        pmv_ok_fraction=loop.pmv_ok_h/occ, mean_pmv=loop.pmv_s/occ,
        mean_temp_c=loop.temp_s/occ, unmet_cooling_h=loop.unmet_h,
        user_pref_scores=pref_scores,
        user_pref_score=sum(pref_scores)/max(1,len(pref_scores)) if pref_scores else None,
        control_decisions=loop.decisions[-50:], output_dir=str(output_dir))

def run_family_pmv_rule(idf_path=DEFAULT_FAMILY_IDF, epw_path=DEFAULT_FAMILY_EPW,
                        output_dir=None, weather_label=""):
    """PMV+Rule: PMV controls setpoint every timestep for comfort.
    During VPP window, a simple rule forces setpoint up to >=26.0C (demand response).
    No LLM involved. Scored by roleplay after each VPP window ends.
    Provides a fair comparison point vs Agent+PMV to isolate LLM's contribution."""
    if output_dir is None:
        output_dir = BENCHMARK_DIR / "results" / f"family_pmv_rule_{weather_label}"
    output_dir = Path(output_dir)
    if output_dir.exists(): shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    from pyenergyplus.api import EnergyPlusAPI
    loop = _FamilyLoop(); api = EnergyPlusAPI(); state = api.state_manager.new_state()
    ex = api.exchange
    ex.request_variable(state, "Zone Mean Air Temperature", "living_unit1")
    ex.request_variable(state, "Facility Total Electricity Demand Rate", "Whole Building")

    _VPP_RULE_SP = 26.0  # rule: during VPP window, clamp setpoint to at least this

    def cb(s):
        if not loop.init(ex, s): return
        day = ex.day_of_year(s)
        if loop.start_day is None: loop.start_day = day
        hod = ex.current_time(s); dt = ex.zone_time_step(s)
        sim_h = (day - loop.start_day)*24 + hod
        wu = ex.warmup_flag(s)
        temp = ex.get_variable_value(s, loop.h_temp) if loop.h_temp != -1 else SP_DEFAULT
        fac  = ex.get_variable_value(s, loop.h_fac)  if loop.h_fac  != -1 else 0.0

        # Detect active VPP window
        active_vpp = None
        for ev in VPP_EVENTS:
            if ev["trigger_h"] <= sim_h < ev["end_h"]:
                active_vpp = ev; break

        # PMV step (every timestep for comfort)
        pmv = _compute_pmv(temp)
        if pmv > PMV_DEADBAND:   loop.sp = max(SP_MIN, loop.sp - SP_STEP)
        elif pmv < -PMV_DEADBAND: loop.sp = min(SP_MAX, loop.sp + SP_STEP)

        # RULE: during VPP window, override setpoint upward (demand reduction)
        if active_vpp is not None:
            loop.sp = max(loop.sp, _VPP_RULE_SP)

        if loop.h_cool != -1: ex.set_actuator_value(s, loop.h_cool, loop.sp)
        if loop.h_heat != -1: ex.set_actuator_value(s, loop.h_heat, HTG_SP)

        if wu: return
        loop.e_wh += fac * dt
        if active_vpp is not None: loop.e_vpp_wh += fac * dt

        # Collect per-VPP-window data
        if active_vpp:
            wd = loop.vpp_window_data.setdefault(active_vpp["id"],
                                                  {"temps": [], "pmvs": [], "sp": loop.sp})
            wd["temps"].append(temp)
            wd["pmvs"].append(abs(pmv) <= PMV_DEADBAND)
            wd["sp"] = loop.sp

        # Score VPP event after window ends
        psim = loop.prev_sim_h
        for ev in VPP_EVENTS:
            if psim < ev["end_h"] <= sim_h and ev["id"] not in loop.vpp_scored:
                ev_idx = next((i+1 for i,e in enumerate(VPP_EVENTS) if e["id"] == ev["id"]), 1)
                try:
                    from user_pref_scorer import score_user_preference
                    wd_e = loop.vpp_window_data.get(ev["id"], {})
                    wtemps = wd_e.get("temps", [])
                    wpmvs  = wd_e.get("pmvs", [])
                    sp_w   = wd_e.get("sp", SP_DEFAULT)
                    mean_t = sum(wtemps)/max(1, len(wtemps)) if wtemps else (loop.temp_s/max(loop.occ_h, 1))
                    pmv_ok = sum(wpmvs)/max(1, len(wpmvs)) if wpmvs else 0.5
                    e_day  = (loop.e_wh/1000) / max(1, sim_h/24)
                    r = score_user_preference(building="family", method="pmv_rule",
                            mean_temp_c=mean_t, pmv_ok_fraction=pmv_ok,
                            energy_kwh_per_day=e_day, agent_setpoint_c=sp_w,
                            event_index=ev_idx)
                    sc = r.get("score") or 0.0
                    print(f"  [FamilyPMVRule VPP score {ev['id']} idx={ev_idx}] {sc}/5")
                    loop.vpp_event_log.append({"id": ev["id"], "setpoint": sp_w,
                        "score": sc, "comment": r.get("comment", "")[:80]})
                except Exception as e2:
                    print(f"  [FamilyPMVRule VPP score] error: {e2}")
                    loop.vpp_event_log.append({"id": ev["id"], "setpoint": SP_DEFAULT,
                        "score": None, "comment": str(e2)[:60]})
                loop.vpp_scored.add(ev["id"])

        if _occupied(hod):
            loop.occ_h += dt; loop.pmv_s += pmv*dt; loop.temp_s += temp*dt
            if abs(pmv) <= PMV_DEADBAND: loop.pmv_ok_h += dt
            if temp > loop.sp + UNMET_TOL: loop.unmet_h += dt
            loop.decisions.append((round(sim_h, 2), round(loop.sp, 1), round(pmv, 3)))
        loop.prev_sim_h = sim_h

    api.runtime.callback_end_system_timestep_after_hvac_reporting(state, cb)
    ec = api.runtime.run_energyplus(state, ["-w", str(epw_path), "-d", str(output_dir), str(idf_path)])
    api.state_manager.delete_state(state)

    kwh = loop.e_wh/1000; occ = max(loop.occ_h, 1e-6)
    pref_scores = [e["score"] for e in loop.vpp_event_log if e.get("score") is not None]
    _VPP_COMPLY_SP = 26.0
    n_comply = sum(1 for e in loop.vpp_event_log if e.get("setpoint", 0) >= _VPP_COMPLY_SP)
    vpp_comply_rate = n_comply / max(1, len(VPP_EVENTS))
    print(f"  [family/pmv_rule] exit={ec} energy={kwh:.1f}kWh pmv_ok={loop.pmv_ok_h/occ*100:.1f}% "
          f"vpp_scores={pref_scores} vpp_comply={vpp_comply_rate*100:.0f}%")

    _vpp_total_h = float(len(VPP_EVENTS))
    _e_vpp_kwh = loop.e_vpp_wh / 1000
    _non_vpp_rate = (kwh - _e_vpp_kwh) / max(1.0, 72.0 - _vpp_total_h)
    _vpp_reduction_kwh = round(_non_vpp_rate * _vpp_total_h - _e_vpp_kwh, 4)

    return BenchmarkResult(scenario=f"family/{weather_label}", building="family",
        weather=weather_label, method="pmv_rule", exit_code=ec,
        vpp_compliance_rate=vpp_comply_rate,
        energy_kwh_total=kwh, energy_kwh_per_day=kwh/3,
        vpp_energy_reduction_kwh=_vpp_reduction_kwh,
        pmv_ok_fraction=loop.pmv_ok_h/occ, mean_pmv=loop.pmv_s/occ,
        mean_temp_c=loop.temp_s/occ, unmet_cooling_h=loop.unmet_h,
        user_pref_scores=pref_scores,
        user_pref_score=sum(pref_scores)/max(1, len(pref_scores)) if pref_scores else None,
        control_decisions=loop.decisions[-50:], output_dir=str(output_dir))


def run_family_agent(idf_path=DEFAULT_FAMILY_IDF, epw_path=DEFAULT_FAMILY_EPW,
                     output_dir=None, weather_label="",
                     user_pref="我希望室内舒适，但也愿意在不影响舒适的前提下节约电力。"):
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

    _LLM_SYS_FAM = """You are an autonomous HVAC agent for a family home (3-day July simulation).
Called at: (1) start of occupied period each day, (2) VPP demand-response events, (3) times YOU schedule.
Set a cooling setpoint and optionally schedule your next check.

Occupied: 08:00-22:00 daily. Unoccupied auto-set 28C. Sim total: 72h (3 days).
COMFORT: PMV in [-0.5,+0.5]. PMV~0 at 25.5C. PMV>+0.5 when zone>27C.
  -> Keep setpoint <= 26.0C during occupied hours for comfort.
VPP DEMAND RESPONSE: When VPP_ACTIVE, you MUST raise setpoint to >= 26.0C.
  HARD RULE: setpoint < 26.0C during VPP = non-compliant. Aim for 26.0-27.0C.
  But comfort still matters - user scores your VPP response after each event.
  Past VPP responses (if any) show user feedback - LEARN from it.

Return JSON ONLY: {"setpoint": X, "next_check_hour": Y_or_null, "reason": "..."}
  setpoint range: 22.0-28.0C
  next_check_hour: sim-hour for next call, or null (wait for next fixed trigger)"""

    def _llm_trigger(temp, out_t, hod, sim_h, remaining_h, vpp_active=False, vpp_id="",
                     user_pref_input=""):
        import json as _j
        hh = int(hod % 24)
        vpp_tag = f"  *** VPP_ACTIVE (event {vpp_id}): reduce load! user will score your response ***" if vpp_active else ""
        mem_tag = loop.vpp_mem_ctx  # contains past event scores + user feedback
        # Current event: user expressed preference before agent acts
        user_now_tag = f"\n[User says NOW]: {user_pref_input}" if user_pref_input else ""
        prompt = (f"sim_hour={sim_h:.1f}  clock={hh:02d}:00{vpp_tag}\n"
                  f"zone_temp={temp:.1f}C  outdoor={out_t:.1f}C\n"
                  f"remaining_sim_hours={remaining_h:.0f}\n"
                  f"user_pref: {user_pref}{user_now_tag}{mem_tag}")
        if vpp_active:
            fb_sp, fb_nch = 26.5, None
        else:
            fb_sp = min(26.0, max(SP_MIN, round(temp - 0.5, 1)))
            fb_nch = None
        fallback = {"setpoint": fb_sp, "next_check_hour": fb_nch}
        try:
            from energybridge.llm.client import LLMClient
            resp = LLMClient().chat(_LLM_SYS_FAM, prompt).strip()
            if resp.startswith("```"):
                resp = "\n".join(l for l in resp.splitlines() if not l.strip().startswith("```")).strip()
            data = _j.loads(resp)
            sp = round(max(SP_MIN, min(28.0, float(data.get("setpoint", fb_sp)))), 1)
            nch = data.get("next_check_hour")
            if nch is not None:
                nch = float(nch)
                if nch <= sim_h + 0.25 or nch > 72.0:
                    nch = None
            reason = str(data.get("reason", ""))[:100]
            print(f"  [FamilyAgent h={sim_h:.1f} vpp={vpp_active}] sp={sp} next={nch} | {reason}")
            return {"setpoint": sp, "next_check_hour": nch, "reason": reason}
        except Exception as e:
            print(f"  [FamilyAgent] LLM error at h={sim_h:.1f}: {e}")
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
            print(f"  [VPP score {ev['id']} idx={event_index}] {sc}/5 ({lbl}) [{src}] | {cmt[:60]}")
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
                    # User in the loop: get roleplay user preference BEFORE agent acts
                    if is_vpp:
                        try:
                            from user_pref_scorer import get_user_preference_input
                            ev_idx = next((i+1 for i,ev in enumerate(VPP_EVENTS)
                                           if ev["id"]==vid), 1)
                            loop.vpp_user_input = get_user_preference_input(
                                "family", ev_idx,
                                {"vpp_id": vid, "hour": sim_h, "duration_h": 1.0},
                                loop.vpp_event_log)
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
                    loop.vpp_last_reason = res.get("reason", "")

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
        if active_vpp is not None: loop.e_vpp_wh += fac * dt
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
    print(f"  [family/agent] exit={ec} energy={kwh:.1f}kWh pmv_ok={loop.pmv_ok_h/occ*100:.1f}% "
          f"vpp_scores={pref_scores} vpp_comply={vpp_comply_rate*100:.0f}%")
    _vpp_total_h = float(len(VPP_EVENTS))
    _e_vpp_kwh = loop.e_vpp_wh / 1000
    _non_vpp_rate = (kwh - _e_vpp_kwh) / max(1.0, 72.0 - _vpp_total_h)
    _vpp_reduction_kwh = round(_non_vpp_rate * _vpp_total_h - _e_vpp_kwh, 4)

    return BenchmarkResult(scenario=f"family/{weather_label}", building="family",
        weather=weather_label, method="agent", exit_code=ec,
        energy_kwh_total=kwh, energy_kwh_per_day=kwh/3,
        vpp_energy_reduction_kwh=_vpp_reduction_kwh,
        pmv_ok_fraction=loop.pmv_ok_h/occ, mean_pmv=loop.pmv_s/occ,
        mean_temp_c=loop.temp_s/occ, unmet_cooling_h=loop.unmet_h,
        agent_setpoint_c=round(avg_sp, 1),
        user_pref_scores=pref_scores,
        user_pref_score=sum(pref_scores)/max(1,len(pref_scores)) if pref_scores else None,
        vpp_compliance_rate=vpp_comply_rate,
        control_decisions=loop.decisions[-50:], output_dir=str(output_dir))


def run_family_agent_pmv(idf_path=DEFAULT_FAMILY_IDF, epw_path=DEFAULT_FAMILY_EPW,
                         output_dir=None, weather_label="",
                         user_pref="\u6211\u5e0c\u671b\u5ba4\u5185\u8212\u9002\uff0c\u4f46\u4e5f\u613f\u610f\u5728\u4e0d\u5f71\u54cd\u8002\u9002\u7684\u524d\u63d0\u4e0b\u8282\u7ea6\u7535\u529b\u3002"):
    """Hybrid: PMV controls during normal occupied hours; Agent (LLM) takes over
    only during VPP demand-response events.  After VPP ends, PMV resumes."""
    if output_dir is None:
        output_dir = BENCHMARK_DIR / "results" / f"family_agent_pmv_{weather_label}"
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

    _SYS = """You are an HVAC agent for a family home during a VPP demand-response event.
PMV baseline controls the home at all other times.  Your ONLY job is the VPP window.
Reduce electricity load by raising the cooling setpoint during the 1-hour VPP window.
HARD RULE: you MUST set setpoint >= 26.0C (compliance threshold). Aim for 26.0-27.0C.
Balance: demand reduction vs user comfort.  User will score your VPP response.
Return JSON ONLY: {"setpoint": X, "reason": "..."}  setpoint range: 22.0-28.0C"""

    def _llm_vpp(temp, out_t, hod, sim_h, vpp_id, user_pref_input=""):
        import json as _j
        hh = int(hod % 24)
        mem_tag = loop.vpp_mem_ctx
        user_now_tag = f"\n[User says NOW]: {user_pref_input}" if user_pref_input else ""
        prompt = (f"sim_hour={sim_h:.1f}  clock={hh:02d}:00  *** VPP_ACTIVE ({vpp_id}): reduce load! ***\n"
                  f"zone_temp={temp:.1f}C  outdoor={out_t:.1f}C\n"
                  f"PMV comfort baseline setpoint: {SP_DEFAULT}C  VPP reduction target: >=26.0C\n"
                  f"user_pref: {user_pref}{user_now_tag}{mem_tag}")
        fallback = {"setpoint": 26.5, "reason": "fallback"}
        try:
            from energybridge.llm.client import LLMClient
            resp = LLMClient().chat(_SYS, prompt).strip()
            if resp.startswith("```"):
                resp = "\n".join(l for l in resp.splitlines() if not l.strip().startswith("```")).strip()
            data = _j.loads(resp)
            sp = round(max(SP_MIN, min(28.0, float(data.get("setpoint", 26.5)))), 1)
            reason = str(data.get("reason", ""))[:100]
            print(f"  [FamilyAgentPMV h={sim_h:.1f} vpp={vpp_id}] sp={sp} | {reason}")
            return {"setpoint": sp, "reason": reason}
        except Exception as e:
            print(f"  [FamilyAgentPMV] LLM error at h={sim_h:.1f}: {e}")
            return fallback

    def _score_event(ev, loop_ref, sim_h, event_index=1):
        try:
            from user_pref_scorer import score_user_preference
            wd = loop_ref.vpp_window_data.get(ev["id"], {})
            wtemps = wd.get("temps", []); wpmvs = wd.get("pmvs", [])
            sp_w = wd.get("sp", loop_ref.sp)
            occ_ref = max(loop_ref.occ_h, 1e-6)
            mean_t = sum(wtemps)/max(1, len(wtemps)) if wtemps else (loop_ref.temp_s/occ_ref)
            pmv_ok = sum(wpmvs)/max(1, len(wpmvs)) if wpmvs else 0.5
            r = score_user_preference(building="family", method="agent_pmv",
                mean_temp_c=mean_t, pmv_ok_fraction=pmv_ok,
                energy_kwh_per_day=loop_ref.e_wh/1000/3, agent_setpoint_c=sp_w,
                event_index=event_index,
                user_preference_text=loop_ref.vpp_user_input,
                agent_reason=loop_ref.vpp_last_reason)
            score = r.get("score") or 3.0; comment = r.get("comment","")
            print(f"  [AgentPMV score event={ev['id']}] score={score} comment={comment[:60]}")
            return {"id": ev["id"], "setpoint": sp_w, "score": score, "comment": comment,
                    "user_input": loop_ref.vpp_user_input, "source": "llm"}
        except Exception as e:
            print(f"  [AgentPMV score] error: {e}")
            return {"id": ev["id"], "setpoint": loop_ref.sp, "score": 3.0,
                    "comment": str(e)[:60], "user_input": "", "source": "error"}

    h_out_handle = [-1]

    def cb(s):
        if not loop.init(ex, s): return
        day = ex.day_of_year(s)
        if loop.start_day is None: loop.start_day = day
        hod = ex.current_time(s); dt = ex.zone_time_step(s)
        sim_h = (day - loop.start_day)*24 + hod
        wu = ex.warmup_flag(s)
        temp = ex.get_variable_value(s, loop.h_temp) if loop.h_temp != -1 else SP_DEFAULT
        fac  = ex.get_variable_value(s, loop.h_fac)  if loop.h_fac  != -1 else 0.0

        out_t = 30.0
        if h_out_handle[0] == -1:
            h_out_handle[0] = ex.get_variable_handle(s, "Site Outdoor Air Drybulb Temperature", "Environment")
        if h_out_handle[0] != -1:
            v = ex.get_variable_value(s, h_out_handle[0])
            if v is not None and not _math.isnan(v): out_t = v

        occ = _occupied(hod)
        active_vpp = None
        for ev in VPP_EVENTS:
            if ev["trigger_h"] <= sim_h < ev["end_h"]:
                active_vpp = ev; break

        if not wu:
            if not occ:
                loop.sp = 28.0
            elif active_vpp is not None:
                # VPP WINDOW: Agent (LLM) controls
                psim = loop.prev_sim_h
                triggered_vpp = None
                for ev in VPP_EVENTS:
                    if psim < ev["trigger_h"] <= sim_h:
                        triggered_vpp = ev; break
                if triggered_vpp:
                    vid = triggered_vpp["id"]
                    try:
                        from user_pref_scorer import get_user_preference_input
                        ev_idx = next((i+1 for i,e in enumerate(VPP_EVENTS) if e["id"]==vid), 1)
                        loop.vpp_user_input = get_user_preference_input(
                            "family", ev_idx,
                            {"vpp_id": vid, "hour": sim_h, "duration_h": 1.0},
                            loop.vpp_event_log)
                    except Exception as _e:
                        print(f"  [UserInput] {_e}"); loop.vpp_user_input = ""
                    res = _llm_vpp(temp, out_t, hod, sim_h, vid, loop.vpp_user_input)
                    loop.sp = res["setpoint"]
                    loop.vpp_last_reason = res.get("reason", "")
                # else: hold agent setpoint through VPP window
            else:
                # NORMAL OCCUPIED: PMV controls every timestep
                pmv_now = _compute_pmv(temp)
                if pmv_now > PMV_DEADBAND:   loop.sp = max(SP_MIN, loop.sp - SP_STEP)
                elif pmv_now < -PMV_DEADBAND: loop.sp = min(SP_MAX, loop.sp + SP_STEP)

            if active_vpp:
                wd = loop.vpp_window_data.setdefault(active_vpp["id"], {"temps":[],"pmvs":[],"sp":loop.sp})
                wd["temps"].append(temp)
                wd["pmvs"].append(abs(_compute_pmv(temp)) <= PMV_DEADBAND)
                wd["sp"] = loop.sp

            psim = loop.prev_sim_h
            for ev in VPP_EVENTS:
                if psim < ev["end_h"] <= sim_h and ev["id"] not in loop.vpp_scored:
                    ev_idx = next((i+1 for i,e in enumerate(VPP_EVENTS) if e["id"]==ev["id"]), 1)
                    result = _score_event(ev, loop, sim_h, event_index=ev_idx)
                    loop.vpp_event_log.append(result)
                    loop.vpp_scored.add(ev["id"])
                    mem_entries = [{"event": e["id"], "sp": e["setpoint"], "score": e["score"],
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
        if active_vpp is not None: loop.e_vpp_wh += fac * dt
        if occ:
            loop.occ_h += dt; loop.pmv_s += pmv*dt; loop.temp_s += temp*dt
            if abs(pmv) <= PMV_DEADBAND: loop.pmv_ok_h += dt
            if temp > loop.sp + UNMET_TOL: loop.unmet_h += dt
            loop.decisions.append((round(sim_h,2), round(loop.sp,1), round(pmv,3)))

    api.runtime.callback_end_system_timestep_after_hvac_reporting(state, cb)
    ec = api.runtime.run_energyplus(state, ["-w",str(epw_path),"-d",str(output_dir),str(idf_path)])
    api.state_manager.delete_state(state)

    kwh = loop.e_wh/1000; occ = max(loop.occ_h, 1e-6)
    avg_sp = sum(d[1] for d in loop.decisions)/max(1,len(loop.decisions)) if loop.decisions else SP_DEFAULT
    pref_scores = [e["score"] for e in loop.vpp_event_log if e.get("score") is not None]
    _VPP_COMPLY_SP = 26.0
    n_comply = sum(1 for e in loop.vpp_event_log if e.get("setpoint", 0) >= _VPP_COMPLY_SP)
    vpp_comply_rate = n_comply / max(1, len(VPP_EVENTS))
    print(f"  [family/agent_pmv] exit={ec} energy={kwh:.1f}kWh pmv_ok={loop.pmv_ok_h/occ*100:.1f}% "
          f"vpp_scores={pref_scores} vpp_comply={vpp_comply_rate*100:.0f}%")
    _vpp_total_h = float(len(VPP_EVENTS))
    _e_vpp_kwh = loop.e_vpp_wh / 1000
    _non_vpp_rate = (kwh - _e_vpp_kwh) / max(1.0, 72.0 - _vpp_total_h)
    _vpp_reduction_kwh = round(_non_vpp_rate * _vpp_total_h - _e_vpp_kwh, 4)

    return BenchmarkResult(scenario=f"family/{weather_label}", building="family",
        weather=weather_label, method="agent_pmv", exit_code=ec,
        energy_kwh_total=kwh, energy_kwh_per_day=kwh/3,
        vpp_energy_reduction_kwh=_vpp_reduction_kwh,
        pmv_ok_fraction=loop.pmv_ok_h/occ, mean_pmv=loop.pmv_s/occ,
        mean_temp_c=loop.temp_s/occ, unmet_cooling_h=loop.unmet_h,
        agent_setpoint_c=round(avg_sp,1),
        user_pref_scores=pref_scores,
        user_pref_score=sum(pref_scores)/max(1,len(pref_scores)) if pref_scores else None,
        vpp_compliance_rate=vpp_comply_rate,
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
