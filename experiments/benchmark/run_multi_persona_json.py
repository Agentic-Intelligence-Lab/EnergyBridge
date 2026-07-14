#!/usr/bin/env python3
"""EnergyBridge multi-persona family benchmark.

N persona JSONs become family members who discuss VPP strategy and scoring.

Usage:
  python3 run_multi_persona_json.py basic_role_a_commuter_price_cooperative \
                                    basic_role_b_home_comfort_gated --city Tianjin
  python3 run_multi_persona_json.py persona_a persona_b persona_c
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

_BENCH_DIR    = Path(__file__).resolve().parent
_PROJECT_ROOT = _BENCH_DIR.parent.parent
DEFAULT_BENCHMARK_RESULTS_DIR = _PROJECT_ROOT / "benchmark_results"
load_dotenv(_PROJECT_ROOT / ".env")

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_BENCH_DIR) not in sys.path:
    sys.path.insert(0, str(_BENCH_DIR))

import user_pref_scorer as _ups
import family_runner as fr
from energybridge.data.vpp_events import describe_vpp_events, load_vpp_events_config, make_daily_vpp_events
from energybridge.roleplay.calendar import attach_calendar
from energybridge.roleplay.households import (
    build_household_persona,
    list_household_ids,
    load_household_config,
    load_household_member_personas,
)
from multi_agent_pool import PersonaAgent, DiscussionPool
from run_persona_json import (
    ENERGYBRIDGE_METHOD_ID,
    _canonical_method,
    _controller_method,
    _default_days_for_city,
    _default_start_date_for_city,
    _prepare_run_assets,
)

PERSONA_DIR = _PROJECT_ROOT / "energybridge" / "roleplay" / "personas"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_persona_json(persona_arg: str) -> dict:
    p = Path(persona_arg)
    if p.exists() and p.suffix == ".json":
        return attach_calendar(json.loads(p.read_text(encoding="utf-8")), PERSONA_DIR)
    candidate = PERSONA_DIR / f"{persona_arg}.json"
    if candidate.exists():
        return attach_calendar(json.loads(candidate.read_text(encoding="utf-8")), PERSONA_DIR)
    raise FileNotFoundError(
        f"Persona '{persona_arg}' not found. Checked: {p}, {candidate}"
    )


def _merge_appliance_configs(personas: list[dict]) -> dict:
    """Union of household appliances. AC setpoints averaged across present members."""
    merged: dict[str, Any] = {}
    for persona in personas:
        for dev, cfg in persona.get("appliances", {}).items():
            if not isinstance(cfg, dict):
                continue
            if dev not in merged:
                merged[dev] = dict(cfg)
            else:
                if cfg.get("present"):
                    merged[dev]["present"] = True
                if dev == "ac" and cfg.get("present") and merged[dev].get("present"):
                    for key in (
                        "setpoint_preferred_min_c",
                        "setpoint_preferred_max_c",
                        "temp_tolerance_c",
                    ):
                        if key in cfg and key in merged[dev]:
                            merged[dev][key] = round(
                                (merged[dev][key] + float(cfg[key])) / 2, 1
                            )
    return merged


def _household_pref_text(personas: list[dict]) -> str:
    """Short description for the AC agent's static user_pref field."""
    names = [p.get("display_name", p["id"]) for p in personas]
    weights = [
        (
            p.get("preferences", {}).get("scoring_weights", {}).get("comfort", 0.5),
            p.get("preferences", {}).get("scoring_weights", {}).get("energy",  0.3),
        )
        for p in personas
    ]
    avg_c = round(sum(w[0] for w in weights) / len(weights), 2)
    avg_e = round(sum(w[1] for w in weights) / len(weights), 2)
    priority = (
        "comfort-first" if avg_c > avg_e + 0.1
        else "savings-first" if avg_e > avg_c + 0.1
        else "balanced"
    )
    return (
        f"This is a {len(personas)}-member household: {', '.join(names)}. "
        f"Average priorities: comfort={avg_c}, energy={avg_e} ({priority}). "
        f"Event-specific consensus preferences will be provided in the [User says NOW] tag."
    )


def _household_run_label(household_id: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in household_id).strip("_").lower()


