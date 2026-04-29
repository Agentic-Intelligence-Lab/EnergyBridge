"""Deterministic safety validator for control plans."""


def validate_safety(
    control_plan: dict,
    user_preferences: dict,
    home_state: dict,
) -> dict:
    violations: list[str] = []
    checked_rules = [
        "setpoint_within_hard_hvac_bounds_18_30",
        "setpoint_within_user_preferred_bounds",
    ]

    setpoint = float(control_plan.get("setpoint", home_state.get("hvac_setpoint", 25.0)))

    if setpoint < 18.0 or setpoint > 30.0:
        violations.append("setpoint_out_of_hard_bounds")

    preferred_min = float(user_preferences.get("preferred_temp_min", 24.0))
    preferred_max = float(user_preferences.get("preferred_temp_max", 26.0))

    if setpoint < preferred_min or setpoint > preferred_max:
        allow_drift = bool(user_preferences.get("allow_temp_drift", False))
        drift_band = 1.0 if allow_drift else 0.0
        if setpoint < preferred_min - drift_band or setpoint > preferred_max + drift_band:
            violations.append("setpoint_out_of_preference_bounds")

    return {
        "safe": len(violations) == 0,
        "violations": violations,
        "checked_rules": checked_rules,
    }
