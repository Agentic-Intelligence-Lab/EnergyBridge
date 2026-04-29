"""Minimal metrics utilities for the first-stage demo."""


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
    safety_report = state.get("safety_report", {})
    duration_hours = float(control_plan.get("duration_minutes", 0) or 0) / 60.0

    expected_power_kw = float(control_plan.get("estimated_power_kw", 0.0) or 0.0)
    expected_energy_kwh = round(expected_power_kw * duration_hours, 3)
    baseline_power_kw = float(state.get("home_state", {}).get("hvac_power_kw", 0.0) or 0.0)
    baseline_energy_kwh = round(baseline_power_kw * duration_hours, 3)
    expected_reduction_kw = float(control_plan.get("estimated_reduction_kw", 0.0) or 0.0)
    local_target_reduction_kw = float(translated_grid_signal.get("target_reduction_kw", 0.0) or 0.0)

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
        "local_target_reduction_kw": round(local_target_reduction_kw, 3),
        "meets_vpp_requirement": expected_reduction_kw >= local_target_reduction_kw,
        "vpp_requirement_basis": translated_grid_signal.get(
            "vpp_local_target_basis",
            "local_grid_signal_target_reduction_kw",
        ),
        "safety_ok": bool(safety_report.get("safe", False)),
        "api_used": bool(llm_metrics.get("used", False)),
        "llm_model": llm_metrics.get("model", "not_used"),
    }
