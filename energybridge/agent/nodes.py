"""Graph node implementations for the minimal EnergyBridge workflow."""

from __future__ import annotations

from typing import Any

from energybridge.control.mock_actuator import execute_control_plan
from energybridge.control.fallback_controller import fallback_control_plan
from energybridge.control.mock_mpc import run_mock_mpc
from energybridge.evaluation.metrics import summarize_run
from energybridge.control.safety_checker import validate_safety
from energybridge.evaluation.logger import build_trajectory_log_path, save_trajectory
from energybridge.memory.store import load_memory, save_memory
from energybridge.skills.explanation_generator import generate_explanation
from energybridge.skills.grid_signal_translator import translate_vpp_context_to_grid_demand
from energybridge.skills.preference_parser import merge_preferences_with_memory, parse_user_preference
from energybridge.skills.strategy_generator import generate_candidate_strategy


def _append_trajectory(
    state: dict[str, Any], node_name: str, output: dict[str, Any]
) -> list[dict[str, Any]]:
    trajectory = list(state.get("trajectory", []))
    trajectory.append({"node": node_name, "output": output})
    return trajectory


def node_load_memory(state: dict[str, Any]) -> dict[str, Any]:
    memory_path = state.get("memory_path", "logs/memory.json")
    memory = load_memory(memory_path)
    session_summary = memory.get("session_summary", {}) if isinstance(memory.get("session_summary", {}), dict) else {}
    output = {
        "memory_loaded": True,
        "episodic_count": len(memory.get("episodic_logs", [])),
        "session_summary_preview": session_summary.get("summary_text", ""),
    }
    return {"memory": memory, "trajectory": _append_trajectory(state, "load_memory", output)}


def node_parse_preference(state: dict[str, Any]) -> dict[str, Any]:
    parsed = parse_user_preference(state.get("user_input", ""))
    prefs = merge_preferences_with_memory(parsed, state.get("memory", {}))
    return {
        "user_preferences": prefs,
        "trajectory": _append_trajectory(state, "parse_preference", prefs),
    }


def node_translate_grid(state: dict[str, Any]) -> dict[str, Any]:
    translated = translate_vpp_context_to_grid_demand(state.get("vpp_context", state.get("grid_demand", {})))
    return {
        "translated_grid_signal": translated,
        "trajectory": _append_trajectory(state, "translate_grid", translated),
    }


def node_generate_strategy(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("candidate_strategy"):
        selected_strategy = dict(state.get("candidate_strategy", {}))
        selected_strategy.setdefault("source", "preselected")
        return {
            "candidate_strategy": selected_strategy,
            "trajectory": _append_trajectory(state, "generate_strategy", selected_strategy),
        }

    strategy = generate_candidate_strategy(
        user_preferences=state.get("user_preferences", {}),
        translated_grid_signal=state.get("translated_grid_signal", {}),
        home_state=state.get("home_state", {}),
        memory=state.get("memory", {}),
    )
    return {
        "candidate_strategy": strategy,
        "trajectory": _append_trajectory(state, "generate_strategy", strategy),
    }


def node_control(state: dict[str, Any]) -> dict[str, Any]:
    control_plan = run_mock_mpc(
        candidate_strategy=state.get("candidate_strategy", {}),
        home_state=state.get("home_state", {}),
        translated_grid_signal=state.get("translated_grid_signal", {}),
    )
    return {
        "control_plan": control_plan,
        "trajectory": _append_trajectory(state, "control", control_plan),
    }


def node_safety(state: dict[str, Any]) -> dict[str, Any]:
    control_plan = state.get("control_plan", {})
    report = validate_safety(
        control_plan=control_plan,
        user_preferences=state.get("user_preferences", {}),
        home_state=state.get("home_state", {}),
    )

    safe_control_plan = control_plan
    if not report.get("safe", False):
        fallback_reason = "; ".join(report.get("violations", [])) or "safety_rejection"
        safe_control_plan = fallback_control_plan(
            home_state=state.get("home_state", {}),
            reason=fallback_reason,
        )
        fallback_report = validate_safety(
            control_plan=safe_control_plan,
            user_preferences=state.get("user_preferences", {}),
            home_state=state.get("home_state", {}),
        )
        fallback_report["fallback_applied"] = True
        fallback_report["original_violations"] = report.get("violations", [])
        report = fallback_report

    return {
        "control_plan": safe_control_plan,
        "safety_report": report,
        "trajectory": _append_trajectory(
            state,
            "safety",
            {"control_plan": safe_control_plan, "safety_report": report},
        ),
    }


def node_actuate(state: dict[str, Any]) -> dict[str, Any]:
    user_choice = state.get("user_choice", {})
    execution_result = execute_control_plan(
        control_plan=state.get("control_plan", {}),
        approved=bool(user_choice.get("approved", True)),
    )
    return {
        "execution_result": execution_result,
        "trajectory": _append_trajectory(state, "actuate", execution_result),
    }


def node_explanation(state: dict[str, Any]) -> dict[str, Any]:
    response = generate_explanation(
        candidate_strategy=state.get("candidate_strategy", {}),
        control_plan=state.get("control_plan", {}),
        safety_report=state.get("safety_report", {}),
        memory=state.get("memory", {}),
    )
    execution_result = state.get("execution_result", {})
    if execution_result:
        response = (
            f"{response} Execution status: {execution_result.get('status', 'unknown')} "
            f"via {execution_result.get('actuator', 'mock_electrical_actuator_v0')}."
        )
    return {
        "final_response": response,
        "trajectory": _append_trajectory(state, "explanation", {"final_response": response}),
    }


def node_metrics(state: dict[str, Any]) -> dict[str, Any]:
    metrics = summarize_run(state)
    return {
        "metrics": metrics,
        "trajectory": _append_trajectory(state, "metrics", metrics),
    }


def node_feedback(state: dict[str, Any]) -> dict[str, Any]:
    memory_path = state.get("memory_path", "logs/memory.json")
    memory = state.get("memory", load_memory(memory_path))
    user_feedback = dict(state.get("user_feedback", {}) or {})

    feedback_history = list(memory.get("feedback_history", []) or [])
    feedback_history.append(user_feedback)
    memory["feedback_history"] = feedback_history[-50:]
    memory["latest_user_feedback"] = user_feedback
    save_memory(memory, memory_path)

    output = {
        "feedback_saved": True,
        "feedback_score": user_feedback.get("satisfaction_score"),
        "feedback_label": user_feedback.get("satisfaction_label"),
    }
    return {
        "memory": memory,
        "trajectory": _append_trajectory(state, "feedback", output),
    }


def node_logging(state: dict[str, Any]) -> dict[str, Any]:
    log_dir = state.get("log_dir", "logs")
    log_path = build_trajectory_log_path(log_dir=log_dir)
    output = {"trajectory_log_path": log_path}
    updated_trajectory = _append_trajectory(state, "logging", output)
    final_state = dict(state)
    final_state["trajectory"] = updated_trajectory
    final_state["trajectory_log_path"] = log_path
    save_trajectory(final_state, path=log_path)
    return {
        "trajectory_log_path": log_path,
        "trajectory": updated_trajectory,
    }
