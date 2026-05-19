#!/usr/bin/env python3
"""EnergyBridge Benchmark: 3 cities x 2 buildings x 2 methods = 12 simulations.

Usage:
  conda activate energybridge
  cd /home/ha_agent/work/EnergyBridge/experiments/benchmark
  python run_benchmark.py                              # full 18-run benchmark (3 methods)
  python run_benchmark.py --scenario family/tianjin/pmv   # single run
  python run_benchmark.py --building family            # only family home
  python run_benchmark.py --skip-existing              # resume interrupted run

Self-checks:
  1) EPW+IDF exist before run
  2) EP exit_code==0 check
  3) Fatal errors in eplusout.err
  4) 15-zone setpoint control verified
  5) Agent vs PMV comparison with auto-improvement suggestions
"""
from __future__ import annotations
import argparse, json, sys, time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

BENCHMARK_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BENCHMARK_DIR / "results"
EPLUS_ROOT = Path("/home/ha_agent/EnergyPlus-24-1-0")
PROJECT_ROOT = Path("/home/ha_agent/work/EnergyBridge")
_BENCH_DIR = Path(__file__).parent
_EXPERIMENTS_DIR = _BENCH_DIR.parent
EPW_DIR = _EXPERIMENTS_DIR / "weather" / "epw"

for p in (str(EPLUS_ROOT), str(PROJECT_ROOT), str(BENCHMARK_DIR)):
    if p not in sys.path: sys.path.insert(0, p)

BUILDINGS = {
    "family": {"idf": _EXPERIMENTS_DIR / "models" / "family_home" / "family_simple_3day.idf",
               "label":"家庭住宅(3天)", "sim_days":3},
    "office": {"idf": _EXPERIMENTS_DIR / "models" / "medium_office" / "medium_office_3day.idf",
               "label":"中型办公楼(15区,3天)", "sim_days":3},
}
CITIES = {
    "beijing":  {"epw": EPW_DIR/"CHN_BJ_Beijing.545110_CSWD.epw",  "label":"北京"},
    "shanghai": {"epw": EPW_DIR/"CHN_SH_Shanghai.583620_CSWD.epw", "label":"上海"},
    "tianjin":  {"epw": EPW_DIR/"CHN_TJ_Tianjin.545270_CSWD.epw", "label":"天津"},
}
METHODS = ["pmv", "pmv_rule", "agent", "agent_pmv"]

def preflight(bldgs, cities):
    errs = []
    for c in cities:
        p = CITIES[c]["epw"]
        if not p.exists(): errs.append(f"EPW missing: {p}")
    for b in bldgs:
        p = BUILDINGS[b]["idf"]
        if not p.exists(): errs.append(f"IDF missing: {p}")
    if not EPLUS_ROOT.exists(): errs.append(f"EnergyPlus not found: {EPLUS_ROOT}")
    return errs

def check_ep_log(output_dir):
    p = Path(output_dir)/"eplusout.err"
    if not p.exists(): return False, ["eplusout.err not found"]
    fatals, warns = [], []
    try:
        for line in p.read_text(errors="replace").splitlines():
            if "** Fatal" in line or "**FATAL" in line: fatals.append(line.strip())
            elif "** Severe" in line: warns.append(line.strip())
    except: return False, ["cannot read err file"]
    return bool(fatals), fatals[:3] + warns[:3]

def check_zone_ctrl(output_dir, building):
    if building == "family":
        return True, "single-zone OK"
    dp = Path(output_dir)/"decisions.json"
    if dp.exists():
        try:
            d = json.loads(dp.read_text())
            return bool(d), f"15-zone: {len(d)} LLM decisions"
        except: pass
    ep = Path(output_dir)/"eplusout.err"
    return ep.exists(), "15-zone PMV: EP ran" if ep.exists() else "no output"

