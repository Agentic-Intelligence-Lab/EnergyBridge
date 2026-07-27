"""Medium office 15-zone benchmark runner (PMV or LLM Agent mode) — 3x VPP-1 per 3-day sim."""
from __future__ import annotations
import json, math, os, shutil, sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass
EPLUS_ROOT = Path(os.getenv("EPLUS_ROOT", "/opt/EnergyPlus-24-1-0"))
BENCHMARK_DIR = Path(__file__).resolve().parent
for p in (str(EPLUS_ROOT), str(PROJECT_ROOT)):
    if p not in sys.path: sys.path.insert(0, p)

from family_runner import BenchmarkResult

_BENCH_DIR = Path(__file__).parent
_EXPERIMENTS_DIR = _BENCH_DIR.parent
DEFAULT_OFFICE_IDF = _EXPERIMENTS_DIR / "models" / "medium_office" / "medium_office_3day.idf"
DEFAULT_OFFICE_EPW = _EXPERIMENTS_DIR / "weather" / "epw" / "CHN_TJ_Tianjin.545270_CSWD.epw"

ZONES = [
    "Core_bottom","Core_mid","Core_top",
    "Perimeter_top_ZN_3","Perimeter_top_ZN_2","Perimeter_top_ZN_1","Perimeter_top_ZN_4",
    "Perimeter_bot_ZN_3","Perimeter_bot_ZN_2","Perimeter_bot_ZN_1","Perimeter_bot_ZN_4",
    "Perimeter_mid_ZN_3","Perimeter_mid_ZN_2","Perimeter_mid_ZN_1","Perimeter_mid_ZN_4",
]
ZONE_GROUP = {}
for z in ZONES:
    if z.startswith("Core_"): ZONE_GROUP[z]="Core"
    elif "_bot_" in z: ZONE_GROUP[z]="Bottom"
    elif "_mid_" in z: ZONE_GROUP[z]="Middle"
    elif "_top_" in z: ZONE_GROUP[z]="Top"
    else: ZONE_GROUP[z]="Core"

OCC_START=8.0; OCC_END=18.0; HTG_SP=16.0; UNOCCUPIED=28.0
SP_DEF=26.0; SP_MIN=22.0; SP_MAX=28.0; SP_STEP=0.5
PMV_DB=0.5; PMV_MET=1.2; PMV_CLO=0.5; PMV_V=0.1; PMV_RH=55.0; UNMET_TOL=0.556

# 3x VPP-1 for office: 17:00 each day (within occupied 8-18), 1-hour window
VPP_EVENTS = [
    {"id": "vpp1", "trigger_h": 17.0, "end_h": 18.0, "day": 1},
    {"id": "vpp2", "trigger_h": 41.0, "end_h": 42.0, "day": 2},
    {"id": "vpp3", "trigger_h": 65.0, "end_h": 66.0, "day": 3},
]

def _pmv(tdb, rh=PMV_RH):
    try:
        from pythermalcomfort.models import pmv_ppd_iso
        r = pmv_ppd_iso(tdb=tdb, tr=tdb, vr=PMV_V, rh=rh, met=PMV_MET, clo=PMV_CLO, limit_inputs=False)
        return float(r.pmv)
    except:
        t_n = 33.5 - 3.5*PMV_MET - 3.0*PMV_CLO
        return round(0.5*(tdb-t_n)+(rh-50)*0.007, 3)

def _occ(h): return OCC_START <= (h%24) < OCC_END
def _sch(zone, kind): return f"{zone.upper()}_{kind}_SP_CONTROL"

_LLM_SYS = """You are an HVAC optimization AI for a 15-zone office building (3-day simulation).
You are called at start of occupied period and VPP events (17:00 each day).

COMFORT: PMV in [-0.5,+0.5]. clo=0.5, met=1.2. PMV~0 at 25C. PMV>+0.5 when zone>26.5C.
  -> During normal hours: keep setpoints <= 26.0C for comfort.
VPP DEMAND RESPONSE: When VPP_ACTIVE, you MUST raise ALL zone setpoints to >= 26.5C.
  HARD RULE: setpoint < 26.5C during VPP = non-compliant. Aim for 26.5-27.0C.
  User scores your VPP response after each event. Past scores are in memory - LEARN.

Return JSON ONLY: {"Core": X, "Bottom": X, "Middle": X, "Top": X, "next_check_hour": Y_or_null}
  Valid range: 22.0-27.0C. next_check_hour: sim-hour or null."""

