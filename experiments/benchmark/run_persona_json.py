#!/usr/bin/env python3
"""Run the family home benchmark for a single persona JSON file.

Usage
-----
  python3 run_persona_json.py <persona_id_or_json_path> [--output <dir>] [--city <Tianjin|Beijing|Shanghai>] [--method <agent|mpc_dynamic|mpc_ep>]

Examples
--------
  python3 run_persona_json.py atom_comfort_sensitive
  python3 run_persona_json.py ../../energybridge/roleplay/personas/atom_comfort_sensitive.json
  python3 run_persona_json.py basic_role_f_commuter_ev_optimizer --city Shanghai --output /tmp/out
  python3 run_persona_json.py atom_comfort_sensitive --city Tianjin --method mpc_dynamic
  python3 run_persona_json.py atom_comfort_sensitive --city Tianjin --method mpc_ep

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
import argparse, json, re, shutil, sys
from datetime import date
from pathlib import Path
from dotenv import load_dotenv

_BENCH_DIR    = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
DEFAULT_BENCHMARK_RESULTS_DIR = _PROJECT_ROOT / "benchmark_results"
load_dotenv(_PROJECT_ROOT / ".env")

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))

import family_runner as fr
from energybridge.data.day_ahead import (
    DEFAULT_GERMANY_EPW,
    DEFAULT_GERMANY_WEATHER_CSV,
    generate_epw_from_openmeteo_csv,
    generate_runperiod_idf,
    maybe_load_price_profile,
)
from energybridge.roleplay.calendar import attach_calendar

PERSONA_DIR = _PROJECT_ROOT / "energybridge" / "roleplay" / "personas"
FAMILY_MODEL_DIR = _PROJECT_ROOT / "experiments" / "models" / "family_home"
DEFAULT_FAMILY_IDF_BY_DAYS = {
    3: FAMILY_MODEL_DIR / "family_simple_3day.idf",
    7: FAMILY_MODEL_DIR / "family_simple_7day.idf",
    14: FAMILY_MODEL_DIR / "family_simple_14day.idf",
}
EPW_DIR = _PROJECT_ROOT / "experiments" / "weather" / "epw"
DEFAULT_EPW_BY_CITY = {
    "tianjin": EPW_DIR / "CHN_TJ_Tianjin.545270_CSWD.epw",
    "beijing": EPW_DIR / "CHN_BJ_Beijing.545110_CSWD.epw",
    "shanghai": EPW_DIR / "CHN_SH_Shanghai.583620_CSWD.epw",
    "germany": DEFAULT_GERMANY_EPW,
}
STANDARD_TIMEZONE_BY_CITY = {
    "tianjin": 8.0,
    "beijing": 8.0,
    "shanghai": 8.0,
    "germany": 1.0,
}


def _load_persona_json(persona_arg: str) -> dict:
    """Accept a persona ID or a path to a JSON file."""
    p = Path(persona_arg)
    if p.exists() and p.suffix == ".json":
        return attach_calendar(json.loads(p.read_text(encoding="utf-8")), PERSONA_DIR)
    # Try by ID in the standard location
    candidate = PERSONA_DIR / f"{persona_arg}.json"
    if candidate.exists():
        return attach_calendar(json.loads(candidate.read_text(encoding="utf-8")), PERSONA_DIR)
    raise FileNotFoundError(
        f"Persona '{persona_arg}' not found. "
        f"Checked: {p}, {candidate}"
    )


def _persona_run_label(persona_id: str) -> str:
    match = re.match(r"^basic_role_([a-z])(?:_|$)", persona_id)
    if match:
        return f"role_{match.group(1)}"
    return re.sub(r"[^a-zA-Z0-9]+", "_", persona_id).strip("_").lower()


def _slug_label(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9]+", "_", value or "").strip("_").lower()


def _run_prefix(persona_id: str, human_name: str = "") -> str:
    if human_name:
        return f"{_slug_label(human_name) or 'human'}_human"
    return _persona_run_label(persona_id)


def _method_run_token(method: str, mpc_horizon: int = 6) -> str:
    method = _canonical_method(method)
    if method in ("mpc_dynamic", "mpc_ep"):
        return f"{method}_H{int(mpc_horizon)}"
    return method


def _default_output_dir(
    persona_id: str,
    method: str,
    city: str,
    days: int = 3,
    mpc_horizon: int = 6,
    human_name: str = "",
) -> Path:
    run_name = (
        f"{_run_prefix(persona_id, human_name)}_"
        f"{_method_run_token(method, mpc_horizon)}_"
        f"{city.lower()}_{days}days"
    )
    return DEFAULT_BENCHMARK_RESULTS_DIR / date.today().isoformat() / run_name


def _prepare_default_output_dir(
    persona_id: str,
    method: str,
    city: str,
    days: int = 3,
    mpc_horizon: int = 6,
    human_name: str = "",
) -> Path:
    """Return the default run directory, replacing only that exact run."""
    method = _canonical_method(method)
    output_dir = _default_output_dir(
        persona_id, method, city, days=days, mpc_horizon=mpc_horizon, human_name=human_name
    )
    expected_parent = DEFAULT_BENCHMARK_RESULTS_DIR / date.today().isoformat()
    expected_name = (
        f"{_run_prefix(persona_id, human_name)}_"
        f"{_method_run_token(method, mpc_horizon)}_"
        f"{city.lower()}_{days}days"
    )
    if output_dir.exists():
        if output_dir.parent != expected_parent or output_dir.name != expected_name:
            raise RuntimeError(f"Refusing to overwrite unexpected output path: {output_dir}")
        shutil.rmtree(output_dir)
    return output_dir


def _default_days_for_city(city: str, requested_days: int | None) -> int:
    if requested_days is not None:
        return max(1, int(requested_days))
    return 7 if city.lower() == "germany" else 3


def _default_start_date_for_city(city: str, requested_start: str) -> str:
    if requested_start:
        return requested_start
    return "2025-06-01" if city.lower() == "germany" else ""


def _prepare_run_assets(args: argparse.Namespace, output_dir: Path, days: int, start_date: str):
    city_key = args.city.lower()
    epw_path = Path(args.epw) if args.epw else DEFAULT_EPW_BY_CITY[city_key]
    price_profile = None
    if city_key == "germany":
        weather_csv = Path(args.weather_csv) if args.weather_csv else DEFAULT_GERMANY_WEATHER_CSV
        if not epw_path.exists() or args.regenerate_epw:
            print(f"[Germany] generating EPW from {weather_csv} -> {epw_path}")
            generate_epw_from_openmeteo_csv(weather_csv, epw_path)
    if args.price_csv:
        price_profile = maybe_load_price_profile(
            Path(args.price_csv),
            standard_timezone_hours=STANDARD_TIMEZONE_BY_CITY.get(city_key),
        )

    template_idf = Path(args.idf) if args.idf else DEFAULT_FAMILY_IDF_BY_DAYS.get(
        days,
        DEFAULT_FAMILY_IDF_BY_DAYS[7] if days > 3 else DEFAULT_FAMILY_IDF_BY_DAYS[3],
    )
    idf_path = template_idf
    if start_date:
        assets_dir = output_dir.parent / "_run_assets" / output_dir.name
        idf_path = generate_runperiod_idf(
            template_idf,
            assets_dir,
            start_date=date.fromisoformat(start_date),
            days=days,
        )
    return idf_path, epw_path, price_profile


def _canonical_method(method: str) -> str:
    aliases = {
        "mpc": "mpc_dynamic",
    }
    return aliases.get((method or "agent").lower(), (method or "agent").lower())


def _method_label(method: str) -> str:
    method = _canonical_method(method)
    labels = {
        "agent": "EnergyBridge Agent",
        "human": "Human-in-loop Agent",
        "mpc_dynamic": "MPC-Dynamic baseline",
        "mpc_ep": "MPC-EnergyPlus baseline",
        "rl": "RL baseline",
    }
    return labels.get(method, method or "unknown")


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
             "Defaults to benchmark_results/<YYYY-MM-DD>/<role>_<method>[_Hn]_<city>_<days>days.",
    )
    parser.add_argument(
        "--city", "-c", default="Tianjin",
        choices=["Tianjin", "Beijing", "Shanghai", "Germany"],
        help="Weather city label (default: Tianjin).",
    )
    parser.add_argument(
        "--days", type=int, default=None,
        help="Simulation length in days. Defaults to 3, or 7 for --city Germany.",
    )
    parser.add_argument(
        "--start-date", default="",
        help="RunPeriod start date YYYY-MM-DD. Defaults to 2025-06-01 for Germany; empty keeps template IDF date.",
    )
    parser.add_argument(
        "--price-csv", default="",
        help="Optional day-ahead price CSV. If omitted, price-aware planning is disabled for every city.",
    )
    parser.add_argument(
        "--weather-csv", default="",
        help="Optional real-weather CSV used to generate Germany EPW.",
    )
    parser.add_argument(
        "--epw", default="",
        help="Optional EPW override. Germany defaults to generated DEU_Germany_2025_real.epw.",
    )
    parser.add_argument(
        "--idf", default="",
        help="Optional family IDF template override. With --start-date, a run-specific copy is generated.",
    )
    parser.add_argument(
        "--regenerate-epw", action="store_true",
        help="Regenerate Germany EPW even if it already exists.",
    )
    parser.add_argument(
        "--vpp-start-hour",
        type=float,
        default=18.0,
        help="Daily VPP event start hour-of-day. Default: 18.0.",
    )
    parser.add_argument(
        "--vpp-duration-hours",
        type=float,
        default=1.0,
        help="Daily VPP event duration in hours. Default: 1.0.",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="Print full LLM prompt + raw JSON response for each agent call.",
    )
    parser.add_argument(
        "--human", action="store_true",
        help="Deprecated alias for --user-mode human.",
    )
    parser.add_argument(
        "--user-mode", choices=["roleplay", "human"], default="roleplay",
        help="User type: roleplay LLM evaluator or real human input (default: roleplay).",
    )
    parser.add_argument(
        "--human-name", default="",
        help="Custom human user name used in default output directory prefix, e.g. alice_human_agent_tianjin_3days.",
    )
    parser.add_argument(
        "--method", choices=["agent", "mpc_dynamic", "mpc_ep", "mpc"], default="agent",
        help="Controller method for family_runner (default: agent).",
    )
    parser.add_argument(
        "--mpc-horizon", type=int, default=6,
        help="MPC prediction horizon in 10-minute steps; used by mpc_dynamic/mpc_ep (default: 6).",
    )
    args = parser.parse_args()

    persona = _load_persona_json(args.persona)
    pid     = persona["id"]
    method = _canonical_method(args.method)
    human_mode = args.human or args.user_mode == "human"
    controller_method = method
    result_method = controller_method
    human_name = args.human_name.strip() if human_mode else ""
    mpc_horizon = max(1, int(args.mpc_horizon))
    vpp_start_hour = float(args.vpp_start_hour) % 24.0
    vpp_duration_hours = float(args.vpp_duration_hours)
    if vpp_duration_hours <= 0:
        raise SystemExit("--vpp-duration-hours must be > 0")
    if vpp_start_hour + vpp_duration_hours > 24.0:
        raise SystemExit("VPP windows crossing midnight are not supported yet; choose start+duration <= 24")
    days = _default_days_for_city(args.city, args.days)
    start_date = _default_start_date_for_city(args.city, args.start_date.strip())
    output_dir = (
        Path(args.output) if args.output
        else _prepare_default_output_dir(
            pid, result_method, args.city, days=days, mpc_horizon=mpc_horizon, human_name=human_name
        )
    )
    idf_path, epw_path, price_profile = _prepare_run_assets(args, output_dir, days, start_date)

    print("=" * 70)
    print(f"PERSONA : {pid}")
    if human_mode:
        print(f"USER    : {human_name or 'human'} (human)")
    print(f"CITY    : {args.city}")
    print(f"METHOD  : {result_method}")
    print(f"DAYS    : {days}")
    print(f"START   : {start_date or '(template IDF)'}")
    print(f"VPP     : daily {vpp_start_hour:.2f}h for {vpp_duration_hours:.2f}h")
    print(f"IDF     : {idf_path}")
    print(f"EPW     : {epw_path}")
    print(f"PRICE   : {getattr(price_profile, 'source', '') or 'N/A'}")
    print(f"OUTPUT  : {output_dir}")
    print("=" * 70)

    result = fr.run_family_agent(
        idf_path         = idf_path,
        epw_path         = epw_path,
        user_pref        = persona["llm_prompts"]["system_prompt"],
        appliance_config = persona.get("appliances", {}),
        persona_config   = persona,
        output_dir       = output_dir,
        weather_label    = args.city.lower(),
        verbose          = args.verbose,
        human_mode       = human_mode,
        method           = controller_method,
        mpc_horizon_steps= mpc_horizon,
        sim_days         = days,
        start_date       = start_date or None,
        day_ahead_price_profile = price_profile,
        vpp_start_h      = vpp_start_hour,
        vpp_duration_h   = vpp_duration_hours,
    )
    if human_mode:
        result.user_label = f"{_slug_label(human_name) or 'human'}_human"

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

    def _first_day_action(key: str):
        for decision in day_decisions or []:
            raw = (decision or {}).get("raw_appliance_actions", {}) or {}
            actions = (decision or {}).get("actions", {}) or {}
            if key in raw and raw.get(key) is not None:
                return raw.get(key)
            if key in actions and actions.get(key) is not None:
                return actions.get(key)
        return None

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
        # Show the VPP-trigger command. Later recovery commands belong to the
        # day timeline, but should not overwrite the event strategy display.
        start_h = ta.get(start_k)
        skip    = ta.get(skip_k)
        default_start_h = _first_day_action(start_k)
        default_skip = _first_day_action(skip_k)
        if skip:
            lines.append(f"    ├ {label:<5}: 跳过 (agent指令skip)")
        elif start_h is not None:
            lines.append(f"    ├ {label:<5}: 排程@{_fmt_h(start_h)}")
        elif default_skip:
            lines.append(f"    ├ {label:<5}: 跳过（默认）")
        elif default_start_h is not None:
            lines.append(f"    ├ {label:<5}: 排程@{_fmt_h(default_start_h)}（默认）")
        else:
            lines.append(f"    ├ {label:<5}: 保持原计划（默认）")

    # ── Water heater ──
    if not pd or "water_heater" in pd:
        wh_preheat    = ta.get("water_heater_preheat")
        wh_start      = ta.get("water_heater_preheat_start_h")
        wh_end        = ta.get("water_heater_preheat_end_h")
        wh_temp       = ta.get("water_heater_preheat_temp_c")
        default_wh_preheat = _first_day_action("water_heater_preheat")
        default_wh_start = _first_day_action("water_heater_preheat_start_h")
        default_wh_end = _first_day_action("water_heater_preheat_end_h")
        default_wh_temp = _first_day_action("water_heater_preheat_temp_c")
        if wh_preheat is False:
            wh_str = "关闭预热"
        elif wh_start is not None and wh_end is not None:
            temp_s = f" @ {wh_temp:.0f}°C" if wh_temp else ""
            wh_str = f"预热 {_fmt_h(wh_start)}-{_fmt_h(wh_end)}{temp_s}"
        elif wh_start is not None:
            wh_str = f"预热开始@{_fmt_h(wh_start)}"
        elif default_wh_preheat is False:
            wh_str = "关闭预热（默认）"
        elif default_wh_start is not None and default_wh_end is not None:
            temp_s = f" @ {default_wh_temp:.0f}°C" if default_wh_temp else ""
            wh_str = f"预热 {_fmt_h(default_wh_start)}-{_fmt_h(default_wh_end)}{temp_s}（默认）"
        elif default_wh_start is not None:
            wh_str = f"预热开始@{_fmt_h(default_wh_start)}（默认）"
        else:
            wh_str = "保持预热策略（默认）"
        lines.append(f"    ├ 热水器  : {wh_str}")

    # ── EV ──
    if not pd or "ev" in pd:
        ev_mode  = ta.get("ev_mode")
        ev_start = ta.get("ev_charge_start_h")
        ev_end   = ta.get("ev_charge_end_h")
        default_ev_mode = _first_day_action("ev_mode")
        default_ev_start = _first_day_action("ev_charge_start_h")
        default_ev_end = _first_day_action("ev_charge_end_h")
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
        elif default_ev_mode == "delay":
            ev_str = "delay模式（默认）"
        elif default_ev_mode == "smart":
            ev_str = "smart模式（默认）"
        elif default_ev_mode == "normal":
            ev_str = "normal模式（默认）"
        elif default_ev_start is not None and default_ev_end is not None:
            ev_str = f"充电窗口 {_fmt_h(default_ev_start)}-{_fmt_h(default_ev_end)}（默认）"
        elif default_ev_start is not None:
            ev_str = f"充电开始@{_fmt_h(default_ev_start)}（默认）"
        else:
            ev_str = "smart模式（默认）"
        lines.append(f"    └ EV      : {ev_str}")

    # Fix tree connector: last line should use └ instead of ├
    for i in range(len(lines)-1, -1, -1):
        if lines[i].strip().startswith('├'):
            lines[i] = lines[i].replace('├', '└', 1)
            break
    return lines


def _vpp_ratio_str(result) -> str:
    """Compute aggregate VPP demand achievement."""
    events = result.vpp_event_log
    shed_targets = [e.get("demand_target_shed_kwh", 0.0) for e in events if e.get("demand_target_shed_kwh")]
    actual_sheds = [e.get("actual_shed_kwh", 0.0) for e in events if e.get("demand_target_shed_kwh")]
    if shed_targets and sum(shed_targets) > 0:
        total_a = sum(actual_sheds)
        total_t = sum(shed_targets)
        ratio = total_a / total_t
        per_ev = "  ".join(
            f"VPP{i+1}:{a:.3f}/{t:.3f}" for i, (a, t) in enumerate(zip(actual_sheds, shed_targets))
        )
        ok = "✓达标" if ratio >= 1.0 else "✗未达标"
        return f"削减{total_a:.3f}/{total_t:.3f}kWh = {ratio:.2f} {ok}  [{per_ev}]"
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


def _fmt_num(value, digits: int = 3, suffix: str = "") -> str:
    try:
        number = float(value)
        if number != number:
            return "NaN"
        return f"{number:.{digits}f}{suffix}"
    except (TypeError, ValueError):
        return "NaN"


def _fmt_duration_h(hours: float) -> str:
    try:
        value = float(hours)
    except (TypeError, ValueError):
        value = 1.0
    if abs(value - round(value)) < 1e-6:
        return f"{int(round(value))}h"
    return f"{value:.2f}h"


def _write_run_summary(result, persona: dict, city: str, output_dir: Path) -> Path:
    """Write a human-readable run_summary.txt (no LLM, pure algorithm)."""
    from datetime import datetime
    d = result.as_dict()
    appl = result.appliance_results   # dict: device -> list of day dicts
    evts = result.vpp_event_log       # list of scored VPP event dicts
    sim_days = int(d.get("sim_days") or 3)
    price_metrics = d.get("day_ahead_price_metrics") or {}

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
        f"  用户       : {d.get('user_label') or persona.get('id', '?')}",
        f"  名称       : {persona.get('name', '')}",
        f"  方法       : {_method_label(d.get('method', ''))}  ({d.get('method', 'unknown')})",
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
    vpp_defs = sorted(
        (
            {
                "id": e.get("id", f"vpp{i + 1}"),
                "trigger_h": float(e.get("trigger_h", i * 24.0 + 18.0)),
                "end_h": float(e.get("end_h", i * 24.0 + 19.0)),
                "day": int(e.get("day", i + 1)),
            }
            for i, e in enumerate(evts)
        ),
        key=lambda item: item["trigger_h"],
    )
    if not vpp_defs:
        vpp_defs = [
            {"id": f"vpp{i + 1}", "trigger_h": i * 24.0 + 18.0, "end_h": i * 24.0 + 19.0, "day": i + 1}
            for i in range(sim_days)
        ]
    evt_by_id = {e["id"]: e for e in evts}
    for vdef in vpp_defs:
        vid = vdef["id"]
        ev_num = int(vid[3:])
        event_duration_h = max(1e-6, float(vdef.get("end_h", 0.0)) - float(vdef.get("trigger_h", 0.0)))
        event_duration_text = _fmt_duration_h(event_duration_h)
        e = evt_by_id.get(vid, {})
        score_str = f"{e['score']}/5 ({e.get('label','?')})" if e.get("score") is not None else "未评分"
        sp_str = f"{e['setpoint']:.1f}°C" if e.get("setpoint") else "N/A"
        # VPP demand: shed target from capacity quantification, plus the
        # equivalent consumption cap used by controller objectives.
        demand_kw = e.get("demand_target_kw")
        demand_shed_kwh = e.get("demand_target_shed_kwh")
        actual_shed = e.get("actual_shed_kwh")
        demand_t = e.get("demand_target_kwh")
        actual_k = e.get("actual_kwh")
        if demand_kw and demand_shed_kwh and actual_shed is not None:
            ratio = actual_shed / demand_shed_kwh if demand_shed_kwh > 0 else 0
            ok = "✓达标" if ratio >= 1.0 else "✗未达标"
            cap_part = (
                f"  等价用电上限≤{demand_t:.2f}kWh  实际用电{actual_k:.3f}kWh"
                if demand_t and actual_k is not None else ""
            )
            demand_str = (
                f"目标削减≥{demand_kw:.3f}kW ({event_duration_text}={demand_shed_kwh:.3f}kWh)  "
                f"实际削减{actual_shed:.3f}kWh  比率{ratio:.2f} {ok}{cap_part}"
            )
        elif demand_t and actual_k is not None:
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
        capacity = e.get("capacity_assessment", {}).get("assessment", {})
        if capacity:
            constraints = ", ".join(capacity.get("main_constraints", [])) or "无"
            capacity_str = (
                f"可承诺{capacity.get('committable_kw', 0):.3f}kW  "
                f"建议上报{capacity.get('recommended_bid_kw', 0):.3f}kW  "
                f"成功率{capacity.get('success_probability', 0)*100:.1f}%  "
                f"约束: {constraints}"
            )
        else:
            capacity_str = "(capacity assessment not run)"
        capacity_window = e.get("capacity_window_summary", {})
        if capacity_window:
            capacity_window_str = (
                f"平均可承诺{capacity_window.get('avg_committable_kw', 0):.3f}kW  "
                f"最小持续{capacity_window.get('firm_min_committable_kw', 0):.3f}kW  "
                f"可承诺电量{capacity_window.get('committable_energy_kwh', 0):.3f}kWh  "
                f"建议上报电量{capacity_window.get('recommended_bid_energy_kwh', 0):.3f}kWh"
            )
        else:
            capacity_window_str = "(capacity window assessment not run)"
        total_q90 = e.get("total_quantification_90", {})
        if total_q90.get("status") == "computed":
            total_q90_str = (
                f"avg_expected_shed_kw={total_q90.get('avg_expected_shed_kw', 0):.3f}kW  "
                f"avg_reported_capacity_90_kw={total_q90.get('avg_reported_capacity_90_kw', 0):.3f}kW  "
                f"vpp_target_capacity_120_kw={total_q90.get('vpp_target_capacity_120_kw', 0):.3f}kW  "
                f"firm_min_capacity_90_kw={total_q90.get('firm_min_capacity_90_kw', 0):.3f}kW  "
                f"expected_shed_energy_kwh={total_q90.get('expected_shed_energy_kwh', 0):.3f}kWh  "
                f"reported_shed_90_energy_kwh={total_q90.get('reported_shed_90_energy_kwh', 0):.3f}kWh  "
                f"vpp_target_kwh={total_q90.get('vpp_target_kwh', 0):.3f}kWh"
            )
        else:
            total_q90_str = (
                "N/A（未运行Total_Quantification；"
                + total_q90.get("reason", "缺少A3 conformal P_base输入") + "）"
            )
        trigger_actions = e.get("vpp_trigger_actions", {})
        day_decisions   = e.get("day_decisions", [])
        _pdev = {k for k, v in e.get("appliance_summary", {}).items() if v.get("present")}
        strat_lines = _fmt_strategy(sp_str, trigger_actions, day_decisions, present_devices=_pdev)
        lines += [
            f"  [事件{ev_num}] Day{vdef['day']} {_fmt_h(vdef['trigger_h'])}-{_fmt_h(vdef['end_h'])}"
            f"  目标：需求侧削峰{event_duration_text}",
            f"    执行策略 ↓ (全天{len(day_decisions)}次控制决策):",
        ] + strat_lines + [
            f"    VPP需求    : {demand_str}",
            f"    触发时容量 : {capacity_str}",
            f"    窗口容量   : {capacity_window_str}",
            f"    90%可信容量: {total_q90_str}",
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
    per_day_shift = d.get("task_shift_success_per_day", [])
    if per_day:
        day_strs = "  ".join(f"Day{i+1}:{int(v*100)}%" for i, v in enumerate(per_day))
        lines.append(f"  {'服务完成率(逐天)':<14}: {day_strs}")
    if per_day_shift:
        day_strs_shift = "  ".join(f"Day{i+1}:{int(v*100)}%" for i, v in enumerate(per_day_shift))
        lines.append(f"  {'完成后避峰率(逐天)':<14}: {day_strs_shift}")

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
    price_day_lines = []
    for item in price_metrics.get("per_day", []) or []:
        price_day_lines.append(
            f"Day{item.get('day', '?')}:{_fmt_num(item.get('cost_eur'), 4, ' EUR')}"
            f"@{_fmt_num(item.get('weighted_price_eur_per_kwh'), 5, ' EUR/kWh')}"
        )
    price_day_str = "  ".join(price_day_lines) if price_day_lines else "NaN"
    total_vpp_duration_h = sum(
        max(0.0, float(v.get("end_h", 0.0)) - float(v.get("trigger_h", 0.0)))
        for v in vpp_defs
    )
    if vpp_defs and total_vpp_duration_h > 0:
        avg_vpp_duration_text = _fmt_duration_h(total_vpp_duration_h / len(vpp_defs))
        vpp_duration_summary = f"{len(vpp_defs)}个事件×{avg_vpp_duration_text}合计"
    else:
        vpp_duration_summary = "无VPP事件"
    lines += [
        "",
        "─" * 62,
        "  关键 Metrics 汇总",
        "─" * 62,
        f"  ▸ VPP削峰",
        f"      VPP时段用电量: {d.get('vpp_window_energy_kwh', 0):.3f} kWh ({vpp_duration_summary})",
        ("      需求达成比率 : " + _vpp_ratio_str(result)),
        f"      服务完成率   : {d.get('appliance_task_completion_rate', 1.0)*100:.0f}%"
        f"  (分母=在户可控电器；热水器=浴前就绪，EV=SOC达标)",
        f"      完成后避峰率 : {d.get('appliance_vpp_avoidance_rate', 0)*100:.0f}%"
        f"  (分母=已完成服务的可控电器；分子=未在VPP运行)",
        f"  ▸ 次日电价",
        f"      加权电费     : {_fmt_num(price_metrics.get('total_cost_eur'), 4, ' EUR')}",
        f"      加权均价     : {_fmt_num(price_metrics.get('weighted_price_eur_per_kwh'), 5, ' EUR/kWh')}",
        f"      逐日         : {price_day_str}",
        f"  ▸ 用电量",
        f"      总能耗       : {d.get('energy_kwh_total', 0):.2f} kWh ({sim_days}天)",
        f"      日均          : {d.get('energy_kwh_per_day', 0):.2f} kWh/天",
        f"  ▸ 用户舒适度",
        f"      满意度均分   : {avg_score_str}",
        f"      逐事件       : {score_per_event}",
        f"      区域均温     : {d.get('mean_temp_c', 0):.2f} °C",
        f"      PMV达标率    : {d.get('pmv_ok_fraction', 0)*100:.1f}%",
        f"      舒适区达标率 : {d.get('comfort_ok_fraction', 0)*100:.1f}% (23-26°C)",
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