def _default_multi_output_dir(
    household_id: str,
    method: str,
    city: str,
    days: int,
    mpc_horizon: int = 6,
) -> Path:
    method = _canonical_method(method)
    token = f"{method}_H{int(mpc_horizon)}" if method == "mpc_dynamic" else method
    run_name = f"{_household_run_label(household_id)}_{token}_{city.lower()}_{int(days)}days"
    from datetime import date

    return DEFAULT_BENCHMARK_RESULTS_DIR / date.today().isoformat() / run_name


def _make_patched_functions(pool: DiscussionPool, transcripts: dict, orig_score: Any):
    """Return patched get_user_preference_input and score_user_preference."""

    def patched_get_user_preference_input(
        building, event_index, vpp_context, past_events,
        persona=None, log_path=None, human_mode=False,
    ) -> str:
        n = len(pool.agents)
        print(f"\n  {'='*62}")
        print(f"  [Multi-user discussion] VPP event {event_index} - {n} household members discuss strategy preference")
        print(f"  {'='*62}")
        pref, transcript = pool.discuss_strategy(event_index, vpp_context, past_events)
        transcripts.setdefault("strategy", {})[event_index] = transcript
        transcripts.setdefault("strategy_consensus", {})[event_index] = pref
        return pref

    def patched_score_user_preference(
        building, method="agent",
        mean_temp_c=25.0, pmv_ok_fraction=0.8,
        energy_kwh_per_day=5.0, agent_setpoint_c=26.5,
        event_index=1, user_preference_text="", agent_reason="",
        persona=None, log_path=None, **kwargs,
    ) -> dict:
        # PMV-baseline: delegate to original rule-based scorer
        if method == "pmv":
            return orig_score(
                building=building, method=method,
                mean_temp_c=mean_temp_c, pmv_ok_fraction=pmv_ok_fraction,
                energy_kwh_per_day=energy_kwh_per_day,
                agent_setpoint_c=agent_setpoint_c,
                event_index=event_index,
                user_preference_text=user_preference_text,
                agent_reason=agent_reason,
                persona=persona,
                log_path=log_path,
                **kwargs,
            )
        # Agent scoring: household discussion
        n = len(pool.agents)
        print(f"\n  {'='*62}")
        print(f"  [Multi-user discussion] VPP event {event_index} ended - {n} household members discuss score")
        print(f"  {'='*62}")
        appliance_summary = kwargs.get("appliance_summary") or {}
        policy_control_context = dict(kwargs.get("policy_control_context") or {})
        present_services = {
            name
            for name, info in appliance_summary.items()
            if name in {"washer", "dishwasher", "dryer", "water_heater", "ev"}
            and isinstance(info, dict)
            and bool(info.get("present"))
        }
        emitted_services = set(policy_control_context.get("emitted_services") or [])
        action_space_services = set(policy_control_context.get("action_space_services") or [])
        if present_services:
            policy_control_context["present_services"] = sorted(present_services)
            policy_control_context["missing_present_services"] = sorted(present_services - emitted_services)
            if action_space_services:
                policy_control_context["unsupported_present_services"] = sorted(present_services - action_space_services)

        vpp_result_context = kwargs.get("vpp_result_context") or {}
        outcome = {
            "setpoint": agent_setpoint_c,
            "mean_temp_c": mean_temp_c,
            "energy_kwh_per_day": energy_kwh_per_day,
            "agent_reason": agent_reason,
            "appliance_summary": appliance_summary,
            "policy_control_context": policy_control_context,
            "vpp_context": kwargs.get("vpp_context") or {},
            "vpp_result_context": vpp_result_context,
        }
        score, reason, transcript = pool.discuss_score(outcome, event_index)
        transcripts.setdefault("score", {})[event_index] = transcript

        label_map = {1: "very_dissatisfied", 2: "dissatisfied", 3: "neutral", 4: "satisfied", 5: "very_satisfied"}
        label = label_map.get(round(score), "neutral")
        return {
            "score": score, "label": label, "comment": reason,
            "source": "multi_agent_discussion",
            "comfort_score": score, "energy_score": score,
            "vpp_score": score, "satisfaction_score": score,
        }

    return patched_get_user_preference_input, patched_score_user_preference


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Multi-run summary writer
# ---------------------------------------------------------------------------

