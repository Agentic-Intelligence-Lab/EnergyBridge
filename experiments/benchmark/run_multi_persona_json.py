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
from multi_agent_pool import PersonaAgent, DiscussionPool

PERSONA_DIR = _PROJECT_ROOT / "energybridge" / "roleplay" / "personas"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_persona_json(persona_arg: str) -> dict:
    p = Path(persona_arg)
    if p.exists() and p.suffix == ".json":
        return json.loads(p.read_text(encoding="utf-8"))
    candidate = PERSONA_DIR / f"{persona_arg}.json"
    if candidate.exists():
        return json.loads(candidate.read_text(encoding="utf-8"))
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
        persona=None, log_path=None,
    ) -> dict:
        # PMV-baseline: delegate to original rule-based scorer
        if method == "pmv":
            return orig_score(
                building=building, method=method,
                mean_temp_c=mean_temp_c, pmv_ok_fraction=pmv_ok_fraction,
                energy_kwh_per_day=energy_kwh_per_day,
                agent_setpoint_c=agent_setpoint_c,
                event_index=event_index,
            )
        # Agent scoring: household discussion
        n = len(pool.agents)
        print(f"\n  {'='*62}")
        print(f"  [Multi-user discussion] VPP event {event_index} ended - {n} household members discuss score")
        print(f"  {'='*62}")
        outcome = {
            "setpoint": agent_setpoint_c,
            "mean_temp_c": mean_temp_c,
            "energy_kwh_per_day": energy_kwh_per_day,
            "agent_reason": agent_reason,
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
    """One-line summary of VPP demand achievement (from event log)."""
    evts = rd.get("vpp_event_log", [])
    parts = []
    for e in evts:
        vid = e.get("id", "?")
        a   = e.get("actual_kwh")
        t   = e.get("demand_target_kwh")
        if a is not None and t is not None and t > 0:
            r   = round(a / t, 2)
            met = "✓" if r <= 1.0 else "✗"
            parts.append(f"{vid}:{r:.2f}{met}")
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
      VPP demand achievement
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
    VPP_DEFS = [
        {"id": "vpp1", "day": 1, "trigger_h": 18.0, "end_h": 19.0},
        {"id": "vpp2", "day": 2, "trigger_h": 42.0, "end_h": 43.0},
        {"id": "vpp3", "day": 3, "trigger_h": 66.0, "end_h": 67.0},
    ]
    lines += [SEP, "  VPP Event Details (discussion -> consensus -> execution -> score)", SEP]
    for ev_num, vdef in enumerate(VPP_DEFS, 1):
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

        # Demand achievement line — from per-event log fields
        t_kwh = e.get("demand_target_kwh")
        a_kwh = e.get("actual_kwh")
        if a_kwh is not None and t_kwh is not None and t_kwh > 0:
            ratio = round(a_kwh / t_kwh, 2)
            met   = "met" if ratio <= 1.0 else "not met"
            demand_str = f"target<={t_kwh:.2f}kWh  actual={a_kwh:.3f}kWh  ratio={ratio:.2f} {met}"
        elif t_kwh is not None:
            demand_str = f"target<={t_kwh:.2f}kWh"
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
        f"  Demand achievement   : {_vpp_ratio_simple(rd)}",
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
        "personas", nargs="+",
        help="2+ persona IDs or paths to JSON files.",
    )
    parser.add_argument("--city", "-c", default="Tianjin",
                        choices=["Tianjin", "Beijing", "Shanghai"])
    parser.add_argument("--output", "-o", default=None,
                        help="Override output directory; defaults to benchmark_results/multi__<ids>.")
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--max-rounds", "-r", type=int, default=3,
                        help="Max discussion rounds per VPP event (default: 3)")
    args = parser.parse_args()

    if len(args.personas) < 2:
        parser.error("At least 2 personas required for household discussion mode.")

    # Load personas
    persona_data: list[dict] = []
    for pid_arg in args.personas:
        p = _load_persona_json(pid_arg)
        persona_data.append(p)

    agents = [PersonaAgent(p) for p in persona_data]
    pool   = DiscussionPool(agents, max_rounds=args.max_rounds)

    # Output dir: join first 4 ID tokens from each persona with __
    id_parts = ["_".join(p["id"].split("_")[:4]) for p in persona_data]
    run_id   = "__".join(id_parts)
    output_dir = (
        Path(args.output) if args.output
        else DEFAULT_BENCHMARK_RESULTS_DIR / f"multi__{run_id}"
    )

    member_list = [p.get("display_name", p["id"]) for p in persona_data]
    print("=" * 70)
    print("  EnergyBridge Multi-user Household Benchmark")
    print(f"  Members : {' | '.join(member_list)}")
    print(f"  Rounds  : 2 rounds per decision point ({len(agents)} members speak in order each round)")
    print(f"  City    : {args.city}")
    print(f"  OUTPUT  : {output_dir}")
    print("=" * 70)

    merged_appliances = _merge_appliance_configs(persona_data)
    household_pref    = _household_pref_text(persona_data)
    present_devs = [k for k, v in merged_appliances.items()
                    if isinstance(v, dict) and v.get("present")]
    print(f"\n  [Household setting] {household_pref[:140]}")
    print(f"  [Appliance config] present after merge: {present_devs}\n")

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
            user_pref        = household_pref,
            appliance_config = merged_appliances,
            output_dir       = output_dir,
            weather_label    = args.city.lower(),
            verbose          = args.verbose,
            human_mode       = False,
        )
    finally:
        _ups.get_user_preference_input = _orig_get_pref
        _ups.score_user_preference     = _orig_score

    if result is None:
        print("  [ERROR] run_family_agent returned None — aborting.")
        sys.exit(1)

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
        "discussion_rounds": 2,
        "city": args.city,
        "merged_appliances": {
            k: v for k, v in merged_appliances.items() if isinstance(v, dict)
        },
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
