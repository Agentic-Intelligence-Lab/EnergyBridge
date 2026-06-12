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
        print(f"  [多用户讨论] VPP事件{event_index} — {n}位家庭成员讨论策略偏好")
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
        print(f"  [多用户讨论] VPP事件{event_index}结束 — {n}位家庭成员评分讨论")
        print(f"  {'='*62}")
        outcome = {
            "setpoint": agent_setpoint_c,
            "mean_temp_c": mean_temp_c,
            "energy_kwh_per_day": energy_kwh_per_day,
            "agent_reason": agent_reason,
        }
        score, reason, transcript = pool.discuss_score(outcome, event_index)
        transcripts.setdefault("score", {})[event_index] = transcript

        label_map = {1: "非常不满", 2: "不满意", 3: "一般", 4: "满意", 5: "非常满意"}
        label = label_map.get(round(score), "一般")
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
    agg_str = f"  (总体{agg:.2f})" if isinstance(agg, float) else ""
    return ("  ".join(parts) + agg_str) if parts else "N/A"



def _appliance_goal_lines(rd: dict) -> list[str]:
    labels = {
        "washer": "洗衣机达标",
        "dishwasher": "洗碗机达标",
        "dryer": "烘干机达标",
        "water_heater": "热水器达标",
        "ev": "EV充电达标",
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
    block_type: "策略讨论" or "满意度讨论"
    consensus_line: the └→ line to append
    """
    if not transcript:
        return []
    n_rounds = max(1, (len(transcript) + n_members - 1) // n_members)
    lines = [f"  ┌─ {block_type} ({n_rounds}轮 · {n_members}人) {'─' * (54 - len(block_type))}"]
    for ri in range(n_rounds):
        rlabel = "初始意见" if ri == 0 else f"第{ri + 1}轮"
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
      策略讨论 (N轮 * M人) → 共识偏好
      执行策略 (AC + appliances)
      VPP需求 achievement
      满意度讨论 (N轮 * M人) → 共识评分
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
        "  EnergyBridge 多用户家庭运行摘要  (run_summary.txt)",
        SEP2,
        f"  家庭成员   : {n_members}人  {'  |  '.join(member_names)}",
        f"  城市       : {city}",
        f"  生成时间   : {now}",
        f"  输出目录   : {output_dir}",
    ]
    _appl_names = {"washer": "洗衣机", "dishwasher": "洗碗机", "dryer": "烘干机",
                   "water_heater": "热水器", "ev": "EV充电桩"}
    _first_summ = evts[0].get("appliance_summary", {}) if evts else {}
    _pstr = " | ".join(_appl_names.get(k, k) for k, v in _first_summ.items()
                       if v.get("present")) or "(无)"
    _astr = " | ".join(_appl_names.get(k, k) for k, v in _first_summ.items()
                       if not v.get("present")) or "(无)"
    lines += [f"  本户电器   : ✓ 已有: {_pstr}   ✗ 未配置: {_astr}", SEP]

    # ── Member profiles ───────────────────────────────────────────────────
    lines += ["  成员档案", SEP]
    for i, p in enumerate(persona_data, 1):
        w  = p.get("preferences", {}).get("scoring_weights", {})
        cw = int(w.get("comfort", 0.5) * 100)
        ew = int(w.get("energy",  0.3) * 100)
        vw = int(w.get("vpp",     0.2) * 100)
        desc = (p.get("description")
                or p.get("llm_prompts", {}).get("system_prompt", ""))[:90]
        lines += [
            f"  [{i}] {p.get('display_name', p['id'])}",
            f"      权重: 舒适{cw}% · 节能{ew}% · VPP{vw}%",
            f"      简介: \"{desc}\"",
            "",
        ]

    # ── VPP event details ─────────────────────────────────────────────────
    VPP_DEFS = [
        {"id": "vpp1", "day": 1, "trigger_h": 18.0, "end_h": 19.0},
        {"id": "vpp2", "day": 2, "trigger_h": 42.0, "end_h": 43.0},
        {"id": "vpp3", "day": 3, "trigger_h": 66.0, "end_h": 67.0},
    ]
    lines += [SEP, "  VPP 事件详情（讨论 → 共识 → 执行 → 评分）", SEP]
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
            met   = "✓达标" if ratio <= 1.0 else "✗未达标"
            demand_str = f"目标≤{t_kwh:.2f}kWh  实际{a_kwh:.3f}kWh  比率{ratio:.2f} {met}"
        elif t_kwh is not None:
            demand_str = f"目标≤{t_kwh:.2f}kWh"
        else:
            demand_str = "N/A"

        # Transcripts — keys are int (live run) or str (loaded from JSON)
        strat_tr = (transcripts.get("strategy", {}).get(ev_num)
                    or transcripts.get("strategy", {}).get(str(ev_num), []))
        score_tr = (transcripts.get("score",    {}).get(ev_num)
                    or transcripts.get("score",    {}).get(str(ev_num), []))

        lines.append(
            f"  [事件{ev_num}] Day{vdef['day']} "
            f"{_fmt_h_multi(vdef['trigger_h'])}-{_fmt_h_multi(vdef['end_h'])}"
            f"  目标：需求侧削峰1小时"
        )
        lines.append("")

        # 1. Strategy discussion → consensus
        _consensus_text = (
            transcripts.get("strategy_consensus", {}).get(ev_num)
            or transcripts.get("strategy_consensus", {}).get(str(ev_num))
            or user_input
        )
        consensus_pref = (f"  └→ 共识偏好: {_consensus_text[:100]}"
                          if _consensus_text else "  └─ (共识偏好未记录)")
        lines += _fmt_discussion_block(strat_tr, n_members, "策略讨论", consensus_pref)
        lines.append("")

        # 2. Execution strategy (AC + appliances)
        _pdev = {k for k, v in e.get("appliance_summary", {}).items() if v.get("present")}
        lines.append(f"    执行策略 ↓ (全天{len(day_decisions)}次LLM决策):")
        if _fmt_strategy:
            lines += _fmt_strategy(sp_str, trigger_actions, day_decisions,
                                   present_devices=_pdev)
        else:
            lines.append(f"    └ 空调: {sp_str}")
        lines += [
            f"    VPP需求    : {demand_str}",
            f"    Agent理由  : {agent_reason[:80]}",
            "",
        ]

        # 3. Score discussion → consensus score
        score_str = (f"{score:.1f}/5 — {comment}" if score is not None else "N/A")
        consensus_score = f"  └→ 共识评分: {score_str}"
        lines += _fmt_discussion_block(score_tr, n_members, "满意度讨论", consensus_score)
        lines += ["", SEP]

    # ── Overall metrics ────────────────────────────────────────────────────
    lines += ["  总体指标", SEP]
    vpp_e  = rd.get("vpp_window_energy_kwh", rd.get("vpp_energy_kwh_total"))
    scores = rd.get("user_pref_scores", [])
    avg_sc = rd.get("user_pref_score")
    per_day = rd.get("task_completion_per_day", [])
    per_day_shift = rd.get("task_shift_success_per_day", [])
    scores_str = "  ".join(f"事件{i+1}:{s:.1f}" for i, s in enumerate(scores))
    per_day_str = "  ".join(f"Day{i+1}:{int(v*100)}%" for i, v in enumerate(per_day)) if per_day else "N/A"
    per_day_shift_str = "  ".join(f"Day{i+1}:{int(v*100)}%" for i, v in enumerate(per_day_shift)) if per_day_shift else "N/A"
    lines += [
        f"  VPP时段用电量   : {f'{vpp_e:.3f} kWh (3事件合计)' if isinstance(vpp_e, (int, float)) else 'N/A'}",
        f"  需求达成比率    : {_vpp_ratio_simple(rd)}",
        f"  完成率(逐天)    : {per_day_str}",
        f"  平移成功率(逐天): {per_day_shift_str}",
        f"  任务完成率      : {rd.get('appliance_task_completion_rate', 1.0)*100:.0f}%"
        f"  (✓=错峰完成 ✗=跳过任务/未完成)",
        f"  平移成功率      : {rd.get('appliance_shift_success_rate', 0.0)*100:.0f}%"
        f"  (分母=全部在户可平移电器任务；分子=完成且不在VPP运行)",
        f"  错峰率          : {rd.get('appliance_vpp_avoidance_rate', 0.0)*100:.0f}%"
        f"  (分母=已完成任务；用于衡量完成后是否避开VPP)",
        f"  总用电量        : {rd.get('energy_kwh_total', 0):.3f} kWh",
        f"  日均用电量      : {rd.get('energy_kwh_per_day', 0):.3f} kWh/day",
        f"  PMV舒适达标率   : {rd.get('pmv_ok_fraction', 0):.1%}",
        f"  平均室温        : {rd.get('mean_temp_c', 0):.2f}°C",
    ]
    lines += _appliance_goal_lines(rd)
    lines += [
        f"  共识满意度均值  : {f'{avg_sc:.2f}/5' if avg_sc else 'N/A'}  [{scores_str}]",
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
    print("  EnergyBridge 多用户家庭基准测试")
    print(f"  成员    : {' | '.join(member_list)}")
    print(f"  讨论轮数: 2 轮/决策点（每轮 {len(agents)} 人依次发言）")
    print(f"  城市    : {args.city}")
    print(f"  OUTPUT  : {output_dir}")
    print("=" * 70)

    merged_appliances = _merge_appliance_configs(persona_data)
    household_pref    = _household_pref_text(persona_data)
    present_devs = [k for k, v in merged_appliances.items()
                    if isinstance(v, dict) and v.get("present")]
    print(f"\n  [家庭设定] {household_pref[:140]}")
    print(f"  [家电配置] 合并后已有: {present_devs}\n")

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