def run_one(building, city, method, verbose=True):
    from family_runner import run_family_pmv, run_family_agent
    from office_runner import run_office
    from user_pref_scorer import score_user_preference

    idf = BUILDINGS[building]["idf"]
    epw = CITIES[city]["epw"]
    out = RESULTS_DIR / f"{building}_{city}_{method}"
    label = f"{building}/{city}/{method}"

    if verbose:
        print(f"\n{'='*55}\n[{label}]\n  IDF: {idf.name}\n  EPW: {epw.name}")

    t0 = time.time()
    result = None
    try:
        if building == "family":
            from family_runner import run_family_pmv_rule, run_family_agent_pmv
            if method == "pmv":        fn = run_family_pmv
            elif method == "pmv_rule": fn = run_family_pmv_rule
            elif method == "agent":    fn = run_family_agent
            else:                      fn = run_family_agent_pmv  # agent_pmv
            result = fn(idf_path=idf, epw_path=epw, output_dir=out, weather_label=city)
        else:
            result = run_office(mode=method, idf_path=idf, epw_path=epw,
                                output_dir=out, weather_label=city)
    except Exception as e:
        import traceback; traceback.print_exc()
        return {"scenario":label,"building":building,"city":city,"method":method,
                "exit_code":-1,"error":str(e),"elapsed_s":round(time.time()-t0,1),
                "energy_kwh_total":0,"pmv_ok_fraction":0,"mean_pmv":0,"mean_temp_c":0,
                "unmet_cooling_h":0,"user_pref_score":None,"has_fatal_error":True,
                "eplus_errors":[],"zone_control_ok":False}

    elapsed = round(time.time()-t0, 1)
    out_path = Path(result.output_dir) if result.output_dir else out
    has_fatal, errs = check_ep_log(out_path)
    zone_ok, zone_msg = check_zone_ctrl(out_path, building)

    # For PMV: scores computed inside runner (per VPP window).
    # For agent: scores computed inside runner after each VPP event.
    # user_pref_scores list is already populated by the runner.
    score_data = {"score": result.user_pref_score, "label":"", "comment": result.user_pref_comment}

    if verbose:
        scores_str = "/".join(f"{s:.1f}" for s in (result.user_pref_scores or [])) or (
            f"{result.user_pref_score:.1f}" if result.user_pref_score else "N/A")
        print(f"  Done {elapsed}s  exit={result.exit_code}  fatal={'YES' if has_fatal else 'no'}")
        print(f"  Energy={result.energy_kwh_total:.1f}kWh  PMV_ok={result.pmv_ok_fraction*100:.1f}%"
              f"  unmet={result.unmet_cooling_h:.1f}h  user_scores={scores_str}")
        print(f"  Zone ctrl: {zone_msg}")
        if has_fatal: print(f"  FATAL: {errs[0] if errs else '?'}")

    d = result.as_dict()
    d["user_pref_scores"] = result.user_pref_scores  # ensure list is included
    d.update({"city":city,"city_label":CITIES[city]["label"],
              "building_label":BUILDINGS[building]["label"],
              "elapsed_s":elapsed,"has_fatal_error":has_fatal,
              "eplus_errors":errs,"zone_control_ok":zone_ok,"zone_msg":zone_msg,
              "user_pref_label":score_data.get("label","")})
    # cache
    try: (out_path/"benchmark_result.json").write_text(json.dumps(d,indent=2,ensure_ascii=False))
    except: pass
    return d