def _llm_advise(zone_temps, outdoor_temp, rh, hod, sim_h, user_pref, sim_days=3,
                vpp_active=False, vpp_id="", vpp_mem="", user_pref_input="", pmv_ref_sps=None):
    groups = {"Core":[],"Bottom":[],"Middle":[],"Top":[]}
    for z,t in zone_temps.items():
        groups[ZONE_GROUP.get(z,"Core")].append(t)
    gavg = {g: round(sum(v)/len(v),1) for g,v in groups.items() if v}
    # Fixed fallback: VPP->26.5C (demand response), normal->SP_DEF 26.0C. Never cumulative.
    _fb_val = 26.5 if vpp_active else SP_DEF
    fallback = {g: _fb_val for g in gavg}
    vpp_tag = f"  *** VPP_ACTIVE ({vpp_id}): reduce load! user will score response ***" if vpp_active else ""
    user_now_tag = f"\n[User says NOW]: {user_pref_input}" if user_pref_input else ""
    pmv_ref_tag = (f"\n[PMV without VPP recommends {pmv_ref_sps} (group setpoints for comfort)]"
                   if vpp_active and pmv_ref_sps else "")
    prompt = (f"sim_hour={sim_h:.1f}  clock={int(hod%24):02d}:00{vpp_tag}\n"
              f"Outdoor: {outdoor_temp:.1f}C, {rh:.0f}%RH\n"
              f"Zone groups (C): {json.dumps(gavg)}\nUser preference: {user_pref}"
              f"{user_now_tag}{pmv_ref_tag}{vpp_mem}")
    try:
        from energybridge.llm.client import LLMClient
        resp = LLMClient().chat(_LLM_SYS, prompt).strip()
        if resp.startswith("```"):
            resp = "\n".join(l for l in resp.splitlines() if not l.strip().startswith("```")).strip()
        raw = json.loads(resp)
        sps = {g: round(max(SP_MIN,min(SP_MAX,float(raw.get(g,fallback.get(g,SP_DEF))))),1)
               for g in ("Core","Bottom","Middle","Top")}
        nch = raw.get("next_check_hour")
        if nch is not None:
            nch = float(nch)
            if nch <= sim_h + 0.25 or nch > sim_days * 24:
                nch = None
        reason = str(raw.get("reason", raw.get("note", "")))[:100]
        print(f"  [ZoneAdvisor h={sim_h:.1f} vpp={vpp_active}] {sps} next={nch} | {reason[:50]}")
        return {"setpoints": sps, "next_check_hour": nch, "reason": reason}
    except Exception as e:
        print(f"  [ZoneAdvisor] LLM failed: {e}; using fallback")
        return {"setpoints": fallback, "next_check_hour": None}

class _OfficeLoop:
    def __init__(self, mode):
        self.mode=mode; self.ready=False; self.start_day=None; self.step=0
        self.h_clg={}; self.h_htg={}; self.h_temp={}; self.h_fac=-1; self.h_out=-1
        self.sp={z:SP_DEF for z in ZONES}; self._pmv_ctrl=None
        self.e_wh=0.0; self.occ_h=0.0; self.pmv_ok_h=0.0; self.pmv_s=0.0; self.e_vpp_wh=0.0
        self.temp_s=0.0; self.unmet_h=0.0; self.decisions=[]; self.llm_n=0
        self.next_check: Optional[float] = OCC_START
        self.prev_sim_h: float = -1.0
        # VPP per-event tracking
        self.vpp_window_data: Dict[str, Any] = {}
        self.vpp_event_log: List[Dict] = []
        self.vpp_scored: set = set()
        self.vpp_mem_ctx: str = ""
        self.vpp_user_input: str = ""    # roleplay user preference before agent acts
        self.vpp_last_reason: str = ""   # agent reason from last LLM call

    def init_pmv(self):
        if self.mode not in ("pmv","agent_pmv","pmv_rule") or self._pmv_ctrl: return
        try:
            from energybridge.control.pmv_baseline_controller import PMVBaselineController, PMVControllerParams
            p = PMVControllerParams(pmv_upper=PMV_DB,pmv_lower=PMV_DB,step_c=SP_STEP,
                sp_min_c=SP_MIN,sp_max_c=SP_MAX,clothing=PMV_CLO,metabolic=PMV_MET,
                air_velocity=PMV_V,default_rh=PMV_RH,initial_setpoint_c=SP_DEF)
            self._pmv_ctrl = PMVBaselineController(ZONES, p)
        except Exception as e: print(f"  PMV ctrl init failed: {e}")

    def init_handles(self, ex, s):
        if self.ready: return True
        if not ex.api_data_fully_ready(s): return False
        for z in ZONES:
            self.h_clg[z]=ex.get_actuator_handle(s,"Schedule:Constant","Schedule Value",_sch(z,"CLG"))
            self.h_htg[z]=ex.get_actuator_handle(s,"Schedule:Constant","Schedule Value",_sch(z,"HTG"))
            self.h_temp[z]=ex.get_variable_handle(s,"Zone Mean Air Temperature",z)
            if self.h_temp[z]==-1: self.h_temp[z]=ex.get_variable_handle(s,"Zone Mean Air Temperature",z.upper())
        self.h_fac=ex.get_variable_handle(s,"Facility Total HVAC Electricity Demand Rate","Whole Building")
        self.h_out=ex.get_variable_handle(s,"Site Outdoor Air Drybulb Temperature","Environment")
        if self.h_out==-1: self.h_out=ex.get_variable_handle(s,"Zone Outdoor Air Drybulb Temperature",ZONES[0])
        self.ready=True; return True

