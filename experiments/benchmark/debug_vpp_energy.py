"""Debug script: run family PMV vs Agent+PMV for Beijing, print timestep energy during VPP."""
import sys, os
from pathlib import Path
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / '.env')
except Exception:
    pass
EPLUS_ROOT = Path(os.getenv('EPLUS_ROOT', '/home/hku_user/EnergyPlus-24-1-0'))
sys.path.insert(0, str(PROJECT_ROOT / 'experiments' / 'benchmark'))
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(EPLUS_ROOT))
os.environ['ENERGYPLUS_DIR'] = str(EPLUS_ROOT)

from pathlib import Path
import shutil, math

IDF = PROJECT_ROOT / 'experiments' / 'models' / 'family_home' / 'family_simple_3day.idf'
EPW = PROJECT_ROOT / 'experiments' / 'weather' / 'epw' / 'CHN_BJ_Beijing.545110_CSWD.epw'
OUT = Path('/tmp/dbg_vpp')

VPP_EVENTS = [
    {"id": "vpp1", "trigger_h": 18, "end_h": 19},
    {"id": "vpp2", "trigger_h": 42, "end_h": 43},
    {"id": "vpp3", "trigger_h": 66, "end_h": 67},
]
SP_DEFAULT = 26.0
SP_MIN = 22.0; SP_MAX = 28.0; SP_STEP = 0.5
PMV_DEADBAND = 0.5
HTG_SP = 20.0
OCCUPIED_START = 8.0; OCCUPIED_END = 22.0

def _compute_pmv(t, rh=50):
    # simplified Fanger PMV
    ta = t; tr = ta; vel = 0.1; met = 1.1; icl = 0.57
    pa = rh * 10 * math.exp(16.6536 - 4030.183 / (ta + 235))
    icl2 = 0.155 * icl
    m = met * 58.15; w = 0
    mw = m - w
    if icl2 <= 0.078: fcl = 1 + 1.29 * icl2
    else: fcl = 1.05 + 0.645 * icl2
    hcf = 12.1 * math.sqrt(vel)
    taa = ta + 273; tra = tr + 273
    for _ in range(150):
        tcla = taa + (35.5 - ta) / (3.5 * icl2 + 0.1)
        p1 = icl2 * fcl; p2 = p1 * 3.96; p3 = p1 * 100; p4 = p1 * taa
        p5 = 308.7 - 0.028 * mw + p2 * (tra/100)**4
        xn = tcla / 100; xf = xn
        for _2 in range(150):
            xf = (xn + xf) / 2
            hcn = 2.38 * abs(100 * xf - taa)**0.25
            hc = max(hcf, hcn)
            xn = (p5 + p4*hc - p2*xf**4) / (100 + p3*hc)
            if abs(xn - xf) <= 1e-6: break
        tcl = 100 * xn - 273
        hl1 = 3.05e-3 * (5733 - 6.99 * mw - pa)
        hl2 = max(0, 0.42 * (mw - 58.15))
        hl3 = 1.7e-5 * met * (5867 - pa)
        hl4 = 0.0014 * met * (34 - ta)
        hl5 = 3.96 * fcl * (xn**4 - (tra/100)**4)
        hl6 = fcl * hc * (tcl - ta)
        ts = 0.303 * math.exp(-0.036 * met * 58.15) + 0.028
        return ts * (mw - hl1 - hl2 - hl3 - hl4 - hl5 - hl6)
    return 0

def _occupied(h): return OCCUPIED_START <= (h % 24) < OCCUPIED_END

