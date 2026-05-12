"""EnergyBridge + EnergyPlus co-simulation demo.

This script connects the EnergyBridge agent loop to a real EnergyPlus
simulation of Family_Simple.idf.  It injects a synthetic VPP DR event
at a specified simulation hour and lets the agent read the building state,
make a decision, and write the HVAC setpoint back to EnergyPlus.

Prerequisites
-------------
- EnergyPlus 24.1.0 installed at /home/ha_agent/EnergyPlus-24-1-0
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
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
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
        # Tianjin EPW (preferred)
        PROJECT_ROOT / "Family_Model" / "Weather" / "Tianjin" / "CHN_Tianjin.Tianjin.545270_CSWD.epw",
        Path("/home/ha_agent/work/Family_Model/Weather/Tianjin/CHN_Tianjin.Tianjin.545270_CSWD.epw"),
        Path("/jupyterfile/Building_Model/Weather/Tianjin/CHN_Tianjin.Tianjin.545270_CSWD.epw"),
        # Fallback: EnergyPlus bundled EPW files for functional testing
        Path("/home/ha_agent/EnergyPlus-24-1-0/WeatherData/USA_IL_Chicago-OHare.Intl.AP.725300_TMY3.epw"),
        Path("/home/ha_agent/EnergyPlus-24-1-0/WeatherData/USA_CA_San.Francisco.Intl.AP.724940_TMY3.epw"),
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
    parser.add_argument("--output", default=str(PROJECT_ROOT / "logs" / "eplus_run"),
                        help="EnergyPlus output directory")
    parser.add_argument("--trigger", type=float, default=42.0,
                        help="Simulation hour to inject VPP event (default: 42 = day2 18:00)")
    parser.add_argument("--user", default="我希望尽量舒服，但如果电网有需求，也可以短时间配合削峰。",
                        help="User preference string")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    idf_path = Path(args.idf)
    epw_path = Path(args.epw) if args.epw else _find_epw()
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

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
