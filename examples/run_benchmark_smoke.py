"""Benchmark smoke-test runner for EnergyBridge.

Usage
-----
With EnergyBridge LLM agent (needs EnergyPlus):
    python examples/run_benchmark_smoke.py \
        --scenario data/scenarios/us_chicago_vpp_smoke.json \
        --agent current

With a rule-based baseline (fast, no EnergyPlus required):
    python examples/run_benchmark_smoke.py \
        --scenario data/scenarios/us_chicago_vpp_smoke.json \
        --agent rule_based_balanced

Available --agent values: current | comfort_first | grid_first | rule_based_balanced

Output: logs/benchmark_runs/<scenario_id>/<timestamp>/
    metrics.json, summary.md, raw_agent_result.json
"""

from __future__ import annotations
import argparse, json, sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

BASELINE_NAMES = {"comfort_first", "grid_first", "rule_based_balanced"}


def _load_scenario(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        print(f"ERROR: scenario file not found: {p}"); sys.exit(1)
    with open(p) as f:
        return json.load(f)


def _resolve(scenario: dict) -> dict:
    s = dict(scenario)
    for k in ("idf_path", "epw_path"):
        v = s.get(k, "")
        if v and not Path(v).is_absolute():
            s[k] = str(PROJECT_ROOT / v)
    return s


def _outdir(scenario_id: str) -> Path:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    d = PROJECT_ROOT / "logs" / "benchmark_runs" / scenario_id / ts
    d.mkdir(parents=True, exist_ok=True)
    return d


def _save(out: Path, metrics, raw: dict) -> None:
    (out / "metrics.json").write_text(json.dumps(metrics.to_dict(), indent=2, ensure_ascii=False))
    (out / "raw_agent_result.json").write_text(json.dumps(raw, indent=2, ensure_ascii=False))
    m = metrics
    lines = [
        "# Benchmark Smoke-Test Summary", "",
        f"**Scenario :** {m.scenario_id}", f"**Agent    :** {m.agent_id}",
        f"**Run time :** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", "",
        "## Control Outcome", "",
        "| Metric | Value |", "|---|---|",
        f"| agent_triggered | {m.agent_triggered} |",
        f"| valid_control_plan | {m.valid_control_plan} |",
        f"| action_type | `{m.action_type}` |",
        f"| setpoint_after_action | {m.setpoint_after_action} °C |",
        f"| execution_status | `{m.execution_status}` |",
        f"| safety_ok | {m.safety_ok} |", "",
        "## VPP Compliance", "",
        "| Metric | Value |", "|---|---|",
        f"| requested_reduction_kw | {m.requested_reduction_kw} kW |",
        f"| estimated_reduction_kw | {m.estimated_reduction_kw} kW |",
        f"| estimated_vpp_compliance | {m.estimated_vpp_compliance} |", "",
        "## Building State at Event", "",
        "| Metric | Value |", "|---|---|",
        f"| sim_hour | {m.sim_hour} |",
        f"| indoor_temp_at_event | {m.indoor_temp_at_event} °C |",
        f"| outdoor_temp_at_event | {m.outdoor_temp_at_event} °C |",
        f"| hvac_power_kw_at_event | {m.hvac_power_kw_at_event} kW |",
        f"| facility_power_kw_at_event | {m.facility_power_kw_at_event} kW |",
        f"| simple_temp_deviation | {m.simple_temp_deviation} °C |", "",
        "> **Note:** event-level proxy metrics only. Actual energy savings require EP .eso parsing.", "",
    ]
    (out / "summary.md").write_text("\n".join(lines) + "\n")


def _run_current(scenario: dict):
    from energybridge.simulation.eplus_env import EplusEnv
    idf, epw = Path(scenario["idf_path"]), Path(scenario["epw_path"])
    for p, lbl in [(idf, "IDF"), (epw, "EPW")]:
        if not p.exists():
            print(f"ERROR: {lbl} not found: {p}"); sys.exit(1)
    vpp = {
        "vpp_task_id": f"benchmark-{scenario['id']}-001",
        "vpp_query_id": f"benchmark-{scenario['id']}-q001",
        "vpp_task_type": scenario["vpp_context"].get("vpp_task_type", "INVITATION_DEMAND_RESPONSE"),
        "vpp_time_scale": "DAY_AHEAD", "vpp_trigger_reason": "REGIONAL_PEAK_LOAD",
        "vpp_start_time": "18:00", "vpp_end_time": "19:00", "vpp_notice_minutes": 60,
        "vpp_duration_minutes": scenario["vpp_context"].get("duration_minutes", 60),
        "vpp_required_capacity_kw": scenario["vpp_context"].get("requested_reduction_kw", 0.5),
        "vpp_declaration_deadline": "", "vpp_response_direction": "REDUCE",
        "vpp_capacity_scope": "upstream_total_capacity",
    }
    ep_out = PROJECT_ROOT / scenario.get("output_dir", "logs/eplus_run")
    env = EplusEnv(idf_path=idf, epw_path=epw, output_dir=ep_out,
                   memory_path=str(PROJECT_ROOT / "logs" / "memory.json"),
                   log_dir=str(PROJECT_ROOT / "logs"))
    env.inject_vpp_event(vpp_context=vpp, user_input=scenario.get("user_input", ""),
                         trigger_hour=float(scenario.get("trigger_hour", 42.0)))
    print("Starting EnergyPlus ...")
    code = env.run()
    print(f"EnergyPlus exit code: {code}")
    if not env.agent_results:
        print("WARNING: agent not triggered."); return None
    return env.agent_results[0]


def _run_baseline(agent_id: str, scenario: dict):
    from energybridge.evaluation.baselines import get_baseline
    fn = get_baseline(agent_id)
    home = {"indoor_temp": 24.5, "outdoor_temp": 22.0, "hvac_power_kw": 2.2,
            "facility_power_kw": 3.1, "hvac_setpoint": 25.0, "occupancy": True}
    cp = fn(home, scenario.get("vpp_context", {}), scenario.get("user_input", ""))

    class _R:
        sim_hour = float(scenario.get("trigger_hour", 42.0))
        home_state = home
        final_response = f"[{agent_id}] setpoint={cp.get('setpoint')}°C"
        trajectory = []
    r = _R(); r.control_plan = cp
    r.safety_report = {"safe": 18.0 <= (cp.get("setpoint") or 25.0) <= 30.0,
                       "violations": [], "source": "runner_check"}
    r.execution_result = {"status": "simulated", "source": "baseline_runner"}
    return r


def main():
    ap = argparse.ArgumentParser(description="EnergyBridge benchmark smoke-test runner")
    ap.add_argument("--scenario", required=True)
    ap.add_argument("--agent", default="current")
    args = ap.parse_args()

    scenario = _resolve(_load_scenario(args.scenario))
    agent_id = args.agent

    print("=" * 60)
    print("EnergyBridge Benchmark Smoke Test")
    print("=" * 60)
    print(f"Scenario : {scenario.get('id')}  —  {scenario.get('name')}")
    print(f"Agent    : {agent_id}"); print()

    if agent_id == "current":
        result = _run_current(scenario)
    elif agent_id in BASELINE_NAMES:
        result = _run_baseline(agent_id, scenario)
    else:
        print(f"ERROR: unknown --agent '{agent_id}'. Valid: current, {sorted(BASELINE_NAMES)}")
        sys.exit(1)

    if result is None:
        print("No agent result."); sys.exit(1)

    from energybridge.evaluation.benchmark_metrics import compute_benchmark_metrics
    metrics = compute_benchmark_metrics(result, scenario, agent_id)

    out = _outdir(scenario.get("id", "unknown"))
    raw = {k: getattr(result, k, None) for k in
           ("sim_hour","home_state","control_plan","safety_report","execution_result","final_response","trajectory")}
    _save(out, metrics, raw)

    print(); print("── Benchmark Metrics ──────────────────────────────────")
    for k, v in metrics.to_dict().items():
        if v is not None:
            print(f"  {k:<35} {v}")
    print(); print(f"Outputs saved to: {out}")


if __name__ == "__main__":
    main()
