"""Graph node implementations for the minimal EnergyBridge workflow."""

from __future__ import annotations

from typing import Any

from energybridge.control.mock_actuator import execute_control_plan
from energybridge.control.ep_controller import build_ep_control_plan
from energybridge.control.fallback_controller import fallback_control_plan
from energybridge.evaluation.metrics import summarize_run
from energybridge.control.safety_checker import validate_safety
from energybridge.evaluation.logger import build_trajectory_log_path, save_trajectory
from energybridge.memory.store import load_memory, save_memory, update_memory
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
    grid_demand = dict(state.get("grid_demand", {}) or {})
    if not grid_demand:
        grid_demand = translate_vpp_context_to_grid_demand(state.get("vpp_context", {}))

    return {
        "grid_demand": grid_demand,
        "trajectory": _append_trajectory(state, "translate_grid", grid_demand),
    }


def node_generate_strategy(state: dict[str, Any]) -> dict[str, Any]:
    if state.get("candidate_strategy"):
        selected_strategy = dict(state.get("candidate_strategy", {}))
        selected_strategy.setdefault("source", "preselected")
        return {
            "candidate_strategy": selected_strategy,
            "trajectory": _append_trajectory(state, "generate_strategy", selected_strategy),
        }

    grid_demand = state.get("grid_demand", {})

    # Rule-based fallback strategy (always computed as baseline)
    strategy = generate_candidate_strategy(
        user_preferences=state.get("user_preferences", {}),
        grid_demand=grid_demand,
        home_state=state.get("home_state", {}),
        memory=state.get("memory", {}),
    )

    # Attempt LLM-enhanced strategy generation when USE_LLM=true
    llm_metrics: dict[str, Any] = {
        "used": False,
        "provider": "not_configured",
        "model": "not_used",
        "latency_seconds": 0.0,
        "token_usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }
    try:
        from energybridge.llm.strategy_advisor import generate_strategy_options
        from energybridge.utils.config import load_llm_config
        llm_cfg = load_llm_config()
        if llm_cfg.use_llm:
            home_state = state.get("home_state", {})
            context = {
                "user_input": state.get("user_input", ""),
                "user_preferences": state.get("user_preferences", {}),
                "grid_demand": grid_demand,
                "home_state": home_state,
                "vpp_context": state.get("vpp_context", {}),
                "fallback_strategy": strategy,
                # Explicitly surface actual indoor temperature for the LLM
                "actual_indoor_temp_c": home_state.get("indoor_temp"),
                "hvac_setpoint_c": home_state.get("hvac_setpoint"),
            }
            options, llm_metrics = generate_strategy_options(
                context=context,
                fallback_strategy=strategy,
            )
            if options:
                # Auto-select: prefer the option whose mode best matches grid intent
                grid_intent = grid_demand.get("control_intent", "normal_operation")
                mode_pref = {
                    "reduce_load": "grid_support",
                    "cost_saving": "cost_saving",
                }.get(grid_intent, "comfort")
                best = next(
                    (o for o in options if o.get("mode") == mode_pref),
                    options[0],
                )
                strategy = best
    except Exception as _exc:
        llm_metrics["error"] = str(_exc)

    # Merge existing llm_metrics if already set (e.g. pre-populated by caller)
    existing = state.get("llm_metrics") or {}
    if not existing.get("used"):
        pass  # replace with the one we just computed
    else:
        llm_metrics = existing

    return {
        "candidate_strategy": strategy,
        "llm_metrics": llm_metrics,
        "trajectory": _append_trajectory(state, "generate_strategy", {
            **strategy,
            "llm_used": llm_metrics.get("used", False),
        }),
    }


def node_control(state: dict[str, Any]) -> dict[str, Any]:
    control_plan = build_ep_control_plan(
        candidate_strategy=state.get("candidate_strategy", {}),
        home_state=state.get("home_state", {}),
        grid_demand=state.get("grid_demand", {}),
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
            f"via {execution_result.get('actuator', 'unknown')}."
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


def node_memory_update(state: dict[str, Any]) -> dict[str, Any]:
    memory_path = state.get("memory_path", "logs/memory.json")
    memory = state.get("memory", load_memory(memory_path))
    episode = {
        "user_input": state.get("user_input", ""),
        "user_preferences": dict(state.get("user_preferences", {}) or {}),
        "grid_demand": dict(state.get("grid_demand", {}) or {}),
        "control_plan": dict(state.get("control_plan", {}) or {}),
        "safety_report": dict(state.get("safety_report", {}) or {}),
        "execution_result": dict(state.get("execution_result", {}) or {}),
        "user_feedback": dict(state.get("user_feedback", {}) or {}),
        "final_response": state.get("final_response", ""),
    }
    updated_memory = update_memory(memory, episode)

    feedback_history = list(updated_memory.get("feedback_history", []) or [])
    user_feedback = dict(state.get("user_feedback", {}) or {})
    if user_feedback:
        feedback_history.append(user_feedback)
        updated_memory["feedback_history"] = feedback_history[-50:]
        updated_memory["latest_user_feedback"] = user_feedback

    save_memory(updated_memory, memory_path)
    output = {
        "memory_updated": True,
        "episodic_count": len(updated_memory.get("episodic_logs", [])),
        "session_summary_preview": updated_memory.get("session_summary", {}).get("summary_text", ""),
    }
    return {
        "memory": updated_memory,
        "trajectory": _append_trajectory(state, "memory_update", output),
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
