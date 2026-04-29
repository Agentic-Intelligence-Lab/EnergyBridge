"""Generate a deterministic candidate strategy from preferences and grid context."""


def _clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(value, max_value))


def generate_candidate_strategy(
    user_preferences: dict,
    translated_grid_signal: dict,
    home_state: dict,
) -> dict:
    comfort_min = float(user_preferences.get("preferred_temp_min", 24.0))
    comfort_max = float(user_preferences.get("preferred_temp_max", 26.0))
    current_setpoint = float(home_state.get("hvac_setpoint", 25.0))

    control_intent = translated_grid_signal.get("control_intent", "normal_operation")
    price_level = translated_grid_signal.get("price_level", "normal")

    mode = "comfort"
    pre_cooling = False
    expected_user_impact = "minimal"
    rationale: list[str] = []

    recommended_setpoint = _clamp(current_setpoint, comfort_min, comfort_max)

    if control_intent == "reduce_load":
        mode = "grid_support"
        recommended_setpoint = _clamp(current_setpoint + 0.8, comfort_min, comfort_max + 0.8)
        expected_user_impact = "slight_warmer"
        rationale.append("Grid requested load reduction, so setpoint is moderately increased.")
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

    if user_preferences.get("allow_pre_cooling", False) and control_intent == "reduce_load":
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
