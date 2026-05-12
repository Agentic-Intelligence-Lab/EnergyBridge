"""Generate a deterministic candidate strategy from preferences and grid context."""


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(value, max_value))


def _get_session_summary(memory: dict | None) -> dict:
    session_summary = (memory or {}).get("session_summary", {})
    return session_summary if isinstance(session_summary, dict) else {}


def generate_candidate_strategy(
    user_preferences: dict,
    translated_grid_signal: dict,
    home_state: dict,
    memory: dict | None = None,
) -> dict:
    session_summary = _get_session_summary(memory)
    recent_preferences = session_summary.get("recent_preferences", {}) if isinstance(session_summary, dict) else {}
    recent_control_plan = session_summary.get("recent_control_plan", {}) if isinstance(session_summary, dict) else {}
    recent_safety_report = session_summary.get("recent_safety_report", {}) if isinstance(session_summary, dict) else {}

    comfort_min = float(user_preferences.get("preferred_temp_min", 24.0))
    comfort_max = float(user_preferences.get("preferred_temp_max", 26.0))
    current_setpoint = float(home_state.get("hvac_setpoint", 25.0))

    # Actual EP indoor temperature (may differ from thermostat setpoint).
    # If the room is already below setpoint, there is thermal buffer to raise
    # the setpoint more aggressively during demand response.
    _indoor_raw = home_state.get("indoor_temp")
    actual_indoor_temp = float(_indoor_raw) if _indoor_raw is not None else None
    # Thermal buffer = how much cooler room is vs current setpoint (>=0)
    thermal_buffer_c = (
        max(0.0, current_setpoint - actual_indoor_temp)
        if actual_indoor_temp is not None else 0.0
    )

    # If the last turn produced a safe control plan, use it as a lightweight
    # short-term anchor when the current state is otherwise ambiguous.
    if (
        current_setpoint == 25.0
        and recent_control_plan
        and recent_safety_report.get("safe", False)
        and "setpoint" in recent_control_plan
    ):
        current_setpoint = float(recent_control_plan.get("setpoint", current_setpoint))

    if "preferred_temp_min" in recent_preferences and comfort_min == 24.0:
        comfort_min = float(recent_preferences["preferred_temp_min"])
    if "preferred_temp_max" in recent_preferences and comfort_max == 26.0:
        comfort_max = float(recent_preferences["preferred_temp_max"])

    control_intent = translated_grid_signal.get("control_intent", "normal_operation")
    price_level = translated_grid_signal.get("price_level", "normal")

    mode = "comfort"
    pre_cooling = False
    expected_user_impact = "minimal"
    rationale: list[str] = []

    recommended_setpoint = _clamp(current_setpoint, comfort_min, comfort_max)

    if control_intent == "reduce_load":
        mode = "grid_support"
        # Extra allowance if room has thermal buffer (cooler than setpoint)
        extra = min(0.5, thermal_buffer_c * 0.4)
        delta = round(0.8 + extra, 2)
        recommended_setpoint = _clamp(current_setpoint + delta, comfort_min, comfort_max + 1.0)
        expected_user_impact = "slight_warmer"
        temp_desc = (
            f"actual indoor {actual_indoor_temp:.1f}°C, buffer {thermal_buffer_c:.1f}°C"
            if actual_indoor_temp is not None else "indoor temp not available"
        )
        rationale.append(
            f"Grid requested load reduction: setpoint +{delta:.1f}°C ({temp_desc})."
        )
    elif control_intent == "cost_saving" or price_level in {"high", "critical"}:
        mode = "cost_saving"
        recommended_setpoint = _clamp(current_setpoint + 0.5, comfort_min, comfort_max + 0.5)
        expected_user_impact = "slight_warmer"
        rationale.append("High price period detected, prioritizing cost-efficient operation.")
    else:
        mode = "comfort"
        recommended_setpoint = _clamp(current_setpoint, comfort_min, comfort_max)
        expected_user_impact = "minimal"
        rationale.append("No grid pressure detected, preserving comfort preference.")

    allow_pre_cooling = bool(user_preferences.get("allow_pre_cooling", False))
    if not allow_pre_cooling and recent_preferences:
        allow_pre_cooling = bool(recent_preferences.get("allow_pre_cooling", False))

    if allow_pre_cooling and control_intent == "reduce_load":
        pre_cooling = True
        rationale.append("User allows pre-cooling for short-term demand response support.")

    recommended_setpoint = _clamp(recommended_setpoint, 18.0, 30.0)

    return {
        "mode": mode,
        "recommended_setpoint": round(recommended_setpoint, 2),
        "pre_cooling": pre_cooling,
        "expected_user_impact": expected_user_impact,
        "rationale": rationale,
    }