def _fmt_h_multi(h: float) -> str:
    """Float hour (0-72) -> 'HH:MM' within-day string."""
    h = h % 24
    return f"{int(h):02d}:{int(round((h % 1) * 60)):02d}"


def _wrap_text(text: str, width: int = 54) -> list:
    """Word-wrap text into lines of at most `width` chars."""
    import textwrap
    return textwrap.wrap(text, width) or [""]


def _vpp_ratio_simple(rd: dict) -> str:
    """One-line summary of VPP non-AC appliance avoidance."""
    evts = rd.get("vpp_event_log", [])
    parts = []
    for e in evts:
        vid = e.get("id", "?")
        achieved = e.get("target_achieved")
        if achieved is not None:
            parts.append(f"{vid}:{'✓' if achieved else '✗'}")
    agg = rd.get("vpp_demand_achievement_ratio")
    agg_str = f"  (overall {agg:.2f})" if isinstance(agg, float) else ""
    return ("  ".join(parts) + agg_str) if parts else "N/A"



def _appliance_goal_lines(rd: dict) -> list[str]:
    labels = {
        "washer": "washer target met",
        "dishwasher": "dishwasher target met",
        "dryer": "dryer target met",
        "water_heater": "water-heater target met",
        "ev": "EV charging target met",
    }
    rates = rd.get("appliance_goal_attainment_rates") or {}
    lines: list[str] = []
    for key in ("washer", "dishwasher", "dryer", "water_heater", "ev"):
        if key in rates:
            lines.append(f"  {labels[key]:<12}: {rates[key]*100:.0f}%")
    return lines


