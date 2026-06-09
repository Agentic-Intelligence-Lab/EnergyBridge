"""EnergyBridge + EnergyPlus co-simulation demo.

This script connects the EnergyBridge agent loop to a real EnergyPlus
simulation of Family_Simple.idf.  It injects a synthetic VPP DR event
at a specified simulation hour and lets the agent read the building state,
make a decision, and write the HVAC setpoint back to EnergyPlus.

Prerequisites
-------------
- EnergyPlus 24.1.0 installed at EPLUS_ROOT or /home/hku_user/EnergyPlus-24-1-0
- Tianjin EPW weather file at Family_Model/../Weather/Tianjin/...
- conda environment: energybridge

Run
---
::

    cd ~/work/EnergyBridge
    python examples/run_eplus_agent_loop.py

Optional arguments
------------------
--idf       Path to IDF file (default: Family_Model/Family_Simple.idf)
--epw       Path to EPW weather file
--output    Output directory for EnergyPlus results (default: logs/eplus_run)
--trigger   Simulation hour at which to inject the VPP event (default: 42.0,
            i.e. 18:00 on day 2 of the simulation)
--user      User preference string passed to the agent
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv
    load_dotenv(PROJECT_ROOT / ".env")
except Exception:
    pass
EPLUS_ROOT = Path(os.getenv("EPLUS_ROOT", "/home/hku_user/EnergyPlus-24-1-0"))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ---------------------------------------------------------------------------
# Default paths
# ---------------------------------------------------------------------------

_FAMILY_MODEL_DIR = PROJECT_ROOT / "Family_Model"
_DEFAULT_IDF = _FAMILY_MODEL_DIR / "Family_Simple.idf"
_DEFAULT_EPW = (
    _FAMILY_MODEL_DIR.parent
    / "Family_Model"
    / ".."
    / "Family_Model"
    / "Weather"
    / "Tianjin"
    / "CHN_Tianjin.Tianjin.545270_CSWD.epw"
)
# Resolve a more reliable default by searching common locations
def _find_epw() -> Path:
    candidates = [
        # Tianjin EPW in repo (preferred)
        PROJECT_ROOT / "experiments" / "weather" / "epw" / "CHN_TJ_Tianjin.545270_CSWD.epw",
        PROJECT_ROOT / "Family_Model" / "Weather" / "Tianjin" / "CHN_Tianjin.Tianjin.545270_CSWD.epw",
        Path("/home/ha_agent/work/Family_Model/Weather/Tianjin/CHN_Tianjin.Tianjin.545270_CSWD.epw"),
        # Fallback: EnergyPlus bundled EPW files for functional testing
        EPLUS_ROOT / "WeatherData" / "USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw",
        EPLUS_ROOT / "WeatherData" / "USA_CA_San.Francisco.Intl.AP.724940_TMY3.epw",
    ]
    for p in candidates:
        if p.exists():
            return p
    # Return first candidate even if missing; error will surface at runtime
    return candidates[0]


# ---------------------------------------------------------------------------
# Synthetic VPP context (mirrors the invitation mode from run_agent_loop.py)
# ---------------------------------------------------------------------------

_SYNTHETIC_VPP_CONTEXT = {
    "vpp_task_id": "demo-eplus-001",
    "vpp_query_id": "demo-eplus-query-001",
    "vpp_task_type": "INVITATION_DEMAND_RESPONSE",
    "vpp_time_scale": "DAY_AHEAD",
    "vpp_trigger_reason": "REGIONAL_PEAK_LOAD",
    "vpp_start_time": "18:00",
    "vpp_end_time": "19:00",
    "vpp_notice_minutes": 60,
    "vpp_duration_minutes": 60,
    "vpp_required_capacity_kw": 0.5,
    "vpp_declaration_deadline": "",
    "vpp_response_direction": "REDUCE",
    "vpp_capacity_scope": "upstream_total_capacity",
}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="EnergyBridge + EnergyPlus co-simulation demo")
    parser.add_argument("--idf", default=str(_DEFAULT_IDF), help="Path to IDF file")
    parser.add_argument("--epw", default=None, help="Path to EPW weather file")
    parser.add_argument("--output", default=None,
                        help="EnergyPlus output directory (default: logs/eplus_run_YYYYMMDD_HHMMSS)")
    parser.add_argument("--trigger", type=float, default=42.0,
                        help="Simulation hour to inject VPP event (default: 42 = day2 18:00)")
    parser.add_argument("--user", default="我希望尽量舒服，但如果电网有需求，也可以短时间配合削峰。",
                        help="User preference string")
    parser.add_argument("--analyze-output", action="store_true",
                        help="Auto-run analyze_eplus_run.py after EP finishes")
    parser.add_argument("--eplus-root", default="/home/hku_user/EnergyPlus-24-1-0",
                        help="Path to EnergyPlus installation root")
    return parser.parse_args()



def _run_roleplay_feedback(agent_result, output_dir: "Path", args) -> None:
    """Run LLM-backed roleplay feedback scoring after the EP simulation.

    Creates a minimal user persona from the --user input and asks the
    RoleplayUserSimulator to score satisfaction with the agent's decision.
    Saves roleplay_feedback.json next to agent_result.json.
    """
    from energybridge.utils.config import load_llm_config
    llm_cfg = load_llm_config(use_key="USE_LLM")
    if not llm_cfg.use_llm:
        print("Roleplay scoring skipped (USE_LLM=false).")
        return

    try:
        from energybridge.llm.roleplay_user import RoleplayUserSimulator

        simulator = RoleplayUserSimulator()

        # Build a minimal persona from the user input string
        persona = {
            "display_name": "EP Run User",
            "summary": args.user,
            "speaking_language": "zh-cn",
            "stable_preferences": {
                "comfort_priority": 0.5,
                "cost_priority": 0.2,
                "grid_priority": 0.3,
                "preferred_temp_min": 24.0,
                "preferred_temp_max": 26.0,
                "allow_pre_cooling": True,
                "allow_temp_drift": True,
            },
        }

        feedback_result = simulator.generate_feedback(
            persona=persona,
            turn_index=1,
            selected_strategy=agent_result.home_state,
            projected_control_plan=agent_result.control_plan,
            projected_safety_report=agent_result.safety_report,
        )

        fb = feedback_result.get("data", {})
        score = fb.get("satisfaction_score")
        label = fb.get("satisfaction_label", "unknown")
        comment = fb.get("comment", "")
        m = feedback_result.get("metrics", {})

        print()
        print("=== Roleplay Comfort Scoring ===")
        print(f"  Satisfaction score : {score}/5  ({label})")
        print(f"  Comment            : {comment}")
        print(f"  LLM latency        : {m.get('latency_seconds', '?')} s")
        print(f"  Tokens             : {m.get('token_usage', {}).get('total_tokens', '?')}")

        # Save to disk
        feedback_path = output_dir / "roleplay_feedback.json"
        feedback_path.write_text(
            json.dumps(
                {
                    "satisfaction_score": score,
                    "satisfaction_label": label,
                    "comment": comment,
                    "llm_metrics": m,
                    "persona_summary": args.user,
                    "control_plan": agent_result.control_plan,
                    "sim_hour": agent_result.sim_hour,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        print(f"  Roleplay feedback  : {feedback_path}")
    except Exception as exc:
        print(f"WARNING: Roleplay feedback failed: {exc}")


def main() -> None:
    args = parse_args()

    idf_path = Path(args.idf)
    eplus_root = Path(args.eplus_root)
    epw_path = Path(args.epw) if args.epw else _find_epw()
    # Generate timestamped output dir when not explicitly specified
    if args.output is None:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_dir = PROJECT_ROOT / "logs" / f"eplus_run_{ts}"
    else:
        output_dir = Path(args.output)

    # Validate paths before starting EnergyPlus
    if not idf_path.exists():
        print(f"ERROR: IDF file not found: {idf_path}")
        sys.exit(1)
    if not epw_path.exists():
        print(f"ERROR: EPW weather file not found: {epw_path}")
        print("  Please specify --epw or place the Tianjin EPW at:")
        print(f"  {_find_epw()}")
        sys.exit(1)

    print("=" * 60)
    print("EnergyBridge + EnergyPlus Co-simulation Demo")
    print("=" * 60)
    print(f"IDF     : {idf_path}")
    print(f"EPW     : {epw_path}")
    print(f"Output  : {output_dir}")
    print(f"Trigger : hour {args.trigger:.1f} (day {int(args.trigger // 24) + 1}, "
          f"{int(args.trigger % 24):02d}:00)")
    print(f"User    : {args.user}")
    print()

    # Import here so the script is importable even without pyenergyplus
    from energybridge.simulation.eplus_env import EplusEnv

    env = EplusEnv(
        idf_path=idf_path,
        epw_path=epw_path,
        output_dir=output_dir,
        memory_path=str(PROJECT_ROOT / "logs" / "memory.json"),
        log_dir=str(PROJECT_ROOT / "logs"),
        eplus_root=eplus_root,
    )

    # Inject the synthetic VPP event
    env.inject_vpp_event(
        vpp_context=_SYNTHETIC_VPP_CONTEXT,
        user_input=args.user,
        trigger_hour=args.trigger,
    )

    print("Starting EnergyPlus simulation …")
    print("(This may take several minutes for a full annual run.)")
    print()

    exit_code = env.run()

    print()
    print("=" * 60)
    print(f"EnergyPlus finished with exit code: {exit_code}")
    print("=" * 60)

    if not env.agent_results:
        print("WARNING: No agent results recorded. The VPP event may not have fired.")
        print(f"  Check that trigger_hour={args.trigger} falls within the simulation period.")
        sys.exit(exit_code)

    # Print summary of each agent invocation
    for idx, result in enumerate(env.agent_results, start=1):
        print(f"\n--- Agent Result #{idx} ---")
        print(f"Simulation hour : {result.sim_hour:.2f}")
        print(f"Indoor temp     : {result.home_state.get('indoor_temp')} °C")
        print(f"Outdoor temp    : {result.home_state.get('outdoor_temp')} °C")
        print(f"HVAC power      : {result.home_state.get('hvac_power_kw')} kW")
        print()
        print("Control plan:")
        print(json.dumps(result.control_plan, ensure_ascii=False, indent=2))
        print()
        print("Safety report:")
        print(json.dumps(result.safety_report, ensure_ascii=False, indent=2))
        print()
        print("Execution result:")
        print(json.dumps(result.execution_result, ensure_ascii=False, indent=2))
        print()
        print("Final response:")
        print(result.final_response)
        print()
        print(f"Trajectory steps: {len(result.trajectory)}")
        for step in result.trajectory:
            print(f"  {step.get('node')}")

    print()
    print(f"EnergyPlus output files: {output_dir}")
    print(f"Agent memory log       : {PROJECT_ROOT / 'logs' / 'memory.json'}")

    # Save agent_result.json into the output folder for post-run analysis
    if env.agent_results:
        agent_result_path = output_dir / "agent_result.json"
        first_result = env.agent_results[0]
        try:
            ar_dict = {
                "sim_hour": first_result.sim_hour,
                "home_state": first_result.home_state,
                "control_plan": first_result.control_plan,
                "safety_report": first_result.safety_report,
                "execution_result": first_result.execution_result,
                "final_response": first_result.final_response,
                "trajectory": first_result.trajectory,
                "llm_metrics": first_result.llm_metrics,
            }
            agent_result_path.write_text(
                json.dumps(ar_dict, indent=2, ensure_ascii=False)
            )
            print(f"Agent result JSON      : {agent_result_path}")
        except Exception as e:
            print(f"WARNING: Could not save agent_result.json: {e}")

    # Optional post-run analysis
    if args.analyze_output:
        print()
        print("Running post-run analyzer …")
        analyzer = Path(__file__).parent / "analyze_eplus_run.py"
        try:
            import subprocess as _sp
            _sp.run(
                [sys.executable, str(analyzer),
                 "--output", str(output_dir),
                 "--trigger", str(args.trigger),
                 "--duration", "60"],
                check=False,
            )
        except Exception as e:
            print(f"WARNING: analyzer failed: {e}")


    # ── Post-run roleplay comfort scoring ─────────────────────────────────
    if env.agent_results:
        first_result = env.agent_results[0]
        _run_roleplay_feedback(first_result, output_dir, args)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
