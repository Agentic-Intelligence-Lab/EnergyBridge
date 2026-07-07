#!/usr/bin/env python3
"""Run the family home benchmark for a single persona JSON file.

Usage
-----
  python3 run_persona_json.py <persona_id_or_json_path> [--output <dir>] [--city <Tianjin|Beijing|Shanghai>] [--method <EnergyBridge|mpc_dynamic|rule_milp>]

Examples
--------
  python3 run_persona_json.py atom_comfort_sensitive
  python3 run_persona_json.py ../../energybridge/roleplay/personas/atom_comfort_sensitive.json
  python3 run_persona_json.py basic_role_f_commuter_ev_optimizer --city Shanghai --output /tmp/out
  python3 run_persona_json.py atom_comfort_sensitive --city Tianjin --method mpc_dynamic

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
    DEFAULT_TIANJIN_TOU_PRICE_CSV,
    generate_epw_from_openmeteo_csv,
    generate_runperiod_idf,
    maybe_load_price_profile,
)
from energybridge.data.vpp_events import (
    describe_vpp_events,
    load_vpp_events_config,
    make_daily_vpp_events,
)
from energybridge.roleplay.calendar import attach_calendar, hourly_occupancy_from_persona
from experiments.benchmark.strategy_explanations import (
    collect_strategy_explanation_records,
    format_strategy_explanation_lines,
    write_strategy_explanation_artifacts,
)

PERSONA_DIR = _PROJECT_ROOT / "energybridge" / "roleplay" / "personas"
FAMILY_MODEL_DIR = _PROJECT_ROOT / "experiments" / "models" / "family_home"
DEFAULT_GERMANY_FAMILY_IDF = FAMILY_MODEL_DIR / "berlin_family_geg_final.idf"
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
ENERGYBRIDGE_METHOD_ID = "EnergyBridge"


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
    if method == "mpc_dynamic":
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


def _idf_field(value: str | float | int, comment: str, *, last: bool = False) -> str:
    sep = ";" if last else ","
    return f"    {value}{sep:<24}!- {comment}"


def _append_persona_occupancy_objects(lines: list[str], hourly: list[list[float]]) -> list[str]:
    schedule_lines: list[str] = [
        "",
        "!- Persona calendar occupancy injected by run_persona_json.py",
    ]
    day_names: list[str] = []
    for idx, values in enumerate(hourly[:7], start=1):
        name = f"PersonaOccupancyDay{idx}"
        day_names.append(name)
        schedule_lines.extend([
            "  Schedule:Day:Hourly,",
            _idf_field(name, "Name"),
            _idf_field("Fraction", "Schedule Type Limits Name"),
        ])
        for hour, value in enumerate(values[:24], start=1):
            schedule_lines.append(_idf_field(f"{float(value):.4f}", f"Hour {hour}", last=hour == 24))
        schedule_lines.append("")
    while len(day_names) < 7:
        day_names.append(day_names[-1] if day_names else "PersonaOccupancyDay1")

    week_fields = [
        ("PersonaOccupancyWeek", "Name"),
        (day_names[0], "Sunday Schedule:Day Name"),
        (day_names[1], "Monday Schedule:Day Name"),
        (day_names[2], "Tuesday Schedule:Day Name"),
        (day_names[3], "Wednesday Schedule:Day Name"),
        (day_names[4], "Thursday Schedule:Day Name"),
        (day_names[5], "Friday Schedule:Day Name"),
        (day_names[6], "Saturday Schedule:Day Name"),
        (day_names[0], "Holiday Schedule:Day Name"),
        (day_names[0], "SummerDesignDay Schedule:Day Name"),
        (day_names[0], "WinterDesignDay Schedule:Day Name"),
        (day_names[0], "CustomDay1 Schedule:Day Name"),
        (day_names[0], "CustomDay2 Schedule:Day Name"),
    ]
    schedule_lines.append("  Schedule:Week:Daily,")
    for idx, (value, comment) in enumerate(week_fields):
        schedule_lines.append(_idf_field(value, comment, last=idx == len(week_fields) - 1))
    schedule_lines.extend([
        "",
        "  Schedule:Year,",
        _idf_field("PersonaOccupancy", "Name"),
        _idf_field("Fraction", "Schedule Type Limits Name"),
        _idf_field("PersonaOccupancyWeek", "Schedule:Week Name 1"),
        _idf_field("1", "Start Month 1"),
        _idf_field("1", "Start Day 1"),
        _idf_field("12", "End Month 1"),
        _idf_field("31", "End Day 1", last=True),
        "",
        "  Output:Variable,",
        _idf_field("living_unit1", "Key Value"),
        _idf_field("Zone People Occupant Count", "Variable Name"),
        _idf_field("Timestep", "Reporting Frequency", last=True),
        "",
    ])
    return lines + schedule_lines


def _replace_people_occupancy_schedule(lines: list[str]) -> list[str]:
    out = list(lines)
    for idx, line in enumerate(out):
        if line.strip().lower() != "people,":
            continue
        end = idx + 1
        while end < len(out):
            if ";" in out[end]:
                end += 1
                break
            end += 1
        end = min(end, len(out), idx + 40)
        for j in range(idx + 1, end):
            if "Number of People Schedule Name" in out[j]:
                out[j] = "    PersonaOccupancy,        !- Number of People Schedule Name"
                return out
        field_lines = [
            j
            for j in range(idx + 1, end)
            if out[j].strip() and not out[j].lstrip().startswith("!")
        ]
        if len(field_lines) >= 3:
            schedule_idx = field_lines[2]
            delimiter = ";" if out[schedule_idx].rstrip().endswith(";") else ","
            out[schedule_idx] = f"    PersonaOccupancy{delimiter}        !- Number of People Schedule Name"
            return out
    raise ValueError("Could not find People object Number of People Schedule Name in IDF")


def _enable_hvac_availability_control(lines: list[str]) -> list[str]:
    """Add a controllable HVAC availability schedule and connect the air loop to it."""
    out = list(lines)
    if not any("HVAC_Availability_Control" in line for line in out):
        out.extend([
            "",
            "!- Controllable HVAC availability injected by run_persona_json.py",
            "  Schedule:Constant,",
            _idf_field("HVAC_Availability_Control", "Name"),
            _idf_field("On/Off", "Schedule Type Limits Name"),
            _idf_field("1", "Hourly Value", last=True),
            "",
        ])
    for idx, line in enumerate(out):
        if line.strip().lower() != "availabilitymanager:scheduled,":
            continue
        end = idx + 1
        while end < len(out):
            if ";" in out[end]:
                end += 1
                break
            end += 1
        end = min(end, len(out), idx + 12)
        is_system_availability = any("System availability" in out[j] for j in range(idx + 1, end))
        if not is_system_availability:
            continue
        for j in range(idx + 1, end):
            if "Schedule Name" in out[j]:
                out[j] = "    HVAC_Availability_Control;  !- Schedule Name"
                return out
        field_lines = [
            j
            for j in range(idx + 1, end)
            if out[j].strip() and not out[j].lstrip().startswith("!")
        ]
        if len(field_lines) >= 2:
            schedule_idx = field_lines[1]
            delimiter = ";" if out[schedule_idx].rstrip().endswith(";") else ","
            out[schedule_idx] = f"    HVAC_Availability_Control{delimiter}  !- Schedule Name"
            return out
    raise ValueError("Could not connect System availability manager to HVAC_Availability_Control")


def _write_persona_occupancy_idf(idf_path: Path, persona: dict | None, days: int) -> bool:
    hourly = hourly_occupancy_from_persona(persona or {}, days)
    if not hourly:
        return False
    idf_path = Path(idf_path)
    lines = idf_path.read_text(encoding="utf-8").splitlines()
    lines = [line for line in lines if "Persona calendar occupancy injected by run_persona_json.py" not in line]
    lines = [line for line in lines if "Controllable HVAC availability injected by run_persona_json.py" not in line]
    lines = _replace_people_occupancy_schedule(lines)
    lines = _enable_hvac_availability_control(lines)
    if not any("PersonaOccupancyDay1" in line for line in lines):
        lines = _append_persona_occupancy_objects(lines, hourly)
    idf_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def _prepare_run_assets(
    args: argparse.Namespace,
    output_dir: Path,
    days: int,
    start_date: str,
    persona: dict | None = None,
):
    city_key = args.city.lower()
    epw_path = Path(args.epw) if args.epw else DEFAULT_EPW_BY_CITY[city_key]
    price_profile = None
    if city_key == "germany":
        weather_csv = Path(args.weather_csv) if args.weather_csv else DEFAULT_GERMANY_WEATHER_CSV
        if not epw_path.exists() or args.regenerate_epw:
            print(f"[Germany] generating EPW from {weather_csv} -> {epw_path}")
            generate_epw_from_openmeteo_csv(weather_csv, epw_path)
    price_csv = Path(args.price_csv) if args.price_csv else None
    if price_csv is None and city_key == "tianjin" and DEFAULT_TIANJIN_TOU_PRICE_CSV.exists():
        price_csv = DEFAULT_TIANJIN_TOU_PRICE_CSV
    if price_csv:
        price_profile = maybe_load_price_profile(
            price_csv,
            standard_timezone_hours=STANDARD_TIMEZONE_BY_CITY.get(city_key),
        )

    if args.idf:
        template_idf = Path(args.idf)
    elif city_key == "germany":
        template_idf = DEFAULT_GERMANY_FAMILY_IDF
    else:
        template_idf = DEFAULT_FAMILY_IDF_BY_DAYS.get(
            days,
            DEFAULT_FAMILY_IDF_BY_DAYS[7] if days > 3 else DEFAULT_FAMILY_IDF_BY_DAYS[3],
        )
    idf_path = template_idf
    occupancy_profile = hourly_occupancy_from_persona(persona or {}, days)
    template_days = set(DEFAULT_FAMILY_IDF_BY_DAYS)
    needs_custom_runperiod = bool(start_date) or days not in template_days
    if needs_custom_runperiod or occupancy_profile:
        assets_dir = output_dir.parent / "_run_assets" / output_dir.name
        if needs_custom_runperiod:
            run_start = date.fromisoformat(start_date) if start_date else date(2007, 7, 1)
            idf_path = generate_runperiod_idf(
                template_idf,
                assets_dir,
                start_date=run_start,
                days=days,
            )
        else:
            assets_dir.mkdir(parents=True, exist_ok=True)
            idf_path = assets_dir / f"{template_idf.stem}_persona_{days}days.idf"
            shutil.copy2(template_idf, idf_path)
        if occupancy_profile:
            _write_persona_occupancy_idf(idf_path, persona, days)
            print(f"[Occupancy] persona calendar schedule injected -> {idf_path}")
    return idf_path, epw_path, price_profile


def _canonical_method(method: str) -> str:
    raw = (method or ENERGYBRIDGE_METHOD_ID).strip()
    key = raw.lower()
    aliases = {
        "agent": ENERGYBRIDGE_METHOD_ID,
        "energybridge": ENERGYBRIDGE_METHOD_ID,
        "mpc": "mpc_dynamic",
        "rl": "rl_ppo_pref_v2",
        "rl_ppo": "rl_ppo_pref_v2",
        "rl_ppo_pref_v2": "rl_ppo_pref_v2",
        "rl_pref_v2": "rl_ppo_pref_v2",
        "rule_milp": "rule_milp",
        "rule+milp": "rule_milp",
        "pmv_milp": "rule_milp",
        "eb_rule_milp": ENERGYBRIDGE_METHOD_ID,
        "eb+rule+milp": ENERGYBRIDGE_METHOD_ID,
        "energybridge_rule_milp": ENERGYBRIDGE_METHOD_ID,
        "agent_milp": ENERGYBRIDGE_METHOD_ID,
        "agent+milp": ENERGYBRIDGE_METHOD_ID,
        "no_dr": "no_dr",
        "none": "no_dr",
        "baseline": "no_dr",
        "hema_agent": "hema_agent",
    }
    return aliases.get(key, key)


def _controller_method(method: str) -> str:
    method = _canonical_method(method)
    return "agent" if method == ENERGYBRIDGE_METHOD_ID else method


def _method_label(method: str) -> str:
    method = _canonical_method(method)
    labels = {
        ENERGYBRIDGE_METHOD_ID: "EnergyBridge",
        "human": "Human-in-loop Agent",
        "mpc_dynamic": "MPC-Dynamic baseline",
        "rl_ppo_pref_v2": "RL PPO Pref-v2",
        "rule_milp": "Rule+MILP oracle baseline",
        "no_dr": "No-DR counterfactual",
        "hema_agent": "HEMA Control Agent",
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
        "--vpp-events-json",
        default="",
        help="Optional JSON file defining VPP events. Overrides the daily start/duration schedule.",
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
        help="Custom human user name used in default output directory prefix, e.g. alice_human_EnergyBridge_tianjin_3days.",
    )
    parser.add_argument(
        "--method",
        choices=[ENERGYBRIDGE_METHOD_ID, "agent", "mpc_dynamic", "mpc", "rl", "rl_ppo", "rl_ppo_pref_v2", "rl_pref_v2", "rule_milp", "rule+milp", "pmv_milp", "no_dr", "none", "baseline", "hema_agent"],
        default=ENERGYBRIDGE_METHOD_ID,
        help="Controller method. Use EnergyBridge for our agent; 'agent' is kept as a deprecated alias.",
    )
    parser.add_argument(
        "--mpc-horizon", type=int, default=6,
        help="MPC prediction horizon in 10-minute steps; used by mpc_dynamic (default: 6).",
    )
    args = parser.parse_args()

    persona = _load_persona_json(args.persona)
    pid     = persona["id"]
    method = _canonical_method(args.method)
    human_mode = args.human or args.user_mode == "human"
    controller_method = _controller_method(method)
    result_method = method
    human_name = args.human_name.strip() if human_mode else ""
    mpc_horizon = max(1, int(args.mpc_horizon))
    vpp_start_hour = float(args.vpp_start_hour) % 24.0
    vpp_duration_hours = float(args.vpp_duration_hours)
    if vpp_duration_hours <= 0:
        raise SystemExit("--vpp-duration-hours must be > 0")
    days = _default_days_for_city(args.city, args.days)
    start_date = _default_start_date_for_city(args.city, args.start_date.strip())
    if args.vpp_events_json:
        vpp_events = load_vpp_events_config(
            args.vpp_events_json,
            sim_days=days,
            default_start_h=vpp_start_hour,
            default_duration_h=vpp_duration_hours,
        )
        vpp_schedule_source = str(Path(args.vpp_events_json))
    else:
        if vpp_start_hour + vpp_duration_hours > 24.0:
            raise SystemExit("VPP windows crossing midnight are not supported yet; choose start+duration <= 24")
        vpp_events = make_daily_vpp_events(days, start_h=vpp_start_hour, duration_h=vpp_duration_hours)
        vpp_schedule_source = "daily_default"
    output_dir = (
        Path(args.output) if args.output
        else _prepare_default_output_dir(
            pid, result_method, args.city, days=days, mpc_horizon=mpc_horizon, human_name=human_name
        )
    )
    idf_path, epw_path, price_profile = _prepare_run_assets(args, output_dir, days, start_date, persona)

    print("=" * 70)
    print(f"PERSONA : {pid}")
    if human_mode:
        print(f"USER    : {human_name or 'human'} (human)")
    print(f"CITY    : {args.city}")
    print(f"METHOD  : {result_method}")
    print(f"DAYS    : {days}")
    print(f"START   : {start_date or '(template IDF)'}")
    print(f"VPP     : {describe_vpp_events(vpp_events)}")
    print(f"VPP SRC : {vpp_schedule_source}")
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
        vpp_events_config= vpp_events,
        vpp_schedule_source = vpp_schedule_source,
    )
    result.method = result_method
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

    has_strategy_explanations = any(
        isinstance((event or {}).get("strategy_explanation"), dict)
        and bool((event or {}).get("strategy_explanation"))
        for event in (result.vpp_event_log or [])
    )
    if has_strategy_explanations:
        explanation_records = collect_strategy_explanation_records(result, persona, args.city)
        explanation_paths = write_strategy_explanation_artifacts(explanation_records, output_dir)
        for label, path in explanation_paths.items():
            print(f"[Saved] strategy explanations {label:<8} → {path}")

    # ── Call analyze_eplus_run.py --report for EP-level MD ────────────


def _fmt_h(h) -> str:
    """Float hour → 'HH:MM' string."""
    if h is None:
        return "?"
    hour = float(h) % 24.0
    hh = int(hour)
    mm = int(round((hour - hh) * 60))
    if mm >= 60:
        hh = (hh + 1) % 24
        mm = 0
    return f"{hh:02d}:{mm:02d}"


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
    day_sps = []
    for decision in day_decisions:
        try:
            policy_sp = float(decision.get("sp", 0.0))
        except (TypeError, ValueError):
            policy_sp = 0.0
        try:
            effective_sp = float(decision.get("effective_setpoint", policy_sp))
        except (TypeError, ValueError):
            effective_sp = policy_sp
        mode = str(decision.get("ac_mode", "on"))
        hvac_avail = decision.get("hvac_availability")
        if mode.startswith("off") or hvac_avail == 0.0:
            day_sps.append(f"off@{_fmt_h(decision.get('h'))} ({mode}, policy {policy_sp:.1f}°C)")
        elif abs(effective_sp - policy_sp) > 1e-6 or mode != "on":
            day_sps.append(
                f"→{effective_sp:.1f}°C@{_fmt_h(decision.get('h'))}"
                f" ({mode}, policy {policy_sp:.1f}°C)"
            )
        else:
            day_sps.append(f"→{policy_sp:.1f}°C@{_fmt_h(decision.get('h'))}")
    ac_line = f"    ├ AC       : {sp_str} (VPP trigger)  daily adjustments: {', '.join(day_sps) if day_sps else 'single decision'}"

    lines = [ac_line]

    # ── Shiftable appliances ──
    pd = present_devices or set()
    for dev, label in [("washer", "washer"), ("dishwasher", "dishwasher"), ("dryer", "dryer")]:
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
            lines.append(f"    ├ {label:<10}: skipped (agent skip command)")
        elif start_h is not None:
            lines.append(f"    ├ {label:<10}: scheduled@{_fmt_h(start_h)}")
        elif default_skip:
            lines.append(f"    ├ {label:<10}: skipped (emitted earlier today)")
        elif default_start_h is not None:
            lines.append(f"    ├ {label:<10}: scheduled@{_fmt_h(default_start_h)} (emitted earlier today)")
        else:
            lines.append(f"    ├ {label:<10}: no emitted policy action")

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
            wh_str = "preheat off"
        elif wh_start is not None and wh_end is not None:
            temp_s = f" @ {wh_temp:.0f}°C" if wh_temp else ""
            wh_str = f"preheat {_fmt_h(wh_start)}-{_fmt_h(wh_end)}{temp_s}"
        elif wh_start is not None:
            wh_str = f"preheat starts@{_fmt_h(wh_start)}"
        elif default_wh_preheat is False:
            wh_str = "preheat off (emitted earlier today)"
        elif default_wh_start is not None and default_wh_end is not None:
            temp_s = f" @ {default_wh_temp:.0f}°C" if default_wh_temp else ""
            wh_str = f"preheat {_fmt_h(default_wh_start)}-{_fmt_h(default_wh_end)}{temp_s} (emitted earlier today)"
        elif default_wh_start is not None:
            wh_str = f"preheat starts@{_fmt_h(default_wh_start)} (emitted earlier today)"
        else:
            wh_str = "no emitted policy action"
        lines.append(f"    ├ water_heater: {wh_str}")

    # ── EV ──
    if not pd or "ev" in pd:
        ev_mode  = ta.get("ev_mode")
        ev_start = ta.get("ev_charge_start_h")
        ev_end   = ta.get("ev_charge_end_h")
        default_ev_mode = _first_day_action("ev_mode")
        default_ev_start = _first_day_action("ev_charge_start_h")
        default_ev_end = _first_day_action("ev_charge_end_h")
        if ev_start is not None and ev_end is not None:
            ev_str = f"charging window {_fmt_h(ev_start)}-{_fmt_h(ev_end)}"
        elif ev_start is not None:
            ev_str = f"charge starts@{_fmt_h(ev_start)}"
        elif ev_mode in {"delay", "smart", "normal"}:
            ev_str = f"{ev_mode} mode only (missing charge window)"
        elif default_ev_start is not None and default_ev_end is not None:
            ev_str = f"charging window {_fmt_h(default_ev_start)}-{_fmt_h(default_ev_end)} (emitted earlier today)"
        elif default_ev_start is not None:
            ev_str = f"charge starts@{_fmt_h(default_ev_start)} (emitted earlier today)"
        elif default_ev_mode in {"delay", "smart", "normal"}:
            ev_str = f"{default_ev_mode} mode only (missing charge window, emitted earlier today)"
        else:
            ev_str = "no emitted policy action"
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
    shed_events = [e for e in events if e.get("demand_target_shed_kwh")]
    shed_targets = []
    for e in shed_events:
        try:
            shed_targets.append(float(e.get("demand_target_shed_kwh", 0.0) or 0.0))
        except (TypeError, ValueError):
            shed_targets.append(0.0)
    actual_sheds = []
    for e in shed_events:
        if e.get("actual_shed_kwh") is None:
            continue
        try:
            actual_sheds.append(float(e.get("actual_shed_kwh")))
        except (TypeError, ValueError):
            continue
    if shed_targets and sum(shed_targets) > 0:
        total_t = sum(shed_targets)
        if len(actual_sheds) == len(shed_events):
            total_a = sum(actual_sheds)
            ratio = total_a / total_t
            per_ev = "  ".join(
                f"VPP{i+1}:{a:.3f}/{t:.3f}" for i, (a, t) in enumerate(zip(actual_sheds, shed_targets))
            )
            ok = "met" if ratio >= 1.0 else "not met"
            return f"shed {total_a:.3f}/{total_t:.3f}kWh = {ratio:.2f} {ok}  [{per_ev}]"
        total_actual = 0.0
        for e in shed_events:
            try:
                total_actual += float(e.get("actual_kwh", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
        return (
            f"shed target {total_t:.3f}kWh; actual shed unavailable "
            f"(needs same-run no-DR counterfactual); actual VPP energy {total_actual:.3f}kWh"
        )
    actuals = [e.get("actual_kwh", 0.0) for e in events if e.get("demand_target_kwh")]
    targets = [e.get("demand_target_kwh", 0.0) for e in events if e.get("demand_target_kwh")]
    if not targets or sum(targets) == 0:
        return "N/A (no demand targets)"
    total_a = sum(actuals)
    total_t = sum(targets)
    ratio = total_a / total_t
    per_ev = "  ".join(f"VPP{i+1}:{a:.3f}/{t:.2f}" for i, (a, t) in enumerate(zip(actuals, targets)))
    ok = "met" if ratio <= 1.0 else "exceeded"
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
    ep_ok = "success" if d.get("exit_code") == 0 else f"failed (exit={d.get('exit_code')})"

    lines = [
        "=" * 62,
        "  EnergyBridge Run Summary  (run_summary.txt)",
        "=" * 62,
        f"  Persona    : {persona.get('id', '?')}",
        f"  User       : {d.get('user_label') or persona.get('id', '?')}",
        f"  Name       : {persona.get('name', '')}",
        f"  Method     : {_method_label(d.get('method', ''))}  ({d.get('method', 'unknown')})",
        f"  City       : {city}",
        f"  VPP schedule: {d.get('vpp_schedule_source') or 'daily_default'}",
        f"  Generated  : {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"  Output dir : {output_dir}",
        "",
    ]

    # ── Header: appliance profile ─────────────────────────────────────
    _appl_names = {"washer": "washer", "dishwasher": "dishwasher", "dryer": "dryer",
                   "water_heater": "water_heater", "ev": "EV charger"}
    _first_summ = evts[0].get("appliance_summary", {}) if evts else {}
    _present = [_appl_names.get(k, k) for k, v in _first_summ.items() if v.get("present")]
    _absent  = [_appl_names.get(k, k) for k, v in _first_summ.items() if not v.get("present")]
    _present_str = " | ".join(_present) if _present else "(none)"
    _absent_str  = " | ".join(_absent)  if _absent  else "(none)"
    lines += [
        f"  Appliances : present: {_present_str}   not configured: {_absent_str}",
        "",
    ]
    # ── Section 1: VPP event details ─────────────────────────────────
    lines += ["─" * 62, "  VPP Event Details (grid event -> strategy -> outcome)", "─" * 62]
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
    for ev_num, vdef in enumerate(vpp_defs, start=1):
        vid = vdef["id"]
        event_duration_h = max(1e-6, float(vdef.get("end_h", 0.0)) - float(vdef.get("trigger_h", 0.0)))
        event_duration_text = _fmt_duration_h(event_duration_h)
        e = evt_by_id.get(vid, {})
        score_str = f"{e['score']}/5 ({e.get('label','?')})" if e.get("score") is not None else "not scored"
        sp_str = f"{e['setpoint']:.1f}°C" if e.get("setpoint") else "N/A"
        # VPP demand: shed target from capacity quantification, plus the
        # equivalent consumption cap used by controller objectives.
        demand_kw = e.get("demand_target_kw")
        demand_shed_kwh = e.get("demand_target_shed_kwh")
        actual_shed = e.get("actual_shed_kwh")
        demand_t = e.get("demand_target_kwh")
        actual_k = e.get("actual_kwh")
        if demand_kw and demand_shed_kwh:
            if actual_shed is not None:
                ratio = actual_shed / demand_shed_kwh if demand_shed_kwh > 0 else 0
                ok = "met" if ratio >= 1.0 else "not met"
                cap_part = (
                    f"  equivalent consumption cap<={demand_t:.2f}kWh  actual consumption={actual_k:.3f}kWh"
                    if demand_t and actual_k is not None else ""
                )
                demand_str = (
                    f"shed target>={demand_kw:.3f}kW ({event_duration_text}={demand_shed_kwh:.3f}kWh)  "
                    f"actual shed={actual_shed:.3f}kWh  ratio={ratio:.2f} {ok}{cap_part}"
                )
            else:
                diag = e.get("reference_pbase_minus_actual_kwh")
                diag_part = (
                    f"  reference Pbase-minus-actual diagnostic={diag:.3f}kWh"
                    if isinstance(diag, (int, float)) else ""
                )
                actual_part = (
                    f"  actual VPP energy={actual_k:.3f}kWh"
                    if actual_k is not None else ""
                )
                demand_str = (
                    f"shed target>={demand_kw:.3f}kW ({event_duration_text}={demand_shed_kwh:.3f}kWh)  "
                    f"actual shed unavailable without same-run no-DR counterfactual.{actual_part}{diag_part}"
                )
        elif demand_t and actual_k is not None:
            ratio = actual_k / demand_t if demand_t > 0 else 0
            ok = "met" if ratio <= 1.0 else "exceeded"
            demand_str = f"target<={demand_t:.2f}kWh  actual={actual_k:.3f}kWh  ratio={ratio:.2f} {ok}"
        else:
            demand_str = "(demand agent not run for this event)"
        reason = e.get("reason", "")
        comment = e.get("comment", "")
        strategy_exp_lines = format_strategy_explanation_lines(
            e.get("strategy_explanation"),
            indent="    ",
        )
        # Per-appliance VPP avoidance for this event
        appl_summ = e.get("appliance_summary", {})
        appl_avoid_parts = []
        for nm, info in appl_summ.items():
            if not info.get("present", False):
                continue
            if nm in ("water_heater", "ev"):
                avoided = not info.get("ran_during_vpp", False)
                appl_avoid_parts.append(f"{nm}:{'avoided VPP' if avoided else 'ran in VPP'}")
            else:
                # shiftable appliances: skip != avoidance
                if info.get("skipped"):
                    appl_avoid_parts.append(f"{nm}:skipped task")
                elif info.get("ran_during_vpp"):
                    appl_avoid_parts.append(f"{nm}:ran in VPP")
                else:
                    appl_avoid_parts.append(f"{nm}:shifted away")
        appl_avoid_str = "  ".join(appl_avoid_parts) if appl_avoid_parts else "no controllable appliances"
        capacity = e.get("capacity_assessment", {}).get("assessment", {})
        if capacity:
            constraints = ", ".join(capacity.get("main_constraints", [])) or "none"
            capacity_str = (
                f"committable={capacity.get('committable_kw', 0):.3f}kW  "
                f"recommended_bid={capacity.get('recommended_bid_kw', 0):.3f}kW  "
                f"success_prob={capacity.get('success_probability', 0)*100:.1f}%  "
                f"constraints: {constraints}"
            )
        else:
            capacity_str = "(capacity assessment not run)"
        capacity_window = e.get("capacity_window_summary", {})
        if capacity_window:
            capacity_window_str = (
                f"avg_committable={capacity_window.get('avg_committable_kw', 0):.3f}kW  "
                f"firm_min={capacity_window.get('firm_min_committable_kw', 0):.3f}kW  "
                f"committable_energy={capacity_window.get('committable_energy_kwh', 0):.3f}kWh  "
                f"recommended_bid_energy={capacity_window.get('recommended_bid_energy_kwh', 0):.3f}kWh"
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
                "N/A (Total_Quantification not run; "
                + total_q90.get("reason", "missing A3 conformal P_base input") + ")"
            )
        trigger_actions = e.get("vpp_trigger_actions", {})
        day_decisions   = e.get("day_decisions", [])
        _pdev = {k for k, v in e.get("appliance_summary", {}).items() if v.get("present")}
        strat_lines = _fmt_strategy(sp_str, trigger_actions, day_decisions, present_devices=_pdev)
        lines += [
            f"  [Event {ev_num}] Day{vdef['day']} {_fmt_h(vdef['trigger_h'])}-{_fmt_h(vdef['end_h'])}"
            f"  objective: demand-side peak shaving for {event_duration_text}",
            f"    Executed strategy ({len(day_decisions)} control decisions today):",
        ] + strat_lines + [
            f"    VPP demand       : {demand_str}",
            f"    Trigger capacity : {capacity_str}",
            f"    Window capacity  : {capacity_window_str}",
            f"    90% firm capacity: {total_q90_str}",
            f"    Agent rationale  : {reason}" if reason else "",
        ] + strategy_exp_lines + [
            f"    Appliance VPP use: {appl_avoid_str}",
            f"    User score       : {score_str}",
            f"    Score comment    : {comment[:100]}" if comment else "",
            "",
        ]
    lines = [l for l in lines if l != ""]  # drop blank-only lines from empty fields

    # ── Section 2: Appliance scheduling goals ────────────────────────
    lines += ["", "─" * 62, "  Appliance Scheduling Goals", "─" * 62]

    def _goal_flag(completed, skipped, ran_vpp):
        if skipped:       return "skipped (not done)"
        if not completed: return "not done"
        if ran_vpp:       return "done (inside VPP)"
        return "done"

    shiftable_order = ["washer", "dishwasher", "dryer"]
    has_shiftable = False
    for dev in shiftable_order:
        days_data = appl.get(dev, [])
        if not days_data or not days_data[0].get("present", False):
            continue   # not in household — don't show
        has_shiftable = True
        parts = []
        for day_d in days_data:
            sched_abs_h = day_d.get("scheduled_abs_h")
            sched_txt = "--:--"
            if sched_abs_h is not None:
                sched_txt = f"{int(float(sched_abs_h) % 24):02d}:00"
            flag = _goal_flag(day_d.get("completed", False),
                              day_d.get("skipped", False),
                              day_d.get("ran_during_vpp", False))
            parts.append(f"Day{day_d['day']+1}[{sched_txt} {flag}]")
        lines.append(f"  {dev:<14}: " + "  ".join(parts))

    # Per-day shiftable completion rate (from metrics)
    per_day = d.get("task_completion_per_day", [])
    per_day_shift = d.get("task_shift_success_per_day", [])
    if per_day:
        day_strs = "  ".join(f"Day{i+1}:{int(v*100)}%" for i, v in enumerate(per_day))
        lines.append(f"  {'Policy output/day':<24}: {day_strs}")
    if per_day_shift:
        day_strs_shift = "  ".join(f"Day{i+1}:{int(v*100)}%" for i, v in enumerate(per_day_shift))
        lines.append(f"  {'Post-completion VPP avoidance/day':<34}: {day_strs_shift}")

    # Water heater (only if present)
    wh_days = appl.get("water_heater", [])
    if wh_days and wh_days[0].get("present", False):
        parts = []
        for day_d in wh_days:
            ph = "preheat=yes" if day_d.get("preheat_used") else "preheat=no"
            bath = "ready_before_bath=yes" if day_d.get("ready_at_bath", True) else "ready_before_bath=no"
            vpp_flag = "heated_in_VPP" if day_d.get("ran_during_vpp") else ""
            ekwh = day_d.get("energy_kwh", 0)
            parts.append(f"Day{day_d['day']+1}[{ph} {bath} {vpp_flag} {ekwh:.1f}kWh]")
        lines.append(f"  {'water_heater':<14}: " + "  ".join(parts))

    # EV (only if present)
    ev_days = appl.get("ev", [])
    if ev_days and ev_days[0].get("present", False):
        parts = []
        for day_d in ev_days:
            tgt = "SOC_target_met" if day_d.get("target_reached") else "SOC_target_missed"
            vpp_flag = "charged_in_VPP" if day_d.get("ran_during_vpp") else ""
            ekwh = day_d.get("energy_kwh", 0)
            soc = day_d.get("soc_end", 0)
            parts.append(f"Day{day_d['day']+1}[{tgt} SOC={soc:.0%} {ekwh:.1f}kWh {vpp_flag}]")
        lines.append(f"  {'ev':<14}: " + "  ".join(parts))

    # Section 3: Key metrics summary
    score_per_event = "  ".join(f"VPP{i+1}:{s}" for i, s in enumerate(scores)) if scores else "N/A"
    price_day_lines = []
    for item in price_metrics.get("per_day", []) or []:
        price_day_lines.append(
            f"Day{item.get('day', '?')}:{_fmt_num(item.get('cost_eur'), 4, ' EUR')}"
            f"@{_fmt_num(item.get('weighted_price_eur_per_kwh'), 5, ' EUR/kWh')}"
        )
    price_day_str = "  ".join(price_day_lines) if price_day_lines else "NaN"
    vpp_durations = [
        max(0.0, float(v.get("end_h", 0.0)) - float(v.get("trigger_h", 0.0)))
        for v in vpp_defs
    ]
    total_vpp_duration_h = sum(vpp_durations)
    if vpp_defs and total_vpp_duration_h > 0:
        unique_durations = {round(value, 6) for value in vpp_durations}
        if len(unique_durations) == 1:
            avg_vpp_duration_text = _fmt_duration_h(total_vpp_duration_h / len(vpp_defs))
            vpp_duration_summary = f"{len(vpp_defs)} events x {avg_vpp_duration_text} total"
        else:
            vpp_duration_summary = f"{len(vpp_defs)} events, total window {_fmt_duration_h(total_vpp_duration_h)}"
    else:
        vpp_duration_summary = "no VPP events"
    vpp_window_energy = float(d.get("vpp_window_energy_kwh", 0) or 0.0)
    vpp_window_avg_hour = d.get("vpp_window_energy_avg_per_hour_kwh")
    if vpp_window_avg_hour is None and total_vpp_duration_h > 0.0:
        vpp_window_avg_hour = vpp_window_energy / total_vpp_duration_h
    lines += [
        "",
        "─" * 62,
        "  Key Metrics Summary",
        "─" * 62,
        f"  - VPP peak shaving",
        f"      VPP-window electricity : {vpp_window_energy:.3f} kWh ({vpp_duration_summary})",
        f"      VPP-window avg / h    : {float(vpp_window_avg_hour or 0.0):.3f} kWh",
        f"      Actual shed           : unavailable without same-run no-DR counterfactual",
        ("      Demand achievement    : " + _vpp_ratio_str(result)),
        f"      Policy service output : {d.get('appliance_task_completion_rate', 1.0)*100:.0f}%"
        f"  (emitted present-appliance strategies / present non-AC appliances)",
        f"      Covered services      : {', '.join(d.get('policy_output_covered_appliance_services') or []) or 'none'}",
        f"      Missing services      : {', '.join(d.get('policy_output_uncovered_appliance_services') or []) or 'none'}",
        f"      Extra emitted services: {', '.join(d.get('policy_output_absent_appliance_services') or []) or 'none'}",
        f"      Physical completion   : {d.get('physical_appliance_task_completion_rate', d.get('appliance_task_completion_rate', 1.0))*100:.0f}%"
        f"  (simulator service outcomes; diagnostic only)",
        f"      Post-completion VPP avoidance: {d.get('appliance_vpp_avoidance_rate', 0)*100:.0f}%"
        f"  (denominator=completed controllable services; numerator=not running in VPP)",
        f"  - Day-ahead price",
        f"      Weighted cost         : {_fmt_num(price_metrics.get('total_cost_eur'), 4, ' EUR')}",
        f"      Weighted average price: {_fmt_num(price_metrics.get('weighted_price_eur_per_kwh'), 5, ' EUR/kWh')}",
        f"      Per day               : {price_day_str}",
        f"  - Electricity",
        f"      Total energy          : {d.get('energy_kwh_total', 0):.2f} kWh ({sim_days} days)",
        f"      Daily average         : {d.get('energy_kwh_per_day', 0):.2f} kWh/day",
        f"  - User comfort",
        f"      Average satisfaction  : {avg_score_str}",
        f"      Per event             : {score_per_event}",
        f"      Mean zone temperature : {d.get('mean_temp_c', 0):.2f} °C",
        f"      PMV pass rate         : {d.get('pmv_ok_fraction', 0)*100:.1f}%",
        f"      Comfort-band pass rate: {d.get('comfort_ok_fraction', 0)*100:.1f}% (23-26°C)",
        f"      Unmet cooling         : {d.get('unmet_cooling_h', 0):.1f} h",
        f"  - Appliance goals",
        f"      EV charging target met: {d.get('ev_target_reached_rate', 0)*100:.0f}%",
        f"      Water-heater preheat  : {d.get('ewh_preheat_used_rate', 0)*100:.0f}%",
        f"  - Token usage",
        f"      LLM calls             : {d.get('llm_call_count', 0)} (failures {d.get('llm_call_failures', 0)})",
        f"      Average latency       : {llm_avg_lat:.2f} s/call",
        f"      Total tokens          : {d.get('llm_tokens_prompt', 0)} prompt + {d.get('llm_tokens_completion', 0)} completion",
        "",
        "─" * 62,
        "  EnergyPlus",
        "─" * 62,
        f"  EP result        : {ep_ok}",
        f"  Detailed log     : {output_dir}/eplusout.err",

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
