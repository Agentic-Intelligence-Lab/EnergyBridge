"""Minimal metrics utilities for the first-stage demo."""

from __future__ import annotations


def summarize_episode(episode: dict) -> dict:
    control_plan = episode.get("control_plan", {})
    safety_report = episode.get("safety_report", {})
    return {
        "estimated_reduction_kw": control_plan.get("estimated_reduction_kw", 0.0),
        "safe": safety_report.get("safe", False),
        "violation_count": len(safety_report.get("violations", [])),
    }


def summarize_run(state: dict) -> dict:
    llm_metrics = state.get("llm_metrics", {})
    token_usage = llm_metrics.get(
        "token_usage",
        {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    )
    user_feedback = state.get("user_feedback", {})
    control_plan = state.get("control_plan", {})
    translated_grid_signal = state.get("translated_grid_signal", {})
    vpp_context = state.get("vpp_context", {})
    safety_report = state.get("safety_report", {})
    duration_hours = float(control_plan.get("duration_minutes", 0) or 0) / 60.0

    expected_power_kw = float(control_plan.get("estimated_power_kw", 0.0) or 0.0)
    expected_energy_kwh = round(expected_power_kw * duration_hours, 3)
    baseline_power_kw = float(state.get("home_state", {}).get("hvac_power_kw", 0.0) or 0.0)
    baseline_energy_kwh = round(baseline_power_kw * duration_hours, 3)
    expected_reduction_kw = float(control_plan.get("estimated_reduction_kw", 0.0) or 0.0)
    total_required_capacity_kw = float(translated_grid_signal.get("total_required_capacity_kw", 0.0) or 0.0)
    capacity_scope = str(translated_grid_signal.get("capacity_scope", "upstream_total_capacity"))
    control_intent = str(translated_grid_signal.get("control_intent", "normal_operation"))
    response_alignment = control_intent in {"reduce_load", "cost_saving"} and expected_reduction_kw > 0.0

    return {
        "api_latency_seconds": round(float(llm_metrics.get("latency_seconds", 0.0) or 0.0), 3),
        "token_usage": token_usage,
        "user_satisfaction_score": user_feedback.get("satisfaction_score", None),
        "user_satisfaction_label": user_feedback.get("satisfaction_label", "not_provided"),
        "expected_power_kw": round(expected_power_kw, 3),
        "expected_energy_kwh": expected_energy_kwh,
        "baseline_energy_kwh": baseline_energy_kwh,
        "expected_energy_saving_kwh": round(max(0.0, baseline_energy_kwh - expected_energy_kwh), 3),
        "expected_reduction_kw": round(expected_reduction_kw, 3),
        "reference_required_capacity_kw": round(total_required_capacity_kw, 3),
        "capacity_scope": capacity_scope,
        "meets_vpp_requirement": response_alignment,
        "safety_ok": bool(safety_report.get("safe", False)),
        "api_used": bool(llm_metrics.get("used", False)),
        "llm_model": llm_metrics.get("model", "not_used"),
        "vpp_task_id": vpp_context.get("vpp_task_id", ""),
        "vpp_query_id": vpp_context.get("vpp_query_id", ""),
    }


def learning_score(persona_preferences: dict, learned_preferences: dict) -> dict:
    components: list[float] = []

    for field in ["comfort_priority", "cost_priority", "grid_priority"]:
        target = float(persona_preferences.get(field, 0.0) or 0.0)
        learned = float(learned_preferences.get(field, 0.0) or 0.0)
        components.append(max(0.0, 1.0 - abs(target - learned)))

    for field in ["preferred_temp_min", "preferred_temp_max"]:
        target = float(persona_preferences.get(field, 0.0) or 0.0)
        learned = float(learned_preferences.get(field, 0.0) or 0.0)
        components.append(max(0.0, 1.0 - min(abs(target - learned) / 4.0, 1.0)))

    for field in ["allow_pre_cooling", "allow_temp_drift"]:
        target = bool(persona_preferences.get(field, False))
        learned = bool(learned_preferences.get(field, False))
        components.append(1.0 if target == learned else 0.0)

    return {
        "preference_learning_score": round(sum(components) / len(components), 3) if components else 0.0,
        "persona_stable_preferences": persona_preferences,
        "learned_stable_preferences": learned_preferences,
    }


def aggregate_roleplay_summaries(summaries: list[dict]) -> dict:
    if not summaries:
        return {
            "user_count": 0,
            "average_learning_score": 0.0,
            "min_learning_score": 0.0,
            "max_learning_score": 0.0,
            "average_satisfaction_score": 0.0,
            "vpp_requirement_meet_rate": 0.0,
            "average_api_latency_seconds": 0.0,
        }

    learning_scores = [
        float(summary.get("learning_summary", {}).get("preference_learning_score", 0.0) or 0.0)
        for summary in summaries
    ]
    satisfaction_scores: list[float] = []
    vpp_meets: list[bool] = []
    latencies: list[float] = []

    for summary in summaries:
        for turn in summary.get("turn_overview", []):
            if turn.get("satisfaction_score") is not None:
                satisfaction_scores.append(float(turn["satisfaction_score"]))
            if turn.get("meets_vpp_requirement") is not None:
                vpp_meets.append(bool(turn["meets_vpp_requirement"]))
            if turn.get("api_latency_seconds") is not None:
                latencies.append(float(turn["api_latency_seconds"]))

    return {
        "user_count": len(summaries),
        "average_learning_score": round(sum(learning_scores) / len(learning_scores), 3),
        "min_learning_score": round(min(learning_scores), 3),
        "max_learning_score": round(max(learning_scores), 3),
        "average_satisfaction_score": round(sum(satisfaction_scores) / len(satisfaction_scores), 3) if satisfaction_scores else 0.0,
        "vpp_requirement_meet_rate": round(sum(vpp_meets) / len(vpp_meets), 3) if vpp_meets else 0.0,
        "average_api_latency_seconds": round(sum(latencies) / len(latencies), 3) if latencies else 0.0,
    }
