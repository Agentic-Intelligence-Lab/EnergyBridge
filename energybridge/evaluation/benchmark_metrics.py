"""Event-level benchmark metrics for EnergyBridge.

These are *proxy* metrics computed from a single AgentResult (the agent's
decisions and building state at the VPP event moment).  They do NOT include
full physical energy-saving measurements.

Physical metrics (actual kWh delta, temperature trajectory, setpoint
compliance over time) require parsing EnergyPlus output time series
(.eso / .csv) — not yet implemented.
"""

from __future__ import annotations
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class BenchmarkMetrics:
    # API-level control
    agent_triggered: bool = False
    valid_control_plan: bool = False
    action_type: str = ""
    setpoint: float | None = None
    execution_status: str = ""
    safety_ok: bool = False

    # VPP
    requested_reduction_kw: float | None = None
    estimated_reduction_kw: float | None = None
    estimated_vpp_compliance: bool | None = None

    # Comfort
    indoor_temp_at_event: float | None = None
    outdoor_temp_at_event: float | None = None
    setpoint_after_action: float | None = None
    simple_temp_deviation: float | None = None  # setpoint - indoor_temp

    # Energy/power proxy
    hvac_power_kw_at_event: float | None = None
    facility_power_kw_at_event: float | None = None

    # Metadata
    sim_hour: float | None = None
    scenario_id: str = ""
    agent_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_benchmark_metrics(
    agent_result: Any,
    scenario: dict[str, Any],
    agent_id: str = "current",
) -> BenchmarkMetrics:
    """Compute BenchmarkMetrics from a single AgentResult.

    agent_result: AgentResult dataclass from EplusEnv.agent_results, or a
                  lightweight stub with the same fields.
    scenario:     Scenario config dict (from JSON).
    agent_id:     Name of the algorithm.
    """
    m = BenchmarkMetrics(
        agent_triggered=True,
        scenario_id=scenario.get("id", ""),
        agent_id=agent_id,
        sim_hour=agent_result.sim_hour,
    )

    home = agent_result.home_state or {}
    m.indoor_temp_at_event = home.get("indoor_temp")
    m.outdoor_temp_at_event = home.get("outdoor_temp")
    m.hvac_power_kw_at_event = home.get("hvac_power_kw")
    m.facility_power_kw_at_event = home.get("facility_power_kw")

    cp = agent_result.control_plan or {}
    m.valid_control_plan = bool(cp.get("action"))
    m.action_type = cp.get("action", "")
    m.setpoint = cp.get("setpoint") or cp.get("target_temperature")
    m.setpoint_after_action = m.setpoint
    m.estimated_reduction_kw = cp.get("estimated_reduction_kw")

    if m.indoor_temp_at_event is not None and m.setpoint_after_action is not None:
        m.simple_temp_deviation = round(
            m.setpoint_after_action - m.indoor_temp_at_event, 2
        )

    sr = agent_result.safety_report or {}
    m.safety_ok = sr.get("safe", False)

    er = agent_result.execution_result or {}
    m.execution_status = er.get("status", "")

    vpp = scenario.get("vpp_context", {})
    m.requested_reduction_kw = vpp.get("requested_reduction_kw")
    if (
        m.estimated_reduction_kw is not None
        and m.requested_reduction_kw is not None
    ):
        m.estimated_vpp_compliance = (
            m.estimated_reduction_kw >= m.requested_reduction_kw
        )

    return m


def compute_metrics_from_dict(
    agent_result_dict: dict[str, Any],
    scenario: dict[str, Any],
    agent_id: str = "current",
) -> BenchmarkMetrics:
    """Same as compute_benchmark_metrics but accepts a plain dict."""

    class _Stub:
        pass

    r = _Stub()
    for attr in (
        "sim_hour", "home_state", "control_plan",
        "safety_report", "execution_result", "final_response", "trajectory",
    ):
        setattr(r, attr, agent_result_dict.get(attr, {} if attr not in ("sim_hour", "final_response") else None))
    return compute_benchmark_metrics(r, scenario, agent_id)
