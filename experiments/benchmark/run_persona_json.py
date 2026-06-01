#!/usr/bin/env python3
"""Run the family home benchmark for a single persona JSON file.

Usage
-----
  python3 run_persona_json.py <persona_id_or_json_path> [--output <dir>] [--city <Tianjin|Beijing|Shanghai>]

Examples
--------
  python3 run_persona_json.py atom_comfort_sensitive
  python3 run_persona_json.py ../../energybridge/roleplay/personas/atom_comfort_sensitive.json
  python3 run_persona_json.py basic_role_f_commuter_ev_optimizer --city Shanghai --output /tmp/out

Output
------
  <output_dir>/  - EnergyPlus raw files (CSV, HTML, audit...)
  Console shows per-VPP-event LLM decisions + appliance rule summary.

Prerequisites
-------------
  conda activate energybridge
  cp .env.example .env   # set LLM_API_KEY_POOL
  pip install -r requirements.txt
"""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path
from dotenv import load_dotenv

_BENCH_DIR    = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
load_dotenv(_PROJECT_ROOT / ".env")

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))

import family_runner as fr

PERSONA_DIR = _PROJECT_ROOT / "energybridge" / "roleplay" / "personas"


def _load_persona_json(persona_arg: str) -> dict:
    """Accept a persona ID or a path to a JSON file."""
    p = Path(persona_arg)
    if p.exists() and p.suffix == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    # Try by ID in the standard location
    candidate = PERSONA_DIR / f"{persona_arg}.json"
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
    raise FileNotFoundError(
        f"Persona '{persona_arg}' not found. "
        f"Checked: {p}, {candidate}"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run family home benchmark for a single persona."
    )
    parser.add_argument(
        "persona",
        help="Persona ID (e.g. atom_comfort_sensitive) or path to a persona JSON file.",
    )
    parser.add_argument(
        "--output", "-o", default=None,
        help="Directory for EnergyPlus output files. "
             "Defaults to experiments/benchmark/results/<persona_id>/",
    )
    parser.add_argument(
        "--city", "-c", default="Tianjin",
        choices=["Tianjin", "Beijing", "Shanghai"],
        help="Weather city label (default: Tianjin).",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print full LLM prompt + raw JSON response for each agent call.",
    )
    parser.add_argument(
        "--human", action="store_true",
        help="Human-in-the-loop: show 3 VPP strategies and wait for terminal selection.",
    )
    args = parser.parse_args()

    persona = _load_persona_json(args.persona)
    pid     = persona["id"]
    output_dir = (
        Path(args.output) if args.output
        else _PROJECT_ROOT / "benchmark_results" / pid
    )

    print("=" * 70)
    print(f"PERSONA : {pid}")
    print(f"CITY    : {args.city}")
    print(f"OUTPUT  : {output_dir}")
    print("=" * 70)

    result = fr.run_family_agent(
        user_pref        = persona["llm_prompts"]["system_prompt"],
        appliance_config = persona.get("appliances", {}),
        output_dir       = output_dir,
        weather_label    = args.city.lower(),
        verbose          = args.verbose,
        human_mode       = args.human,
    )

    print()
    print("=" * 70)
    print("RESULT SUMMARY")
    print("=" * 70)
    for k, v in result.as_dict().items():
        print(f"  {k}: {v}")

    # ── Save structured JSON ──────────────────────────────────────────
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "benchmark_result.json"
    json_path.write_text(
        json.dumps(result.as_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n[Saved] benchmark_result.json → {json_path}")

    # ── Generate human-readable run_summary.txt (pure algorithm) ─────
    txt_path = _write_run_summary(result, persona, args.city, output_dir)
    print(f"[Saved] run_summary.txt         → {txt_path}")

    # ── Call analyze_eplus_run.py --report for EP-level MD ────────────


def _fmt_h(h) -> str:
    """Float hour → 'HH:MM' string."""
    if h is None:
        return "?"
    return f"{int(h) % 24:02d}:{int((h % 1) * 60):02d}"


def _fmt_strategy(sp_str: str, trigger_actions: dict, day_decisions: list,
                   present_devices: set = None) -> list:
    """
    Build a multi-line strategy display from:
      sp_str          - AC setpoint string at VPP trigger
      trigger_actions - appliance_actions dict at VPP trigger moment
      day_decisions   - list of {h, sp, actions} for the whole day
    Returns list of display lines (one per device touched).
    """
    ta = trigger_actions or {}

    # ── AC ──
    # Gather all setpoints used across the day
    day_sps = [f"→{d['sp']:.1f}°C@{_fmt_h(d['h'])}" for d in day_decisions]
    ac_line = f"    ├ 空调     : {sp_str} (VPP触发)  全天调整: {', '.join(day_sps) if day_sps else '仅1次'}"

    lines = [ac_line]

    # ── Shiftable appliances ──
    pd = present_devices or set()
    for dev, label in [("washer", "洗衣机"), ("dishwasher", "洗碗机"), ("dryer", "烘干机")]:
        if pd and dev not in pd:
            continue  # skip appliances not installed in this household
        start_k = f"{dev}_start_h"
        skip_k  = f"{dev}_skip"
        # aggregate across all day decisions (last non-null wins)
        start_h = ta.get(start_k)
        skip    = ta.get(skip_k)
        for d in day_decisions:
            a = d.get("actions", {})
            if a.get(start_k) is not None:
                start_h = a[start_k]
            if a.get(skip_k) is not None:
                skip = a[skip_k]
        if skip:
            lines.append(f"    ├ {label:<5}: 跳过 (agent指令skip)")
        elif start_h is not None:
            lines.append(f"    ├ {label:<5}: 排程@{_fmt_h(start_h)}")
        else:
            lines.append(f"    ├ {label:<5}: 未显式排程 (依赖自主调度)")

    # ── Water heater ──
    if not pd or "water_heater" in pd:
        wh_preheat    = ta.get("water_heater_preheat")
        wh_start      = ta.get("water_heater_preheat_start_h")
        wh_end        = ta.get("water_heater_preheat_end_h")
        wh_temp       = ta.get("water_heater_preheat_temp_c")
        for d in day_decisions:
            a = d.get("actions", {})
            if a.get("water_heater_preheat") is not None:
                wh_preheat = a["water_heater_preheat"]
            if a.get("water_heater_preheat_start_h") is not None:
                wh_start = a["water_heater_preheat_start_h"]
            if a.get("water_heater_preheat_end_h") is not None:
                wh_end = a["water_heater_preheat_end_h"]
            if a.get("water_heater_preheat_temp_c") is not None:
                wh_temp = a["water_heater_preheat_temp_c"]
        if wh_preheat is False:
            wh_str = "关闭预热"
        elif wh_start is not None and wh_end is not None:
            temp_s = f" @ {wh_temp:.0f}°C" if wh_temp else ""
            wh_str = f"预热 {_fmt_h(wh_start)}-{_fmt_h(wh_end)}{temp_s}"
        elif wh_start is not None:
            wh_str = f"预热开始@{_fmt_h(wh_start)}"
        else:
            wh_str = "未显式控制 (默认预热窗口)"
        lines.append(f"    ├ 热水器  : {wh_str}")

    # ── EV ──
    if not pd or "ev" in pd:
        ev_mode  = ta.get("ev_mode")
        ev_start = ta.get("ev_charge_start_h")
        ev_end   = ta.get("ev_charge_end_h")
        for d in day_decisions:
            a = d.get("actions", {})
            if a.get("ev_mode") is not None:
                ev_mode = a["ev_mode"]
            if a.get("ev_charge_start_h") is not None:
                ev_start = a["ev_charge_start_h"]
            if a.get("ev_charge_end_h") is not None:
                ev_end = a["ev_charge_end_h"]
        if ev_mode == "delay":
            ev_str = f"delay模式 (22:00后充电)"
        elif ev_mode == "smart":
            ev_str = "smart模式 (自动避峰)"
        elif ev_mode == "normal":
            ev_str = "normal模式 (立即充电)"
        elif ev_start is not None and ev_end is not None:
            ev_str = f"充电窗口 {_fmt_h(ev_start)}-{_fmt_h(ev_end)}"
        elif ev_start is not None:
            ev_str = f"充电开始@{_fmt_h(ev_start)}"
        else:
            ev_str = "未显式控制 (默认smart)"
        lines.append(f"    └ EV      : {ev_str}")

    # Fix tree connector: last line should use └ instead of ├
    for i in range(len(lines)-1, -1, -1):
        if lines[i].strip().startswith('├'):
            lines[i] = lines[i].replace('├', '└', 1)
            break
    return lines


def _vpp_ratio_str(result) -> str:
    """Compute aggregate VPP demand achievement: sum(actual_kwh)/sum(target_kwh)."""
    events = result.vpp_event_log
    actuals = [e.get("actual_kwh", 0.0) for e in events if e.get("demand_target_kwh")]
    targets = [e.get("demand_target_kwh", 0.0) for e in events if e.get("demand_target_kwh")]
    if not targets or sum(targets) == 0:
        return "N/A (no demand targets)"
    total_a = sum(actuals)
    total_t = sum(targets)
    ratio = total_a / total_t
    per_ev = "  ".join(f"VPP{i+1}:{a:.3f}/{t:.2f}" for i, (a, t) in enumerate(zip(actuals, targets)))
    ok = "✓达标" if ratio <= 1.0 else "✗超标"
    return f"{total_a:.3f}/{total_t:.3f}kWh = {ratio:.2f} {ok}  [{per_ev}]"

def _write_run_summary(result, persona: dict, city: str, output_dir: Path) -> Path:
    """Write a human-readable run_summary.txt (no LLM, pure algorithm)."""
    from datetime import datetime
    d = result.as_dict()
    appl = result.appliance_results   # dict: device -> list of day dicts
    evts = result.vpp_event_log       # list of scored VPP event dicts

    scores = d.get("user_pref_scores") or []
    avg_score = d.get("user_pref_score")
    avg_score_str = f"{avg_score:.1f}/5" if avg_score is not None else "N/A"
    llm_avg_lat = (d["llm_latency_total_s"] / d["llm_call_count"]
                   if d.get("llm_call_count") else 0.0)
    ep_ok = "成功 ✓" if d.get("exit_code") == 0 else f"失败 (exit={d.get('exit_code')})"

    lines = [
        "=" * 62,
        "  EnergyBridge 运行摘要  (run_summary.txt)",
        "=" * 62,
        f"  Persona    : {persona.get('id', '?')}",
        f"  名称       : {persona.get('name', '')}",
        f"  城市       : {city}",
        f"  生成时间   : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  输出目录   : {output_dir}",
        "",
    ]

    # ── 页头：电器配置 ─────────────────────────────────────────────────
    _appl_names = {"washer": "洗衣机", "dishwasher": "洗碗机", "dryer": "烘干机",
                   "water_heater": "热水器", "ev": "EV充电桩"}
    _first_summ = evts[0].get("appliance_summary", {}) if evts else {}
    _present = [_appl_names.get(k, k) for k, v in _first_summ.items() if v.get("present")]
    _absent  = [_appl_names.get(k, k) for k, v in _first_summ.items() if not v.get("present")]
    _present_str = " | ".join(_present) if _present else "(无)"
    _absent_str  = " | ".join(_absent)  if _absent  else "(无)"
    lines += [
        f"  本户电器  : ✓ 已有: {_present_str}   ✗ 未配置: {_absent_str}",
        "",
    ]
    # ── Section 1: VPP 事件详情 ──────────────────────────────────────
    lines += ["─" * 62, "  VPP 事件详情（电网事件 → 策略 → 执行结果）", "─" * 62]
    vpp_defs = [
        {"id": "vpp1", "trigger_h": 18, "end_h": 19, "day": 1},
        {"id": "vpp2", "trigger_h": 42, "end_h": 43, "day": 2},
        {"id": "vpp3", "trigger_h": 66, "end_h": 67, "day": 3},
    ]
    evt_by_id = {e["id"]: e for e in evts}
    for vdef in vpp_defs:
        vid = vdef["id"]
        ev_num = int(vid[3:])
        e = evt_by_id.get(vid, {})
        score_str = f"{e['score']}/5 ({e.get('label','?')})" if e.get("score") is not None else "未评分"
        sp_str = f"{e['setpoint']:.1f}°C" if e.get("setpoint") else "N/A"
        # VPP demand: actual vs target (from grid-side demand agent)
        demand_t = e.get("demand_target_kwh")
        actual_k = e.get("actual_kwh")
        if demand_t and actual_k is not None:
            ratio = actual_k / demand_t if demand_t > 0 else 0
            ok = "✓达标" if ratio <= 1.0 else "✗超标"
            demand_str = f"目标≤{demand_t:.2f}kWh  实际{actual_k:.3f}kWh  比率{ratio:.2f} {ok}"
        else:
            demand_str = "(demand agent not run for this event)"
        reason = e.get("reason", "")
        comment = e.get("comment", "")
        # Per-appliance VPP avoidance for this event
        appl_summ = e.get("appliance_summary", {})
        appl_avoid_parts = []
        for nm, info in appl_summ.items():
            if not info.get("present", False):
                continue
            if nm in ("water_heater", "ev"):
                avoided = not info.get("ran_during_vpp", False)
                appl_avoid_parts.append(f"{nm}:{'✓避峰' if avoided else '✗VPP中运行'}")
            else:
                # shiftable appliances: skip != avoidance
                if info.get("skipped"):
                    appl_avoid_parts.append(f"{nm}:✗跳过任务")
                elif info.get("ran_during_vpp"):
                    appl_avoid_parts.append(f"{nm}:✗VPP中运行")
                else:
                    appl_avoid_parts.append(f"{nm}:✓错峰完成")
        appl_avoid_str = "  ".join(appl_avoid_parts) if appl_avoid_parts else "无可控电器"
        trigger_actions = e.get("vpp_trigger_actions", {})
        day_decisions   = e.get("day_decisions", [])
        _pdev = {k for k, v in e.get("appliance_summary", {}).items() if v.get("present")}
        strat_lines = _fmt_strategy(sp_str, trigger_actions, day_decisions, present_devices=_pdev)
        lines += [
            f"  [事件{ev_num}] Day{vdef['day']} {int(vdef['trigger_h']%24):02d}:00-{int(vdef['end_h']%24):02d}:00"
            f"  目标：需求侧削峰1小时",
            f"    执行策略 ↓ (全天{len(day_decisions)}次LLM决策):",
        ] + strat_lines + [
            f"    VPP需求    : {demand_str}",
            f"    Agent理由  : {reason}" if reason else "",
            f"    电器避峰   : {appl_avoid_str}",
            f"    用户评分   : {score_str}",
            f"    评分说明   : {comment[:100]}" if comment else "",
            "",
        ]
    lines = [l for l in lines if l != ""]  # drop blank-only lines from empty fields

    # ── Section 2: 电器调度目标达成 ──────────────────────────────────
    lines += ["", "─" * 62, "  电器调度目标达成", "─" * 62]

    def _goal_flag(completed, skipped, ran_vpp):
        if skipped:       return "✗跳过(未完成)"
        if not completed: return "✗未完成"
        if ran_vpp:       return "⚠ 完成(VPP中)"
        return "✓ 完成"

    shiftable_order = ["washer", "dishwasher", "dryer"]
    has_shiftable = False
    for dev in shiftable_order:
        days_data = appl.get(dev, [])
        if not days_data or not days_data[0].get("present", False):
            continue   # not in household — don't show
        has_shiftable = True
        parts = []
        for day_d in days_data:
            sched_h = day_d.get("scheduled_abs_h", 0) % 24
            flag = _goal_flag(day_d.get("completed", False),
                              day_d.get("skipped", False),
                              day_d.get("ran_during_vpp", False))
            parts.append(f"Day{day_d['day']+1}[{int(sched_h):02d}:00 {flag}]")
        lines.append(f"  {dev:<14}: " + "  ".join(parts))

    # Per-day shiftable completion rate (from metrics)
    per_day = d.get("task_completion_per_day", [])
    if has_shiftable and per_day:
        day_strs = "  ".join(f"Day{i+1}:{int(v*100)}%" for i, v in enumerate(per_day))
        lines.append(f"  {'完成率(逐天)':<14}: {day_strs}")

    # Water heater (only if present)
    wh_days = appl.get("water_heater", [])
    if wh_days and wh_days[0].get("present", False):
        parts = []
        for day_d in wh_days:
            ph = "预热✓" if day_d.get("preheat_used") else "预热✗"
            bath = "浴前就绪✓" if day_d.get("ready_at_bath", True) else "浴前就绪✗"
            vpp_flag = "⚠VPP中加热" if day_d.get("ran_during_vpp") else ""
            ekwh = day_d.get("energy_kwh", 0)
            parts.append(f"Day{day_d['day']+1}[{ph} {bath} {vpp_flag} {ekwh:.1f}kWh]")
        lines.append(f"  {'water_heater':<14}: " + "  ".join(parts))

    # EV (only if present)
    ev_days = appl.get("ev", [])
    if ev_days and ev_days[0].get("present", False):
        parts = []
        for day_d in ev_days:
            tgt = "SOC达标✓" if day_d.get("target_reached") else "SOC未达标✗"
            vpp_flag = "⚠VPP中充电" if day_d.get("ran_during_vpp") else ""
            ekwh = day_d.get("energy_kwh", 0)
            soc = day_d.get("soc_end", 0)
            parts.append(f"Day{day_d['day']+1}[{tgt} SOC={soc:.0%} {ekwh:.1f}kWh {vpp_flag}]")
        lines.append(f"  {'ev':<14}: " + "  ".join(parts))

    # ── Section 3: 关键 Metrics 汇总 ──────────────────────────────────
    score_per_event = "  ".join(f"VPP{i+1}:{s}" for i, s in enumerate(scores)) if scores else "N/A"
    lines += [
        "",
        "─" * 62,
        "  关键 Metrics 汇总",
        "─" * 62,
        f"  ▸ VPP削峰",
        f"      VPP时段用电量: {d.get('vpp_window_energy_kwh', 0):.3f} kWh (3个事件×1h合计)",
        ("      需求达成比率 : " + _vpp_ratio_str(result)),
        f"      任务完成率   : {d.get('appliance_task_completion_rate', 1.0)*100:.0f}%"
        f"  (✓=错峰完成 ✗=跳过任务/未完成)",
        f"      错峰率       : {d.get('appliance_vpp_avoidance_rate', 0)*100:.0f}%"
        f"  (仅统计已完成任务中未在VPP窗口内运行的比例)",
        f"  ▸ 用电量",
        f"      总能耗       : {d.get('energy_kwh_total', 0):.2f} kWh (3天)",
        f"      日均          : {d.get('energy_kwh_per_day', 0):.2f} kWh/天",
        f"  ▸ 用户舒适度",
        f"      满意度均分   : {avg_score_str}",
        f"      逐事件       : {score_per_event}",
        f"      区域均温     : {d.get('mean_temp_c', 0):.2f} °C",
        f"      PMV达标率    : {d.get('pmv_ok_fraction', 0)*100:.1f}%",
        f"      未满足制冷   : {d.get('unmet_cooling_h', 0):.1f} h",
        f"  ▸ 电器目标达成",
        f"      EV充电达标   : {d.get('ev_target_reached_rate', 0)*100:.0f}%",
        f"      热水器预热   : {d.get('ewh_preheat_used_rate', 0)*100:.0f}%",
        f"  ▸ Token消耗",
        f"      LLM调用      : {d.get('llm_call_count', 0)} 次 (失败 {d.get('llm_call_failures', 0)})",
        f"      平均延迟     : {llm_avg_lat:.2f} s/次",
        f"      Token总量    : {d.get('llm_tokens_prompt', 0)} prompt + {d.get('llm_tokens_completion', 0)} completion",
        "",
        "─" * 62,
        "  EnergyPlus",
        "─" * 62,
        f"  EP 结果          : {ep_ok}",
        f"  详细日志         : {output_dir}/eplusout.err",

        "=" * 62,
    ]
    txt_path = output_dir / "run_summary.txt"
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return txt_path

def _generate_ep_report(output_dir: Path) -> None:
    """Call analyze_eplus_run.py to produce eplus_run_report.md (pure algorithm)."""
    import subprocess
    analyzer = _PROJECT_ROOT / "examples" / "analyze_eplus_run.py"
    if not analyzer.exists():
        print(f"  [Skip EP report] {analyzer} not found")
        return
    cmd = [
        sys.executable, str(analyzer),
        "--output", str(output_dir),
        "--report",
        "--trigger", "18.0",
        "--duration", "60",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        md_path = output_dir / "eplus_run_report.md"
        if md_path.exists():
            print(f"[Saved] eplus_run_report.md     → {md_path}")
        else:
            print(f"  [EP report] no MD generated (exit={proc.returncode})")
    except Exception as e:
        print(f"  [EP report] skipped: {e}")


if __name__ == "__main__":
    main()