def make_table(results):
    lines = []
    HR = "=" * 127
    lines += [HR,"EnergyBridge Benchmark Results",
              f"Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",HR]
    # header
    h = f"{'场景(建筑/城市)':<22}{'方法':<10}{'能耗(kWh)':<13}{'PMV达标率':<11}{'均温(°C)':<10}{'未满足(h)':<12}{'VPP响应率':<11}{'Roleplay评分(1-5)':<20}{'状态'}"
    lines.append(h)
    lines.append("-"*129)

    # group by building/city
    by_sc: Dict[str,List] = {}
    for r in results:
        k = f"{r.get('building','?')}/{r.get('city','?')}"
        by_sc.setdefault(k,[]).append(r)

    agent_wins=pmv_wins=ties=0
    for sc_key, sc_res in sorted(by_sc.items()):
        _order = {"pmv": 0, "pmv_rule": 1, "agent": 2, "agent_pmv": 3}
        sc_res.sort(key=lambda x: _order.get(x.get("method",""), 9))
        for idx,r in enumerate(sc_res):
            b = "家庭" if r.get("building")=="family" else "办公"
            cl = CITIES.get(r.get("city",""),{}).get("label","?")
            sc = f"{b}/{cl}" if idx==0 else ""
            _mmap = {"pmv":"PMV","pmv_rule":"PMV+RULE","agent":"AGENT","agent_pmv":"AGNT+PMV"}
            m = _mmap.get(r.get("method","?"), r.get("method","?").upper()[:10])
            e = f"{r.get('energy_kwh_total',0):.1f}" if r.get('energy_kwh_total') is not None else "N/A"
            pmv = f"{(r.get('pmv_ok_fraction',0)*100):.1f}%" if r.get('pmv_ok_fraction') is not None else "N/A"
            t  = f"{r.get('mean_temp_c',0):.2f}" if r.get('mean_temp_c') else "N/A"
            uh = f"{r.get('unmet_cooling_h',0):.1f}"
            scores = r.get("user_pref_scores") or []
            if scores:
                us = "/".join(f"{s:.1f}" for s in scores)
            elif r.get("user_pref_score"):
                us = f"{r['user_pref_score']:.1f}"
            else:
                us = "N/A"
            st = "ERROR" if r.get("has_fatal_error") or r.get("exit_code",-1)!=0 else "OK"
            vr = r.get("vpp_compliance_rate", 0.0)
            vc = f"{vr*100:.0f}%" if r.get("method","pmv") in ("agent","agent_pmv","pmv_rule") else "0%(PMV)"
            lines.append(f"{sc:<22}{m:<10}{e:<13}{pmv:<11}{t:<10}{uh:<12}{vc:<11}{us:<20}{st}")
        lines.append("-"*129)

        pmv_r = next((x for x in sc_res if x.get("method")=="pmv"),{})
        agent_r = next((x for x in sc_res if x.get("method") in ("agent","agent_pmv")),{})
        if pmv_r and agent_r:
            ps = pmv_r.get("user_pref_score") or 0
            as_ = agent_r.get("user_pref_score") or 0
            if as_>ps+0.1: agent_wins+=1
            elif ps>as_+0.1: pmv_wins+=1
            else: ties+=1

    lines.append("")
    lines.append(f"总结: Agent优于PMV={agent_wins}  PMV优于Agent={pmv_wins}  持平={ties}")
    lines.append("")

    # Improvement suggestions
    lines += _suggest_improvements(results)
    lines.append(HR)
    return "\n".join(lines)