def run_office(mode="pmv", idf_path=DEFAULT_OFFICE_IDF, epw_path=DEFAULT_OFFICE_EPW,
               output_dir=None, weather_label="",
               user_pref="Please save energy while keeping occupants comfortable."):
    if output_dir is None:
        output_dir = BENCHMARK_DIR/"results"/f"office_{mode}_{weather_label}"
    output_dir = Path(output_dir)
    if output_dir.exists(): shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    from pyenergyplus.api import EnergyPlusAPI
    loop = _OfficeLoop(mode); loop.init_pmv()
    api = EnergyPlusAPI(); state = api.state_manager.new_state(); ex = api.exchange

    for z in ZONES:
        ex.request_variable(state,"Zone Mean Air Temperature",z)
        ex.request_variable(state,"Zone Air System Sensible Cooling Rate",z)
    ex.request_variable(state,"Facility Total HVAC Electricity Demand Rate","Whole Building")
    ex.request_variable(state,"Site Outdoor Air Drybulb Temperature","Environment")

    # PMV per-VPP-window tracking
    vpp_window_temps: Dict[str, list] = {ev["id"]: [] for ev in VPP_EVENTS}
    vpp_window_pmvs:  Dict[str, list] = {ev["id"]: [] for ev in VPP_EVENTS}

    def _score_event(ev, loop_ref, sim_h, event_index=1):
        """Score office VPP event. Returns M1 3D scores + M2 zone_group comfort scores."""
        try:
            from user_pref_scorer import score_user_preference
            wd = loop_ref.vpp_window_data.get(ev["id"], {})
            wtemps = wd.get("temps", [])
            wpmvs  = wd.get("pmvs", [])
            sp_w   = wd.get("sp", SP_DEF)
            mean_t = sum(wtemps)/max(1,len(wtemps)) if wtemps else loop_ref.temp_s/max(loop_ref.occ_h,1)
            pmv_ok = sum(wpmvs)/max(1,len(wpmvs)) if wpmvs else 0.5
            e_day  = (loop_ref.e_wh/1000) / max(1, sim_h/24)
            # M2: compute per-zone-group mean temperature from vpp_window_data
            zgt = wd.get("zone_group_temps", {})  # {group: [temps]}
            zone_group_means = {g: (sum(ts)/len(ts) if ts else mean_t) for g, ts in zgt.items()} if zgt else None
            r = score_user_preference(
                building="office", method="agent",
                mean_temp_c=mean_t, pmv_ok_fraction=pmv_ok,
                energy_kwh_per_day=e_day, agent_setpoint_c=sp_w,
                event_index=event_index,
                user_preference_text=loop_ref.vpp_user_input,
                agent_reason=loop_ref.vpp_last_reason,
                zone_group_temps=zone_group_means)
            sc  = r.get("score", 3)
            lbl = r.get("label", "neutral")
            cmt = r.get("comment","")[:100]
            src = r.get("source","?")
            ev_id = ev["id"]
            print(f"  [Office VPP score {ev_id} idx={event_index}] overall={sc} "
                  f"comfort={r.get('comfort_score')} energy={r.get('energy_score')} "
                  f"vpp={r.get('vpp_score')} zone={r.get('zone_comfort_scores')} [{src}]")
            return {"id": ev["id"], "setpoint": sp_w, "score": sc, "label": lbl,
                    "comfort_score": r.get("comfort_score", 3),
                    "energy_score": r.get("energy_score", 3),
                    "vpp_score": r.get("vpp_score", 3),
                    "zone_comfort_scores": r.get("zone_comfort_scores"),
                    "comment": cmt, "user_input": loop_ref.vpp_user_input[:80], "source": src}
        except Exception as e:
            print(f"  [Office VPP score] error: {e}")
            return {"id": ev["id"], "setpoint": SP_DEF, "score": 3,
                    "comfort_score": 3, "energy_score": 3, "vpp_score": 3,
                    "zone_comfort_scores": None,
                    "label": "neutral", "comment": str(e)[:60], "user_input": "", "source": "error"}

    def cb(s):
        if not loop.init_handles(ex, s): return
        day=ex.day_of_year(s)
        if loop.start_day is None: loop.start_day=day
        hod=ex.current_time(s); dt=ex.system_time_step(s)
        sim_h=(day-loop.start_day)*24+hod; wu=ex.warmup_flag(s)
        occ=_occ(hod); loop.step+=1

        out_t=30.0
        if loop.h_out!=-1:
            v=ex.get_variable_value(s,loop.h_out)
            if v is not None and not math.isnan(v): out_t=v

        zone_t={z: (ex.get_variable_value(s,loop.h_temp[z]) if loop.h_temp.get(z,-1)!=-1 else SP_DEF) for z in ZONES}

        # Current VPP status
        active_vpp = None
        for ev in VPP_EVENTS:
            if ev["trigger_h"] <= sim_h < ev["end_h"]:
                active_vpp = ev; break

        if wu:
            for z in ZONES: loop.sp[z]=SP_DEF if occ else UNOCCUPIED
        elif mode=="pmv" and loop._pmv_ctrl:
            for z in ZONES:
                loop.sp[z]=loop._pmv_ctrl.step(z,zone_t[z],outdoor_rh=PMV_RH) if occ else UNOCCUPIED
            # PMV VPP window data collection
            if active_vpp:
                t_avg = sum(zone_t.values())/len(zone_t)
                vpp_window_temps[active_vpp["id"]].append(t_avg)
                vpp_window_pmvs[active_vpp["id"]].append(abs(_pmv(t_avg)) <= PMV_DB)
        elif mode=="pmv_rule" and loop._pmv_ctrl:
            # PMV+Rule: PMV for comfort, rule-based VPP response (no LLM)
            _VPP_RULE_SP = 26.5  # office VPP compliance threshold
            if occ:
                for z in ZONES:
                    loop.sp[z] = loop._pmv_ctrl.step(z, zone_t[z], outdoor_rh=PMV_RH)
                    if active_vpp is not None:
                        loop.sp[z] = max(loop.sp[z], _VPP_RULE_SP)  # rule: clamp up during VPP
            else:
                for z in ZONES: loop.sp[z] = UNOCCUPIED
            # Collect VPP window data
            if active_vpp and occ:
                t_avg = sum(zone_t.values())/len(zone_t)
                sp_avg = sum(loop.sp.values())/len(loop.sp)
                wd = loop.vpp_window_data.setdefault(active_vpp["id"],
                                                      {"temps":[],"pmvs":[],"sp":sp_avg,"zone_group_temps":{}})
                wd["temps"].append(t_avg)
                wd["pmvs"].append(abs(_pmv(t_avg)) <= PMV_DB)
                wd["sp"] = sp_avg
                # M2: accumulate per-zone-group temperatures
                for g in ("Core","Bottom","Middle","Top"):
                    g_temps = [zone_t[z] for z in ZONES if ZONE_GROUP.get(z)==g and z in zone_t]
                    if g_temps:
                        wd["zone_group_temps"].setdefault(g, []).append(sum(g_temps)/len(g_temps))
            # Score VPP event after window ends (outside occ check)
            psim_r = loop.prev_sim_h
            for ev in VPP_EVENTS:
                if psim_r < ev["end_h"] <= sim_h and ev["id"] not in loop.vpp_scored:
                    ev_idx = next((i+1 for i,e in enumerate(VPP_EVENTS) if e["id"]==ev["id"]), 1)
                    try:
                        from user_pref_scorer import score_user_preference
                        wd_e = loop.vpp_window_data.get(ev["id"], {})
                        wtemps = wd_e.get("temps", [])
                        wpmvs  = wd_e.get("pmvs", [])
                        sp_w   = wd_e.get("sp", SP_DEF)
                        mean_t = sum(wtemps)/max(1,len(wtemps)) if wtemps else loop.temp_s/max(loop.occ_h,1)
                        pmv_ok = sum(wpmvs)/max(1,len(wpmvs)) if wpmvs else 0.5
                        e_day  = (loop.e_wh/1000) / max(1, sim_h/24)
                        r = score_user_preference(building="office", method="pmv_rule",
                                mean_temp_c=mean_t, pmv_ok_fraction=pmv_ok,
                                energy_kwh_per_day=e_day, agent_setpoint_c=sp_w,
                                event_index=ev_idx)
                        sc = r.get("score") or 0.0
                        print(f"  [OfficePMVRule VPP score {ev['id']} idx={ev_idx}] {sc}/5")
                        loop.vpp_event_log.append({"id": ev["id"], "setpoint": sp_w,
                            "score": sc, "comment": r.get("comment","")[:80]})
                    except Exception as e2:
                        print(f"  [OfficePMVRule VPP score] error: {e2}")
                        loop.vpp_event_log.append({"id": ev["id"], "setpoint": SP_DEF,
                            "score": None, "comment": str(e2)[:60]})
                    loop.vpp_scored.add(ev["id"])
        elif mode=="agent":
            if occ:
                psim = loop.prev_sim_h
                # VPP-only: only intervene at VPP event start (max 6 LLM interventions per sim)
                triggered_vpp = None
                for ev in VPP_EVENTS:
                    if psim < ev["trigger_h"] <= sim_h:
                        triggered_vpp = ev; break
                if triggered_vpp is not None:
                    vid = triggered_vpp["id"]
                    try:
                        from user_pref_scorer import get_user_preference_input
                        ev_idx = next((i+1 for i,ev in enumerate(VPP_EVENTS)
                                       if ev["id"]==vid), 1)
                        loop.vpp_user_input = get_user_preference_input(
                            "office", ev_idx,
                            {"vpp_id": vid, "hour": sim_h, "duration_h": 1.0},
                            loop.vpp_event_log)
                    except Exception as _e:
                        print(f"  [UserInput] {_e}")
                        loop.vpp_user_input = ""
                    pmv_ref_sps = {g: round(sum(loop.sp[z] for z in ZONES if ZONE_GROUP[z]==g)
                                             / max(1, sum(1 for z in ZONES if ZONE_GROUP[z]==g)), 1)
                                    for g in set(ZONE_GROUP.values())}
                    result = _llm_advise(zone_t, out_t, PMV_RH, hod, sim_h, user_pref,
                                         vpp_active=True, vpp_id=vid, vpp_mem=loop.vpp_mem_ctx,
                                         user_pref_input=loop.vpp_user_input,
                                         pmv_ref_sps=pmv_ref_sps)
                    loop.llm_n += 1
                    gsps = result["setpoints"]
                    loop.vpp_last_reason = result.get("reason", "")
                    for z in ZONES: loop.sp[z] = gsps.get(ZONE_GROUP[z], SP_DEF)
                    loop.decisions.append({"sim_h": round(sim_h, 1), "hod": round(hod, 1),
                        "gsps": gsps, "out_t": round(out_t, 1), "vpp": True})
                elif active_vpp is None:
                    # Non-VPP occupied hours: maintain comfort default setpoint
                    for z in ZONES: loop.sp[z] = SP_DEF
                # Collect VPP window data
                if active_vpp:
                    t_avg = sum(zone_t.values())/len(zone_t)
                    sp_avg = sum(loop.sp.values())/len(loop.sp)
                    wd = loop.vpp_window_data.setdefault(active_vpp["id"], {"temps":[],"pmvs":[],"sp":sp_avg})
                    wd["temps"].append(t_avg)
                    wd["pmvs"].append(abs(_pmv(t_avg)) <= PMV_DB)
                    wd["sp"] = sp_avg
            else:
                for z in ZONES: loop.sp[z]=UNOCCUPIED

            # Score VPP event after window ends
            psim = loop.prev_sim_h
            for ev in VPP_EVENTS:
                if psim < ev["end_h"] <= sim_h and ev["id"] not in loop.vpp_scored:
                    ev_idx = next((i+1 for i,e in enumerate(VPP_EVENTS) if e["id"]==ev["id"]), 1)
                    result = _score_event(ev, loop, sim_h, event_index=ev_idx)
                    loop.vpp_event_log.append(result)
                    loop.vpp_scored.add(ev["id"])
                    mem_entries = [{"event": e["id"], "sp": e["setpoint"],
                                    "overall": e["score"],
                                    "comfort": e.get("comfort_score","?"),
                                    "energy": e.get("energy_score","?"),
                                    "vpp": e.get("vpp_score","?"),
                                    "zone": e.get("zone_comfort_scores"),
                                    "user_said": e.get("user_input","")[:60],
                                    "feedback": e["comment"][:80]}
                                   for e in loop.vpp_event_log]
                    avg_c = sum(e.get("comfort_score",3) for e in loop.vpp_event_log)/max(1,len(loop.vpp_event_log))
                    avg_e = sum(e.get("energy_score",3) for e in loop.vpp_event_log)/max(1,len(loop.vpp_event_log))
                    trend = "improve_comfort" if avg_c < 3.5 else ("save_energy" if avg_e > avg_c else "balanced")
                    loop.vpp_mem_ctx = (
                        f"\n[L3 cross-day] Past VPP: {mem_entries}"
                        f"\nLearned trend: {trend} (avg_comfort={avg_c:.1f}, avg_energy={avg_e:.1f})"
                        f"\nNext VPP: {'prioritize comfort' if trend=='improve_comfort' else 'increase demand reduction' if trend=='save_energy' else 'maintain balance'}"
                    )
                    # VPP END: recovery setpoint decision
                    try:
                        _rec_pref = (f"VPP {ev['id']} just ended. "
                                     f"Score={result.get('score', 3)}/5 "
                                     f"(comfort={result.get('comfort_score', 3)}/5). "
                                     "Set recovery setpoints for next occupied period.")
                        _rec = _llm_advise(zone_t, out_t, PMV_RH, hod, sim_h, user_pref,
                                           vpp_active=False, vpp_id="",
                                           vpp_mem=loop.vpp_mem_ctx,
                                           user_pref_input=_rec_pref)
                        _gsps_r = _rec["setpoints"]
                        for z in ZONES: loop.sp[z] = _gsps_r.get(ZONE_GROUP[z], SP_DEF)
                    except Exception:
                        pass

        elif mode=="agent_pmv":
            if occ:
                psim = loop.prev_sim_h
                if active_vpp is not None:
                    # VPP WINDOW: LLM decides at VPP start only
                    triggered_vpp = None
                    for ev in VPP_EVENTS:
                        if psim < ev["trigger_h"] <= sim_h:
                            triggered_vpp = ev; break
                    if triggered_vpp:
                        vid = triggered_vpp["id"]
                        try:
                            from user_pref_scorer import get_user_preference_input
                            ev_idx = next((i+1 for i,ev2 in enumerate(VPP_EVENTS)
                                           if ev2["id"]==vid), 1)
                            loop.vpp_user_input = get_user_preference_input(
                                "office", ev_idx,
                                {"vpp_id": vid, "hour": sim_h, "duration_h": 1.0},
                                loop.vpp_event_log)
                        except Exception as _e:
                            print(f"  [UserInput] {_e}"); loop.vpp_user_input = ""
                        gavg = {g: sum(zone_t[z] for z in ZONES if ZONE_GROUP[z]==g)
                                    / max(1, sum(1 for z in ZONES if ZONE_GROUP[z]==g))
                                for g in set(ZONE_GROUP.values())}
                        pmv_ref_sps2 = {g: round(sum(loop.sp[z] for z in ZONES if ZONE_GROUP[z]==g)
                                                 / max(1, sum(1 for z in ZONES if ZONE_GROUP[z]==g)), 1)
                                        for g in set(ZONE_GROUP.values())}
                        result = _llm_advise(zone_t, out_t, PMV_RH, hod, sim_h, user_pref,
                                             vpp_active=True, vpp_id=vid,
                                             vpp_mem=loop.vpp_mem_ctx,
                                             user_pref_input=loop.vpp_user_input,
                                             pmv_ref_sps=pmv_ref_sps2)
                        loop.llm_n += 1
                        gsps = result["setpoints"]
                        loop.next_check = None
                        loop.vpp_last_reason = result.get("reason","")
                        for z in ZONES: loop.sp[z] = gsps.get(ZONE_GROUP[z], SP_DEF)
                        loop.decisions.append({"sim_h": round(sim_h,1), "hod": round(hod,1),
                            "gsps": gsps, "out_t": round(out_t,1), "vpp": True})
                    # else: keep agent setpoint through VPP window
                else:
                    # NORMAL OCCUPIED: PMV controls every timestep
                    if loop._pmv_ctrl:
                        for z in ZONES:
                            loop.sp[z] = loop._pmv_ctrl.step(z, zone_t[z], outdoor_rh=PMV_RH)
                    else:
                        for z in ZONES:
                            pmv_z = _pmv(zone_t[z])
                            if pmv_z > PMV_DB:    loop.sp[z] = max(SP_MIN, loop.sp[z]-SP_STEP)
                            elif pmv_z < -PMV_DB: loop.sp[z] = min(SP_MAX, loop.sp[z]+SP_STEP)
                # Collect VPP window data
                if active_vpp:
                    t_avg = sum(zone_t.values())/len(zone_t)
                    sp_avg = sum(loop.sp.values())/len(loop.sp)
                    wd = loop.vpp_window_data.setdefault(active_vpp["id"],
                                                          {"temps":[],"pmvs":[],"sp":sp_avg})
                    wd["temps"].append(t_avg)
                    wd["pmvs"].append(abs(_pmv(t_avg)) <= PMV_DB)
                    wd["sp"] = sp_avg
            else:
                for z in ZONES: loop.sp[z] = UNOCCUPIED
            # Score VPP event after window ends (outside if occ: so end_h==OCC_END works)
            psim2 = loop.prev_sim_h
            for ev in VPP_EVENTS:
                if psim2 < ev["end_h"] <= sim_h and ev["id"] not in loop.vpp_scored:
                    ev_idx = next((i+1 for i,e in enumerate(VPP_EVENTS) if e["id"]==ev["id"]), 1)
                    result = _score_event(ev, loop, sim_h, event_index=ev_idx)
                    loop.vpp_event_log.append(result)
                    loop.vpp_scored.add(ev["id"])
                    mem_entries = [{"event": e["id"], "sp": e["setpoint"],
                                    "overall": e["score"],
                                    "comfort": e.get("comfort_score","?"),
                                    "energy": e.get("energy_score","?"),
                                    "vpp": e.get("vpp_score","?"),
                                    "zone": e.get("zone_comfort_scores"),
                                    "user_said": e.get("user_input","")[:60],
                                    "feedback": e["comment"][:80]}
                                   for e in loop.vpp_event_log]
                    avg_c = sum(e.get("comfort_score",3) for e in loop.vpp_event_log)/max(1,len(loop.vpp_event_log))
                    avg_e = sum(e.get("energy_score",3) for e in loop.vpp_event_log)/max(1,len(loop.vpp_event_log))
                    trend = "improve_comfort" if avg_c < 3.5 else ("save_energy" if avg_e > avg_c else "balanced")
                    loop.vpp_mem_ctx = (
                        f"\n[L3 cross-day] Past VPP: {mem_entries}"
                        f"\nLearned trend: {trend} (avg_comfort={avg_c:.1f}, avg_energy={avg_e:.1f})"
                        f"\nNext VPP: {'prioritize comfort' if trend=='improve_comfort' else 'increase demand reduction' if trend=='save_energy' else 'maintain balance'}"
                    )
                    # VPP END: PMV resumes naturally from VPP setpoints (no forced reset)
                    print(f"  [OfficeAgentPMV h={sim_h:.1f} vpp={ev['id']} END] PMV resumes from sp")

        for z in ZONES:
            hc=loop.h_clg.get(z,-1); hh_=loop.h_htg.get(z,-1)
            if hc!=-1: ex.set_actuator_value(s,hc,loop.sp[z])
            if hh_!=-1: ex.set_actuator_value(s,hh_,HTG_SP)

        loop.prev_sim_h = sim_h
        if wu: return
        fac=ex.get_variable_value(s,loop.h_fac) if loop.h_fac!=-1 else 0.0
        loop.e_wh+=fac*dt
        if active_vpp is not None: loop.e_vpp_wh+=fac*dt
        if occ:
            for z in ZONES:
                t=zone_t[z]; pmv=_pmv(t)
                loop.occ_h+=dt; loop.pmv_s+=pmv*dt; loop.temp_s+=t*dt
                if abs(pmv)<=PMV_DB: loop.pmv_ok_h+=dt
                if t>loop.sp[z]+UNMET_TOL: loop.unmet_h+=dt

    api.runtime.callback_end_system_timestep_after_hvac_reporting(state, cb)
    ec=api.runtime.run_energyplus(state,["-w",str(epw_path),"-d",str(output_dir),str(idf_path)])
    api.state_manager.delete_state(state)

    kwh=loop.e_wh/1000; occ=max(loop.occ_h,1e-6)
    pref_scores = []
    _off_comfort_scores = []; _off_energy_scores = []; _off_vpp_scores = []
    if mode in ("agent", "agent_pmv", "pmv_rule"):
        pref_scores = [e["score"] for e in loop.vpp_event_log if e.get("score") is not None]
    elif mode == "pmv":
        # Score PMV for each VPP window
        try:
            from user_pref_scorer import score_user_preference
            for idx, ev in enumerate(VPP_EVENTS):
                wtemps = vpp_window_temps.get(ev["id"], [])
                wpmvs  = vpp_window_pmvs.get(ev["id"], [])
                wt = sum(wtemps)/max(1,len(wtemps)) if wtemps else loop.temp_s/occ
                wp = sum(wpmvs)/max(1,len(wpmvs)) if wpmvs else loop.pmv_ok_h/occ
                r = score_user_preference(building="office", method="pmv",
                    mean_temp_c=wt, pmv_ok_fraction=wp, energy_kwh_per_day=kwh/3,
                    event_index=idx+1)
                pref_scores.append(r.get("score", 3))
                _off_comfort_scores.append(r.get("comfort_score", 3))
                _off_energy_scores.append(r.get("energy_score", 3))
                _off_vpp_scores.append(r.get("vpp_score", 3))
        except Exception as e:
            print(f"  [Office PMV score] {e}")

    # VPP compliance: office baseline 26.0C -> compliant if VPP setpoint >= 26.5C
    _VPP_COMPLY_SP = 26.5
    n_comply = sum(1 for e in loop.vpp_event_log if e.get("setpoint", 0) >= _VPP_COMPLY_SP)
    vpp_comply_rate = n_comply / max(1, len(VPP_EVENTS)) if mode in ("agent","agent_pmv","pmv_rule") else 0.0
    print(f"  [office/{mode}] exit={ec} llm_calls={loop.llm_n} "
          f"energy={kwh:.1f}kWh pmv_ok={loop.pmv_ok_h/occ*100:.1f}% vpp_scores={pref_scores} "
          f"vpp_comply={vpp_comply_rate*100:.0f}%")

    if loop.decisions:
        (output_dir/"decisions.json").write_text(
            json.dumps(loop.decisions[-100:], indent=2, ensure_ascii=False))

    _vpp_total_h = float(len(VPP_EVENTS))
    _e_vpp_kwh = loop.e_vpp_wh / 1000
    _non_vpp_rate = (kwh - _e_vpp_kwh) / max(1.0, 72.0 - _vpp_total_h)
    _vpp_reduction_kwh = round(_non_vpp_rate * _vpp_total_h - _e_vpp_kwh, 4)

    return BenchmarkResult(scenario=f"office/{weather_label}", building="office",
        weather=weather_label, method=mode, exit_code=ec,
        energy_kwh_total=kwh, energy_kwh_per_day=kwh/3,
        vpp_energy_reduction_kwh=_vpp_reduction_kwh,
        pmv_ok_fraction=loop.pmv_ok_h/occ, mean_pmv=loop.pmv_s/occ,
        mean_temp_c=loop.temp_s/occ, unmet_cooling_h=loop.unmet_h,
        user_pref_scores=pref_scores,
        user_pref_score=sum(pref_scores)/max(1,len(pref_scores)) if pref_scores else None,
        user_comfort_scores=([e.get("comfort_score",3) for e in loop.vpp_event_log] if loop.vpp_event_log else _off_comfort_scores),
        user_energy_scores=([e.get("energy_score",3) for e in loop.vpp_event_log] if loop.vpp_event_log else _off_energy_scores),
        user_vpp_scores=([e.get("vpp_score",3) for e in loop.vpp_event_log] if loop.vpp_event_log else _off_vpp_scores),
        vpp_compliance_rate=vpp_comply_rate,
        control_decisions=[(d["sim_h"],d.get("gsps",{}).get("Core",SP_DEF),0.0)
                           for d in loop.decisions[-10:]],
        output_dir=str(output_dir))

if __name__=="__main__":
    import argparse
    p=argparse.ArgumentParser()
    p.add_argument("--mode",choices=["pmv","agent"],default="pmv")
    p.add_argument("--epw",default=str(DEFAULT_OFFICE_EPW))
    p.add_argument("--city",default="tianjin")
    a=p.parse_args()
    r=run_office(mode=a.mode,epw_path=Path(a.epw),weather_label=a.city)
    print(json.dumps(r.as_dict(),indent=2,ensure_ascii=False))