def _fmt_discussion_block(transcript: list, n_members: int,
                           block_type: str, consensus_line: str) -> list:
    """Render one discussion block (strategy or score) as lines.

    transcript: list of {"name": str, "text": str}
    block_type: "strategy discussion" or "satisfaction discussion"
    consensus_line: the final consensus line to append
    """
    if not transcript:
        return []
    n_rounds = max(1, (len(transcript) + n_members - 1) // n_members)
    lines = [f"  ┌─ {block_type} ({n_rounds} rounds · {n_members} members) {'─' * (54 - len(block_type))}"]
    for ri in range(n_rounds):
        rlabel = "initial opinions" if ri == 0 else f"round {ri + 1}"
        lines.append(f"  │  [{rlabel}]")
        for mi in range(n_members):
            idx = ri * n_members + mi
            if idx < len(transcript):
                ent  = transcript[idx]
                name = ent.get("name", "?")
                text = ent.get("text", "")
                lines.append(f"  │    [{name}]")
                for chunk in _wrap_text(text, 52):
                    lines.append(f"  │      {chunk}")
    lines.append(consensus_line)
    return lines


def _write_multi_run_summary(
    result,
    persona_data: list,
    city: str,
    output_dir: Path,
    transcripts: dict,
) -> Path:
    """Generate human-readable run_summary.txt for multi-persona household run.

    Structure per VPP event:
      Strategy discussion (N rounds * M members) -> consensus preference
      Execution strategy (AC + appliances)
      VPP non-AC appliance avoidance
      Satisfaction discussion (N rounds * M members) -> consensus score
    """
    import datetime

    # Try to import _fmt_strategy from single-persona runner
    try:
        from run_persona_json import _fmt_strategy
    except Exception:
        _fmt_strategy = None  # fallback: plain setpoint line

    rd           = result.as_dict()
    evts         = rd.get("vpp_event_log", [])
    n_members    = len(persona_data)
    member_names = [p.get("display_name", p["id"]) for p in persona_data]
    now          = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    SEP  = "─" * 62
    SEP2 = "=" * 62
    lines: list = []

    # ── Header ────────────────────────────────────────────────────────────
    lines += [
        SEP2,
        "  EnergyBridge Multi-user Household Run Summary  (run_summary.txt)",
        SEP2,
        f"  Household members : {n_members}  {'  |  '.join(member_names)}",
        f"  City              : {city}",
        f"  Generated         : {now}",
        f"  Output dir        : {output_dir}",
    ]
    _appl_names = {"washer": "washer", "dishwasher": "dishwasher", "dryer": "dryer",
                   "water_heater": "water_heater", "ev": "EV charger"}
    _first_summ = evts[0].get("appliance_summary", {}) if evts else {}
    _pstr = " | ".join(_appl_names.get(k, k) for k, v in _first_summ.items()
                       if v.get("present")) or "(none)"
    _astr = " | ".join(_appl_names.get(k, k) for k, v in _first_summ.items()
                       if not v.get("present")) or "(none)"
    lines += [f"  Appliances        : present: {_pstr}   not configured: {_astr}", SEP]

    # ── Member profiles ───────────────────────────────────────────────────
    lines += ["  Member profiles", SEP]
    for i, p in enumerate(persona_data, 1):
        w  = p.get("preferences", {}).get("scoring_weights", {})
        cw = int(w.get("comfort", 0.5) * 100)
        ew = int(w.get("energy",  0.3) * 100)
        vw = int(w.get("vpp",     0.2) * 100)
        desc = (p.get("description")
                or p.get("llm_prompts", {}).get("system_prompt", ""))[:90]
        lines += [
            f"  [{i}] {p.get('display_name', p['id'])}",
            f"      Weights: comfort {cw}% · energy {ew}% · VPP {vw}%",
            f"      Profile: \"{desc}\"",
            "",
        ]

    # ── VPP event details ─────────────────────────────────────────────────
    lines += [SEP, "  VPP Event Details (discussion -> consensus -> execution -> score)", SEP]
    vpp_defs = []
    for idx, event in enumerate(evts, 1):
        try:
            trigger_h = float(event.get("trigger_h", (idx - 1) * 24 + 18.0))
        except (TypeError, ValueError):
            trigger_h = (idx - 1) * 24 + 18.0
        try:
            end_h = float(event.get("end_h", trigger_h + 1.0))
        except (TypeError, ValueError):
            end_h = trigger_h + 1.0
        vpp_defs.append({
            "id": event.get("id", f"vpp{idx}"),
            "day": int(trigger_h // 24) + 1,
            "trigger_h": trigger_h,
            "end_h": end_h,
        })
    for ev_num, vdef in enumerate(vpp_defs, 1):
        vid = vdef["id"]
        e   = next((x for x in evts if x.get("id") == vid), {})

        sp_val          = e.get("setpoint", 26.5)
        sp_str          = f"{sp_val:.1f}°C"
        score           = e.get("score")
        comment         = e.get("comment", "")[:80]
        trigger_actions = e.get("vpp_trigger_actions", {})
        day_decisions   = e.get("day_decisions", [])
        user_input      = e.get("user_input", "")
        agent_reason    = e.get("reason", "")

        # VPP success line: appliance avoidance, not shed/cap target matching.
        non_ac = e.get("vpp_non_ac_appliances_during_event") or []
        achieved = e.get("target_achieved")
        actual = e.get("actual_kwh")
        if achieved is not None:
            demand_str = (
                f"non-AC appliance avoidance={'met' if achieved else 'not met'}; "
                f"during_vpp={non_ac or 'none'}"
            )
            if actual is not None:
                demand_str += f"; actual_window={actual:.3f}kWh diagnostic"
        else:
            demand_str = "N/A"

        # Transcripts — keys are int (live run) or str (loaded from JSON)
        strat_tr = (transcripts.get("strategy", {}).get(ev_num)
                    or transcripts.get("strategy", {}).get(str(ev_num), []))
        score_tr = (transcripts.get("score",    {}).get(ev_num)
                    or transcripts.get("score",    {}).get(str(ev_num), []))

        lines.append(
            f"  [Event {ev_num}] Day{vdef['day']} "
            f"{_fmt_h_multi(vdef['trigger_h'])}-{_fmt_h_multi(vdef['end_h'])}"
            f"  objective: demand-side peak shaving for 1 hour"
        )
        lines.append("")

        # 1. Strategy discussion → consensus
        _consensus_text = (
            transcripts.get("strategy_consensus", {}).get(ev_num)
            or transcripts.get("strategy_consensus", {}).get(str(ev_num))
            or user_input
        )
        consensus_pref = (f"  └-> Consensus preference: {_consensus_text[:100]}"
                          if _consensus_text else "  └- (consensus preference not recorded)")
        lines += _fmt_discussion_block(strat_tr, n_members, "strategy discussion", consensus_pref)
        lines.append("")

        # 2. Execution strategy (AC + appliances)
        _pdev = {k for k, v in e.get("appliance_summary", {}).items() if v.get("present")}
        lines.append(f"    Executed strategy (all-day {len(day_decisions)} LLM decisions):")
        if _fmt_strategy:
            lines += _fmt_strategy(sp_str, trigger_actions, day_decisions,
                                   present_devices=_pdev)
        else:
            lines.append(f"    └ AC: {sp_str}")
        lines += [
            f"    VPP demand      : {demand_str}",
            f"    Agent rationale : {agent_reason[:80]}",
            "",
        ]

        # 3. Score discussion → consensus score
        score_str = (f"{score:.1f}/5 - {comment}" if score is not None else "N/A")
        consensus_score = f"  └-> Consensus score: {score_str}"
        lines += _fmt_discussion_block(score_tr, n_members, "satisfaction discussion", consensus_score)
        lines += ["", SEP]

    # ── Overall metrics ────────────────────────────────────────────────────
    lines += ["  Overall metrics", SEP]
    vpp_e  = rd.get("vpp_window_energy_kwh", rd.get("vpp_energy_kwh_total"))
    scores = rd.get("user_pref_scores", [])
    avg_sc = rd.get("user_pref_score")
    per_day = rd.get("task_completion_per_day", [])
    per_day_shift = rd.get("task_shift_success_per_day", [])
    scores_str = "  ".join(f"Event{i+1}:{s:.1f}" for i, s in enumerate(scores))
    per_day_str = "  ".join(f"Day{i+1}:{int(v*100)}%" for i, v in enumerate(per_day)) if per_day else "N/A"
    per_day_shift_str = "  ".join(f"Day{i+1}:{int(v*100)}%" for i, v in enumerate(per_day_shift)) if per_day_shift else "N/A"
    lines += [
        f"  VPP-window energy    : {f'{vpp_e:.3f} kWh (3 events total)' if isinstance(vpp_e, (int, float)) else 'N/A'}",
        f"  VPP appliance avoid  : {_vpp_ratio_simple(rd)}",
        f"  Completion by day    : {per_day_str}",
        f"  Shift success by day : {per_day_shift_str}",
        f"  Task completion      : {rd.get('appliance_task_completion_rate', 1.0)*100:.0f}%"
        f"  (ok=shifted away, x=skipped/not completed)",
        f"  Shift success        : {rd.get('appliance_shift_success_rate', 0.0)*100:.0f}%"
        f"  (denominator=all present shiftable appliance tasks; numerator=completed and not run in VPP)",
        f"  VPP avoidance        : {rd.get('appliance_vpp_avoidance_rate', 0.0)*100:.0f}%"
        f"  (denominator=completed tasks; measures whether completed tasks avoided VPP)",
        f"  Total electricity    : {rd.get('energy_kwh_total', 0):.3f} kWh",
        f"  Daily electricity    : {rd.get('energy_kwh_per_day', 0):.3f} kWh/day",
        f"  PMV comfort pass     : {rd.get('pmv_ok_fraction', 0):.1%}",
        f"  Mean indoor temp     : {rd.get('mean_temp_c', 0):.2f}°C",
    ]
    lines += _appliance_goal_lines(rd)
    lines += [
        f"  Mean consensus score : {f'{avg_sc:.2f}/5' if avg_sc else 'N/A'}  [{scores_str}]",
        SEP2,
        "",
    ]

    path = output_dir / "run_summary.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="EnergyBridge multi-persona family benchmark (household discussion).",
    )
    parser.add_argument(
        "personas", nargs="*",
        help="2+ persona IDs or paths to JSON files. Omit when using --household.",
    )
    parser.add_argument(
        "--household",
        default="",
        help="Fixed household ID or JSON path under energybridge/roleplay/households.",
    )
    parser.add_argument(
        "--list-households",
        action="store_true",
        help="List available fixed household IDs and exit.",
    )
    parser.add_argument("--city", "-c", default="Tianjin",
                        choices=["Tianjin", "Beijing", "Shanghai", "Germany"])
    parser.add_argument("--days", type=int, default=None,
                        help="Simulation length in days. Defaults to 3, or 7 for Germany.")
    parser.add_argument("--start-date", default="",
                        help="RunPeriod start date YYYY-MM-DD. Defaults to 2025-06-01 for Germany.")
    parser.add_argument("--price-csv", default="",
                        help="Optional day-ahead price CSV.")
    parser.add_argument("--weather-csv", default="",
                        help="Optional Germany real-weather CSV used to generate EPW.")
    parser.add_argument("--epw", default="",
                        help="Optional EPW override.")
    parser.add_argument("--idf", default="",
                        help="Optional family IDF template override.")
    parser.add_argument("--regenerate-epw", action="store_true",
                        help="Regenerate Germany EPW even if it already exists.")
    parser.add_argument("--vpp-start-hour", type=float, default=18.0,
                        help="Daily VPP event start hour-of-day. Default: 18.0.")
    parser.add_argument("--vpp-duration-hours", type=float, default=1.0,
                        help="Daily VPP event duration in hours. Default: 1.0.")
    parser.add_argument("--vpp-events-json", default="",
                        help="Optional JSON file defining VPP events.")
    parser.add_argument("--output", "-o", default=None,
                        help="Override output directory; defaults to benchmark_results/multi__<ids>.")
    parser.add_argument(
        "--method",
        choices=[ENERGYBRIDGE_METHOD_ID, "agent", "mpc_dynamic", "mpc", "rule_milp", "rule+milp", "pmv_milp", "no_dr", "none", "baseline"],
        default=ENERGYBRIDGE_METHOD_ID,
        help="Controller method. Default: EnergyBridge.",
    )
    parser.add_argument("--mpc-horizon", type=int, default=6,
                        help="MPC prediction horizon in 10-minute steps.")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--max-rounds", "-r", type=int, default=3,
                        help="Max discussion rounds per VPP event (default: 3)")
    args = parser.parse_args()

    if args.list_households:
        for household_id in list_household_ids():
            print(household_id)
        return

    days = _default_days_for_city(args.city, args.days)
    start_date = _default_start_date_for_city(args.city, args.start_date.strip())
    method = _canonical_method(args.method)
    controller_method = _controller_method(method)
    result_method = method
    mpc_horizon = max(1, int(args.mpc_horizon))
    vpp_start_hour = float(args.vpp_start_hour) % 24.0
    vpp_duration_hours = float(args.vpp_duration_hours)
    if vpp_duration_hours <= 0:
        raise SystemExit("--vpp-duration-hours must be > 0")
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

    household_config: dict | None = None
    if args.household:
        household_config = load_household_config(args.household)
        persona_data = load_household_member_personas(household_config)
        household_persona = build_household_persona(household_config, persona_data, days=days)
    else:
        # Convenience: a single positional household ID is accepted.
        if len(args.personas) == 1 and args.personas[0] in set(list_household_ids()):
            household_config = load_household_config(args.personas[0])
            persona_data = load_household_member_personas(household_config)
            household_persona = build_household_persona(household_config, persona_data, days=days)
        else:
            if len(args.personas) < 2:
                parser.error("Use --household <id> or provide at least 2 personas.")
            persona_data = [_load_persona_json(pid_arg) for pid_arg in args.personas]
            household_config = {
                "id": "ad_hoc_multi_persona_household",
                "display_name": "Ad-hoc Multi-Persona Household",
                "appliances": _merge_appliance_configs(persona_data),
                "llm_prompts": {"system_prompt": _household_pref_text(persona_data)},
            }
            household_persona = build_household_persona(household_config, persona_data, days=days)

    agents = [PersonaAgent(p) for p in persona_data]
    household_prompt = household_persona.get("llm_prompts", {}).get("system_prompt", "")
    pool = DiscussionPool(agents, max_rounds=args.max_rounds, household_context=household_prompt)

    output_dir = (
        Path(args.output) if args.output
        else _default_multi_output_dir(household_persona["id"], result_method, args.city, days, mpc_horizon)
    )
    idf_path, epw_path, price_profile = _prepare_run_assets(args, output_dir, days, start_date, household_persona)

    member_list = [p.get("display_name", p["id"]) for p in persona_data]
    print("=" * 70)
    print("  EnergyBridge Multi-user Household Benchmark")
    print(f"  Household : {household_persona.get('display_name', household_persona['id'])}")
    print(f"  ID        : {household_persona['id']}")
    print(f"  Members : {' | '.join(member_list)}")
    print(f"  Rounds  : up to {args.max_rounds} rounds per decision point ({len(agents)} members speak in order each round)")
    print(f"  City    : {args.city}")
    print(f"  Method  : {result_method}")
    print(f"  Days    : {days}")
    print(f"  Start   : {start_date or '(template IDF)'}")
    print(f"  VPP     : {describe_vpp_events(vpp_events)}")
    print(f"  VPP SRC : {vpp_schedule_source}")
    print(f"  IDF     : {idf_path}")
    print(f"  EPW     : {epw_path}")
    print(f"  PRICE   : {getattr(price_profile, 'source', '') or 'N/A'}")
    print(f"  OUTPUT  : {output_dir}")
    print("=" * 70)

    merged_appliances = household_persona.get("appliances", {})
    household_pref = household_prompt or _household_pref_text(persona_data)
    present_devs = [k for k, v in merged_appliances.items()
                    if isinstance(v, dict) and v.get("present")]
    print(f"\n  [Household setting] {household_pref[:140]}")
    print(f"  [Appliance config] present after merge: {present_devs}\n")
    controller_user_pref = household_pref
    if controller_method == "agent":
        controller_user_pref = (
            "No hidden household persona prompt is preloaded. Infer preferences from the initial "
            "questionnaire memory, observable calendar context, event-time user messages, "
            "and scored feedback over the run."
        )

    # Patch user_pref_scorer
    _orig_get_pref = _ups.get_user_preference_input
    _orig_score    = _ups.score_user_preference
    transcripts: dict = {}

    patched_get, patched_score = _make_patched_functions(pool, transcripts, _orig_score)
    _ups.get_user_preference_input = patched_get
    _ups.score_user_preference     = patched_score

    result = None
    try:
        result = fr.run_family_agent(
            idf_path         = idf_path,
            epw_path         = epw_path,
            user_pref        = controller_user_pref,
            appliance_config = merged_appliances,
            persona_config   = household_persona,
            output_dir       = output_dir,
            weather_label    = args.city.lower(),
            verbose          = args.verbose,
            human_mode       = False,
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
    finally:
        _ups.get_user_preference_input = _orig_get_pref
        _ups.score_user_preference     = _orig_score

    if result is None:
        print("  [ERROR] run_family_agent returned None — aborting.")
        sys.exit(1)
    result.method = result_method

    print()
    print("=" * 70)
    print("  RESULT SUMMARY")
    print("=" * 70)
    for k, v in result.as_dict().items():
        print(f"  {k}: {v}")

    output_dir.mkdir(parents=True, exist_ok=True)

    json_path = output_dir / "benchmark_result.json"
    json_path.write_text(
        json.dumps(result.as_dict(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(f"\n[Saved] benchmark_result.json → {json_path}")

    meta = {
        "run_type": "multi_agent_household",
        "household_id": household_persona["id"],
        "household_display_name": household_persona.get("display_name", household_persona["id"]),
        "household_source_path": (household_config or {}).get("_source_path", ""),
        "members": [
            {
                "id": p["id"],
                "display_name": p.get("display_name", p["id"]),
                "comfort_priority": p.get("preferences", {}).get(
                    "scoring_weights", {}
                ).get("comfort", 0.5),
                "energy_priority": p.get("preferences", {}).get(
                    "scoring_weights", {}
                ).get("energy", 0.3),
            }
            for p in persona_data
        ],
        "discussion_rounds": args.max_rounds,
        "city": args.city,
        "days": days,
        "start_date": start_date,
        "method": result_method,
        "vpp_schedule_source": vpp_schedule_source,
        "merged_appliances": {
            k: v for k, v in merged_appliances.items() if isinstance(v, dict)
        },
        "household_calendar": household_persona.get("calendar", {}),
        "household_prompt": household_pref,
        "transcripts": transcripts,
    }
    meta_path = output_dir / "household_meta.json"
    meta_path.write_text(
        json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    print(f"[Saved] household_meta.json   → {meta_path}")

    summary_path = _write_multi_run_summary(
        result, persona_data, args.city, output_dir, transcripts
    )
    print(f"[Saved] run_summary.txt        → {summary_path}")


if __name__ == "__main__":
    main()
