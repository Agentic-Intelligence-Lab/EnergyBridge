#!/usr/bin/env python3
"""14-day long-term memory & preference learning test for EnergyBridge.

Tests:
  - Cross-day memory accumulation (14 VPP events)
  - Preference adaptation (do scores improve over time?)
  - Memory compression (older events summarised, last 5 kept in full)
  - Comfort/energy/VPP learning curves

Usage:
  python3 run_longterm.py [--persona commuter] [--city Tianjin]

Output:
  results_longterm/family_14d_<persona>_<city>/
    trajectory.jsonl     - all events + scores
    learning_curve.csv   - per-day metrics
    summary.txt          - final analysis
"""
from __future__ import annotations
import argparse, sys, json, csv, shutil, math
from datetime import datetime
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

BENCH_DIR    = Path(__file__).resolve().parent
PROJECT_ROOT = BENCH_DIR.parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")
except Exception:
    pass
EPLUS_ROOT   = Path(os.getenv("EPLUS_ROOT", "/home/hku_user/EnergyPlus-24-1-0"))

for p in (str(EPLUS_ROOT), str(PROJECT_ROOT), str(BENCH_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

IDF_PATH  = BENCH_DIR.parent / "models" / "family_home" / "family_simple_14day.idf"
WEATHER_DIR = BENCH_DIR.parent / "weather" / "epw"

CITY_EPW = {
    "Beijing":  WEATHER_DIR / "CHN_BJ_Beijing.545110_CSWD.epw",
    "Shanghai": WEATHER_DIR / "CHN_SH_Shanghai.583670_CSWD.epw",
    "Tianjin":  WEATHER_DIR / "CHN_TJ_Tianjin.545270_CSWD.epw",
}

N_DAYS      = 14
TOTAL_HOURS = N_DAYS * 24          # 336h
VPP_EVENTS  = [
    {"id": f"vpp{d}", "trigger_h": float(d * 24 - 6), "end_h": float(d * 24 - 5), "day": d}
    for d in range(1, N_DAYS + 1)
]  # day 1→h=18, day 2→h=42, ..., day 14→h=330

OCCUPIED_START = 8.0;  OCCUPIED_END = 22.0
PMV_MET = 1.1; PMV_CLO = 0.5; PMV_V = 0.1; PMV_RH = 55.0
PMV_DEADBAND = 0.5
SP_MIN = 22.0; SP_MAX = 28.0; SP_STEP = 0.5
SP_DEFAULT = 26.0; HTG_SP = 20.0

def _occupied(h): return OCCUPIED_START <= (h % 24) < OCCUPIED_END

def _compute_pmv(tdb: float) -> float:
    try:
        from pythermalcomfort.models import pmv_ppd_iso
        r = pmv_ppd_iso(tdb=tdb, tr=tdb, vr=PMV_V, rh=PMV_RH, met=PMV_MET,
                        clo=PMV_CLO, limit_inputs=False)
        return float(r.pmv)
    except Exception:
        t_n = 33.5 - 3.5 * PMV_MET - 3.0 * PMV_CLO
        return round(0.5 * (tdb - t_n) + (PMV_RH - 50) * 0.007, 3)


class _Loop:
    def __init__(self):
        self.sp = SP_DEFAULT
        self.ready = False; self.start_day = None
        self.h_cool = self.h_heat = self.h_temp = self.h_fac = self.h_out = -1
        self.e_wh = self.occ_h = self.pmv_ok_h = self.pmv_s = self.temp_s = 0.0
        self.decisions: List = []
        self.prev_sim_h = -1.0; self.next_check: Optional[float] = 8.0
        # Memory
        self.event_log: List[Dict] = []       # full scored-event history
        self.scored: set = set()
        self.mem_ctx: str = ""               # compressed for LLM
        self.user_input_for_event: str = ""
        self.last_reason: str = ""
        self.vpp_window_data: Dict[str, Any] = {}  # id→{temps, pmvs, sp, reason}

    def init(self, ex, s) -> bool:
        if self.ready: return True
        if not ex.api_data_fully_ready(s): return False
        self.h_cool = ex.get_actuator_handle(s, "Schedule:Compact", "Schedule Value", "cooling_sch")
        self.h_heat = ex.get_actuator_handle(s, "Schedule:Compact", "Schedule Value", "heating_sch")
        self.h_temp = ex.get_variable_handle(s, "Zone Mean Air Temperature", "living_unit1")
        self.h_fac  = ex.get_variable_handle(s, "Facility Total Electricity Demand Rate", "Whole Building")
        self.h_out  = ex.get_variable_handle(s, "Site Outdoor Air Drybulb Temperature", "Environment")
        self.ready = True; return True


def _build_mem_ctx(event_log: List[Dict]) -> str:
    """Build compressed memory string for LLM. Keep last 5 in full; summarise rest."""
    if not event_log:
        return ""
    recent = event_log[-5:]
    full_recent = json.dumps(
        [{"day": e["day"], "sp": e["setpoint"],
          "overall": e["overall"], "comfort": e["comfort"],
          "energy": e["energy"], "vpp": e["vpp"],
          "user_said": e.get("user_input", "")[:60],
          "fb": e.get("comment", "")[:80]}
         for e in recent],
        ensure_ascii=False)
    n_all = len(event_log)
    avg_comfort = sum(e["comfort"] for e in event_log) / n_all
    avg_energy  = sum(e["energy"]  for e in event_log) / n_all
    avg_vpp     = sum(e["vpp"]     for e in event_log) / n_all
    avg_overall = sum(e["overall"] for e in event_log) / n_all
    early_avg = sum(e["overall"] for e in event_log[:min(3,n_all)]) / min(3,n_all)
    late_avg  = sum(e["overall"] for e in event_log[-min(3,n_all):]) / min(3,n_all)
    trend = "IMPROVING" if late_avg > early_avg + 0.3 else ("DECLINING" if late_avg < early_avg - 0.3 else "STABLE")
    if avg_comfort < 3.5:         guidance = "User comfort is below target — prioritise thermal comfort."
    elif avg_energy < 3.0:        guidance = "Energy efficiency is low — seek smarter demand reduction."
    elif avg_vpp < 3.0:           guidance = "VPP cooperation is poor — improve demand response compliance."
    else:                         guidance = "Scores are good — maintain current balanced strategy."
    summary = (f"[Events 1-{n_all}] avg_overall={avg_overall:.1f}/5 "
               f"comfort={avg_comfort:.1f} energy={avg_energy:.1f} vpp={avg_vpp:.1f} "
               f"trend={trend}")
    return (f"\n[L3 14-day memory — {n_all} events]\n"
            f"SUMMARY: {summary}\n"
            f"GUIDANCE: {guidance}\n"
            f"RECENT (last {len(recent)}): {full_recent}")


def run_14day_agent(
    persona_name: str = "commuter",
    city: str = "Tianjin",
    output_dir: Optional[Path] = None,
) -> Dict[str, Any]:
    """Run 14-day long-term agent test and return results dict."""
    from personas import get_persona, persona_user_pref_string
    from shiftable_load import make_washer_from_persona

    _persona  = get_persona(persona_name)
    user_pref = persona_user_pref_string(_persona)
    _washer   = make_washer_from_persona(_persona)

    if output_dir is None:
        output_dir = BENCH_DIR / "results_longterm" / f"family_{N_DAYS}d_{persona_name}_{city}"
    output_dir = Path(output_dir)
    if output_dir.exists(): shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    _diag_log = BENCH_DIR / "logs" / "dialogue" / f"longterm_{persona_name}_{city}.jsonl"
    _diag_log.parent.mkdir(parents=True, exist_ok=True)
    if _diag_log.exists(): _diag_log.unlink()

    epw = CITY_EPW[city]
    if not epw.exists():
        raise FileNotFoundError(f"EPW not found: {epw}")
    if not IDF_PATH.exists():
        raise FileNotFoundError(f"IDF not found: {IDF_PATH}")

    print(f"\n{'='*65}")
    print(f"{N_DAYS}-Day Long-Term Memory & Learning Test")
    print(f"  Persona: {persona_name}  |  City: {city}  |  Days: {N_DAYS}")
    print(f"  VPP events: {N_DAYS} (daily at 18:00)")
    print(f"  Output: {output_dir}")
    print(f"{'='*65}\n")

    _LLM_SYS = (
        f"You are an autonomous HVAC agent for a family home ({N_DAYS}-day simulation).\n"
        f"Called at: (1) occupied-period start each day, (2) VPP demand-response.\n"
        f"Occupied: 08:00-22:00. Total sim: {TOTAL_HOURS}h ({N_DAYS} days).\n"
        "COMFORT: Keep setpoint <= 26.0C for comfort. PMV target [-0.5,+0.5].\n"
        "VPP DEMAND RESPONSE: When VPP_ACTIVE, MUST raise setpoint >= 26.0C (HARD RULE).\n"
        "  IMPORTANT: You have 14-day memory. Learn user preferences across ALL days.\n"
        "  Adapt strategy based on past feedback — do not repeat the same mistakes.\n"
        f"\nUSER PROFILE:\n{_persona.get('persona_prompt', '')}\n"
        "WASHING MACHINE (shiftable 1.5kW, avoid running during VPP windows).\n"
        '  Include "start_washer": true in JSON to start it (IDLE and in time window only).\n'
        'Return JSON ONLY: {"setpoint": X, "next_check_hour": Y_or_null, '
        '"start_washer": true/false, "reason": "..."}\n'
        "  setpoint range: 22.0-28.0C\n"
        "CRITICAL: Output ONLY the raw JSON object. No markdown, no explanation, no reasoning text."
    )

    # ── API Key Pool ──────────────────────────────────────────────────────
    from api_pool import ApiKeyPool
    _DOTENV = BENCH_DIR.parent.parent / ".env"
    _KEY_FILE = BENCH_DIR / "api_keys.txt"
    _BASE_URL = "https://www.dmxapi.cn/v1"
    try:
        _agent_pool = ApiKeyPool.from_dotenv(
            _DOTENV, _BASE_URL, "claude-sonnet-4-6",
            key_file=_KEY_FILE if _KEY_FILE.exists() else None,
            min_gap_s=12.0,
        )
    except (FileNotFoundError, ValueError):
        # Fall back to single key from .env
        import os as _os
        _key = _os.environ.get("LLM_API_KEY", "")
        if not _key:
            for line in (_DOTENV.read_text() if _DOTENV.exists() else "").splitlines():
                if line.startswith("LLM_API_KEY="):
                    _key = line.split("=",1)[1].strip().strip('"').strip("'")
        _agent_pool = ApiKeyPool([_key], _BASE_URL, "claude-sonnet-4-6", min_gap_s=12.0)
    # ──────────────────────────────────────────────────────────────────────

    # -- MemGPT-style hierarchical memory (isolated per run) --
    from memory_manager import MemoryManager
    _run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    _memory = MemoryManager(persona_name, city, run_id=_run_id, pool=_agent_pool)

    loop = _Loop()
    washer_kwh_total = 0.0
    washer_completed_days: List[bool] = []
    washer_vpp_days: List[bool] = []

    from pyenergyplus.api import EnergyPlusAPI
    api = EnergyPlusAPI(); state = api.state_manager.new_state()
    ex = api.exchange
    ex.request_variable(state, "Zone Mean Air Temperature", "living_unit1")
    ex.request_variable(state, "Facility Total Electricity Demand Rate", "Whole Building")
    ex.request_variable(state, "Site Outdoor Air Drybulb Temperature", "Environment")

    def _llm_call(temp, out_t, hod, sim_h, vpp_active=False, vpp_id="", extra_ctx=""):
        hh = int(hod % 24)
        day_num = int(sim_h // 24) + 1
        vpp_tag = f"  *** VPP_ACTIVE (event {vpp_id} day {day_num}): reduce load! ***" if vpp_active else ""
        prompt = (
            f"sim_hour={sim_h:.1f}  day={day_num}/{N_DAYS}  clock={hh:02d}:00{vpp_tag}\n"
            f"zone_temp={temp:.1f}C  outdoor={out_t:.1f}C\n"
            f"remaining_sim_hours={TOTAL_HOURS - sim_h:.0f}\n"
            f"[Appliance] {_washer.prompt_line(sim_h)}\n"
            f"user_pref: {user_pref}"
            + (f"\n{extra_ctx}" if extra_ctx else "")
            + loop.mem_ctx
        )
        is_recovery = bool(extra_ctx and "ended" in extra_ctx)
        fb_sp = 26.5 if vpp_active else (24.0 if is_recovery else min(26.0, max(SP_MIN, round(temp - 0.5, 1))))
        fallback = {"setpoint": fb_sp, "next_check_hour": None, "reason": ""}
        try:
            resp = _agent_pool.chat(_LLM_SYS, prompt).strip()
            if resp.startswith("```"):
                resp = "\n".join(l for l in resp.splitlines() if not l.strip().startswith("```")).strip()
            if not resp:
                raise ValueError("LLM returned empty code block")
            # Fallback: model returned prose reasoning — find the embedded JSON object
            if not resp.lstrip().startswith("{"):
                a, b = resp.find("{"), resp.rfind("}")
                if a != -1 and b > a:
                    resp = resp[a:b+1]
                else:
                    raise ValueError(f"No JSON object in response: {resp[:60]!r}")
            try:
                data = json.loads(resp)
            except Exception as _je:
                print(f"  [Agent] Non-JSON resp h={sim_h:.1f}: {repr(resp[:120])}")
                raise _je
            sp  = round(max(SP_MIN, min(28.0, float(data.get("setpoint", fb_sp)))), 1)
            nch = data.get("next_check_hour")
            if nch is not None:
                nch = float(nch)
                if nch <= sim_h + 0.25 or nch > TOTAL_HOURS:
                    nch = None
            reason = str(data.get("reason", ""))[:120]
            sw = bool(data.get("start_washer", False))
            if sw and _washer.can_start(sim_h):
                if _washer.start(sim_h):
                    print(f"  [Washer day {day_num}] started at h={sim_h:.1f}")
            print(f"  [Agent h={sim_h:.1f} d{day_num} vpp={vpp_active}] sp={sp} | {reason[:80]}")
            return {"setpoint": sp, "next_check_hour": nch, "reason": reason}
        except Exception as exc:
            print(f"  [Agent] LLM error h={sim_h:.1f}: {exc}")
            return fallback

    def _score_event(ev, sim_h):
        try:
            from user_pref_scorer import score_user_preference
            wd    = loop.vpp_window_data.get(ev["id"], {})
            wt    = wd.get("temps", []); wp = wd.get("pmvs", [])
            mean_t = sum(wt) / max(1, len(wt)) if wt else loop.temp_s / max(loop.occ_h, 1)
            pmv_ok = sum(wp) / max(1, len(wp)) if wp else 0.5
            e_day  = (loop.e_wh / 1000) / max(1, sim_h / 24)
            ev_start = ev["trigger_h"]; ev_end = ev["end_h"]
            w_ok    = _washer.state in ("RUNNING", "DONE")
            w_vpp   = _washer.is_running_during(ev_start, ev_end)
            r = score_user_preference(
                building="family", method="agent",
                mean_temp_c=mean_t, pmv_ok_fraction=pmv_ok,
                energy_kwh_per_day=e_day, agent_setpoint_c=wd.get("sp", loop.sp),
                event_index=ev["day"],
                user_preference_text=loop.user_input_for_event,
                agent_reason=loop.last_reason,
                persona=_persona,
                washer_completed=w_ok, washer_during_vpp=w_vpp,
                log_path=_diag_log)
            src = r.get("source", "?")
            entry = {
                "day": ev["day"], "id": ev["id"],
                "overall": r.get("score", 3),
                "comfort": r.get("comfort_score", 3),
                "energy":  r.get("energy_score", 3),
                "vpp":     r.get("vpp_score", 3),
                "setpoint": wd.get("sp", loop.sp),
                "mean_temp": round(mean_t, 2),
                "pmv_ok_pct": round(pmv_ok * 100, 1),
                "comment": r.get("comment", "")[:100],
                "user_input": loop.user_input_for_event[:80],
                "washer_completed": w_ok,
                "washer_during_vpp": w_vpp,
                "source": src,
            }
            print(f"  [Score day {ev['day']}] overall={entry['overall']}/5 "
                  f"comfort={entry['comfort']} energy={entry['energy']} vpp={entry['vpp']} "
                  f"[{src}] | {entry['comment'][:55]}")
            # Roleplay: get user input for NEXT event
            try:
                from energybridge.roleplay.loader import load_persona as _lp
                from energybridge.roleplay.simulator import RoleplayUserSimulator
                _p_json = _lp(persona_name)
                _sim = RoleplayUserSimulator(_p_json)
                next_ev_idx = ev["day"] + 1
                _txt = _sim.get_user_input(
                    next_ev_idx,
                    {"vpp_id": f"vpp{next_ev_idx}", "trigger_h": ev["trigger_h"] + 24, "end_h": ev["end_h"] + 24},
                    loop.event_log[-3:],
                    log_path=_diag_log)
                loop.user_input_for_event = _txt
                print(f"  [User pre-event d{ev['day']+1}] {_txt[:90]}")
            except Exception as ue:
                loop.user_input_for_event = ""
            return entry
        except Exception as exc:
            print(f"  [Score {ev['id']}] error: {exc}")
            import traceback; traceback.print_exc()
            return {"day": ev["day"], "id": ev["id"], "overall": 3,
                    "comfort": 3, "energy": 3, "vpp": 3,
                    "setpoint": loop.sp, "mean_temp": 25.0, "pmv_ok_pct": 75.0,
                    "comment": str(exc)[:80], "user_input": "", "source": "error"}

    def cb(s):
        nonlocal washer_kwh_total
        try:
            _cb_inner(s)
        except Exception as _cb_exc:
            import traceback
            print(f"  [CB ERROR] {_cb_exc}")
            traceback.print_exc()

    def _cb_inner(s):
        nonlocal washer_kwh_total
        if not loop.init(ex, s): return
        day   = ex.day_of_year(s)
        if loop.start_day is None: loop.start_day = day
        hod   = ex.current_time(s); dt = ex.system_time_step(s)
        sim_h = (day - loop.start_day) * 24 + hod
        wu    = ex.warmup_flag(s)
        occ   = _occupied(hod)
        _tv   = ex.get_variable_value(s, loop.h_temp) if loop.h_temp != -1 else SP_DEFAULT
        temp  = SP_DEFAULT if (_tv is None or (isinstance(_tv, float) and math.isnan(_tv))) else _tv
        _fv   = ex.get_variable_value(s, loop.h_fac)  if loop.h_fac  != -1 else 0.0
        fac   = 0.0 if (_fv is None or (isinstance(_fv, float) and math.isnan(_fv))) else _fv
        out_t = 30.0
        if loop.h_out != -1:
            v = ex.get_variable_value(s, loop.h_out)
            if v is not None and not math.isnan(v): out_t = v

        active_vpp = next((ev for ev in VPP_EVENTS if ev["trigger_h"] <= sim_h < ev["end_h"]), None)

        if not wu:
            fac_kw = (fac or 0.0) / 1000.0
            if occ:
                pmv = _compute_pmv(temp)
                loop.occ_h    += dt; loop.pmv_s += pmv; loop.temp_s += temp * dt
                if abs(pmv) <= PMV_DEADBAND: loop.pmv_ok_h += dt
            loop.e_wh += fac_kw * dt * 1000

            # Track washer power
            if _washer.state == "RUNNING":
                washer_kwh_total += _washer.power_kw * dt / 1.0  # dt in hours
                _washer.tick(sim_h, dt)

            # VPP window data collection
            for ev in VPP_EVENTS:
                if ev["trigger_h"] <= sim_h < ev["end_h"]:
                    d = loop.vpp_window_data.setdefault(ev["id"], {"temps": [], "pmvs": [], "sp": loop.sp, "reason": ""})
                    d["temps"].append(temp)
                    pmv2 = _compute_pmv(temp)
                    d["pmvs"].append(abs(pmv2) <= PMV_DEADBAND)

            psim = loop.prev_sim_h

            if not occ:
                loop.sp = 28.0  # unoccupied: save energy

            else:
                # ── VPP start trigger ─────────────────────────────────────────
                for ev in VPP_EVENTS:
                    if psim < ev["trigger_h"] <= sim_h and ev["id"] not in loop.scored:
                        loop.vpp_window_data.setdefault(ev["id"], {"temps": [], "pmvs": [], "sp": loop.sp, "reason": ""})
                        # Get user pre-event preference (first event: use static)
                        if not loop.event_log:
                            loop.user_input_for_event = user_pref
                        r = _llm_call(temp, out_t, hod, sim_h,
                                      vpp_active=True, vpp_id=ev["id"],
                                      extra_ctx=f"[User NOW]: {loop.user_input_for_event}" if loop.user_input_for_event else "")
                        loop.sp = r["setpoint"]
                        loop.last_reason = r["reason"]
                        loop.vpp_window_data[ev["id"]]["sp"] = loop.sp
                        loop.vpp_window_data[ev["id"]]["reason"] = loop.last_reason
                        if r.get("next_check_hour"):
                            loop.next_check = r["next_check_hour"]

                # ── VPP end trigger ───────────────────────────────────────────
                for ev in VPP_EVENTS:
                    if psim < ev["end_h"] <= sim_h and ev["id"] not in loop.scored:
                        result = _score_event(ev, sim_h)
                        loop.event_log.append(result)
                        loop.scored.add(ev["id"])
                        # MemGPT-style: reflection + structured belief update
                        _memory.update(result)
                        loop.mem_ctx = _memory.build_prompt_ctx()
                        # Recovery setpoint
                        rec = _llm_call(temp, out_t, hod, sim_h,
                                        extra_ctx=f"VPP {ev['id']} just ended. Score {result['overall']}/5 "
                                                   f"(comfort={result['comfort']}/5). Set recovery setpoint.")
                        loop.sp = rec["setpoint"]
                        # After recovery: schedule next morning check; suppress further checks today
                        next_morning = (int(sim_h // 24) + 1) * 24 + OCCUPIED_START
                        loop.next_check = next_morning if next_morning < TOTAL_HOURS else None

                        # Day boundary: record washer status BEFORE reset
                        washer_completed_days.append(_washer.state in ("RUNNING", "DONE"))
                        washer_vpp_days.append(_washer.is_running_during(ev["trigger_h"], ev["end_h"]))
                        _washer.reset_for_day()

                # ── Scheduled occupied-period check ──────────────────────────
                if loop.next_check is not None and psim < loop.next_check <= sim_h and active_vpp is None:
                    r = _llm_call(temp, out_t, hod, sim_h)
                    loop.sp = r["setpoint"]
                    loop.next_check = r.get("next_check_hour")
                    if loop.next_check is None:
                        # Schedule next morning check
                        next_day_start = (int(sim_h // 24) + 1) * 24 + OCCUPIED_START
                        loop.next_check = next_day_start if next_day_start < TOTAL_HOURS else None

        loop.prev_sim_h = sim_h
        if loop.h_cool != -1: ex.set_actuator_value(s, loop.h_cool, loop.sp)
        if loop.h_heat != -1: ex.set_actuator_value(s, loop.h_heat, HTG_SP)

    api.runtime.callback_begin_zone_timestep_after_init_heat_balance(state, cb)
    ret = api.runtime.run_energyplus(state, [
        "-w", str(epw), "-d", str(output_dir), "-r", str(IDF_PATH),
    ])

    # ── Post-run analysis ─────────────────────────────────────────────────────
    n = max(len(loop.event_log), 1)
    kwh_total = loop.e_wh / 1000
    kwh_per_day = kwh_total / N_DAYS
    pmv_ok_frac = loop.pmv_ok_h / max(loop.occ_h, 1)
    mean_temp = loop.temp_s / max(loop.occ_h, 1)

    # Learning curve analysis
    first3_avg = sum(e["overall"] for e in loop.event_log[:3]) / min(3, n) if loop.event_log else 0
    last3_avg  = sum(e["overall"] for e in loop.event_log[-3:]) / min(3, n) if loop.event_log else 0
    improvement = last3_avg - first3_avg

    # Save trajectory JSONL
    traj_path = output_dir / "trajectory.jsonl"
    with open(traj_path, "w", encoding="utf-8") as f:
        for e in loop.event_log:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")

    # Save learning curve CSV
    csv_path = output_dir / "learning_curve.csv"
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["day","overall","comfort","energy","vpp","setpoint","mean_temp","pmv_ok_pct","comment"], extrasaction='ignore')
        w.writeheader()
        w.writerows(loop.event_log)

    # Save summary
    summary_lines = [
        f"{N_DAYS}-Day Long-Term Test: {persona_name} / {city}",
        "=" * 55,
        f"Total energy:      {kwh_total:.1f} kWh  ({kwh_per_day:.1f} kWh/day)",
        f"PMV comfort:       {pmv_ok_frac*100:.1f}% in-band",
        f"Mean temp:         {mean_temp:.1f} C",
        f"Events scored:     {n}/{N_DAYS}",
        "",
        "LEARNING CURVE:",
        f"  Days 1-3 avg score:   {first3_avg:.2f}/5",
        f"  Days 12-14 avg score: {last3_avg:.2f}/5",
        f"  Improvement:          {improvement:+.2f}  ({'IMPROVING' if improvement>0.3 else 'STABLE' if abs(improvement)<=0.3 else 'DECLINING'})",
        "",
        "PER-DAY SCORES:",
    ]
    for e in loop.event_log:
        bar = "█" * e["overall"] + "░" * (5 - e["overall"])
        summary_lines.append(f"  Day {e['day']:>2}: {bar} {e['overall']}/5  "
                              f"(c={e['comfort']} e={e['energy']} v={e['vpp']})  {e['comment'][:45]}")
    summary_path = output_dir / "summary.txt"
    summary_path.write_text("\n".join(summary_lines), encoding="utf-8")

    print(f"\n{'='*65}")
    print(f"  14-Day Test Complete: {persona_name} / {city}")
    print(f"{'='*65}")
    print(f"  Energy:        {kwh_per_day:.1f} kWh/day total  ({kwh_total:.1f} kWh)")
    print(f"  PMV comfort:   {pmv_ok_frac*100:.1f}% in-band")
    print(f"  Mean temp:     {mean_temp:.1f} C")
    print(f"  Events scored: {n}/14")
    print(f"\n  LEARNING CURVE:")
    print(f"    Days 1-3  avg: {first3_avg:.2f}/5")
    print(f"    Days 12-14avg: {last3_avg:.2f}/5")
    print(f"    Trend:         {'+' if improvement > 0 else ''}{improvement:.2f}  "
          f"({'IMPROVING' if improvement>0.3 else 'STABLE' if abs(improvement)<=0.3 else 'DECLINING'})")
    print(f"\n  PER-DAY SCORES (overall/5):")
    for e in loop.event_log:
        bar = "█" * e["overall"] + "░" * (5 - e["overall"])
        print(f"    Day {e['day']:>2}: {bar} {e['overall']}/5  "
              f"c={e['comfort']} e={e['energy']} v={e['vpp']}")
    print(f"\n  Trajectory: {traj_path}")
    print(f"  CSV:        {csv_path}")
    print(f"  Summary:    {summary_path}")

    return {
        "persona": persona_name, "city": city,
        "exit_code": ret,
        "energy_kwh_total": round(kwh_total, 2),
        "energy_kwh_per_day": round(kwh_per_day, 2),
        "pmv_ok_fraction": round(pmv_ok_frac, 4),
        "mean_temp_c": round(mean_temp, 2),
        "n_events_scored": n,
        "score_days_1_3": round(first3_avg, 2),
        f"score_days_{N_DAYS-2}_{N_DAYS}": round(last3_avg, 2),
        "improvement": round(improvement, 2),
        "event_log": loop.event_log,
        "output_dir": str(output_dir),
    }


class _Tee:
    """Write to multiple streams simultaneously (for tee-style logging)."""
    def __init__(self, *files): self.files = files
    def write(self, obj):
        for f in self.files: f.write(obj); f.flush()
    def flush(self):
        for f in self.files: f.flush()
    def fileno(self): return self.files[0].fileno()
    def isatty(self): return False


def main():
    parser = argparse.ArgumentParser(description="Multi-day memory & learning test")
    parser.add_argument("--persona", default="commuter",
                        choices=["commuter","comfort_sensitive","price_sensitive",
                                 "irregular_schedule","caregiver_rigid","night_owl_fatigued"],
                        help="Persona to test")
    parser.add_argument("--city", default="Tianjin",
                        choices=list(CITY_EPW.keys()),
                        help="Weather city")
    parser.add_argument("--days", default=7, type=int, choices=[7, 14],
                        help="Simulation length in days (default: 7)")
    parser.add_argument("--log-file", default=None,
                        help="Also write stdout+stderr to this file")
    args = parser.parse_args()

    # Log file tee setup
    if args.log_file:
        import io
        log_path = Path(args.log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        _log_f = open(log_path, "w", encoding="utf-8", buffering=1)
        sys.stdout = _Tee(sys.__stdout__, _log_f)
        sys.stderr = _Tee(sys.__stderr__, _log_f)
        print(f"[Logging to {log_path}]")

    # Override module-level globals based on --days
    global N_DAYS, TOTAL_HOURS, VPP_EVENTS, IDF_PATH
    N_DAYS = args.days
    TOTAL_HOURS = N_DAYS * 24
    VPP_EVENTS = [
        {"id": f"vpp{d}", "trigger_h": float(d * 24 - 6), "end_h": float(d * 24 - 5), "day": d}
        for d in range(1, N_DAYS + 1)
    ]
    IDF_PATH = BENCH_DIR.parent / "models" / "family_home" / f"family_simple_{N_DAYS}day.idf"

    run_14day_agent(persona_name=args.persona, city=args.city)
if __name__ == "__main__":
    main()
