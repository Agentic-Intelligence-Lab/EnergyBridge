"""State schema for the minimal EnergyBridge agent loop."""

from typing import Any, TypedDict


class EnergyBridgeState(TypedDict, total=False):
    user_input: str
    grid_signal: dict[str, Any]
    home_state: dict[str, Any]
    grid_signal_source: str
    vpp_task: dict[str, Any]
    vpp_query: dict[str, Any]

    user_preferences: dict[str, Any]
    translated_grid_signal: dict[str, Any]
    strategy_options: list[dict[str, Any]]
    candidate_strategy: dict[str, Any]
    user_choice: dict[str, Any]
    user_feedback: dict[str, Any]
    llm_metrics: dict[str, Any]
    control_plan: dict[str, Any]
    safety_report: dict[str, Any]
    execution_result: dict[str, Any]
    metrics: dict[str, Any]
    final_response: str

    memory: dict[str, Any]
    trajectory: list[dict[str, Any]]
    trajectory_log_path: str