def run_debug(method_name, sp_callback):
    """Run simulation and print VPP-period energy details."""
    out_dir = OUT / method_name
    if out_dir.exists(): shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    
    from pyenergyplus.api import EnergyPlusAPI
    api = EnergyPlusAPI(); state = api.state_manager.new_state(); ex = api.exchange
    ex.request_variable(state, "Zone Mean Air Temperature", "living_unit1")
    ex.request_variable(state, "Facility Total Electricity Demand Rate", "Whole Building")
    
    data = {
        'ready': False, 'h_cool': -1, 'h_heat': -1, 'h_temp': -1, 'h_fac': -1,
        'start_day': None, 'sp': SP_DEFAULT, 'prev_sim_h': -1.0,
        'e_wh': 0.0, 'e_vpp_wh': 0.0,
        'vpp_steps': []  # (sim_h, fac, sp, active_vpp)
    }
    
    def cb(s):
        if not data['ready']:
            if not ex.api_data_fully_ready(s): return
            data['h_cool'] = ex.get_actuator_handle(s, "Schedule:Compact", "Schedule Value", "cooling_sch")
            data['h_heat'] = ex.get_actuator_handle(s, "Schedule:Compact", "Schedule Value", "heating_sch")
            data['h_temp'] = ex.get_variable_handle(s, "Zone Mean Air Temperature", "living_unit1")
            data['h_fac']  = ex.get_variable_handle(s, "Facility Total Electricity Demand Rate", "Whole Building")
            data['ready'] = True
        
        day = ex.day_of_year(s)
        if data['start_day'] is None: data['start_day'] = day
        hod = ex.current_time(s); dt = ex.zone_time_step(s)
        sim_h = (day - data['start_day']) * 24 + hod
        wu = ex.warmup_flag(s)
        temp = ex.get_variable_value(s, data['h_temp']) if data['h_temp'] != -1 else SP_DEFAULT
        fac  = ex.get_variable_value(s, data['h_fac'])  if data['h_fac']  != -1 else 0.0
        occ  = _occupied(hod)
        
        active_vpp = None
        for ev in VPP_EVENTS:
            if ev["trigger_h"] <= sim_h < ev["end_h"]:
                active_vpp = ev; break
        
        # Apply setpoint (delegates to method-specific callback)
        data['sp'] = sp_callback(data, sim_h, hod, temp, occ, active_vpp)
        
        if data['h_cool'] != -1: ex.set_actuator_value(s, data['h_cool'], data['sp'])
        if data['h_heat'] != -1: ex.set_actuator_value(s, data['h_heat'], HTG_SP)
        data['prev_sim_h'] = sim_h
        
        if wu: return
        data['e_wh'] += fac * dt
        if active_vpp is not None:
            data['e_vpp_wh'] += fac * dt
            data['vpp_steps'].append((round(sim_h, 3), round(fac, 1), round(data['sp'], 1), temp))
        
        # Print a few hours around VPP
        for ev in VPP_EVENTS:
            if abs(sim_h - ev["trigger_h"]) < 0.5 or active_vpp is not None:
                if sim_h >= ev["trigger_h"] - 0.35 and sim_h <= ev["end_h"] + 0.1:
                    print(f"  {method_name} sim_h={sim_h:.3f} hod={hod:.3f} sp={data['sp']:.1f} "
                          f"temp={temp:.2f} fac={fac:.1f}W active={'Y' if active_vpp else 'N'} dt={dt:.4f}")
    
    api.runtime.callback_end_system_timestep_after_hvac_reporting(state, cb)
    ec = api.runtime.run_energyplus(state, ["-w", str(EPW), "-d", str(out_dir), str(IDF)])
    api.state_manager.delete_state(state)
    
    kwh = data['e_wh'] / 1000
    e_vpp = data['e_vpp_wh'] / 1000
    print(f"\n[{method_name}] total={kwh:.2f}kWh  e_vpp_3h={e_vpp:.3f}kWh  VPP steps={len(data['vpp_steps'])}")
    return kwh, e_vpp

# PMV method
def pmv_sp(d, sim_h, hod, temp, occ, active_vpp):
    sp = d['sp']
    pmv = _compute_pmv(temp)
    if pmv > PMV_DEADBAND: sp = max(SP_MIN, sp - SP_STEP)
    elif pmv < -PMV_DEADBAND: sp = min(SP_MAX, sp + SP_STEP)
    return sp

# Agent method (fixed sp=26°C non-VPP, LLM at VPP)
def agent_sp(d, sim_h, hod, temp, occ, active_vpp):
    if not occ:
        return 28.0
    # For testing: simulate agent with fixed 26.5°C during VPP (no LLM call)
    psim = d['prev_sim_h']
    for ev in VPP_EVENTS:
        if psim < ev["trigger_h"] <= sim_h:
            d['sp'] = 26.5  # simulate LLM setting 26.5°C
            return 26.5
    if active_vpp is not None:
        return d['sp']  # hold
    return SP_DEFAULT  # 26.0°C

# Agent+PMV: PMV during non-VPP, agent (26.5°C) during VPP
def agentpmv_sp(d, sim_h, hod, temp, occ, active_vpp):
    if not occ:
        return 28.0
    if active_vpp is not None:
        psim = d['prev_sim_h']
        for ev in VPP_EVENTS:
            if psim < ev["trigger_h"] <= sim_h:
                # VPP trigger: use PMV current sp as reference, raise to 26.5°C
                d['sp'] = 26.5  # simulate _llm_vpp returning 26.5°C
                return 26.5
        return d['sp']  # hold VPP sp
    else:
        # PMV control
        sp = d['sp']
        pmv = _compute_pmv(temp)
        if pmv > PMV_DEADBAND: sp = max(SP_MIN, sp - SP_STEP)
        elif pmv < -PMV_DEADBAND: sp = min(SP_MAX, sp + SP_STEP)
        d['sp'] = sp
        return sp

print("=== PMV ===")
pmv_kwh, pmv_vpp = run_debug('pmv', pmv_sp)

print("\n=== Agent (fixed 26.5 during VPP) ===")
agent_kwh, agent_vpp = run_debug('agent_fixed', agent_sp)

print("\n=== Agent+PMV (PMV non-VPP, 26.5 during VPP) ===")
apmv_kwh, apmv_vpp = run_debug('agent_pmv_fixed', agentpmv_sp)

print(f"\n=== SUMMARY ===")
print(f"PMV:       total={pmv_kwh:.2f}kWh  vpp_3h={pmv_vpp:.3f}kWh")
print(f"Agent:     total={agent_kwh:.2f}kWh  vpp_3h={agent_vpp:.3f}kWh  saving={pmv_vpp-agent_vpp:+.3f}")
print(f"Agent+PMV: total={apmv_kwh:.2f}kWh  vpp_3h={apmv_vpp:.3f}kWh  saving={pmv_vpp-apmv_vpp:+.3f}")