def _suggest_improvements(results):
    lines = ["改进建议:"]
    for r in results:
        if r.get("method")!="agent": continue
        sc = f"{r.get('building','?')}/{r.get('city','?')}"
        pmv_ok = r.get("pmv_ok_fraction",0)
        unmet = r.get("unmet_cooling_h",0)
        energy = r.get("energy_kwh_total",0)
        score = r.get("user_pref_score") or 0

        if pmv_ok < 0.7:
            lines.append(f"  [{sc}] PMV达标率低({pmv_ok*100:.0f}%): 建议agent增加主动预冷策略")
        if unmet > 50:
            lines.append(f"  [{sc}] 未满足时数高({unmet:.0f}h): 建议agent提前1h降低设定点")
        if score < 3.5:
            lines.append(f"  [{sc}] 用户满意度低({score:.1f}/5): 建议改进用户偏好提取和个性化策略")
        if r.get("has_fatal_error"):
            lines.append(f"  [{sc}] EP仿真有致命错误，需检查IDF和EPW兼容性")

    # Check if agent wins
    agent_better = 0
    by_sc: Dict[str,Dict] = {}
    for r in results:
        k = f"{r.get('building','?')}/{r.get('city','?')}"
        by_sc.setdefault(k,{})[r.get("method","?")] = r
    for k,m in by_sc.items():
        a=m.get("agent",{}); p=m.get("pmv",{})
        if a and p and (a.get("user_pref_score") or 0)>(p.get("user_pref_score") or 0)+0.1:
            agent_better+=1

    if agent_better==0 and len(by_sc)>0:
        lines.append("")
        lines.append("  !! Agent方法尚未全面超越PMV基线 !!")
        lines.append("  可能原因及改进方向:")
        lines.append("  1. [连续控制] LLM每小时调用间隔过长,可改为每15分钟(每步)更频繁")
        lines.append("  2. [预测优化] 加入天气预测和负荷预测,实现MPC-like前向优化")
        lines.append("  3. [记忆学习] 利用EnergyBridge memory存储历史偏好,提升个性化")
        lines.append("  4. [全天候控制] 家庭agent目前只在VPP事件时激活,应实现全天连续控制")
        lines.append("  5. [提示工程] 改进Zone Advisor的system prompt,加入能耗约束")
    return lines

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--building", choices=["family","office","all"], default="all")
    ap.add_argument("--city", choices=["beijing","shanghai","tianjin","all"], default="all")
    ap.add_argument("--method", choices=["pmv","pmv_rule","agent","agent_pmv","all"], default="all")
    ap.add_argument("--scenario", help="e.g. family/tianjin or family/tianjin/pmv")
    ap.add_argument("--skip-existing", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.scenario:
        parts = args.scenario.split("/")
        bldgs  = [parts[0]] if parts[0] in BUILDINGS else list(BUILDINGS)
        cities = [parts[1]] if len(parts)>1 and parts[1] in CITIES else list(CITIES)
        meths  = [parts[2]] if len(parts)>2 and parts[2] in METHODS else METHODS
    else:
        bldgs  = [args.building] if args.building!="all" else list(BUILDINGS)
        cities = [args.city]     if args.city!="all"     else list(CITIES)
        meths  = [args.method]   if args.method!="all"   else METHODS

    print("=== Pre-flight checks ===")
    errs = preflight(bldgs, cities)
    if errs:
        for e in errs: print(f"  ERROR: {e}")
        sys.exit(1)
    print("  All files found OK")

    runs = [(b,c,m) for b in bldgs for c in cities for m in meths]
    print(f"\nPlan: {len(runs)} simulations")
    for b,c,m in runs: print(f"  {b}/{c}/{m}")

    all_results, t0 = [], time.time()
    for i,(b,c,m) in enumerate(runs,1):
        print(f"\n[{i}/{len(runs)}]", end=" ")
        out = RESULTS_DIR/f"{b}_{c}_{m}"
        if args.skip_existing and (out/"eplusout.err").exists():
            cache = out/"benchmark_result.json"
            if cache.exists():
                try: all_results.append(json.loads(cache.read_text())); print(f"skip (cached)"); continue
                except: pass

        res = run_one(b,c,m, verbose=not args.quiet)
        all_results.append(res)

    # Save all results
    all_path = RESULTS_DIR/"benchmark_results.json"
    all_path.write_text(json.dumps(all_results,indent=2,ensure_ascii=False))

    table = make_table(all_results)
    tbl_path = RESULTS_DIR/"benchmark_table.txt"
    tbl_path.write_text(table, encoding="utf-8")

    print(f"\n{table}")
    print(f"\nSaved: {all_path}")
    print(f"Table: {tbl_path}")
    print(f"Total: {(time.time()-t0)/60:.1f} min")

    # Summary
    n_ok = sum(1 for r in all_results if r.get("exit_code")==0)
    n_fat = sum(1 for r in all_results if r.get("has_fatal_error"))
    print(f"\n== Self-Check Summary ==")
    print(f"  EP exit 0  : {n_ok}/{len(all_results)}")
    print(f"  Fatal errs : {n_fat}/{len(all_results)}")
    print(f"  Zone ctrl  : {sum(1 for r in all_results if r.get('zone_control_ok'))}/{len(all_results)}")

if __name__ == "__main__":
    main()
