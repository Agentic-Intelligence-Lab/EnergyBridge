"""EnergyBridge + EnergyPlus 3-day summer co-simulation with persona role-play.

Uses Family_Simple_3day.idf (July 1-3), Tianjin EPW.
Injects VPP events at 18:00 on each of the 3 days.
Uses basic_role_a_commuter_price_cooperative.json for role-play scoring.

Run
---
    cd ~/work/EnergyBridge
    python examples/run_eplus_3day_persona.py
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
IDF_PATH  = PROJECT_ROOT / "Family_Model" / "Family_Simple_3day.idf"
EPW_PATH  = PROJECT_ROOT / "experiments" / "weather" / "epw" / "CHN_TJ_Tianjin.545270_CSWD.epw"
EPLUS_ROOT = Path("/home/hku_user/EnergyPlus-24-1-0")
PERSONA_PATH = PROJECT_ROOT / "energybridge" / "roleplay" / "personas" / "basic_role_a_commuter_price_cooperative.json"

# VPP event injected at 18:00 on each of the 3 days
VPP_TRIGGER_HOURS = [18.0, 42.0, 66.0]   # day1 18:00 / day2 18:00 / day3 18:00

_VPP_TEMPLATE = {
    "vpp_task_type": "INVITATION_DEMAND_RESPONSE",
    "vpp_time_scale": "DAY_AHEAD",
    "vpp_trigger_reason": "REGIONAL_PEAK_LOAD",
    "vpp_start_time": "18:00",
    "vpp_end_time": "19:00",
    "vpp_notice_minutes": 60,
    "vpp_duration_minutes": 60,
    "vpp_required_capacity_kw": 0.5,
    "vpp_declaration_deadline": "",
    "vpp_response_direction": "load_reduction",
    "vpp_capacity_scope": "upstream_total_capacity",
}


def _make_vpp(day: int) -> dict:
    ctx = dict(_VPP_TEMPLATE)
    ctx["vpp_task_id"]  = f"summer-3day-{day:02d}"
    ctx["vpp_query_id"] = f"summer-3day-query-{day:02d}"
    return ctx


def _roleplay_score(persona: dict, result, turn_index: int) -> dict:
    from energybridge.llm.roleplay_user import RoleplayUserSimulator
    sim = RoleplayUserSimulator()
    fb = sim.generate_feedback(
        persona=persona,
        turn_index=turn_index,
        selected_strategy=result.home_state,
        projected_control_plan=result.control_plan,
        projected_safety_report=result.safety_report,
    )
    return fb


def main() -> None:
    # Validate paths
    for p, label in [(IDF_PATH, "IDF"), (EPW_PATH, "EPW"), (EPLUS_ROOT, "EnergyPlus")]:
        if not p.exists():
            print(f"ERROR: {label} not found: {p}")
            sys.exit(1)

    # Load persona
    persona = json.loads(PERSONA_PATH.read_text(encoding="utf-8"))
    if "persona_id" not in persona:
        persona["persona_id"] = persona.get("id", "basic_role_a")
    user_input = persona.get("llm_prompts", {}).get("system_prompt",
        "我希望尽量舒服，但如果电网有需求，可以短时间配合削峰。")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = PROJECT_ROOT / "logs" / f"eplus_3day_{ts}"

    print("=" * 62)
    print("EnergyBridge + EnergyPlus  3-Day Summer Co-simulation")
    print("=" * 62)
    print(f"IDF         : {IDF_PATH.name}")
    print(f"EPW         : {EPW_PATH.name}")
    print(f"Persona     : {persona.get('display_name')}")
    print(f"VPP events  : hours {VPP_TRIGGER_HOURS} (18:00 each day)")
    print(f"Output      : {output_dir}")
    print()

    from energybridge.simulation.eplus_env import EplusEnv

    env = EplusEnv(
        idf_path=IDF_PATH,
        epw_path=EPW_PATH,
        output_dir=output_dir,
        eplus_root=EPLUS_ROOT,
        memory_path=str(PROJECT_ROOT / "logs" / "memory.json"),
        log_dir=str(PROJECT_ROOT / "logs"),
    )

    # Inject one VPP event per day
    for day, hour in enumerate(VPP_TRIGGER_HOURS, start=1):
        env.inject_vpp_event(
            vpp_context=_make_vpp(day),
            user_input=user_input,
            trigger_hour=hour,
        )
        print(f"Queued VPP event day {day} @ hour {hour:.0f}")

    print()
    print("Starting EnergyPlus …  (3-day simulation, ~10s)")
    print()

    exit_code = env.run()

    print()
    print("=" * 62)
    print(f"EnergyPlus finished  exit_code={exit_code}  results={len(env.agent_results)}")
    print("=" * 62)

    if not env.agent_results:
        print("WARNING: No agent results — check trigger hours are within simulation period.")
        sys.exit(exit_code)

    # Per-day summary + roleplay scoring
    all_scores = []
    all_results = []
    for idx, result in enumerate(env.agent_results, start=1):
        print(f"\n--- Day {idx}  (sim_hour={result.sim_hour:.1f}) ---")
        print(f"  Indoor  : {result.home_state.get('indoor_temp')} °C")
        print(f"  Outdoor : {result.home_state.get('outdoor_temp')} °C")
        print(f"  HVAC kW : {result.home_state.get('hvac_cooling_thermal_kw')} kW(th)")
        print(f"  Total kW: {result.home_state.get('facility_power_kw')} kW")
        print(f"  Setpoint: {result.control_plan.get('setpoint')} °C  "
              f"({result.control_plan.get('duration_minutes')} min)")
        print(f"  Response: {result.final_response[:120]}")

        print(f"  [Role-play scoring day {idx}…]")
        try:
            fb_result = _roleplay_score(persona, result, turn_index=idx)
            fb = fb_result.get("data", {})
            score = fb.get("satisfaction_score", "?")
            label = fb.get("satisfaction_label", "")
            comment = fb.get("comment", "")
            latency = fb_result.get("metrics", {}).get("latency_seconds", "?")
            print(f"  Satisfaction : {score}/5  ({label})")
            print(f"  Comment      : {comment}")
            print(f"  LLM latency  : {latency}s")
            all_scores.append(score if isinstance(score, (int, float)) else 0)
        except Exception as exc:
            print(f"  WARNING: roleplay scoring failed: {exc}")
            fb = {}

        all_results.append({
            "day": idx,
            "sim_hour": result.sim_hour,
            "home_state": result.home_state,
            "control_plan": result.control_plan,
            "final_response": result.final_response,
            "satisfaction_score": fb.get("satisfaction_score"),
            "satisfaction_label": fb.get("satisfaction_label"),
            "comment": fb.get("comment"),
        })

    # Save combined result
    output_dir.mkdir(parents=True, exist_ok=True)
    summary_path = output_dir / "3day_summary.json"
    summary_path.write_text(json.dumps({
        "persona": persona.get("display_name"),
        "idf": str(IDF_PATH.name),
        "epw": str(EPW_PATH.name),
        "days": all_results,
        "avg_satisfaction": round(sum(all_scores) / len(all_scores), 2) if all_scores else None,
    }, indent=2, ensure_ascii=False))

    print()
    print("=" * 62)
    if all_scores:
        print(f"Average satisfaction : {sum(all_scores)/len(all_scores):.1f} / 5")
    print(f"Summary saved        : {summary_path}")
    print("=" * 62)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
